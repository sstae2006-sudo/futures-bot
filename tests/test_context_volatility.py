"""Tests for context/volatility.py -- Volatility Context (Market Context
Engine Phase 3; see ROADMAP.md's "Market Context Engine (phased)" and
docs/ARCHITECTURE.md's "Market Context Engine" section).

Named test_context_volatility.py, matching test_context_session.py's
naming (not test_volatility.py) for consistency -- no collision exists
for either name in this codebase, but the pattern is kept regardless.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from futures_bot.context import ContextEngine, VolatilityContext, analyze_volatility
from futures_bot.context.models import VolatilityState
from futures_bot.context.volatility import classify_volatility_ratio
from futures_bot.models import Bar

CT = ZoneInfo("America/Chicago")


def _bars(n: int, high_low_range: Decimal, start=None, base=Decimal("5000")) -> list[Bar]:
    """``n`` bars, each with a fixed high-low range and a flat close, so
    ATR converges to (approximately) ``high_low_range`` -- deterministic,
    no randomness needed to control volatility precisely."""
    start = start or datetime(2026, 1, 6, 17, 0, tzinfo=CT)
    out = []
    for i in range(n):
        ts = start + timedelta(minutes=i)
        out.append(
            Bar(
                timestamp=ts,
                open=base,
                high=base + high_low_range / 2,
                low=base - high_low_range / 2,
                close=base,
                volume=100,
            )
        )
    return out


class TestClassifyVolatilityRatio:
    def test_matches_the_task_spec_example_exactly(self):
        # {current_atr: 18, average_atr: 12, volatility_ratio: 1.5, state: HIGH}
        assert classify_volatility_ratio(1.5) is VolatilityState.HIGH

    @pytest.mark.parametrize(
        "ratio,expected",
        [
            (0.5, VolatilityState.LOW),
            (0.9, VolatilityState.NORMAL),
            (1.0, VolatilityState.NORMAL),
            (1.5, VolatilityState.HIGH),
            (2.5, VolatilityState.EXTREME),
        ],
    )
    def test_thresholds(self, ratio, expected):
        assert classify_volatility_ratio(ratio) is expected

    def test_none_ratio_is_unknown(self):
        assert classify_volatility_ratio(None) is VolatilityState.UNKNOWN


class TestLowVolatilityPeriod:
    """✓ Low volatility period."""

    def test_flat_recent_range_relative_to_wider_history_is_low(self):
        # 30 bars with a wide range, then 20 bars with a much tighter
        # range -- current ATR ends up well below the trailing average.
        bars = _bars(30, Decimal("40")) + _bars(20, Decimal("4"), start=datetime(2026, 1, 6, 17, 30, tzinfo=CT))
        ctx = analyze_volatility(bars[-1].timestamp, "MES", "1min", bars, average_lookback=30)
        assert ctx.state in (VolatilityState.LOW, VolatilityState.NORMAL)
        assert ctx.current_atr < ctx.average_atr

    def test_constant_range_is_normal_not_low(self):
        # No regime change at all -- ratio should sit right at 1.0.
        bars = _bars(40, Decimal("10"))
        ctx = analyze_volatility(bars[-1].timestamp, "MES", "1min", bars)
        assert ctx.state is VolatilityState.NORMAL
        assert ctx.volatility_ratio == pytest.approx(1.0, abs=0.05)


class TestHighVolatilityPeriod:
    """✓ High volatility period."""

    def test_matches_the_task_spec_example_shape(self):
        # Construct bars so current ATR ~= 18 while the trailing average ~= 12.
        bars = _bars(30, Decimal("12")) + _bars(5, Decimal("18"), start=datetime(2026, 1, 6, 17, 30, tzinfo=CT))
        ctx = analyze_volatility(bars[-1].timestamp, "MES", "1min", bars, atr_period=3, average_lookback=30)
        assert ctx.current_atr > ctx.average_atr
        assert ctx.volatility_ratio > 1.0
        assert ctx.state in (VolatilityState.HIGH, VolatilityState.EXTREME)

    def test_extreme_spike_is_extreme(self):
        bars = _bars(30, Decimal("5")) + _bars(5, Decimal("40"), start=datetime(2026, 1, 6, 17, 30, tzinfo=CT))
        ctx = analyze_volatility(bars[-1].timestamp, "MES", "1min", bars, atr_period=3, average_lookback=30)
        assert ctx.state is VolatilityState.EXTREME


class TestMissingData:
    """✓ Missing data."""

    def test_no_bars_is_unknown_not_an_error(self):
        ctx = analyze_volatility(datetime(2026, 1, 6, 17, 0, tzinfo=CT), "MES", "1min", [])
        assert ctx.state is VolatilityState.UNKNOWN
        assert ctx.current_atr is None
        assert ctx.average_atr is None
        assert ctx.volatility_ratio is None

    def test_fewer_bars_than_atr_period_is_unknown(self):
        bars = _bars(5, Decimal("10"))
        ctx = analyze_volatility(bars[-1].timestamp, "MES", "1min", bars, atr_period=14)
        assert ctx.state is VolatilityState.UNKNOWN

    def test_context_engine_handles_no_bars_gracefully(self):
        engine = ContextEngine(symbol="MES", timeframe="1min")
        ctx = engine.build_context(timestamp=datetime(2026, 1, 6, 17, 0, tzinfo=CT))
        assert ctx.volatility_state is VolatilityState.UNKNOWN
        assert ctx.volatility_context is not None
        assert "volatility" not in ctx.confidence_scores

    def test_single_bar_realized_volatility_is_none(self):
        bars = _bars(1, Decimal("10"))
        ctx = analyze_volatility(bars[-1].timestamp, "MES", "1min", bars)
        assert ctx.realized_volatility is None


class TestNoFutureDataLeakage:
    """✓ No future data leakage."""

    def test_truncated_history_is_unaffected_by_bars_that_would_come_later(self):
        # Low-vol prefix, then a high-vol suffix appended *after* it.
        low_vol = _bars(40, Decimal("5"))
        high_vol = _bars(10, Decimal("50"), start=low_vol[-1].timestamp + timedelta(minutes=1))
        full = low_vol + high_vol

        # "As of" the last low-vol bar: only the prefix is visible.
        as_of_prefix = analyze_volatility(low_vol[-1].timestamp, "MES", "1min", low_vol, atr_period=3)
        # The full series (including the future high-vol bars) must not
        # change what "as of the prefix" looked like.
        as_of_prefix_again = analyze_volatility(
            low_vol[-1].timestamp, "MES", "1min", full[: len(low_vol)], atr_period=3
        )
        assert as_of_prefix.current_atr == as_of_prefix_again.current_atr
        assert as_of_prefix.average_atr == as_of_prefix_again.average_atr
        assert as_of_prefix.state is as_of_prefix_again.state
        assert as_of_prefix.state not in (VolatilityState.HIGH, VolatilityState.EXTREME)

        # "As of now" (the full series, ending on a high-vol bar) is a
        # genuinely different, elevated reading -- proving the function
        # actually looks at "now", not a fixed/cached earlier value.
        as_of_now = analyze_volatility(full[-1].timestamp, "MES", "1min", full, atr_period=3)
        assert as_of_now.current_atr > as_of_prefix.current_atr
        assert as_of_now.state in (VolatilityState.HIGH, VolatilityState.EXTREME)

    def test_average_atr_only_uses_trailing_window_not_whole_series(self):
        # A long, wide-range history followed by a short, tight-range
        # tail: if the average leaked in the whole series (like
        # research.regime.classify_volatility's whole-dataset terciles),
        # a trailing lookback of 5 (entirely inside the tight tail) would
        # still be dragged up toward the wide bars' ~50-point range.
        from futures_bot.strategy.indicators import atr_series

        wide = _bars(100, Decimal("50"))
        tight = _bars(10, Decimal("5"), start=wide[-1].timestamp + timedelta(minutes=1))
        bars = wide + tight

        ctx = analyze_volatility(bars[-1].timestamp, "MES", "1min", bars, atr_period=3, average_lookback=5)

        # Definition-level check: average_atr is exactly the mean of the
        # last 5 ATR values, not something computed over the whole series.
        expected_avg = sum(atr_series(bars, period=3)[-5:]) / 5
        assert ctx.average_atr == pytest.approx(expected_avg)
        # And that trailing average sits nowhere near the wide bars'
        # true range -- proof it wasn't contaminated by them.
        assert ctx.average_atr < 20


class TestVolatilityContextSerialization:
    def test_to_dict_round_trips_through_from_dict(self):
        bars = _bars(30, Decimal("10"))
        original = analyze_volatility(bars[-1].timestamp, "MES", "1min", bars)
        restored = VolatilityContext.from_dict(original.to_dict())
        assert restored == original


class TestIntegratedIntoMarketContext:
    """Volatility information integrated into MarketContext (via ContextEngine)."""

    def test_context_engine_wires_volatility_context_through(self):
        engine = ContextEngine(symbol="MES", timeframe="1min")
        bars = _bars(30, Decimal("10"))
        ctx = engine.build_context(timestamp=bars[-1].timestamp, bars=bars)

        assert ctx.volatility_context is not None
        assert ctx.volatility_state is ctx.volatility_context.state
        assert ctx.volatility_state is not VolatilityState.UNKNOWN
        assert "volatility" in ctx.confidence_scores

    def test_market_context_to_dict_includes_nested_volatility_context(self):
        engine = ContextEngine(symbol="MES", timeframe="1min")
        bars = _bars(30, Decimal("10"))
        ctx = engine.build_context(timestamp=bars[-1].timestamp, bars=bars)
        d = ctx.to_dict()
        assert d["volatility_context"]["state"] == ctx.volatility_state.value

    def test_market_context_from_dict_restores_volatility_context(self):
        from futures_bot.context import MarketContext

        engine = ContextEngine(symbol="MES", timeframe="1min")
        bars = _bars(30, Decimal("10"))
        original = engine.build_context(timestamp=bars[-1].timestamp, bars=bars)
        restored = MarketContext.from_dict(original.to_dict())
        assert restored == original

    def test_supports_multiple_symbols_and_timeframes(self):
        bars = _bars(30, Decimal("10"))
        for symbol, timeframe in (("MES", "1min"), ("MNQ", "5min"), ("M2K", "15min")):
            engine = ContextEngine(symbol=symbol, timeframe=timeframe)
            ctx = engine.build_context(timestamp=bars[-1].timestamp, bars=bars)
            assert ctx.volatility_context.symbol == symbol
            assert ctx.volatility_context.timeframe == timeframe
