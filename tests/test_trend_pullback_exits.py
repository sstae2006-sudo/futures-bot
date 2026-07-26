"""Exit rule and broker stop-modification tests.

The broker tests matter most here: they check that a trailing/breakeven stop
can only ever ratchet toward the position, never away from it, and that a
request which rounds to the tick the stop is already at is treated as a
harmless no-op rather than logged as a rejection.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from futures_bot.brokers.base import BrokerError
from futures_bot.brokers.paper import PaperBroker
from futures_bot.contracts import CME_TZ, MES
from futures_bot.models import Bar, Position, Side
from futures_bot.strategy.trend_pullback.exits import (
    ExitConfig,
    breakeven_stop_level,
    check_ema_reversal_exit,
    check_max_bars_exit,
    check_vwap_loss_exit,
    initial_stop_and_target,
    trailing_stop_level,
)
from futures_bot.strategy.trend_pullback.rolling import IndicatorSnapshot


def snap(**overrides) -> IndicatorSnapshot:
    base = dict(
        ema9=Decimal("7500"), ema21=Decimal("7500"), ema50=Decimal("7500"),
        ema200=Decimal("7500"), vwap=Decimal("7500"), atr=Decimal("3"),
        rsi=Decimal("50"), adx=Decimal("25"), volume_sma=Decimal("1000"),
    )
    base.update(overrides)
    return IndicatorSnapshot(**base)


def long_position(entry=Decimal("7500"), stop=Decimal("7490")) -> Position:
    return Position(
        side=Side.LONG, quantity=1, entry_price=entry,
        entry_time=datetime(2026, 7, 21, 9, 0, tzinfo=CME_TZ), stop_loss=stop,
    )


def short_position(entry=Decimal("7500"), stop=Decimal("7510")) -> Position:
    return Position(
        side=Side.SHORT, quantity=1, entry_price=entry,
        entry_time=datetime(2026, 7, 21, 9, 0, tzinfo=CME_TZ), stop_loss=stop,
    )


class TestInitialStopAndTarget:
    def test_long_stop_below_target_above(self):
        stop, target = initial_stop_and_target(Side.LONG, Decimal("7500"), Decimal("4"), ExitConfig())
        assert stop < Decimal("7500") < target

    def test_short_target_below_stop_above(self):
        stop, target = initial_stop_and_target(Side.SHORT, Decimal("7500"), Decimal("4"), ExitConfig())
        assert target < Decimal("7500") < stop

    def test_scales_with_atr(self):
        tight = initial_stop_and_target(Side.LONG, Decimal("7500"), Decimal("2"), ExitConfig())
        wide = initial_stop_and_target(Side.LONG, Decimal("7500"), Decimal("8"), ExitConfig())
        assert (Decimal("7500") - tight[0]) < (Decimal("7500") - wide[0])


class TestTrailingStop:
    def test_advances_when_price_moves_favorably_long(self):
        pos = long_position(entry=Decimal("7500"), stop=Decimal("7490"))
        new_stop = trailing_stop_level(pos, Decimal("7520"), Decimal("3"), ExitConfig(trailing_atr_mult=Decimal("2")))
        assert new_stop == Decimal("7514")  # 7520 - 2*3

    def test_does_not_loosen_when_price_pulls_back(self):
        pos = long_position(entry=Decimal("7500"), stop=Decimal("7510"))
        # Price at 7505 with a wide trail would suggest a LOWER stop than
        # 7510 -- must return None rather than loosen it.
        new_stop = trailing_stop_level(pos, Decimal("7505"), Decimal("3"), ExitConfig(trailing_atr_mult=Decimal("2")))
        assert new_stop is None

    def test_mirrors_for_short(self):
        pos = short_position(entry=Decimal("7500"), stop=Decimal("7510"))
        new_stop = trailing_stop_level(pos, Decimal("7480"), Decimal("3"), ExitConfig(trailing_atr_mult=Decimal("2")))
        assert new_stop == Decimal("7486")  # 7480 + 2*3


class TestBreakevenStop:
    def test_moves_to_breakeven_after_trigger(self):
        pos = long_position(entry=Decimal("7500"), stop=Decimal("7490"))
        config = ExitConfig(breakeven_trigger_points=Decimal("8"), breakeven_buffer_points=Decimal("0.5"))
        result = breakeven_stop_level(pos, Decimal("7509"), config)  # 9 pts profit, over the 8 trigger
        assert result == Decimal("7500.5")

    def test_no_move_before_trigger(self):
        pos = long_position(entry=Decimal("7500"), stop=Decimal("7490"))
        config = ExitConfig(breakeven_trigger_points=Decimal("8"))
        assert breakeven_stop_level(pos, Decimal("7505"), config) is None

    def test_disabled_when_trigger_is_none(self):
        pos = long_position()
        config = ExitConfig(breakeven_trigger_points=None)
        assert breakeven_stop_level(pos, Decimal("7550"), config) is None

    def test_never_loosens_an_already_better_stop(self):
        pos = long_position(entry=Decimal("7500"), stop=Decimal("7505"))  # already past breakeven
        config = ExitConfig(breakeven_trigger_points=Decimal("8"))
        assert breakeven_stop_level(pos, Decimal("7509"), config) is None


class TestStrategyExits:
    def test_ema_reversal_exits_long_on_cross_down(self):
        pos = long_position()
        reason = check_ema_reversal_exit(pos, snap(ema9=Decimal("7495"), ema21=Decimal("7500")), ExitConfig())
        assert reason is not None and "reversed" in reason

    def test_ema_reversal_none_when_aligned(self):
        pos = long_position()
        reason = check_ema_reversal_exit(pos, snap(ema9=Decimal("7505"), ema21=Decimal("7500")), ExitConfig())
        assert reason is None

    def test_ema_reversal_disabled(self):
        pos = long_position()
        reason = check_ema_reversal_exit(
            pos, snap(ema9=Decimal("7495"), ema21=Decimal("7500")),
            ExitConfig(ema_reversal_enabled=False),
        )
        assert reason is None

    def test_vwap_loss_exits_long_below_vwap(self):
        pos = long_position()
        reason = check_vwap_loss_exit(pos, Decimal("7495"), snap(vwap=Decimal("7500")), ExitConfig())
        assert reason is not None and "VWAP" in reason

    def test_vwap_loss_none_when_above(self):
        pos = long_position()
        reason = check_vwap_loss_exit(pos, Decimal("7505"), snap(vwap=Decimal("7500")), ExitConfig())
        assert reason is None

    def test_max_bars_exit_fires_at_limit(self):
        assert check_max_bars_exit(24, ExitConfig(max_bars_in_trade=24)) is not None
        assert check_max_bars_exit(23, ExitConfig(max_bars_in_trade=24)) is None


class TestPaperBrokerModifyStopLoss:
    def _opened_long(self) -> PaperBroker:
        broker = PaperBroker(contract=MES, starting_cash=Decimal("5000"))
        broker.connect()
        now = datetime(2026, 7, 21, 9, 0, tzinfo=CME_TZ)
        bar = Bar(
            timestamp=now, open=Decimal("7500"), high=Decimal("7501"),
            low=Decimal("7499"), close=Decimal("7500"), volume=1000,
        )
        broker.on_bar(bar, now)
        broker.submit_bracket(Side.LONG, 1, Decimal("7490"), Decimal("7520"), now)
        return broker

    def test_tightens_long_stop_successfully(self):
        broker = self._opened_long()
        moved = broker.modify_stop_loss(Decimal("7495"))
        assert moved is True
        assert broker.get_position().stop_loss == Decimal("7495")

    def test_rejects_loosening_long_stop(self):
        broker = self._opened_long()
        broker.modify_stop_loss(Decimal("7495"))
        with pytest.raises(BrokerError, match="loosens"):
            broker.modify_stop_loss(Decimal("7492"))

    def test_same_tick_is_a_silent_noop_not_an_error(self):
        broker = self._opened_long()
        broker.modify_stop_loss(Decimal("7495"))
        moved = broker.modify_stop_loss(Decimal("7495"))  # identical level
        assert moved is False  # no-op, not an exception

    def test_rejects_stop_that_would_fill_immediately(self):
        broker = self._opened_long()
        with pytest.raises(BrokerError, match="fill immediately"):
            broker.modify_stop_loss(Decimal("7501"))  # at/above current price

    def test_rejects_when_no_position_open(self):
        broker = PaperBroker(contract=MES)
        broker.connect()
        with pytest.raises(BrokerError, match="No open position"):
            broker.modify_stop_loss(Decimal("7495"))

    def test_short_side_mirrors_long(self):
        broker = PaperBroker(contract=MES, starting_cash=Decimal("5000"))
        broker.connect()
        now = datetime(2026, 7, 21, 9, 0, tzinfo=CME_TZ)
        bar = Bar(timestamp=now, open=Decimal("7500"), high=Decimal("7501"),
                  low=Decimal("7499"), close=Decimal("7500"), volume=1000)
        broker.on_bar(bar, now)
        broker.submit_bracket(Side.SHORT, 1, Decimal("7510"), Decimal("7480"), now)

        assert broker.modify_stop_loss(Decimal("7505")) is True
        with pytest.raises(BrokerError, match="loosens"):
            broker.modify_stop_loss(Decimal("7508"))
