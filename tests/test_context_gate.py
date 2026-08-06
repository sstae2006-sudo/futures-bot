"""Tests for strategy/context_gate.py -- the ContextGatedStrategy wrapper and
its ContextScorer interface (Phase 1 of wiring the Market Context Engine into
the Strategy Engine, see docs/ARCHITECTURE.md's "Market Context Engine"
section).

Covers the scoring interface in isolation (ContextScore validation, the
reject_if/require_if/all_of/any_of scorer library), the wrapper's delegation
and veto behavior against a controllable inner strategy (no engine involved),
and an end-to-end proof that the wrapper works unmodified through both
TradingEngine (ContextMode.ENABLED) and the existing
backtest.context_comparison A/B framework -- the "extends, doesn't
duplicate, existing systems" requirement this phase was scoped around.
"""

from __future__ import annotations

import dataclasses
import random
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional, Sequence

import pytest

from futures_bot.backtest.context_comparison import compare_context_impact
from futures_bot.backtest.runner import run_backtest
from futures_bot.config import BrokerSettings, RiskSettings, Settings
from futures_bot.context import unknown_context
from futures_bot.contracts import CME_TZ, MES
from futures_bot.engine import ContextMode
from futures_bot.models import Bar, Position, Signal, SignalAction
from futures_bot.strategy.base import Strategy
from futures_bot.strategy.context_gate import (
    ContextGatedStrategy,
    ContextScore,
    all_of,
    any_of,
    reject_if,
    require_if,
)


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


def make_context(**overrides):
    ts = overrides.pop("timestamp", datetime(2026, 7, 21, 9, 5, tzinfo=CME_TZ))
    ctx = unknown_context(ts, "MES", "1min")
    if overrides:
        ctx = dataclasses.replace(ctx, **overrides)
    return ctx


class _FixedSignalStrategy(Strategy):
    """Returns whatever Signal it's constructed with, every call. Tracks
    on_start/on_stop invocations so ContextGatedStrategy's delegation can be
    asserted directly."""

    warmup_bars = 3

    def __init__(self, contract, signal: Signal, **params):
        super().__init__(contract, **params)
        self._signal = signal
        self.started = False
        self.stopped = False

    def on_bar(self, bars: Sequence[Bar], position: Optional[Position]) -> Signal:
        return self._signal

    def on_start(self) -> None:
        self.started = True

    def on_stop(self) -> None:
        self.stopped = True


class _SimpleMomentum(Strategy):
    """Enters long on an up-tick, holds one position at a time -- a stand-in
    for "any existing, unmodified bundled strategy" for the engine-level
    tests below (same pattern tests/test_engine_context_integration.py and
    tests/test_backtest_context_comparison.py already use)."""

    warmup_bars = 5

    def on_bar(self, bars: Sequence[Bar], position: Optional[Position]) -> Signal:
        if len(bars) < 6:
            return self.hold("warmup")
        if position is None:
            if bars[-1].close > bars[-2].close:
                return self.enter_long("uptick")
            return self.hold("no signal")
        return self.hold("holding")


class TestContextScore:
    def test_value_above_range_raises(self):
        with pytest.raises(ValueError):
            ContextScore(1.5, "too high")

    def test_value_below_range_raises(self):
        with pytest.raises(ValueError):
            ContextScore(-0.1, "too low")

    def test_empty_reason_raises(self):
        with pytest.raises(ValueError):
            ContextScore(1.0, "   ")

    def test_boundary_values_are_valid(self):
        ContextScore(0.0, "veto")
        ContextScore(1.0, "pass")


class TestScorerLibrary:
    def test_reject_if_vetoes_when_predicate_true(self):
        scorer = reject_if(lambda ctx: ctx.symbol == "MES", "wrong symbol")
        signal = Signal(action=SignalAction.ENTER_LONG, reason="x")
        score = scorer(signal, make_context())
        assert score.value == 0.0
        assert score.reason == "wrong symbol"

    def test_reject_if_passes_when_predicate_false(self):
        scorer = reject_if(lambda ctx: ctx.symbol == "NOT_MES", "wrong symbol")
        score = scorer(Signal(action=SignalAction.ENTER_LONG, reason="x"), make_context())
        assert score.value == 1.0

    def test_require_if_passes_when_predicate_true(self):
        scorer = require_if(lambda ctx: ctx.symbol == "MES", "needs MES")
        score = scorer(Signal(action=SignalAction.ENTER_LONG, reason="x"), make_context())
        assert score.value == 1.0

    def test_require_if_vetoes_when_predicate_false(self):
        scorer = require_if(lambda ctx: ctx.symbol == "NOT_MES", "needs NOT_MES")
        score = scorer(Signal(action=SignalAction.ENTER_LONG, reason="x"), make_context())
        assert score.value == 0.0
        assert score.reason == "needs NOT_MES"

    def test_all_of_is_vetoed_by_a_single_failing_scorer(self):
        combined = all_of(reject_if(lambda ctx: False, "never"), reject_if(lambda ctx: True, "always vetoes"))
        score = combined(Signal(action=SignalAction.ENTER_LONG, reason="x"), make_context())
        assert score.value == 0.0
        assert score.reason == "always vetoes"

    def test_all_of_passes_when_every_scorer_passes(self):
        combined = all_of(reject_if(lambda ctx: False, "a"), reject_if(lambda ctx: False, "b"))
        score = combined(Signal(action=SignalAction.ENTER_LONG, reason="x"), make_context())
        assert score.value == 1.0

    def test_any_of_passes_when_a_single_scorer_passes(self):
        combined = any_of(reject_if(lambda ctx: True, "vetoes"), reject_if(lambda ctx: False, "passes"))
        score = combined(Signal(action=SignalAction.ENTER_LONG, reason="x"), make_context())
        assert score.value == 1.0

    def test_any_of_vetoes_when_every_scorer_vetoes(self):
        combined = any_of(reject_if(lambda ctx: True, "a"), reject_if(lambda ctx: True, "b"))
        score = combined(Signal(action=SignalAction.ENTER_LONG, reason="x"), make_context())
        assert score.value == 0.0


class TestContextGatedStrategyDelegation:
    def test_warmup_bars_delegates_from_inner(self):
        inner = _FixedSignalStrategy(MES, Signal(action=SignalAction.HOLD, reason="x"))
        wrapper = ContextGatedStrategy(MES, inner=inner, scorer=reject_if(lambda c: False, "n/a"))
        assert wrapper.warmup_bars == inner.warmup_bars == 3

    def test_on_start_and_on_stop_delegate_to_inner(self):
        inner = _FixedSignalStrategy(MES, Signal(action=SignalAction.HOLD, reason="x"))
        wrapper = ContextGatedStrategy(MES, inner=inner, scorer=reject_if(lambda c: False, "n/a"))
        assert not inner.started and not inner.stopped
        wrapper.on_start()
        assert inner.started
        wrapper.on_stop()
        assert inner.stopped

    def test_hold_signal_passes_through_and_scorer_is_never_called(self):
        inner = _FixedSignalStrategy(MES, Signal(action=SignalAction.HOLD, reason="waiting"))
        calls: list[int] = []

        def spy_scorer(signal, context):
            calls.append(1)
            return ContextScore(1.0, "should not be called")

        wrapper = ContextGatedStrategy(MES, inner=inner, scorer=spy_scorer)
        wrapper.context = make_context()
        result = wrapper.on_bar([], None)
        assert result.action is SignalAction.HOLD
        assert result.reason == "waiting"
        assert calls == []
        assert wrapper.last_score is None

    def test_exit_signal_passes_through_and_scorer_is_never_called(self):
        inner = _FixedSignalStrategy(MES, Signal(action=SignalAction.EXIT, reason="done"))
        calls: list[int] = []

        def spy_scorer(signal, context):
            calls.append(1)
            return ContextScore(1.0, "should not be called")

        wrapper = ContextGatedStrategy(MES, inner=inner, scorer=spy_scorer)
        wrapper.context = make_context()
        result = wrapper.on_bar([], None)
        assert result.action is SignalAction.EXIT
        assert calls == []

    def test_hold_carrying_stop_loss_passes_through_unchanged(self):
        """A stop-management HOLD (e.g. trend_pullback's trailing-stop
        requests) is not an entry signal -- must never be scored or altered."""
        stop_move = Signal(action=SignalAction.HOLD, reason="trailing stop", stop_loss=Decimal("7490"))
        inner = _FixedSignalStrategy(MES, stop_move)
        wrapper = ContextGatedStrategy(MES, inner=inner, scorer=reject_if(lambda c: True, "would veto"))
        wrapper.context = make_context()
        result = wrapper.on_bar([], None)
        assert result is stop_move


class TestContextGatedStrategyVeto:
    def test_entry_passes_through_unchanged_when_score_meets_threshold(self):
        entry = Signal(action=SignalAction.ENTER_LONG, reason="breakout")
        inner = _FixedSignalStrategy(MES, entry)
        wrapper = ContextGatedStrategy(
            MES, inner=inner, scorer=reject_if(lambda c: False, "never"), threshold=1.0,
        )
        wrapper.context = make_context()
        result = wrapper.on_bar([], None)
        assert result is entry
        assert wrapper.last_score == ContextScore(1.0, "reject_if condition not met")

    def test_entry_is_vetoed_into_a_hold_when_score_below_threshold(self):
        entry = Signal(action=SignalAction.ENTER_LONG, reason="breakout")
        inner = _FixedSignalStrategy(MES, entry)
        wrapper = ContextGatedStrategy(
            MES, inner=inner, scorer=reject_if(lambda c: True, "risk too high"), threshold=1.0,
        )
        wrapper.context = make_context()
        result = wrapper.on_bar([], None)
        assert result.action is SignalAction.HOLD
        assert "risk too high" in result.reason
        assert "[context-gate]" in result.reason
        assert wrapper.last_score is not None
        assert wrapper.last_score.value == 0.0

    def test_entry_passes_through_unchanged_when_context_is_none(self):
        """ENABLED mode but context generation failed this bar, or the
        wrapper is being exercised outside ContextMode.ENABLED entirely --
        either way, no context to score against means no veto, not a
        fail-closed guess."""
        entry = Signal(action=SignalAction.ENTER_LONG, reason="breakout")
        inner = _FixedSignalStrategy(MES, entry)
        calls: list[int] = []

        def spy_scorer(signal, context):
            calls.append(1)
            return ContextScore(0.0, "would veto")

        wrapper = ContextGatedStrategy(MES, inner=inner, scorer=spy_scorer)
        wrapper.context = None
        result = wrapper.on_bar([], None)
        assert result is entry
        assert calls == []
        assert wrapper.last_score is None

    def test_last_score_resets_between_bars(self):
        entry = Signal(action=SignalAction.ENTER_LONG, reason="breakout")
        inner = _FixedSignalStrategy(MES, entry)
        wrapper = ContextGatedStrategy(MES, inner=inner, scorer=reject_if(lambda c: False, "never"))
        wrapper.context = make_context()
        wrapper.on_bar([], None)
        assert wrapper.last_score is not None
        wrapper.context = None  # simulate the engine clearing context outside ENABLED mode
        wrapper.on_bar([], None)
        assert wrapper.last_score is None

    def test_threshold_out_of_range_raises(self):
        inner = _FixedSignalStrategy(MES, Signal(action=SignalAction.HOLD, reason="x"))
        with pytest.raises(ValueError):
            ContextGatedStrategy(MES, inner=inner, scorer=reject_if(lambda c: False, "n/a"), threshold=1.5)


class TestContextGatedStrategyThroughEngine:
    """Proves the wrapper works unmodified through the real TradingEngine --
    not just the unit-level on_bar calls above."""

    def test_off_mode_behaves_like_the_unwrapped_inner_strategy(self, tmp_path):
        """ContextMode.OFF never sets Strategy.context (engine.py's own
        guarantee) -- so even though this wrapper declares
        uses_context=True, it never sees a context and therefore never
        vetoes anything, regardless of the scorer."""
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        bars = make_bars(80)

        baseline = run_backtest(settings, _SimpleMomentum(MES), bars, journal_dir=tmp_path)

        wrapped = ContextGatedStrategy(
            MES, inner=_SimpleMomentum(MES),
            scorer=reject_if(lambda c: True, "always vetoes if ever consulted"),
        )
        gated = run_backtest(settings, wrapped, bars, journal_dir=tmp_path, context_mode=ContextMode.OFF)
        assert gated.trade_count == baseline.trade_count

    def test_enabled_mode_actually_vetoes_entries(self, tmp_path):
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        bars = make_bars(80)

        baseline = run_backtest(
            settings, _SimpleMomentum(MES), bars, journal_dir=tmp_path, context_mode=ContextMode.ENABLED,
        )
        assert baseline.trade_count > 0  # sanity: the unwrapped strategy trades at all

        wrapped = ContextGatedStrategy(
            MES, inner=_SimpleMomentum(MES), scorer=reject_if(lambda c: True, "always vetoes"),
        )
        gated = run_backtest(
            settings, wrapped, bars, journal_dir=tmp_path, context_mode=ContextMode.ENABLED,
        )
        assert gated.trade_count == 0

    def test_enabled_mode_with_a_never_vetoing_scorer_matches_baseline(self, tmp_path):
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        bars = make_bars(80)

        baseline = run_backtest(
            settings, _SimpleMomentum(MES), bars, journal_dir=tmp_path, context_mode=ContextMode.ENABLED,
        )
        wrapped = ContextGatedStrategy(
            MES, inner=_SimpleMomentum(MES), scorer=reject_if(lambda c: False, "never vetoes"),
        )
        gated = run_backtest(
            settings, wrapped, bars, journal_dir=tmp_path, context_mode=ContextMode.ENABLED,
        )
        assert gated.trade_count == baseline.trade_count


class TestContextGatedStrategyThroughComparisonFramework:
    """Plugs directly into the existing OFF-vs-ENABLED A/B comparison tool
    (backtest/context_comparison.py) with no modification to that module --
    the "extends existing systems, doesn't duplicate them" requirement this
    phase was scoped around."""

    def test_compare_context_impact_accepts_a_context_gated_factory(self, tmp_path):
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        bars = make_bars(80)

        def strategy_factory():
            return ContextGatedStrategy(
                MES, inner=_SimpleMomentum(MES), scorer=reject_if(lambda c: True, "always vetoes"),
            )

        result = compare_context_impact(settings, strategy_factory, bars, journal_dir=tmp_path)
        assert result.with_context.total_trades == 0
        assert result.baseline.total_trades > 0
        assert result.trades_changed > 0
