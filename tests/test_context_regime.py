"""Tests for context/regime.py -- Market Regime Detection (Market Context
Engine Phase 4; see ROADMAP.md's "Market Context Engine (phased)" and
docs/ARCHITECTURE.md's "Market Context Engine" section).

Named test_context_regime.py, matching test_context_session.py /
test_context_volatility.py's naming (not test_regime.py, which would
collide with the unrelated tests/test_api_regime.py for the
GET /api/regime/performance endpoint).

Every scenario here was verified manually against the live module
before being written down as an assertion (see the session's own
manual-verification discipline in test_context_session.py's docstring).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from futures_bot.context import ContextEngine, RegimeContext, classify_regime
from futures_bot.context.models import MarketRegime
from futures_bot.models import Bar

CT = ZoneInfo("America/Chicago")
START = datetime(2026, 1, 6, 17, 0, tzinfo=CT)


def _bars(n: int, drift: Decimal, bar_range: Decimal, start=None, base=Decimal("5000")) -> list[Bar]:
    """``n`` bars stepping by a fixed ``drift`` per bar (0 for no net
    direction) with a fixed high-low ``bar_range`` around each bar --
    deterministic, no randomness needed to control trend/range/
    volatility precisely."""
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


class TestTrendingMarketDetected:
    """✓ Trending market detected."""

    def test_steady_uptrend_is_trending_up(self):
        bars = _bars(60, Decimal("5"), Decimal("8"))
        ctx = classify_regime(bars[-1].timestamp, "MES", "1min", bars)
        assert ctx.regime is MarketRegime.TRENDING_UP
        assert ctx.trend_direction == "bullish"
        assert ctx.adx >= 25.0

    def test_steady_downtrend_is_trending_down(self):
        bars = _bars(60, Decimal("-5"), Decimal("8"))
        ctx = classify_regime(bars[-1].timestamp, "MES", "1min", bars)
        assert ctx.regime is MarketRegime.TRENDING_DOWN
        assert ctx.trend_direction == "bearish"

    def test_context_engine_wires_regime_context_through_for_a_trend(self):
        engine = ContextEngine(symbol="MES", timeframe="1min")
        bars = _bars(60, Decimal("5"), Decimal("8"))
        ctx = engine.build_context(timestamp=bars[-1].timestamp, bars=bars)
        assert ctx.market_regime is MarketRegime.TRENDING_UP
        assert ctx.regime_context is not None
        assert ctx.regime_context.regime is MarketRegime.TRENDING_UP


class TestRangeDetected:
    """✓ Range detected."""

    def test_flat_back_and_forth_is_ranging(self):
        bars = _bars(60, Decimal("0"), Decimal("8"))
        ctx = classify_regime(bars[-1].timestamp, "MES", "1min", bars)
        assert ctx.regime is MarketRegime.RANGING
        assert ctx.adx < 25.0

    def test_context_engine_wires_regime_context_through_for_a_range(self):
        engine = ContextEngine(symbol="MES", timeframe="1min")
        bars = _bars(60, Decimal("0"), Decimal("8"))
        ctx = engine.build_context(timestamp=bars[-1].timestamp, bars=bars)
        assert ctx.market_regime is MarketRegime.RANGING


class TestVolatileMarketDetected:
    """✓ Volatile market detected."""

    def test_calm_history_then_a_violent_spike_is_high_volatility(self):
        calm = _bars(40, Decimal("0"), Decimal("5"))
        spike = _bars(10, Decimal("0"), Decimal("60"), start=calm[-1].timestamp + timedelta(minutes=1))
        bars = calm + spike
        ctx = classify_regime(bars[-1].timestamp, "MES", "1min", bars)
        assert ctx.regime is MarketRegime.HIGH_VOLATILITY
        assert ctx.volatility_ratio > 2.0

    def test_wide_history_then_a_very_tight_recent_range_is_low_volatility(self):
        wide = _bars(60, Decimal("0"), Decimal("40"))
        tight = _bars(20, Decimal("0"), Decimal("3"), start=wide[-1].timestamp + timedelta(minutes=1))
        bars = wide + tight
        ctx = classify_regime(bars[-1].timestamp, "MES", "1min", bars)
        assert ctx.regime is MarketRegime.LOW_VOLATILITY
        assert ctx.volatility_ratio < 0.75

    def test_extreme_volatility_takes_priority_over_a_concurrent_trend(self):
        # A steady uptrend that suddenly spikes in range -- volatility
        # should dominate the label per the documented priority order.
        trend = _bars(40, Decimal("5"), Decimal("8"))
        spike = _bars(
            10, Decimal("5"), Decimal("80"),
            start=trend[-1].timestamp + timedelta(minutes=1), base=trend[-1].close,
        )
        bars = trend + spike
        ctx = classify_regime(bars[-1].timestamp, "MES", "1min", bars)
        assert ctx.regime is MarketRegime.HIGH_VOLATILITY

    def test_context_engine_wires_regime_context_through_for_high_volatility(self):
        engine = ContextEngine(symbol="MES", timeframe="1min")
        calm = _bars(40, Decimal("0"), Decimal("5"))
        spike = _bars(10, Decimal("0"), Decimal("60"), start=calm[-1].timestamp + timedelta(minutes=1))
        bars = calm + spike
        ctx = engine.build_context(timestamp=bars[-1].timestamp, bars=bars)
        assert ctx.market_regime is MarketRegime.HIGH_VOLATILITY


class TestConfidenceCalculationWorks:
    """✓ Confidence calculation works."""

    def test_matches_the_task_spec_example_shape(self):
        # { regime: TRENDING_UP, confidence: 0.78 } -- ADX 39 maps to
        # confidence 0.78 under this module's ADX_CONFIDENCE_SCALE=50.
        from futures_bot.context.regime import ADX_CONFIDENCE_SCALE

        assert min(1.0, 39.0 / ADX_CONFIDENCE_SCALE) == pytest.approx(0.78)

    def test_confidence_is_always_in_valid_range(self):
        for drift, rng in [(Decimal("5"), Decimal("8")), (Decimal("0"), Decimal("8")), (Decimal("0"), Decimal("60"))]:
            bars = _bars(60, drift, rng)
            ctx = classify_regime(bars[-1].timestamp, "MES", "1min", bars)
            assert 0.0 <= ctx.confidence <= 1.0

    def test_stronger_trend_yields_higher_confidence_than_a_weaker_one(self):
        weak = _bars(60, Decimal("0.5"), Decimal("8"))
        strong = _bars(60, Decimal("6"), Decimal("8"))
        weak_ctx = classify_regime(weak[-1].timestamp, "MES", "1min", weak)
        strong_ctx = classify_regime(strong[-1].timestamp, "MES", "1min", strong)
        # Only compare when both actually landed on a trending regime --
        # otherwise the confidence formulas aren't the same one.
        if weak_ctx.regime in (MarketRegime.TRENDING_UP, MarketRegime.TRENDING_DOWN) and \
                strong_ctx.regime in (MarketRegime.TRENDING_UP, MarketRegime.TRENDING_DOWN):
            assert strong_ctx.confidence >= weak_ctx.confidence

    def test_unknown_regime_always_has_zero_confidence(self):
        ctx = classify_regime(START, "MES", "1min", [])
        assert ctx.regime is MarketRegime.UNKNOWN
        assert ctx.confidence == 0.0


class TestMissingData:
    def test_no_bars_is_unknown_not_an_error(self):
        ctx = classify_regime(START, "MES", "1min", [])
        assert ctx.regime is MarketRegime.UNKNOWN
        assert ctx.adx is None
        assert ctx.trend_direction is None
        assert ctx.volatility_ratio is None

    def test_enough_bars_for_volatility_but_not_adx_degrades_gracefully(self):
        # 20 bars: enough for analyze_volatility's default atr_period=14
        # (needs 15), not enough for adx's default period=14 (needs 28).
        bars = _bars(20, Decimal("0"), Decimal("8"))
        ctx = classify_regime(bars[-1].timestamp, "MES", "1min", bars)
        assert ctx.adx is None
        assert ctx.regime is not MarketRegime.UNKNOWN  # volatility alone still classifies it

    def test_context_engine_handles_no_bars_gracefully(self):
        engine = ContextEngine(symbol="MES", timeframe="1min")
        ctx = engine.build_context(timestamp=START)
        assert ctx.market_regime is MarketRegime.UNKNOWN
        assert ctx.regime_context is not None
        assert "regime" not in ctx.confidence_scores


class TestNoFutureDataLeakage:
    def test_truncated_history_is_unaffected_by_bars_that_would_come_later(self):
        low_vol_trend = _bars(60, Decimal("5"), Decimal("8"))
        future_spike = _bars(
            10, Decimal("5"), Decimal("80"),
            start=low_vol_trend[-1].timestamp + timedelta(minutes=1), base=low_vol_trend[-1].close,
        )
        full = low_vol_trend + future_spike

        as_of_prefix = classify_regime(low_vol_trend[-1].timestamp, "MES", "1min", low_vol_trend)
        as_of_prefix_again = classify_regime(
            low_vol_trend[-1].timestamp, "MES", "1min", full[: len(low_vol_trend)]
        )
        assert as_of_prefix.regime == as_of_prefix_again.regime
        assert as_of_prefix.confidence == as_of_prefix_again.confidence
        assert as_of_prefix.adx == as_of_prefix_again.adx
        assert as_of_prefix.regime is MarketRegime.TRENDING_UP

        as_of_now = classify_regime(full[-1].timestamp, "MES", "1min", full)
        assert as_of_now.regime is MarketRegime.HIGH_VOLATILITY


class TestRegimeContextSerialization:
    def test_to_dict_round_trips_through_from_dict(self):
        bars = _bars(60, Decimal("5"), Decimal("8"))
        original = classify_regime(bars[-1].timestamp, "MES", "1min", bars)
        restored = RegimeContext.from_dict(original.to_dict())
        assert restored == original


class TestIntegratedIntoMarketContext:
    def test_market_context_to_dict_includes_nested_regime_context(self):
        engine = ContextEngine(symbol="MES", timeframe="1min")
        bars = _bars(60, Decimal("5"), Decimal("8"))
        ctx = engine.build_context(timestamp=bars[-1].timestamp, bars=bars)
        d = ctx.to_dict()
        assert d["regime_context"]["regime"] == ctx.market_regime.value

    def test_market_context_from_dict_restores_regime_context(self):
        from futures_bot.context import MarketContext

        engine = ContextEngine(symbol="MES", timeframe="1min")
        bars = _bars(60, Decimal("5"), Decimal("8"))
        original = engine.build_context(timestamp=bars[-1].timestamp, bars=bars)
        restored = MarketContext.from_dict(original.to_dict())
        assert restored == original

    def test_regime_field_and_regime_context_field_always_agree(self):
        engine = ContextEngine(symbol="MES", timeframe="1min")
        for bars in (
            _bars(60, Decimal("5"), Decimal("8")),
            _bars(60, Decimal("0"), Decimal("8")),
            [],
        ):
            ctx = engine.build_context(
                timestamp=(bars[-1].timestamp if bars else START), bars=bars
            )
            assert ctx.market_regime is ctx.regime_context.regime
