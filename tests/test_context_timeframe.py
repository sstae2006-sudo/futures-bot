"""Tests for context/timeframe.py -- Multi-Timeframe Context (Market
Context Engine Phase 5; see ROADMAP.md's "Market Context Engine
(phased)" and docs/ARCHITECTURE.md's "Market Context Engine" section).

Named test_context_timeframe.py, matching test_context_session.py /
test_context_volatility.py / test_context_regime.py's naming (no
collision exists for either name in this codebase).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from futures_bot.context import ContextEngine, TimeframeAlignment, classify_timeframe_alignment
from futures_bot.context.models import TrendState
from futures_bot.context.timeframe import TIMEFRAME_ORDER
from futures_bot.models import Bar

CT = ZoneInfo("America/Chicago")
NOW = datetime(2026, 1, 6, 20, 0, tzinfo=CT)


def _series(n: int, drift: Decimal, bar_range: Decimal, end: datetime, minutes: int, base=Decimal("5000")) -> list[Bar]:
    """``n`` completed bars of length ``minutes``, ending at (and
    including) ``end``, stepping by ``drift`` per bar -- deterministic
    trend direction, no randomness needed."""
    start = end - timedelta(minutes=minutes * (n - 1))
    out = []
    price = base
    for i in range(n):
        ts = start + timedelta(minutes=minutes * i)
        open_ = price
        price = price + drift
        close = price
        high = max(open_, close) + bar_range / 2
        low = min(open_, close) - bar_range / 2
        out.append(Bar(timestamp=ts, open=open_, high=high, low=low, close=close, volume=100))
    return out


class TestAlignmentAcrossTimeframes:
    def test_matches_the_task_spec_example_shape(self):
        # 1m neutral, 5m/15m/1h bullish, daily missing entirely.
        bars_by_timeframe = {
            "1m": _series(30, Decimal("0"), Decimal("4"), NOW, 1),
            "5m": _series(30, Decimal("3"), Decimal("10"), NOW, 5),
            "15m": _series(30, Decimal("3"), Decimal("15"), NOW, 15),
            "1h": _series(30, Decimal("3"), Decimal("20"), NOW, 60),
        }
        result = classify_timeframe_alignment(NOW, "MES", bars_by_timeframe)
        assert result.alignment["1m"] is TrendState.NEUTRAL
        assert result.alignment["5m"] is TrendState.BULLISH
        assert result.alignment["15m"] is TrendState.BULLISH
        assert result.alignment["1h"] is TrendState.BULLISH
        assert "1d" not in result.alignment
        assert result.alignment_score > 0.7

    def test_all_timeframes_bullish_is_full_alignment(self):
        bars_by_timeframe = {
            tf: _series(30, Decimal("3"), Decimal("10"), NOW, minutes)
            for tf, minutes in zip(TIMEFRAME_ORDER, (1, 5, 15, 60, 1440))
        }
        result = classify_timeframe_alignment(NOW, "MES", bars_by_timeframe)
        assert set(result.alignment) == set(TIMEFRAME_ORDER)
        assert all(state is TrendState.BULLISH for state in result.alignment.values())
        assert result.alignment_score == pytest.approx(1.0)

    def test_evenly_split_bullish_and_bearish_is_low_alignment(self):
        # 1m/5m bullish, 15m/1h bearish -- weights (1+2) vs (3+4), net
        # magnitude should be modest, not near 1.0.
        bars_by_timeframe = {
            "1m": _series(30, Decimal("3"), Decimal("10"), NOW, 1),
            "5m": _series(30, Decimal("3"), Decimal("10"), NOW, 5),
            "15m": _series(30, Decimal("-3"), Decimal("10"), NOW, 15),
            "1h": _series(30, Decimal("-3"), Decimal("10"), NOW, 60),
        }
        result = classify_timeframe_alignment(NOW, "MES", bars_by_timeframe)
        assert result.alignment_score < 0.5

    def test_context_engine_wires_timeframe_alignment_through(self):
        engine = ContextEngine(symbol="MES", timeframe="5min")
        bars_by_timeframe = {
            "5m": _series(30, Decimal("3"), Decimal("10"), NOW, 5),
            "1h": _series(30, Decimal("3"), Decimal("20"), NOW, 60),
        }
        ctx = engine.build_context(timestamp=NOW, bars_by_timeframe=bars_by_timeframe)
        assert ctx.timeframe_alignment is not None
        assert ctx.timeframe_alignment.alignment["5m"] is TrendState.BULLISH
        assert "timeframe_alignment" in ctx.confidence_scores


class TestSupportsMissingTimeframeData:
    def test_no_mapping_at_all_is_empty_not_an_error(self):
        result = classify_timeframe_alignment(NOW, "MES", None)
        assert result.alignment == {}
        assert result.alignment_score == 0.0

    def test_empty_mapping_is_empty_not_an_error(self):
        result = classify_timeframe_alignment(NOW, "MES", {})
        assert result.alignment == {}
        assert result.alignment_score == 0.0

    def test_a_timeframe_with_no_bars_is_omitted(self):
        result = classify_timeframe_alignment(NOW, "MES", {"5m": [], "1h": _series(30, Decimal("3"), Decimal("10"), NOW, 60)})
        assert "5m" not in result.alignment
        assert "1h" in result.alignment

    def test_a_timeframe_with_only_one_bar_is_omitted(self):
        one_bar = _series(1, Decimal("0"), Decimal("10"), NOW, 60)
        result = classify_timeframe_alignment(NOW, "MES", {"1h": one_bar})
        assert "1h" not in result.alignment

    def test_context_engine_handles_no_timeframe_data_gracefully(self):
        engine = ContextEngine(symbol="MES", timeframe="5min")
        ctx = engine.build_context(timestamp=NOW)
        assert ctx.timeframe_alignment is not None
        assert ctx.timeframe_alignment.alignment == {}
        assert "timeframe_alignment" not in ctx.confidence_scores


class TestAvoidsFutureLeakage:
    def test_an_in_progress_bar_is_excluded_until_it_actually_closes(self):
        # A 1-hour bar opened at 09:00 CT closes at 10:00 CT -- it must
        # not be usable while classifying "as of" any moment before that.
        prior = _series(10, Decimal("1"), Decimal("5"), datetime(2026, 1, 6, 8, 0, tzinfo=CT), 60)
        in_progress = Bar(
            timestamp=datetime(2026, 1, 6, 9, 0, tzinfo=CT),
            open=Decimal("5010"), high=Decimal("5200"), low=Decimal("4800"), close=Decimal("5190"),
            volume=50,
        )
        bars_1h = prior + [in_progress]

        # As of 09:05 -- the 09:00 bar has not closed yet.
        as_of_forming = classify_timeframe_alignment(
            datetime(2026, 1, 6, 9, 5, tzinfo=CT), "MES", {"1h": bars_1h}
        )
        # As of 09:05 with only the completed prior bars given directly
        # (what a correct caller would have produced) must match exactly.
        as_of_forming_reference = classify_timeframe_alignment(
            datetime(2026, 1, 6, 9, 5, tzinfo=CT), "MES", {"1h": prior}
        )
        assert as_of_forming.alignment == as_of_forming_reference.alignment
        assert as_of_forming.alignment_score == as_of_forming_reference.alignment_score

        # As of 10:00 -- the 09:00 bar has now closed and may count.
        as_of_closed = classify_timeframe_alignment(
            datetime(2026, 1, 6, 10, 0, tzinfo=CT), "MES", {"1h": bars_1h}
        )
        as_of_closed_reference = classify_timeframe_alignment(
            datetime(2026, 1, 6, 10, 0, tzinfo=CT), "MES", {"1h": bars_1h[:-1] + [in_progress]}
        )
        assert as_of_closed.alignment == as_of_closed_reference.alignment

    def test_a_shorter_history_is_unaffected_by_bars_appended_after_it(self):
        base_bars = _series(30, Decimal("3"), Decimal("10"), NOW, 5)
        future_bars = _series(
            10, Decimal("-10"), Decimal("10"),
            NOW + timedelta(minutes=5 * 10), 5, base=base_bars[-1].close,
        )
        full = base_bars + future_bars

        as_of_now = classify_timeframe_alignment(NOW, "MES", {"5m": base_bars})
        as_of_now_from_full = classify_timeframe_alignment(NOW, "MES", {"5m": full})
        assert as_of_now.alignment == as_of_now_from_full.alignment
        assert as_of_now.alignment_score == as_of_now_from_full.alignment_score


class TestTimeframeAlignmentSerialization:
    def test_to_dict_round_trips_through_from_dict(self):
        bars_by_timeframe = {"5m": _series(30, Decimal("3"), Decimal("10"), NOW, 5)}
        original = classify_timeframe_alignment(NOW, "MES", bars_by_timeframe)
        restored = TimeframeAlignment.from_dict(original.to_dict())
        assert restored == original


class TestIntegratedIntoMarketContext:
    def test_market_context_to_dict_includes_nested_timeframe_alignment(self):
        engine = ContextEngine(symbol="MES", timeframe="5min")
        bars_by_timeframe = {"5m": _series(30, Decimal("3"), Decimal("10"), NOW, 5)}
        ctx = engine.build_context(timestamp=NOW, bars_by_timeframe=bars_by_timeframe)
        d = ctx.to_dict()
        assert d["timeframe_alignment"]["alignment"]["5m"] == "BULLISH"

    def test_market_context_from_dict_restores_timeframe_alignment(self):
        from futures_bot.context import MarketContext

        engine = ContextEngine(symbol="MES", timeframe="5min")
        bars_by_timeframe = {"5m": _series(30, Decimal("3"), Decimal("10"), NOW, 5)}
        original = engine.build_context(timestamp=NOW, bars_by_timeframe=bars_by_timeframe)
        restored = MarketContext.from_dict(original.to_dict())
        assert restored == original
