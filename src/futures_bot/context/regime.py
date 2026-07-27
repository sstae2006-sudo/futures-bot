"""Market Regime Detection -- Market Context Engine Phase 4 (2026-07-27).

Classifies overall market behavior into one of five mutually exclusive
regimes (``MarketRegime``, from ``context/models.py``): ``TRENDING_UP``,
``TRENDING_DOWN``, ``RANGING``, ``HIGH_VOLATILITY``, ``LOW_VOLATILITY``.

**Classification only.** This describes the environment; it is never
consulted for a trade decision and never produces a buy/sell signal --
the same hard boundary every other file in ``context/`` is held to (see
``models.MarketContext``'s docstring and docs/ARCHITECTURE.md's "Market
Context Engine" section).

**No parameter optimization this phase.** Every threshold below is
either an already-existing convention reused from elsewhere in this
codebase, or a well-known, unmodified technical-analysis default (ADX
>= 25 as "trending", attributed to Wilder himself) -- nothing here was
tuned against this project's own data.

Reuse, not re-derivation:

- ``strategy.indicators.adx``: trend *strength* (0-100 scale; the
  standard "ADX >= 25 means actually trending, below that treat the
  market as range-bound" reading).
- ``research.regime.classify_trend``: trend *direction*
  (bullish/bearish/sideways via a simple start-to-end % move over the
  trailing 20 closes) -- already look-ahead-safe by construction
  (operates purely on whatever ``closes`` slice it's given) and already
  used for exactly this purpose elsewhere in this codebase, so reused
  verbatim rather than re-derived a second way (e.g. a fresh EMA
  crossover).
- ``context.volatility.analyze_volatility``: the ``volatility_ratio``/
  ``VolatilityState`` signal already built in Phase 2b, rather than
  re-deriving ATR-ratio math a third time in this file. Inherits that
  module's look-ahead safety for free (a trailing window ending at the
  last bar given, never a whole-series statistic).

**Priority when signals could point different ways (explicit, not
implicit):** extreme volatility dominates trend/range labeling -- a
violent move overwhelms whatever directional structure might otherwise
be read into it. Otherwise a strong ADX reading (with a non-sideways
direction) decides ``TRENDING_UP``/``TRENDING_DOWN``. Otherwise low
volatility is reported as its own regime. Otherwise the default is
``RANGING``. Missing/insufficient data (not even enough bars for
``analyze_volatility``'s own cheapest signal) is handled safely:
``UNKNOWN`` with confidence 0.0, never an exception or a guess. A
partial reading (enough bars for volatility but not yet for ADX, which
needs ``2 * adx_period`` bars) degrades gracefully rather than failing
outright -- the ADX-dependent branch is simply skipped.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence

from ..models import Bar
from ..research.regime import classify_trend
from ..strategy.indicators import adx
from .models import MarketRegime, VolatilityState
from .volatility import EXTREME_RATIO_FLOOR, LOW_RATIO_CEILING, analyze_volatility

#: Wilder's ADX smoothing period -- same default strategy.indicators.adx uses.
DEFAULT_ADX_PERIOD = 14

#: The conventional technical-analysis threshold: ADX >= this is
#: considered "actually trending" (Wilder's own writing on ADX) -- not
#: tuned for this codebase, reused as the industry-standard value.
ADX_TRENDING_THRESHOLD = 25.0

#: ADX's 0-100 scale mapped to confidence: this many points of ADX is
#: treated as "fully confident" (1.0), capped there rather than
#: requiring an implausibly extreme reading to ever reach 1.0. Chosen
#: so the task's own worked example lines up exactly: ADX 39 -> 0.78.
ADX_CONFIDENCE_SCALE = 50.0


@dataclass(frozen=True)
class RegimeContext:
    """A market-regime snapshot as of ``timestamp``, for one symbol/
    timeframe. Immutable, matching ``SessionContext``/
    ``VolatilityContext``. ``confidence`` is always in ``[0.0, 1.0]``;
    ``UNKNOWN`` always carries confidence ``0.0``, never a fabricated
    guess."""

    timestamp: datetime
    symbol: str
    timeframe: str
    regime: MarketRegime
    confidence: float
    adx: Optional[float]
    #: "bullish" | "bearish" | "sideways" -- research.regime.classify_trend's
    #: own vocabulary, carried through unchanged rather than re-labeled.
    trend_direction: Optional[str]
    volatility_ratio: Optional[float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "regime": self.regime.value,
            "confidence": self.confidence,
            "adx": self.adx,
            "trend_direction": self.trend_direction,
            "volatility_ratio": self.volatility_ratio,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RegimeContext":
        timestamp = data["timestamp"]
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp)
        return cls(
            timestamp=timestamp,
            symbol=data["symbol"],
            timeframe=data["timeframe"],
            regime=MarketRegime(data["regime"]) if data.get("regime") else MarketRegime.UNKNOWN,
            confidence=data.get("confidence", 0.0),
            adx=data.get("adx"),
            trend_direction=data.get("trend_direction"),
            volatility_ratio=data.get("volatility_ratio"),
        )


def _unknown(timestamp: datetime, symbol: str, timeframe: str) -> RegimeContext:
    return RegimeContext(
        timestamp=timestamp,
        symbol=symbol,
        timeframe=timeframe,
        regime=MarketRegime.UNKNOWN,
        confidence=0.0,
        adx=None,
        trend_direction=None,
        volatility_ratio=None,
    )


def _high_volatility_confidence(ratio: Optional[float]) -> float:
    """0.0 right at the EXTREME threshold itself, rising to 1.0 by the
    time ``ratio`` reaches twice that threshold -- "barely extreme" is a
    real but low-confidence reading, not a contradiction."""
    if ratio is None:
        return 0.0
    return min(1.0, max(0.0, (ratio - EXTREME_RATIO_FLOOR) / EXTREME_RATIO_FLOOR))


def _low_volatility_confidence(ratio: Optional[float]) -> float:
    """1.0 at ratio 0 (as calm as possible), falling to 0.0 right at the
    LOW threshold itself."""
    if ratio is None:
        return 0.0
    return min(1.0, max(0.0, (LOW_RATIO_CEILING - ratio) / LOW_RATIO_CEILING))


def _ranging_confidence(adx_value: Optional[float]) -> float:
    """1.0 at ADX 0 (as non-trending as possible), falling to 0.0 right
    at the trending threshold itself (an ADX reading that strong casts
    real doubt on calling the market range-bound at all)."""
    return 1.0 - min(1.0, (adx_value or 0.0) / ADX_TRENDING_THRESHOLD)


def classify_regime(
    timestamp: datetime,
    symbol: str,
    timeframe: str,
    bars: Sequence[Bar],
    adx_period: int = DEFAULT_ADX_PERIOD,
) -> RegimeContext:
    """Builds a ``RegimeContext`` from ``bars``.

    ``bars`` must be history up to and including the bar that just
    closed -- the same convention every other classifier in this
    package already holds callers to. Every signal this function reads
    (``adx``, ``classify_trend``, ``analyze_volatility``) only ever
    looks at ``bars`` itself, so a shorter (earlier) slice can never be
    influenced by bars that would come after it -- no look-ahead is
    possible by construction, inherited from those three functions'
    own guarantees.
    """
    volatility_ctx = analyze_volatility(timestamp, symbol, timeframe, bars)
    if volatility_ctx.state is VolatilityState.UNKNOWN:
        # Not even enough bars for the cheapest signal -- nothing else
        # here would be trustworthy either.
        return _unknown(timestamp, symbol, timeframe)

    adx_value = adx(bars, period=adx_period)
    adx_float = float(adx_value) if adx_value is not None else None

    closes = [b.close for b in bars]
    direction = classify_trend(closes)
    ratio = volatility_ctx.volatility_ratio

    if volatility_ctx.state is VolatilityState.EXTREME:
        regime = MarketRegime.HIGH_VOLATILITY
        confidence = _high_volatility_confidence(ratio)
    elif adx_float is not None and adx_float >= ADX_TRENDING_THRESHOLD and direction != "sideways":
        regime = MarketRegime.TRENDING_UP if direction == "bullish" else MarketRegime.TRENDING_DOWN
        confidence = min(1.0, adx_float / ADX_CONFIDENCE_SCALE)
    elif volatility_ctx.state is VolatilityState.LOW:
        regime = MarketRegime.LOW_VOLATILITY
        confidence = _low_volatility_confidence(ratio)
    else:
        regime = MarketRegime.RANGING
        confidence = _ranging_confidence(adx_float)

    return RegimeContext(
        timestamp=timestamp,
        symbol=symbol,
        timeframe=timeframe,
        regime=regime,
        confidence=confidence,
        adx=adx_float,
        trend_direction=direction,
        volatility_ratio=ratio,
    )
