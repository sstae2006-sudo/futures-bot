"""The Market Context Engine -- foundation phase (2026-07-27), Session
Context, Volatility Context, and Market Regime Detection (all
2026-07-27) implemented.

Builds ``MarketContext`` snapshots from market data. Provides information
*to* strategies; never makes a trade decision and never holds a reference
to a broker, risk manager, or engine -- see ``models.MarketContext``'s
docstring and docs/ARCHITECTURE.md's "Market Context Engine" section for
the full rationale and the target layering:

    Market Data -> Context Engine -> Strategy Engine -> Risk Engine -> Execution

**Scope, deliberately:** ``_classify_session``, ``_classify_volatility``,
and ``_classify_regime`` are now real (see
``session.py``/``volatility.py``/``regime.py``). The other three
``_classify_*`` methods below (trend, liquidity, risk) are still stubs
returning ``UNKNOWN`` with no confidence recorded -- that's explicitly a
follow-up phase (see ROADMAP.md), not this one. This class exists so the
*shape* of that future work has an obvious, already-typed home instead of
being designed from scratch under time pressure later, and so nothing
calling it today needs to change when real classification logic lands
for the rest.

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
    TrendState,
    VolatilityState,
)
from .regime import RegimeContext, classify_regime
from .session import SessionContext, classify_session
from .volatility import VolatilityContext, analyze_volatility


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

        Session, volatility, and regime classification are real (see
        ``session.py``/``volatility.py``/``regime.py``); the other
        three below are still stubs, so this returns ``UNKNOWN`` for
        those with zero confidence. See the module docstring for why,
        and docs/ARCHITECTURE.md for what a real implementation should
        reuse instead of re-deriving.
        """
        bars = bars or ()
        session_ctx = self._classify_session(timestamp)
        volatility_ctx = self._classify_volatility(timestamp, bars)
        regime_ctx = self._classify_regime(timestamp, bars)

        confidence_scores = {"session": 1.0}
        if volatility_ctx.state is not VolatilityState.UNKNOWN:
            # Real confidence only once there's actually enough history
            # to have computed a ratio -- an UNKNOWN reading (missing
            # data) stays out of confidence_scores entirely, same
            # contract as the three still-stubbed dimensions below.
            confidence_scores["volatility"] = 1.0
        if regime_ctx.regime is not MarketRegime.UNKNOWN:
            confidence_scores["regime"] = regime_ctx.confidence

        return MarketContext(
            timestamp=timestamp,
            symbol=self.symbol,
            timeframe=self.timeframe,
            session=session_ctx.session,
            session_context=session_ctx,
            market_regime=regime_ctx.regime,
            regime_context=regime_ctx,
            volatility_state=volatility_ctx.state,
            volatility_context=volatility_ctx,
            trend_state=self._classify_trend(bars),
            liquidity_state=self._classify_liquidity(bars),
            risk_state=self._classify_risk(bars),
            confidence_scores=confidence_scores,
        )

    # --- Classification methods ---
    #
    # None of these may reach into risk/manager.py, brokers/, or engine.py
    # -- context describes, it never decides or acts.

    def _classify_session(self, timestamp: datetime) -> SessionContext:
        """Real, as of 2026-07-27 -- delegates entirely to
        ``session.classify_session``, which reuses ``contracts.py``'s
        existing CME market-calendar logic. See that module for the
        actual boundary/holiday/weekend handling; nothing is
        re-implemented here."""
        return classify_session(timestamp, self.symbol)

    def _classify_regime(self, timestamp: datetime, bars: Sequence[Bar]) -> RegimeContext:
        """Real, as of 2026-07-27 -- delegates entirely to
        ``regime.classify_regime``, which reuses ``strategy.indicators.adx``
        (trend strength), ``research.regime.classify_trend`` (trend
        direction), and this module's own ``volatility.analyze_volatility``
        (volatility signal). See that module for the exact priority order
        between trend/range/volatility labels."""
        return classify_regime(timestamp, self.symbol, self.timeframe, bars)

    def _classify_volatility(
        self, timestamp: datetime, bars: Sequence[Bar]
    ) -> VolatilityContext:
        """Real, as of 2026-07-27 -- delegates entirely to
        ``volatility.analyze_volatility``, which reuses
        ``strategy.indicators.atr_series`` and compares the latest ATR
        reading against a trailing historical average. See that module
        for why ``research.regime.classify_volatility``'s tercile
        approach isn't reused as-is (whole-series, not look-ahead-safe
        for real-time use)."""
        return analyze_volatility(timestamp, self.symbol, self.timeframe, bars)

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
