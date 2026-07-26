"""Phase 9: `TradingEngine`'s optional `signal_filter` hook (and
`run_backtest`/`build_engine`'s threading of it through). `None` (the
default) must behave identically to before Phase 9 existed; a filter must
only ever see entry signals, never HOLD/EXIT."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional, Sequence

import pytest

from futures_bot.backtest.runner import run_backtest
from futures_bot.config import BrokerSettings, RiskSettings, Settings
from futures_bot.contracts import CME_TZ, MES
from futures_bot.models import Bar, Position, Signal, SignalAction
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


def make_bars(n: int) -> list[Bar]:
    start = datetime(2026, 7, 21, 9, 0, tzinfo=CME_TZ)
    price = Decimal("7500")
    bars = []
    for i in range(n):
        price += Decimal("2")
        bars.append(Bar(timestamp=start + timedelta(minutes=i), open=price, high=price + 1, low=price - 1, close=price, volume=500))
    return bars


class _EntersOnceThenHolds(Strategy):
    warmup_bars = 0

    def __init__(self, contract, **params):
        super().__init__(contract, **params)
        self.entered = False

    def on_bar(self, bars: Sequence[Bar], position: Optional[Position]) -> Signal:
        if position is None and not self.entered:
            self.entered = True
            return self.enter_long("test entry", win_probability=0.9)
        return self.hold("holding" if position else "done")


class _HoldsForever(Strategy):
    warmup_bars = 0

    def on_bar(self, bars: Sequence[Bar], position: Optional[Position]) -> Signal:
        return self.hold("never enters")


class TestNoBehaviorChangeWhenFilterIsNone:
    def test_omitted_signal_filter_matches_explicit_none(self, tmp_path):
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        bars = make_bars(20)
        omitted = run_backtest(settings, _EntersOnceThenHolds(MES), bars, journal_dir=tmp_path)
        explicit_none = run_backtest(settings, _EntersOnceThenHolds(MES), bars, journal_dir=tmp_path, signal_filter=None)
        assert omitted.trade_count == explicit_none.trade_count == 1


class TestFilterAppliesOnlyToEntrySignals:
    def test_filter_rejecting_the_entry_prevents_the_trade(self, tmp_path):
        def always_reject(signal: Signal) -> Signal:
            return Signal(action=SignalAction.HOLD, reason="AI filtered: rejected for test")

        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        metrics = run_backtest(
            settings, _EntersOnceThenHolds(MES), make_bars(20), journal_dir=tmp_path, signal_filter=always_reject,
        )
        assert metrics.trade_count == 0

    def test_filter_approving_the_entry_still_takes_the_trade(self, tmp_path):
        def always_approve(signal: Signal) -> Signal:
            return signal

        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        metrics = run_backtest(
            settings, _EntersOnceThenHolds(MES), make_bars(20), journal_dir=tmp_path, signal_filter=always_approve,
        )
        assert metrics.trade_count == 1

    def test_filter_is_never_called_for_a_strategy_that_only_holds(self, tmp_path):
        calls = []

        def spy_filter(signal: Signal) -> Signal:
            calls.append(signal)
            return signal

        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        run_backtest(settings, _HoldsForever(MES), make_bars(20), journal_dir=tmp_path, signal_filter=spy_filter)
        assert calls == []

    def test_filter_receives_the_signals_own_metadata(self, tmp_path):
        seen_metadata = {}

        def capture_filter(signal: Signal) -> Signal:
            seen_metadata.update(signal.metadata)
            return signal

        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        run_backtest(settings, _EntersOnceThenHolds(MES), make_bars(20), journal_dir=tmp_path, signal_filter=capture_filter)
        assert seen_metadata == {"win_probability": 0.9}
