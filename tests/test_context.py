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
        # data" means here. Every other dimension is real too as of
        # Phase 8 (test_context_regime.py / test_context_volatility.py /
        # test_context_trend.py / test_context_liquidity.py /
        # test_context_risk.py) but still returns UNKNOWN with zero
        # bars -- not because anything is stubbed anymore, but because
        # there's genuinely no data to classify (risk additionally needs
        # neither volatility nor regime to be known, both of which are
        # themselves bar-dependent).
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
        # handled safely" contract as with zero bars. trend_state, by
        # contrast, only needs 2 closes (see context/trend.py) and IS
        # classifiable here -- these fixture bars are all equal price,
        # so it reads NEUTRAL, not UNKNOWN; that's correct, not a stub.
        engine = ContextEngine(symbol="MES", timeframe="5min")
        bars = [_bar("2026-01-05"), _bar("2026-01-06"), _bar("2026-01-07")]
        ctx = engine.build_context(timestamp=bars[-1].timestamp, bars=bars)
        assert ctx.market_regime is MarketRegime.UNKNOWN
        assert ctx.trend_state is TrendState.NEUTRAL
        assert ctx.volatility_state is VolatilityState.UNKNOWN

    def test_from_dict_with_only_required_keys(self):
        ctx = MarketContext.from_dict({
            "timestamp": "2026-01-05T00:00:00+00:00", "symbol": "MES", "timeframe": "5min",
        })
        assert ctx.session is SessionPhase.UNKNOWN
        assert ctx.confidence_scores == {}

    def test_from_dict_accepts_a_pre_phase_8_dict_missing_the_newer_context_keys(self):
        # A dict shaped like what Phase 2a (session-only) would have
        # produced -- entirely missing the trend_context/
        # liquidity_context/risk_context/environment_score keys Phase 8
        # added, not just present-but-null. Forward/backward
        # compatibility of stored/logged MarketContext JSON depends on
        # this still working.
        old_format = {
            "timestamp": "2026-01-05T00:00:00+00:00",
            "symbol": "MES",
            "timeframe": "5min",
            "session": "OPENING_RANGE",
        }
        ctx = MarketContext.from_dict(old_format)
        assert ctx.session is SessionPhase.OPENING_RANGE
        assert ctx.trend_context is None
        assert ctx.liquidity_context is None
        assert ctx.risk_context is None
        assert ctx.environment_score is None
        assert ctx.trend_state is TrendState.UNKNOWN
        assert ctx.liquidity_state is LiquidityState.UNKNOWN
        assert ctx.risk_state is RiskState.UNKNOWN


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

    Historical note: through Phase 8, this class asserted that
    `engine.py`/`strategy/base.py` had *no* reference to `context/` at
    all -- true at the time, since integration hadn't happened yet. The
    Market Context Engine is now wired into `TradingEngine` (see
    `engine.ContextMode` and `tests/test_engine_context_integration.py`
    for the integration's own, much more thorough tests), so those two
    checks are updated below to verify the *actual* invariant that
    matters: `Strategy.on_bar`'s call signature is still unchanged (no
    strategy needs to change to keep working), `strategy/base.py`'s own
    reference to `context/` is TYPE_CHECKING-only (never executed, purely
    for IDE/type-checker benefit), and `ContextMode.OFF` -- the default
    for every caller not explicitly opting in -- is a true no-op.
    """

    def test_strategy_on_bar_signature_is_unchanged(self):
        from futures_bot.strategy.base import Strategy

        params = list(inspect.signature(Strategy.on_bar).parameters)
        assert params == ["self", "bars", "position"]

    def test_trading_engine_defaults_to_context_mode_off(self):
        import futures_bot.engine as engine_module

        assert hasattr(engine_module, "ContextEngine")  # real import now, by design
        assert hasattr(engine_module.TradingEngine, "__init__")
        sig = inspect.signature(engine_module.TradingEngine.__init__)
        assert sig.parameters["context_mode"].default is engine_module.ContextMode.OFF

    def test_strategy_base_reference_to_context_is_type_checking_only(self):
        # A TYPE_CHECKING-guarded import never actually executes, so the
        # imported name is absent from the module's real runtime
        # namespace -- the precise, unambiguous way to confirm
        # strategy/base.py's `from ..context.models import MarketContext`
        # is purely a type hint, never a real dependency.
        import futures_bot.strategy.base as strategy_base

        assert "MarketContext" not in vars(strategy_base)

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
