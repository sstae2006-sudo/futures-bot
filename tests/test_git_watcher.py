"""Tests for `collaboration.git_watcher.GitWatcherScheduler` -- SIL Phase 4's
background thread that drafts a work item for uncommitted changes not
covered by any active work item. Same lifecycle-test conventions
`test_market_data_scheduler.py` already established; the reconciliation
logic itself (`_reconcile`) is tested directly (synchronously) rather than
through the thread, so dedup/supersede assertions don't need to poll a
background cycle.
"""

from __future__ import annotations

import threading
import time

import pytest

from futures_bot.collaboration.git_watcher import GitWatcherScheduler, get_git_watcher, reset_git_watcher
from futures_bot.collaboration.store import get_collaboration_store


@pytest.fixture(autouse=True)
def _reset(monkeypatch, tmp_path):
    reset_git_watcher()
    monkeypatch.setenv("FUTURES_BOT_MARKET_DATA_DB", str(tmp_path / "market_data.db"))
    monkeypatch.setenv("FUTURES_BOT_RESEARCH_DB", str(tmp_path / "research.db"))
    yield
    reset_git_watcher()


def _wait_until(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


class TestLifecycle:
    def test_start_and_stop_without_leaking_a_thread(self, monkeypatch):
        monkeypatch.setattr("futures_bot.collaboration.git_watcher.git_info.changed_files", lambda: [])
        watcher = GitWatcherScheduler()
        watcher.start(interval_seconds=1)
        assert watcher.status()["running"] is True

        watcher.stop(timeout=5)
        assert watcher.status()["running"] is False

    def test_starting_twice_raises(self, monkeypatch):
        monkeypatch.setattr("futures_bot.collaboration.git_watcher.git_info.changed_files", lambda: [])
        watcher = GitWatcherScheduler()
        watcher.start(interval_seconds=1)
        try:
            with pytest.raises(RuntimeError, match="already running"):
                watcher.start(interval_seconds=1)
        finally:
            watcher.stop(timeout=5)

    def test_runs_a_cycle_and_updates_status(self, monkeypatch):
        monkeypatch.setattr("futures_bot.collaboration.git_watcher.git_info.changed_files", lambda: [])
        watcher = GitWatcherScheduler()
        watcher.start(interval_seconds=1)
        assert _wait_until(lambda: watcher.status()["cycles_completed"] >= 1)
        watcher.stop(timeout=5)

        assert watcher.status()["last_error"] is None
        assert watcher.status()["last_cycle_at"] is not None


class TestReconcile:
    """Direct, synchronous tests of `_reconcile` -- the dedup/supersede
    contract this module's docstring promises."""

    def test_no_changed_files_creates_nothing(self, monkeypatch):
        monkeypatch.setattr("futures_bot.collaboration.git_watcher.git_info.changed_files", lambda: [])
        watcher = GitWatcherScheduler()

        assert watcher._reconcile() is None
        assert get_collaboration_store().fetch_draft_work_items() == []

    def test_changed_file_covered_by_an_active_item_creates_nothing(self, monkeypatch):
        store = get_collaboration_store()
        store.create_work_item(item_id="w1", title="Existing", estimated_files=["a.py"])
        monkeypatch.setattr("futures_bot.collaboration.git_watcher.git_info.changed_files", lambda: ["a.py"])
        watcher = GitWatcherScheduler()

        assert watcher._reconcile() is None
        assert store.fetch_draft_work_items() == []

    def test_uncovered_file_creates_exactly_one_draft(self, monkeypatch):
        monkeypatch.setattr("futures_bot.collaboration.git_watcher.git_info.changed_files", lambda: ["src/new.py"])
        watcher = GitWatcherScheduler()

        created = watcher._reconcile()

        assert created is not None
        assert created["is_draft"] is True
        assert created["estimated_files"] == ["src/new.py"]
        assert created["priority"] == "low"

    def test_repeated_cycles_with_the_same_uncovered_set_are_idempotent(self, monkeypatch):
        """The core dedup requirement: running the same cycle twice must
        not create a second draft."""
        monkeypatch.setattr("futures_bot.collaboration.git_watcher.git_info.changed_files", lambda: ["src/new.py"])
        watcher = GitWatcherScheduler()
        watcher._reconcile()

        second = watcher._reconcile()

        assert second is None
        assert len(get_collaboration_store().fetch_draft_work_items()) == 1

    def test_growing_change_set_supersedes_the_old_draft(self, monkeypatch):
        """The old draft's files (["src/a.py"]) are a proper subset of the
        new uncovered set -- the stale draft is discarded and replaced,
        never left alongside the new one."""
        monkeypatch.setattr("futures_bot.collaboration.git_watcher.git_info.changed_files", lambda: ["src/a.py"])
        watcher = GitWatcherScheduler()
        first = watcher._reconcile()

        monkeypatch.setattr(
            "futures_bot.collaboration.git_watcher.git_info.changed_files", lambda: ["src/a.py", "src/b.py"],
        )
        second = watcher._reconcile()

        store = get_collaboration_store()
        drafts = store.fetch_draft_work_items()
        assert len(drafts) == 1
        assert drafts[0]["id"] == second["id"]
        assert drafts[0]["id"] != first["id"]
        assert sorted(drafts[0]["estimated_files"]) == ["src/a.py", "src/b.py"]

    def test_shrinking_change_set_creates_a_new_draft_alongside_discarding_the_old(self, monkeypatch):
        """Old files aren't a subset of the new set (some files were
        committed/removed from the working tree) -- still self-heals to
        exactly one draft reflecting the current truth, not a stale one."""
        monkeypatch.setattr(
            "futures_bot.collaboration.git_watcher.git_info.changed_files", lambda: ["src/a.py", "src/b.py"],
        )
        watcher = GitWatcherScheduler()
        watcher._reconcile()

        monkeypatch.setattr("futures_bot.collaboration.git_watcher.git_info.changed_files", lambda: ["src/a.py"])
        watcher._reconcile()

        store = get_collaboration_store()
        drafts = store.fetch_draft_work_items()
        assert len(drafts) == 1
        assert drafts[0]["estimated_files"] == ["src/a.py"]

    def test_never_touches_a_real_work_item(self, monkeypatch):
        """A real (non-draft) item covering none of the changed files must
        never be discarded or modified by reconciliation -- only drafts
        are ever candidates for the supersede/discard path."""
        store = get_collaboration_store()
        real = store.create_work_item(item_id="real1", title="Real work", estimated_files=["unrelated.py"])
        monkeypatch.setattr("futures_bot.collaboration.git_watcher.git_info.changed_files", lambda: ["src/new.py"])
        watcher = GitWatcherScheduler()
        watcher._reconcile()

        monkeypatch.setattr(
            "futures_bot.collaboration.git_watcher.git_info.changed_files", lambda: ["src/new.py", "src/more.py"],
        )
        watcher._reconcile()  # a second cycle with a different set, to also exercise the supersede path

        assert store.fetch_work_item("real1") == real

    def test_approved_draft_is_never_touched_by_a_later_cycle(self, monkeypatch):
        """Once a draft is approved (is_draft cleared), it becomes a real
        work item and `fetch_draft_work_items` no longer returns it --
        reconciliation must leave it alone even if the file set changes."""
        monkeypatch.setattr("futures_bot.collaboration.git_watcher.git_info.changed_files", lambda: ["src/a.py"])
        watcher = GitWatcherScheduler()
        created = watcher._reconcile()
        store = get_collaboration_store()
        store.approve_draft_work_item(created["id"])

        monkeypatch.setattr(
            "futures_bot.collaboration.git_watcher.git_info.changed_files", lambda: ["src/a.py", "src/b.py"],
        )
        watcher._reconcile()

        approved = store.fetch_work_item(created["id"])
        assert approved["is_draft"] is False
        assert approved["estimated_files"] == ["src/a.py"]  # untouched by the later cycle

    def test_concurrent_discard_of_a_stale_draft_is_tolerated(self, monkeypatch):
        """If a draft is approved/discarded by a human between this
        cycle's `fetch_draft_work_items()` read and its own discard call
        (a real race under a background thread + concurrent human
        action), `discard_draft_work_item` raises `CollaborationError` --
        `_reconcile` must swallow that rather than letting one race kill
        the whole cycle."""
        from futures_bot.collaboration.store import CollaborationError, CollaborationStore

        monkeypatch.setattr("futures_bot.collaboration.git_watcher.git_info.changed_files", lambda: ["src/a.py"])
        watcher = GitWatcherScheduler()
        watcher._reconcile()  # creates a draft covering ["src/a.py"]

        def _raise_already_gone(self, item_id, *, actor_user_id=None):
            raise CollaborationError(f"Work item {item_id!r} is not a draft.")

        monkeypatch.setattr(CollaborationStore, "discard_draft_work_item", _raise_already_gone)
        monkeypatch.setattr(
            "futures_bot.collaboration.git_watcher.git_info.changed_files", lambda: ["src/a.py", "src/b.py"],
        )

        second = watcher._reconcile()  # must not raise despite the discard failing

        assert second is not None
        assert sorted(second["estimated_files"]) == ["src/a.py", "src/b.py"]


class TestGlobalAccessor:
    def test_get_git_watcher_returns_the_same_instance(self):
        first = get_git_watcher()
        second = get_git_watcher()
        assert first is second


class TestConcurrency:
    def test_concurrent_start_calls_never_both_win(self, monkeypatch):
        """Mirrors the canonical atomic-claim regression test
        (`AutonomousPaperTrader`'s) -- two threads racing `start()` must
        result in exactly one live thread, never two, and never a
        deadlock/silent double-start."""
        monkeypatch.setattr("futures_bot.collaboration.git_watcher.git_info.changed_files", lambda: [])
        watcher = GitWatcherScheduler()
        results = []

        def _try_start():
            try:
                watcher.start(interval_seconds=1)
                results.append("started")
            except RuntimeError:
                results.append("rejected")

        threads = [threading.Thread(target=_try_start) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        watcher.stop(timeout=5)
        assert results.count("started") == 1
        assert results.count("rejected") == 4
