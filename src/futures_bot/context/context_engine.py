"""The Market Context Engine -- every dimension implemented as of Phase 8
(2026-07-27): Session, Volatility, Market Regime, Multi-Timeframe,
Structure, Trend, Liquidity, and Risk, plus the combined Context Scoring
System.

Builds ``MarketContext`` snapshots from market data. Provides information
*to* strategies; never makes a trade decision and never holds a reference
to a broker, risk manager, or engine -- see ``models.MarketContext``'s
docstring and docs/ARCHITECTURE.md's "Market Context Engine" section for
the full rationale and the target layering:

    Market Data -> Context Engine -> Strategy Engine -> Risk Engine -> Execution

**All eight ``_classify_*`` methods below are now real** (see
``session.py``/``volatility.py``/``regime.py``/``timeframe.py``/
``structure.py``/``trend.py``/``liquidity.py``/``risk.py``). Every
dimension is classification-only -- no buy/sell logic, no execution
logic, no broker interaction, no strategy decisions anywhere in this
package.

**Not wired into ``TradingEngine`` yet -- deliberately, per Phase 8's
own instructions.** Nothing in ``engine.py``, ``strategy/``, or
``risk/`` imports this module. Building it standalone first, with its
own tests, keeps every phase purely additive -- the existing trading
system cannot be affected by code nothing calls. See
docs/ARCHITECTURE.md's "Market Context Engine" section for the
integration point a future, explicitly-approved phase would use.
"""

from __future__ import annotations

from datetime import datetime
from typing import Mapping, Optional, Sequence

from ..models import Bar
from .liquidity import LiquidityContext, analyze_liquidity
from .models import (
    LiquidityState,
    MarketContext,
    MarketRegime,
    RiskState,
    TrendState,
    VolatilityState,
)
from .regime import RegimeContext, classify_regime
from .risk import RiskContext, assess_risk
from .scoring import DEFAULT_SCORING_CONFIG, ScoringConfig, with_environment_score
from .session import SessionContext, classify_session
from .structure import StructureContext, analyze_structure
from .timeframe import TimeframeAlignment, classify_timeframe_alignment
from .trend import TrendContext, analyze_trend
from .volatility import VolatilityContext, analyze_volatility


class ContextEngine:
    """Builds a ``MarketContext`` for one symbol/timeframe.

    ``symbol``/``timeframe`` are fixed at construction (one engine per
    series being watched), matching how ``Strategy`` is bound to one
    ``ContractSpec`` for its lifetime rather than being handed a symbol
    per call.

    ``scoring_config`` (optional) lets a caller experiment with a
    different ``scoring.ScoringConfig`` weighting without touching any
    code -- defaults to ``scoring.DEFAULT_SCORING_CONFIG``, the exact
    weighting this package has used since Phase 7.
    """

    def __init__(
        self,
        symbol: str,
        timeframe: str,
        scoring_config: Optional[ScoringConfig] = None,
    ) -> None:
        self.symbol = symbol
        self.timeframe = timeframe
        self.scoring_config = scoring_config or DEFAULT_SCORING_CONFIG

    def build_context(
        self,
        timestamp: datetime,
        bars: Optional[Sequence[Bar]] = None,
        bars_by_timeframe: Optional[Mapping[str, Sequence[Bar]]] = None,
    ) -> MarketContext:
        """Returns a ``MarketContext`` as of ``timestamp``.

        ``bars`` is history up to and including the bar that just closed
        (the same convention as ``Strategy.on_bar``'s own ``bars``
        argument -- nothing beyond it) -- optional because a caller may
        want a context before any history exists yet (session start), in
        which case every classification is ``UNKNOWN`` regardless.

        ``bars_by_timeframe`` is a separate, optional mapping of
        ``timeframe.TIMEFRAME_ORDER`` labels ("1m"/"5m"/"15m"/"1h"/"1d")
        to that timeframe's own bar history, for multi-timeframe trend
        alignment -- entirely independent of ``bars``/``self.timeframe``;
        a caller wanting its own timeframe counted toward the alignment
        score includes it under the matching key itself. Omitted or
        partial data degrades gracefully (see ``timeframe.py``).

        Session, volatility, regime, timeframe-alignment, structure,
        trend, liquidity, and risk classification are all real (see
        ``session.py``/``volatility.py``/``regime.py``/``timeframe.py``/
        ``structure.py``/``trend.py``/``liquidity.py``/``risk.py``).
        Missing/insufficient data still returns ``UNKNOWN`` with zero
        confidence for whichever dimension couldn't be classified --
        that contract is unchanged from every earlier phase.

        The returned context's ``environment_score`` (see
        ``scoring.py``) is always populated, combining whatever
        dimensions above actually had data -- information only, never
        consulted for a trade decision.
        """
        bars = bars or ()
        session_ctx = self._classify_session(timestamp)
        volatility_ctx = self._classify_volatility(timestamp, bars)
        regime_ctx = self._classify_regime(timestamp, bars)
        timeframe_ctx = self._classify_timeframe_alignment(timestamp, bars_by_timeframe)
        structure_ctx = self._classify_structure(timestamp, bars)
        trend_ctx = self._classify_trend(timestamp, bars)
        liquidity_ctx = self._classify_liquidity(timestamp, bars)
        risk_ctx = self._classify_risk(timestamp, volatility_ctx.state, regime_ctx.regime)

        confidence_scores = {"session": 1.0}
        if volatility_ctx.state is not VolatilityState.UNKNOWN:
            # Real confidence only once there's actually enough history
            # to have computed a ratio -- an UNKNOWN reading (missing
            # data) stays out of confidence_scores entirely.
            confidence_scores["volatility"] = 1.0
        if regime_ctx.regime is not MarketRegime.UNKNOWN:
            confidence_scores["regime"] = regime_ctx.confidence
        if timeframe_ctx.alignment:
            confidence_scores["timeframe_alignment"] = timeframe_ctx.alignment_score
        if structure_ctx.trend is not TrendState.UNKNOWN:
            confidence_scores["structure"] = structure_ctx.structure_confidence
        if trend_ctx.trend is not TrendState.UNKNOWN:
            confidence_scores["trend"] = trend_ctx.confidence
        if liquidity_ctx.state is not LiquidityState.UNKNOWN:
            confidence_scores["liquidity"] = liquidity_ctx.confidence
        if risk_ctx.state is not RiskState.UNKNOWN:
            confidence_scores["risk"] = risk_ctx.confidence

        context = MarketContext(
            timestamp=timestamp,
            symbol=self.symbol,
            timeframe=self.timeframe,
            session=session_ctx.session,
            session_context=session_ctx,
            market_regime=regime_ctx.regime,
            regime_context=regime_ctx,
            volatility_state=volatility_ctx.state,
            volatility_context=volatility_ctx,
            timeframe_alignment=timeframe_ctx,
            structure_context=structure_ctx,
            trend_state=trend_ctx.trend,
            trend_context=trend_ctx,
            liquidity_state=liquidity_ctx.state,
            liquidity_context=liquidity_ctx,
            risk_state=risk_ctx.state,
            risk_context=risk_ctx,
            confidence_scores=confidence_scores,
        )
        # Computed last, and necessarily as a second step: the
        # environment score reads every field already set above, so it
        # cannot be filled in during the same MarketContext(...) call
        # that produces those fields (see scoring.with_environment_score).
        return with_environment_score(context, self.scoring_config)

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

    def _classify_timeframe_alignment(
        self,
        timestamp: datetime,
        bars_by_timeframe: Optional[Mapping[str, Sequence[Bar]]],
    ) -> TimeframeAlignment:
        """Real, as of 2026-07-27 -- delegates entirely to
        ``timeframe.classify_timeframe_alignment``, which reuses
        ``research.regime.classify_trend`` per timeframe. See that
        module for the completed-candle filtering that keeps this
        look-ahead-safe across independent bar streams."""
        return classify_timeframe_alignment(timestamp, self.symbol, bars_by_timeframe)

    def _classify_structure(self, timestamp: datetime, bars: Sequence[Bar]) -> StructureContext:
        """Real, as of 2026-07-27 -- delegates entirely to
        ``structure.analyze_structure``, which detects confirmed swing
        highs/lows to judge trend structure and the nearest support/
        resistance levels. Strictly descriptive -- see that module's
        docstring for why swing-point confirmation lag is not a
        look-ahead violation."""
        return analyze_structure(timestamp, self.symbol, bars)

    def _classify_trend(self, timestamp: datetime, bars: Sequence[Bar]) -> TrendContext:
        """Real, as of 2026-07-27 (Phase 8) -- delegates entirely to
        ``trend.analyze_trend``, which reuses
        ``research.regime.classify_trend`` for direction and
        ``strategy.indicators.adx`` (plus ``regime.py``'s own confidence
        constants) for confidence. Independent of ``regime_context``'s
        volatility-coupled composite -- see that module's docstring for
        why a separate, simpler direction-only reading is kept."""
        return analyze_trend(timestamp, self.symbol, bars)

    def _classify_liquidity(self, timestamp: datetime, bars: Sequence[Bar]) -> LiquidityContext:
        """Real, as of 2026-07-27 (Phase 8) -- delegates entirely to
        ``liquidity.analyze_liquidity``, which reuses
        ``strategy.indicators.sma`` for the trailing volume average.
        No existing general-purpose liquidity classifier to reuse
        (confirmed by search) -- see that module's docstring for what's
        genuinely new here and why."""
        return analyze_liquidity(timestamp, self.symbol, self.timeframe, bars)

    def _classify_risk(
        self, timestamp: datetime, volatility_state: VolatilityState, market_regime: MarketRegime,
    ) -> RiskContext:
        """Real, as of 2026-07-27 (Phase 8) -- delegates entirely to
        ``risk.assess_risk``, a composite of the already-classified
        ``volatility_state``/``market_regime`` (no new market-data
        analysis of its own) -- exactly what this method's own Phase-1
        stub docstring anticipated. See that module for the exact
        decision order."""
        return assess_risk(timestamp, self.symbol, volatility_state, market_regime)
