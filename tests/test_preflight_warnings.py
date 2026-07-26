"""Phase 4 Part 3: smart config/data warnings.

`Settings.strategy_warnings()` catches config-only contradictions (no bars
needed); `research.preflight.strategy_data_warnings()` catches mismatches
between the strategy and the actual loaded data. Both are advisory --
nothing here should ever block a run, only `config.py`'s hard validation
errors do that.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from futures_bot.config import BrokerSettings, RiskSettings, Settings
from futures_bot.contracts import CME_TZ
from futures_bot.models import Bar
from futures_bot.research.preflight import bar_interval_minutes, strategy_data_warnings

# Registers every bundled strategy so `strategy_data_warnings`'s generic
# warmup check (which looks the name up in `StrategyRegistry`) can find them.
from futures_bot.strategy import ema_crossover, opening_range_breakout, vwap_reversion  # noqa: F401
from futures_bot.strategy.trend_pullback import strategy as trend_pullback_strategy  # noqa: F401


def make_settings(**overrides) -> Settings:
    base = dict(
        contract="MES",
        mode="paper",
        risk=RiskSettings(
            contracts_per_trade=1, stop_loss_points=Decimal("10"), take_profit_points=Decimal("20"),
            daily_max_loss=Decimal("500"), max_trades_per_session=20, account_size=Decimal("5000"),
        ),
        broker=BrokerSettings(starting_cash=Decimal("5000")),
    )
    base.update(overrides)
    return Settings(**base)


def make_bars(n: int, interval_minutes: int = 5, start=None) -> list[Bar]:
    start = start or datetime(2026, 1, 5, 8, 30, tzinfo=CME_TZ)
    price = Decimal("7500")
    return [
        Bar(
            timestamp=start + timedelta(minutes=interval_minutes * i),
            open=price, high=price + 1, low=price - 1, close=price, volume=500,
        )
        for i in range(n)
    ]


class TestStrategyWarningsNoDataNeeded:
    def test_ema_fast_not_below_slow_warns(self):
        s = make_settings(
            strategy_name="ema_crossover",
            strategy_params={"fast_period": 30, "slow_period": 10},
        )
        assert any("fast_period" in w for w in s.strategy_warnings())

    def test_ema_normal_params_no_warning(self):
        s = make_settings(strategy_name="ema_crossover", strategy_params={"fast_period": 8, "slow_period": 34})
        assert s.strategy_warnings() == []

    def test_ema_swept_params_are_skipped_not_crashed(self):
        s = make_settings(
            strategy_name="ema_crossover",
            strategy_params={"fast_period": [5, 9], "slow_period": [21, 30]},
        )
        assert s.strategy_warnings() == []  # can't judge a sweep pointwise; must not raise

    def test_orb_min_range_above_max_range_warns(self):
        s = make_settings(
            strategy_name="opening_range_breakout",
            strategy_params={"min_range_points": 40, "max_range_points": 2},
        )
        warnings = s.strategy_warnings()
        assert any("never take a trade" in w for w in warnings)

    def test_orb_latest_entry_before_earliest_warns(self):
        s = make_settings(
            strategy_name="opening_range_breakout",
            strategy_params={"earliest_entry_ct": "11:00", "latest_entry_ct": "10:00"},
        )
        assert any("entry window never opens" in w for w in s.strategy_warnings())

    def test_orb_entry_window_closes_before_range_completes_warns(self):
        s = make_settings(
            strategy_name="opening_range_breakout",
            strategy_params={
                "session_start_ct": "08:30", "range_minutes": 30,
                "earliest_entry_ct": "08:30", "latest_entry_ct": "08:45",
            },
        )
        assert any("closes before there is ever a completed range" in w for w in s.strategy_warnings())

    def test_orb_sane_config_no_warning(self):
        s = make_settings(
            strategy_name="opening_range_breakout",
            strategy_params={
                "session_start_ct": "08:30", "range_minutes": 30,
                "earliest_entry_ct": "09:00", "latest_entry_ct": "11:00",
                "min_range_points": 2, "max_range_points": 40,
            },
        )
        assert s.strategy_warnings() == []

    def test_trend_pullback_inverted_session_window_warns(self):
        s = make_settings(
            strategy_name="trend_pullback",
            strategy_params={"trading_sessions": [["10:30", "08:30"]]},
        )
        assert any("never allow an entry" in w for w in s.strategy_warnings())

    def test_trend_pullback_overlapping_rsi_filters_warns(self):
        s = make_settings(
            strategy_name="trend_pullback",
            strategy_params={"rsi_long_min": 40, "rsi_short_max": 60},
        )
        assert any("rsi_long_min" in w for w in s.strategy_warnings())

    def test_unknown_strategy_name_does_not_raise(self):
        s = make_settings(strategy_name="not_a_real_strategy", strategy_params={})
        assert s.strategy_warnings() == []


class TestBarIntervalMinutes:
    def test_detects_five_minute_bars(self):
        bars = make_bars(30, interval_minutes=5)
        assert bar_interval_minutes(bars) == Decimal("5")

    def test_detects_hourly_bars(self):
        bars = make_bars(30, interval_minutes=60)
        assert bar_interval_minutes(bars) == Decimal("60")

    def test_ignores_overnight_gaps(self):
        day1 = make_bars(10, interval_minutes=5, start=datetime(2026, 1, 5, 8, 30, tzinfo=CME_TZ))
        day2 = make_bars(10, interval_minutes=5, start=datetime(2026, 1, 6, 8, 30, tzinfo=CME_TZ))
        assert bar_interval_minutes(day1 + day2) == Decimal("5")

    def test_none_below_two_bars(self):
        assert bar_interval_minutes(make_bars(1)) is None


class TestStrategyDataWarnings:
    def test_no_warnings_for_empty_bars(self):
        s = make_settings(strategy_name="ema_crossover", strategy_params={})
        assert strategy_data_warnings(s, []) == []

    def test_hourly_bars_flagged_for_five_minute_strategy(self):
        s = make_settings(strategy_name="opening_range_breakout", strategy_params={"trend_period": 5})
        bars = make_bars(50, interval_minutes=60)
        warnings = strategy_data_warnings(s, bars)
        assert any("designed around" in w for w in warnings)

    def test_five_minute_bars_not_flagged_for_five_minute_strategy(self):
        s = make_settings(strategy_name="opening_range_breakout", strategy_params={"trend_period": 5})
        bars = make_bars(50, interval_minutes=5)
        warnings = strategy_data_warnings(s, bars)
        assert not any("designed around" in w for w in warnings)

    def test_orb_range_window_narrower_than_bar_interval_warns(self):
        s = make_settings(
            strategy_name="opening_range_breakout",
            strategy_params={"range_minutes": 30, "trend_period": 5},
        )
        bars = make_bars(50, interval_minutes=60)
        warnings = strategy_data_warnings(s, bars)
        assert any("built from at most one bar" in w for w in warnings)

    def test_vwap_min_bars_exceeds_session_capacity_warns(self):
        s = make_settings(strategy_name="vwap_reversion", strategy_params={"min_bars": 100})
        bars = make_bars(50, interval_minutes=60)  # ~7 bars/session at this resolution
        warnings = strategy_data_warnings(s, bars)
        assert any("likely to produce zero trades" in w for w in warnings)

    def test_not_enough_bars_for_warmup_warns(self):
        s = make_settings(strategy_name="ema_crossover", strategy_params={"trend_period": 200})
        bars = make_bars(10, interval_minutes=5)
        warnings = strategy_data_warnings(s, bars)
        assert any("needs 205 to clear warmup" in w for w in warnings)

    def test_plenty_of_bars_for_warmup_no_warning(self):
        s = make_settings(strategy_name="ema_crossover", strategy_params={"trend_period": 5})
        bars = make_bars(500, interval_minutes=5)
        warnings = strategy_data_warnings(s, bars)
        assert not any("clear warmup" in w for w in warnings)

    def test_unknown_strategy_name_does_not_raise(self):
        s = make_settings(strategy_name="not_a_real_strategy", strategy_params={})
        bars = make_bars(50, interval_minutes=5)
        strategy_data_warnings(s, bars)  # must not raise
