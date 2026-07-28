"""Platform Verification Phase 1 -- proves the Market Context Engine
integration is completely correct and introduces zero behavioral
regressions. See docs/PLATFORM_VERIFICATION_PHASE1.md for the full
audit report this file's results feed into.

This file is deliberately more exhaustive/explicit than
test_engine_context_integration.py's own coverage -- it checks each of
the specific metrics the verification asked for individually (entry/exit
timestamps, entry/exit prices, exit reasons, net P&L, win rate, profit
factor) rather than only a bundled tuple comparison, and adds the
MarketContext internal-consistency and stale-instance-reuse checks the
audit specifically asked for.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional, Sequence

import pytest

from futures_bot.backtest.runner import run_backtest
from futures_bot.config import BrokerSettings, RiskSettings, Settings
from futures_bot.context.models import (
    LiquidityState,
    MarketRegime,
    RiskState,
    SessionPhase,
    TrendState,
    VolatilityState,
)
from futures_bot.contracts import CME_TZ, MES
from futures_bot.engine import ContextMode
from futures_bot.models import Bar, Position, Signal
from futures_bot.risk.manager import RiskManager
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


class _ContextAwareButUnused(_SimpleMomentum):
    """Opts in (uses_context=True) but never actually reads self.context
    to change a decision -- proves ENABLED mode alone isn't what changes
    behavior; only a strategy that *acts* on the context can differ."""

    uses_context = True


class TestBackwardCompatibilityRegression:
    """Requirement #3: every one of these eight figures must be
    unchanged between OFF and a pre-integration-style call."""

    @pytest.fixture
    def runs(self, tmp_path):
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        bars = make_bars(600)
        baseline = run_backtest(settings, _SimpleMomentum(MES), bars, journal_dir=tmp_path)
        off = run_backtest(settings, _SimpleMomentum(MES), bars, journal_dir=tmp_path, context_mode=ContextMode.OFF)
        return baseline, off

    def test_entry_timestamps_unchanged(self, runs):
        baseline, off = runs
        assert [t.entry_time for t in baseline.trades] == [t.entry_time for t in off.trades]

    def test_exit_timestamps_unchanged(self, runs):
        baseline, off = runs
        assert [t.exit_time for t in baseline.trades] == [t.exit_time for t in off.trades]

    def test_entry_prices_unchanged(self, runs):
        baseline, off = runs
        assert [t.entry_price for t in baseline.trades] == [t.entry_price for t in off.trades]

    def test_exit_prices_unchanged(self, runs):
        baseline, off = runs
        assert [t.exit_price for t in baseline.trades] == [t.exit_price for t in off.trades]

    def test_exit_reasons_unchanged(self, runs):
        baseline, off = runs
        assert [t.exit_reason for t in baseline.trades] == [t.exit_reason for t in off.trades]

    def test_net_pnl_unchanged(self, runs):
        baseline, off = runs
        assert baseline.net_pnl == off.net_pnl

    def test_win_rate_unchanged(self, runs):
        baseline, off = runs
        assert baseline.win_rate == off.win_rate

    def test_profit_factor_unchanged(self, runs):
        baseline, off = runs
        assert baseline.profit_factor == off.profit_factor

    def test_observe_mode_also_matches_on_every_metric(self, tmp_path):
        # OBSERVE generates and attaches context but must not change a
        # single decision -- verified against the same eight figures.
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        bars = make_bars(600)
        off = run_backtest(settings, _SimpleMomentum(MES), bars, journal_dir=tmp_path, context_mode=ContextMode.OFF)
        observe = run_backtest(settings, _SimpleMomentum(MES), bars, journal_dir=tmp_path, context_mode=ContextMode.OBSERVE)
        assert [t.entry_time for t in off.trades] == [t.entry_time for t in observe.trades]
        assert [t.exit_time for t in off.trades] == [t.exit_time for t in observe.trades]
        assert [t.entry_price for t in off.trades] == [t.entry_price for t in observe.trades]
        assert [t.exit_price for t in off.trades] == [t.exit_price for t in observe.trades]
        assert [t.exit_reason for t in off.trades] == [t.exit_reason for t in observe.trades]
        assert off.net_pnl == observe.net_pnl
        assert off.win_rate == observe.win_rate
        assert off.profit_factor == observe.profit_factor

    def test_enabled_mode_matches_for_a_strategy_that_opts_in_but_never_reads_context(self, tmp_path):
        # Opting in (uses_context=True) alone changes nothing -- only a
        # strategy that actually branches on self.context can differ.
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        bars = make_bars(600)
        off = run_backtest(settings, _SimpleMomentum(MES), bars, journal_dir=tmp_path, context_mode=ContextMode.OFF)
        enabled = run_backtest(settings, _ContextAwareButUnused(MES), bars, journal_dir=tmp_path, context_mode=ContextMode.ENABLED)
        assert off.net_pnl == enabled.net_pnl
        assert off.trade_count == enabled.trade_count
        assert [t.exit_reason for t in off.trades] == [t.exit_reason for t in enabled.trades]


class TestMarketContextCompletenessAndConsistency:
    """Requirement #4: every completed trade's entry_context must carry
    all nine fields, and the bare enum fields must always agree with
    their corresponding rich nested object (guaranteed by construction
    in context_engine.py, verified here directly)."""

    @pytest.fixture
    def trades_with_context(self, tmp_path):
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        bars = make_bars(600)
        metrics = run_backtest(settings, _SimpleMomentum(MES), bars, journal_dir=tmp_path, context_mode=ContextMode.OBSERVE)
        assert metrics.trade_count > 0, "test needs at least one trade to verify anything"
        return [t.entry_context for t in metrics.trades]

    def test_every_trade_has_a_session_reading(self, trades_with_context):
        assert all(ctx.session_context is not None for ctx in trades_with_context)
        assert all(isinstance(ctx.session, SessionPhase) for ctx in trades_with_context)

    def test_every_trade_has_a_regime_reading(self, trades_with_context):
        assert all(ctx.regime_context is not None for ctx in trades_with_context)
        assert all(isinstance(ctx.market_regime, MarketRegime) for ctx in trades_with_context)

    def test_every_trade_has_a_trend_reading(self, trades_with_context):
        assert all(ctx.trend_context is not None for ctx in trades_with_context)
        assert all(isinstance(ctx.trend_state, TrendState) for ctx in trades_with_context)

    def test_every_trade_has_a_structure_reading(self, trades_with_context):
        assert all(ctx.structure_context is not None for ctx in trades_with_context)

    def test_every_trade_has_a_volatility_reading(self, trades_with_context):
        assert all(ctx.volatility_context is not None for ctx in trades_with_context)
        assert all(isinstance(ctx.volatility_state, VolatilityState) for ctx in trades_with_context)

    def test_every_trade_has_a_liquidity_reading(self, trades_with_context):
        assert all(ctx.liquidity_context is not None for ctx in trades_with_context)
        assert all(isinstance(ctx.liquidity_state, LiquidityState) for ctx in trades_with_context)

    def test_every_trade_has_a_risk_reading(self, trades_with_context):
        assert all(ctx.risk_context is not None for ctx in trades_with_context)
        assert all(isinstance(ctx.risk_state, RiskState) for ctx in trades_with_context)

    def test_every_trade_has_an_environment_score(self, trades_with_context):
        assert all(ctx.environment_score is not None for ctx in trades_with_context)
        for ctx in trades_with_context:
            assert 0 <= ctx.environment_score.score <= 100

    def test_every_trade_has_a_confidence_value(self, trades_with_context):
        for ctx in trades_with_context:
            assert 0.0 <= ctx.confidence <= 1.0

    def test_bare_enum_fields_agree_with_their_nested_context_object(self, trades_with_context):
        # Internal-consistency check: the "quick" enum field and the rich
        # nested object must always describe the same classification --
        # guaranteed by context_engine.py's construction (both set from
        # the same intermediate variable), verified directly here rather
        # than just trusted.
        for ctx in trades_with_context:
            assert ctx.session is ctx.session_context.session
            assert ctx.market_regime is ctx.regime_context.regime
            assert ctx.volatility_state is ctx.volatility_context.state
            assert ctx.trend_state is ctx.trend_context.trend
            assert ctx.liquidity_state is ctx.liquidity_context.state
            assert ctx.risk_state is ctx.risk_context.state

    def test_environment_score_breakdown_is_internally_consistent(self, trades_with_context):
        for ctx in trades_with_context:
            score = ctx.environment_score
            clamped_total = max(0, min(100, round(sum(score.breakdown.values()))))
            assert score.score == clamped_total


class _FlipStrategy(Strategy):
    """Re-enters immediately whenever flat -- combined with a very tight
    stop, this forces a position to close and a new one to open within
    the *same* on_bar call, repeatedly. Exercises the exact sequencing
    _pending_entry_context/_record_trade depend on: the closing trade
    must get the context from *its own* (earlier) entry bar, not the
    current bar's fresh reading meant for the new position."""

    warmup_bars = 2

    def on_bar(self, bars: Sequence[Bar], position: Optional[Position]) -> Signal:
        if len(bars) < 3:
            return self.hold("warmup")
        if position is None:
            return self.enter_long("enter")
        return self.hold("holding")


class TestSameBarCloseThenReentry:
    """A close-then-reenter within one bar is a real, pre-existing
    sequence this engine already supported (a stop/target hit resolves
    in step 1, then the strategy -- seeing position=None -- may enter
    again in step 4 of that same on_bar call). Verifies the entry-context
    attachment stays correct under this stress case rather than just
    reasoning about it."""

    def test_every_trade_gets_its_own_entry_bars_context_even_under_rapid_flipping(self, tmp_path):
        settings = make_settings(
            risk=RiskSettings(
                contracts_per_trade=1, stop_loss_points=Decimal("2"), take_profit_points=Decimal("100"),
                daily_max_loss=Decimal("5000"), max_trades_per_session=50, account_size=Decimal("5000"),
            ),
            logging={"directory": tmp_path, "level": "WARNING"},
        )
        # A tight 2-point stop against a 6-point range gets hit almost
        # every bar, forcing repeated same-bar close-then-reenter cycles.
        start = datetime(2026, 7, 21, 9, 0, tzinfo=CME_TZ)
        bars = [
            Bar(timestamp=start + timedelta(minutes=i), open=Decimal("7500"),
                high=Decimal("7501"), low=Decimal("7495"), close=Decimal("7497"), volume=500)
            for i in range(30)
        ]
        metrics = run_backtest(settings, _FlipStrategy(MES), bars, journal_dir=tmp_path, context_mode=ContextMode.OBSERVE)

        assert metrics.trade_count > 10, "test needs many rapid flips to be a meaningful stress case"
        for trade in metrics.trades:
            assert trade.entry_context is not None
            assert trade.entry_context.timestamp == trade.entry_time


class TestNoDuplicateContextGenerationEndToEnd:
    def test_build_context_called_exactly_once_per_bar_across_a_full_backtest(self, tmp_path):
        from futures_bot.context import ContextEngine

        calls = []

        class SpyContextEngine(ContextEngine):
            def build_context(self, timestamp, bars=None, bars_by_timeframe=None):
                calls.append(timestamp)
                return super().build_context(timestamp, bars=bars, bars_by_timeframe=bars_by_timeframe)

        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        bars = make_bars(300)
        spy = SpyContextEngine(symbol="MES", timeframe="1min")
        run_backtest(settings, _SimpleMomentum(MES), bars, journal_dir=tmp_path, context_mode=ContextMode.OBSERVE, context_engine=spy)

        assert len(calls) == len(bars)
        assert len(calls) == len(set(calls)), "duplicate timestamps would indicate double-processing a bar"


class TestRiskManagerRecordTradeUnaffected:
    def test_record_trade_only_reads_net_pnl_not_the_new_field(self):
        import inspect

        source = inspect.getsource(RiskManager.record_trade)
        assert "entry_context" not in source


class TestStaleStrategyContextAcrossReusedInstancesIsResolved:
    """Platform Verification Phase 1 found that reusing one Strategy
    instance across two separate engine runs with different
    ContextMode/settings could leave a stale self.context from the
    first run visible during the second (OFF *after* an ENABLED run on
    the same instance left the old value in place, since OFF never
    touched Strategy.context at all). Platform Verification Phase 2
    closed this defensively and automatically: TradingEngine now sets
    self.strategy.context every bar (to the real value or None) and
    also resets it at construction, in __init__, so no caller has to
    remember to clear anything. This test proves the fix, superseding
    the finding this class used to just document.
    """

    def test_reusing_a_strategy_instance_across_modes_no_longer_leaves_a_stale_context(self, tmp_path):
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        bars = make_bars(60)

        shared_strategy = _ContextAwareButUnused(MES)
        run_backtest(settings, shared_strategy, bars, journal_dir=tmp_path, context_mode=ContextMode.ENABLED)
        assert shared_strategy.context is not None  # set during the ENABLED run

        run_backtest(settings, shared_strategy, bars, journal_dir=tmp_path, context_mode=ContextMode.OFF)
        # Fixed in Phase 2: OFF now explicitly resets Strategy.context to
        # None every bar (and TradingEngine.__init__ resets it immediately
        # at construction too), so no value from a previous run/mode on the
        # same reused instance can ever leak through.
        assert shared_strategy.context is None
