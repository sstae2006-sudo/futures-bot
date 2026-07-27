"""The Market Context Engine -- foundation phase (2026-07-27).

Builds ``MarketContext`` snapshots from market data. Provides information
*to* strategies; never makes a trade decision and never holds a reference
to a broker, risk manager, or engine -- see ``models.MarketContext``'s
docstring and docs/ARCHITECTURE.md's "Market Context Engine" section for
the full rationale and the target layering:

    Market Data -> Context Engine -> Strategy Engine -> Risk Engine -> Execution

**Foundation phase scope, deliberately:** every ``_classify_*`` method
below is a stub that returns ``UNKNOWN`` with no confidence recorded. No
indicator math, no regime/volatility/trend detection is implemented here
yet -- that's explicitly a follow-up phase (see ROADMAP.md), and per this
phase's own instructions, not this one. This class exists so the *shape*
of that future work has an obvious, already-typed home instead of being
designed from scratch under time pressure later, and so nothing calling
it today needs to change when real classification logic lands.

**Not wired into ``TradingEngine`` yet.** Nothing in ``engine.py``,
``strategy/``, or ``risk/`` imports this module. Building it standalone
first, with its own tests, keeps this phase purely additive -- the
existing trading system cannot be affected by code nothing calls.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Sequence

from ..models import Bar
from .models import (
    LiquidityState,
    MarketContext,
    MarketRegime,
    RiskState,
    SessionPhase,
    TrendState,
    VolatilityState,
)


class ContextEngine:
    """Builds a ``MarketContext`` for one symbol/timeframe.

    ``symbol``/``timeframe`` are fixed at construction (one engine per
    series being watched), matching how ``Strategy`` is bound to one
    ``ContractSpec`` for its lifetime rather than being handed a symbol
    per call.
    """

    def __init__(self, symbol: str, timeframe: str) -> None:
        self.symbol = symbol
        self.timeframe = timeframe

    def build_context(
        self,
        timestamp: datetime,
        bars: Optional[Sequence[Bar]] = None,
    ) -> MarketContext:
        """Returns a ``MarketContext`` as of ``timestamp``.

        ``bars`` is history up to and including the bar that just closed
        (the same convention as ``Strategy.on_bar``'s own ``bars``
        argument -- nothing beyond it) -- optional because a caller may
        want a context before any history exists yet (session start), in
        which case every classification is ``UNKNOWN`` regardless.

        Foundation phase: every classification below is a stub, so this
        always returns all-``UNKNOWN`` with zero confidence. See the
        module docstring for why, and docs/ARCHITECTURE.md for what a
        real implementation should reuse instead of re-deriving.
        """
        bars = bars or ()
        return MarketContext(
            timestamp=timestamp,
            symbol=self.symbol,
            timeframe=self.timeframe,
            session=self._classify_session(timestamp),
            market_regime=self._classify_regime(bars),
            volatility_state=self._classify_volatility(bars),
            trend_state=self._classify_trend(bars),
            liquidity_state=self._classify_liquidity(bars),
            risk_state=self._classify_risk(bars),
            confidence_scores={},
        )

    # --- Classification stubs -- foundation phase, no logic yet ---
    #
    # Each of these is the extension point for a specific future phase.
    # None of them may reach into risk/manager.py, brokers/, or engine.py
    # when implemented -- context describes, it never decides or acts.

    def _classify_session(self, timestamp: datetime) -> SessionPhase:
        """Future phase: derive from ``contracts.py``'s own session
        arithmetic (``session_date``, ``is_market_open``, ``to_ct``) or
        ``research.regime.classify_session``'s RTH buckets -- reuse one
        of those, don't redefine session boundaries a third time."""
        return SessionPhase.UNKNOWN

    def _classify_regime(self, bars: Sequence[Bar]) -> MarketRegime:
        """Future phase: ``strategy.indicators.adx`` is the natural
        trending-vs-ranging signal already available in this codebase."""
        return MarketRegime.UNKNOWN

    def _classify_volatility(self, bars: Sequence[Bar]) -> VolatilityState:
        """Future phase: ``research.regime.classify_volatility`` already
        does ATR-tercile bucketing for this exact purpose (currently
        applied post-trade; the math doesn't change if it's called
        pre-decision instead)."""
        return VolatilityState.UNKNOWN

    def _classify_trend(self, bars: Sequence[Bar]) -> TrendState:
        """Future phase: ``research.regime.classify_trend`` (start-to-end
        % move over a lookback) or ``strategy.indicators.ema_series``'s
        slope -- reuse rather than re-deriving a second trend definition."""
        return TrendState.UNKNOWN

    def _classify_liquidity(self, bars: Sequence[Bar]) -> LiquidityState:
        """Future phase: no existing equivalent to reuse -- this is
        genuinely new (e.g. volume/spread-based bucketing)."""
        return LiquidityState.UNKNOWN

    def _classify_risk(self, bars: Sequence[Bar]) -> RiskState:
        """Future phase: no existing equivalent to reuse -- likely a
        composite of the other classifications above (e.g. VOLATILE
        regime + HIGH volatility => ELEVATED/HIGH risk_state), decided
        once real thresholds exist for those inputs."""
        return RiskState.UNKNOWN
