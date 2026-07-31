from decimal import Decimal

from futures_bot.models import DEFAULT_BREAKEVEN_EPSILON, Side, classify_stop_exit


class TestClassifyStopExit:
    def test_long_losing_stop(self):
        assert classify_stop_exit(Side.LONG, Decimal("5000"), Decimal("4990")) == "stop_loss"

    def test_short_losing_stop(self):
        assert classify_stop_exit(Side.SHORT, Decimal("5000"), Decimal("5010")) == "stop_loss"

    def test_long_breakeven_stop_exact(self):
        assert classify_stop_exit(Side.LONG, Decimal("5000"), Decimal("5000")) == "breakeven_stop"

    def test_short_breakeven_stop_exact(self):
        assert classify_stop_exit(Side.SHORT, Decimal("5000"), Decimal("5000")) == "breakeven_stop"

    def test_long_breakeven_within_epsilon(self):
        exit_price = Decimal("5000") + (DEFAULT_BREAKEVEN_EPSILON / 2)
        assert classify_stop_exit(Side.LONG, Decimal("5000"), exit_price) == "breakeven_stop"

    def test_long_trailing_stop_profit(self):
        assert classify_stop_exit(Side.LONG, Decimal("5000"), Decimal("5010")) == "trailing_stop"

    def test_short_trailing_stop_profit(self):
        assert classify_stop_exit(Side.SHORT, Decimal("5000"), Decimal("4990")) == "trailing_stop"

    def test_long_just_past_epsilon_is_trailing_not_breakeven(self):
        exit_price = Decimal("5000") + (DEFAULT_BREAKEVEN_EPSILON * 2)
        assert classify_stop_exit(Side.LONG, Decimal("5000"), exit_price) == "trailing_stop"

    def test_custom_epsilon_widens_the_breakeven_band(self):
        assert classify_stop_exit(
            Side.LONG, Decimal("5000"), Decimal("5000.10"), epsilon=Decimal("0.25"),
        ) == "breakeven_stop"
