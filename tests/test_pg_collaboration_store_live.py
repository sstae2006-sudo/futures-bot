"""Behavior verification for `PgCollaborationStore` against a *real*
Postgres/TimescaleDB server, mirroring `test_pg_account_store_live.py`'s
structure. Skipped automatically whenever no reachable server is
configured via `FUTURES_BOT_DATABASE_URL`.
"""

from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("psycopg")

from futures_bot.db.engine import database_url, dispose_engine  # noqa: E402
from futures_bot.db.health import check_database_health  # noqa: E402


def _live_server_reachable() -> bool:
    if not database_url():
        return False
    return check_database_health().ok


pytestmark = pytest.mark.skipif(
    not _live_server_reachable(),
    reason=(
        f"No reachable Postgres/TimescaleDB at {os.environ.get('FUTURES_BOT_DATABASE_URL', '<unset>')} "
        "-- start the compose timescaledb service to run this module."
    ),
)


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
