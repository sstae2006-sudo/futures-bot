"""Filter and pullback state machine tests.

These construct an IndicatorSnapshot directly rather than feeding real bars
through RollingIndicators -- the rolling engine has its own dedicated
cross-check tests, so here the goal is to verify the filter/state-machine
*logic* in isolation, with values chosen to make each condition's pass/fail
boundary explicit.
"""

from __future__ import annotations

from datetime import datetime, time
from decimal import Decimal

from futures_bot.contracts import CME_TZ
from futures_bot.models import Bar, Side
from futures_bot.strategy.trend_pullback.filters import (
    FilterConfig,
    SessionWindow,
    in_trading_session,
    long_filters_pass,
    short_filters_pass,
)
from futures_bot.strategy.trend_pullback.pullback import PullbackConfig, PullbackTracker
from futures_bot.strategy.trend_pullback.rolling import IndicatorSnapshot


def ct(hh, mm=0, day=21):
    return datetime(2026, 7, day, hh, mm, tzinfo=CME_TZ)


def bar(t, o, h, low, c, v=1000):
    return Bar(
        timestamp=t, open=Decimal(str(o)), high=Decimal(str(h)),
        low=Decimal(str(low)), close=Decimal(str(c)), volume=v,
    )


def bullish_snapshot(**overrides) -> IndicatorSnapshot:
    """A snapshot where every long filter passes, so tests can flip one
    field at a time to check that specific condition is actually enforced."""
    base = dict(
        ema9=Decimal("7502"), ema21=Decimal("7498"), ema50=Decimal("7490"),
        ema200=Decimal("7470"), vwap=Decimal("7495"), atr=Decimal("3"),
        rsi=Decimal("60"), adx=Decimal("25"), volume_sma=Decimal("1000"),
    )
    base.update(overrides)
    return IndicatorSnapshot(**base)


def bearish_snapshot(**overrides) -> IndicatorSnapshot:
    base = dict(
        ema9=Decimal("7498"), ema21=Decimal("7502"), ema50=Decimal("7510"),
        ema200=Decimal("7530"), vwap=Decimal("7505"), atr=Decimal("3"),
        rsi=Decimal("40"), adx=Decimal("25"), volume_sma=Decimal("1000"),
    )
    base.update(overrides)
    return IndicatorSnapshot(**base)


class TestSessionWindow:
    def test_contains_inside_window(self):
        w = SessionWindow(time(8, 30), time(10, 30))
        assert w.contains(time(9, 0))
        assert w.contains(time(8, 30))
        assert w.contains(time(10, 30))

    def test_excludes_outside_window(self):
        w = SessionWindow(time(8, 30), time(10, 30))
        assert not w.contains(time(8, 29))
        assert not w.contains(time(10, 31))

    def test_in_trading_session_checks_all_windows(self):
        config = FilterConfig(sessions=(SessionWindow(time(8, 30), time(10, 30)),
                                         SessionWindow(time(13, 30), time(15, 0))))
        b1 = bar(ct(9, 0), 7500, 7501, 7499, 7500)
        b2 = bar(ct(14, 0), 7500, 7501, 7499, 7500)
        b3 = bar(ct(12, 0), 7500, 7501, 7499, 7500)
        assert in_trading_session(b1, config)
        assert in_trading_session(b2, config)
        assert not in_trading_session(b3, config)


class TestLongFilters:
    def test_all_pass_returns_none(self):
        b = bar(ct(9, 0), 7503, 7505, 7501, 7503, v=1300)
        assert long_filters_pass(b, bullish_snapshot(), FilterConfig()) is None

    def test_fails_when_not_ready(self):
        b = bar(ct(9, 0), 7503, 7505, 7501, 7503, v=1300)
        snap = IndicatorSnapshot(None, None, None, None, None, None, None, None, None)
        assert "warming up" in long_filters_pass(b, snap, FilterConfig())

    def test_fails_when_ema50_below_ema200(self):
        b = bar(ct(9, 0), 7503, 7505, 7501, 7503, v=1300)
        snap = bullish_snapshot(ema50=Decimal("7460"))  # now below ema200=7470
        reason = long_filters_pass(b, snap, FilterConfig())
        assert reason is not None and "uptrend" in reason

    def test_fails_when_price_below_ema200(self):
        b = bar(ct(9, 0), 7460, 7462, 7458, 7460, v=1300)  # close below ema200=7470
        reason = long_filters_pass(b, bullish_snapshot(), FilterConfig())
        assert reason is not None and "EMA200" in reason

    def test_fails_when_price_below_vwap(self):
        b = bar(ct(9, 0), 7490, 7492, 7488, 7490, v=1300)  # below vwap=7495, above ema200
        reason = long_filters_pass(b, bullish_snapshot(), FilterConfig())
        assert reason is not None and "VWAP" in reason

    def test_fails_when_rsi_too_low(self):
        b = bar(ct(9, 0), 7503, 7505, 7501, 7503, v=1300)
        reason = long_filters_pass(b, bullish_snapshot(rsi=Decimal("50")), FilterConfig())
        assert reason is not None and "RSI" in reason

    def test_fails_when_adx_too_weak(self):
        b = bar(ct(9, 0), 7503, 7505, 7501, 7503, v=1300)
        reason = long_filters_pass(b, bullish_snapshot(adx=Decimal("15")), FilterConfig())
        assert reason is not None and "ADX" in reason

    def test_fails_when_volume_not_confirmed(self):
        b = bar(ct(9, 0), 7503, 7505, 7501, 7503, v=1100)  # 1.1x, needs 1.25x
        reason = long_filters_pass(b, bullish_snapshot(), FilterConfig())
        assert reason is not None and "Volume" in reason

    def test_fails_when_atr_too_low(self):
        b = bar(ct(9, 0), 7503, 7505, 7501, 7503, v=1300)
        reason = long_filters_pass(b, bullish_snapshot(atr=Decimal("0.1")), FilterConfig(atr_min=Decimal("1")))
        assert reason is not None and "ATR" in reason

    def test_fails_outside_session(self):
        b = bar(ct(12, 0), 7503, 7505, 7501, 7503, v=1300)  # midday, outside both windows
        reason = long_filters_pass(b, bullish_snapshot(), FilterConfig())
        assert reason is not None and "session" in reason.lower()


class TestShortFilters:
    def test_all_pass_returns_none(self):
        b = bar(ct(9, 0), 7497, 7499, 7495, 7497, v=1300)
        assert short_filters_pass(b, bearish_snapshot(), FilterConfig()) is None

    def test_fails_when_ema50_above_ema200(self):
        b = bar(ct(9, 0), 7497, 7499, 7495, 7497, v=1300)
        reason = short_filters_pass(b, bearish_snapshot(ema50=Decimal("7540")), FilterConfig())
        assert reason is not None and "downtrend" in reason

    def test_fails_when_rsi_too_high(self):
        b = bar(ct(9, 0), 7497, 7499, 7495, 7497, v=1300)
        reason = short_filters_pass(b, bearish_snapshot(rsi=Decimal("50")), FilterConfig())
        assert reason is not None and "RSI" in reason


class TestPullbackTracker:
    def test_starts_idle_and_moves_to_watching_on_trend(self):
        tracker = PullbackTracker(Side.LONG, PullbackConfig())
        snap = bullish_snapshot()
        b = bar(ct(9, 0), 7500, 7501, 7499, 7500)
        result = tracker.update(b, snap, bar_index=1, trend_ok=True)
        assert not result.triggered
        assert tracker.state.value == "watching"

    def test_resets_to_idle_when_trend_breaks(self):
        tracker = PullbackTracker(Side.LONG, PullbackConfig())
        snap = bullish_snapshot()
        b = bar(ct(9, 0), 7500, 7501, 7499, 7500)
        tracker.update(b, snap, 1, trend_ok=True)
        result = tracker.update(b, snap, 2, trend_ok=False)
        assert not result.triggered
        assert tracker.state.value == "idle"

    def test_full_sequence_triggers_entry(self):
        """WATCHING -> pullback candle closes bullish -> ARMED -> next bar
        breaks the armed high -> triggers."""
        config = PullbackConfig(pullback_distance=Decimal("3"))
        tracker = PullbackTracker(Side.LONG, config)
        snap = bullish_snapshot(ema21=Decimal("7498"))

        # Bar 1: trend confirmed, not yet in pullback zone.
        b1 = bar(ct(9, 0), 7510, 7511, 7509, 7510)
        r1 = tracker.update(b1, snap, 1, trend_ok=True)
        assert not r1.triggered and tracker.state.value == "watching"

        # Bar 2: price dips into the pullback zone (low <= ema21 + distance)
        # and closes bullish (green candle) -> arms.
        b2 = bar(ct(9, 5), 7497, 7499, 7496, 7499)  # open < close: bullish
        r2 = tracker.update(b2, snap, 2, trend_ok=True)
        assert not r2.triggered and tracker.state.value == "armed"

        # Bar 3: doesn't yet close above bar 2's high (7499) -> still armed.
        b3 = bar(ct(9, 10), 7499, 7499, 7497, 7498)
        r3 = tracker.update(b3, snap, 3, trend_ok=True)
        assert not r3.triggered and tracker.state.value == "armed"

        # Bar 4: closes above the armed level -> triggers.
        b4 = bar(ct(9, 15), 7499, 7502, 7499, 7501)
        r4 = tracker.update(b4, snap, 4, trend_ok=True)
        assert r4.triggered
        assert r4.trigger_level == Decimal("7499")
        assert tracker.state.value == "idle"  # reset after triggering

    def test_stale_armed_setup_resets_to_watching(self):
        config = PullbackConfig(pullback_distance=Decimal("3"), max_arm_bars=2)
        tracker = PullbackTracker(Side.LONG, config)
        snap = bullish_snapshot(ema21=Decimal("7498"))

        tracker.update(bar(ct(9, 0), 7510, 7511, 7509, 7510), snap, 1, True)
        # Arm at bar index 2.
        tracker.update(bar(ct(9, 5), 7497, 7499, 7496, 7499), snap, 2, True)
        assert tracker.state.value == "armed"

        # Bar index 5 is more than max_arm_bars(2) past the arm bar -> stale.
        result = tracker.update(bar(ct(9, 20), 7498, 7498, 7497, 7497), snap, 5, True)
        assert not result.triggered
        assert tracker.state.value == "watching"

    def test_short_pullback_mirrors_long(self):
        config = PullbackConfig(pullback_distance=Decimal("3"))
        tracker = PullbackTracker(Side.SHORT, config)
        snap = bearish_snapshot(ema21=Decimal("7502"))

        tracker.update(bar(ct(9, 0), 7490, 7491, 7489, 7490), snap, 1, True)
        # Rally into the pullback zone, closes bearish (red candle) -> arms.
        b2 = bar(ct(9, 5), 7503, 7504, 7501, 7501)  # open > close: bearish
        r2 = tracker.update(b2, snap, 2, True)
        assert tracker.state.value == "armed"
        # Closes below the armed low -> triggers short.
        b3 = bar(ct(9, 10), 7500, 7500, 7498, 7499)
        r3 = tracker.update(b3, snap, 3, True)
        assert r3.triggered
        assert r3.trigger_level == Decimal("7501")

    def test_reset_clears_state(self):
        tracker = PullbackTracker(Side.LONG, PullbackConfig())
        tracker.update(bar(ct(9, 0), 7500, 7501, 7499, 7500), bullish_snapshot(), 1, True)
        assert tracker.state.value != "idle"
        tracker.reset()
        assert tracker.state.value == "idle"
