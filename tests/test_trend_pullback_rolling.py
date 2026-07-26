"""Rolling indicator tests.

The critical property here is that the incremental engine agrees exactly
with the batch functions in strategy/indicators.py when fed the same history
one bar at a time -- a rolling indicator that silently drifts from its batch
equivalent is worse than an obviously broken one, because it looks fine in
small tests and only disagrees once enough bars accumulate for float/Decimal
drift or an off-by-one seed to show up.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from decimal import Decimal

from futures_bot.contracts import CME_TZ
from futures_bot.models import Bar
from futures_bot.strategy.indicators import adx as batch_adx
from futures_bot.strategy.indicators import atr as batch_atr
from futures_bot.strategy.indicators import ema as batch_ema
from futures_bot.strategy.indicators import rsi as batch_rsi
from futures_bot.strategy.trend_pullback.rolling import RollingIndicators


def _make_bars(n: int, seed: int = 1) -> list[Bar]:
    rng = random.Random(seed)
    start = datetime(2026, 6, 1, 8, 30, tzinfo=CME_TZ)
    bars = []
    price = 7500.0
    for i in range(n):
        price += rng.gauss(0, 1.5)
        o = price
        c = price + rng.gauss(0, 0.5)
        h = max(o, c) + abs(rng.gauss(0, 0.5))
        low = min(o, c) - abs(rng.gauss(0, 0.5))
        bars.append(
            Bar(
                timestamp=start + timedelta(minutes=5 * i),
                open=Decimal(str(round(o, 2))),
                high=Decimal(str(round(h, 2))),
                low=Decimal(str(round(low, 2))),
                close=Decimal(str(round(c, 2))),
                volume=rng.randint(200, 3000),
            )
        )
        price = c
    return bars


class TestRollingMatchesBatch:
    """The regression test that caught two real bugs during development:
    an off-by-one in the incremental ATR seed, and a batch RSI that
    re-seeded from the trailing window instead of smoothing forward through
    all of history. Both are fixed; this test is what would catch either
    coming back.
    """

    def test_ema_matches_batch_bar_for_bar(self):
        bars = _make_bars(150)
        roll = RollingIndicators()
        for i, bar in enumerate(bars):
            snap = roll.update(bar)
            closes = [b.close for b in bars[: i + 1]]
            if snap.ema9 is not None:
                assert abs(snap.ema9 - batch_ema(closes, 9)) < Decimal("0.0000001")
            if snap.ema21 is not None:
                assert abs(snap.ema21 - batch_ema(closes, 21)) < Decimal("0.0000001")

    def test_rsi_matches_batch_bar_for_bar(self):
        bars = _make_bars(150)
        roll = RollingIndicators()
        checked = 0
        for i, bar in enumerate(bars):
            snap = roll.update(bar)
            if snap.rsi is not None:
                closes = [b.close for b in bars[: i + 1]]
                batch_val = batch_rsi(closes, 14)
                assert batch_val is not None
                assert abs(snap.rsi - batch_val) < Decimal("0.0001")
                checked += 1
        assert checked > 100, "sanity check: RSI should have been active for most of the series"

    def test_adx_matches_batch_bar_for_bar(self):
        bars = _make_bars(150)
        roll = RollingIndicators()
        checked = 0
        for i, bar in enumerate(bars):
            snap = roll.update(bar)
            if snap.adx is not None:
                history = bars[: i + 1]
                batch_val = batch_adx(history, 14)
                assert batch_val is not None
                assert abs(snap.adx - batch_val) < Decimal("0.0001")
                checked += 1
        assert checked > 100

    def test_atr_matches_batch_bar_for_bar(self):
        bars = _make_bars(150)
        roll = RollingIndicators()
        checked = 0
        for i, bar in enumerate(bars):
            snap = roll.update(bar)
            if snap.atr is not None:
                history = bars[: i + 1]
                batch_val = batch_atr(history, 14)
                assert batch_val is not None
                assert abs(snap.atr - batch_val) < Decimal("0.0001")
                checked += 1
        assert checked > 100


class TestRollingBehavior:
    def test_snapshot_not_ready_during_warmup(self):
        bars = _make_bars(5)
        roll = RollingIndicators()
        for bar in bars:
            snap = roll.update(bar)
        assert not snap.ready  # 5 bars is nowhere near enough for EMA200/ADX

    def test_snapshot_ready_after_full_warmup(self):
        bars = _make_bars(250)
        roll = RollingIndicators()
        snap = None
        for bar in bars:
            snap = roll.update(bar)
        assert snap.ready

    def test_vwap_resets_on_new_session(self):
        """VWAP must reset at the CME session boundary, not accumulate forever."""
        roll = RollingIndicators()
        day1_start = datetime(2026, 7, 20, 8, 30, tzinfo=CME_TZ)
        day1_bars = [
            Bar(
                timestamp=day1_start + timedelta(minutes=5 * i),
                open=Decimal("7500"), high=Decimal("7505"), low=Decimal("7495"),
                close=Decimal("7500"), volume=1000,
            )
            for i in range(10)
        ]
        for b in day1_bars:
            roll.update(b)
        vwap_day1_end = roll.update(day1_bars[-1]).vwap

        # A very different price on day 2 should NOT be dragged toward day 1's VWAP.
        day2_start = datetime(2026, 7, 21, 8, 30, tzinfo=CME_TZ)
        day2_bar = Bar(
            timestamp=day2_start, open=Decimal("8000"), high=Decimal("8005"),
            low=Decimal("7995"), close=Decimal("8000"), volume=1000,
        )
        snap = roll.update(day2_bar)
        assert snap.vwap == Decimal("8000"), (
            f"VWAP should reset to the new session's own price, got {snap.vwap} "
            f"(day1 end was {vwap_day1_end})"
        )

    def test_rejects_non_positive_period(self):
        import pytest
        from futures_bot.strategy.trend_pullback.rolling import _EMA

        with pytest.raises(ValueError):
            _EMA(0)


class TestRollingPerformance:
    def test_significantly_faster_than_naive_batch_recompute(self):
        """Not a strict timing assertion (flaky on shared CI hardware) --
        just confirms the incremental engine finishes a few thousand bars
        fast enough that it clearly isn't doing O(n) work per bar."""
        import time

        bars = _make_bars(3000)
        roll = RollingIndicators()
        t0 = time.perf_counter()
        for b in bars:
            roll.update(b)
        elapsed = time.perf_counter() - t0
        assert elapsed < 2.0, f"3000 bars took {elapsed:.2f}s -- expected well under 2s for O(1)/bar"
