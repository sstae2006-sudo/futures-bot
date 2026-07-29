"""HTTP-level tests for `/api/workers*` -- the Worker Registry (SIL Phase
6 "Integration Coordinator" Milestone 1). See
`collaboration/store.py::heartbeat_worker`/`api/worker_service.py` for
the underlying logic.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from futures_bot.api.app import create_app
from futures_bot.api.worker_service import _STALE_AFTER_SECONDS, _is_worker_stale


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("FUTURES_BOT_MARKET_DATA_DB", str(tmp_path / "market_data.db"))
    monkeypatch.setenv("FUTURES_BOT_RESEARCH_DB", str(tmp_path / "research.db"))
    return TestClient(create_app())


class TestHeartbeat:
    def test_first_heartbeat_creates_the_worker(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)

        resp = client.post("/api/workers/w1/heartbeat", json={"worker_type": "claude_code_session", "display_name": "Session 1"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == "w1"
        assert body["worker_type"] == "claude_code_session"
        assert body["is_stale"] is False

    def test_second_heartbeat_updates_the_same_worker(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)
        client.post("/api/workers/w1/heartbeat", json={"display_name": "Session 1", "status": "online"})

        resp = client.post("/api/workers/w1/heartbeat", json={"display_name": "Session 1", "status": "idle"})

        assert resp.status_code == 200
        assert resp.json()["status"] == "idle"
        assert len(client.get("/api/workers").json()) == 1

    def test_invalid_worker_type_is_422(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)

        resp = client.post("/api/workers/w1/heartbeat", json={"display_name": "X", "worker_type": "not_a_real_type"})

        assert resp.status_code == 422

    def test_missing_display_name_is_422(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)

        resp = client.post("/api/workers/w1/heartbeat", json={})

        assert resp.status_code == 422

    def test_heartbeat_with_capabilities_round_trips(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)

        resp = client.post(
            "/api/workers/w1/heartbeat",
            json={"display_name": "X", "capabilities": ["backend", "testing"]},
        )

        assert resp.json()["capabilities"] == ["backend", "testing"]


class TestListAndGet:
    def test_list_returns_every_worker(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)
        client.post("/api/workers/w1/heartbeat", json={"display_name": "A"})
        client.post("/api/workers/w2/heartbeat", json={"display_name": "B"})

        resp = client.get("/api/workers")

        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_list_filters_by_org_id(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)
        client.post("/api/workers/w1/heartbeat", json={"display_name": "A", "org_id": "org-a"})
        client.post("/api/workers/w2/heartbeat", json={"display_name": "B", "org_id": "org-b"})

        resp = client.get("/api/workers", params={"org_id": "org-a"})

        assert [w["id"] for w in resp.json()] == ["w1"]

    def test_list_filters_by_capability(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)
        client.post("/api/workers/w1/heartbeat", json={"display_name": "A", "capabilities": ["frontend"]})
        client.post("/api/workers/w2/heartbeat", json={"display_name": "B", "capabilities": ["backend"]})

        resp = client.get("/api/workers", params={"capability": "backend"})

        assert [w["id"] for w in resp.json()] == ["w2"]

    def test_get_returns_the_worker(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)
        client.post("/api/workers/w1/heartbeat", json={"display_name": "A"})

        resp = client.get("/api/workers/w1")

        assert resp.status_code == 200
        assert resp.json()["id"] == "w1"

    def test_get_unknown_worker_is_400(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)

        resp = client.get("/api/workers/does-not-exist")

        assert resp.status_code == 400


class TestStaleness:
    """Staleness is always computed live from `last_heartbeat_at`, never
    stored -- see `worker_service.py`'s module docstring."""

    def test_a_fresh_heartbeat_is_not_stale(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)
        client.post("/api/workers/w1/heartbeat", json={"display_name": "A"})

        resp = client.get("/api/workers/w1")

        assert resp.json()["is_stale"] is False

    def test_boundary_is_inclusive_of_stale_after_seconds(self):
        """A worker exactly `_STALE_AFTER_SECONDS` since its last
        heartbeat counts as stale (>=), matching
        `maintenance.py`'s "exactly at the cutoff counts as stale"
        precedent for drafts -- pinned as an explicit regression test,
        not left to accident."""
        now = datetime.now(timezone.utc)
        heartbeat_at = now - timedelta(seconds=_STALE_AFTER_SECONDS)
        worker = {"last_heartbeat_at": heartbeat_at.isoformat()}

        is_stale, seconds = _is_worker_stale(worker, now)

        assert is_stale is True
        assert seconds == _STALE_AFTER_SECONDS

    def test_just_under_the_boundary_is_not_stale(self):
        now = datetime.now(timezone.utc)
        heartbeat_at = now - timedelta(seconds=_STALE_AFTER_SECONDS - 1)
        worker = {"last_heartbeat_at": heartbeat_at.isoformat()}

        is_stale, _ = _is_worker_stale(worker, now)

        assert is_stale is False
