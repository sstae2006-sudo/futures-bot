"""Tests for context/risk.py -- Risk State (Market Context Engine
Phase 8; see ROADMAP.md's "Market Context Engine (phased)" and
docs/ARCHITECTURE.md's "Market Context Engine" section).

Named test_context_risk.py -- test_risk.py already exists for
futures_bot.risk.manager.RiskManager (an unrelated account/session risk
gate, naming-collision-only per RiskState's own docstring).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from futures_bot.context import ContextEngine, RiskContext, assess_risk
from futures_bot.context.models import MarketRegime, RiskState, VolatilityState
from futures_bot.models import Bar

CT = ZoneInfo("America/Chicago")
START = datetime(2026, 1, 6, 17, 0, tzinfo=CT)


class TestCompositeClassification:
    def test_extreme_volatility_is_high_risk(self):
        ctx = assess_risk(START, "MES", VolatilityState.EXTREME, MarketRegime.RANGING)
        assert ctx.state is RiskState.HIGH
        assert ctx.confidence == pytest.approx(1.0)

    def test_high_volatility_is_elevated_risk(self):
        ctx = assess_risk(START, "MES", VolatilityState.HIGH, MarketRegime.RANGING)
        assert ctx.state is RiskState.ELEVATED

    def test_low_volatility_is_low_risk(self):
        ctx = assess_risk(START, "MES", VolatilityState.LOW, MarketRegime.RANGING)
        assert ctx.state is RiskState.LOW

    def test_normal_volatility_is_low_risk(self):
        ctx = assess_risk(START, "MES", VolatilityState.NORMAL, MarketRegime.TRENDING_UP)
        assert ctx.state is RiskState.LOW

    def test_regime_high_volatility_corroborates_even_with_calm_atr(self):
        # Volatility alone reads calm, but the regime independently
        # flagged HIGH_VOLATILITY -- risk should still bump to ELEVATED,
        # not be silently overridden by the calmer ATR reading.
        ctx = assess_risk(START, "MES", VolatilityState.LOW, MarketRegime.HIGH_VOLATILITY)
        assert ctx.state is RiskState.ELEVATED
        assert ctx.confidence < 1.0  # a weaker, secondary corroboration

    def test_regime_alone_is_a_fallback_when_volatility_is_unknown(self):
        high = assess_risk(START, "MES", VolatilityState.UNKNOWN, MarketRegime.HIGH_VOLATILITY)
        assert high.state is RiskState.HIGH
        low = assess_risk(START, "MES", VolatilityState.UNKNOWN, MarketRegime.LOW_VOLATILITY)
        assert low.state is RiskState.LOW

    def test_neither_input_known_is_unknown(self):
        ctx = assess_risk(START, "MES", VolatilityState.UNKNOWN, MarketRegime.UNKNOWN)
        assert ctx.state is RiskState.UNKNOWN
        assert ctx.confidence == 0.0

    def test_regime_ranging_with_unknown_volatility_is_unknown(self):
        # RANGING carries no volatility implication of its own.
        ctx = assess_risk(START, "MES", VolatilityState.UNKNOWN, MarketRegime.RANGING)
        assert ctx.state is RiskState.UNKNOWN


class TestConfidenceCalculationWorks:
    def test_confidence_is_always_in_valid_range(self):
        for vol in VolatilityState:
            for regime in MarketRegime:
                ctx = assess_risk(START, "MES", vol, regime)
                assert 0.0 <= ctx.confidence <= 1.0

    def test_direct_volatility_signal_has_higher_confidence_than_regime_fallback(self):
        direct = assess_risk(START, "MES", VolatilityState.EXTREME, MarketRegime.UNKNOWN)
        fallback = assess_risk(START, "MES", VolatilityState.UNKNOWN, MarketRegime.HIGH_VOLATILITY)
        assert direct.state is fallback.state is RiskState.HIGH
        assert direct.confidence > fallback.confidence


class TestInputsCarriedThroughForTransparency:
    def test_volatility_state_and_market_regime_are_preserved_on_the_result(self):
        ctx = assess_risk(START, "MES", VolatilityState.HIGH, MarketRegime.TRENDING_DOWN)
        assert ctx.volatility_state is VolatilityState.HIGH
        assert ctx.market_regime is MarketRegime.TRENDING_DOWN


class TestRiskContextSerialization:
    def test_to_dict_round_trips_through_from_dict(self):
        original = assess_risk(START, "MES", VolatilityState.EXTREME, MarketRegime.HIGH_VOLATILITY)
        restored = RiskContext.from_dict(original.to_dict())
        assert restored == original


class TestIntegratedIntoMarketContext:
    def test_context_engine_wires_risk_context_through(self):
        # Constant, tight-range bars -> LOW/NORMAL volatility -> LOW risk.
        bars = []
        price = Decimal("5000")
        for i in range(30):
            ts = START + timedelta(minutes=i)
            bars.append(Bar(timestamp=ts, open=price, high=price + 2, low=price - 2, close=price, volume=100))
        engine = ContextEngine(symbol="MES", timeframe="1min")
        ctx = engine.build_context(timestamp=bars[-1].timestamp, bars=bars)
        assert ctx.risk_context is not None
        assert ctx.risk_state is ctx.risk_context.state
        assert "risk" in ctx.confidence_scores

    def test_context_engine_handles_no_bars_gracefully(self):
        engine = ContextEngine(symbol="MES", timeframe="1min")
        ctx = engine.build_context(timestamp=START)
        assert ctx.risk_context is not None
        assert ctx.risk_state is RiskState.UNKNOWN
        assert "risk" not in ctx.confidence_scores

    def test_market_context_to_dict_includes_nested_risk_context(self):
        bars = []
        price = Decimal("5000")
        for i in range(30):
            ts = START + timedelta(minutes=i)
            bars.append(Bar(timestamp=ts, open=price, high=price + 2, low=price - 2, close=price, volume=100))
        engine = ContextEngine(symbol="MES", timeframe="1min")
        ctx = engine.build_context(timestamp=bars[-1].timestamp, bars=bars)
        d = ctx.to_dict()
        assert d["risk_context"]["state"] == ctx.risk_state.value

    def test_market_context_from_dict_restores_risk_context(self):
        from futures_bot.context import MarketContext

        bars = []
        price = Decimal("5000")
        for i in range(30):
            ts = START + timedelta(minutes=i)
            bars.append(Bar(timestamp=ts, open=price, high=price + 2, low=price - 2, close=price, volume=100))
        engine = ContextEngine(symbol="MES", timeframe="1min")
        original = engine.build_context(timestamp=bars[-1].timestamp, bars=bars)
        restored = MarketContext.from_dict(original.to_dict())
        assert restored == original


class TestNoRiskManagerRelationship:
    """RiskState is unrelated to and never consulted by
    risk.manager.RiskManager -- naming collision only."""

    def test_risk_module_carries_no_broker_engine_or_risk_manager_reference(self):
        import inspect

        import futures_bot.context.risk as risk_module

        source = inspect.getsource(risk_module)
        import_lines = [
            line.strip() for line in source.splitlines()
            if line.strip().startswith(("import ", "from "))
        ]
        for line in import_lines:
            assert "risk.manager" not in line, line
            assert "brokers" not in line, line
            assert "engine" not in line, line
