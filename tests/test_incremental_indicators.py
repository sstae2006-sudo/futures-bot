"""Equivalence tests for Phase 4's incremental indicators.

Every strategy touched in Phase 4 (`ema_crossover`, `vwap_reversion`,
`opening_range_breakout`) switched from recomputing indicators over the full
bar history on every call to maintaining running state one bar at a time (see
`strategy/indicators.py`'s `IncrementalEMA` / `IncrementalSessionVWAP`, and
`OpeningRangeBreakout`'s own `_range_high`/`_range_low` tracking). The
strategy logic itself did not change -- only how the numbers feeding it are
computed -- so this file's job is to prove the two computation paths agree,
bar for bar, rather than re-test trading logic already covered by
`test_strategies.py`.
"""

from __future__ import annotations

import random
import time
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from futures_bot.contracts import CME_TZ, MES
from futures_bot.models import Bar
from futures_bot.strategy.indicators import (
    IncrementalEMA,
    IncrementalSessionVWAP,
    ema_series,
    session_bars,
    vwap_bands,
)
from futures_bot.strategy.opening_range_breakout import OpeningRangeBreakout


def _random_bars(n: int, seed: int, start=None, gap_minutes: int = 1) -> list[Bar]:
    rng = random.Random(seed)
    start = start or datetime(2026, 1, 5, 17, 0, tzinfo=CME_TZ)
    bars = []
    price = Decimal("7500")
    for i in range(n):
        price += Decimal(str(round(rng.uniform(-3, 3), 2)))
        o = price
        h = price + Decimal(str(round(rng.uniform(0, 2), 2)))
        lo = price - Decimal(str(round(rng.uniform(0, 2), 2)))
        c = price + Decimal(str(round(rng.uniform(-1, 1), 2)))
        v = rng.randint(0, 2000)  # includes zero-volume bars on purpose
        bars.append(
            Bar(
                timestamp=start + timedelta(minutes=i * gap_minutes),
                open=o, high=h, low=lo, close=c, volume=v,
            )
        )
    return bars


class TestIncrementalEmaMatchesBatch:
    @pytest.mark.parametrize("period", [3, 8, 21, 34, 200])
    def test_matches_ema_series_bar_for_bar(self, period):
        bars = _random_bars(500, seed=period)
        closes = [b.close for b in bars]
        batch = ema_series(closes, period)

        inc = IncrementalEMA(period, history=1)
        produced = [v for v in (inc.update(c) for c in closes) if v is not None]

        assert produced == batch

    def test_lookback_matches_manual_indexing(self):
        bars = _random_bars(300, seed=99)
        closes = [b.close for b in bars]
        batch = ema_series(closes, 21)

        inc = IncrementalEMA(21, history=5)
        values = []
        for c in closes:
            inc.update(c)
            values.append((inc.lookback(1), inc.lookback(5)))

        # Once the series has at least 5 values, lookback(5) must equal the
        # value 4 batch-index positions back from the current one.
        seeded_at = next(i for i, v in enumerate(values) if v[0] is not None)
        for offset, (now, five_back) in enumerate(values[seeded_at:]):
            batch_index = offset
            assert now == batch[batch_index]
            if batch_index >= 4:
                assert five_back == batch[batch_index - 4]
            else:
                assert five_back is None

    def test_resetting_clears_state(self):
        inc = IncrementalEMA(3)
        for c in (Decimal("10"), Decimal("20"), Decimal("30")):
            inc.update(c)
        assert inc.value is not None
        inc.reset()
        assert inc.value is None
        assert inc.lookback(1) is None

    def test_rejects_nonpositive_period(self):
        with pytest.raises(ValueError):
            IncrementalEMA(0)


class TestIncrementalVwapMatchesBatch:
    def test_matches_vwap_bands_bar_for_bar_within_a_session(self):
        bars = _random_bars(120, seed=7)  # all inside one CME session
        inc = IncrementalSessionVWAP()

        for i, b in enumerate(bars):
            inc.update(b)
            today = session_bars(bars[: i + 1])
            expected = vwap_bands(today, Decimal("1.5"))
            got = inc.bands(Decimal("1.5"))

            if expected is None or got is None:
                assert expected is got
                continue
            for e, g in zip(expected, got):
                assert abs(e - g) < Decimal("0.000001")

    def test_matches_across_a_session_boundary(self):
        # Three CME sessions back to back (17:00 CT resets, not midnight).
        bars = (
            _random_bars(50, seed=1, start=datetime(2026, 1, 5, 17, 0, tzinfo=CME_TZ))
            + _random_bars(50, seed=2, start=datetime(2026, 1, 6, 17, 0, tzinfo=CME_TZ))
            + _random_bars(50, seed=3, start=datetime(2026, 1, 7, 17, 0, tzinfo=CME_TZ))
        )
        inc = IncrementalSessionVWAP()
        for i, b in enumerate(bars):
            inc.update(b)
            today = session_bars(bars[: i + 1])
            expected = vwap_bands(today, Decimal("2"))
            got = inc.bands(Decimal("2"))
            if expected is None or got is None:
                assert expected is got
                continue
            for e, g in zip(expected, got):
                assert abs(e - g) < Decimal("0.000001")

    def test_session_bar_count_resets_on_new_session(self):
        inc = IncrementalSessionVWAP()
        day1 = _random_bars(5, seed=1, start=datetime(2026, 1, 5, 17, 0, tzinfo=CME_TZ))
        for b in day1:
            inc.update(b)
        assert inc.session_bar_count == 5

        day2 = _random_bars(3, seed=2, start=datetime(2026, 1, 6, 17, 0, tzinfo=CME_TZ))
        for b in day2:
            inc.update(b)
        assert inc.session_bar_count == 3

    def test_zero_volume_session_falls_back_to_unweighted_mean(self):
        bars = [
            Bar(
                timestamp=datetime(2026, 1, 5, 17, i, tzinfo=CME_TZ),
                open=Decimal("100"), high=Decimal("100"), low=Decimal("100"),
                close=Decimal(str(100 + i)), volume=0,
            )
            for i in range(10)
        ]
        inc = IncrementalSessionVWAP()
        for b in bars:
            inc.update(b)
        expected = vwap_bands(bars, Decimal("1"))
        got = inc.bands(Decimal("1"))
        for e, g in zip(expected, got):
            assert abs(e - g) < Decimal("0.000001")


class TestOpeningRangeIncrementalMatchesBatch:
    """The strategy's own `_opening_range` (batch: filter + max/min over the
    session) is kept as the reference definition; `_range_high`/`_range_low`
    (incremental: accumulated bar by bar during the window) must agree with
    it once the range window has closed, across many sessions."""

    def test_range_matches_batch_definition_across_sessions(self):
        s = OpeningRangeBreakout(MES, range_minutes=30, trend_period=5)
        rng = random.Random(2026)

        for day in range(5, 15):
            session_start = datetime(2026, 1, day, 8, 30, tzinfo=CME_TZ)
            bars: list[Bar] = []
            price = Decimal("7500")
            all_bars_this_session = []
            for i in range(40):  # 08:30 .. 09:35 in 5-min steps, well past the range window
                price += Decimal(str(round(rng.uniform(-5, 5), 2)))
                b = Bar(
                    timestamp=session_start + timedelta(minutes=i * 5),
                    open=price,
                    high=price + Decimal(str(round(rng.uniform(0, 3), 2))),
                    low=price - Decimal(str(round(rng.uniform(0, 3), 2))),
                    close=price,
                    volume=rng.randint(100, 1000),
                )
                all_bars_this_session.append(b)
                bars.append(b)
                s.on_bar(bars, None)

            sd = session_start.date()
            expected = s._opening_range(all_bars_this_session, sd)
            assert expected is not None, "range window should have bars in this synthetic session"
            assert s._range_high == expected[0]
            assert s._range_low == expected[1]


class TestIncrementalPerformance:
    """Not a correctness test -- documents that the incremental strategies no
    longer degrade quadratically with backtest length. Skipped by default
    (slow); run explicitly with `pytest -m benchmark` to see numbers."""

    @pytest.mark.benchmark
    def test_ema_crossover_backtest_time_grows_linearly_not_quadratically(self):
        from futures_bot.backtest.runner import run_backtest
        from futures_bot.config import BrokerSettings, RiskSettings, Settings
        from futures_bot.strategy.ema_crossover import EmaCrossover

        def settings(tmp_dir):
            return Settings(
                contract="MES", mode="paper",
                risk=RiskSettings(
                    contracts_per_trade=1, stop_loss_points=Decimal("10"),
                    take_profit_points=Decimal("20"), daily_max_loss=Decimal("100000"),
                    max_trades_per_session=10_000, account_size=Decimal("5000"),
                ),
                broker=BrokerSettings(starting_cash=Decimal("5000")),
                logging={"directory": tmp_dir, "level": "WARNING"},
            )

        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            small = _random_bars(2_000, seed=1)
            large = _random_bars(8_000, seed=1)  # 4x the bars

            t0 = time.perf_counter()
            run_backtest(settings(tmp), EmaCrossover(MES), small, journal_dir=tmp)
            small_time = time.perf_counter() - t0

            t0 = time.perf_counter()
            run_backtest(settings(tmp), EmaCrossover(MES), large, journal_dir=tmp)
            large_time = time.perf_counter() - t0

        ratio = large_time / small_time if small_time > 0 else float("inf")
        # O(n) predicts ~4x; a quadratic implementation would show ~16x.
        assert ratio < 10, (
            f"4x the bars took {ratio:.1f}x as long ({small_time:.3f}s -> {large_time:.3f}s); "
            f"that looks quadratic, not linear."
        )
