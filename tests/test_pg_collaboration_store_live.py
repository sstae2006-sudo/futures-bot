"""Behavior verification for `PgCollaborationStore` against a *real*
Postgres/TimescaleDB server, mirroring `test_pg_account_store_live.py`'s
structure. Skipped automatically unless BOTH a reachable server is
configured via `FUTURES_BOT_DATABASE_URL` AND the destructive-test
opt-in is given (see `tests/_live_test_guard.py`'s module docstring,
KNOWN_ISSUES.md ISSUE-041 -- this module TRUNCATEs real tables in its
cleanup fixture).
"""

from __future__ import annotations

import uuid

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("psycopg")

from futures_bot.db.engine import dispose_engine  # noqa: E402
from tests._live_test_guard import live_server_reachable, skip_reason  # noqa: E402

pytestmark = pytest.mark.skipif(not live_server_reachable(), reason=skip_reason())


@pytest.fixture()
def store(live_database_url):
    from futures_bot.collaboration.pg_store import PgCollaborationStore

    s = PgCollaborationStore()
    s.ensure_schema()
    yield s
    from sqlalchemy import text

    with s._engine.begin() as conn:  # noqa: SLF001 -- test-only, direct cleanup
        conn.execute(text("TRUNCATE work_item_activity, work_items"))
    s.close()


@pytest.fixture(scope="module", autouse=True)
def _dispose_engine_after_module():
    yield
    dispose_engine()


def _item_id() -> str:
    return uuid.uuid4().hex[:12]


class TestWorkItemLifecycle:
    def test_create_claim_release_complete_round_trip(self, store):
        item_id = _item_id()
        item = store.create_work_item(item_id=item_id, title="Task", estimated_files=["src/a.py"])
        assert item["status"] == "open"
        assert item["estimated_files"] == ["src/a.py"]
        assert isinstance(item["created_at"], str)

        claimed = store.claim_work_item(item_id, "u1")
        assert claimed["status"] == "claimed"
        assert claimed["owner_user_id"] == "u1"

        released = store.release_work_item(item_id, actor_user_id="u1")
        assert released["status"] == "open"

        store.claim_work_item(item_id, "u2")
        completed = store.complete_work_item(item_id, actor_user_id="u2")
        assert completed["status"] == "completed"

    def test_claiming_already_claimed_raises(self, store):
        from futures_bot.collaboration.store import CollaborationError

        item_id = _item_id()
        store.create_work_item(item_id=item_id, title="Task", owner_user_id="u1")

        with pytest.raises(CollaborationError, match="already claimed"):
            store.claim_work_item(item_id, "u2")

    def test_fetch_active_work_items_excludes_completed(self, store):
        id_a, id_b = _item_id(), _item_id()
        store.create_work_item(item_id=id_a, title="Done")
        store.create_work_item(item_id=id_b, title="Still open")
        store.complete_work_item(id_a)

        active_ids = {i["id"] for i in store.fetch_active_work_items()}
        assert id_a not in active_ids
        assert id_b in active_ids

    def test_activity_log_records_every_transition(self, store):
        item_id = _item_id()
        store.create_work_item(item_id=item_id, title="Task")
        store.claim_work_item(item_id, "u1")
        store.complete_work_item(item_id, actor_user_id="u1")

        events = [a["event"] for a in reversed(store.fetch_activity(work_item_id=item_id))]

        assert events == ["created", "claimed", "completed"]


class TestOwnerTypeAndStatusLifecycle:
    def test_owner_type_defaults_to_human(self, store):
        item = store.create_work_item(item_id=_item_id(), title="Task")
        assert item["owner_type"] == "human"

    def test_owner_type_can_be_ai(self, store):
        item = store.create_work_item(item_id=_item_id(), title="Task", owner_type="ai")
        assert item["owner_type"] == "ai"

    def test_update_status_moves_through_lifecycle(self, store):
        item_id = _item_id()
        store.create_work_item(item_id=item_id, title="Task")

        moved = store.update_status(item_id, "in_progress", actor_user_id="u1")

        assert moved["status"] == "in_progress"
        event = next(a for a in store.fetch_activity(work_item_id=item_id) if a["event"] == "status_changed")
        assert event["detail"] == "open->in_progress"

    def test_update_status_rejects_open_claimed_completed(self, store):
        from futures_bot.collaboration.store import CollaborationError

        item_id = _item_id()
        store.create_work_item(item_id=item_id, title="Task")

        with pytest.raises(CollaborationError, match="Unknown status"):
            store.update_status(item_id, "completed")
