"""Tests for `market_data.resample.resample_bars`: standard OHLCV
aggregation correctness against a small, hand-built series with a known
answer, plus a real end-to-end check against actual stored MNQ 1-minute
bars (professional-research-pipeline plan Phase 2a, KNOWN_ISSUES.md
ISSUE-045).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from futures_bot.market_data.resample import resample_bars
from futures_bot.models import Bar


def _minute_bar(minute: int, o: int, h: int, lo: int, c: int, v: int) -> Bar:
    return Bar(
        timestamp=datetime(2025, 1, 1, 9, 0, tzinfo=timezone.utc) + timedelta(minutes=minute),
        open=Decimal(o), high=Decimal(h), low=Decimal(lo), close=Decimal(c), volume=v,
    )


class TestResampleBars:
    def test_empty_input_returns_empty(self):
        assert resample_bars([], "5min") == []

    def test_aggregates_five_one_minute_bars_into_one_five_minute_bar(self):
        # Minutes 0-4 (aligned to a clean 5-minute boundary at :00).
        bars = [
            _minute_bar(0, o=100, h=105, lo=99, c=102, v=10),
            _minute_bar(1, o=102, h=110, lo=101, c=108, v=20),
            _minute_bar(2, o=108, h=109, lo=95, c=97, v=15),
            _minute_bar(3, o=97, h=101, lo=90, c=100, v=5),
            _minute_bar(4, o=100, h=103, lo=98, c=103, v=8),
        ]
        result = resample_bars(bars, "5min")
        assert len(result) == 1
        bucket = result[0]
        assert bucket.timestamp == bars[0].timestamp
        assert bucket.open == Decimal(100)   # first bar's open
        assert bucket.high == Decimal(110)   # max high across the group
        assert bucket.low == Decimal(90)     # min low across the group
        assert bucket.close == Decimal(103)  # last bar's close
        assert bucket.volume == 58           # summed volume

    def test_splits_ten_one_minute_bars_into_two_five_minute_buckets(self):
        bars = [_minute_bar(m, o=100 + m, h=100 + m, lo=100 + m, c=100 + m, v=1) for m in range(10)]
        result = resample_bars(bars, "5min")
        assert len(result) == 2
        assert result[0].timestamp == bars[0].timestamp
        assert result[1].timestamp == bars[5].timestamp
        assert result[0].volume == 5
        assert result[1].volume == 5

    def test_bucket_boundaries_are_aligned_to_the_clock_not_the_first_bar(self):
        # Starting at minute 2 (not a clean 5-minute boundary): bars land
        # in the [0,5) and [5,10) buckets, not a bucket seeded from minute 2.
        bars = [_minute_bar(m, o=1, h=1, lo=1, c=1, v=1) for m in (2, 3, 6, 7)]
        result = resample_bars(bars, "5min")
        assert len(result) == 2
        assert result[0].timestamp == datetime(2025, 1, 1, 9, 0, tzinfo=timezone.utc)
        assert result[1].timestamp == datetime(2025, 1, 1, 9, 5, tzinfo=timezone.utc)

    def test_rejects_a_daily_target_resolution(self):
        bars = [_minute_bar(0, 1, 1, 1, 1, 1)]
        with pytest.raises(ValueError, match="sub-daily"):
            resample_bars(bars, "1day")

    def test_rejects_upsampling_to_a_finer_resolution(self):
        five_min_bars = [
            Bar(
                timestamp=datetime(2025, 1, 1, 9, 0, tzinfo=timezone.utc) + timedelta(minutes=5 * i),
                open=Decimal(1), high=Decimal(1), low=Decimal(1), close=Decimal(1), volume=1,
            )
            for i in range(3)
        ]
        with pytest.raises(ValueError, match="coarser"):
            resample_bars(five_min_bars, "1min")

    def test_rejects_resampling_to_the_same_resolution(self):
        five_min_bars = [
            Bar(
                timestamp=datetime(2025, 1, 1, 9, 0, tzinfo=timezone.utc) + timedelta(minutes=5 * i),
                open=Decimal(1), high=Decimal(1), low=Decimal(1), close=Decimal(1), volume=1,
            )
            for i in range(3)
        ]
        with pytest.raises(ValueError, match="coarser"):
            resample_bars(five_min_bars, "5min")


class TestResampleBarsAgainstRealData:
    """End-to-end spot-check against actual stored bars, not a synthetic
    series -- confirms the aggregation matches a manual calculation on
    real MNQ 1-minute data, not just the hand-built cases above."""

    def test_resampled_mnq_5min_matches_a_manual_calculation(self):
        pytest.importorskip("futures_bot.market_data.store")
        from futures_bot.backtest.data import load_bars_from_db

        one_min, report = load_bars_from_db("MNQ", "1min", end=None)
        if report.bars_loaded < 10:
            pytest.skip("market_data.db not populated with MNQ 1min bars on this machine")

        # Take the first 15 minutes of real data (3 clean 5-minute buckets)
        # and manually compute the expected first bucket.
        window = one_min[:15]
        result = resample_bars(window, "5min")
        assert len(result) == 3

        first_group = window[:5]
        expected_open = first_group[0].open
        expected_high = max(b.high for b in first_group)
        expected_low = min(b.low for b in first_group)
        expected_close = first_group[-1].close
        expected_volume = sum(b.volume for b in first_group)

        assert result[0].open == expected_open
        assert result[0].high == expected_high
        assert result[0].low == expected_low
        assert result[0].close == expected_close
        assert result[0].volume == expected_volume


class TestLoadBarsFromDbResampleFrom:
    """`backtest.data.load_bars_from_db`'s optional `resample_from`
    passthrough -- an explicit, caller-opted-in choice to fetch a finer
    stored resolution and resample it locally instead of the requested
    one, with zero effect on any caller that doesn't pass it."""

    def test_omitting_resample_from_is_unaffected(self):
        from futures_bot.backtest.data import load_bars_from_db

        native, native_report = load_bars_from_db("MES", "5min")
        assert native_report.bars_loaded == len(native)

    def test_resample_from_produces_five_minute_aligned_bars(self):
        pytest.importorskip("futures_bot.market_data.store")
        from futures_bot.backtest.data import load_bars_from_db

        native_1min, native_report = load_bars_from_db("MNQ", "1min")
        if native_report.bars_loaded < 10:
            pytest.skip("market_data.db not populated with MNQ 1min bars on this machine")

        resampled, resampled_report = load_bars_from_db("MNQ", "5min", resample_from="1min")
        assert resampled_report.bars_loaded == len(resampled)
        assert 0 < len(resampled) < len(native_1min)
        # Every bucket must land on a real 5-minute boundary.
        assert all(bar.timestamp.minute % 5 == 0 and bar.timestamp.second == 0 for bar in resampled[:20])
