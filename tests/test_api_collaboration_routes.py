"""HTTP-level tests for `/api/work-items*` -- the Active Work Registry's
routes. See `collaboration/store.py`/`api/collaboration_service.py` for
the underlying logic.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from futures_bot.api.app import create_app


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("FUTURES_BOT_MARKET_DATA_DB", str(tmp_path / "market_data.db"))
    monkeypatch.setenv("FUTURES_BOT_RESEARCH_DB", str(tmp_path / "research.db"))
    return TestClient(create_app())


class TestCreateWorkItem:
    def test_create_without_overlap(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)

        resp = client.post("/api/work-items", json={"title": "Fix login bug", "estimated_files": ["src/auth.py"]})

        assert resp.status_code == 200
        body = resp.json()
        assert body["work_item"]["title"] == "Fix login bug"
        assert body["work_item"]["status"] == "open"
        assert body["overlap_warnings"] == []

    def test_create_with_owner_is_claimed(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)

        resp = client.post("/api/work-items", json={"title": "Task", "owner_user_id": "u1"})

        assert resp.json()["work_item"]["status"] == "claimed"

    def test_create_warns_about_file_overlap_but_still_creates(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)
        client.post("/api/work-items", json={"title": "First", "estimated_files": ["src/a.py"]})

        resp = client.post("/api/work-items", json={"title": "Second", "estimated_files": ["src/a.py"]})

        assert resp.status_code == 200
        assert len(resp.json()["overlap_warnings"]) == 1
        assert resp.json()["overlap_warnings"][0]["risk"] in ("low", "medium", "high", "critical")

    def test_invalid_priority_is_422(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)

        resp = client.post("/api/work-items", json={"title": "Task", "priority": "urgent"})

        assert resp.status_code == 422

    def test_empty_title_is_422(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)

        resp = client.post("/api/work-items", json={"title": ""})

        assert resp.status_code == 422


class TestListAndGet:
    def test_list_and_get_round_trip(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)
        created = client.post("/api/work-items", json={"title": "Task"}).json()["work_item"]

        resp = client.get(f"/api/work-items/{created['id']}")
        assert resp.status_code == 200
        assert resp.json() == created

        resp = client.get("/api/work-items")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_list_filters_by_status(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)
        client.post("/api/work-items", json={"title": "Open"})
        client.post("/api/work-items", json={"title": "Claimed", "owner_user_id": "u1"})

        resp = client.get("/api/work-items", params={"status": "claimed"})

        assert [i["title"] for i in resp.json()] == ["Claimed"]

    def test_get_unknown_is_400(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)

        resp = client.get("/api/work-items/does-not-exist")

        assert resp.status_code == 400


class TestClaimReleaseCompleteReassign:
    def test_full_lifecycle(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)
        item = client.post("/api/work-items", json={"title": "Task"}).json()["work_item"]

        resp = client.post(f"/api/work-items/{item['id']}/claim", json={"user_id": "u1"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "claimed"

        resp = client.post(f"/api/work-items/{item['id']}/release")
        assert resp.status_code == 200
        assert resp.json()["status"] == "open"

        client.post(f"/api/work-items/{item['id']}/claim", json={"user_id": "u1"})
        resp = client.post(f"/api/work-items/{item['id']}/reassign", json={"user_id": "u2"})
        assert resp.status_code == 200
        assert resp.json()["owner_user_id"] == "u2"

        resp = client.post(f"/api/work-items/{item['id']}/complete")
        assert resp.status_code == 200
        assert resp.json()["status"] == "completed"

    def test_claiming_already_claimed_by_someone_else_is_400(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)
        item = client.post("/api/work-items", json={"title": "Task", "owner_user_id": "u1"}).json()["work_item"]

        resp = client.post(f"/api/work-items/{item['id']}/claim", json={"user_id": "u2"})

        assert resp.status_code == 400


class TestOverlapAndActivity:
    def test_overlap_endpoint_recomputes_against_current_active_items(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)
        item = client.post("/api/work-items", json={"title": "First", "estimated_files": ["src/a.py"]}).json()["work_item"]
        assert client.get(f"/api/work-items/{item['id']}/overlap").json() == []

        client.post("/api/work-items", json={"title": "Second", "estimated_files": ["src/a.py"]})

        resp = client.get(f"/api/work-items/{item['id']}/overlap")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_activity_log_reflects_the_lifecycle(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)
        item = client.post("/api/work-items", json={"title": "Task"}).json()["work_item"]
        client.post(f"/api/work-items/{item['id']}/claim", json={"user_id": "u1"})
        client.post(f"/api/work-items/{item['id']}/complete")

        resp = client.get("/api/work-items-activity", params={"work_item_id": item["id"]})

        assert resp.status_code == 200
        events = [a["event"] for a in reversed(resp.json())]
        assert events == ["created", "claimed", "completed"]
