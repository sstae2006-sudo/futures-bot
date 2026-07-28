"""Unit tests for `collaboration.store.CollaborationStore` (SQLite) -- the
Active Work Registry backing the Team Collaboration MVP.
"""

from __future__ import annotations

import pytest

from futures_bot.collaboration.store import CollaborationError, CollaborationStore


@pytest.fixture
def store(tmp_path):
    s = CollaborationStore(tmp_path / "collab_test.db")
    yield s
    s.close()


class TestCreateAndFetch:
    def test_create_without_owner_is_open(self, store):
        item = store.create_work_item(item_id="w1", title="Task", estimated_files=["a.py"])
        assert item["status"] == "open"
        assert item["owner_user_id"] is None
        assert item["estimated_files"] == ["a.py"]

    def test_create_with_owner_is_claimed(self, store):
        item = store.create_work_item(item_id="w1", title="Task", owner_user_id="u1")
        assert item["status"] == "claimed"
        assert item["owner_user_id"] == "u1"

    def test_create_rejects_unknown_priority(self, store):
        with pytest.raises(CollaborationError, match="Unknown priority"):
            store.create_work_item(item_id="w1", title="Task", priority="urgent!!!")

    def test_fetch_unknown_returns_none(self, store):
        assert store.fetch_work_item("does-not-exist") is None

    def test_estimated_files_round_trips_as_a_list(self, store):
        store.create_work_item(item_id="w1", title="Task", estimated_files=["a.py", "b.py"])
        assert store.fetch_work_item("w1")["estimated_files"] == ["a.py", "b.py"]

    def test_estimated_files_defaults_to_empty_list(self, store):
        store.create_work_item(item_id="w1", title="Task")
        assert store.fetch_work_item("w1")["estimated_files"] == []


class TestListAndFilterByStatus:
    def test_fetch_work_items_filters_by_status(self, store):
        store.create_work_item(item_id="w1", title="Open", estimated_files=[])
        store.create_work_item(item_id="w2", title="Claimed", owner_user_id="u1")

        assert [i["id"] for i in store.fetch_work_items(status="open")] == ["w1"]
        assert [i["id"] for i in store.fetch_work_items(status="claimed")] == ["w2"]

    def test_fetch_active_work_items_excludes_completed(self, store):
        store.create_work_item(item_id="w1", title="Done")
        store.create_work_item(item_id="w2", title="Still open")
        store.complete_work_item("w1")

        assert [i["id"] for i in store.fetch_active_work_items()] == ["w2"]

    def test_fetch_active_work_items_can_exclude_an_id(self, store):
        store.create_work_item(item_id="w1", title="A")
        store.create_work_item(item_id="w2", title="B")

        active = store.fetch_active_work_items(exclude_id="w1")

        assert [i["id"] for i in active] == ["w2"]


class TestOrgScoping:
    def test_org_id_defaults_to_none(self, store):
        item = store.create_work_item(item_id="w1", title="Task")
        assert item["org_id"] is None

    def test_fetch_work_items_filters_by_org(self, store):
        store.create_work_item(item_id="w1", title="Org A task", org_id="org-a")
        store.create_work_item(item_id="w2", title="Org B task", org_id="org-b")
        store.create_work_item(item_id="w3", title="Unscoped task")

        assert [i["id"] for i in store.fetch_work_items(org_id="org-a")] == ["w1"]
        assert {i["id"] for i in store.fetch_work_items()} == {"w1", "w2", "w3"}

    def test_fetch_active_work_items_filters_by_org(self, store):
        store.create_work_item(item_id="w1", title="Org A", org_id="org-a")
        store.create_work_item(item_id="w2", title="Org B", org_id="org-b")

        active = store.fetch_active_work_items(org_id="org-a")

        assert [i["id"] for i in active] == ["w1"]

    def test_org_id_and_status_filters_combine(self, store):
        store.create_work_item(item_id="w1", title="Org A open", org_id="org-a")
        store.create_work_item(item_id="w2", title="Org A claimed", org_id="org-a", owner_user_id="u1")
        store.create_work_item(item_id="w3", title="Org B open", org_id="org-b")

        result = store.fetch_work_items(status="open", org_id="org-a")

        assert [i["id"] for i in result] == ["w1"]


class TestClaimReleaseCompleteReassign:
    def test_claim_sets_owner_and_status(self, store):
        store.create_work_item(item_id="w1", title="Task")
        claimed = store.claim_work_item("w1", "u1")
        assert claimed["status"] == "claimed"
        assert claimed["owner_user_id"] == "u1"

    def test_claiming_already_claimed_by_another_user_raises(self, store):
        store.create_work_item(item_id="w1", title="Task", owner_user_id="u1")
        with pytest.raises(CollaborationError, match="already claimed"):
            store.claim_work_item("w1", "u2")

    def test_reclaiming_by_the_same_owner_is_a_no_op_success(self, store):
        store.create_work_item(item_id="w1", title="Task", owner_user_id="u1")
        claimed = store.claim_work_item("w1", "u1")
        assert claimed["owner_user_id"] == "u1"

    def test_claim_unknown_item_raises(self, store):
        with pytest.raises(CollaborationError, match="No such work item"):
            store.claim_work_item("does-not-exist", "u1")

    def test_release_clears_owner_and_reopens(self, store):
        store.create_work_item(item_id="w1", title="Task", owner_user_id="u1")
        released = store.release_work_item("w1", actor_user_id="u1")
        assert released["status"] == "open"
        assert released["owner_user_id"] is None

    def test_complete_sets_status(self, store):
        store.create_work_item(item_id="w1", title="Task", owner_user_id="u1")
        completed = store.complete_work_item("w1", actor_user_id="u1")
        assert completed["status"] == "completed"

    def test_reassign_changes_owner_and_stays_claimed(self, store):
        store.create_work_item(item_id="w1", title="Task", owner_user_id="u1")
        reassigned = store.reassign_work_item("w1", "u2", actor_user_id="u1")
        assert reassigned["status"] == "claimed"
        assert reassigned["owner_user_id"] == "u2"

    def test_release_unknown_item_raises(self, store):
        with pytest.raises(CollaborationError, match="No such work item"):
            store.release_work_item("does-not-exist")

    def test_concurrent_claims_never_both_win(self, tmp_path):
        """Regression test (Stabilization Mode, 2026-07-28): `claim_work_item`
        used to read the item, decide in Python whether the claim was
        allowed, then run an unconditional `UPDATE` -- two concurrent
        callers (two humans, or two AI sessions, racing to claim the same
        item) could both pass the Python-level check before either
        committed, and whichever `UPDATE` committed last would silently
        win: *both* callers would get back a 200 claiming ownership, with
        no error telling either of them a conflict happened. Fixed by
        re-checking ownership in the `UPDATE`'s own `WHERE` clause and
        inspecting `rowcount` -- verified here with two real threads, each
        on its own `CollaborationStore` connection (matching how two
        separate HTTP requests would each get their own store instance),
        and an artificial delay between the initial read and the write to
        reliably widen the race window rather than relying on incidental
        thread-scheduling luck."""
        import threading
        import time

        path = tmp_path / "concurrent_claim_test.db"
        setup_store = CollaborationStore(path)
        setup_store.create_work_item(item_id="w1", title="Task")
        setup_store.close()

        results: list[object] = []
        results_lock = threading.Lock()

        def claim_as(user_id: str) -> None:
            store = CollaborationStore(path)
            original_fetch = store.fetch_work_item

            def slow_fetch(item_id: str):
                row = original_fetch(item_id)
                time.sleep(0.2)
                return row

            store.fetch_work_item = slow_fetch  # type: ignore[method-assign]
            try:
                outcome: object = store.claim_work_item("w1", user_id)
            except CollaborationError as exc:
                outcome = exc
            with results_lock:
                results.append(outcome)
            store.close()

        threads = [threading.Thread(target=claim_as, args=(u,)) for u in ("alice", "bob")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        successes = [r for r in results if isinstance(r, dict)]
        failures = [r for r in results if isinstance(r, CollaborationError)]
        assert len(successes) == 1, f"expected exactly one winning claim, got {results!r}"
        assert len(failures) == 1
        assert "already claimed" in str(failures[0])

        final = CollaborationStore(path)
        try:
            item = final.fetch_work_item("w1")
            assert item is not None
            assert item["owner_user_id"] == successes[0]["owner_user_id"]
        finally:
            final.close()


class TestActivityLog:
    def test_every_transition_is_logged(self, store):
        store.create_work_item(item_id="w1", title="Task")
        store.claim_work_item("w1", "u1")
        store.release_work_item("w1", actor_user_id="u1")
        store.claim_work_item("w1", "u2")
        store.complete_work_item("w1", actor_user_id="u2")

        events = [a["event"] for a in reversed(store.fetch_activity(work_item_id="w1"))]

        assert events == ["created", "claimed", "released", "claimed", "completed"]

    def test_activity_records_the_actor(self, store):
        store.create_work_item(item_id="w1", title="Task")
        store.claim_work_item("w1", "u1")

        claimed_event = next(a for a in store.fetch_activity(work_item_id="w1") if a["event"] == "claimed")

        assert claimed_event["actor_user_id"] == "u1"

    def test_fetch_activity_without_filter_returns_everything(self, store):
        store.create_work_item(item_id="w1", title="A")
        store.create_work_item(item_id="w2", title="B")

        all_events = store.fetch_activity()

        assert len(all_events) == 2

    def test_activity_within_the_same_second_stays_in_insertion_order(self, store):
        """`created_at` is only second-resolution -- three rapid-fire
        transitions (entirely realistic for an automated/AI-driven
        workflow) can tie on that column alone. The `rowid` tiebreaker in
        `fetch_activity` is what keeps "most recent first" correct rather
        than merely usually-correct when that happens."""
        store.create_work_item(item_id="w1", title="Task")
        store.claim_work_item("w1", "u1")
        store.complete_work_item("w1", actor_user_id="u1")

        events = [a["event"] for a in store.fetch_activity(work_item_id="w1")]

        assert events == ["completed", "claimed", "created"]


class TestOwnerType:
    def test_defaults_to_human(self, store):
        item = store.create_work_item(item_id="w1", title="Task")
        assert item["owner_type"] == "human"

    def test_can_be_created_as_ai(self, store):
        item = store.create_work_item(item_id="w1", title="Task", owner_type="ai")
        assert item["owner_type"] == "ai"

    def test_rejects_unknown_owner_type(self, store):
        with pytest.raises(CollaborationError, match="Unknown owner_type"):
            store.create_work_item(item_id="w1", title="Task", owner_type="robot")


class TestUpdateStatus:
    def test_moves_through_manual_lifecycle_stages(self, store):
        store.create_work_item(item_id="w1", title="Task", owner_user_id="u1")

        for stage in ("in_progress", "testing", "ready_for_review", "merged"):
            updated = store.update_status("w1", stage, actor_user_id="u1")
            assert updated["status"] == stage

    def test_rejects_open_claimed_completed(self, store):
        store.create_work_item(item_id="w1", title="Task")
        for forbidden in ("open", "claimed", "completed"):
            with pytest.raises(CollaborationError, match="Unknown status"):
                store.update_status("w1", forbidden)

    def test_rejects_unknown_status(self, store):
        store.create_work_item(item_id="w1", title="Task")
        with pytest.raises(CollaborationError, match="Unknown status"):
            store.update_status("w1", "blocked")

    def test_unknown_item_raises(self, store):
        with pytest.raises(CollaborationError, match="No such work item"):
            store.update_status("does-not-exist", "testing")

    def test_status_change_is_logged(self, store):
        store.create_work_item(item_id="w1", title="Task")
        store.update_status("w1", "in_progress", actor_user_id="u1")

        event = next(a for a in store.fetch_activity(work_item_id="w1") if a["event"] == "status_changed")
        assert event["actor_user_id"] == "u1"
        assert event["detail"] == "open->in_progress"

    def test_allows_backward_moves(self, store):
        store.create_work_item(item_id="w1", title="Task")
        store.update_status("w1", "ready_for_review")
        moved_back = store.update_status("w1", "in_progress")
        assert moved_back["status"] == "in_progress"
