"""Tests for `collaboration.maintenance.MaintenanceScheduler` -- SIL Phase
4's periodic housekeeping (stale-draft cleanup + DB health check). Same
lifecycle-test conventions `test_market_data_scheduler.py`/
`test_git_watcher.py` already established; staleness/discard logic is
tested directly via `_run_cycle`/`_discard_stale_drafts` rather than
waiting on a real background cycle.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest

from futures_bot.collaboration.maintenance import (
    MaintenanceScheduler, get_maintenance_scheduler, reset_maintenance_scheduler,
)
from futures_bot.collaboration.store import get_collaboration_store


@pytest.fixture(autouse=True)
def _reset(monkeypatch, tmp_path):
    reset_maintenance_scheduler()
    monkeypatch.setenv("FUTURES_BOT_MARKET_DATA_DB", str(tmp_path / "market_data.db"))
    monkeypatch.setenv("FUTURES_BOT_RESEARCH_DB", str(tmp_path / "research.db"))
    yield
    reset_maintenance_scheduler()


class TestLifecycle:
    def test_start_and_stop_without_leaking_a_thread(self):
        scheduler = MaintenanceScheduler()
        scheduler.start(interval_seconds=1)
        assert scheduler.status()["running"] is True

        scheduler.stop(timeout=5)
        assert scheduler.status()["running"] is False

    def test_starting_twice_raises(self):
        scheduler = MaintenanceScheduler()
        scheduler.start(interval_seconds=1)
        try:
            with pytest.raises(RuntimeError, match="already running"):
                scheduler.start(interval_seconds=1)
        finally:
            scheduler.stop(timeout=5)


class TestDiscardStaleDrafts:
    def test_a_fresh_draft_is_not_discarded(self):
        store = get_collaboration_store()
        store.create_work_item(item_id="w1", title="Fresh draft", is_draft=True)
        scheduler = MaintenanceScheduler()

        discarded = scheduler._discard_stale_drafts(datetime.now(timezone.utc), stale_draft_days=3.0)

        assert discarded == 0
        assert store.fetch_work_item("w1") is not None

    def test_a_stale_draft_is_discarded(self, monkeypatch):
        store = get_collaboration_store()
        store.create_work_item(item_id="w1", title="Old draft", is_draft=True)
        scheduler = MaintenanceScheduler()
        far_future = datetime.now(timezone.utc) + timedelta(days=10)

        discarded = scheduler._discard_stale_drafts(far_future, stale_draft_days=3.0)

        assert discarded == 1
        assert store.fetch_work_item("w1") is None

    def test_a_stale_real_item_is_never_touched(self):
        """Only `fetch_draft_work_items()` results are ever candidates --
        a real item's age is irrelevant."""
        store = get_collaboration_store()
        real = store.create_work_item(item_id="w1", title="Old real item")
        scheduler = MaintenanceScheduler()
        far_future = datetime.now(timezone.utc) + timedelta(days=10)

        discarded = scheduler._discard_stale_drafts(far_future, stale_draft_days=3.0)

        assert discarded == 0
        assert store.fetch_work_item("w1") == real

    def test_an_approved_draft_is_never_touched_even_if_old(self):
        store = get_collaboration_store()
        store.create_work_item(item_id="w1", title="Approved", is_draft=True)
        store.approve_draft_work_item("w1")
        scheduler = MaintenanceScheduler()
        far_future = datetime.now(timezone.utc) + timedelta(days=10)

        discarded = scheduler._discard_stale_drafts(far_future, stale_draft_days=3.0)

        assert discarded == 0
        assert store.fetch_work_item("w1") is not None

    def test_boundary_is_inclusive_of_stale_draft_days(self):
        """A draft exactly `stale_draft_days` old counts as stale (>=),
        not still-fresh -- avoids an off-by-one where "3 days" quietly
        means "more than 3 days."""
        store = get_collaboration_store()
        item = store.create_work_item(item_id="w1", title="Exactly at cutoff", is_draft=True)
        from futures_bot.collaboration import parse_db_timestamp

        updated_at = parse_db_timestamp(item["updated_at"])
        now = updated_at + timedelta(days=3.0)
        scheduler = MaintenanceScheduler()

        discarded = scheduler._discard_stale_drafts(now, stale_draft_days=3.0)

        assert discarded == 1


class TestRunCycle:
    def test_updates_status_with_discard_count_and_db_health(self, monkeypatch):
        store = get_collaboration_store()
        store.create_work_item(item_id="w1", title="Old draft", is_draft=True)
        scheduler = MaintenanceScheduler()
        far_future = datetime.now(timezone.utc) + timedelta(days=10)

        scheduler._run_cycle(far_future, stale_draft_days=3.0)

        status = scheduler.status()
        assert status["stale_drafts_discarded_count"] == 1
        assert status["cycles_completed"] == 1
        assert status["last_error"] is None
        assert "discarded 1 stale draft" in status["last_result"]

    def test_a_bad_cycle_records_last_error_without_raising(self, monkeypatch):
        scheduler = MaintenanceScheduler()
        monkeypatch.setattr(
            scheduler, "_discard_stale_drafts", lambda now, stale_draft_days: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        scheduler._run_cycle(datetime.now(timezone.utc), stale_draft_days=3.0)  # must not raise

        assert scheduler.status()["last_error"] == "boom"


class TestGlobalAccessor:
    def test_get_maintenance_scheduler_returns_the_same_instance(self):
        first = get_maintenance_scheduler()
        second = get_maintenance_scheduler()
        assert first is second


class TestConcurrency:
    def test_concurrent_start_calls_never_both_win(self):
        scheduler = MaintenanceScheduler()
        results = []

        def _try_start():
            try:
                scheduler.start(interval_seconds=1)
                results.append("started")
            except RuntimeError:
                results.append("rejected")

        threads = [threading.Thread(target=_try_start) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        scheduler.stop(timeout=5)
        assert results.count("started") == 1
        assert results.count("rejected") == 4
