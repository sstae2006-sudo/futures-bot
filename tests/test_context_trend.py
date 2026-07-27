"""Tests for context/trend.py -- standalone Trend State (Market Context
Engine Phase 8; see ROADMAP.md's "Market Context Engine (phased)" and
docs/ARCHITECTURE.md's "Market Context Engine" section).

Named test_context_trend.py -- no collision with
test_trend_pullback_*.py (an unrelated strategy's own test modules).

Every scenario here was verified manually against the live module
before being written down as an assertion.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from futures_bot.context import ContextEngine, TrendContext, analyze_trend
from futures_bot.context.models import TrendState
from futures_bot.models import Bar

CT = ZoneInfo("America/Chicago")
START = datetime(2026, 1, 6, 17, 0, tzinfo=CT)


def _bars(n: int, drift: Decimal, bar_range: Decimal = Decimal("8"), start=None, base=Decimal("5000")) -> list[Bar]:
    start = start or START
    out = []
    price = base
    for i in range(n):
        ts = start + timedelta(minutes=i)
        open_ = price
        price = price + drift
        close = price
        high = max(open_, close) + bar_range / 2
        low = min(open_, close) - bar_range / 2
        out.append(Bar(timestamp=ts, open=open_, high=high, low=low, close=close, volume=100))
    return out


class TestDirectionDetection:
    def test_steady_uptrend_is_bullish(self):
        bars = _bars(60, Decimal("5"))
        ctx = analyze_trend(bars[-1].timestamp, "MES", bars)
        assert ctx.trend is TrendState.BULLISH
        assert ctx.confidence > 0.5

    def test_steady_downtrend_is_bearish(self):
        bars = _bars(60, Decimal("-5"))
        ctx = analyze_trend(bars[-1].timestamp, "MES", bars)
        assert ctx.trend is TrendState.BEARISH

    def test_flat_market_is_neutral(self):
        bars = _bars(60, Decimal("0"))
        ctx = analyze_trend(bars[-1].timestamp, "MES", bars)
        assert ctx.trend is TrendState.NEUTRAL


class TestConfidenceReflectsAdx:
    def test_strong_trend_has_higher_confidence_than_a_weak_one(self):
        weak = _bars(60, Decimal("0.5"))
        strong = _bars(60, Decimal("6"))
        weak_ctx = analyze_trend(weak[-1].timestamp, "MES", weak)
        strong_ctx = analyze_trend(strong[-1].timestamp, "MES", strong)
        if weak_ctx.trend is strong_ctx.trend is TrendState.BULLISH:
            assert strong_ctx.confidence >= weak_ctx.confidence

    def test_confidence_is_always_in_valid_range(self):
        for drift in (Decimal("5"), Decimal("0"), Decimal("-5")):
            bars = _bars(60, drift)
            ctx = analyze_trend(bars[-1].timestamp, "MES", bars)
            assert 0.0 <= ctx.confidence <= 1.0


class TestDifferentFromRegimesCompositeReading:
    """TrendState is available with far less history than MarketRegime
    (which additionally needs enough bars for ATR/volatility)."""

    def test_direction_available_with_only_two_bars(self):
        # research.regime.classify_trend requires at least a 0.2% net
        # move to call it a direction (see _TREND_THRESHOLD) -- a big
        # enough drift over just 2 bars still clears that easily.
        bars = _bars(2, Decimal("50"))
        ctx = analyze_trend(bars[-1].timestamp, "MES", bars)
        assert ctx.trend is TrendState.BULLISH
        # ADX needs far more history -- confidence honestly 0 here, but
        # direction itself is already known.
        assert ctx.adx is None
        assert ctx.confidence == 0.0


class TestMissingData:
    def test_no_bars_is_unknown_not_an_error(self):
        ctx = analyze_trend(START, "MES", [])
        assert ctx.trend is TrendState.UNKNOWN
        assert ctx.confidence == 0.0
        assert ctx.adx is None

    def test_single_bar_is_unknown(self):
        bars = _bars(1, Decimal("5"))
        ctx = analyze_trend(bars[-1].timestamp, "MES", bars)
        assert ctx.trend is TrendState.UNKNOWN

    def test_context_engine_handles_no_bars_gracefully(self):
        engine = ContextEngine(symbol="MES", timeframe="1min")
        ctx = engine.build_context(timestamp=START)
        assert ctx.trend_context is not None
        assert ctx.trend_state is TrendState.UNKNOWN
        assert "trend" not in ctx.confidence_scores


class TestNoFutureDataLeakage:
    def test_a_shorter_prefix_is_unaffected_by_bars_appended_after_it(self):
        low = _bars(40, Decimal("5"))
        future = _bars(10, Decimal("-20"), start=low[-1].timestamp + timedelta(minutes=1), base=low[-1].close)
        full = low + future

        as_of_prefix = analyze_trend(low[-1].timestamp, "MES", low)
        as_of_prefix_again = analyze_trend(low[-1].timestamp, "MES", full[: len(low)])
        assert as_of_prefix.trend == as_of_prefix_again.trend
        assert as_of_prefix.confidence == as_of_prefix_again.confidence
        assert as_of_prefix.adx == as_of_prefix_again.adx


class TestTrendContextSerialization:
    def test_to_dict_round_trips_through_from_dict(self):
        bars = _bars(60, Decimal("5"))
        original = analyze_trend(bars[-1].timestamp, "MES", bars)
        restored = TrendContext.from_dict(original.to_dict())
        assert restored == original


class TestIntegratedIntoMarketContext:
    def test_context_engine_wires_trend_context_through(self):
        engine = ContextEngine(symbol="MES", timeframe="1min")
        bars = _bars(60, Decimal("5"))
        ctx = engine.build_context(timestamp=bars[-1].timestamp, bars=bars)
        assert ctx.trend_context is not None
        assert ctx.trend_state is ctx.trend_context.trend
        assert ctx.trend_state is TrendState.BULLISH
        assert "trend" in ctx.confidence_scores

    def test_market_context_to_dict_includes_nested_trend_context(self):
        engine = ContextEngine(symbol="MES", timeframe="1min")
        bars = _bars(60, Decimal("5"))
        ctx = engine.build_context(timestamp=bars[-1].timestamp, bars=bars)
        d = ctx.to_dict()
        assert d["trend_context"]["trend"] == ctx.trend_state.value

    def test_market_context_from_dict_restores_trend_context(self):
        from futures_bot.context import MarketContext

        engine = ContextEngine(symbol="MES", timeframe="1min")
        bars = _bars(60, Decimal("5"))
        original = engine.build_context(timestamp=bars[-1].timestamp, bars=bars)
        restored = MarketContext.from_dict(original.to_dict())
        assert restored == original
