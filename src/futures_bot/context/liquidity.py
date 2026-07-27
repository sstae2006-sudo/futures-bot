"""Liquidity Context -- Market Context Engine Phase 8 (2026-07-27).

Classifies current liquidity (``LiquidityState``: THIN/NORMAL/DEEP/
UNKNOWN) from relative volume: how does the current bar's volume
compare to a trailing average?

**Reuse:** the trailing average itself is computed with
``strategy.indicators.sma`` -- the same simple-moving-average primitive
used throughout this codebase -- rather than a new averaging loop.
There is no existing *general-purpose* liquidity/relative-volume
*classifier* anywhere in this codebase to reuse wholesale (confirmed by
search): ``strategy/trend_pullback/strategy.py`` computes a similarly-
shaped ``volume_ratio`` (``bar.volume / snap.volume_sma``), but only as
that one strategy's own entry-context analytics field, tied to its own
``IndicatorSnapshot`` -- reusing it directly would create an
inappropriate `context/` -> `strategy/` dependency (the wrong
direction; see docs/ARCHITECTURE.md's dependency rules), and it isn't a
general classifier in the first place, just one strategy's own
descriptive field.

**What's genuinely new here, and why:** turning a volume ratio into a
THIN/NORMAL/DEEP classification with a confidence score has no existing
equivalent anywhere in this codebase. This module builds that new
classifier from the existing ``sma`` primitive, following the exact
same trailing-window-ratio *shape* ``context/volatility.py`` already
established for this package (current reading / trailing average of
the same series) -- reusing the established *pattern*, not copying
code, and inheriting the same look-ahead safety for the same reason:
the trailing window always ends at the last bar given, never anything
beyond it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Mapping, Optional, Sequence

from ..models import Bar
from ..strategy.indicators import sma
from .models import LiquidityState

#: How many trailing bars' volume forms "the recent average" a fresh
#: reading is compared against -- the same role
#: volatility.DEFAULT_AVERAGE_LOOKBACK plays for ATR.
DEFAULT_VOLUME_LOOKBACK = 20

#: volume_ratio = current_volume / average_volume thresholds. Mirrors
#: volatility.py's ratio-threshold shape (documented, overridable
#: constants, not tuned against real data).
THIN_RATIO_CEILING = 0.5
DEEP_RATIO_FLOOR = 1.5


def classify_liquidity_ratio(
    ratio: Optional[float],
    thin_ceiling: float = THIN_RATIO_CEILING,
    deep_floor: float = DEEP_RATIO_FLOOR,
) -> LiquidityState:
    """Maps a ``current_volume / average_volume`` ratio to a
    ``LiquidityState``. ``None`` (no ratio could be formed) is always
    ``UNKNOWN``, never a fabricated guess."""
    if ratio is None:
        return LiquidityState.UNKNOWN
    if ratio < thin_ceiling:
        return LiquidityState.THIN
    if ratio < deep_floor:
        return LiquidityState.NORMAL
    return LiquidityState.DEEP


def _thin_confidence(ratio: Optional[float], thin_ceiling: float) -> float:
    if ratio is None:
        return 0.0
    return min(1.0, max(0.0, (thin_ceiling - ratio) / thin_ceiling))


def _deep_confidence(ratio: Optional[float], deep_floor: float) -> float:
    if ratio is None:
        return 0.0
    return min(1.0, max(0.0, (ratio - deep_floor) / deep_floor))


def _normal_confidence(ratio: Optional[float], thin_ceiling: float, deep_floor: float) -> float:
    if ratio is None:
        return 0.0
    center = (thin_ceiling + deep_floor) / 2
    half_width = (deep_floor - thin_ceiling) / 2
    if half_width <= 0:
        return 0.0
    return min(1.0, max(0.0, 1.0 - abs(ratio - center) / half_width))


@dataclass(frozen=True)
class LiquidityContext:
    """A liquidity snapshot as of ``timestamp``, for one symbol/
    timeframe. Immutable, matching every other ``*Context`` in this
    package. ``confidence`` is always ``[0.0, 1.0]``; ``UNKNOWN``
    always carries confidence ``0.0``."""

    timestamp: datetime
    symbol: str
    timeframe: str
    current_volume: Optional[int]
    average_volume: Optional[float]
    volume_ratio: Optional[float]
    state: LiquidityState
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "current_volume": self.current_volume,
            "average_volume": self.average_volume,
            "volume_ratio": self.volume_ratio,
            "state": self.state.value,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LiquidityContext":
        timestamp = data["timestamp"]
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp)
        return cls(
            timestamp=timestamp,
            symbol=data["symbol"],
            timeframe=data["timeframe"],
            current_volume=data.get("current_volume"),
            average_volume=data.get("average_volume"),
            volume_ratio=data.get("volume_ratio"),
            state=LiquidityState(data["state"]) if data.get("state") else LiquidityState.UNKNOWN,
            confidence=data.get("confidence", 0.0),
        )


def analyze_liquidity(
    timestamp: datetime,
    symbol: str,
    timeframe: str,
    bars: Sequence[Bar],
    lookback: int = DEFAULT_VOLUME_LOOKBACK,
    thin_ceiling: float = THIN_RATIO_CEILING,
    deep_floor: float = DEEP_RATIO_FLOOR,
) -> LiquidityContext:
    """Builds a ``LiquidityContext`` from ``bars``.

    ``bars`` must be history up to and including the bar that just
    closed -- the same convention every classifier in this package
    already holds callers to. The trailing average (via ``sma``)
    always ends at the last bar given, the same current-bar-inclusive
    window shape ``analyze_volatility`` already uses, so a shorter
    (earlier) ``bars`` slice can never be influenced by bars that would
    come after it.

    Only the trailing ``lookback`` bars are ever converted to
    ``Decimal`` -- unlike ``atr_series``'s Wilder smoothing (whose
    result is seed-sensitive to exactly where a truncated slice starts,
    so ``analyze_volatility`` cannot safely narrow its input), a plain
    average has no such sensitivity: slicing to the trailing window
    *before* converting produces the identical ``average_volume`` while
    skipping O(n) work over history this function was never going to
    use anyway -- a real, measured cost with a large ``bars`` list (see
    ``tools/benchmark_context_engine.py``), fixed without changing any
    output.
    """
    if not bars:
        return LiquidityContext(
            timestamp=timestamp, symbol=symbol, timeframe=timeframe,
            current_volume=None, average_volume=None, volume_ratio=None,
            state=LiquidityState.UNKNOWN, confidence=0.0,
        )

    current_volume = bars[-1].volume
    window_bars = bars[-lookback:]
    volumes = [Decimal(b.volume) for b in window_bars]
    period = len(volumes)
    average = sma(volumes, period=period)

    if average is None or average == 0:
        ratio = None
    else:
        ratio = float(current_volume) / float(average)

    state = classify_liquidity_ratio(ratio, thin_ceiling, deep_floor)
    if state is LiquidityState.THIN:
        confidence = _thin_confidence(ratio, thin_ceiling)
    elif state is LiquidityState.DEEP:
        confidence = _deep_confidence(ratio, deep_floor)
    elif state is LiquidityState.NORMAL:
        confidence = _normal_confidence(ratio, thin_ceiling, deep_floor)
    else:
        confidence = 0.0

    return LiquidityContext(
        timestamp=timestamp,
        symbol=symbol,
        timeframe=timeframe,
        current_volume=current_volume,
        average_volume=float(average) if average is not None else None,
        volume_ratio=ratio,
        state=state,
        confidence=confidence,
    )
