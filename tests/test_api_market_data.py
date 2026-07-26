"""HTTP-level tests for `/api/market-data/*` -- overview, manual
sync/backfill/verify/repair, and scheduler start/stop/status. Catches
route-wiring issues the same way `test_api_jobs_routes.py` does for jobs;
`tests/test_market_data_*.py` already cover the underlying store/sync/
scheduler logic directly.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
import requests
from fastapi.testclient import TestClient


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _agg_bar(window_start: datetime, close: float = 7500.0):
    ns = int(window_start.timestamp() * 1e9)
    return {"window_start": ns, "open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 500}


def _single(ticker, first_trade_date, last_trade_date, query_date):
    return {
        "active": True, "date": query_date, "name": ticker, "product_code": "MES",
        "ticker": ticker, "type": "single", "first_trade_date": first_trade_date, "last_trade_date": last_trade_date,
    }


@pytest.fixture
def patched_requests(monkeypatch):
    state = {"contracts_by_date": {}, "aggs_by_ticker": {}}

    def fake_get(self, url, params=None, timeout=None):
        if "/contracts" in url:
            query_date = (params or {}).get("date")
            return FakeResponse(state["contracts_by_date"].get(query_date, {"results": [], "status": "OK"}))
        ticker = url.rsplit("/", 1)[-1]
        return FakeResponse({"results": state["aggs_by_ticker"].get(ticker, []), "status": "OK"})

    monkeypatch.setattr(requests.Session, "get", fake_get)
    return state


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FUTURES_BOT_MARKET_DATA_DB", str(tmp_path / "market_data.db"))
    monkeypatch.setenv("MASSIVE_API_KEY", "test-key")

    from futures_bot.api.app import create_app
    from futures_bot.market_data import scheduler as scheduler_module

    scheduler_module.reset_scheduler()
    yield TestClient(create_app())
    scheduler_module.reset_scheduler()


class TestOverview:
    def test_overview_on_an_empty_database(self, client):
        resp = client.get("/api/market-data/overview")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_bars"] == 0
        assert body["products"] == []
        assert body["scheduler_running"] is False

    def test_overview_after_a_sync(self, client, patched_requests):
        today = datetime.now(timezone.utc).date().isoformat()
        patched_requests["contracts_by_date"][today] = {
            "status": "OK", "results": [_single("MESU6", "2025-06-20", "2026-09-18", today)],
        }
        patched_requests["aggs_by_ticker"]["MESU6"] = [_agg_bar(datetime.now(timezone.utc))]

        client.post("/api/market-data/sync", json={"product_code": "MES", "resolution": "5min"})

        resp = client.get("/api/market-data/overview")
        body = resp.json()
        assert body["total_bars"] == 1
        assert body["products"][0]["product_code"] == "MES"
        assert body["products"][0]["contracts_stored"] == ["MESU6"]


class TestSyncAndBackfill:
    def test_sync_without_api_key_is_400(self, client, monkeypatch):
        monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
        resp = client.post("/api/market-data/sync", json={"product_code": "MES", "resolution": "5min"})
        assert resp.status_code == 400
        assert "MASSIVE_API_KEY" in resp.json()["detail"]

    def test_backfill_round_trip(self, client, patched_requests):
        patched_requests["contracts_by_date"]["2026-07-20"] = {
            "status": "OK", "results": [_single("MESU6", "2025-06-20", "2026-09-18", "2026-07-20")],
        }
        patched_requests["aggs_by_ticker"]["MESU6"] = [_agg_bar(datetime(2026, 7, 20, 14, 0, tzinfo=timezone.utc))]

        resp = client.post("/api/market-data/backfill", json={
            "product_code": "MES", "resolution": "5min", "start": "2026-07-20", "end": "2026-07-20",
        })

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "completed"
        assert body["bars_fetched"] == 1

    def test_verify_reports_gaps(self, client, patched_requests):
        patched_requests["contracts_by_date"]["2026-07-20"] = {
            "status": "OK", "results": [_single("MESU6", "2025-06-20", "2026-09-18", "2026-07-20")],
        }
        patched_requests["aggs_by_ticker"]["MESU6"] = [
            _agg_bar(datetime(2026, 7, 20, 14, 0, tzinfo=timezone.utc)),
            _agg_bar(datetime(2026, 7, 20, 14, 20, tzinfo=timezone.utc)),
        ]
        client.post("/api/market-data/backfill", json={
            "product_code": "MES", "resolution": "5min", "start": "2026-07-20", "end": "2026-07-20",
        })

        resp = client.post("/api/market-data/verify", json={"product_code": "MES", "resolution": "5min"})

        assert resp.status_code == 200
        assert resp.json()["new_gaps"] == 1

        gaps_resp = client.get("/api/market-data/gaps?product_code=MES")
        assert len(gaps_resp.json()) == 1

    def test_list_sync_runs(self, client, patched_requests):
        today = datetime.now(timezone.utc).date().isoformat()
        patched_requests["contracts_by_date"][today] = {
            "status": "OK", "results": [_single("MESU6", "2025-06-20", "2026-09-18", today)],
        }
        client.post("/api/market-data/sync", json={"product_code": "MES", "resolution": "5min"})

        resp = client.get("/api/market-data/runs?product_code=MES")
        assert resp.status_code == 200
        runs = resp.json()
        assert len(runs) == 1
        assert runs[0]["kind"] == "incremental"


class TestScheduler:
    def test_start_status_stop_lifecycle(self, client):
        start_resp = client.post(
            "/api/market-data/scheduler/start",
            json={"targets": [{"product_code": "MES", "resolution": "5min"}], "interval_seconds": 60},
        )
        assert start_resp.status_code == 200
        assert start_resp.json()["running"] is True

        status_resp = client.get("/api/market-data/scheduler/status")
        assert status_resp.json()["running"] is True

        stop_resp = client.post("/api/market-data/scheduler/stop")
        assert stop_resp.status_code == 200
        assert stop_resp.json()["running"] is False

    def test_status_before_ever_starting_is_not_running(self, client):
        resp = client.get("/api/market-data/scheduler/status")
        assert resp.status_code == 200
        assert resp.json()["running"] is False

    def test_starting_twice_is_400(self, client):
        body = {"targets": [{"product_code": "MES", "resolution": "5min"}], "interval_seconds": 60}
        client.post("/api/market-data/scheduler/start", json=body)
        resp = client.post("/api/market-data/scheduler/start", json=body)
        assert resp.status_code == 400

        client.post("/api/market-data/scheduler/stop")
