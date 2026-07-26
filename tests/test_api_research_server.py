"""HTTP-level tests for `/api/research-server/*` -- status, manual
start/stop, the manual nightly-batch trigger, and findings. Catches
route-wiring issues the same way `test_api_market_data.py` does for the
data pipeline; the underlying orchestrator/paper-trader/nightly-jobs logic
is already covered directly by `tests/test_research_server_*.py`.
"""

from __future__ import annotations

import pytest
import requests
from fastapi.testclient import TestClient

from tests.test_research_server_paper_trader import FakeContractsSession, FakeMassiveBarFeed

CONFIG_YAML = """
contract: MES
mode: paper
risk:
  contracts_per_trade: 1
  stop_loss_points: 5
  take_profit_points: 10
  daily_max_loss: 100000
  max_trades_per_session: 2000
  account_size: 2500
broker:
  name: paper
  starting_cash: 2500
logging:
  level: WARNING
  directory: {log_dir}
strategy_name: ema_crossover
strategy_params:
  fast_period: 3
  slow_period: 5
research_server:
  enabled: true
  paper_strategies: [ema_crossover]
  data_sync_products: [MES]
  resolution: 5min
  poll_seconds: 1
state_file: {state_file}
"""


@pytest.fixture
def client(tmp_path, monkeypatch):
    (tmp_path / "config.yaml").write_text(
        CONFIG_YAML.format(
            log_dir=(tmp_path / "logs").as_posix(), state_file=(tmp_path / "state" / "bot_state.json").as_posix(),
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FUTURES_BOT_RESEARCH_DB", str(tmp_path / "research.db"))
    monkeypatch.setenv("FUTURES_BOT_MARKET_DATA_DB", str(tmp_path / "market_data.db"))
    monkeypatch.setenv("MASSIVE_API_KEY", "test-key")

    FakeMassiveBarFeed.instances = []
    monkeypatch.setattr("futures_bot.feeds.massive.MassiveBarFeed", FakeMassiveBarFeed)
    monkeypatch.setattr(requests, "Session", FakeContractsSession)

    from futures_bot.api.app import create_app
    from futures_bot.market_data.scheduler import reset_scheduler
    from futures_bot.research_server.nightly_jobs import reset_nightly_job_scheduler
    from futures_bot.research_server.orchestrator import reset_research_server
    from futures_bot.research_server.paper_trader import reset_paper_trader

    reset_research_server()
    reset_scheduler()
    reset_paper_trader()
    reset_nightly_job_scheduler()
    test_client = TestClient(create_app())
    yield test_client
    try:
        test_client.post("/api/research-server/stop")
    except Exception:
        pass
    reset_research_server()
    reset_scheduler()
    reset_paper_trader()
    reset_nightly_job_scheduler()


class TestStatusAndLifecycle:
    def test_status_before_starting(self, client):
        resp = client.get("/api/research-server/status")
        assert resp.status_code == 200
        assert resp.json()["running"] is False

    def test_start_then_status_then_stop(self, client):
        start_resp = client.post("/api/research-server/start")
        assert start_resp.status_code == 200
        body = start_resp.json()
        assert body["running"] is True
        assert body["paper_trader"]["running"] is True
        assert "ema_crossover" in body["paper_trader"]["strategies"]

        status_resp = client.get("/api/research-server/status")
        assert status_resp.json()["running"] is True

        stop_resp = client.post("/api/research-server/stop")
        assert stop_resp.status_code == 200
        assert stop_resp.json()["running"] is False

    def test_start_without_api_key_is_400(self, client, monkeypatch):
        monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
        resp = client.post("/api/research-server/start")
        assert resp.status_code == 400
        assert "MASSIVE_API_KEY" in resp.json()["detail"]

    def test_starting_twice_is_400(self, client):
        client.post("/api/research-server/start")
        resp = client.post("/api/research-server/start")
        assert resp.status_code == 400


class TestNightlyAndFindings:
    def test_run_nightly_now(self, client):
        resp = client.post("/api/research-server/nightly/run-now")
        assert resp.status_code == 200
        assert "job(s)" in resp.json()["summary"]

    def test_run_nightly_now_updates_the_status_the_dashboard_reads(self, client):
        """Regression test for the singleton-mismatch bug: `ResearchServer`
        used to construct its own private `NightlyJobScheduler`, while this
        route reached a *different* one via `get_nightly_job_scheduler()` --
        the manual-run button worked (jobs got submitted) but
        `/api/research-server/status` never reflected it, because the two
        objects didn't share state. Both must now agree."""
        client.post("/api/research-server/start")

        run_resp = client.post("/api/research-server/nightly/run-now")
        assert run_resp.status_code == 200

        status = client.get("/api/research-server/status").json()
        assert status["nightly_jobs"]["last_run_summary"] == run_resp.json()["summary"]
        assert status["nightly_jobs"]["last_run_date"] is not None

    def test_findings_with_no_data_is_an_empty_list(self, client):
        resp = client.get("/api/research-server/findings")
        assert resp.status_code == 200
        assert resp.json() == []
