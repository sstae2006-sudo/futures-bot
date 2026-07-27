"""Tests for context/session.py -- Session Context (Market Context Engine
Phase 2, session dimension only; see ROADMAP.md's "Market Context Engine
(phased)" and docs/ARCHITECTURE.md's "Market Context Engine" section).

Named test_context_session.py, not test_session.py -- that filename is
already taken by tests for the unrelated futures_bot.session module
(session-summary reporting for StateStore/RiskManager).

Every scenario here was also verified manually against the live module
before being written down as an assertion (not just derived from the
implementation) -- including catching and fixing a real bug this way:
the first implementation mis-measured ``minutes_since_open`` during the
16:00-17:00 CT maintenance halt (it used ``contracts.session_date``,
which attributes a halt moment to the *upcoming* session, the wrong one
to measure elapsed minutes against during the halt itself).
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from futures_bot.context import ContextEngine, SessionContext, classify_session
from futures_bot.context.models import SessionPhase

CT = ZoneInfo("America/Chicago")


def _ct(*args) -> datetime:
    return datetime(*args, tzinfo=CT)


class TestNormalTradingDay:
    """✓ Normal trading day."""

    def test_matches_the_task_spec_example_exactly(self):
        # {session: "OPENING_RANGE", minutes_since_open: 12, liquidity_expectation: "HIGH"}
        ctx = classify_session(_ct(2026, 1, 6, 8, 42), "MES")  # a Tuesday
        assert ctx.session is SessionPhase.OPENING_RANGE
        assert ctx.minutes_since_open == 12
        assert ctx.liquidity_expectation == "HIGH"
        assert ctx.is_market_open is True

    @pytest.mark.parametrize(
        "hour,minute,expected_session,expected_minutes",
        [
            (8, 30, SessionPhase.OPENING_RANGE, 0),      # RTH open
            (9, 29, SessionPhase.OPENING_RANGE, 59),
            (9, 30, SessionPhase.MORNING_SESSION, 0),
            (10, 45, SessionPhase.MORNING_SESSION, 75),
            (11, 0, SessionPhase.LUNCH_SESSION, 0),
            (12, 30, SessionPhase.LUNCH_SESSION, 90),
            (13, 0, SessionPhase.POWER_HOUR, 0),
            (15, 59, SessionPhase.POWER_HOUR, 179),
        ],
    )
    def test_every_rth_phase_boundary(self, hour, minute, expected_session, expected_minutes):
        ctx = classify_session(_ct(2026, 1, 6, hour, minute), "MES")
        assert ctx.session is expected_session
        assert ctx.minutes_since_open == expected_minutes
        assert ctx.is_market_open is True

    def test_pre_market_window(self):
        ctx = classify_session(_ct(2026, 1, 6, 8, 15), "MES")
        assert ctx.session is SessionPhase.PRE_MARKET
        assert ctx.minutes_since_open == 15
        assert ctx.liquidity_expectation == "LOW"

    def test_symbol_is_carried_onto_the_result(self):
        ctx = classify_session(_ct(2026, 1, 6, 8, 42), "MNQ")
        assert ctx.symbol == "MNQ"


class TestWeekend:
    """✓ Weekend."""

    def test_saturday_is_not_open(self):
        ctx = classify_session(_ct(2026, 1, 10, 12, 0), "MES")  # a Saturday
        assert ctx.is_market_open is False
        assert ctx.liquidity_expectation == "NONE"
        assert ctx.session is SessionPhase.OVERNIGHT

    def test_friday_after_close_is_weekend(self):
        # Market closes Friday 16:00 CT for the weekend.
        ctx = classify_session(_ct(2026, 1, 9, 18, 0), "MES")
        assert ctx.is_market_open is False
        assert ctx.liquidity_expectation == "NONE"

    def test_sunday_before_reopen_is_still_weekend(self):
        ctx = classify_session(_ct(2026, 1, 11, 16, 0), "MES")  # before 17:00 CT reopen
        assert ctx.is_market_open is False

    def test_sunday_after_reopen_is_open(self):
        ctx = classify_session(_ct(2026, 1, 11, 18, 0), "MES")  # after 17:00 CT reopen
        assert ctx.is_market_open is True
        assert ctx.session is SessionPhase.OVERNIGHT
        assert ctx.liquidity_expectation == "LOW"


class TestHoliday:
    """✓ Holiday."""

    def test_christmas_is_closed_all_day(self):
        ctx = classify_session(_ct(2026, 12, 25, 10, 0), "MES")
        assert ctx.is_market_open is False
        assert ctx.liquidity_expectation == "NONE"
        assert ctx.session is SessionPhase.OVERNIGHT

    def test_new_years_day_is_closed(self):
        ctx = classify_session(_ct(2026, 1, 1, 10, 0), "MES")
        assert ctx.is_market_open is False

    def test_day_before_a_holiday_is_normal(self):
        # Confirms the holiday check is date-specific, not a whole-week
        # closure -- Dec 24 is a normal (if early-close-in-reality,
        # unmodeled-here) trading day per contracts.cme_full_closures.
        ctx = classify_session(_ct(2026, 12, 24, 8, 42), "MES")
        assert ctx.is_market_open is True
        assert ctx.session is SessionPhase.OPENING_RANGE


class TestOvernightSession:
    """✓ Overnight session."""

    def test_late_evening_is_overnight_and_open(self):
        ctx = classify_session(_ct(2026, 1, 6, 20, 0), "MES")  # Tuesday 8pm CT
        assert ctx.session is SessionPhase.OVERNIGHT
        assert ctx.is_market_open is True
        assert ctx.liquidity_expectation == "LOW"
        assert ctx.minutes_since_open == 180  # 3 hours after the 17:00 CT reopen

    def test_early_morning_before_premarket_is_overnight(self):
        ctx = classify_session(_ct(2026, 1, 7, 3, 0), "MES")  # 3am CT
        assert ctx.session is SessionPhase.OVERNIGHT
        assert ctx.is_market_open is True

    def test_maintenance_halt_is_market_close_not_overnight(self):
        # The 16:00-17:00 CT daily halt is its own phase (MARKET_CLOSE),
        # not folded into OVERNIGHT.
        ctx = classify_session(_ct(2026, 1, 6, 16, 30), "MES")
        assert ctx.session is SessionPhase.MARKET_CLOSE
        assert ctx.is_market_open is False
        assert ctx.liquidity_expectation == "NONE"
        assert ctx.minutes_since_open == 30  # 30 minutes into the halt

    def test_minutes_since_open_is_correct_at_every_point_in_the_halt(self):
        # Regression test for the exact bug found and fixed while
        # building this: session_date() attributes a halt moment to the
        # session about to open, which is the wrong reference point for
        # "minutes elapsed" during the halt itself.
        assert classify_session(_ct(2026, 1, 6, 16, 0), "MES").minutes_since_open == 0
        assert classify_session(_ct(2026, 1, 6, 16, 30), "MES").minutes_since_open == 30
        assert classify_session(_ct(2026, 1, 6, 16, 59), "MES").minutes_since_open == 59


class TestMarketOpenTransition:
    """✓ Market open transition."""

    def test_session_reopen_at_1700_ct(self):
        before = classify_session(_ct(2026, 1, 6, 16, 59), "MES")
        at_open = classify_session(_ct(2026, 1, 6, 17, 0), "MES")
        assert before.session is SessionPhase.MARKET_CLOSE
        assert before.is_market_open is False
        assert at_open.session is SessionPhase.OVERNIGHT
        assert at_open.is_market_open is True
        assert at_open.minutes_since_open == 0

    def test_rth_open_transition_at_0830_ct(self):
        before = classify_session(_ct(2026, 1, 6, 8, 29), "MES")
        at_open = classify_session(_ct(2026, 1, 6, 8, 30), "MES")
        assert before.session is SessionPhase.PRE_MARKET
        assert at_open.session is SessionPhase.OPENING_RANGE
        assert at_open.minutes_since_open == 0

    def test_weekend_to_open_transition(self):
        closed = classify_session(_ct(2026, 1, 11, 16, 59), "MES")  # Sunday, before reopen
        reopened = classify_session(_ct(2026, 1, 11, 17, 0), "MES")  # Sunday, at reopen
        assert closed.is_market_open is False
        assert reopened.is_market_open is True
        assert reopened.session is SessionPhase.OVERNIGHT


class TestSessionContextSerialization:
    def test_to_dict_round_trips_through_from_dict(self):
        original = classify_session(_ct(2026, 1, 6, 8, 42), "MES")
        restored = SessionContext.from_dict(original.to_dict())
        assert restored == original

    def test_naive_timestamp_is_rejected(self):
        with pytest.raises(ValueError):
            classify_session(datetime(2026, 1, 6, 8, 42), "MES")


class TestIntegratedIntoMarketContext:
    """Session information integrated into MarketContext (via ContextEngine)."""

    def test_context_engine_wires_session_context_through(self):
        engine = ContextEngine(symbol="MES", timeframe="5min")
        ctx = engine.build_context(timestamp=_ct(2026, 1, 6, 8, 42))

        assert ctx.session is SessionPhase.OPENING_RANGE
        assert ctx.session_context is not None
        assert ctx.session_context.session is SessionPhase.OPENING_RANGE
        assert ctx.session_context.minutes_since_open == 12
        assert ctx.session_context.liquidity_expectation == "HIGH"

    def test_session_field_and_session_context_field_always_agree(self):
        engine = ContextEngine(symbol="MES", timeframe="5min")
        for moment in (
            _ct(2026, 1, 6, 8, 42),   # normal trading day
            _ct(2026, 1, 10, 12, 0),  # weekend
            _ct(2026, 12, 25, 10, 0),  # holiday
            _ct(2026, 1, 6, 20, 0),   # overnight
            _ct(2026, 1, 6, 16, 30),  # maintenance halt
        ):
            ctx = engine.build_context(timestamp=moment)
            assert ctx.session is ctx.session_context.session

    def test_market_context_to_dict_includes_nested_session_context(self):
        engine = ContextEngine(symbol="MES", timeframe="5min")
        ctx = engine.build_context(timestamp=_ct(2026, 1, 6, 8, 42))
        d = ctx.to_dict()
        assert d["session_context"]["minutes_since_open"] == 12
        assert d["session_context"]["liquidity_expectation"] == "HIGH"

    def test_market_context_from_dict_restores_session_context(self):
        engine = ContextEngine(symbol="MES", timeframe="5min")
        original = engine.build_context(timestamp=_ct(2026, 1, 6, 8, 42))
        from futures_bot.context import MarketContext

        restored = MarketContext.from_dict(original.to_dict())
        assert restored == original
