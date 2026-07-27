"""Volatility Context -- Market Context Engine Phase 3 (2026-07-27).

Classifies how volatile a symbol/timeframe currently is, relative to its
own recent history: ``VolatilityState`` (``LOW``/``NORMAL``/``HIGH``/
``EXTREME``/``UNKNOWN``, from ``context/models.py`` -- unchanged since
Phase 1, reused here rather than redefined).

Reuse, not re-derivation: ATR itself comes straight from
``strategy.indicators.atr_series`` (the same Wilder's-smoothing
implementation every strategy already uses) -- this module does not
reimplement true range or ATR math a second time.

**Deliberately NOT reusing ``research.regime.classify_volatility`` /
``compute_regimes``'s tercile approach**, even though it already does
ATR-based volatility bucketing. Two concrete reasons, not just
stylistic preference:

1. Look-ahead safety. ``compute_regimes`` computes its low/high tercile
   cutoffs with ``sorted(atr_values)`` over the *entire* ``bars``
   sequence passed to it, once, up front -- correct for its own use
   case (post-hoc, read-only labeling of trades that have already
   closed, over the whole backtest's bars), but if reused verbatim for
   real-time classification "as of timestamp T", a trade at T would be
   labeled relative to volatility that hasn't happened yet whenever
   ``bars`` includes anything after T. This phase's explicit
   requirement is the opposite: only ever use information available at
   that timestamp. So instead, "average ATR" here is a *trailing*
   window ending at the last bar in whatever ``bars`` the caller
   passed in -- safe by construction, since a caller that (per this
   codebase's established convention -- see ``Strategy.on_bar`` and
   ``ContextEngine.build_context``) only ever passes bars up to "now"
   can never leak a future ATR value into the average.
2. Output shape. The task asks for a ratio-based, four-state result
   (``current_atr / average_atr`` compared against fixed thresholds,
   matching the worked example ``{current_atr: 18, average_atr: 12,
   volatility_ratio: 1.5, state: HIGH}``) -- not tercile buckets over a
   whole dataset, which don't produce a stable "1.5x normal" figure at
   all.

Realized volatility (stdev of simple close-to-close returns over the
same trailing window) has no existing equivalent in this codebase --
newly implemented here, deliberately kept simple (unannualized) since
the task doesn't specify a particular model and this is a descriptive
context signal, not a risk model; a future phase can refine it if a
consumer needs annualized figures.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence

from ..models import Bar
from ..strategy.indicators import atr_series
from .models import VolatilityState

#: ATR's own smoothing window -- same default strategy/indicators.py uses.
DEFAULT_ATR_PERIOD = 14

#: How many trailing ATR values form "the historical average" a fresh
#: reading is compared against. Not hardcoded further than this one,
#: documented, overridable constant -- see the module docstring.
DEFAULT_AVERAGE_LOOKBACK = 20

#: volatility_ratio = current_atr / average_atr thresholds. Chosen so the
#: task's own worked example (ratio 1.5) lands on HIGH, the boundary
#: nearest the example while leaving equal-width bands around NORMAL.
LOW_RATIO_CEILING = 0.75
HIGH_RATIO_FLOOR = 1.25
EXTREME_RATIO_FLOOR = 2.0


def classify_volatility_ratio(
    ratio: Optional[float],
    low_ceiling: float = LOW_RATIO_CEILING,
    high_floor: float = HIGH_RATIO_FLOOR,
    extreme_floor: float = EXTREME_RATIO_FLOOR,
) -> VolatilityState:
    """Maps a ``current_atr / average_atr`` ratio to a ``VolatilityState``.
    ``None`` (no ratio could be formed -- see ``analyze_volatility``) is
    always ``UNKNOWN``, never a fabricated guess."""
    if ratio is None:
        return VolatilityState.UNKNOWN
    if ratio < low_ceiling:
        return VolatilityState.LOW
    if ratio < high_floor:
        return VolatilityState.NORMAL
    if ratio < extreme_floor:
        return VolatilityState.HIGH
    return VolatilityState.EXTREME


def _realized_volatility(bars: Sequence[Bar], lookback: int) -> Optional[float]:
    """Stdev of simple close-to-close returns over the trailing
    ``lookback + 1`` bars (needs ``lookback + 1`` closes to form
    ``lookback`` returns) -- unannualized. ``None`` if there isn't
    enough history or every trailing close is identical/zero (no
    meaningful return series to measure)."""
    window = bars[-(lookback + 1):]
    closes = [float(b.close) for b in window]
    if len(closes) < 2:
        return None

    returns = [
        (closes[i] / closes[i - 1]) - 1.0
        for i in range(1, len(closes))
        if closes[i - 1] != 0
    ]
    if len(returns) < 2:
        return None

    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return variance ** 0.5


@dataclass(frozen=True)
class VolatilityContext:
    """A volatility snapshot as of ``timestamp``, for one symbol/
    timeframe. Immutable, matching ``SessionContext``/``MarketContext``.
    Every numeric field is ``Optional`` -- ``None`` (with
    ``state=UNKNOWN``) is the safe, explicit "not enough history yet"
    result, never a fabricated 0 or a raised exception."""

    timestamp: datetime
    symbol: str
    timeframe: str
    current_atr: Optional[float]
    average_atr: Optional[float]
    volatility_ratio: Optional[float]
    realized_volatility: Optional[float]
    state: VolatilityState

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "current_atr": self.current_atr,
            "average_atr": self.average_atr,
            "volatility_ratio": self.volatility_ratio,
            "realized_volatility": self.realized_volatility,
            "state": self.state.value,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "VolatilityContext":
        timestamp = data["timestamp"]
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp)
        return cls(
            timestamp=timestamp,
            symbol=data["symbol"],
            timeframe=data["timeframe"],
            current_atr=data.get("current_atr"),
            average_atr=data.get("average_atr"),
            volatility_ratio=data.get("volatility_ratio"),
            realized_volatility=data.get("realized_volatility"),
            state=VolatilityState(data["state"]) if data.get("state") else VolatilityState.UNKNOWN,
        )


def analyze_volatility(
    timestamp: datetime,
    symbol: str,
    timeframe: str,
    bars: Sequence[Bar],
    atr_period: int = DEFAULT_ATR_PERIOD,
    average_lookback: int = DEFAULT_AVERAGE_LOOKBACK,
) -> VolatilityContext:
    """Builds a ``VolatilityContext`` from ``bars``.

    ``bars`` must be history up to and including the bar that just
    closed -- the same convention ``Strategy.on_bar``/
    ``ContextEngine.build_context`` already hold every caller to.
    Nothing here reads or requires anything beyond the last element of
    ``bars``: ``current_atr`` is the *last* value of
    ``atr_series(bars, atr_period)``, and ``average_atr`` is the mean of
    a trailing window of that same series ending at that last value --
    so a shorter (earlier) ``bars`` slice can never be influenced by
    bars that would come after it. See the module docstring for why
    that specifically rules out reusing
    ``research.regime.classify_volatility``'s whole-series tercile
    approach as-is.

    Supports any symbol/timeframe/ATR-period/lookback combination --
    nothing here is hardcoded to one instrument; ``symbol``/``timeframe``
    are carried onto the result purely as identifying context, exactly
    like ``SessionContext``.

    Missing/insufficient data (fewer than ``atr_period + 1`` bars) is
    handled safely: returns a context with every numeric field ``None``
    and ``state=UNKNOWN``, never an exception.
    """
    atr_values = atr_series(bars, period=atr_period)
    realized_vol = _realized_volatility(bars, average_lookback)

    if not atr_values:
        return VolatilityContext(
            timestamp=timestamp,
            symbol=symbol,
            timeframe=timeframe,
            current_atr=None,
            average_atr=None,
            volatility_ratio=None,
            realized_volatility=realized_vol,
            state=VolatilityState.UNKNOWN,
        )

    current_atr = atr_values[-1]
    window = atr_values[-average_lookback:]
    average_atr = sum(window) / len(window)

    ratio = current_atr / average_atr if average_atr != 0 else None
    state = classify_volatility_ratio(ratio)

    return VolatilityContext(
        timestamp=timestamp,
        symbol=symbol,
        timeframe=timeframe,
        current_atr=current_atr,
        average_atr=average_atr,
        volatility_ratio=ratio,
        realized_volatility=realized_vol,
        state=state,
    )
