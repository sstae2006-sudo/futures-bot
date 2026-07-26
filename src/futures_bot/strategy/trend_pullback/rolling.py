"""Incremental indicators, updated one bar at a time.

Every other strategy in this codebase recomputes its indicators over the full
bar history on every call — fine at the scan sizes those strategies run at,
but ``ema_series(closes, period)`` rescanning from bar zero on every single
bar makes a backtest O(n^2) in bar count. On a multi-month 5-minute MES
series (tens of thousands of bars) that difference is the gap between a
backtest finishing in seconds and one that doesn't finish before you give up
and Ctrl-C it.

This module trades that away: each indicator keeps just enough running state
to accept the next bar in O(1) (EMA, Wilder RSI, Wilder ADX) or O(1) amortized
(VWAP and volume SMA, via a deque that only pays for the bars currently in the
window). Feed bars one at a time through :meth:`RollingIndicators.update`;
never call it twice for the same bar, and never skip one — these are
recurrences, not pure functions, so replaying history correctly means calling
this in bar order exactly once per bar.

VWAP resets on the CME session boundary using the same ``session_date`` logic
as the rest of the codebase — 17:00 CT, not midnight.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional

from ...contracts import session_date
from ...models import Bar
from ..indicators import true_range, typical_price


class _EMA:
    """One exponential moving average, updated incrementally."""

    def __init__(self, period: int) -> None:
        if period <= 0:
            raise ValueError(f"period must be positive, got {period}")
        self.period = period
        self._k = Decimal(2) / Decimal(period + 1)
        self._seed_buffer: list[Decimal] = []
        self.value: Optional[Decimal] = None

    def update(self, price: Decimal) -> Optional[Decimal]:
        if self.value is not None:
            self.value = price * self._k + self.value * (1 - self._k)
            return self.value

        # Not yet seeded: accumulate until we have `period` closes, then seed
        # with their simple average — identical to the batch implementation's
        # seeding, so incremental and batch EMA agree bar-for-bar.
        self._seed_buffer.append(price)
        if len(self._seed_buffer) < self.period:
            return None
        self.value = sum(self._seed_buffer) / Decimal(self.period)
        self._seed_buffer = []
        return self.value


class _WilderRSI:
    """Wilder RSI, updated incrementally."""

    def __init__(self, period: int = 14) -> None:
        if period <= 0:
            raise ValueError(f"period must be positive, got {period}")
        self.period = period
        self._prev_close: Optional[Decimal] = None
        self._changes: list[Decimal] = []
        self._avg_gain: Optional[Decimal] = None
        self._avg_loss: Optional[Decimal] = None
        self.value: Optional[Decimal] = None

    def update(self, close: Decimal) -> Optional[Decimal]:
        if self._prev_close is None:
            self._prev_close = close
            return None

        change = close - self._prev_close
        self._prev_close = close
        gain = change if change > 0 else Decimal("0")
        loss = -change if change < 0 else Decimal("0")

        if self._avg_gain is None:
            self._changes.append(change)
            if len(self._changes) < self.period:
                return None
            gains = sum((c if c > 0 else Decimal("0")) for c in self._changes)
            losses = sum((-c if c < 0 else Decimal("0")) for c in self._changes)
            self._avg_gain = gains / Decimal(self.period)
            self._avg_loss = losses / Decimal(self.period)
        else:
            self._avg_gain = (self._avg_gain * Decimal(self.period - 1) + gain) / Decimal(self.period)
            self._avg_loss = (self._avg_loss * Decimal(self.period - 1) + loss) / Decimal(self.period)

        if self._avg_loss == 0:
            self.value = Decimal("100")
        else:
            rs = self._avg_gain / self._avg_loss
            self.value = Decimal("100") - Decimal("100") / (Decimal("1") + rs)
        return self.value


class _WilderADX:
    """Wilder ADX, updated incrementally. Needs ~2x period bars to produce a value."""

    def __init__(self, period: int = 14) -> None:
        if period <= 0:
            raise ValueError(f"period must be positive, got {period}")
        self.period = period
        self._prev_bar: Optional[Bar] = None
        self._tr_buf: list[Decimal] = []
        self._plus_dm_buf: list[Decimal] = []
        self._minus_dm_buf: list[Decimal] = []
        self._smoothed_tr: Optional[Decimal] = None
        self._smoothed_plus_dm: Optional[Decimal] = None
        self._smoothed_minus_dm: Optional[Decimal] = None
        self._dx_buf: list[Decimal] = []
        self.value: Optional[Decimal] = None

    def update(self, bar: Bar) -> Optional[Decimal]:
        if self._prev_bar is None:
            self._prev_bar = bar
            return None

        prev = self._prev_bar
        self._prev_bar = bar

        up_move = bar.high - prev.high
        down_move = prev.low - bar.low
        plus_dm = up_move if (up_move > down_move and up_move > 0) else Decimal("0")
        minus_dm = down_move if (down_move > up_move and down_move > 0) else Decimal("0")
        tr = true_range(bar, prev.close)

        if self._smoothed_tr is None:
            self._tr_buf.append(tr)
            self._plus_dm_buf.append(plus_dm)
            self._minus_dm_buf.append(minus_dm)
            if len(self._tr_buf) < self.period:
                return None
            self._smoothed_tr = sum(self._tr_buf)
            self._smoothed_plus_dm = sum(self._plus_dm_buf)
            self._smoothed_minus_dm = sum(self._minus_dm_buf)
        else:
            p = Decimal(self.period)
            self._smoothed_tr = self._smoothed_tr - (self._smoothed_tr / p) + tr
            self._smoothed_plus_dm = self._smoothed_plus_dm - (self._smoothed_plus_dm / p) + plus_dm
            self._smoothed_minus_dm = self._smoothed_minus_dm - (self._smoothed_minus_dm / p) + minus_dm

        if self._smoothed_tr == 0:
            dx = Decimal("0")
        else:
            plus_di = Decimal("100") * self._smoothed_plus_dm / self._smoothed_tr
            minus_di = Decimal("100") * self._smoothed_minus_dm / self._smoothed_tr
            di_sum = plus_di + minus_di
            dx = Decimal("0") if di_sum == 0 else Decimal("100") * abs(plus_di - minus_di) / di_sum

        if self.value is None:
            self._dx_buf.append(dx)
            if len(self._dx_buf) < self.period:
                return None
            self.value = sum(self._dx_buf) / Decimal(self.period)
        else:
            self.value = (self.value * Decimal(self.period - 1) + dx) / Decimal(self.period)
        return self.value


class _SessionVWAP:
    """VWAP that resets on the CME session boundary, not midnight."""

    def __init__(self) -> None:
        self._session: Optional[date] = None
        self._cum_pv = Decimal("0")
        self._cum_vol = 0
        self._cum_typical_unweighted = Decimal("0")
        self._n = 0
        self.value: Optional[Decimal] = None

    def update(self, bar: Bar) -> Optional[Decimal]:
        sd = session_date(bar.timestamp)
        if sd != self._session:
            self._session = sd
            self._cum_pv = Decimal("0")
            self._cum_vol = 0
            self._cum_typical_unweighted = Decimal("0")
            self._n = 0

        tp = typical_price(bar)
        self._cum_pv += tp * Decimal(bar.volume)
        self._cum_vol += bar.volume
        self._cum_typical_unweighted += tp
        self._n += 1

        if self._cum_vol == 0:
            self.value = self._cum_typical_unweighted / Decimal(self._n)
        else:
            self.value = self._cum_pv / Decimal(self._cum_vol)
        return self.value


class _RollingSMA:
    """Simple moving average via a fixed-size deque — O(1) amortized per update."""

    def __init__(self, period: int) -> None:
        if period <= 0:
            raise ValueError(f"period must be positive, got {period}")
        self.period = period
        self._window: deque[Decimal] = deque(maxlen=period)
        self._sum = Decimal("0")
        self.value: Optional[Decimal] = None

    def update(self, value: Decimal) -> Optional[Decimal]:
        if len(self._window) == self.period:
            self._sum -= self._window[0]
        self._window.append(value)
        self._sum += value
        if len(self._window) < self.period:
            return None
        self.value = self._sum / Decimal(self.period)
        return self.value


@dataclass
class IndicatorSnapshot:
    """Every indicator value as of the most recent bar. ``None`` during warmup."""

    ema9: Optional[Decimal]
    ema21: Optional[Decimal]
    ema50: Optional[Decimal]
    ema200: Optional[Decimal]
    vwap: Optional[Decimal]
    atr: Optional[Decimal]
    rsi: Optional[Decimal]
    adx: Optional[Decimal]
    volume_sma: Optional[Decimal]

    @property
    def ready(self) -> bool:
        """True once every indicator has cleared its warmup period."""
        return all(
            v is not None
            for v in (
                self.ema9, self.ema21, self.ema50, self.ema200,
                self.vwap, self.atr, self.rsi, self.adx, self.volume_sma,
            )
        )


class RollingIndicators:
    """Owns one instance of each indicator and updates all of them per bar."""

    def __init__(
        self,
        ema_fast: int = 9,
        ema_mid: int = 21,
        ema_slow: int = 50,
        ema_trend: int = 200,
        atr_period: int = 14,
        rsi_period: int = 14,
        adx_period: int = 14,
        volume_sma_period: int = 20,
    ) -> None:
        self._ema9 = _EMA(ema_fast)
        self._ema21 = _EMA(ema_mid)
        self._ema50 = _EMA(ema_slow)
        self._ema200 = _EMA(ema_trend)
        self._vwap = _SessionVWAP()
        self._rsi = _WilderRSI(rsi_period)
        self._adx = _WilderADX(adx_period)
        self._volume_sma = _RollingSMA(volume_sma_period)

        # ATR needs Wilder smoothing over true range; reuses the same
        # recurrence shape as RSI's average-gain/loss, so it is implemented
        # directly here rather than as a fifth helper class.
        self._atr_period = atr_period
        self._atr_buf: list[Decimal] = []
        self._atr_value: Optional[Decimal] = None
        self._prev_close: Optional[Decimal] = None

        self.history_len = 0

    def _update_atr(self, bar: Bar) -> Optional[Decimal]:
        # First bar has no previous close, so it contributes no true range at
        # all -- matching the batch atr(), which starts its range list at
        # index 1. Including a "range from bar zero alone" entry here would
        # shift the seed window by one bar relative to the batch version.
        if self._prev_close is None:
            self._prev_close = bar.close
            return None

        tr = true_range(bar, self._prev_close)
        self._prev_close = bar.close

        if self._atr_value is None:
            self._atr_buf.append(tr)
            if len(self._atr_buf) < self._atr_period:
                return None
            self._atr_value = sum(self._atr_buf) / Decimal(self._atr_period)
        else:
            p = Decimal(self._atr_period)
            self._atr_value = (self._atr_value * (p - 1) + tr) / p
        return self._atr_value

    def update(self, bar: Bar) -> IndicatorSnapshot:
        """Feed exactly one new bar in. Must be called in chronological order."""
        self.history_len += 1
        return IndicatorSnapshot(
            ema9=self._ema9.update(bar.close),
            ema21=self._ema21.update(bar.close),
            ema50=self._ema50.update(bar.close),
            ema200=self._ema200.update(bar.close),
            vwap=self._vwap.update(bar),
            atr=self._update_atr(bar),
            rsi=self._rsi.update(bar.close),
            adx=self._adx.update(bar),
            volume_sma=self._volume_sma.update(Decimal(bar.volume)),
        )
