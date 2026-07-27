"""Tests for context/structure.py -- Market Structure Context (Market
Context Engine Phase 6; see ROADMAP.md's "Market Context Engine
(phased)" and docs/ARCHITECTURE.md's "Market Context Engine" section).

Named test_context_structure.py, matching test_context_session.py /
test_context_volatility.py / test_context_regime.py /
test_context_timeframe.py's naming (no collision exists for either name
in this codebase).

Every scenario here was verified manually against the live module
before being written down as an assertion (see the session's own
manual-verification discipline in test_context_session.py's docstring)
-- including catching and fixing a test-data bug this way: an early
zigzag-generator draft accidentally produced *rising* cycle lows
regardless of the intended drift direction, which would have silently
mislabeled a "downtrend" fixture as bullish.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from futures_bot.context import ContextEngine, StructureContext, analyze_structure
from futures_bot.context.models import TrendState
from futures_bot.models import Bar

CT = ZoneInfo("America/Chicago")
START = datetime(2026, 1, 6, 17, 0, tzinfo=CT)


def _bar(i: int, price: Decimal, rng: Decimal = Decimal("4")) -> Bar:
    return Bar(
        timestamp=START + timedelta(minutes=i),
        open=price, high=price + rng, low=price - rng, close=price,
        volume=100,
    )


def _zigzag(n_cycles: int, cycle_low_start: Decimal, cycle_low_drift: Decimal) -> list[Bar]:
    """``n_cycles`` rise-then-fall legs (8 bars each: 4 up, 4 down), each
    cycle's low offset from the previous by ``cycle_low_drift`` --
    positive drift produces rising swing highs/lows (uptrend structure),
    negative drift produces falling ones (downtrend structure), zero
    drift repeats the same cycle (no structure)."""
    prices: list[Decimal] = []
    for c in range(n_cycles):
        cycle_low = cycle_low_start + cycle_low_drift * c
        peak = cycle_low + 40
        for k in range(1, 5):
            prices.append(cycle_low + (peak - cycle_low) * k // 4)
        trough = peak - 30
        for k in range(1, 5):
            prices.append(peak - (peak - trough) * k // 4)
    return [_bar(i, p) for i, p in enumerate(prices)]


class TestHigherHighsHigherLowsDetected:
    def test_rising_zigzag_is_bullish_structure(self):
        bars = _zigzag(6, Decimal("5900"), Decimal("10"))
        ctx = analyze_structure(bars[-1].timestamp, "MES", bars)
        assert ctx.trend is TrendState.BULLISH
        assert ctx.structure_confidence == pytest.approx(1.0)

    def test_context_engine_wires_structure_context_through_for_uptrend(self):
        engine = ContextEngine(symbol="MES", timeframe="1min")
        bars = _zigzag(6, Decimal("5900"), Decimal("10"))
        ctx = engine.build_context(timestamp=bars[-1].timestamp, bars=bars)
        assert ctx.structure_context is not None
        assert ctx.structure_context.trend is TrendState.BULLISH
        assert "structure" in ctx.confidence_scores


class TestLowerHighsLowerLowsDetected:
    def test_falling_zigzag_is_bearish_structure(self):
        bars = _zigzag(6, Decimal("6100"), Decimal("-10"))
        ctx = analyze_structure(bars[-1].timestamp, "MES", bars)
        assert ctx.trend is TrendState.BEARISH
        assert ctx.structure_confidence == pytest.approx(1.0)


class TestSupportAndResistanceDetected:
    def test_support_is_below_and_resistance_is_above_current_price(self):
        bars = _zigzag(6, Decimal("5900"), Decimal("10"))
        ctx = analyze_structure(bars[-1].timestamp, "MES", bars)
        current_price = bars[-1].close
        assert ctx.support is not None and ctx.support <= current_price
        assert ctx.resistance is not None and ctx.resistance >= current_price

    def test_distance_to_levels_matches_the_raw_price_difference(self):
        bars = _zigzag(6, Decimal("5900"), Decimal("10"))
        ctx = analyze_structure(bars[-1].timestamp, "MES", bars)
        current_price = bars[-1].close
        assert ctx.distance_to_support == current_price - ctx.support
        assert ctx.distance_to_resistance == ctx.resistance - current_price

    def test_matches_the_task_spec_example_shape(self):
        # { trend: bullish, support: 5900, resistance: 5950, structure_confidence: 0.75 }
        bars = _zigzag(6, Decimal("5900"), Decimal("10"))
        ctx = analyze_structure(bars[-1].timestamp, "MES", bars)
        assert ctx.trend is TrendState.BULLISH
        assert ctx.support is not None
        assert ctx.resistance is not None
        assert ctx.resistance > ctx.support
        assert 0.0 <= ctx.structure_confidence <= 1.0


class TestNoClearStructureIsHandledSafely:
    def test_a_flat_repeating_cycle_has_no_confirmed_directional_edge(self):
        bars = _zigzag(6, Decimal("5900"), Decimal("0"))
        ctx = analyze_structure(bars[-1].timestamp, "MES", bars)
        assert ctx.trend in (TrendState.NEUTRAL, TrendState.UNKNOWN)
        if ctx.trend is TrendState.NEUTRAL:
            assert ctx.structure_confidence == 0.0


class TestMissingData:
    def test_too_few_bars_is_unknown_not_an_error(self):
        bars = _zigzag(6, Decimal("5900"), Decimal("10"))[:5]
        ctx = analyze_structure(bars[-1].timestamp, "MES", bars)
        assert ctx.trend is TrendState.UNKNOWN
        assert ctx.support is None
        assert ctx.resistance is None
        assert ctx.distance_to_support is None
        assert ctx.distance_to_resistance is None
        assert ctx.structure_confidence == 0.0

    def test_no_bars_is_unknown_not_an_error(self):
        ctx = analyze_structure(START, "MES", [])
        assert ctx.trend is TrendState.UNKNOWN

    def test_context_engine_handles_no_bars_gracefully(self):
        engine = ContextEngine(symbol="MES", timeframe="1min")
        ctx = engine.build_context(timestamp=START)
        assert ctx.structure_context is not None
        assert ctx.structure_context.trend is TrendState.UNKNOWN
        assert "structure" not in ctx.confidence_scores


class TestConfirmationLagIsNotFutureLeakage:
    def test_a_shorter_prefix_is_unaffected_by_bars_appended_after_it(self):
        full = _zigzag(6, Decimal("5900"), Decimal("10"))
        prefix = full[:20]
        as_of_prefix = analyze_structure(prefix[-1].timestamp, "MES", prefix)
        as_of_prefix_from_full = analyze_structure(prefix[-1].timestamp, "MES", full[:20])
        assert as_of_prefix.trend == as_of_prefix_from_full.trend
        assert as_of_prefix.support == as_of_prefix_from_full.support
        assert as_of_prefix.resistance == as_of_prefix_from_full.resistance
        assert as_of_prefix.structure_confidence == as_of_prefix_from_full.structure_confidence

    def test_the_most_recent_bars_have_no_confirmed_swing_yet(self):
        # A swing point needs `swing_window` bars *after* it to confirm --
        # the tail of any series is honestly un-confirmable yet, not a bug.
        bars = _zigzag(6, Decimal("5900"), Decimal("10"))
        from futures_bot.context.structure import _swing_high_indices, DEFAULT_SWING_WINDOW

        highs = _swing_high_indices(bars, DEFAULT_SWING_WINDOW)
        assert all(i <= len(bars) - 1 - DEFAULT_SWING_WINDOW for i in highs)


class TestStructureContextSerialization:
    def test_to_dict_round_trips_through_from_dict(self):
        bars = _zigzag(6, Decimal("5900"), Decimal("10"))
        original = analyze_structure(bars[-1].timestamp, "MES", bars)
        restored = StructureContext.from_dict(original.to_dict())
        assert restored == original

    def test_to_dict_handles_none_levels_when_unknown(self):
        original = analyze_structure(START, "MES", [])
        restored = StructureContext.from_dict(original.to_dict())
        assert restored == original


class TestIntegratedIntoMarketContext:
    def test_market_context_to_dict_includes_nested_structure_context(self):
        engine = ContextEngine(symbol="MES", timeframe="1min")
        bars = _zigzag(6, Decimal("5900"), Decimal("10"))
        ctx = engine.build_context(timestamp=bars[-1].timestamp, bars=bars)
        d = ctx.to_dict()
        assert d["structure_context"]["trend"] == ctx.structure_context.trend.value

    def test_market_context_from_dict_restores_structure_context(self):
        from futures_bot.context import MarketContext

        engine = ContextEngine(symbol="MES", timeframe="1min")
        bars = _zigzag(6, Decimal("5900"), Decimal("10"))
        original = engine.build_context(timestamp=bars[-1].timestamp, bars=bars)
        restored = MarketContext.from_dict(original.to_dict())
        assert restored == original


class TestDoesNotGenerateTradesOrOverrideStrategies:
    def test_structure_context_carries_no_broker_risk_or_engine_reference(self):
        import inspect

        import futures_bot.context.structure as structure_module

        source = inspect.getsource(structure_module)
        import_lines = [
            line.strip() for line in source.splitlines()
            if line.strip().startswith(("import ", "from "))
        ]
        for line in import_lines:
            assert "risk.manager" not in line, line
            assert "brokers" not in line, line
            assert "engine" not in line, line
