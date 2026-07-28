"""`/api/system/health` -- team-deployment mode's real-data source for
Mission Control (see PROJECT_STATE.md, plan item #7). Every case here runs
with no `FUTURES_BOT_DATABASE_URL` set (the default, every existing
single-developer setup) -- the live-Postgres case is covered by
`tests/test_pg_market_data_store_live.py`'s sibling module and by manual
verification in PROJECT_STATE.md; this file's job is to prove the route
degrades correctly, not to stand up a second live-server test.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

CONFIG_YAML = """
contract: MES
mode: backtest
risk:
  contracts_per_trade: 1
  stop_loss_points: 10
  take_profit_points: 20
  daily_max_loss: 1000
  max_trades_per_session: 10
  account_size: 50000
"""


@pytest.fixture
def client(tmp_path, monkeypatch):
    (tmp_path / "config.yaml").write_text(CONFIG_YAML, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FUTURES_BOT_RESEARCH_DB", str(tmp_path / "research.db"))
    monkeypatch.delenv("FUTURES_BOT_DATABASE_URL", raising=False)

    from futures_bot.api.app import create_app

    return TestClient(create_app())


class TestHealthRouteWithoutDatabaseUrl:
    def test_returns_200_with_status_ok(self, client):
        resp = client.get("/api/system/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_database_is_reported_unconfigured(self, client):
        body = client.get("/api/system/health").json()
        assert body["database"] == {"configured": False, "ok": False, "latency_ms": None, "error": None}

    def test_environment_reflects_config_yaml_default(self, client):
        body = client.get("/api/system/health").json()
        assert body["environment"] == "development"

    def test_uptime_is_nonnegative_and_increases(self, client):
        first = client.get("/api/system/health").json()["uptime_seconds"]
        second = client.get("/api/system/health").json()["uptime_seconds"]
        assert first >= 0
        assert second >= first

    def test_no_backup_marker_reports_none(self, client):
        body = client.get("/api/system/health").json()
        assert body["last_backup_at"] is None

    def test_connected_users_counts_the_calling_client(self, client):
        body = client.get("/api/system/health").json()
        assert body["connected_users"] >= 1

    def test_version_matches_package_version(self, client):
        from futures_bot import __version__

        body = client.get("/api/system/health").json()
        assert body["version"] == __version__

    def test_missing_config_yaml_falls_back_to_development(self, client, tmp_path):
        (tmp_path / "config.yaml").unlink()
        body = client.get("/api/system/health").json()
        assert body["environment"] == "development"

    def test_last_backup_at_reads_a_real_marker_file(self, client, tmp_path, monkeypatch):
        import json

        # Shape written by tools/backup_timescaledb.py::_write_marker.
        marker = tmp_path / "db_backups" / "last_backup.json"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps({
            "timestamp": "2026-07-27T12:00:00+00:00", "path": "db_backups/futures_bot_x.pgdump", "size_bytes": 123,
        }))
        monkeypatch.setenv("FUTURES_BOT_BACKUP_MARKER", str(marker))

        body = client.get("/api/system/health").json()
        assert body["last_backup_at"] == "2026-07-27T12:00:00+00:00"

    def test_corrupt_backup_marker_reports_none_not_500(self, client, tmp_path, monkeypatch):
        marker = tmp_path / "db_backups" / "last_backup.json"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("not valid json")
        monkeypatch.setenv("FUTURES_BOT_BACKUP_MARKER", str(marker))

        resp = client.get("/api/system/health")
        assert resp.status_code == 200
        assert resp.json()["last_backup_at"] is None
