"""Risk Context -- Market Context Engine Phase 8 (2026-07-27).

Classifies current market-condition risk (``RiskState``: LOW/ELEVATED/
HIGH/UNKNOWN) as a **composite of two already-computed signals** --
``VolatilityState`` and ``MarketRegime`` -- rather than any new
market-data analysis. This is exactly what ``context_engine.py``'s own
Phase-1 stub docstring for ``_classify_risk`` anticipated: "likely a
composite of the other classifications above (e.g. VOLATILE regime +
HIGH volatility => ELEVATED/HIGH risk_state), decided once real
thresholds exist for those inputs." Both inputs now have real
thresholds (Phase 2b/2c), so this module implements exactly that
composite -- no new data reading, no new indicator.

``volatility_state`` is the primary signal (the more directly
"how dangerous does price action look right now" read); ``market_regime``
only corroborates or acts as a lower-confidence fallback when
volatility itself is ``UNKNOWN``. See ``assess_risk``'s docstring for
the exact decision order.

**Unrelated to, and never consulted by,** ``risk.manager.RiskManager``
(the account/session risk gate -- daily loss kill switch, trade caps,
trading-hours filter) -- naming collision only, no code relationship.
This module has no import of, and no reference to, ``risk.manager``,
``brokers``, or ``engine`` of any kind; see ``context/models.py``'s
``RiskState`` docstring and docs/ARCHITECTURE.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from .models import MarketRegime, RiskState, VolatilityState


@dataclass(frozen=True)
class RiskContext:
    """A market-condition-risk snapshot as of ``timestamp``, for one
    symbol. Immutable, matching every other ``*Context`` in this
    package. ``volatility_state``/``market_regime`` are carried through
    unchanged -- the two inputs this composite was built from, for
    transparency/debugging, not re-derived from anything else."""

    timestamp: datetime
    symbol: str
    state: RiskState
    confidence: float
    volatility_state: VolatilityState
    market_regime: MarketRegime

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "symbol": self.symbol,
            "state": self.state.value,
            "confidence": self.confidence,
            "volatility_state": self.volatility_state.value,
            "market_regime": self.market_regime.value,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RiskContext":
        timestamp = data["timestamp"]
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp)
        return cls(
            timestamp=timestamp,
            symbol=data["symbol"],
            state=RiskState(data["state"]) if data.get("state") else RiskState.UNKNOWN,
            confidence=data.get("confidence", 0.0),
            volatility_state=(
                VolatilityState(data["volatility_state"])
                if data.get("volatility_state") else VolatilityState.UNKNOWN
            ),
            market_regime=(
                MarketRegime(data["market_regime"])
                if data.get("market_regime") else MarketRegime.UNKNOWN
            ),
        )


def assess_risk(
    timestamp: datetime,
    symbol: str,
    volatility_state: VolatilityState,
    market_regime: MarketRegime,
) -> RiskContext:
    """Builds a ``RiskContext`` purely from two already-classified
    inputs -- no bars, no new indicator math.

    Decision order:

    1. ``volatility_state`` known -> it drives the base decision
       (EXTREME -> HIGH, HIGH -> ELEVATED, LOW/NORMAL -> LOW),
       full confidence since it's the direct, already-real signal.
    2. Even when volatility alone reads LOW/NORMAL, a ``market_regime``
       independently reading ``HIGH_VOLATILITY`` corroborates elevated
       risk rather than being silently overridden -- two independent
       signals agreeing on "risky" is not dismissed just because one of
       them individually looked calm.
    3. ``volatility_state`` UNKNOWN -> fall back to ``market_regime``
       alone (``HIGH_VOLATILITY`` -> HIGH, ``LOW_VOLATILITY`` -> LOW),
       at reduced confidence since it's a weaker, secondary signal for
       this purpose.
    4. Neither input known -> ``UNKNOWN``, confidence ``0.0``.
    """
    if volatility_state is VolatilityState.EXTREME:
        return RiskContext(timestamp, symbol, RiskState.HIGH, 1.0, volatility_state, market_regime)
    if volatility_state is VolatilityState.HIGH:
        return RiskContext(timestamp, symbol, RiskState.ELEVATED, 1.0, volatility_state, market_regime)
    if volatility_state in (VolatilityState.LOW, VolatilityState.NORMAL):
        if market_regime is MarketRegime.HIGH_VOLATILITY:
            return RiskContext(timestamp, symbol, RiskState.ELEVATED, 0.6, volatility_state, market_regime)
        return RiskContext(timestamp, symbol, RiskState.LOW, 1.0, volatility_state, market_regime)

    # volatility_state is UNKNOWN from here on -- fall back to regime alone.
    if market_regime is MarketRegime.HIGH_VOLATILITY:
        return RiskContext(timestamp, symbol, RiskState.HIGH, 0.5, volatility_state, market_regime)
    if market_regime is MarketRegime.LOW_VOLATILITY:
        return RiskContext(timestamp, symbol, RiskState.LOW, 0.5, volatility_state, market_regime)

    return RiskContext(timestamp, symbol, RiskState.UNKNOWN, 0.0, volatility_state, market_regime)
