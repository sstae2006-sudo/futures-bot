"""Tests for the Market Context Engine foundation (2026-07-27).

See docs/ARCHITECTURE.md's "Market Context Engine" section for the target
architecture and why this phase is data-shape/integration-point only --
``ContextEngine``'s classification methods are stubs, not real indicator
logic, so these tests verify construction/serialization/safety, not any
regime/volatility/trend detection (there isn't any yet).
"""
from __future__ import annotations

import inspect
import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from futures_bot.context import (
    ContextEngine,
    LiquidityState,
    MarketContext,
    MarketRegime,
    RiskState,
    SessionPhase,
    TrendState,
    VolatilityState,
    unknown_context,
)
from futures_bot.models import Bar


def _bar(day: str, price: str = "100") -> Bar:
    return Bar(
        timestamp=datetime.fromisoformat(f"{day}T00:00:00+00:00"),
        open=Decimal(price), high=Decimal(price), low=Decimal(price), close=Decimal(price),
        volume=1,
    )


class TestMarketContextCreation:
    """✓ Context object creates correctly."""

    def test_minimal_construction(self):
        ctx = MarketContext(
            timestamp=datetime(2026, 1, 5, tzinfo=timezone.utc),
            symbol="MES",
            timeframe="5min",
        )
        assert ctx.symbol == "MES"
        assert ctx.timeframe == "5min"

    def test_fully_specified_construction_matches_the_spec_example(self):
        # MarketContext: {session: "OPENING_RANGE", regime: "TRENDING_UP",
        # volatility: "NORMAL", trend: "BULLISH", confidence: 0.82}
        ctx = MarketContext(
            timestamp=datetime(2026, 1, 5, 9, 35, tzinfo=timezone.utc),
            symbol="MES",
            timeframe="5min",
            session=SessionPhase.OPENING_RANGE,
            market_regime=MarketRegime.TRENDING_UP,
            volatility_state=VolatilityState.NORMAL,
            trend_state=TrendState.BULLISH,
            confidence_scores={"market_regime": 0.82},
        )
        assert ctx.session is SessionPhase.OPENING_RANGE
        assert ctx.market_regime is MarketRegime.TRENDING_UP
        assert ctx.volatility_state is VolatilityState.NORMAL
        assert ctx.trend_state is TrendState.BULLISH
        assert ctx.confidence == pytest.approx(0.82)

    def test_is_immutable(self):
        ctx = MarketContext(
            timestamp=datetime(2026, 1, 5, tzinfo=timezone.utc), symbol="MES", timeframe="5min",
        )
        with pytest.raises(Exception):
            ctx.symbol = "MNQ"  # type: ignore[misc]

    def test_naive_timestamp_is_rejected(self):
        with pytest.raises(ValueError):
            MarketContext(timestamp=datetime(2026, 1, 5), symbol="MES", timeframe="5min")

    def test_empty_symbol_is_rejected(self):
        with pytest.raises(ValueError):
            MarketContext(timestamp=datetime(2026, 1, 5, tzinfo=timezone.utc), symbol="", timeframe="5min")

    def test_confidence_score_out_of_range_is_rejected(self):
        with pytest.raises(ValueError):
            MarketContext(
                timestamp=datetime(2026, 1, 5, tzinfo=timezone.utc), symbol="MES", timeframe="5min",
                confidence_scores={"trend_state": 1.5},
            )


class TestMissingValuesHandledSafely:
    """✓ Missing values handled safely."""

    def test_only_required_fields_defaults_everything_else_to_unknown(self):
        ctx = MarketContext(
            timestamp=datetime(2026, 1, 5, tzinfo=timezone.utc), symbol="MES", timeframe="5min",
        )
        assert ctx.session is SessionPhase.UNKNOWN
        assert ctx.market_regime is MarketRegime.UNKNOWN
        assert ctx.volatility_state is VolatilityState.UNKNOWN
        assert ctx.trend_state is TrendState.UNKNOWN
        assert ctx.liquidity_state is LiquidityState.UNKNOWN
        assert ctx.risk_state is RiskState.UNKNOWN

    def test_no_confidence_scores_means_zero_confidence_not_an_error(self):
        ctx = MarketContext(
            timestamp=datetime(2026, 1, 5, tzinfo=timezone.utc), symbol="MES", timeframe="5min",
        )
        assert ctx.confidence_scores == {}
        assert ctx.confidence == 0.0

    def test_unknown_context_helper_matches_bare_defaults(self):
        ts = datetime(2026, 1, 5, tzinfo=timezone.utc)
        assert unknown_context(ts, "MES", "5min") == MarketContext(timestamp=ts, symbol="MES", timeframe="5min")

    def test_context_engine_with_no_bars_returns_unknown_for_every_data_dependent_dimension(self):
        # Session classification is real as of 2026-07-27 (see
        # test_context_session.py) and doesn't need bars at all -- only
        # a timestamp -- so it's no longer part of what "safe with no
        # data" means here. Volatility and regime are also real now
        # (test_context_volatility.py / test_context_regime.py) but
        # still return UNKNOWN with zero bars -- not because they're
        # stubbed, but because there's genuinely no data to classify.
        # Trend/liquidity/risk remain actual stubs.
        engine = ContextEngine(symbol="MES", timeframe="5min")
        ctx = engine.build_context(timestamp=datetime(2026, 1, 5, tzinfo=timezone.utc))
        assert ctx.market_regime is MarketRegime.UNKNOWN
        assert ctx.volatility_state is VolatilityState.UNKNOWN
        assert ctx.trend_state is TrendState.UNKNOWN
        assert ctx.liquidity_state is LiquidityState.UNKNOWN
        assert ctx.risk_state is RiskState.UNKNOWN

    def test_context_engine_with_a_few_bars_still_returns_unknown_for_data_hungry_dimensions(self):
        # 3 bars is nowhere near enough history for volatility (needs
        # atr_period + 1) or regime (needs that plus 2 * adx_period for
        # ADX) -- both correctly stay UNKNOWN, same "insufficient data
        # handled safely" contract as with zero bars. trend_state is a
        # genuine stub regardless of bars.
        engine = ContextEngine(symbol="MES", timeframe="5min")
        bars = [_bar("2026-01-05"), _bar("2026-01-06"), _bar("2026-01-07")]
        ctx = engine.build_context(timestamp=bars[-1].timestamp, bars=bars)
        assert ctx.market_regime is MarketRegime.UNKNOWN
        assert ctx.trend_state is TrendState.UNKNOWN
        assert ctx.volatility_state is VolatilityState.UNKNOWN

    def test_from_dict_with_only_required_keys(self):
        ctx = MarketContext.from_dict({
            "timestamp": "2026-01-05T00:00:00+00:00", "symbol": "MES", "timeframe": "5min",
        })
        assert ctx.session is SessionPhase.UNKNOWN
        assert ctx.confidence_scores == {}


class TestSerialization:
    """✓ Serialization works."""

    def test_to_dict_round_trips_through_from_dict(self):
        original = MarketContext(
            timestamp=datetime(2026, 1, 5, 9, 35, tzinfo=timezone.utc),
            symbol="MES",
            timeframe="5min",
            session=SessionPhase.OPENING_RANGE,
            market_regime=MarketRegime.TRENDING_UP,
            volatility_state=VolatilityState.NORMAL,
            trend_state=TrendState.BULLISH,
            liquidity_state=LiquidityState.DEEP,
            risk_state=RiskState.LOW,
            confidence_scores={"market_regime": 0.82, "trend_state": 0.6},
        )
        restored = MarketContext.from_dict(original.to_dict())
        assert restored == original

    def test_to_dict_is_json_serializable(self):
        ctx = MarketContext(
            timestamp=datetime(2026, 1, 5, tzinfo=timezone.utc),
            symbol="MES", timeframe="5min",
            session=SessionPhase.OPENING_RANGE,
            confidence_scores={"session": 0.9},
        )
        text = json.dumps(ctx.to_dict())
        parsed = json.loads(text)
        assert parsed["session"] == "OPENING_RANGE"
        assert parsed["symbol"] == "MES"
        assert parsed["confidence"] == pytest.approx(0.9)

    def test_enum_values_serialize_as_plain_strings_not_repr(self):
        ctx = MarketContext(
            timestamp=datetime(2026, 1, 5, tzinfo=timezone.utc), symbol="MES", timeframe="5min",
            market_regime=MarketRegime.RANGING,
        )
        d = ctx.to_dict()
        assert d["market_regime"] == "RANGING"
        assert isinstance(d["market_regime"], str)

    def test_from_dict_accepts_plain_string_timestamp_and_enum_values(self):
        ctx = MarketContext.from_dict({
            "timestamp": "2026-01-05T09:35:00+00:00",
            "symbol": "MES",
            "timeframe": "5min",
            "market_regime": "TRENDING_UP",
            "confidence_scores": {"market_regime": 0.7},
        })
        assert ctx.market_regime is MarketRegime.TRENDING_UP
        assert ctx.timestamp == datetime(2026, 1, 5, 9, 35, tzinfo=timezone.utc)


class TestExistingTradingSystemUnaffected:
    """✓ Existing trading system unaffected.

    This module is standalone and imported by nothing in the existing
    decision path -- these tests assert that boundary directly, on top
    of the fact that the full existing test suite (engine/strategy/risk)
    passes unchanged with this module present.
    """

    def test_strategy_on_bar_signature_is_unchanged(self):
        from futures_bot.strategy.base import Strategy

        params = list(inspect.signature(Strategy.on_bar).parameters)
        assert params == ["self", "bars", "position"]

    def test_trading_engine_does_not_import_context(self):
        import futures_bot.engine as engine_module

        assert "context" not in engine_module.__dict__
        assert not hasattr(engine_module.TradingEngine, "context_engine")

    def test_strategy_base_does_not_import_context(self):
        import futures_bot.strategy.base as strategy_base

        source = inspect.getsource(strategy_base)
        assert "context" not in source.lower()

    def test_context_module_has_no_import_of_engine_risk_or_brokers(self):
        # The context engine must never gain a way to reach the broker or
        # risk manager -- see models.MarketContext's docstring. Checked
        # here as an explicit guard over actual import statements (not
        # docstring prose, which legitimately mentions these modules by
        # name when explaining the boundary).
        import futures_bot.context.context_engine as ce_module
        import futures_bot.context.models as models_module

        for module in (ce_module, models_module):
            import_lines = [
                line.strip() for line in inspect.getsource(module).splitlines()
                if line.strip().startswith(("import ", "from "))
            ]
            for line in import_lines:
                assert "risk.manager" not in line, line
                assert "brokers" not in line, line
                assert "engine" not in line, line
