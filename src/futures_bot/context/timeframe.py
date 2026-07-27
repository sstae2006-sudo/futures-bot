"""Multi-Timeframe Context -- Market Context Engine Phase 5 (2026-07-27).

Combines trend direction across five canonical timeframes (1 minute, 5
minute, 15 minute, 1 hour, Daily) into one alignment reading: how much
do they agree, and in which direction. Classification only -- describes
how aligned the market's trend is across horizons; never a trading
signal.

Reuse, not re-derivation: per-timeframe direction comes from
``research.regime.classify_trend`` (bullish/bearish/sideways via a
start-to-end % move over the trailing 20 closes) -- the same function
``context.regime`` already uses for its own trend-direction signal, now
applied once per timeframe instead of once overall. Its "sideways"
result is mapped onto ``TrendState.NEUTRAL`` (``context/models.py``'s
existing enum, previously unused outside ``MarketContext.trend_state``'s
still-stubbed slot) rather than inventing a new direction vocabulary.

**Avoiding future leakage is the central concern of this module, more so
than any single-timeframe classifier in this package.** A single-stream
classifier (session/volatility/regime) only has to trust that its one
``bars`` argument already ends at "now" -- the same contract
``Strategy.on_bar`` has always used. Here, multiple *independent* bar
streams are combined, and it is entirely realistic for a caller to hand
over a coarser timeframe's series where the last bar is still
in-progress (e.g. at 09:05, a 1-hour series' 09:00 bar has opened but
not yet closed) even though its *timestamp* already looks like it's "at
or before now". A naive ``bar.timestamp <= now`` filter would happily
accept that still-forming bar and leak its (necessarily incomplete, and
in a backtest, potentially clairvoyant) high/low/close into the
classification. This module instead computes each timeframe's actual
bar duration and only keeps a bar once ``bar.timestamp + duration <=
timestamp`` -- i.e. its close time has genuinely passed -- before
handing anything to ``classify_trend``.

**Missing timeframe data is handled safely, not as an error.** A
timeframe absent from the caller's mapping, present but empty, or
present but with fewer than two completed bars, is simply left out of
the ``alignment`` result entirely (matching this package's established
"absence means not recorded" convention -- see
``MarketContext.confidence_scores``) rather than being forced into a
placeholder direction or raising.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping, Optional, Sequence

from ..models import Bar
from ..research.regime import classify_trend
from .models import TrendState

#: Canonical timeframe labels, ascending by duration -- also the key
#: vocabulary callers use in ``bars_by_timeframe``/the output ``alignment``
#: mapping.
TIMEFRAME_ORDER: tuple[str, ...] = ("1m", "5m", "15m", "1h", "1d")

#: How long one bar spans, per timeframe label -- used only to decide
#: whether a given bar has actually closed by ``timestamp`` (see the
#: module docstring). Not a resampling utility; this module never
#: aggregates bars from a finer timeframe into a coarser one.
_TIMEFRAME_DURATIONS: Mapping[str, timedelta] = {
    "1m": timedelta(minutes=1),
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "1d": timedelta(days=1),
}

#: Weight each timeframe contributes to ``alignment_score`` -- rank-based
#: (1 through 5, ascending with ``TIMEFRAME_ORDER``) so a longer-horizon
#: timeframe counts for more, without hardcoding the huge, mismatched
#: numeric ratios raw minute-durations would imply (a naive 1440:1 daily
#: vs. 1-minute weighting would make every shorter timeframe irrelevant).
#: A documented, overridable convention, not a tuned parameter.
_TIMEFRAME_WEIGHTS: Mapping[str, float] = {
    tf: float(rank) for rank, tf in enumerate(TIMEFRAME_ORDER, start=1)
}

#: research.regime.classify_trend's own vocabulary ("bullish"/"bearish"/
#: "sideways") mapped onto context/models.py's existing TrendState enum.
_TREND_LABELS: Mapping[str, TrendState] = {
    "bullish": TrendState.BULLISH,
    "bearish": TrendState.BEARISH,
    "sideways": TrendState.NEUTRAL,
}

#: Signed contribution of each direction toward alignment_score's
#: weighted average -- NEUTRAL is a real, counted reading (0), not
#: excluded the way a genuinely missing timeframe is.
_DIRECTION_VALUE: Mapping[TrendState, float] = {
    TrendState.BULLISH: 1.0,
    TrendState.BEARISH: -1.0,
    TrendState.NEUTRAL: 0.0,
}


def _completed_bars(bars: Sequence[Bar], timeframe: str, as_of: datetime) -> list[Bar]:
    """Bars for ``timeframe`` whose close time has already passed as of
    ``as_of`` -- see the module docstring for why this is stricter than
    a plain ``timestamp <= as_of`` check."""
    duration = _TIMEFRAME_DURATIONS[timeframe]
    return [b for b in bars if b.timestamp + duration <= as_of]


@dataclass(frozen=True)
class TimeframeAlignment:
    """A multi-timeframe trend-alignment snapshot as of ``timestamp``,
    for one symbol. Immutable, matching ``SessionContext``/
    ``VolatilityContext``/``RegimeContext``.

    ``alignment`` only contains keys for timeframes that actually had
    enough completed history to classify -- a missing key means "no
    data available for that timeframe", never a fabricated direction.
    ``alignment_score`` is the magnitude (``[0.0, 1.0]``) of the
    weighted-average direction across whatever timeframes *are*
    present: 1.0 means every present timeframe agrees on one direction,
    0.0 means either no data at all or a perfect split between bullish
    and bearish (or everything neutral)."""

    timestamp: datetime
    symbol: str
    alignment: dict[str, TrendState]
    alignment_score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "symbol": self.symbol,
            "alignment": {tf: state.value for tf, state in self.alignment.items()},
            "alignment_score": self.alignment_score,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TimeframeAlignment":
        timestamp = data["timestamp"]
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp)
        alignment = {
            tf: TrendState(value) for tf, value in data.get("alignment", {}).items()
        }
        return cls(
            timestamp=timestamp,
            symbol=data["symbol"],
            alignment=alignment,
            alignment_score=data.get("alignment_score", 0.0),
        )


def _compute_alignment_score(alignment: Mapping[str, TrendState]) -> float:
    if not alignment:
        return 0.0
    weighted_sum = 0.0
    weight_total = 0.0
    for tf, state in alignment.items():
        weight = _TIMEFRAME_WEIGHTS[tf]
        weighted_sum += weight * _DIRECTION_VALUE[state]
        weight_total += weight
    if weight_total == 0.0:
        return 0.0
    return min(1.0, abs(weighted_sum / weight_total))


def classify_timeframe_alignment(
    timestamp: datetime,
    symbol: str,
    bars_by_timeframe: Optional[Mapping[str, Sequence[Bar]]],
) -> TimeframeAlignment:
    """Builds a ``TimeframeAlignment`` from one bar series per timeframe.

    ``bars_by_timeframe`` maps a subset (or all) of ``TIMEFRAME_ORDER``
    to that timeframe's own bar history, each ending at or after
    ``timestamp`` -- this function itself filters each series down to
    only bars that have genuinely closed by ``timestamp`` (see the
    module docstring), so a caller does not need to pre-trim every
    stream perfectly; it only needs to not omit anything that *should*
    be visible. An absent, empty, or too-short (fewer than 2 completed
    bars) timeframe is simply left out of the result -- never an error.
    """
    alignment: dict[str, TrendState] = {}
    bars_by_timeframe = bars_by_timeframe or {}

    for tf in TIMEFRAME_ORDER:
        bars = bars_by_timeframe.get(tf)
        if not bars:
            continue
        completed = _completed_bars(bars, tf, timestamp)
        if len(completed) < 2:
            continue
        closes = [b.close for b in completed]
        alignment[tf] = _TREND_LABELS[classify_trend(closes)]

    return TimeframeAlignment(
        timestamp=timestamp,
        symbol=symbol,
        alignment=alignment,
        alignment_score=_compute_alignment_score(alignment),
    )
