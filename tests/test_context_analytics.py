"""Tests for context/analytics.py -- Context Analytics (Market Context
Engine Phase 8, Part 6; see ROADMAP.md's "Market Context Engine
(phased)" and docs/ARCHITECTURE.md's "Market Context Engine" section).

Named test_context_analytics.py -- no collision with
test_api_analytics.py or test_trend_pullback_analytics.py (both
unrelated).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from futures_bot.context import ContextEngine, MarketContext, analyze_context_batch
from futures_bot.models import Bar

CT = ZoneInfo("America/Chicago")
START = datetime(2026, 1, 6, 17, 0, tzinfo=CT)


def _bars(n: int, drift: Decimal, base=Decimal("5900")) -> list[Bar]:
    out = []
    price = base
    for i in range(n):
        ts = START + timedelta(minutes=i)
        open_ = price
        price = price + drift
        close = price
        out.append(Bar(timestamp=ts, open=open_, high=max(open_, close) + 4, low=min(open_, close) - 4, close=close, volume=100 + i % 30))
    return out


def _build_batch(n: int, drift: Decimal) -> list[MarketContext]:
    engine = ContextEngine(symbol="MES", timeframe="1min")
    full = _bars(n, drift)
    return [engine.build_context(timestamp=full[i].timestamp, bars=full[: i + 1]) for i in range(2, len(full))]


class TestEmptyBatchHandledSafely:
    def test_empty_batch_never_raises(self):
        report = analyze_context_batch([])
        assert report.total_contexts == 0
        assert report.session.total == 0
        assert report.environment_score.count == 0
        assert report.confidence.count == 0

    def test_empty_batch_renders_without_crashing(self):
        report = analyze_context_batch([])
        text = report.render()
        assert "0 context" in text


class TestDistributions:
    def test_session_distribution_sums_to_the_batch_size(self):
        contexts = _build_batch(50, Decimal("3"))
        report = analyze_context_batch(contexts)
        assert sum(report.session.counts.values()) == len(contexts)
        assert sum(report.session.fractions.values()) == pytest.approx(1.0)

    def test_a_strong_uptrend_batch_shows_mostly_bullish_trend(self):
        contexts = _build_batch(80, Decimal("4"))
        report = analyze_context_batch(contexts)
        assert report.trend_state.counts.get("BULLISH", 0) > report.trend_state.counts.get("BEARISH", 0)

    def test_market_regime_distribution_present_and_normalized(self):
        contexts = _build_batch(80, Decimal("4"))
        report = analyze_context_batch(contexts)
        assert sum(report.market_regime.fractions.values()) == pytest.approx(1.0)

    def test_all_dimensions_present_in_the_report(self):
        contexts = _build_batch(30, Decimal("0"))
        report = analyze_context_batch(contexts)
        for dist in (
            report.session, report.market_regime, report.volatility_state,
            report.trend_state, report.liquidity_state, report.risk_state,
        ):
            assert dist.total == len(contexts)


class TestUnknownFrequency:
    def test_early_contexts_with_thin_history_show_up_as_unknown(self):
        # The first few contexts in any batch have very little history
        # (volatility/regime need 15+/28+ bars) -- a real, expected
        # source of UNKNOWN readings, not a bug.
        contexts = _build_batch(80, Decimal("3"))
        report = analyze_context_batch(contexts)
        assert report.unknown_frequency["volatility_state"] > 0.0
        assert report.unknown_frequency["market_regime"] > 0.0
        # Session never needs bars at all.
        assert report.unknown_frequency["session"] == 0.0

    def test_unknown_frequency_matches_each_distributions_own_unknown_fraction(self):
        contexts = _build_batch(80, Decimal("3"))
        report = analyze_context_batch(contexts)
        assert report.unknown_frequency["market_regime"] == report.market_regime.unknown_fraction
        assert report.unknown_frequency["risk_state"] == report.risk_state.unknown_fraction


class TestEnvironmentScoreAndConfidenceDistribution:
    def test_environment_score_summary_is_within_valid_bounds(self):
        contexts = _build_batch(80, Decimal("4"))
        report = analyze_context_batch(contexts)
        assert 0 <= report.environment_score.minimum <= report.environment_score.maximum <= 100
        assert report.environment_score.count == len(contexts)

    def test_confidence_summary_is_within_valid_bounds(self):
        contexts = _build_batch(80, Decimal("4"))
        report = analyze_context_batch(contexts)
        assert 0.0 <= report.confidence.minimum <= report.confidence.maximum <= 1.0

    def test_single_context_has_zero_stdev(self):
        contexts = _build_batch(80, Decimal("4"))[:1]
        report = analyze_context_batch(contexts)
        assert report.environment_score.stdev == 0.0
        assert report.confidence.stdev == 0.0


class TestRendersHumanReadableReport:
    def test_render_includes_every_dimension_label(self):
        contexts = _build_batch(80, Decimal("4"))
        report = analyze_context_batch(contexts)
        text = report.render()
        for label in ("Session", "Market regime", "Volatility", "Trend", "Liquidity", "Risk", "Environment score", "Confidence", "UNKNOWN frequency"):
            assert label in text
