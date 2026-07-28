"""Unit tests for `accounts.store.AccountStore` (SQLite) -- the
user/organization data model backing the Team Collaboration MVP. See
`accounts/store.py`'s module docstring: deliberately not an authentication
system.
"""

from __future__ import annotations

import pytest

from futures_bot.accounts.store import AccountError, AccountStore


@pytest.fixture
def store(tmp_path):
    s = AccountStore(tmp_path / "accounts_test.db")
    yield s
    s.close()


class TestOrganizations:
    def test_create_and_fetch(self, store):
        org = store.create_organization(org_id="org1", name="Acme Research")
        assert org["name"] == "Acme Research"
        assert store.fetch_organization("org1") == org

    def test_fetch_unknown_returns_none(self, store):
        assert store.fetch_organization("does-not-exist") is None

    def test_duplicate_name_rejected(self, store):
        store.create_organization(org_id="org1", name="Acme")
        with pytest.raises(AccountError, match="already exists"):
            store.create_organization(org_id="org2", name="Acme")

    def test_fetch_organizations_sorted_by_name(self, store):
        store.create_organization(org_id="org-z", name="Zeta")
        store.create_organization(org_id="org-a", name="Alpha")
        names = [o["name"] for o in store.fetch_organizations()]
        assert names == ["Alpha", "Zeta"]

    def test_update_organization_renames(self, store):
        store.create_organization(org_id="org1", name="Acme")
        updated = store.update_organization("org1", name="Acme Research")
        assert updated["name"] == "Acme Research"

    def test_update_organization_rejects_duplicate_name(self, store):
        store.create_organization(org_id="org1", name="Acme")
        store.create_organization(org_id="org2", name="Widgets")
        with pytest.raises(AccountError, match="already exists"):
            store.update_organization("org2", name="Acme")

    def test_update_organization_unknown_raises(self, store):
        with pytest.raises(AccountError, match="No such organization"):
            store.update_organization("does-not-exist", name="X")

    def test_update_organization_with_no_name_is_a_no_op(self, store):
        org = store.create_organization(org_id="org1", name="Acme")
        assert store.update_organization("org1") == org


class TestUsers:
    def test_create_requires_existing_organization(self, store):
        with pytest.raises(AccountError, match="No such organization"):
            store.create_user(
                user_id="u1", display_name="Seth", username="seth",
                org_id="does-not-exist", role="owner",
            )

    def test_create_rejects_unknown_role(self, store):
        store.create_organization(org_id="org1", name="Acme")
        with pytest.raises(AccountError, match="Unknown role"):
            store.create_user(
                user_id="u1", display_name="Seth", username="seth",
                org_id="org1", role="superuser",
            )

    def test_create_and_fetch(self, store):
        store.create_organization(org_id="org1", name="Acme")
        user = store.create_user(
            user_id="u1", display_name="Seth", username="seth", org_id="org1",
            role="owner", email="seth@example.com",
        )
        assert user["username"] == "seth"
        assert user["role"] == "owner"
        assert user["last_active_at"] is None
        assert store.fetch_user("u1") == user
        assert store.fetch_user_by_username("seth") == user

    def test_duplicate_username_rejected(self, store):
        store.create_organization(org_id="org1", name="Acme")
        store.create_user(user_id="u1", display_name="Seth", username="seth", org_id="org1", role="owner")
        with pytest.raises(AccountError, match="already exists"):
            store.create_user(user_id="u2", display_name="Other", username="seth", org_id="org1", role="member")

    def test_fetch_users_filters_by_org(self, store):
        store.create_organization(org_id="org1", name="Acme")
        store.create_organization(org_id="org2", name="Widgets")
        store.create_user(user_id="u1", display_name="A", username="a", org_id="org1", role="owner")
        store.create_user(user_id="u2", display_name="B", username="b", org_id="org2", role="owner")

        assert [u["username"] for u in store.fetch_users(org_id="org1")] == ["a"]
        assert {u["username"] for u in store.fetch_users()} == {"a", "b"}

    def test_update_user_only_changes_supplied_fields(self, store):
        store.create_organization(org_id="org1", name="Acme")
        store.create_user(
            user_id="u1", display_name="Seth", username="seth", org_id="org1",
            role="member", email="seth@example.com",
        )
        updated = store.update_user("u1", role="admin")
        assert updated["role"] == "admin"
        assert updated["display_name"] == "Seth"  # unchanged
        assert updated["email"] == "seth@example.com"  # unchanged

    def test_update_rejects_unknown_role(self, store):
        store.create_organization(org_id="org1", name="Acme")
        store.create_user(user_id="u1", display_name="Seth", username="seth", org_id="org1", role="member")
        with pytest.raises(AccountError, match="Unknown role"):
            store.update_user("u1", role="superuser")

    def test_update_unknown_user_raises(self, store):
        with pytest.raises(AccountError, match="No such user"):
            store.update_user("does-not-exist", role="admin")

    def test_touch_last_active_sets_a_timestamp(self, store):
        store.create_organization(org_id="org1", name="Acme")
        store.create_user(user_id="u1", display_name="Seth", username="seth", org_id="org1", role="member")
        assert store.fetch_user("u1")["last_active_at"] is None

        touched = store.touch_last_active("u1")

        assert touched["last_active_at"] is not None

    def test_touch_last_active_unknown_user_raises(self, store):
        with pytest.raises(AccountError, match="No such user"):
            store.touch_last_active("does-not-exist")


class TestApiKey:
    def test_generated_automatically_on_create(self, store):
        store.create_organization(org_id="org1", name="Acme")
        user = store.create_user(user_id="u1", display_name="Seth", username="seth", org_id="org1", role="owner")
        assert user["api_key"]
        assert user["api_key"].startswith("fbot_")

    def test_each_user_gets_a_distinct_key(self, store):
        store.create_organization(org_id="org1", name="Acme")
        u1 = store.create_user(user_id="u1", display_name="A", username="a", org_id="org1", role="owner")
        u2 = store.create_user(user_id="u2", display_name="B", username="b", org_id="org1", role="member")
        assert u1["api_key"] != u2["api_key"]

    def test_regenerate_replaces_the_key(self, store):
        store.create_organization(org_id="org1", name="Acme")
        original = store.create_user(user_id="u1", display_name="Seth", username="seth", org_id="org1", role="owner")
        regenerated = store.regenerate_api_key("u1")
        assert regenerated["api_key"] != original["api_key"]
        assert regenerated["api_key"].startswith("fbot_")

    def test_regenerate_unknown_user_raises(self, store):
        with pytest.raises(AccountError, match="No such user"):
            store.regenerate_api_key("does-not-exist")


class TestProfileFields:
    def test_default_to_none_or_empty(self, store):
        store.create_organization(org_id="org1", name="Acme")
        user = store.create_user(user_id="u1", display_name="Seth", username="seth", org_id="org1", role="owner")
        assert user["timezone"] is None
        assert user["preferred_ai_model"] is None
        assert user["default_branch_prefix"] is None
        assert user["notification_preferences"] == {}

    def test_update_sets_profile_fields(self, store):
        store.create_organization(org_id="org1", name="Acme")
        store.create_user(user_id="u1", display_name="Seth", username="seth", org_id="org1", role="owner")

        updated = store.update_user(
            "u1", timezone="America/New_York", preferred_ai_model="claude-sonnet-5",
            default_branch_prefix="seth/", notification_preferences={"email": True, "digest": "daily"},
        )

        assert updated["timezone"] == "America/New_York"
        assert updated["preferred_ai_model"] == "claude-sonnet-5"
        assert updated["default_branch_prefix"] == "seth/"
        assert updated["notification_preferences"] == {"email": True, "digest": "daily"}

    def test_update_notification_preferences_round_trips_through_fetch(self, store):
        store.create_organization(org_id="org1", name="Acme")
        store.create_user(user_id="u1", display_name="Seth", username="seth", org_id="org1", role="owner")
        store.update_user("u1", notification_preferences={"email": False})

        assert store.fetch_user("u1")["notification_preferences"] == {"email": False}


class TestFactory:
    def test_get_account_store_returns_sqlite_by_default(self, tmp_path, monkeypatch):
        from futures_bot.accounts.store import AccountStore as _AccountStore
        from futures_bot.accounts.store import get_account_store

        monkeypatch.setenv("FUTURES_BOT_RESEARCH_DB", str(tmp_path / "research.db"))
        monkeypatch.delenv("FUTURES_BOT_DATABASE_URL", raising=False)

        store = get_account_store()
        try:
            assert isinstance(store, _AccountStore)
        finally:
            store.close()
