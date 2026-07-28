"""Market Context Engine Phase 8, Part 3 -- complete internal validation.

These tests encode the audit performed for Phase 8's "Engine Validation"
requirement as lasting, executable checks (not just a one-time claim):
no circular imports, no duplicated logic/calendars/regime/session/
volatility definitions, module independence from the trading side,
determinism, missing-data safety, correct UNKNOWN behavior, and valid
confidence values. Every individual dimension already has its own
dedicated missing-data/UNKNOWN/confidence tests (test_context_*.py);
this file checks the *cross-cutting* properties instead of repeating
per-dimension coverage.
"""

from __future__ import annotations

import importlib
import inspect
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from futures_bot.context import ContextEngine
from futures_bot.context.models import (
    LiquidityState,
    MarketRegime,
    RiskState,
    SessionPhase,
    TrendState,
    VolatilityState,
)
from futures_bot.models import Bar

CT = ZoneInfo("America/Chicago")
START = datetime(2026, 1, 6, 17, 0, tzinfo=CT)

_CONTEXT_SUBMODULES = (
    "futures_bot.context.models",
    "futures_bot.context.session",
    "futures_bot.context.volatility",
    "futures_bot.context.regime",
    "futures_bot.context.trend",
    "futures_bot.context.timeframe",
    "futures_bot.context.structure",
    "futures_bot.context.liquidity",
    "futures_bot.context.risk",
    "futures_bot.context.scoring",
    "futures_bot.context.context_engine",
    "futures_bot.context",
)


def _zigzag(n_cycles: int, cycle_low_start: Decimal, cycle_low_drift: Decimal, start=None, volume: int = 300) -> list[Bar]:
    start = start or START
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


class TestNoCircularImports:
    def test_every_context_submodule_imports_standalone_in_a_fresh_process(self):
        # A subprocess, not importlib.reload(): reload() rebuilds a
        # module's namespace *in place* inside the already-running test
        # process, which mints brand-new Enum class objects (e.g. a
        # second, distinct MarketRegime) that no longer satisfy `is`
        # against ones other already-imported modules are still holding
        # a reference to -- this genuinely broke later tests in this
        # same file during this phase's audit before being caught here
        # and fixed. A real subprocess avoids corrupting this process's
        # module/enum identities while still proving each submodule has
        # no import-order dependency on any other.
        import subprocess
        import sys

        for name in _CONTEXT_SUBMODULES:
            result = subprocess.run(
                [sys.executable, "-c", f"import {name}"],
                capture_output=True, text=True, timeout=30,
            )
            assert result.returncode == 0, f"{name} failed to import standalone:\n{result.stderr}"


class TestNoDuplicatedLogic:
    """Every piece of reused math (ATR, ADX, SMA, trend-direction) has
    exactly one implementation; every other module in context/ imports
    it rather than re-deriving it."""

    def test_atr_series_and_true_range_are_defined_only_in_indicators(self):
        import futures_bot.strategy.indicators as indicators

        assert hasattr(indicators, "atr_series")
        assert hasattr(indicators, "true_range")
        for module_name in ("volatility", "regime", "trend"):
            module = importlib.import_module(f"futures_bot.context.{module_name}")
            source = inspect.getsource(module)
            assert "def atr_series(" not in source
            assert "def true_range(" not in source

    def test_classify_trend_is_defined_only_in_research_regime(self):
        import futures_bot.research.regime as research_regime

        assert hasattr(research_regime, "classify_trend")
        for module_name in ("regime", "timeframe", "trend"):
            module = importlib.import_module(f"futures_bot.context.{module_name}")
            source = inspect.getsource(module)
            assert "def classify_trend(" not in source

    def test_adx_is_defined_only_in_indicators(self):
        import futures_bot.strategy.indicators as indicators

        assert hasattr(indicators, "adx")
        for module_name in ("regime", "trend"):
            module = importlib.import_module(f"futures_bot.context.{module_name}")
            source = inspect.getsource(module)
            assert "def adx(" not in source

    def test_sma_is_defined_only_in_indicators(self):
        import futures_bot.strategy.indicators as indicators

        assert hasattr(indicators, "sma")
        module = importlib.import_module("futures_bot.context.liquidity")
        assert "def sma(" not in inspect.getsource(module)


class TestNoDuplicatedCalendarsRegimesOrSessions:
    def test_cme_calendar_functions_are_defined_only_in_contracts(self):
        import futures_bot.contracts as contracts

        for fn in ("is_weekend_closure", "is_cme_holiday", "in_maintenance_halt", "is_market_open"):
            assert hasattr(contracts, fn)
        session_source = inspect.getsource(importlib.import_module("futures_bot.context.session"))
        for fn in ("def is_weekend_closure(", "def is_cme_holiday(", "def in_maintenance_halt("):
            assert fn not in session_source

    def test_every_state_enum_is_defined_only_in_context_models(self):
        import futures_bot.context.models as models

        for enum_name in (
            "SessionPhase", "MarketRegime", "VolatilityState",
            "TrendState", "LiquidityState", "RiskState",
        ):
            assert hasattr(models, enum_name)
        for module_name in (
            "session", "volatility", "regime", "trend", "liquidity", "risk", "timeframe", "structure", "scoring",
        ):
            source = inspect.getsource(importlib.import_module(f"futures_bot.context.{module_name}"))
            for enum_name in (
                "class SessionPhase(", "class MarketRegime(", "class VolatilityState(",
                "class TrendState(", "class LiquidityState(", "class RiskState(",
            ):
                assert enum_name not in source


class TestModulesRemainIndependentFromTheTradingSide:
    def test_context_package_never_imports_risk_manager_brokers_or_engine(self):
        import futures_bot.context as context_pkg

        for module_name in (
            "context_engine", "models", "session", "volatility", "regime",
            "trend", "liquidity", "risk", "timeframe", "structure", "scoring",
        ):
            module = importlib.import_module(f"futures_bot.context.{module_name}")
            import_lines = [
                line.strip() for line in inspect.getsource(module).splitlines()
                if line.strip().startswith(("import ", "from "))
            ]
            for line in import_lines:
                assert "risk.manager" not in line, f"{module_name}: {line}"
                assert "brokers" not in line, f"{module_name}: {line}"
                assert ".engine" not in line and "futures_bot.engine" not in line, f"{module_name}: {line}"

    def test_risk_manager_and_brokers_still_have_zero_reference_to_context(self):
        # Historical note: through Phase 8, this test also asserted
        # engine.py/strategy/base.py had zero reference to context/ at
        # all -- true before integration. engine.py now imports context/
        # for real, by design (see engine.ContextMode and
        # tests/test_engine_context_integration.py for that integration's
        # own thorough tests); strategy/base.py's own reference is
        # TYPE_CHECKING-only (verified separately in
        # tests/test_context.py). risk/manager.py and every broker are
        # untouched by this integration -- requirement #6 ("no risk or
        # broker logic changes") -- and still have zero reference at all.
        import futures_bot.brokers.paper as paper_broker_module
        import futures_bot.brokers.tradovate as tradovate_broker_module
        import futures_bot.risk.manager as risk_manager_module

        for module in (risk_manager_module, paper_broker_module, tradovate_broker_module):
            source = inspect.getsource(module)
            assert "futures_bot.context" not in source
            assert "from ..context" not in source
            assert "from .context" not in source

    def test_engine_context_reference_is_gated_by_context_mode_off_default(self):
        import futures_bot.engine as engine_module

        assert hasattr(engine_module, "ContextEngine")  # real import, by design
        sig = inspect.signature(engine_module.TradingEngine.__init__)
        assert sig.parameters["context_mode"].default is engine_module.ContextMode.OFF


class TestContextGenerationIsDeterministic:
    def test_identical_inputs_produce_an_identical_market_context(self):
        bars = _zigzag(10, Decimal("5900"), Decimal("40"), volume=350)
        engine_a = ContextEngine(symbol="MES", timeframe="1min")
        engine_b = ContextEngine(symbol="MES", timeframe="1min")
        ctx_a = engine_a.build_context(timestamp=bars[-1].timestamp, bars=bars)
        ctx_b = engine_b.build_context(timestamp=bars[-1].timestamp, bars=bars)
        assert ctx_a == ctx_b

    def test_repeated_calls_on_the_same_engine_produce_identical_results(self):
        bars = _zigzag(10, Decimal("5900"), Decimal("40"), volume=350)
        engine = ContextEngine(symbol="MES", timeframe="1min")
        results = [engine.build_context(timestamp=bars[-1].timestamp, bars=bars) for _ in range(5)]
        assert all(r == results[0] for r in results)

    def test_no_wall_clock_or_randomness_in_context_source(self):
        for module_name in (
            "session", "volatility", "regime", "trend", "liquidity",
            "risk", "timeframe", "structure", "scoring", "context_engine",
        ):
            source = inspect.getsource(importlib.import_module(f"futures_bot.context.{module_name}"))
            assert "datetime.now(" not in source
            assert "date.today(" not in source
            assert "random." not in source


class TestMissingDataHandledSafelyAcrossEveryDimension:
    def test_no_bars_never_raises_and_every_dimension_is_unknown(self):
        engine = ContextEngine(symbol="MES", timeframe="1min")
        ctx = engine.build_context(timestamp=START)
        assert ctx.market_regime is MarketRegime.UNKNOWN
        assert ctx.volatility_state is VolatilityState.UNKNOWN
        assert ctx.trend_state is TrendState.UNKNOWN
        assert ctx.liquidity_state is LiquidityState.UNKNOWN
        assert ctx.risk_state is RiskState.UNKNOWN
        # Session is timestamp-only, so it's the one dimension that IS
        # classifiable with no bars at all -- correct, not a violation.
        assert ctx.session is not SessionPhase.UNKNOWN
        assert ctx.environment_score is not None

    def test_empty_bars_by_timeframe_never_raises(self):
        engine = ContextEngine(symbol="MES", timeframe="1min")
        ctx = engine.build_context(timestamp=START, bars_by_timeframe={})
        assert ctx.timeframe_alignment.alignment == {}


class TestUnknownStatesBehaveCorrectly:
    def test_every_unknown_dimension_carries_zero_confidence_and_no_reason(self):
        engine = ContextEngine(symbol="MES", timeframe="1min")
        ctx = engine.build_context(timestamp=START)
        for key in ("volatility", "regime", "trend", "liquidity", "risk", "timeframe_alignment", "structure"):
            assert key not in ctx.confidence_scores
        assert ctx.regime_context.confidence == 0.0
        assert ctx.volatility_context.volatility_ratio is None
        assert ctx.trend_context.confidence == 0.0
        assert ctx.liquidity_context.confidence == 0.0
        assert ctx.risk_context.confidence == 0.0
        assert ctx.structure_context.structure_confidence == 0.0


class TestConfidenceCalculationsRemainValid:
    def test_every_confidence_value_stays_in_0_to_1_across_many_scenarios(self):
        scenarios = [
            _zigzag(10, Decimal("5900"), Decimal("40"), volume=350),
            _zigzag(10, Decimal("6100"), Decimal("-40"), volume=50),
            _zigzag(10, Decimal("5900"), Decimal("0"), volume=300),
            [],
        ]
        engine = ContextEngine(symbol="MES", timeframe="1min")
        for bars in scenarios:
            ts = bars[-1].timestamp if bars else START
            ctx = engine.build_context(timestamp=ts, bars=bars)
            assert 0.0 <= ctx.confidence <= 1.0
            for value in ctx.confidence_scores.values():
                assert 0.0 <= value <= 1.0
            assert 0.0 <= ctx.regime_context.confidence <= 1.0
            assert 0.0 <= ctx.trend_context.confidence <= 1.0
            assert 0.0 <= ctx.liquidity_context.confidence <= 1.0
            assert 0.0 <= ctx.risk_context.confidence <= 1.0
            assert 0.0 <= ctx.structure_context.structure_confidence <= 1.0
            assert 0.0 <= ctx.environment_score.confidence <= 1.0
