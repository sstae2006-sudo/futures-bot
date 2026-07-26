"""`session.build_session_summaries` -- reshaping what `StateStore`/
`RiskManager` already record into one row per simulated trading day. Pure
read, no new simulation."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from futures_bot.contracts import CME_TZ, session_date
from futures_bot.session import build_session_summaries
from futures_bot.state import StateStore


def ct(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=CME_TZ)


@pytest.fixture
def store(tmp_path):
    return StateStore(tmp_path / "state.json")


class TestBuildSessionSummaries:
    def test_empty_store_produces_no_summaries(self, store):
        assert build_session_summaries(store, Decimal("50000")) == []

    def test_one_day_reflects_starting_balance_and_pnl(self, store):
        now = ct(2026, 7, 21, 10, 0)
        store.record_pnl(session_date(now), Decimal("150"))

        summaries = build_session_summaries(store, Decimal("50000"))

        assert len(summaries) == 1
        s = summaries[0]
        assert s.starting_balance == Decimal("50000")
        assert s.session_pnl == Decimal("150")
        assert s.ending_balance == Decimal("50150")
        assert s.trade_count == 1

    def test_balance_compounds_across_days_not_reset(self, store):
        day1 = ct(2026, 7, 21, 10, 0)
        day2 = ct(2026, 7, 22, 10, 0)
        store.record_pnl(session_date(day1), Decimal("500"))
        store.record_pnl(session_date(day2), Decimal("-200"))

        summaries = build_session_summaries(store, Decimal("50000"))

        assert len(summaries) == 2
        assert summaries[0].starting_balance == Decimal("50000")
        assert summaries[0].ending_balance == Decimal("50500")
        assert summaries[1].starting_balance == Decimal("50500"), "day 2 must start from day 1's ending balance"
        assert summaries[1].ending_balance == Decimal("50300")

    def test_halt_category_and_timestamps_pass_through(self, store):
        now = ct(2026, 7, 21, 10, 0)
        sd = session_date(now)
        store.record_pnl(sd, Decimal("500"))
        store.record_target_hit(sd, now)
        store.halt(sd, "Profit target reached.", category="profit_target", at=now)

        s = build_session_summaries(store, Decimal("50000"))[0]
        assert s.halted is True
        assert s.halt_category == "profit_target"
        assert s.stopped_on_profit is True
        assert s.stopped_on_loss is False
        assert s.target_hit_at == now.isoformat()
        assert s.halted_at == now.isoformat()

    def test_missed_opportunities_pass_through(self, store):
        now = ct(2026, 7, 21, 10, 0)
        sd = session_date(now)
        store.halt(sd, "Daily loss limit reached.", category="daily_loss", at=now)
        store.record_missed_opportunity(sd)
        store.record_missed_opportunity(sd)

        s = build_session_summaries(store, Decimal("50000"))[0]
        assert s.missed_opportunities == 2
        assert s.stopped_on_loss is True
