"""Indicator and strategy tests.

The session-boundary tests matter most. Both new strategies reset state at the
CME trade date, not at midnight, and getting that wrong would silently merge
two sessions' opening ranges — producing a strategy that looks fine and trades
levels that never existed.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from futures_bot.contracts import CME_TZ, MES
from futures_bot.models import Bar, Position, Side, SignalAction
from futures_bot.strategy.indicators import (
    atr,
    ema,
    ema_series,
    is_new_session,
    session_bars,
    sma,
    true_range,
    typical_price,
    vwap,
    vwap_bands,
)
from futures_bot.strategy.opening_range_breakout import OpeningRangeBreakout
from futures_bot.strategy.vwap_reversion import VwapReversion


def ct(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=CME_TZ)


def bar(ts, o, h, l, c, v=1000) -> Bar:
    return Bar(
        timestamp=ts,
        open=Decimal(str(o)),
        high=Decimal(str(h)),
        low=Decimal(str(l)),
        close=Decimal(str(c)),
        volume=v,
    )


class TestIndicators:
    def test_sma(self):
        values = [Decimal(x) for x in ("10", "20", "30", "40")]
        assert sma(values, 4) == Decimal("25")
        assert sma(values, 2) == Decimal("35")
        assert sma(values, 5) is None

    def test_sma_rejects_bad_period(self):
        with pytest.raises(ValueError):
            sma([Decimal("1")], 0)

    def test_ema_seeds_from_sma(self):
        values = [Decimal(x) for x in ("10", "20", "30")]
        series = ema_series(values, 3)
        assert series == [Decimal("20")]  # seed only

    def test_ema_follows_price(self):
        rising = [Decimal(str(i)) for i in range(1, 30)]
        falling = list(reversed(rising))
        assert ema(rising, 10) > ema(rising[:15], 10)
        assert ema(falling, 10) < Decimal("15")

    def test_true_range_includes_gap(self):
        b = bar(ct(2026, 7, 21, 9), 100, 105, 99, 104)
        assert true_range(b, None) == Decimal("6")  # high - low
        # A gap up from 90 makes the true range larger than the bar's own range.
        assert true_range(b, Decimal("90")) == Decimal("15")

    def test_atr_needs_history(self):
        bars = [bar(ct(2026, 7, 21, 9, i), 100, 101, 99, 100) for i in range(5)]
        assert atr(bars, 14) is None
        assert atr(bars, 3) is not None

    def test_atr_on_constant_range(self):
        bars = [bar(ct(2026, 7, 21, 9, i), 100, 102, 98, 100) for i in range(20)]
        assert atr(bars, 14) == Decimal("4")

    def test_typical_price(self):
        assert typical_price(bar(ct(2026, 7, 21, 9), 100, 110, 100, 105)) == Decimal("105")

    def test_vwap_weights_by_volume(self):
        bars = [
            bar(ct(2026, 7, 21, 9, 0), 100, 100, 100, 100, v=1),
            bar(ct(2026, 7, 21, 9, 1), 200, 200, 200, 200, v=9),
        ]
        # Heavily weighted toward the second bar.
        assert vwap(bars) == Decimal("190")

    def test_vwap_falls_back_when_volume_is_zero(self):
        bars = [
            bar(ct(2026, 7, 21, 9, 0), 100, 100, 100, 100, v=0),
            bar(ct(2026, 7, 21, 9, 1), 200, 200, 200, 200, v=0),
        ]
        assert vwap(bars) == Decimal("150")

    def test_vwap_bands_bracket_the_mean(self):
        bars = [
            bar(ct(2026, 7, 21, 9, i), 100 + i, 100 + i, 100 + i, 100 + i, v=100)
            for i in range(10)
        ]
        lower, centre, upper = vwap_bands(bars, Decimal("1"))
        assert lower < centre < upper

    def test_session_bars_respect_the_17_00_boundary(self):
        """An 18:00 bar belongs to the next trade date, not the calendar one."""
        bars = [
            bar(ct(2026, 7, 20, 15, 0), 100, 100, 100, 100),   # Monday session
            bar(ct(2026, 7, 20, 18, 0), 100, 100, 100, 100),   # Tuesday session
            bar(ct(2026, 7, 21, 9, 0), 100, 100, 100, 100),    # Tuesday session
        ]
        latest = session_bars(bars)
        assert len(latest) == 2
        assert latest[0].timestamp.hour == 18

    def test_is_new_session(self):
        same = [bar(ct(2026, 7, 21, 9, 0), 1, 1, 1, 1), bar(ct(2026, 7, 21, 9, 1), 1, 1, 1, 1)]
        crossing = [bar(ct(2026, 7, 20, 15, 0), 1, 1, 1, 1), bar(ct(2026, 7, 20, 18, 0), 1, 1, 1, 1)]
        assert not is_new_session(same)
        assert is_new_session(crossing)


def orb_session(
    open_high=10.0, open_low=0.0, breakout_close=None, base=Decimal("7500")
) -> list[Bar]:
    """Thirty minutes of range, then one bar after the window closes."""
    bars = []
    for i in range(6):  # 08:30-09:00 in 5m bars
        bars.append(
            bar(
                ct(2026, 7, 21, 8, 30 + i * 5),
                base, base + Decimal(str(open_high)), base + Decimal(str(open_low)), base,
            )
        )
    if breakout_close is not None:
        c = base + Decimal(str(breakout_close))
        bars.append(bar(ct(2026, 7, 21, 9, 5), base, max(c, base), min(c, base), c))
    return bars


class TestOpeningRangeBreakout:
    """Constructor calls below pass ``earliest_entry_ct``/``latest_entry_ct``
    (wide open) and a small ``trend_period`` explicitly. The strategy now
    also gates entries on a configurable entry-time window and a 200-period
    EMA trend filter (see ``config.yaml``'s ``opening_range_breakout``
    section for the real defaults); the synthetic fixtures here are only a
    handful of bars, so a 200-bar EMA warmup is neutralized with a tiny
    period, and the default 10:00-11:00 entry window is widened, so these
    tests keep exercising the range-breakout logic they were written for
    rather than being gated by unrelated filters.
    """

    def test_holds_while_building_the_range(self):
        s = OpeningRangeBreakout(MES, range_minutes=30)
        bars = orb_session()[:3]
        sig = s.on_bar(bars, None)
        assert sig.action is SignalAction.HOLD
        assert "opening range" in sig.reason.lower()

    def test_enters_long_on_break_above(self):
        s = OpeningRangeBreakout(
            MES, range_minutes=30, earliest_entry_ct="08:30", latest_entry_ct="12:00", trend_period=3
        )
        bars = orb_session(breakout_close=15.0)
        for i in range(1, len(bars) + 1):
            sig = s.on_bar(bars[:i], None)
        assert sig.action is SignalAction.ENTER_LONG

    def test_enters_short_on_break_below(self):
        s = OpeningRangeBreakout(
            MES, range_minutes=30, earliest_entry_ct="08:30", latest_entry_ct="12:00", trend_period=3
        )
        bars = orb_session(breakout_close=-5.0)
        for i in range(1, len(bars) + 1):
            sig = s.on_bar(bars[:i], None)
        assert sig.action is SignalAction.ENTER_SHORT

    def test_skips_range_that_is_too_tight(self):
        s = OpeningRangeBreakout(
            MES, range_minutes=30, min_range_points=Decimal("20"),
            earliest_entry_ct="08:30", latest_entry_ct="12:00",
        )
        bars = orb_session(open_high=5.0, breakout_close=6.0)
        for i in range(1, len(bars) + 1):
            sig = s.on_bar(bars[:i], None)
        assert sig.action is SignalAction.HOLD
        assert "too small" in sig.reason

    def test_skips_range_that_is_too_wide(self):
        s = OpeningRangeBreakout(
            MES, range_minutes=30, max_range_points=Decimal("5"),
            earliest_entry_ct="08:30", latest_entry_ct="12:00",
        )
        bars = orb_session(open_high=50.0, breakout_close=60.0)
        for i in range(1, len(bars) + 1):
            sig = s.on_bar(bars[:i], None)
        assert sig.action is SignalAction.HOLD
        assert "too large" in sig.reason

    def test_respects_entry_limit(self):
        s = OpeningRangeBreakout(
            MES, range_minutes=30, max_entries_per_session=1,
            earliest_entry_ct="08:30", latest_entry_ct="12:00", trend_period=3,
        )
        bars = orb_session(breakout_close=15.0)
        for i in range(1, len(bars) + 1):
            s.on_bar(bars[:i], None)
        # A second break of the same level must not re-enter.
        bars.append(bar(ct(2026, 7, 21, 9, 10), 7520, 7525, 7519, 7522))
        sig = s.on_bar(bars, None)
        assert sig.action is SignalAction.HOLD

    def test_entry_limit_caps_total_entries_across_both_directions(self):
        """Regression: `max_entries_per_session` was previously stored but
        never enforced -- only `_broken_up`/`_broken_down` gated re-entry,
        which allowed one long AND one short breakout in the same session
        (2 total) even at the default limit of 1. A long breakout followed
        by a genuine opposite-direction (short) breakout must not both fire."""
        s = OpeningRangeBreakout(
            MES, range_minutes=30, max_entries_per_session=1,
            earliest_entry_ct="08:30", latest_entry_ct="12:00", trend_period=3,
        )
        bars = orb_session(breakout_close=15.0)
        sig = None
        for i in range(1, len(bars) + 1):
            sig = s.on_bar(bars[:i], None)
        assert sig.action is SignalAction.ENTER_LONG
        assert s._entries_taken == 1

        # Not a re-break of the same level -- a real breakdown below the
        # opening range low.
        bars.append(bar(ct(2026, 7, 21, 9, 10), 7480, 7480, 7470, 7470))
        sig = s.on_bar(bars, None)
        assert sig.action is SignalAction.HOLD
        assert s._entries_taken == 1

    def test_entry_window_filter_holds_outside_configured_hours(self):
        """The earliest/latest entry-time window is real, current behavior.

        Replaces a stale test for ``stop_at_range_opposite``, a parameter the
        strategy accepts (via ``**params``) but does not implement -- see the
        Phase 1 reliability report for that discrepancy.
        """
        s = OpeningRangeBreakout(MES, range_minutes=30, earliest_entry_ct="10:00", latest_entry_ct="11:00")
        bars = orb_session(breakout_close=15.0)  # breakout bar is at 09:05, before the window
        for i in range(1, len(bars) + 1):
            sig = s.on_bar(bars[:i], None)
        assert sig.action is SignalAction.HOLD
        assert "10:00" in sig.reason

    def test_resets_on_a_new_session(self):
        s = OpeningRangeBreakout(
            MES, range_minutes=30, max_entries_per_session=1,
            earliest_entry_ct="08:30", latest_entry_ct="12:00", trend_period=3,
        )
        day1 = orb_session(breakout_close=15.0)
        for i in range(1, len(day1) + 1):
            s.on_bar(day1[:i], None)
        assert s._entries_taken == 1

        # Next trade date: the counter must clear.
        day2 = [bar(ct(2026, 7, 22, 8, 30 + i * 5), 7600, 7610, 7600, 7600) for i in range(6)]
        s.on_bar(day1 + day2, None)
        assert s._entries_taken == 0

    def test_wick_only_break_ignored_when_close_required(self):
        s = OpeningRangeBreakout(
            MES, range_minutes=30, require_close_beyond=True,
            earliest_entry_ct="08:30", latest_entry_ct="12:00", trend_period=3,
        )
        bars = orb_session()
        # High pokes above the range but the close does not.
        bars.append(bar(ct(2026, 7, 21, 9, 5), 7505, 7520, 7504, 7505))
        for i in range(1, len(bars) + 1):
            sig = s.on_bar(bars[:i], None)
        assert sig.action is SignalAction.HOLD


class TestVwapReversion:
    def _session(self, n=30, price=Decimal("7500")) -> list[Bar]:
        return [
            bar(ct(2026, 7, 21, 9, 0) + timedelta(minutes=i), price, price, price, price, v=500)
            for i in range(n)
        ]

    def test_waits_for_enough_bars(self):
        s = VwapReversion(MES, min_bars=20)
        sig = s.on_bar(self._session(5), None)
        assert sig.action is SignalAction.HOLD
        assert "bands mean anything" in sig.reason

    def test_shorts_above_the_upper_band(self):
        # max_entries_per_session raised well above default: fed
        # incrementally (see below), the ramp preceding the final breakout
        # bar now genuinely crosses the upper band multiple times -- each
        # call sees `position=None`, same as the original single-shot
        # version of this test, so without headroom the session's entry cap
        # would be spent before reaching the bar this test means to check.
        s = VwapReversion(MES, min_bars=10, std_devs=Decimal("1"), max_entries_per_session=99)
        bars = self._session(20)
        # Introduce variance, then a sharp extension upward.
        for i in range(10):
            bars.append(
                bar(ct(2026, 7, 21, 9, 20) + timedelta(minutes=i),
                    7500 + i, 7500 + i, 7500 + i, 7500 + i, v=500)
            )
        bars.append(bar(ct(2026, 7, 21, 9, 40), 7600, 7605, 7599, 7604, v=500))
        # VWAP is now incremental, session-anchored state (see
        # `IncrementalSessionVWAP`) -- it must be fed one new bar per call,
        # in order, the same way the live engine and backtest runner do.
        for i in range(1, len(bars) + 1):
            sig = s.on_bar(bars[:i], None)
        assert sig.action is SignalAction.ENTER_SHORT
        assert "upper VWAP band" in sig.reason

    def test_exits_when_price_returns_to_vwap(self):
        s = VwapReversion(MES, min_bars=10)
        bars = self._session(20)
        pos = Position(
            side=Side.SHORT, quantity=1,
            entry_price=Decimal("7600"), entry_time=bars[-1].timestamp,
        )
        # Warm up the incremental VWAP state on every bar but the last, then
        # hand it the final bar together with the open position.
        for i in range(1, len(bars)):
            s.on_bar(bars[:i], None)
        sig = s.on_bar(bars, pos)
        assert sig.action is SignalAction.EXIT
        assert "returned to VWAP" in sig.reason

    def test_resets_entry_count_on_new_session(self):
        s = VwapReversion(MES, min_bars=5, max_entries_per_session=1)
        s._session = None
        s.on_bar(self._session(10), None)
        s._entries_taken = 1

        next_day = [
            bar(ct(2026, 7, 22, 9, 0) + timedelta(minutes=i), 7500, 7500, 7500, 7500, v=500)
            for i in range(10)
        ]
        s.on_bar(next_day, None)
        assert s._entries_taken == 0

    def test_rejects_bad_min_bars(self):
        with pytest.raises(ValueError):
            VwapReversion(MES, min_bars=1)
