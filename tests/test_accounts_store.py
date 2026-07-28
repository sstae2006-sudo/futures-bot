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
