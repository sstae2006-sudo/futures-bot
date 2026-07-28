"""backtest/context_comparison.py -- the Context OFF vs ENABLED A/B
comparison framework.

Covers: no duplicate backtesting pipeline (both runs go through the same
run_backtest/TradingEngine), the reported metrics come straight off
BacktestMetrics (nothing recomputed independently), and the trade-level
diff correctly classifies unchanged/removed/added/entered-differently/
exited-differently trades, each carrying the MarketContext/EnvironmentScore
that explains it.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional, Sequence

import pytest

from futures_bot.backtest.context_comparison import (
    TradeChangeKind,
    compare_context_impact,
)
from futures_bot.config import BrokerSettings, RiskSettings, Settings
from futures_bot.contracts import CME_TZ, MES
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
    warmup_bars = 5

    def on_bar(self, bars: Sequence[Bar], position: Optional[Position]) -> Signal:
        if len(bars) < 6:
            return self.hold("warmup")
        if position is None:
            if bars[-1].close > bars[-2].close:
                return self.enter_long("uptick")
            return self.hold("no signal")
        return self.hold("holding")


class _ContextFilteredMomentum(_SimpleMomentum):
    """Skips an entry whenever the environment score reads below 30 --
    deterministic and guaranteed to actually change some trades over a
    long-enough random walk, purely for exercising the diff logic. Not a
    claim that this is a good trading rule."""

    uses_context = True

    def on_bar(self, bars, position):
        if len(bars) >= 6 and position is None and bars[-1].close > bars[-2].close:
            if self.context is not None and self.context.environment_score is not None:
                if self.context.environment_score.score < 30:
                    return self.hold("skipped: low environment score")
        return super().on_bar(bars, position)


class TestNoDuplicatePipeline:
    def test_both_runs_use_run_backtest(self, tmp_path, monkeypatch):
        import futures_bot.backtest.context_comparison as comparison_module

        calls = []
        original = comparison_module.run_backtest

        def spy(*args, **kwargs):
            calls.append(kwargs.get("context_mode"))
            return original(*args, **kwargs)

        monkeypatch.setattr(comparison_module, "run_backtest", spy)

        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        bars = make_bars(50)
        compare_context_impact(settings, lambda: _SimpleMomentum(MES), bars, journal_dir=tmp_path)

        assert len(calls) == 2
        from futures_bot.engine import ContextMode

        assert set(calls) == {ContextMode.OBSERVE, ContextMode.ENABLED}


class TestMetricsComeDirectlyFromBacktestMetrics:
    def test_metrics_summary_matches_the_underlying_backtest_metrics(self, tmp_path):
        from futures_bot.backtest.runner import run_backtest
        from futures_bot.engine import ContextMode

        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        bars = make_bars(300)

        report = compare_context_impact(settings, lambda: _SimpleMomentum(MES), bars, journal_dir=tmp_path)
        direct = run_backtest(settings, _SimpleMomentum(MES), bars, journal_dir=tmp_path, context_mode=ContextMode.OBSERVE)

        assert report.baseline.net_profit == direct.net_pnl
        assert report.baseline.total_trades == direct.trade_count
        assert report.baseline.win_rate == direct.win_rate
        assert report.baseline.profit_factor == direct.profit_factor
        assert report.baseline.max_drawdown == direct.max_drawdown
        assert report.baseline.largest_winner == direct.largest_win
        assert report.baseline.largest_loser == direct.largest_loss


class TestNonContextAwareStrategyShowsNoChanges:
    def test_existing_strategy_reports_zero_changed_trades(self, tmp_path):
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        bars = make_bars(500)
        report = compare_context_impact(settings, lambda: _SimpleMomentum(MES), bars, journal_dir=tmp_path)
        assert report.trades_changed == 0
        assert all(c.kind is TradeChangeKind.UNCHANGED for c in report.changes)
        assert report.baseline.total_trades == report.with_context.total_trades
        assert report.baseline.net_profit == report.with_context.net_profit


class TestContextAwareStrategyShowsRealChanges:
    def test_filtering_strategy_reports_removed_trades(self, tmp_path):
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        bars = make_bars(500)
        report = compare_context_impact(settings, lambda: _ContextFilteredMomentum(MES), bars, journal_dir=tmp_path)

        assert report.trades_changed > 0
        assert report.with_context.total_trades < report.baseline.total_trades
        removed = [c for c in report.changes if c.kind is TradeChangeKind.REMOVED_BY_CONTEXT]
        assert len(removed) > 0

    def test_every_changed_trade_carries_market_context_and_environment_score(self, tmp_path):
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        bars = make_bars(500)
        report = compare_context_impact(settings, lambda: _ContextFilteredMomentum(MES), bars, journal_dir=tmp_path)

        changed = [c for c in report.changes if c.kind is not TradeChangeKind.UNCHANGED]
        assert changed
        for change in changed:
            assert change.market_context is not None
            assert change.environment_score is not None
            assert change.explanation

    def test_at_least_one_removed_trade_has_a_low_environment_score_matching_the_filter_rule(self, tmp_path):
        # Only the *first* divergence between the two runs is guaranteed
        # to be directly explained by the filter rule: once one run skips
        # an entry the other took, the two runs' open-position timelines
        # can diverge (the skipping run stays flat and re-evaluates bars
        # the other run was busy holding through), so later "removed"
        # trades may be a downstream consequence of that first divergence
        # rather than each independently scoring below the threshold --
        # an inherent property of comparing two sequential, stateful
        # trading runs, not a defect in the diff logic. At least one
        # match is the correct, defensible claim.
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        bars = make_bars(500)
        report = compare_context_impact(settings, lambda: _ContextFilteredMomentum(MES), bars, journal_dir=tmp_path)

        removed = [c for c in report.changes if c.kind is TradeChangeKind.REMOVED_BY_CONTEXT]
        assert removed
        assert any(change.environment_score.score < 30 for change in removed)

    def test_baseline_and_with_context_trade_counts_reconcile_with_the_diff(self, tmp_path):
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        bars = make_bars(500)
        report = compare_context_impact(settings, lambda: _ContextFilteredMomentum(MES), bars, journal_dir=tmp_path)

        removed = sum(1 for c in report.changes if c.kind is TradeChangeKind.REMOVED_BY_CONTEXT)
        added = sum(1 for c in report.changes if c.kind is TradeChangeKind.ADDED_BY_CONTEXT)
        entered_differently = sum(1 for c in report.changes if c.kind is TradeChangeKind.ENTERED_DIFFERENTLY)
        unchanged = sum(1 for c in report.changes if c.kind is TradeChangeKind.UNCHANGED)
        exited_differently = sum(1 for c in report.changes if c.kind is TradeChangeKind.EXITED_DIFFERENTLY)

        assert unchanged + entered_differently + exited_differently + removed == report.baseline.total_trades
        assert unchanged + entered_differently + exited_differently + added == report.with_context.total_trades


class TestFreshStrategyInstancePerRun:
    def test_strategy_factory_is_called_once_per_run_not_shared(self, tmp_path):
        instances = []

        def factory():
            s = _SimpleMomentum(MES)
            instances.append(s)
            return s

        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        bars = make_bars(50)
        compare_context_impact(settings, factory, bars, journal_dir=tmp_path)

        assert len(instances) == 2
        assert instances[0] is not instances[1]
