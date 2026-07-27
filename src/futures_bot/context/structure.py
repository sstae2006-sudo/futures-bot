"""Market Structure Context -- Market Context Engine Phase 6 (2026-07-27).

Detects price-structure characteristics from swing points: higher-highs/
higher-lows (an uptrend structure) or lower-highs/lower-lows (a downtrend
structure), the nearest support/resistance levels around the current
price, and distance from those levels.

**Classification only, strictly descriptive.** This module never
generates a trade, never places or sizes an order, and never overrides
or is consulted by a strategy's own signal -- the same hard boundary
every other file in ``context/`` is held to (see
``models.MarketContext``'s docstring). ``StructureContext`` carries no
reference to a broker, risk manager, engine, or strategy of any kind.

**Reuse:** nothing existing in this codebase performs fractal/swing-point
detection or support/resistance leveling, so this is genuinely new work
-- the same disclosure ``context/regime.py``'s module docstring gives
for liquidity/risk (some dimensions simply have no reuse candidate).
``TrendState`` (``context/models.py``, already used by
``MarketContext.trend_state`` and reused again by
``context/timeframe.py``) is reused here too rather than inventing a
fourth bullish/bearish/neutral vocabulary.

**On "future leakage":** every bar this module ever sees is already-
completed history -- the same "bars up to and including the bar that
just closed" convention every classifier in this package already holds
callers to. Confirming a swing high at index ``i`` does require looking
at bars *after* ``i`` within that already-known history (a peak isn't
confirmed as a swing until price has made lower highs following it) --
but those later indices are themselves already in the past relative to
``timestamp``, so this is confirmation *lag*, not a violation of "only
use information available at that timestamp". The most recent
``swing_window`` bars simply won't have a confirmed swing near them yet
-- the honest behavior of a real-time swing detector, not a bug.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Mapping, Optional, Sequence

from ..models import Bar
from .models import TrendState

#: Bars required on *each side* of a candidate bar for its high/low to
#: count as a confirmed swing point. A documented, overridable
#: convention (a common fractal/swing default), not a tuned parameter --
#: no parameter optimization this phase.
DEFAULT_SWING_WINDOW = 3

#: How many of the most recent confirmed swing highs/lows to compare
#: when judging trend structure and its confidence.
DEFAULT_STRUCTURE_LOOKBACK = 3


def _swing_high_indices(bars: Sequence[Bar], window: int) -> list[int]:
    """Indices of confirmed swing highs -- bar ``i``'s high is strictly
    greater than every high within ``window`` bars on both sides."""
    highs = [b.high for b in bars]
    out = []
    for i in range(window, len(bars) - window):
        if highs[i] > max(highs[i - window:i]) and highs[i] > max(highs[i + 1:i + window + 1]):
            out.append(i)
    return out


def _swing_low_indices(bars: Sequence[Bar], window: int) -> list[int]:
    """Indices of confirmed swing lows -- bar ``i``'s low is strictly
    lower than every low within ``window`` bars on both sides."""
    lows = [b.low for b in bars]
    out = []
    for i in range(window, len(bars) - window):
        if lows[i] < min(lows[i - window:i]) and lows[i] < min(lows[i + 1:i + window + 1]):
            out.append(i)
    return out


def _classify_trend_and_confidence(
    bars: Sequence[Bar],
    swing_high_idx: Sequence[int],
    swing_low_idx: Sequence[int],
    lookback: int,
) -> tuple[TrendState, float]:
    """Compares the most recent ``lookback`` confirmed swing highs and
    lows pairwise: a higher high or higher low votes bullish, a lower
    high or lower low votes bearish. ``structure_confidence`` is the
    winning side's share of all pairwise comparisons -- unanimous
    agreement across every available swing pair is 1.0, a tied/mixed
    read is ``NEUTRAL`` with 0.0 confidence (no structural edge either
    way), and too few confirmed swings to compare at all is ``UNKNOWN``
    with 0.0 confidence."""
    recent_highs = [bars[i].high for i in swing_high_idx[-lookback:]]
    recent_lows = [bars[i].low for i in swing_low_idx[-lookback:]]

    higher_highs = sum(1 for a, b in zip(recent_highs, recent_highs[1:]) if b > a)
    lower_highs = sum(1 for a, b in zip(recent_highs, recent_highs[1:]) if b < a)
    higher_lows = sum(1 for a, b in zip(recent_lows, recent_lows[1:]) if b > a)
    lower_lows = sum(1 for a, b in zip(recent_lows, recent_lows[1:]) if b < a)

    total = len(recent_highs) - 1 if len(recent_highs) > 1 else 0
    total += len(recent_lows) - 1 if len(recent_lows) > 1 else 0
    if total == 0:
        return TrendState.UNKNOWN, 0.0

    bullish_votes = higher_highs + higher_lows
    bearish_votes = lower_highs + lower_lows

    if bullish_votes > bearish_votes:
        return TrendState.BULLISH, bullish_votes / total
    if bearish_votes > bullish_votes:
        return TrendState.BEARISH, bearish_votes / total
    return TrendState.NEUTRAL, 0.0


def _nearest_support(swing_low_idx: Sequence[int], bars: Sequence[Bar], current_price: Decimal) -> Optional[Decimal]:
    """The highest confirmed swing low at or below ``current_price``
    (the nearest real support underneath price) -- or, if every
    confirmed swing low sits above price (price has broken below all of
    them), the most recent confirmed swing low instead, as the closest
    available reference level."""
    if not swing_low_idx:
        return None
    lows_below = [bars[i].low for i in swing_low_idx if bars[i].low <= current_price]
    if lows_below:
        return max(lows_below)
    return bars[swing_low_idx[-1]].low


def _nearest_resistance(swing_high_idx: Sequence[int], bars: Sequence[Bar], current_price: Decimal) -> Optional[Decimal]:
    """The lowest confirmed swing high at or above ``current_price``
    (the nearest real resistance above price) -- or, if every confirmed
    swing high sits below price (price has broken above all of them),
    the most recent confirmed swing high instead."""
    if not swing_high_idx:
        return None
    highs_above = [bars[i].high for i in swing_high_idx if bars[i].high >= current_price]
    if highs_above:
        return min(highs_above)
    return bars[swing_high_idx[-1]].high


@dataclass(frozen=True)
class StructureContext:
    """A price-structure snapshot as of ``timestamp``, for one symbol.
    Immutable, matching every other ``*Context`` in this package.
    ``trend`` is ``UNKNOWN`` (confidence ``0.0``) whenever there isn't
    enough confirmed swing-point history to judge structure at all --
    never a fabricated guess. ``support``/``resistance`` and their
    distances are independently ``None`` when no swing low/high has
    been confirmed yet, even if ``trend`` itself was classifiable."""

    timestamp: datetime
    symbol: str
    trend: TrendState
    support: Optional[Decimal]
    resistance: Optional[Decimal]
    distance_to_support: Optional[Decimal]
    distance_to_resistance: Optional[Decimal]
    structure_confidence: float

    def to_dict(self) -> dict[str, Any]:
        def _f(value: Optional[Decimal]) -> Optional[float]:
            return float(value) if value is not None else None

        return {
            "timestamp": self.timestamp.isoformat(),
            "symbol": self.symbol,
            "trend": self.trend.value,
            "support": _f(self.support),
            "resistance": _f(self.resistance),
            "distance_to_support": _f(self.distance_to_support),
            "distance_to_resistance": _f(self.distance_to_resistance),
            "structure_confidence": self.structure_confidence,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "StructureContext":
        def _d(value: Any) -> Optional[Decimal]:
            return Decimal(str(value)) if value is not None else None

        timestamp = data["timestamp"]
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp)
        return cls(
            timestamp=timestamp,
            symbol=data["symbol"],
            trend=TrendState(data["trend"]) if data.get("trend") else TrendState.UNKNOWN,
            support=_d(data.get("support")),
            resistance=_d(data.get("resistance")),
            distance_to_support=_d(data.get("distance_to_support")),
            distance_to_resistance=_d(data.get("distance_to_resistance")),
            structure_confidence=data.get("structure_confidence", 0.0),
        )


def analyze_structure(
    timestamp: datetime,
    symbol: str,
    bars: Sequence[Bar],
    swing_window: int = DEFAULT_SWING_WINDOW,
    structure_lookback: int = DEFAULT_STRUCTURE_LOOKBACK,
) -> StructureContext:
    """Builds a ``StructureContext`` from ``bars``.

    ``bars`` must be history up to and including the bar that just
    closed -- the same convention every other classifier in this
    package already holds callers to. Needs at least
    ``2 * swing_window + 1`` bars to confirm even a single swing point;
    fewer than that returns ``UNKNOWN``/``None`` throughout, never an
    exception.
    """
    if len(bars) < 2 * swing_window + 1:
        return StructureContext(
            timestamp=timestamp, symbol=symbol, trend=TrendState.UNKNOWN,
            support=None, resistance=None,
            distance_to_support=None, distance_to_resistance=None,
            structure_confidence=0.0,
        )

    swing_highs = _swing_high_indices(bars, swing_window)
    swing_lows = _swing_low_indices(bars, swing_window)

    trend, confidence = _classify_trend_and_confidence(bars, swing_highs, swing_lows, structure_lookback)

    current_price = bars[-1].close
    support = _nearest_support(swing_lows, bars, current_price)
    resistance = _nearest_resistance(swing_highs, bars, current_price)

    return StructureContext(
        timestamp=timestamp,
        symbol=symbol,
        trend=trend,
        support=support,
        resistance=resistance,
        distance_to_support=(current_price - support) if support is not None else None,
        distance_to_resistance=(resistance - current_price) if resistance is not None else None,
        structure_confidence=confidence,
    )
