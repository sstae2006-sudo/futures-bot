"""Tests for context/scoring.py -- the Context Scoring System (Market
Context Engine Phase 7; see ROADMAP.md's "Market Context Engine
(phased)" and docs/ARCHITECTURE.md's "Market Context Engine" section).

Named test_context_scoring.py, matching every other context/*.py test
module's naming convention (no collision exists for this name).

Every worked example from the task's own spec was verified manually
against the live module before being written down as an assertion (see
test_context_session.py's docstring for why this discipline matters).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from futures_bot.context import (
    DEFAULT_SCORING_CONFIG,
    ContextEngine,
    EnvironmentScore,
    MarketContext,
    ScoringConfig,
    score_environment,
    with_environment_score,
)
from futures_bot.context.models import (
    LiquidityState,
    MarketRegime,
    RiskState,
    SessionPhase,
    TrendState,
    VolatilityState,
)
from futures_bot.context.regime import RegimeContext
from futures_bot.context.session import SessionContext
from futures_bot.context.structure import StructureContext
from futures_bot.context.timeframe import TimeframeAlignment
from futures_bot.models import Bar

CT = ZoneInfo("America/Chicago")
TS = datetime(2026, 1, 6, 8, 42, tzinfo=CT)


def _zigzag(n_cycles: int, cycle_low_start: Decimal, cycle_low_drift: Decimal, start: datetime, volume: int = 300) -> list[Bar]:
    """Rise-then-fall bars with a per-cycle drift -- gives both
    context/structure.py (confirmed swing highs/lows) and
    research.regime.classify_trend (net directional move) a real signal
    simultaneously, unlike a straight line (no swings) or a small-
    amplitude zigzag (net move too small to register as a trend)."""
    prices: list[Decimal] = []
    for c in range(n_cycles):
        cycle_low = cycle_low_start + cycle_low_drift * c
        peak = cycle_low + 60
        for k in range(1, 5):
            prices.append(cycle_low + (peak - cycle_low) * k // 4)
        trough = peak - 20
        for k in range(1, 5):
            prices.append(peak - (peak - trough) * k // 4)
    return [
        Bar(timestamp=start + timedelta(minutes=i), open=p, high=p + 4, low=p - 4, close=p, volume=volume)
        for i, p in enumerate(prices)
    ]


def _full_context(
    *,
    market_regime=MarketRegime.TRENDING_UP,
    regime_confidence=1.0,
    volatility_state=VolatilityState.NORMAL,
    liquidity_expectation="HIGH",
    structure_trend=TrendState.BULLISH,
    structure_confidence=1.0,
    liquidity_state=LiquidityState.DEEP,
    risk_state=RiskState.HIGH,
) -> MarketContext:
    """A MarketContext with every dimension hand-set -- used to hit
    exact, predictable scores rather than depending on other modules'
    own classification math."""
    session_ctx = SessionContext(
        timestamp=TS, symbol="MES", session=SessionPhase.OPENING_RANGE,
        minutes_since_open=12, liquidity_expectation=liquidity_expectation, is_market_open=True,
    )
    regime_ctx = RegimeContext(
        timestamp=TS, symbol="MES", timeframe="5min", regime=market_regime,
        confidence=regime_confidence, adx=40.0, trend_direction="bullish", volatility_ratio=1.0,
    )
    structure_ctx = StructureContext(
        timestamp=TS, symbol="MES", trend=structure_trend,
        support=Decimal("5900"), resistance=Decimal("5950"),
        distance_to_support=Decimal("10"), distance_to_resistance=Decimal("30"),
        structure_confidence=structure_confidence,
    )
    return MarketContext(
        timestamp=TS, symbol="MES", timeframe="5min",
        session=SessionPhase.OPENING_RANGE, session_context=session_ctx,
        market_regime=market_regime, regime_context=regime_ctx,
        volatility_state=volatility_state,
        structure_context=structure_ctx,
        liquidity_state=liquidity_state,
        risk_state=risk_state,
    )


class TestWorkedExample:
    """The task's own spec example: Trend +20, Volatility +15, Session
    +10, Structure +20, Liquidity +15, Risk -10 -> Environment Score
    70/100."""

    def test_matches_the_task_spec_example_exactly(self):
        ctx = _full_context()
        result = score_environment(ctx)
        assert result.score == 70
        assert result.breakdown == {
            "trend": 20.0, "volatility": 15.0, "session": 10.0,
            "structure": 20.0, "liquidity": 15.0, "risk": -10.0,
        }

    def test_score_is_always_an_int_in_the_0_to_100_range(self):
        ctx = _full_context()
        result = score_environment(ctx)
        assert isinstance(result.score, int)
        assert 0 <= result.score <= 100


class TestExplanationOutput:
    """The task's second spec example: { score: 72, reasons: ["Strong
    trend alignment", "Normal volatility", "Good liquidity"] } -- only
    the reason phrases are exactly specified; the score itself is
    illustrative (this scenario's actual arithmetic doesn't reproduce
    72 exactly, same as other illustrative examples throughout this
    project), so only the reasons are asserted precisely."""

    def test_reason_phrasing_matches_the_task_spec_example_exactly(self):
        regime_ctx = RegimeContext(
            timestamp=TS, symbol="MES", timeframe="5min", regime=MarketRegime.TRENDING_UP,
            confidence=1.0, adx=40.0, trend_direction="bullish", volatility_ratio=1.0,
        )
        ctx = MarketContext(
            timestamp=TS, symbol="MES", timeframe="5min",
            market_regime=MarketRegime.TRENDING_UP, regime_context=regime_ctx,
            volatility_state=VolatilityState.NORMAL,
            liquidity_state=LiquidityState.DEEP,
        )
        result = score_environment(ctx)
        assert result.reasons == ("Strong trend alignment", "Normal volatility", "Good liquidity")

    def test_reasons_omit_dimensions_with_no_data(self):
        ctx = MarketContext(timestamp=TS, symbol="MES", timeframe="5min")
        result = score_environment(ctx)
        assert result.reasons == ()

    def test_reasons_include_unfavorable_readings_too_not_just_positives(self):
        ctx = _full_context(volatility_state=VolatilityState.EXTREME, risk_state=RiskState.HIGH)
        result = score_environment(ctx)
        assert "Extreme volatility" in result.reasons
        assert "High risk conditions" in result.reasons


class TestConfidenceAggregation:
    def test_all_six_dimensions_known_is_full_confidence(self):
        ctx = _full_context()
        result = score_environment(ctx)
        assert result.confidence == pytest.approx(1.0)

    def test_no_dimensions_known_is_zero_confidence(self):
        ctx = MarketContext(timestamp=TS, symbol="MES", timeframe="5min")
        result = score_environment(ctx)
        assert result.confidence == 0.0
        assert result.score == 0

    def test_partial_data_gives_a_fractional_confidence(self):
        ctx = MarketContext(
            timestamp=TS, symbol="MES", timeframe="5min",
            volatility_state=VolatilityState.NORMAL,
            liquidity_state=LiquidityState.DEEP,
            risk_state=RiskState.LOW,
        )
        result = score_environment(ctx)
        assert result.confidence == pytest.approx(3 / 6)

    def test_confidence_is_independent_of_whether_the_score_is_high_or_low(self):
        # Full data, but every reading is the worst possible -- low
        # score, yet still full confidence (we know a lot, it's just
        # bad). RANGING regime alone carries no trend-direction signal
        # (see _score_trend), so a weak-but-present timeframe_alignment
        # is added here specifically to keep the trend dimension
        # "known" -- otherwise this scenario would only be 5/6 known,
        # which is a correct but different thing to assert.
        ctx = _full_context(
            volatility_state=VolatilityState.EXTREME,
            liquidity_expectation="NONE",
            structure_trend=TrendState.NEUTRAL,
            structure_confidence=0.0,
            liquidity_state=LiquidityState.THIN,
            risk_state=RiskState.HIGH,
            market_regime=MarketRegime.RANGING,
            regime_confidence=0.0,
        )
        from dataclasses import replace

        ctx = replace(ctx, timeframe_alignment=TimeframeAlignment(
            timestamp=TS, symbol="MES", alignment={"1h": TrendState.NEUTRAL}, alignment_score=0.0,
        ))
        result = score_environment(ctx)
        assert result.confidence == pytest.approx(1.0)
        assert result.score < 50


class TestClamping:
    def test_score_never_exceeds_100_even_if_weights_summed_higher(self):
        # All positive dimensions maxed, no risk penalty at all.
        ctx = _full_context(risk_state=RiskState.LOW)
        result = score_environment(ctx)
        assert result.score <= 100

    def test_score_never_goes_below_zero(self):
        ctx = _full_context(
            volatility_state=VolatilityState.EXTREME,
            liquidity_expectation="NONE",
            structure_trend=TrendState.UNKNOWN,
            liquidity_state=LiquidityState.THIN,
            risk_state=RiskState.HIGH,
            market_regime=MarketRegime.RANGING,
            regime_confidence=0.0,
        )
        result = score_environment(ctx)
        assert result.score >= 0


class TestMissingDataHandledSafely:
    """Liquidity and risk are still UNKNOWN stubs everywhere else in
    this codebase as of this phase -- a real, already-exercised
    "missing data" case, not just a hypothetical one."""

    def test_default_market_context_has_liquidity_and_risk_unknown(self):
        ctx = MarketContext(timestamp=TS, symbol="MES", timeframe="5min")
        assert ctx.liquidity_state is LiquidityState.UNKNOWN
        assert ctx.risk_state is RiskState.UNKNOWN

    def test_unknown_liquidity_and_risk_contribute_zero_not_a_guess(self):
        ctx = _full_context(liquidity_state=LiquidityState.UNKNOWN, risk_state=RiskState.UNKNOWN)
        result = score_environment(ctx)
        assert result.breakdown["liquidity"] == 0.0
        assert result.breakdown["risk"] == 0.0
        assert "Good liquidity" not in result.reasons
        assert result.confidence == pytest.approx(4 / 6)

    def test_context_engine_with_no_bars_scores_only_from_session(self):
        # With zero bars, every bar-dependent dimension (volatility,
        # regime, trend, liquidity, risk, structure) is genuinely
        # UNKNOWN -- only session (timestamp-only) contributes. This is
        # "missing data", not a stub: as of Phase 8, every dimension is
        # real and would contribute given enough bars (see the test
        # below).
        engine = ContextEngine(symbol="MES", timeframe="1min")
        ctx = engine.build_context(timestamp=TS)
        assert ctx.environment_score is not None
        assert ctx.environment_score.score == 10  # session's full weight, nothing else known
        assert ctx.environment_score.confidence == pytest.approx(1 / 6)

    def test_context_engine_with_full_data_exceeds_the_old_stub_era_ceiling(self):
        # Before Phase 8, liquidity_state/risk_state were permanent
        # UNKNOWN stubs, capping any real ContextEngine score at 65
        # (trend 20 + volatility 15 + session 10 + structure 20). Now
        # that both are real, a favorable, data-rich context can exceed
        # that -- proving the two new dimensions genuinely contribute,
        # not just that they exist.
        start = datetime(2026, 1, 6, 8, 0, tzinfo=CT)
        bars = _zigzag(10, Decimal("5900"), Decimal("40"), start, volume=350)
        engine = ContextEngine(symbol="MES", timeframe="1min")
        ctx = engine.build_context(timestamp=bars[-1].timestamp, bars=bars)
        assert ctx.environment_score.score > 65
        assert ctx.trend_state is not TrendState.UNKNOWN
        assert ctx.liquidity_state is not LiquidityState.UNKNOWN
        assert ctx.risk_state is not RiskState.UNKNOWN


class TestIntegratedIntoMarketContext:
    def test_context_engine_always_populates_environment_score(self):
        engine = ContextEngine(symbol="MES", timeframe="1min")
        ctx = engine.build_context(timestamp=TS)
        assert ctx.environment_score is not None
        assert isinstance(ctx.environment_score.score, int)

    def test_market_context_to_dict_includes_nested_environment_score(self):
        engine = ContextEngine(symbol="MES", timeframe="1min")
        ctx = engine.build_context(timestamp=TS)
        d = ctx.to_dict()
        assert d["environment_score"]["score"] == ctx.environment_score.score
        assert d["environment_score"]["reasons"] == list(ctx.environment_score.reasons)

    def test_market_context_from_dict_restores_environment_score(self):
        engine = ContextEngine(symbol="MES", timeframe="1min")
        original = engine.build_context(timestamp=TS)
        restored = MarketContext.from_dict(original.to_dict())
        assert restored == original


class TestEnvironmentScoreSerialization:
    def test_to_dict_round_trips_through_from_dict(self):
        ctx = _full_context()
        original = score_environment(ctx)
        restored = EnvironmentScore.from_dict(original.to_dict())
        assert restored == original


class TestInformationOnlyNeverDecidesTrades:
    def test_environment_score_carries_no_broker_risk_or_engine_reference(self):
        import inspect

        import futures_bot.context.scoring as scoring_module

        source = inspect.getsource(scoring_module)
        import_lines = [
            line.strip() for line in source.splitlines()
            if line.strip().startswith(("import ", "from "))
        ]
        for line in import_lines:
            assert "risk.manager" not in line, line
            assert "brokers" not in line, line
            assert "engine" not in line, line

    def test_strategy_and_trading_engine_do_not_import_scoring(self):
        import inspect

        import futures_bot.engine as engine_module
        import futures_bot.strategy.base as strategy_base

        assert "scoring" not in engine_module.__dict__
        assert "scoring" not in inspect.getsource(strategy_base).lower()


class TestConfigurableScoring:
    """Phase 8, Part 2: weighting must be configurable, not hardcoded,
    and the default configuration must reproduce every pre-Phase-8
    scoring behavior exactly."""

    def test_default_config_matches_the_historical_hardcoded_constants(self):
        # Before this phase, these six weights (20/15/10/20/15/10) were
        # module-level constants baked directly into the scoring
        # formulas -- this is the exact validation the task asked for:
        # the default ScoringConfig reproduces that behavior exactly.
        assert DEFAULT_SCORING_CONFIG == ScoringConfig(
            trend_weight=20.0, volatility_weight=15.0, session_weight=10.0,
            structure_weight=20.0, liquidity_weight=15.0, risk_weight=10.0,
        )

    def test_score_environment_with_no_config_argument_uses_the_default(self):
        ctx = _full_context()
        assert score_environment(ctx) == score_environment(ctx, DEFAULT_SCORING_CONFIG)

    def test_default_config_still_reproduces_the_worked_example_exactly(self):
        # Byte-for-byte re-check of Phase 7's own worked example, now
        # routed through the configurable path -- proves the refactor
        # changed nothing about default behavior.
        ctx = _full_context()
        result = score_environment(ctx)
        assert result.score == 70
        assert result.breakdown == {
            "trend": 20.0, "volatility": 15.0, "session": 10.0,
            "structure": 20.0, "liquidity": 15.0, "risk": -10.0,
        }

    def test_a_custom_config_changes_the_score(self):
        ctx = _full_context()
        default_result = score_environment(ctx)
        custom_result = score_environment(ctx, ScoringConfig(trend_weight=40.0))
        assert custom_result.breakdown["trend"] == pytest.approx(40.0)
        assert custom_result.score != default_result.score
        # Every other dimension's weight is untouched.
        assert custom_result.breakdown["volatility"] == default_result.breakdown["volatility"]

    def test_a_zero_weight_config_produces_a_zero_score(self):
        ctx = _full_context()
        zeroed = ScoringConfig(
            trend_weight=0.0, volatility_weight=0.0, session_weight=0.0,
            structure_weight=0.0, liquidity_weight=0.0, risk_weight=0.0,
        )
        result = score_environment(ctx, zeroed)
        assert result.score == 0
        assert all(v == 0.0 for v in result.breakdown.values())
        # Confidence is about data availability, not about the weights --
        # still full confidence even though every weight is zero.
        assert result.confidence == pytest.approx(1.0)

    def test_with_environment_score_accepts_a_custom_config(self):
        ctx = _full_context()
        custom = ScoringConfig(structure_weight=50.0)
        result = with_environment_score(ctx, custom)
        assert result.environment_score.breakdown["structure"] == pytest.approx(50.0)

    def test_context_engine_accepts_a_custom_scoring_config(self):
        bars = _zigzag(10, Decimal("5900"), Decimal("40"), datetime(2026, 1, 6, 8, 0, tzinfo=CT), volume=350)
        default_engine = ContextEngine(symbol="MES", timeframe="1min")
        custom_engine = ContextEngine(
            symbol="MES", timeframe="1min",
            scoring_config=ScoringConfig(structure_weight=0.0),
        )
        default_ctx = default_engine.build_context(timestamp=bars[-1].timestamp, bars=bars)
        custom_ctx = custom_engine.build_context(timestamp=bars[-1].timestamp, bars=bars)
        assert custom_ctx.environment_score.breakdown["structure"] == 0.0
        assert default_ctx.environment_score.score != custom_ctx.environment_score.score

    def test_context_engine_default_scoring_config_is_the_package_default(self):
        engine = ContextEngine(symbol="MES", timeframe="1min")
        assert engine.scoring_config == DEFAULT_SCORING_CONFIG

    def test_risk_weight_remains_a_pure_penalty_under_a_custom_config(self):
        # Risk's contribution range should stay [-risk_weight, 0]
        # regardless of the configured magnitude -- never positive.
        ctx = _full_context(risk_state=RiskState.LOW)
        result = score_environment(ctx, ScoringConfig(risk_weight=99.0))
        assert result.breakdown["risk"] == 0.0
