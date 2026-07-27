"""Trend State -- Market Context Engine Phase 8 (2026-07-27).

Classifies pure trend *direction* (``TrendState``: BULLISH/BEARISH/
NEUTRAL/UNKNOWN), independent of volatility regime or the stricter
data requirements a composite classification needs. ``context/regime.py``'s
``MarketRegime`` already combines direction with strength *and*
volatility into one label (``TRENDING_UP``/``TRENDING_DOWN``/``RANGING``/
``HIGH_VOLATILITY``/``LOW_VOLATILITY``) -- and, because it requires
``analyze_volatility`` to have enough history too, returns ``UNKNOWN``
outright whenever ATR data is thin, even if direction alone was
perfectly computable from just two closes. ``TrendState`` is the
simpler, standalone direction-only reading `context/models.py`'s
docstring always described it as -- for a consumer that wants "is this
market trending up or down" without volatility coupling.

**Reuse, not re-derivation:** direction comes from
``research.regime.classify_trend`` -- the same function
``context/regime.py`` and ``context/timeframe.py`` already reuse for
this exact purpose, not a fourth re-derivation of the same start-to-end
percentage-move logic. Confidence reuses ``strategy.indicators.adx``
plus ``context/regime.py``'s own ``ADX_TRENDING_THRESHOLD``/
``ADX_CONFIDENCE_SCALE`` constants, so "how strong is this direction"
sits on the exact same scale ``regime.py`` already established, instead
of a second, subtly different one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence

from ..models import Bar
from ..research.regime import classify_trend
from ..strategy.indicators import adx
from .models import TrendState
from .regime import ADX_CONFIDENCE_SCALE, ADX_TRENDING_THRESHOLD, DEFAULT_ADX_PERIOD

#: research.regime.classify_trend's own vocabulary mapped onto
#: TrendState -- the same mapping context/timeframe.py already uses.
_TREND_LABELS: Mapping[str, TrendState] = {
    "bullish": TrendState.BULLISH,
    "bearish": TrendState.BEARISH,
    "sideways": TrendState.NEUTRAL,
}


@dataclass(frozen=True)
class TrendContext:
    """A pure trend-direction snapshot as of ``timestamp``, for one
    symbol. Immutable, matching every other ``*Context`` in this
    package. ``confidence`` is always ``[0.0, 1.0]``; ``UNKNOWN``
    always carries confidence ``0.0``."""

    timestamp: datetime
    symbol: str
    trend: TrendState
    confidence: float
    adx: Optional[float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "symbol": self.symbol,
            "trend": self.trend.value,
            "confidence": self.confidence,
            "adx": self.adx,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TrendContext":
        timestamp = data["timestamp"]
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp)
        return cls(
            timestamp=timestamp,
            symbol=data["symbol"],
            trend=TrendState(data["trend"]) if data.get("trend") else TrendState.UNKNOWN,
            confidence=data.get("confidence", 0.0),
            adx=data.get("adx"),
        )


def analyze_trend(
    timestamp: datetime,
    symbol: str,
    bars: Sequence[Bar],
    adx_period: int = DEFAULT_ADX_PERIOD,
) -> TrendContext:
    """Builds a ``TrendContext`` from ``bars``.

    ``bars`` must be history up to and including the bar that just
    closed -- the same convention every classifier in this package
    already holds callers to. ``classify_trend`` only needs 2 closes,
    so direction is available far sooner than ``regime.py``'s composite
    classification; ``adx`` (needed for confidence) may still be
    ``None`` with fewer bars, in which case confidence is honestly
    ``0.0`` rather than a guess -- direction is still reported, just
    with no confidence behind it yet.
    """
    if len(bars) < 2:
        return TrendContext(timestamp=timestamp, symbol=symbol, trend=TrendState.UNKNOWN, confidence=0.0, adx=None)

    closes = [b.close for b in bars]
    trend = _TREND_LABELS[classify_trend(closes)]

    adx_value = adx(bars, period=adx_period)
    adx_float = float(adx_value) if adx_value is not None else None

    if adx_float is None:
        confidence = 0.0
    elif trend is TrendState.NEUTRAL:
        # Low ADX corroborates "no trend" -- a confident NEUTRAL read is
        # a low ADX reading, the same shape as regime.py's ranging
        # confidence.
        confidence = 1.0 - min(1.0, adx_float / ADX_TRENDING_THRESHOLD)
    else:
        confidence = min(1.0, adx_float / ADX_CONFIDENCE_SCALE)

    return TrendContext(timestamp=timestamp, symbol=symbol, trend=trend, confidence=confidence, adx=adx_float)
