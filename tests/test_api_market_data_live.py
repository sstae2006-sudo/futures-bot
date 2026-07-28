"""HTTP-level regression test against a real Postgres/TimescaleDB server --
catches exactly the class of bug `tests/test_pg_market_data_store_live.py`
(store-level) and `tests/test_market_data_store_parity.py` (signature-level)
cannot: a real `pydantic.ValidationError` 500 was found hitting
`GET /api/market-data/overview` against the live compose `timescaledb`
service, because `PgMarketDataStore.fetch_sync_runs` returned native
`datetime` objects where `MarketDataOverviewOut.last_sync_at` (and every
caller up the stack) assumes a plain string -- the SQLite-only fixture in
`test_api_market_data.py` can't exercise this at all, since SQLite's own
`fetch_sync_runs` already returns strings.

Skipped automatically when no reachable server is configured -- same
pattern as `test_pg_market_data_store_live.py`.
"""

from __future__ import annotations

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
    reason="No reachable Postgres/TimescaleDB configured via FUTURES_BOT_DATABASE_URL.",
)


@pytest.fixture
def client(tmp_path, monkeypatch, live_database_url):
    monkeypatch.chdir(tmp_path)  # no config.yaml here -- research-server auto-start stays off
    monkeypatch.setenv("FUTURES_BOT_RESEARCH_DB", str(tmp_path / "research.db"))

    from fastapi.testclient import TestClient
    from sqlalchemy import text

    from futures_bot.api.app import create_app
    from futures_bot.db.engine import get_engine
    from futures_bot.market_data import scheduler as scheduler_module
    from futures_bot.market_data.pg_store import PgMarketDataStore

    PgMarketDataStore().ensure_schema()

    def _truncate():
        # Truncate before AND after -- the "empty database" test's own
        # assumption must never depend on some earlier test/session having
        # cleaned up correctly (found the hard way: this file originally
        # only cleaned up in one test's own `finally`, so any leftover
        # state from outside this specific test run -- another test file,
        # a prior manual verification pass -- made "total_bars == 0" flaky
        # depending on run order/history, not a real regression).
        with get_engine().begin() as conn:
            conn.execute(text("TRUNCATE bars, sync_runs, gaps, active_contracts, contract_rolls"))

    _truncate()
    scheduler_module.reset_scheduler()
    yield TestClient(create_app())
    scheduler_module.reset_scheduler()
    _truncate()
    dispose_engine()


class TestMarketDataOverviewAgainstLiveServer:
    def test_overview_on_an_empty_live_database_returns_200(self, client):
        resp = client.get("/api/market-data/overview")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_bars"] == 0
        assert body["last_sync_at"] is None

    def test_overview_after_a_real_sync_run_returns_200_with_string_timestamps(self, client):
        from futures_bot.market_data.pg_store import PgMarketDataStore

        store = PgMarketDataStore()
        store.ensure_schema()
        try:
            store.start_sync_run("live-api-test-run", "MES", "5min", "incremental")
            store.complete_sync_run("live-api-test-run")

            resp = client.get("/api/market-data/overview")
            assert resp.status_code == 200
            body = resp.json()
            assert body["last_sync_at"] is not None
            assert isinstance(body["last_sync_at"], str)
            assert body["last_sync_status"] == "completed"

            runs_resp = client.get("/api/market-data/runs")
            assert runs_resp.status_code == 200
            assert isinstance(runs_resp.json()[0]["started_at"], str)
        finally:
            from sqlalchemy import text

            with store._engine.begin() as conn:  # noqa: SLF001 -- test-only cleanup
                conn.execute(text("TRUNCATE bars, sync_runs, gaps, active_contracts, contract_rolls"))
            store.close()
