"""Tests for context/liquidity.py -- Liquidity State (Market Context
Engine Phase 8; see ROADMAP.md's "Market Context Engine (phased)" and
docs/ARCHITECTURE.md's "Market Context Engine" section).

Named test_context_liquidity.py -- no collision exists for this name.

Every scenario here was verified manually against the live module
before being written down as an assertion.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from futures_bot.context import ContextEngine, LiquidityContext, analyze_liquidity, classify_liquidity_ratio
from futures_bot.context.models import LiquidityState
from futures_bot.models import Bar

CT = ZoneInfo("America/Chicago")
START = datetime(2026, 1, 6, 17, 0, tzinfo=CT)


def _bars_with_volume(volumes: list[int], start=None, base=Decimal("5000")) -> list[Bar]:
    start = start or START
    out = []
    for i, vol in enumerate(volumes):
        ts = start + timedelta(minutes=i)
        out.append(Bar(timestamp=ts, open=base, high=base + 2, low=base - 2, close=base, volume=vol))
    return out


class TestClassifyLiquidityRatio:
    @pytest.mark.parametrize(
        "ratio,expected",
        [(0.2, LiquidityState.THIN), (1.0, LiquidityState.NORMAL), (2.0, LiquidityState.DEEP)],
    )
    def test_thresholds(self, ratio, expected):
        assert classify_liquidity_ratio(ratio) is expected

    def test_none_ratio_is_unknown(self):
        assert classify_liquidity_ratio(None) is LiquidityState.UNKNOWN


class TestRelativeVolumeDetection:
    def test_a_volume_spike_relative_to_history_is_deep(self):
        bars = _bars_with_volume([100] * 30 + [500] * 5)
        ctx = analyze_liquidity(bars[-1].timestamp, "MES", "1min", bars, lookback=20)
        assert ctx.state is LiquidityState.DEEP
        assert ctx.volume_ratio > 1.5

    def test_a_volume_drought_relative_to_history_is_thin(self):
        bars = _bars_with_volume([300] * 30 + [20] * 5)
        ctx = analyze_liquidity(bars[-1].timestamp, "MES", "1min", bars, lookback=20)
        assert ctx.state is LiquidityState.THIN
        assert ctx.volume_ratio < 0.5

    def test_constant_volume_is_normal(self):
        bars = _bars_with_volume([200] * 30)
        ctx = analyze_liquidity(bars[-1].timestamp, "MES", "1min", bars, lookback=20)
        assert ctx.state is LiquidityState.NORMAL
        assert ctx.volume_ratio == pytest.approx(1.0)


class TestConfidenceCalculationWorks:
    def test_confidence_is_always_in_valid_range(self):
        for volumes in ([100] * 30 + [500] * 5, [300] * 30 + [20] * 5, [200] * 30):
            bars = _bars_with_volume(volumes)
            ctx = analyze_liquidity(bars[-1].timestamp, "MES", "1min", bars, lookback=20)
            assert 0.0 <= ctx.confidence <= 1.0

    def test_unknown_state_has_zero_confidence(self):
        ctx = analyze_liquidity(START, "MES", "1min", [])
        assert ctx.state is LiquidityState.UNKNOWN
        assert ctx.confidence == 0.0


class TestMissingData:
    def test_no_bars_is_unknown_not_an_error(self):
        ctx = analyze_liquidity(START, "MES", "1min", [])
        assert ctx.state is LiquidityState.UNKNOWN
        assert ctx.current_volume is None
        assert ctx.average_volume is None
        assert ctx.volume_ratio is None

    def test_all_zero_volume_does_not_crash(self):
        bars = _bars_with_volume([0, 0, 0, 0])
        ctx = analyze_liquidity(bars[-1].timestamp, "MES", "1min", bars, lookback=4)
        # average_volume is genuinely 0 here -- no ratio can be formed,
        # a safe UNKNOWN rather than a division-by-zero crash.
        assert ctx.average_volume == 0.0
        assert ctx.volume_ratio is None
        assert ctx.state is LiquidityState.UNKNOWN

    def test_zero_history_with_a_nonzero_current_bar_is_deep(self):
        bars = _bars_with_volume([0, 0, 0, 100])
        ctx = analyze_liquidity(bars[-1].timestamp, "MES", "1min", bars, lookback=4)
        # The trailing average is current-inclusive (same shape as
        # volatility.py), so it's (0+0+0+100)/4 = 25, not zero.
        assert ctx.average_volume == pytest.approx(25.0)
        assert ctx.volume_ratio == pytest.approx(4.0)
        assert ctx.state is LiquidityState.DEEP

    def test_context_engine_handles_no_bars_gracefully(self):
        engine = ContextEngine(symbol="MES", timeframe="1min")
        ctx = engine.build_context(timestamp=START)
        assert ctx.liquidity_context is not None
        assert ctx.liquidity_state is LiquidityState.UNKNOWN
        assert "liquidity" not in ctx.confidence_scores


class TestNoFutureDataLeakage:
    def test_a_shorter_prefix_is_unaffected_by_bars_appended_after_it(self):
        low = _bars_with_volume([100] * 30)
        future = _bars_with_volume([900] * 10, start=low[-1].timestamp + timedelta(minutes=1))
        full = low + future

        as_of_prefix = analyze_liquidity(low[-1].timestamp, "MES", "1min", low, lookback=20)
        as_of_prefix_again = analyze_liquidity(low[-1].timestamp, "MES", "1min", full[: len(low)], lookback=20)
        assert as_of_prefix.state == as_of_prefix_again.state
        assert as_of_prefix.volume_ratio == as_of_prefix_again.volume_ratio
        assert as_of_prefix.state is LiquidityState.NORMAL

        as_of_now = analyze_liquidity(full[-1].timestamp, "MES", "1min", full, lookback=20)
        assert as_of_now.state is LiquidityState.DEEP


class TestTrailingOnlyOptimizationPreservesOutput:
    """Phase 8, Part 5 performance fix: analyze_liquidity only converts
    the trailing `lookback` bars to Decimal instead of the entire
    history. Must produce bit-identical results to computing over the
    full history, since sma() only ever used the trailing slice
    anyway -- this is a pure efficiency change, not a behavior change."""

    def test_large_history_gives_the_same_result_as_a_manually_pre_sliced_trailing_window(self):
        volumes = [100 + (i % 37) * 3 for i in range(5000)] + [800] * 5
        bars = _bars_with_volume(volumes)
        lookback = 20

        full_history_result = analyze_liquidity(bars[-1].timestamp, "MES", "1min", bars, lookback=lookback)
        pre_sliced_result = analyze_liquidity(
            bars[-1].timestamp, "MES", "1min", bars[-lookback:], lookback=lookback,
        )
        assert full_history_result.average_volume == pre_sliced_result.average_volume
        assert full_history_result.volume_ratio == pre_sliced_result.volume_ratio
        assert full_history_result.state == pre_sliced_result.state
        assert full_history_result.confidence == pre_sliced_result.confidence


class TestLiquidityContextSerialization:
    def test_to_dict_round_trips_through_from_dict(self):
        bars = _bars_with_volume([200] * 30)
        original = analyze_liquidity(bars[-1].timestamp, "MES", "1min", bars)
        restored = LiquidityContext.from_dict(original.to_dict())
        assert restored == original


class TestIntegratedIntoMarketContext:
    def test_context_engine_wires_liquidity_context_through(self):
        engine = ContextEngine(symbol="MES", timeframe="1min")
        bars = _bars_with_volume([100] * 30 + [500] * 5)
        ctx = engine.build_context(timestamp=bars[-1].timestamp, bars=bars)
        assert ctx.liquidity_context is not None
        assert ctx.liquidity_state is ctx.liquidity_context.state
        assert ctx.liquidity_state is LiquidityState.DEEP
        assert "liquidity" in ctx.confidence_scores

    def test_market_context_to_dict_includes_nested_liquidity_context(self):
        engine = ContextEngine(symbol="MES", timeframe="1min")
        bars = _bars_with_volume([200] * 30)
        ctx = engine.build_context(timestamp=bars[-1].timestamp, bars=bars)
        d = ctx.to_dict()
        assert d["liquidity_context"]["state"] == ctx.liquidity_state.value

    def test_market_context_from_dict_restores_liquidity_context(self):
        from futures_bot.context import MarketContext

        engine = ContextEngine(symbol="MES", timeframe="1min")
        bars = _bars_with_volume([200] * 30)
        original = engine.build_context(timestamp=bars[-1].timestamp, bars=bars)
        restored = MarketContext.from_dict(original.to_dict())
        assert restored == original
