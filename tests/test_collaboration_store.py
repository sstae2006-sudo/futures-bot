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
