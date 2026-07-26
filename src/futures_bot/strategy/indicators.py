"""Indicator math shared across strategies.

Pure functions over sequences. No I/O, no state, no awareness of positions —
which keeps them trivially testable and means a bug here shows up in a unit
test rather than in a live fill.

Session-anchored indicators (VWAP) take the bars for a single session only.
Deciding where a session starts is the caller's job, because that boundary is
exchange-specific and belongs with the contract, not the arithmetic.
"""

from __future__ import annotations

from collections import deque
from datetime import date
from decimal import Decimal
from typing import Optional, Sequence

from ..contracts import session_date
from ..models import Bar


def sma(values: Sequence[Decimal], period: int) -> Optional[Decimal]:
    if period <= 0:
        raise ValueError(f"period must be positive, got {period}")
    if len(values) < period:
        return None
    return sum(values[-period:]) / Decimal(period)


def ema_series(values: Sequence[Decimal], period: int) -> list[Decimal]:
    if period <= 0:
        raise ValueError(f"period must be positive, got {period}")
    if len(values) < period:
        return []

    k = Decimal(2) / Decimal(period + 1)
    seed = sum(values[:period]) / Decimal(period)

    out = [seed]
    for value in values[period:]:
        out.append(value * k + out[-1] * (1 - k))

    return out


def ema(values: Sequence[Decimal], period: int) -> Optional[Decimal]:
    series = ema_series(values, period)
    return series[-1] if series else None


def true_range(bar: Bar, previous_close: Optional[Decimal]) -> Decimal:
    if previous_close is None:
        return bar.high - bar.low

    return max(
        bar.high - bar.low,
        abs(bar.high - previous_close),
        abs(bar.low - previous_close),
    )


def atr(bars: Sequence[Bar], period: int = 14) -> Optional[Decimal]:
    if period <= 0:
        raise ValueError(f"period must be positive, got {period}")
    if len(bars) < period + 1:
        return None

    ranges = [true_range(bars[i], bars[i - 1].close) for i in range(1, len(bars))]
    value = sum(ranges[:period]) / Decimal(period)

    for r in ranges[period:]:
        value = (value * Decimal(period - 1) + r) / Decimal(period)

    return value


def typical_price(bar: Bar) -> Decimal:
    return (bar.high + bar.low + bar.close) / Decimal(3)


def vwap(bars: Sequence[Bar]) -> Optional[Decimal]:
    if not bars:
        return None

    total_volume = sum(b.volume for b in bars)
    if total_volume == 0:
        return sum(typical_price(b) for b in bars) / Decimal(len(bars))

    weighted = sum(typical_price(b) * Decimal(b.volume) for b in bars)
    return weighted / Decimal(total_volume)


def vwap_bands(
    bars: Sequence[Bar], std_devs: Decimal = Decimal("1")
) -> Optional[tuple[Decimal, Decimal, Decimal]]:
    centre = vwap(bars)
    if centre is None or len(bars) < 2:
        return None

    total_volume = sum(b.volume for b in bars)
    if total_volume == 0:
        variance = sum((typical_price(b) - centre) ** 2 for b in bars) / Decimal(len(bars))
    else:
        variance = sum(
            Decimal(b.volume) * (typical_price(b) - centre) ** 2 for b in bars
        ) / Decimal(total_volume)

    deviation = variance.sqrt()
    return (centre - deviation * std_devs, centre, centre + deviation * std_devs)


def rsi(closes: Sequence[Decimal], period: int = 14) -> Optional[Decimal]:
    if period <= 0:
        raise ValueError(f"period must be positive, got {period}")
    if len(closes) < period + 1:
        return None

    gains = []
    losses = []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains.append(change if change > 0 else Decimal("0"))
        losses.append(-change if change < 0 else Decimal("0"))

    avg_gain = sum(gains[:period]) / Decimal(period)
    avg_loss = sum(losses[:period]) / Decimal(period)

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * Decimal(period - 1) + gains[i]) / Decimal(period)
        avg_loss = (avg_loss * Decimal(period - 1) + losses[i]) / Decimal(period)

    if avg_loss == 0:
        return Decimal("100")

    rs = avg_gain / avg_loss
    return Decimal("100") - Decimal("100") / (Decimal("1") + rs)


def adx(bars: Sequence[Bar], period: int = 14) -> Optional[Decimal]:
    if len(bars) < period * 2:
        return None

    plus_dm = []
    minus_dm = []
    tr = []

    for i in range(1, len(bars)):
        up_move = bars[i].high - bars[i - 1].high
        down_move = bars[i - 1].low - bars[i].low

        plus_dm.append(up_move if up_move > down_move and up_move > 0 else Decimal("0"))
        minus_dm.append(down_move if down_move > up_move and down_move > 0 else Decimal("0"))
        tr.append(true_range(bars[i], bars[i - 1].close))

    def wilder_smooth(values, period):
        if len(values) < period:
            return []
        out = [sum(values[:period])]
        for value in values[period:]:
            out.append(out[-1] - out[-1] / Decimal(period) + value)
        return out

    smoothed_tr = wilder_smooth(tr, period)
    smoothed_plus = wilder_smooth(plus_dm, period)
    smoothed_minus = wilder_smooth(minus_dm, period)

    dx_values = []
    for i in range(len(smoothed_tr)):
        if smoothed_tr[i] == 0:
            dx_values.append(Decimal("0"))
            continue

        plus_di = Decimal("100") * smoothed_plus[i] / smoothed_tr[i]
        minus_di = Decimal("100") * smoothed_minus[i] / smoothed_tr[i]
        total = plus_di + minus_di

        dx_values.append(Decimal("0") if total == 0 else Decimal("100") * abs(plus_di - minus_di) / total)

    if len(dx_values) < period:
        return None

    result = sum(dx_values[:period]) / Decimal(period)
    for dx in dx_values[period:]:
        result = (result * Decimal(period - 1) + dx) / Decimal(period)

    return result


def session_bars(bars: Sequence[Bar], for_session: Optional[date] = None) -> list[Bar]:
    """Return bars for one CME trading session.

    Optimized for backtesting. A previous implementation scanned the entire
    historical dataset every time the strategy requested today's session,
    causing large walk-forward tests to degrade toward O(n^2). Since bars are
    chronological, walk backward from the newest bar until the session
    changes.
    """
    if not bars:
        return []

    target = for_session or session_date(bars[-1].timestamp)

    result = []
    for bar in reversed(bars):
        if session_date(bar.timestamp) != target:
            break
        result.append(bar)

    result.reverse()
    return result


def is_new_session(bars: Sequence[Bar]) -> bool:
    if len(bars) < 2:
        return True
    return session_date(bars[-1].timestamp) != session_date(bars[-2].timestamp)


class IncrementalEMA:
    """One EMA, updated one price at a time instead of rescanned from bar zero.

    ``ema_series(closes, period)`` recomputes the whole series on every call,
    which makes a strategy that calls it once per bar O(n^2) in bar count over
    a full backtest. This class keeps just the running value (plus a short
    lookback window for crossover/slope checks) and accepts each new price in
    O(1), seeded identically to :func:`ema_series` -- a simple average of the
    first ``period`` prices -- so the two agree bar-for-bar. See
    ``tests/test_incremental_indicators.py`` for the equivalence proof.

    Like :class:`~futures_bot.strategy.trend_pullback.rolling.RollingIndicators`,
    this is a recurrence, not a pure function: feed prices in chronological
    order, exactly once each, starting from the first bar of the series it is
    tracking. Skipping or replaying a price desyncs it from the batch
    equivalent.
    """

    def __init__(self, period: int, history: int = 2) -> None:
        if period <= 0:
            raise ValueError(f"period must be positive, got {period}")
        self.period = period
        self._k = Decimal(2) / Decimal(period + 1)
        self._seed_buffer: list[Decimal] = []
        self._history: deque[Decimal] = deque(maxlen=max(history, 1))

    @property
    def value(self) -> Optional[Decimal]:
        """The current (most recent) EMA value, or ``None`` during warmup."""
        return self._history[-1] if self._history else None

    def lookback(self, n: int) -> Optional[Decimal]:
        """The value from ``n`` updates ago; ``n=1`` is the current value.

        Returns ``None`` if fewer than ``n`` values have been produced yet,
        including when ``n`` exceeds the ``history`` window this instance was
        constructed with.
        """
        if n < 1 or len(self._history) < n:
            return None
        return self._history[-n]

    def update(self, price: Decimal) -> Optional[Decimal]:
        if self._history:
            value = price * self._k + self._history[-1] * (1 - self._k)
        else:
            # Not yet seeded: accumulate until `period` prices are in hand,
            # then seed with their simple average -- identical to the batch
            # implementation's seeding.
            self._seed_buffer.append(price)
            if len(self._seed_buffer) < self.period:
                return None
            value = sum(self._seed_buffer) / Decimal(self.period)
            self._seed_buffer = []
        self._history.append(value)
        return value

    def reset(self) -> None:
        self._seed_buffer = []
        self._history.clear()


class IncrementalSessionVWAP:
    """Session-anchored VWAP and bands, updated one bar at a time.

    Equivalent to calling :func:`session_bars` + :func:`vwap_bands` on every
    bar of the session, but O(1) per update instead of O(session length).
    Resets on the CME session boundary (17:00 CT), same as :func:`session_bars`.

    The bands use the identity ``Var(X) = E[X^2] - E[X]^2`` so the variance
    can be maintained as a running sum rather than recomputed from every bar
    in the session against the *current* mean each time -- see
    ``tests/test_incremental_indicators.py`` for the proof that this matches
    :func:`vwap_bands` (to within Decimal rounding).
    """

    def __init__(self) -> None:
        self._session: Optional[date] = None
        self._cum_pv = Decimal("0")
        self._cum_pv2 = Decimal("0")
        self._cum_vol = 0
        self._cum_tp = Decimal("0")
        self._cum_tp2 = Decimal("0")
        self.session_bar_count = 0
        self.centre: Optional[Decimal] = None
        self._variance: Optional[Decimal] = None

    def reset(self) -> None:
        self.__init__()  # noqa: PLC2801 - reinitializing is simplest here.

    def update(self, bar: Bar) -> Optional[Decimal]:
        sd = session_date(bar.timestamp)
        if sd != self._session:
            self._session = sd
            self._cum_pv = Decimal("0")
            self._cum_pv2 = Decimal("0")
            self._cum_vol = 0
            self._cum_tp = Decimal("0")
            self._cum_tp2 = Decimal("0")
            self.session_bar_count = 0

        tp = typical_price(bar)
        self._cum_pv += tp * Decimal(bar.volume)
        self._cum_pv2 += tp * tp * Decimal(bar.volume)
        self._cum_vol += bar.volume
        self._cum_tp += tp
        self._cum_tp2 += tp * tp
        self.session_bar_count += 1

        if self._cum_vol == 0:
            self.centre = self._cum_tp / Decimal(self.session_bar_count)
            mean_sq = self._cum_tp2 / Decimal(self.session_bar_count)
        else:
            self.centre = self._cum_pv / Decimal(self._cum_vol)
            mean_sq = self._cum_pv2 / Decimal(self._cum_vol)

        # Clamp at zero: Decimal rounding can push this fractionally negative
        # when the true variance is ~0 (e.g. a flat session).
        self._variance = max(mean_sq - self.centre * self.centre, Decimal("0"))
        return self.centre

    def bands(self, std_devs: Decimal = Decimal("1")) -> Optional[tuple[Decimal, Decimal, Decimal]]:
        """Mirrors :func:`vwap_bands`'s ``(lower, centre, upper)`` return, or
        ``None`` before the first bar (or with fewer than 2 bars, matching
        the batch function's own minimum)."""
        if self.centre is None or self._variance is None or self.session_bar_count < 2:
            return None
        deviation = self._variance.sqrt()
        return (self.centre - deviation * std_devs, self.centre, self.centre + deviation * std_devs)


def atr_series(bars: Sequence[Bar], period: int = 14) -> list[float]:
    """Average True Range (ATR). Returns a list of ATR values."""
    if len(bars) < period + 1:
        return []

    true_ranges = []
    for i in range(1, len(bars)):
        high = float(bars[i].high)
        low = float(bars[i].low)
        prev_close = float(bars[i - 1].close)
        true_ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))

    atr_value = sum(true_ranges[:period]) / period
    values = [atr_value]

    for tr in true_ranges[period:]:
        atr_value = ((atr_value * (period - 1)) + tr) / period
        values.append(atr_value)

    return values
