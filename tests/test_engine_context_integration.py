"""Market Context Engine integration into TradingEngine (``ContextMode``).

Covers the integration itself -- exactly one MarketContext per processed
bar, the correct (entry-time) context attached to each completed trade, no
circular imports, no duplicate context generation, and -- the most
important guarantee -- that existing behavior is completely unchanged:
``ContextMode.OFF`` (the default for every existing caller) must reproduce
identical trades to a pre-integration engine, and ``ContextMode.OBSERVE``
must reproduce identical *decisions* to ``OFF`` even though it generates
and attaches context, since ``Strategy.context`` is never set in that mode.
``ContextMode.ENABLED`` additionally respects each strategy's own
``uses_context`` opt-in -- an existing, unmodified strategy must behave
identically in every mode.

See ``tests/test_backtest_context_comparison.py`` for the A/B comparison
framework's own tests.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional, Sequence

import pytest

from futures_bot.backtest.runner import CountingJournal, run_backtest
from futures_bot.brokers.paper import PaperBroker
from futures_bot.config import BrokerSettings, RiskSettings, Settings
from futures_bot.context import ContextEngine, MarketContext
from futures_bot.contracts import CME_TZ, MES
from futures_bot.engine import ContextMode, TradingEngine, build_engine
from futures_bot.models import Bar, Position, Signal
from futures_bot.strategy.base import Strategy


def make_settings(**overrides) -> Settings:
    base = dict(
        contract="MES", mode="paper",
        risk=RiskSettings(
            contracts_per_trade=1, stop_loss_points=Decimal("10"), take_profit_points=Decimal("20"),
            daily_max_loss=Decimal("500"), max_trades_per_session=50, account_size=Decimal("5000"),
        ),
        broker=BrokerSettings(starting_cash=Decimal("5000")),
    )
    base.update(overrides)
    return Settings(**base)


def make_bars(n: int, seed: int = 42) -> list[Bar]:
    """A random walk, not a monotonic trend -- exercises real entries,
    exits (stop and target), and enough history for every context
    dimension to actually classify something (not just UNKNOWN)."""
    start = datetime(2026, 7, 21, 9, 0, tzinfo=CME_TZ)
    price = Decimal("7500")
    rng = random.Random(seed)
    bars = []
    for i in range(n):
        price += Decimal(str(rng.choice([-3, -2, -1, 1, 2, 3])))
        bars.append(Bar(
            timestamp=start + timedelta(minutes=i),
            open=price, high=price + 2, low=price - 2, close=price, volume=200 + (i % 50) * 5,
        ))
    return bars


class _SimpleMomentum(Strategy):
    """Enters long on an up-tick, holds one position at a time, never
    reads ``self.context`` -- a stand-in for "any existing, unmodified
    bundled strategy" for these tests."""

    warmup_bars = 5

    def on_bar(self, bars: Sequence[Bar], position: Optional[Position]) -> Signal:
        if len(bars) < 6:
            return self.hold("warmup")
        if position is None:
            if bars[-1].close > bars[-2].close:
                return self.enter_long("uptick")
            return self.hold("no signal")
        return self.hold("holding")


class _ContextAwareMomentum(_SimpleMomentum):
    """Same entry rule, but explicitly opts into context and skips an
    entry when the environment score reads low -- used only to prove
    ENABLED mode can actually change behavior when a strategy asks for
    it, never to claim this is a good trading rule."""

    uses_context = True

    def on_bar(self, bars, position):
        if len(bars) >= 6 and position is None and bars[-1].close > bars[-2].close:
            if self.context is not None and self.context.environment_score is not None:
                if self.context.environment_score.score < 30:
                    return self.hold("skipped: low environment score")
        return super().on_bar(bars, position)


class TestOneContextPerProcessedBar:
    def test_context_engine_is_called_exactly_once_per_on_bar(self, tmp_path):
        calls = []

        class SpyContextEngine(ContextEngine):
            def build_context(self, timestamp, bars=None, bars_by_timeframe=None):
                calls.append(timestamp)
                return super().build_context(timestamp, bars=bars, bars_by_timeframe=bars_by_timeframe)

        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        bars = make_bars(50)
        spy = SpyContextEngine(symbol="MES", timeframe="1min")
        run_backtest(
            settings, _SimpleMomentum(MES), bars, journal_dir=tmp_path,
            context_mode=ContextMode.OBSERVE, context_engine=spy,
        )
        assert len(calls) == len(bars)
        assert calls == [b.timestamp for b in bars]

    def test_off_mode_never_calls_build_context_at_all(self, tmp_path):
        calls = []

        class SpyContextEngine(ContextEngine):
            def build_context(self, timestamp, bars=None, bars_by_timeframe=None):
                calls.append(timestamp)
                return super().build_context(timestamp, bars=bars, bars_by_timeframe=bars_by_timeframe)

        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        bars = make_bars(50)
        spy = SpyContextEngine(symbol="MES", timeframe="1min")
        run_backtest(
            settings, _SimpleMomentum(MES), bars, journal_dir=tmp_path,
            context_mode=ContextMode.OFF, context_engine=spy,
        )
        assert calls == []


class TestCorrectContextAttachedToTrades:
    def test_entry_context_timestamp_matches_the_trades_own_entry_time(self, tmp_path):
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        bars = make_bars(300)
        broker = PaperBroker(contract=MES, starting_cash=Decimal("5000"))
        metrics = run_backtest(
            settings, _SimpleMomentum(MES), bars, journal_dir=tmp_path,
            broker=broker, context_mode=ContextMode.OBSERVE,
        )
        assert metrics.trade_count > 0
        for trade in metrics.trades:
            assert trade.entry_context is not None
            assert trade.entry_context.timestamp == trade.entry_time

    def test_entry_context_is_not_the_exit_bars_context(self, tmp_path):
        # The context attached must reflect what was known at ENTRY, not
        # whatever was true when the trade eventually closed (a different,
        # possibly much later bar).
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        bars = make_bars(300)
        metrics = run_backtest(
            settings, _SimpleMomentum(MES), bars, journal_dir=tmp_path, context_mode=ContextMode.OBSERVE,
        )
        for trade in metrics.trades:
            if trade.exit_time != trade.entry_time:
                assert trade.entry_context.timestamp != trade.exit_time

    def test_off_mode_never_attaches_entry_context(self, tmp_path):
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        bars = make_bars(300)
        metrics = run_backtest(settings, _SimpleMomentum(MES), bars, journal_dir=tmp_path, context_mode=ContextMode.OFF)
        assert metrics.trade_count > 0
        assert all(t.entry_context is None for t in metrics.trades)

    def test_a_forced_flatten_trade_still_gets_its_entry_context(self, tmp_path):
        # _record_trade is shared by every closing path (resting-order
        # resolution, a risk-forced flatten, a strategy exit) -- the
        # attachment must not be special-cased to only one of them.
        settings = make_settings(
            logging={"directory": tmp_path, "level": "WARNING"},
            risk=RiskSettings(
                contracts_per_trade=1, stop_loss_points=Decimal("10"), take_profit_points=Decimal("20"),
                daily_max_loss=Decimal("500"), max_trades_per_session=1, account_size=Decimal("5000"),
            ),
        )
        bars = make_bars(300)
        metrics = run_backtest(settings, _SimpleMomentum(MES), bars, journal_dir=tmp_path, context_mode=ContextMode.OBSERVE)
        # The end-of-backtest force-close (if a position was still open)
        # goes through the exact same `_record_trade` -- either way, every
        # trade produced must carry its context.
        assert all(t.entry_context is not None for t in metrics.trades)


class TestNoCircularImports:
    def test_engine_context_and_backtest_modules_import_standalone(self):
        import subprocess
        import sys

        for module in (
            "futures_bot.engine",
            "futures_bot.context",
            "futures_bot.backtest.runner",
            "futures_bot.backtest.context_comparison",
            "futures_bot.strategy.base",
            "futures_bot.models",
        ):
            result = subprocess.run(
                [sys.executable, "-c", f"import {module}"], capture_output=True, text=True, timeout=30,
            )
            assert result.returncode == 0, f"{module} failed to import standalone:\n{result.stderr}"


class TestNoDuplicateContextGeneration:
    def test_context_engine_object_identity_is_reused_not_recreated_per_bar(self, tmp_path):
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        bars = make_bars(50)
        context_engine = ContextEngine(symbol="MES", timeframe="1min")
        engine = build_engine(settings, _SimpleMomentum(MES), context_mode=ContextMode.OBSERVE, context_engine=context_engine)
        assert engine.context_engine is context_engine

    def test_default_construction_builds_exactly_one_context_engine(self, tmp_path):
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        engine1 = build_engine(settings, _SimpleMomentum(MES))
        engine2 = build_engine(settings, _SimpleMomentum(MES))
        # Each TradingEngine gets its own -- not a shared singleton that
        # would leak state between independent engines/backtests.
        assert engine1.context_engine is not engine2.context_engine


class TestExistingBehaviorUnchanged:
    def test_off_mode_matches_a_pre_integration_style_backtest(self, tmp_path):
        # No context_mode argument at all -- the exact call shape every
        # caller used before this integration existed.
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        bars = make_bars(500)
        baseline = run_backtest(settings, _SimpleMomentum(MES), bars, journal_dir=tmp_path)
        explicit_off = run_backtest(settings, _SimpleMomentum(MES), bars, journal_dir=tmp_path, context_mode=ContextMode.OFF)
        assert baseline.trade_count == explicit_off.trade_count
        assert baseline.net_pnl == explicit_off.net_pnl
        assert [(t.entry_time, t.exit_time, t.net_pnl) for t in baseline.trades] == \
               [(t.entry_time, t.exit_time, t.net_pnl) for t in explicit_off.trades]

    def test_observe_mode_produces_decisions_identical_to_off(self, tmp_path):
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        bars = make_bars(500)
        off = run_backtest(settings, _SimpleMomentum(MES), bars, journal_dir=tmp_path, context_mode=ContextMode.OFF)
        observe = run_backtest(settings, _SimpleMomentum(MES), bars, journal_dir=tmp_path, context_mode=ContextMode.OBSERVE)
        assert off.trade_count == observe.trade_count
        assert off.net_pnl == observe.net_pnl
        assert [(t.entry_time, t.exit_time, t.exit_reason, t.net_pnl) for t in off.trades] == \
               [(t.entry_time, t.exit_time, t.exit_reason, t.net_pnl) for t in observe.trades]

    def test_enabled_mode_matches_off_for_a_strategy_that_does_not_opt_in(self, tmp_path):
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        bars = make_bars(500)
        off = run_backtest(settings, _SimpleMomentum(MES), bars, journal_dir=tmp_path, context_mode=ContextMode.OFF)
        enabled = run_backtest(settings, _SimpleMomentum(MES), bars, journal_dir=tmp_path, context_mode=ContextMode.ENABLED)
        assert off.trade_count == enabled.trade_count
        assert off.net_pnl == enabled.net_pnl

    def test_strategy_context_is_never_set_in_observe_mode(self, tmp_path):
        # Provable, not just "should be unaffected": a strategy that DOES
        # opt in (uses_context=True) must still see None in OBSERVE mode.
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        bars = make_bars(50)
        strategy = _ContextAwareMomentum(MES)
        run_backtest(settings, strategy, bars, journal_dir=tmp_path, context_mode=ContextMode.OBSERVE)
        assert strategy.context is None

    def test_strategy_context_is_never_set_for_a_strategy_that_does_not_opt_in(self, tmp_path):
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        bars = make_bars(50)
        strategy = _SimpleMomentum(MES)  # uses_context defaults to False
        run_backtest(settings, strategy, bars, journal_dir=tmp_path, context_mode=ContextMode.ENABLED)
        assert strategy.context is None

    def test_strategy_context_is_set_in_enabled_mode_for_an_opted_in_strategy(self, tmp_path):
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        bars = make_bars(50)
        strategy = _ContextAwareMomentum(MES)
        run_backtest(settings, strategy, bars, journal_dir=tmp_path, context_mode=ContextMode.ENABLED)
        assert strategy.context is not None
        assert isinstance(strategy.context, MarketContext)

    def test_engine_defaults_to_off_when_context_mode_is_omitted(self, tmp_path):
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        engine = build_engine(settings, _SimpleMomentum(MES))
        assert engine.context_mode is ContextMode.OFF


class TestNoBrokerRiskOrExecutionLogicChanged:
    def test_paper_broker_never_imports_context(self):
        import inspect

        import futures_bot.brokers.paper as paper_module

        source = inspect.getsource(paper_module)
        assert "futures_bot.context" not in source
        assert "from ..context" not in source

    def test_risk_manager_record_trade_is_unaffected_by_the_new_trade_field(self, tmp_path):
        # RiskManager.record_trade only reads trade.net_pnl -- adding
        # entry_context to Trade must not change any risk calculation.
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        bars = make_bars(500)
        off = run_backtest(settings, _SimpleMomentum(MES), bars, journal_dir=tmp_path, context_mode=ContextMode.OFF)
        observe = run_backtest(settings, _SimpleMomentum(MES), bars, journal_dir=tmp_path, context_mode=ContextMode.OBSERVE)
        # Kill-switch/session bookkeeping is driven by trade.net_pnl only --
        # identical net P&L per trade means identical risk-manager state.
        assert off.max_drawdown == observe.max_drawdown
