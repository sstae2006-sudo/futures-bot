"""Reliability safeguards added in the Phase 1 hardening pass.

Covers: `Signal`'s own construction-time validation, the engine's fail-safe
wrapper around `strategy.on_bar`, the backtest runner's "cannot silently
fail" guards, and the new config parameter validation. Deliberately does NOT
test strategy trading logic or performance -- see test_strategies.py and
test_trend_pullback_*.py for that.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from futures_bot.backtest.data import load_bars
from futures_bot.backtest.runner import run_backtest
from futures_bot.backtest.sample_data import generate_sample_csv
from futures_bot.brokers.paper import PaperBroker
from futures_bot.config import BrokerSettings, RiskSettings, SessionSettings, Settings, load_settings
from futures_bot.contracts import CME_TZ, MES
from futures_bot.models import Bar, InvalidSignalError, Signal, SignalAction
from futures_bot.strategy.base import Strategy, StrategyRegistry

# Register every bundled strategy, including trend_pullback -- which the CLI
# does not currently wire into StrategyRegistry (a separate, already-known
# gap; see the Phase 1 report) but which must still never crash a backtest.
from futures_bot.strategy import ema_crossover, opening_range_breakout, vwap_reversion  # noqa: F401
from futures_bot.strategy.trend_pullback import strategy as trend_pullback_strategy  # noqa: F401


def make_settings(**overrides) -> Settings:
    base = dict(
        contract="MES",
        mode="paper",
        risk=RiskSettings(
            contracts_per_trade=1,
            stop_loss_points=Decimal("10"),
            take_profit_points=Decimal("20"),
            daily_max_loss=Decimal("500"),
            max_trades_per_session=50,
            account_size=Decimal("5000"),
        ),
        session=SessionSettings(start_ct="08:30", end_ct="15:00"),
        broker=BrokerSettings(starting_cash=Decimal("5000")),
    )
    base.update(overrides)
    return Settings(**base)


def make_bars(n: int, start_price: Decimal = Decimal("7500")) -> list[Bar]:
    start = datetime(2026, 7, 21, 9, 0, tzinfo=CME_TZ)
    bars = []
    price = start_price
    for i in range(n):
        price += Decimal("2")
        bars.append(
            Bar(
                timestamp=start + timedelta(minutes=i),
                open=price,
                high=price + Decimal("1"),
                low=price - Decimal("1"),
                close=price,
                volume=500,
            )
        )
    return bars


class _AlwaysHold(Strategy):
    warmup_bars = 0

    def on_bar(self, bars, position):
        return self.hold("noop")


class _RaisingStrategy(Strategy):
    """Raises on the bar where history reaches ``fail_at`` bars -- a stand-in
    for a real strategy bug (e.g. a division by zero, an index error)."""

    warmup_bars = 0

    def __init__(self, contract, fail_at: int = 2, **params):
        super().__init__(contract, **params)
        self.fail_at = fail_at

    def on_bar(self, bars, position):
        if len(bars) == self.fail_at:
            raise ZeroDivisionError("simulated strategy bug")
        return self.hold("fine")


class _NoneReturningStrategy(Strategy):
    """Forgets to return anything on one bar -- a common real bug (a bare
    ``return`` or a code path with no return statement)."""

    warmup_bars = 0

    def __init__(self, contract, fail_at: int = 2, **params):
        super().__init__(contract, **params)
        self.fail_at = fail_at

    def on_bar(self, bars, position):
        if len(bars) == self.fail_at:
            return None
        return self.hold("fine")


class _EntersOnce(Strategy):
    warmup_bars = 0

    def __init__(self, contract, **params):
        super().__init__(contract, **params)
        self.entered = False

    def on_bar(self, bars, position):
        if position is None and not self.entered:
            self.entered = True
            return self.enter_long("test entry")
        return self.hold("holding")


class TestSignalValidation:
    """`Signal.__post_init__` -- every strategy always returns a valid
    Signal because the type itself refuses to be constructed otherwise."""

    def test_valid_signal_constructs_fine(self):
        Signal(action=SignalAction.HOLD, reason="ok")

    def test_rejects_non_signalaction(self):
        with pytest.raises(InvalidSignalError):
            Signal(action="hold", reason="ok")  # plain string, not the enum

    def test_rejects_empty_reason(self):
        with pytest.raises(InvalidSignalError):
            Signal(action=SignalAction.HOLD, reason="")

    def test_rejects_blank_reason(self):
        with pytest.raises(InvalidSignalError):
            Signal(action=SignalAction.HOLD, reason="   ")

    def test_rejects_non_decimal_stop_loss(self):
        with pytest.raises(InvalidSignalError):
            Signal(action=SignalAction.ENTER_LONG, reason="x", stop_loss=99.5)  # float, not Decimal

    def test_rejects_non_decimal_take_profit(self):
        with pytest.raises(InvalidSignalError):
            Signal(action=SignalAction.ENTER_LONG, reason="x", take_profit=100)  # int, not Decimal

    def test_rejects_non_dict_metadata(self):
        with pytest.raises(InvalidSignalError):
            Signal(action=SignalAction.HOLD, reason="x", metadata="not a dict")


class TestEngineSafeguard:
    """`TradingEngine._safe_signal` -- contains a misbehaving strategy
    instead of crashing the run or corrupting state."""

    def test_strategy_exception_is_contained_and_counted(self, tmp_path):
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        metrics = run_backtest(
            settings, _RaisingStrategy(MES, fail_at=5), make_bars(10), journal_dir=tmp_path
        )
        assert metrics.strategy_errors == 1
        assert metrics.bars_processed == 10  # the run completed rather than aborting

    def test_non_signal_return_is_contained_and_counted(self, tmp_path):
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        metrics = run_backtest(
            settings, _NoneReturningStrategy(MES, fail_at=5), make_bars(10), journal_dir=tmp_path
        )
        assert metrics.strategy_errors == 1

    def test_strategy_error_is_journalled(self, tmp_path):
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        run_backtest(settings, _RaisingStrategy(MES, fail_at=3), make_bars(6), journal_dir=tmp_path)

        lines = (tmp_path / "decisions.jsonl").read_text(encoding="utf-8").splitlines()
        events = [json.loads(line) for line in lines if json.loads(line)["type"] == "event"]
        assert any(e["kind"] == "strategy_error" for e in events)

    def test_strategy_errors_surface_in_caveats(self, tmp_path):
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        metrics = run_backtest(
            settings, _RaisingStrategy(MES, fail_at=3), make_bars(6), journal_dir=tmp_path
        )
        assert any("bug" in c.lower() for c in metrics.caveats())


class TestBacktestCannotSilentlyFail:
    def test_empty_bars_rejected(self, tmp_path):
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        with pytest.raises(ValueError, match="No bars"):
            run_backtest(settings, _AlwaysHold(MES), [], journal_dir=tmp_path)

    def test_out_of_order_bars_rejected(self, tmp_path):
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        bars = make_bars(5)
        bars[2], bars[3] = bars[3], bars[2]
        with pytest.raises(ValueError, match="chronological"):
            run_backtest(settings, _AlwaysHold(MES), bars, journal_dir=tmp_path)

    def test_stuck_position_raises_instead_of_omitting_it(self, tmp_path):
        """A broker adapter whose ``flatten()`` reports success without
        actually closing the position must not be allowed to produce a
        report that quietly excludes that position's P&L."""

        class StuckPositionBroker(PaperBroker):
            def flatten(self, now, reason):
                return None  # pretends to succeed; leaves the position open

        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        broker = StuckPositionBroker(
            contract=settings.contract_spec,
            starting_cash=settings.broker.starting_cash,
            slippage_ticks=settings.broker.slippage_ticks,
            commission_per_side=settings.broker.commission_per_side,
        )

        with pytest.raises(RuntimeError, match="could not be flattened"):
            run_backtest(settings, _EntersOnce(MES), make_bars(5), journal_dir=tmp_path, broker=broker)


class TestConfigValidation:
    def test_session_window_must_be_ordered(self):
        with pytest.raises(ValueError):
            make_settings(session=SessionSettings(start_ct="15:00", end_ct="08:30"))

    def test_session_window_equal_start_end_rejected(self):
        with pytest.raises(ValueError):
            make_settings(session=SessionSettings(start_ct="09:00", end_ct="09:00"))

    def test_malformed_yaml_raises_clear_value_error(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text("risk:\n  stop_loss_points: [unterminated\n", encoding="utf-8")
        with pytest.raises(ValueError):
            load_settings(bad)

    def test_non_mapping_yaml_raises_clear_value_error(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text("- just\n- a\n- list\n", encoding="utf-8")
        with pytest.raises(ValueError):
            load_settings(bad)

    def test_account_size_starting_cash_mismatch_warns(self):
        settings = make_settings(broker=BrokerSettings(starting_cash=Decimal("1000")))
        assert any("starting_cash" in w for w in settings.risk_warnings())


class TestEveryStrategyProducesValidSignals:
    """Runs each bundled strategy through a short synthetic backtest and
    confirms it never trips the engine's invalid-signal safeguard. This does
    NOT assert anything about trading performance -- only that the
    "always return a valid Signal" contract holds under normal operation.
    """

    @pytest.mark.parametrize("name", sorted(StrategyRegistry.names()))
    def test_strategy_completes_a_backtest_cleanly(self, name, tmp_path):
        csv_path = tmp_path / "sample.csv"
        generate_sample_csv(
            csv_path, MES, start=datetime(2026, 1, 5, 17, 0, tzinfo=CME_TZ), days=10, bar_minutes=5
        )
        bars, _ = load_bars(csv_path)
        assert bars, "sample data generator produced no bars"

        settings = make_settings(
            strategy_name=name, logging={"directory": tmp_path, "level": "WARNING"}
        )
        strategy_cls = StrategyRegistry.get(name)
        metrics = run_backtest(settings, strategy_cls(MES), bars, journal_dir=tmp_path)

        assert metrics.strategy_errors == 0, f"{name} produced an invalid Signal during a clean run"
