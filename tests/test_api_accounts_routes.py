"""HTTP-level tests for `/api/organizations`/`/api/users` -- the Team
Collaboration MVP's lightweight account routes. See
`accounts/store.py`/`api/accounts_service.py` for the underlying logic;
this file catches route-wiring and request/response-shape issues the same
way `test_api_market_data.py` does for its own routes.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from futures_bot.api.app import create_app


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("FUTURES_BOT_MARKET_DATA_DB", str(tmp_path / "market_data.db"))
    monkeypatch.setenv("FUTURES_BOT_RESEARCH_DB", str(tmp_path / "research.db"))
    return TestClient(create_app())


class TestOrganizations:
    def test_create_and_list(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)

        resp = client.post("/api/organizations", json={"name": "Acme Research"})
        assert resp.status_code == 200
        org = resp.json()
        assert org["name"] == "Acme Research"
        assert org["id"]

        resp = client.get("/api/organizations")
        assert resp.status_code == 200
        assert [o["name"] for o in resp.json()] == ["Acme Research"]

        resp = client.get(f"/api/organizations/{org['id']}")
        assert resp.status_code == 200
        assert resp.json() == org

    def test_get_unknown_organization_is_400(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)

        resp = client.get("/api/organizations/does-not-exist")

        assert resp.status_code == 400

    def test_duplicate_name_is_400(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)
        client.post("/api/organizations", json={"name": "Acme"})

        resp = client.post("/api/organizations", json={"name": "Acme"})

        assert resp.status_code == 400

    def test_empty_name_is_422(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)

        resp = client.post("/api/organizations", json={"name": ""})

        assert resp.status_code == 422


class TestUsers:
    def _make_org(self, client) -> str:
        return client.post("/api/organizations", json={"name": "Acme"}).json()["id"]

    def test_create_and_get(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)
        org_id = self._make_org(client)

        resp = client.post("/api/users", json={
            "display_name": "Seth", "username": "seth", "org_id": org_id, "role": "owner",
            "email": "seth@example.com",
        })
        assert resp.status_code == 200
        user = resp.json()
        assert user["username"] == "seth"
        assert user["role"] == "owner"
        assert user["last_active_at"] is None

        resp = client.get(f"/api/users/{user['id']}")
        assert resp.status_code == 200
        assert resp.json() == user

    def test_invalid_role_is_422(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)
        org_id = self._make_org(client)

        resp = client.post("/api/users", json={
            "display_name": "Seth", "username": "seth", "org_id": org_id, "role": "superuser",
        })

        assert resp.status_code == 422

    def test_unknown_organization_is_400(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)

        resp = client.post("/api/users", json={
            "display_name": "Seth", "username": "seth", "org_id": "does-not-exist", "role": "owner",
        })

        assert resp.status_code == 400

    def test_duplicate_username_is_400(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)
        org_id = self._make_org(client)
        client.post("/api/users", json={"display_name": "Seth", "username": "seth", "org_id": org_id, "role": "owner"})

        resp = client.post("/api/users", json={"display_name": "Other", "username": "seth", "org_id": org_id, "role": "member"})

        assert resp.status_code == 400

    def test_list_filters_by_org(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)
        org_a = client.post("/api/organizations", json={"name": "A"}).json()["id"]
        org_b = client.post("/api/organizations", json={"name": "B"}).json()["id"]
        client.post("/api/users", json={"display_name": "Alice", "username": "alice", "org_id": org_a, "role": "owner"})
        client.post("/api/users", json={"display_name": "Bob", "username": "bob", "org_id": org_b, "role": "owner"})

        resp = client.get("/api/users", params={"org_id": org_a})

        assert resp.status_code == 200
        assert [u["username"] for u in resp.json()] == ["alice"]

    def test_patch_updates_only_supplied_fields(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)
        org_id = self._make_org(client)
        user = client.post("/api/users", json={
            "display_name": "Seth", "username": "seth", "org_id": org_id, "role": "member",
            "email": "seth@example.com",
        }).json()

        resp = client.patch(f"/api/users/{user['id']}", json={"role": "admin"})

        assert resp.status_code == 200
        updated = resp.json()
        assert updated["role"] == "admin"
        assert updated["display_name"] == "Seth"
        assert updated["email"] == "seth@example.com"

    def test_patch_invalid_role_is_422(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)
        org_id = self._make_org(client)
        user = client.post("/api/users", json={
            "display_name": "Seth", "username": "seth", "org_id": org_id, "role": "member",
        }).json()

        resp = client.patch(f"/api/users/{user['id']}", json={"role": "superuser"})

        assert resp.status_code == 422

    def test_heartbeat_sets_last_active(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)
        org_id = self._make_org(client)
        user = client.post("/api/users", json={
            "display_name": "Seth", "username": "seth", "org_id": org_id, "role": "member",
        }).json()
        assert user["last_active_at"] is None

        resp = client.post(f"/api/users/{user['id']}/heartbeat")

        assert resp.status_code == 200
        assert resp.json()["last_active_at"] is not None

    def test_heartbeat_unknown_user_is_400(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)

        resp = client.post("/api/users/does-not-exist/heartbeat")

        assert resp.status_code == 400
