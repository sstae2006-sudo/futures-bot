"""Opening Range Breakout with EMA trend filter, trade controls, and missed signal tracking."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Optional, Sequence

from ..contracts import CME_TZ, session_date, to_ct
from ..models import Bar, Position, Signal
from .base import Strategy, StrategyRegistry
from .indicators import IncrementalEMA


def _parse_ct(value: str) -> time:
    hh, mm = value.split(":")
    return time(int(hh), int(mm))


@StrategyRegistry.register("opening_range_breakout")
class OpeningRangeBreakout(Strategy):

    def __init__(
        self,
        contract,
        range_minutes: int = 30,
        session_start_ct: str = "08:30",
        earliest_entry_ct: str = "10:00",
        latest_entry_ct: str = "11:00",
        max_entries_per_session: int = 1,
        require_close_beyond: bool = True,
        min_range_points: Decimal = Decimal("2"),
        max_range_points: Decimal = Decimal("40"),
        trend_period: int = 200,
        allow_long: bool = True,
        allow_short: bool = True,
        stop_at_range_opposite: bool = False,
        **params,
    ) -> None:
        super().__init__(contract, **params)

        self.range_minutes = range_minutes
        self.session_start = _parse_ct(session_start_ct)
        self.earliest_entry = _parse_ct(earliest_entry_ct)
        self.latest_entry = _parse_ct(latest_entry_ct)

        self.max_entries_per_session = max_entries_per_session
        self.require_close_beyond = require_close_beyond

        self.min_range_points = Decimal(str(min_range_points))
        self.max_range_points = Decimal(str(max_range_points))

        self.trend_period = trend_period

        self.allow_long = allow_long
        self.allow_short = allow_short
        self.stop_at_range_opposite = stop_at_range_opposite

        # Missed-signal tracking: counts breakouts that happened but were not
        # taken, broken down by which filter declined them.
        self.missed_breakouts = {
            "total": 0,
            "time_filter": 0,
            "ema_filter": 0,
            "long_disabled": 0,
            "short_disabled": 0,
        }

        self._session: Optional[date] = None
        self._entries_taken = 0
        self._broken_up = False
        self._broken_down = False

        # Opening-range high/low, accumulated one bar at a time as bars land
        # inside the range window instead of re-filtering the whole session
        # on every call after the window closes (see `_opening_range`, kept
        # below only as the definition this incremental version must match).
        self._range_high: Optional[Decimal] = None
        self._range_low: Optional[Decimal] = None

        # EMA trend filter, updated incrementally instead of rescanned from
        # bar zero every call (see `IncrementalEMA`'s docstring). Spans the
        # whole close history, not just the session -- same as the batch
        # `ema_series(closes, trend_period)` this replaces.
        self._trend_ema = IncrementalEMA(trend_period, history=1)

        self.warmup_bars = trend_period + 5

    def _reset_session(self, sd: date):
        self._session = sd
        self._entries_taken = 0
        self._broken_up = False
        self._broken_down = False
        self._range_high = None
        self._range_low = None

    def _range_window(self, sd: date):
        start = datetime.combine(sd, self.session_start, tzinfo=CME_TZ)
        return start, start + timedelta(minutes=self.range_minutes)

    def _opening_range(self, bars, sd):
        """Batch definition of the opening range -- kept as the reference
        `tests/test_incremental_indicators.py` checks `_range_high`/
        `_range_low` against, not used on the hot path (see `on_bar`)."""
        start, end = self._range_window(sd)
        window = [b for b in bars if start <= to_ct(b.timestamp) < end]
        if not window:
            return None
        return (max(b.high for b in window), min(b.low for b in window))

    def on_bar(
        self,
        bars: Sequence[Bar],
        position: Optional[Position],
    ) -> Signal:
        current = bars[-1]
        sd = session_date(current.timestamp)

        if sd != self._session:
            self._reset_session(sd)

        # Fed every bar, warmup or not -- a recurrence over the full close
        # history, same as the trend EMA it replaces.
        self._trend_ema.update(current.close)

        start, end = self._range_window(sd)
        now = to_ct(current.timestamp)

        if now < start:
            return self.hold("Before opening range.")

        if now < end:
            # Still inside the range window -- accumulate this bar into the
            # running high/low rather than re-scanning for it later.
            if self._range_high is None:
                self._range_high, self._range_low = current.high, current.low
            else:
                self._range_high = max(self._range_high, current.high)
                self._range_low = min(self._range_low, current.low)
            return self.hold("Building opening range.")

        if self._range_high is None:
            return self.hold("No opening range.")

        range_high, range_low = self._range_high, self._range_low
        range_size = range_high - range_low

        if position:
            return self.hold(f"Holding {position.side.value}.")

        breakout_price = current.close if self.require_close_beyond else current.high
        breakdown_price = current.close if self.require_close_beyond else current.low

        long_break = breakout_price > range_high
        short_break = breakdown_price < range_low

        # Detect missed breakouts BEFORE filters.
        if long_break and not self._broken_up:
            self.missed_breakouts["total"] += 1
        if short_break and not self._broken_down:
            self.missed_breakouts["total"] += 1

        # Entry time window filter.
        current_time = now.time()

        if current_time < self.earliest_entry:
            if long_break or short_break:
                self.missed_breakouts["time_filter"] += 1
            return self.hold(f"Waiting until {self.earliest_entry:%H:%M} CT.")

        if current_time > self.latest_entry:
            if long_break or short_break:
                self.missed_breakouts["time_filter"] += 1
            return self.hold(f"Entry window closed at {self.latest_entry:%H:%M} CT.")

        if range_size < self.min_range_points:
            return self.hold("Opening range too small.")

        if range_size > self.max_range_points:
            return self.hold("Opening range too large.")

        trend = self._trend_ema.value

        if trend is None:
            return self.hold("Waiting for EMA.")

        bullish = current.close > trend
        bearish = current.close < trend

        if long_break and not bullish:
            self.missed_breakouts["ema_filter"] += 1

        if short_break and not bearish:
            self.missed_breakouts["ema_filter"] += 1

        # `max_entries_per_session` was previously stored but never enforced
        # here -- `_broken_up`/`_broken_down` alone capped this strategy at
        # one long *and* one short per session (up to 2 total) regardless of
        # the configured value, silently ignoring it. Wired in the same way
        # `vwap_reversion`'s (correctly working) `max_entries_per_session`
        # already is, as a hard ceiling layered on top of the existing
        # one-per-direction gating below.
        if self._entries_taken >= self.max_entries_per_session:
            return self.hold(
                f"Already took {self._entries_taken} entries this session (limit {self.max_entries_per_session})."
            )

        if long_break and bullish and self.allow_long and not self._broken_up:
            self._broken_up = True
            self._entries_taken += 1
            return self.enter_long(
                "ORB long breakout with EMA trend",
                stop_loss=range_low if self.stop_at_range_opposite else None,
                range_high=float(range_high),
                range_low=float(range_low),
                range_size=float(range_size),
                trend_ema=float(trend),
            )

        if short_break and bearish and self.allow_short and not self._broken_down:
            self._broken_down = True
            self._entries_taken += 1
            return self.enter_short(
                "ORB short breakdown with EMA trend",
                stop_loss=range_high if self.stop_at_range_opposite else None,
                range_high=float(range_high),
                range_low=float(range_low),
                range_size=float(range_size),
                trend_ema=float(trend),
            )

        return self.hold(f"No setup. EMA {trend:.2f}")
