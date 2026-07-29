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


class TestDrafts:
    """SIL Phase 4's git-watcher output -- a draft is a normal work item
    (visible in fetch_work_items/fetch_active_work_items, subject to
    overlap detection) except that it must be explicitly approved or
    discarded rather than acted on directly."""

    def test_create_draft_defaults_is_draft_false(self, store):
        """The default is a real item -- is_draft=True must be explicit,
        never accidental."""
        item = store.create_work_item(item_id="w1", title="Task")
        assert item["is_draft"] is False

    def test_create_with_is_draft_true(self, store):
        item = store.create_work_item(item_id="w1", title="Auto-detected", is_draft=True)
        assert item["is_draft"] is True

    def test_fetch_draft_work_items_only_returns_drafts(self, store):
        store.create_work_item(item_id="w1", title="Real", is_draft=False)
        store.create_work_item(item_id="w2", title="Draft", is_draft=True)

        assert [i["id"] for i in store.fetch_draft_work_items()] == ["w2"]

    def test_drafts_are_visible_in_fetch_work_items_and_active_items(self, store):
        """A draft is a normal work item in every other respect --
        overlap detection and Mission Control's normal listings must see
        it, not just the dedicated drafts endpoint."""
        store.create_work_item(item_id="w1", title="Draft", estimated_files=["a.py"], is_draft=True)

        assert [i["id"] for i in store.fetch_work_items()] == ["w1"]
        assert [i["id"] for i in store.fetch_active_work_items()] == ["w1"]

    def test_approve_draft_clears_the_flag(self, store):
        store.create_work_item(item_id="w1", title="Draft", is_draft=True)

        approved = store.approve_draft_work_item("w1")

        assert approved["is_draft"] is False
        assert store.fetch_work_item("w1")["is_draft"] is False

    def test_approve_logs_activity(self, store):
        store.create_work_item(item_id="w1", title="Draft", is_draft=True)
        store.approve_draft_work_item("w1", actor_user_id="u1")

        event = next(a for a in store.fetch_activity(work_item_id="w1") if a["event"] == "draft_approved")
        assert event["actor_user_id"] == "u1"

    def test_approve_rejects_a_non_draft_item(self, store):
        store.create_work_item(item_id="w1", title="Real")
        with pytest.raises(CollaborationError, match="not a draft"):
            store.approve_draft_work_item("w1")

    def test_approve_rejects_unknown_item(self, store):
        with pytest.raises(CollaborationError, match="No such work item"):
            store.approve_draft_work_item("does-not-exist")

    def test_discard_deletes_the_row(self, store):
        store.create_work_item(item_id="w1", title="Draft", is_draft=True)

        store.discard_draft_work_item("w1")

        assert store.fetch_work_item("w1") is None

    def test_discard_deletes_its_activity_log_too(self, store):
        """Without this, the FK from work_item_activity would block the
        DELETE outright (PRAGMA foreign_keys = ON)."""
        store.create_work_item(item_id="w1", title="Draft", is_draft=True)

        store.discard_draft_work_item("w1")  # must not raise an IntegrityError

        assert store.fetch_activity(work_item_id="w1") == []

    def test_discard_rejects_a_non_draft_item(self, store):
        """Refuses to delete a real work item -- the whole point of this
        method existing separately from a generic delete."""
        store.create_work_item(item_id="w1", title="Real")
        with pytest.raises(CollaborationError, match="not a draft"):
            store.discard_draft_work_item("w1")
        assert store.fetch_work_item("w1") is not None  # still there

    def test_discard_rejects_unknown_item(self, store):
        with pytest.raises(CollaborationError, match="No such work item"):
            store.discard_draft_work_item("does-not-exist")


class TestWorkers:
    """SIL Phase 6 "Integration Coordinator" Milestone 1 -- the Worker
    Registry. Heartbeat is an upsert (not update-only like
    accounts/store.py::touch_last_active) -- see
    `heartbeat_worker`'s own docstring for why."""

    def test_first_heartbeat_creates_the_worker(self, store):
        worker = store.heartbeat_worker(worker_id="w1", worker_type="claude_code_session", display_name="Session 1")

        assert worker["id"] == "w1"
        assert worker["worker_type"] == "claude_code_session"
        assert worker["status"] == "online"
        assert worker["capabilities"] == []

    def test_second_heartbeat_updates_in_place_no_duplicate_row(self, store):
        store.heartbeat_worker(worker_id="w1", display_name="Session 1", status="online")
        store.heartbeat_worker(worker_id="w1", display_name="Session 1", status="idle")

        workers = store.fetch_workers()
        assert len(workers) == 1
        assert workers[0]["status"] == "idle"

    def test_heartbeat_with_a_nonexistent_current_work_item_id_succeeds(self, store):
        """Pinned as a regression test, not just a docstring note, so a
        future contributor can't silently "fix" this into a hard FK
        without an explicit discussion -- current_work_item_id is a soft
        reference by design, same convention work_items.owner_user_id
        already establishes."""
        worker = store.heartbeat_worker(
            worker_id="w1", display_name="Session 1", current_work_item_id="does-not-exist-at-all",
        )

        assert worker["current_work_item_id"] == "does-not-exist-at-all"

    def test_heartbeat_rejects_unknown_worker_type(self, store):
        with pytest.raises(CollaborationError, match="Unknown worker_type"):
            store.heartbeat_worker(worker_id="w1", display_name="X", worker_type="not_a_real_type")

    def test_heartbeat_rejects_unknown_status(self, store):
        with pytest.raises(CollaborationError, match="Unknown status"):
            store.heartbeat_worker(worker_id="w1", display_name="X", status="not_a_real_status")

    def test_capabilities_round_trip_as_a_list(self, store):
        store.heartbeat_worker(worker_id="w1", display_name="X", capabilities=["backend", "testing"])

        assert store.fetch_worker("w1")["capabilities"] == ["backend", "testing"]

    def test_capabilities_defaults_to_empty_list(self, store):
        store.heartbeat_worker(worker_id="w1", display_name="X")

        assert store.fetch_worker("w1")["capabilities"] == []

    def test_fetch_worker_unknown_returns_none(self, store):
        assert store.fetch_worker("does-not-exist") is None

    def test_fetch_workers_filters_by_org_id(self, store):
        store.heartbeat_worker(worker_id="w1", display_name="A", org_id="org-a")
        store.heartbeat_worker(worker_id="w2", display_name="B", org_id="org-b")

        assert [w["id"] for w in store.fetch_workers(org_id="org-a")] == ["w1"]

    def test_fetch_workers_filters_by_status(self, store):
        store.heartbeat_worker(worker_id="w1", display_name="A", status="online")
        store.heartbeat_worker(worker_id="w2", display_name="B", status="offline")

        assert [w["id"] for w in store.fetch_workers(status="offline")] == ["w2"]

    def test_fetch_workers_filters_by_capability(self, store):
        store.heartbeat_worker(worker_id="w1", display_name="A", capabilities=["frontend"])
        store.heartbeat_worker(worker_id="w2", display_name="B", capabilities=["backend", "database"])

        assert [w["id"] for w in store.fetch_workers(capability="database")] == ["w2"]

    def test_fetch_workers_with_no_capability_match_returns_empty(self, store):
        store.heartbeat_worker(worker_id="w1", display_name="A", capabilities=["frontend"])

        assert store.fetch_workers(capability="machine-learning") == []


class TestSetWorkerStatus:
    """SIL Phase 6 Milestone 2 -- the maintenance scheduler's stale-worker
    cleanup write path. Deliberately separate from `heartbeat_worker`:
    must NOT bump `last_heartbeat_at` (see `set_worker_status`'s own
    docstring for why)."""

    def test_sets_status_without_touching_last_heartbeat_at(self, store):
        worker = store.heartbeat_worker(worker_id="w1", display_name="X", status="online")
        original_heartbeat = worker["last_heartbeat_at"]

        updated = store.set_worker_status("w1", "offline")

        assert updated["status"] == "offline"
        assert updated["last_heartbeat_at"] == original_heartbeat

    def test_rejects_unknown_status(self, store):
        store.heartbeat_worker(worker_id="w1", display_name="X")
        with pytest.raises(CollaborationError, match="Unknown status"):
            store.set_worker_status("w1", "not_a_real_status")

    def test_rejects_nonexistent_worker(self, store):
        with pytest.raises(CollaborationError, match="No such worker"):
            store.set_worker_status("does-not-exist", "offline")

    def test_worker_marked_offline_recovers_on_next_heartbeat(self, store):
        """SIL Phase 6 Milestone 2 reliability pass -- the maintenance
        scheduler's stale-worker cleanup (`set_worker_status`) must never
        permanently strand a worker: its very next heartbeat should bring
        it back `online` (and, since it's a real heartbeat, `last_heartbeat_at`
        SHOULD move forward this time -- unlike `set_worker_status` itself)."""
        store.heartbeat_worker(worker_id="w1", display_name="X", status="online")
        store.set_worker_status("w1", "offline")
        assert store.fetch_worker("w1")["status"] == "offline"

        recovered = store.heartbeat_worker(worker_id="w1", display_name="X", status="online")

        assert recovered["status"] == "online"


class TestIntegrationReviews:
    """SIL Phase 6 Milestone 2 -- the persistent Integration Review. Unlike
    every other collaboration/ computation (merge_readiness, overlap_v2,
    worker staleness), this is a deliberate exception to "compute live,
    never persist": every call inserts a new, immutable row."""

    def _make_item(self, store):
        return store.create_work_item(item_id="wi1", title="Test item", estimated_files=["src/futures_bot/risk/manager.py"])

    def _review_kwargs(self, **overrides):
        kwargs = dict(
            review_id="rev1", work_item_id="wi1", worker_id="w1", branch="main", status_at_review="testing",
            confidence_score=85, risk_level="low", level="ready", related_work_item_ids=["wi2"],
            affected_subsystems=["Risk Management"], conflict_resolutions=[{"work_item_id": "wi2", "title": "x"}],
            validation_recommendation={"recommended_tests": ["tests/test_risk.py"]}, readiness_note=None,
            summary="summary text", recommendation="proceed",
        )
        kwargs.update(overrides)
        return kwargs

    def test_create_review_round_trips_all_fields(self, store):
        self._make_item(store)
        review = store.create_integration_review(**self._review_kwargs())

        assert review["id"] == "rev1"
        assert review["work_item_id"] == "wi1"
        assert review["confidence_score"] == 85
        assert review["related_work_item_ids"] == ["wi2"]
        assert review["affected_subsystems"] == ["Risk Management"]
        assert review["conflict_resolutions"] == [{"work_item_id": "wi2", "title": "x"}]
        assert review["validation_recommendation"] == {"recommended_tests": ["tests/test_risk.py"]}

    def test_two_reviews_for_the_same_item_never_overwrite_each_other(self, store):
        """The core "never overwrite historical reviews" contract --
        pinned as a real regression test, not just documented."""
        self._make_item(store)
        store.create_integration_review(**self._review_kwargs(review_id="rev1", confidence_score=50))
        store.create_integration_review(**self._review_kwargs(review_id="rev2", confidence_score=90))

        reviews = store.fetch_integration_reviews("wi1")
        assert len(reviews) == 2
        assert {r["id"] for r in reviews} == {"rev1", "rev2"}

    def test_fetch_reviews_newest_first(self, store):
        self._make_item(store)
        store.create_integration_review(**self._review_kwargs(review_id="rev1"))
        store.create_integration_review(**self._review_kwargs(review_id="rev2"))

        reviews = store.fetch_integration_reviews("wi1")
        assert [r["id"] for r in reviews] == ["rev2", "rev1"]

    def test_fetch_latest_review_returns_the_most_recent(self, store):
        self._make_item(store)
        store.create_integration_review(**self._review_kwargs(review_id="rev1", confidence_score=50))
        store.create_integration_review(**self._review_kwargs(review_id="rev2", confidence_score=90))

        assert store.fetch_latest_integration_review("wi1")["id"] == "rev2"

    def test_fetch_latest_review_for_item_with_none_returns_none(self, store):
        self._make_item(store)
        assert store.fetch_latest_integration_review("wi1") is None

    def test_create_review_logs_review_generated_activity(self, store):
        self._make_item(store)
        store.create_integration_review(**self._review_kwargs(level="needs_review"))

        events = [a["event"] for a in store.fetch_activity(work_item_id="wi1")]
        assert "review_generated" in events
        assert "integration_recommended" not in events  # only logged when level == "ready"

    def test_create_review_logs_integration_recommended_when_ready(self, store):
        self._make_item(store)
        store.create_integration_review(**self._review_kwargs(level="ready"))

        events = [a["event"] for a in store.fetch_activity(work_item_id="wi1")]
        assert "integration_recommended" in events

    def test_reviews_scoped_to_their_own_work_item(self, store):
        self._make_item(store)
        store.create_work_item(item_id="wi2", title="Other item")
        store.create_integration_review(**self._review_kwargs(review_id="rev1", work_item_id="wi1"))
        store.create_integration_review(**self._review_kwargs(review_id="rev2", work_item_id="wi2"))

        assert [r["id"] for r in store.fetch_integration_reviews("wi1")] == ["rev1"]
        assert [r["id"] for r in store.fetch_integration_reviews("wi2")] == ["rev2"]


class TestWorkersConcurrency:
    """Mirrors `claim_work_item`'s two-thread regression test -- heartbeat
    races are benign (last-write-wins is correct semantics for "who's
    alive"), but concurrent writers must never crash or lose rows."""

    def test_concurrent_heartbeats_from_many_workers_never_crash_or_lose_rows(self, tmp_path):
        import threading

        from futures_bot.collaboration.store import CollaborationStore

        store_path = tmp_path / "concurrent_workers.db"
        CollaborationStore(store_path).close()  # ensure schema exists before threads race on creating it
        errors = []

        def _heartbeat(n):
            try:
                s = CollaborationStore(store_path)
                s.heartbeat_worker(worker_id=f"worker-{n}", display_name=f"Worker {n}")
                s.close()
            except Exception as exc:  # noqa: BLE001 -- captured for the assertion below, not swallowed silently
                errors.append((n, exc))

        threads = [threading.Thread(target=_heartbeat, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert errors == []
        final_store = CollaborationStore(store_path)
        assert len(final_store.fetch_workers()) == 20
        final_store.close()


class TestIntegrationReviewsConcurrency:
    """SIL Phase 6 Milestone 2 reliability pass -- concurrent review
    generation for the same work item must never crash or lose a row
    (append-only inserts, no shared mutable state to race on, unlike
    `claim_work_item`'s guarded UPDATE)."""

    def test_concurrent_review_creation_never_crashes_or_loses_rows(self, tmp_path):
        import threading

        from futures_bot.collaboration.store import CollaborationStore

        store_path = tmp_path / "concurrent_reviews.db"
        setup_store = CollaborationStore(store_path)
        setup_store.create_work_item(item_id="wi1", title="Test item")
        setup_store.close()
        errors = []

        def _create_review(n):
            try:
                s = CollaborationStore(store_path)
                s.create_integration_review(
                    review_id=f"rev-{n}", work_item_id="wi1", worker_id=None, branch=None,
                    status_at_review="testing", confidence_score=50, risk_level="low", level="needs_review",
                    related_work_item_ids=[], affected_subsystems=[], conflict_resolutions=[],
                    validation_recommendation={}, readiness_note=None, summary="s", recommendation="r",
                )
                s.close()
            except Exception as exc:  # noqa: BLE001 -- captured for the assertion below, not swallowed silently
                errors.append((n, exc))

        threads = [threading.Thread(target=_create_review, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert errors == []
        final_store = CollaborationStore(store_path)
        assert len(final_store.fetch_integration_reviews("wi1", limit=100)) == 20
        final_store.close()
