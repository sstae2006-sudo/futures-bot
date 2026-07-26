"""Correctness audit: PaperBroker fill pricing. No dedicated test file for
this broker existed before -- these lock down slippage on every exit path,
including stop-loss/take-profit (the fix), so this doesn't regress silently
again."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from futures_bot.brokers.paper import PaperBroker
from futures_bot.contracts import MES
from futures_bot.models import Bar, Side

START = datetime(2026, 7, 21, 9, 0, tzinfo=ZoneInfo("America/Chicago"))


def bar(o, h, lo, c, minute=0):
    ts = START + timedelta(minutes=minute)
    return ts, Bar(timestamp=ts, open=Decimal(str(o)), high=Decimal(str(h)), low=Decimal(str(lo)), close=Decimal(str(c)), volume=100)


def make_broker(slippage_ticks=Decimal("1")):
    b = PaperBroker(contract=MES, starting_cash=Decimal("2500"), slippage_ticks=slippage_ticks, commission_per_side=Decimal("0.62"))
    b.connect()
    return b


class TestEntrySlippage:
    def test_long_entry_fills_above_signal_price(self):
        b = make_broker()
        ts, b0 = bar(5000, 5001, 4999, 5000)
        b.on_bar(b0, ts)
        order = b.submit_bracket(Side.LONG, 1, stop_loss=Decimal("4990"), take_profit=Decimal("5010"), now=ts)
        assert b.get_position().entry_price == Decimal("5000.25")  # +1 tick, adverse for a long
        assert order.status.value == "filled"

    def test_short_entry_fills_below_signal_price(self):
        b = make_broker()
        ts, b0 = bar(5000, 5001, 4999, 5000)
        b.on_bar(b0, ts)
        b.submit_bracket(Side.SHORT, 1, stop_loss=Decimal("5010"), take_profit=Decimal("4990"), now=ts)
        assert b.get_position().entry_price == Decimal("4999.75")  # -1 tick, adverse for a short


class TestStopAndTargetSlippage:
    """Regression test for the audit finding: on_bar() used to close
    stop/target hits at the exact protective level with zero slippage,
    contradicting the module's own "every market fill is adverse" design
    and overstating P&L on the two most common exit paths."""

    def test_long_stop_loss_fills_below_the_stop_level(self):
        b = make_broker()
        ts0, b0 = bar(5000, 5001, 4999, 5000)
        b.on_bar(b0, ts0)
        b.submit_bracket(Side.LONG, 1, stop_loss=Decimal("4990"), take_profit=Decimal("5020"), now=ts0)

        ts1, b1 = bar(4995, 4996, 4988, 4992, minute=5)
        trade = b.on_bar(b1, ts1)

        assert trade is not None
        assert trade.exit_reason == "stop_loss"
        assert trade.exit_price == Decimal("4989.75")  # 1 tick worse than the 4990 stop, adverse to a long

    def test_short_stop_loss_fills_above_the_stop_level(self):
        b = make_broker()
        ts0, b0 = bar(5000, 5001, 4999, 5000)
        b.on_bar(b0, ts0)
        b.submit_bracket(Side.SHORT, 1, stop_loss=Decimal("5010"), take_profit=Decimal("4980"), now=ts0)

        ts1, b1 = bar(5005, 5012, 5004, 5008, minute=5)
        trade = b.on_bar(b1, ts1)

        assert trade is not None
        assert trade.exit_reason == "stop_loss"
        assert trade.exit_price == Decimal("5010.25")  # 1 tick worse than the 5010 stop, adverse to a short

    def test_long_take_profit_fills_below_the_target_level(self):
        b = make_broker()
        ts0, b0 = bar(5000, 5001, 4999, 5000)
        b.on_bar(b0, ts0)
        b.submit_bracket(Side.LONG, 1, stop_loss=Decimal("4990"), take_profit=Decimal("5010"), now=ts0)

        ts1, b1 = bar(5008, 5012, 5007, 5009, minute=5)
        trade = b.on_bar(b1, ts1)

        assert trade is not None
        assert trade.exit_reason == "take_profit"
        assert trade.exit_price == Decimal("5009.75")  # 1 tick worse than the 5010 target, adverse to a long

    def test_short_take_profit_fills_above_the_target_level(self):
        b = make_broker()
        ts0, b0 = bar(5000, 5001, 4999, 5000)
        b.on_bar(b0, ts0)
        b.submit_bracket(Side.SHORT, 1, stop_loss=Decimal("5010"), take_profit=Decimal("4990"), now=ts0)

        ts1, b1 = bar(4985, 4991, 4988, 4989, minute=5)
        trade = b.on_bar(b1, ts1)

        assert trade is not None
        assert trade.exit_reason == "take_profit"
        assert trade.exit_price == Decimal("4990.25")  # 1 tick worse than the 4990 target, adverse to a short

    def test_ambiguous_bar_resolves_against_position_and_still_applies_slippage(self):
        b = make_broker()
        ts0, b0 = bar(5000, 5001, 4999, 5000)
        b.on_bar(b0, ts0)
        b.submit_bracket(Side.LONG, 1, stop_loss=Decimal("4990"), take_profit=Decimal("5010"), now=ts0)

        # Both stop (4990) and target (5010) are inside this bar's range.
        ts1, b1 = bar(5000, 5015, 4985, 5002, minute=5)
        trade = b.on_bar(b1, ts1)

        assert trade is not None
        assert trade.exit_reason == "stop_loss (ambiguous bar, resolved against)"
        assert trade.exit_price == Decimal("4989.75")
        assert b.ambiguous_bars == 1

    def test_flatten_still_applies_slippage_unchanged(self):
        """Not part of the bug -- flatten() already sliped; confirms the
        fix didn't touch this path."""
        b = make_broker()
        ts0, b0 = bar(5000, 5001, 4999, 5000)
        b.on_bar(b0, ts0)
        b.submit_bracket(Side.LONG, 1, stop_loss=Decimal("4990"), take_profit=Decimal("5020"), now=ts0)
        ts1, b1 = bar(5001, 5002, 5000, 5001, minute=5)
        b.on_bar(b1, ts1)

        trade = b.flatten(ts1, "manual flatten")

        assert trade.exit_price == Decimal("5000.75")  # 1 tick below the 5001 close, adverse to a long


class TestPositionState:
    def test_cannot_submit_a_second_bracket_while_one_is_open(self):
        b = make_broker()
        ts0, b0 = bar(5000, 5001, 4999, 5000)
        b.on_bar(b0, ts0)
        b.submit_bracket(Side.LONG, 1, stop_loss=Decimal("4990"), take_profit=Decimal("5010"), now=ts0)

        with pytest.raises(Exception):
            b.submit_bracket(Side.LONG, 1, stop_loss=Decimal("4990"), take_profit=Decimal("5010"), now=ts0)

    def test_position_is_cleared_after_a_stop_fill(self):
        b = make_broker()
        ts0, b0 = bar(5000, 5001, 4999, 5000)
        b.on_bar(b0, ts0)
        b.submit_bracket(Side.LONG, 1, stop_loss=Decimal("4990"), take_profit=Decimal("5020"), now=ts0)
        ts1, b1 = bar(4995, 4996, 4988, 4992, minute=5)
        b.on_bar(b1, ts1)
        assert b.get_position() is None
