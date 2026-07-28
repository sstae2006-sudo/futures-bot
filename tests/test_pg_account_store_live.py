"""Behavior verification for `PgAccountStore` against a *real* Postgres/
TimescaleDB server, mirroring `test_pg_market_data_store_live.py`'s own
structure. Skipped automatically whenever no reachable server is
configured via `FUTURES_BOT_DATABASE_URL`, so the default single-developer
test run is completely unaffected.

    docker compose -f deploy/docker-compose.yml up -d timescaledb
    export FUTURES_BOT_DATABASE_URL=postgresql+psycopg://futures_bot:futures_bot_dev_only@127.0.0.1:5432/futures_bot
    pytest tests/test_pg_account_store_live.py -v
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
    from futures_bot.accounts.pg_store import PgAccountStore
    from futures_bot.accounts.store import AccountError

    s = PgAccountStore()
    s.ensure_schema()
    yield s, AccountError
    # Truncate rather than drop: this may be a shared team-dev instance,
    # and dropping tables would fight Alembic's ownership of the schema.
    from sqlalchemy import text

    with s._engine.begin() as conn:  # noqa: SLF001 -- test-only, direct cleanup
        conn.execute(text("TRUNCATE users, organizations"))
    s.close()


@pytest.fixture(scope="module", autouse=True)
def _dispose_engine_after_module():
    yield
    dispose_engine()


def _org_id() -> str:
    return uuid.uuid4().hex[:12]


def _user_id() -> str:
    return uuid.uuid4().hex[:12]


class TestOrganizationsAndUsers:
    def test_create_and_fetch_round_trip(self, store):
        s, _ = store
        org_id = _org_id()
        org = s.create_organization(org_id=org_id, name=f"Acme {org_id}")
        assert s.fetch_organization(org_id) == org

        user_id = _user_id()
        user = s.create_user(
            user_id=user_id, display_name="Seth", username=f"seth-{user_id}",
            org_id=org_id, role="owner", email="seth@example.com",
        )
        assert s.fetch_user(user_id) == user
        assert s.fetch_user_by_username(f"seth-{user_id}") == user
        # Every timestamp column comes back as a plain string, matching
        # AccountStore's SQLite contract -- confirmed directly, not assumed.
        assert isinstance(user["created_at"], str)

    def test_duplicate_username_raises(self, store):
        s, AccountError = store
        org_id = _org_id()
        s.create_organization(org_id=org_id, name=f"Acme {org_id}")
        username = f"dup-{uuid.uuid4().hex[:8]}"
        s.create_user(user_id=_user_id(), display_name="A", username=username, org_id=org_id, role="owner")

        with pytest.raises(AccountError, match="already exists"):
            s.create_user(user_id=_user_id(), display_name="B", username=username, org_id=org_id, role="member")

    def test_create_user_requires_existing_organization(self, store):
        s, AccountError = store
        with pytest.raises(AccountError, match="No such organization"):
            s.create_user(
                user_id=_user_id(), display_name="Seth", username=f"seth-{uuid.uuid4().hex[:8]}",
                org_id="does-not-exist", role="owner",
            )

    def test_update_and_touch_last_active(self, store):
        s, _ = store
        org_id = _org_id()
        s.create_organization(org_id=org_id, name=f"Acme {org_id}")
        user_id = _user_id()
        s.create_user(
            user_id=user_id, display_name="Seth", username=f"seth-{user_id}", org_id=org_id, role="member",
        )

        updated = s.update_user(user_id, role="admin")
        assert updated["role"] == "admin"
        assert updated["last_active_at"] is None

        touched = s.touch_last_active(user_id)
        assert touched["last_active_at"] is not None
        assert isinstance(touched["last_active_at"], str)

    def test_fetch_users_filters_by_org(self, store):
        s, _ = store
        org_a, org_b = _org_id(), _org_id()
        s.create_organization(org_id=org_a, name=f"A {org_a}")
        s.create_organization(org_id=org_b, name=f"B {org_b}")
        s.create_user(user_id=_user_id(), display_name="Alice", username=f"alice-{org_a}", org_id=org_a, role="owner")
        s.create_user(user_id=_user_id(), display_name="Bob", username=f"bob-{org_b}", org_id=org_b, role="owner")

        usernames = {u["username"] for u in s.fetch_users(org_id=org_a)}
        assert usernames == {f"alice-{org_a}"}

    def test_api_key_generated_and_regeneratable(self, store):
        s, _ = store
        org_id = _org_id()
        s.create_organization(org_id=org_id, name=f"Acme {org_id}")
        user_id = _user_id()
        user = s.create_user(
            user_id=user_id, display_name="Seth", username=f"seth-{user_id}", org_id=org_id, role="owner",
        )
        assert user["api_key"].startswith("fbot_")

        regenerated = s.regenerate_api_key(user_id)
        assert regenerated["api_key"] != user["api_key"]

    def test_profile_fields_round_trip(self, store):
        s, _ = store
        org_id = _org_id()
        s.create_organization(org_id=org_id, name=f"Acme {org_id}")
        user_id = _user_id()
        s.create_user(user_id=user_id, display_name="Seth", username=f"seth-{user_id}", org_id=org_id, role="owner")

        updated = s.update_user(
            user_id, timezone="America/New_York", notification_preferences={"email": True},
        )

        assert updated["timezone"] == "America/New_York"
        assert updated["notification_preferences"] == {"email": True}

    def test_update_organization_renames(self, store):
        s, _ = store
        org_id = _org_id()
        s.create_organization(org_id=org_id, name=f"Acme {org_id}")

        updated = s.update_organization(org_id, name=f"Renamed {org_id}")

        assert updated["name"] == f"Renamed {org_id}"
