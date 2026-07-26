"""HTTP-level tests for `/api/jobs/*` -- submission, polling, and the SSE
stream. `tests/test_api_jobs.py` covers `api.jobs.JobManager` directly;
these catch route-wiring issues (wrong response_model, wrong status code)
the same way `test_api_routes.py` does for the Phase 6A endpoints.
"""

from __future__ import annotations

import csv
import json
import random
import time
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from futures_bot.contracts import CME_TZ

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
  directory: logs
  log_every_decision: false

strategy_name: vwap_reversion
strategy_params:
  min_bars: 10

state_file: state/bot_state.json
"""


def _write_dataset(path: Path, n: int = 1200, seed: int = 9) -> None:
    rng = random.Random(seed)
    rows = [["timestamp", "open", "high", "low", "close", "volume"]]
    price = Decimal("7500")
    start = datetime(2026, 1, 5, 8, 30, tzinfo=CME_TZ)
    for i in range(n):
        price += Decimal(str(round(rng.uniform(-5, 5), 2)))
        rows.append([
            str(start + timedelta(minutes=i)), str(price), str(price + 2), str(price - 2), str(price),
            str(rng.randint(100, 1000)),
        ])
    with path.open("w", newline="") as fh:
        csv.writer(fh).writerows(rows)


@pytest.fixture
def client(tmp_path, monkeypatch):
    (tmp_path / "config.yaml").write_text(CONFIG_YAML, encoding="utf-8")
    _write_dataset(tmp_path / "data.csv")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FUTURES_BOT_RESEARCH_DB", str(tmp_path / "research.db"))

    from futures_bot.api import jobs
    from futures_bot.api.app import create_app

    jobs.reset_executor()
    yield TestClient(create_app())
    jobs.reset_executor()


def _wait_for_terminal(client, job_id, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("completed", "failed"):
            return job
        time.sleep(0.05)
    raise TimeoutError(f"job {job_id} did not finish in time")


class TestSubmitBacktestJob:
    def test_submits_and_completes(self, client):
        resp = client.post("/api/jobs/backtest", json={"strategy_name": "vwap_reversion", "dataset": "data.csv"})
        assert resp.status_code == 200
        job = resp.json()
        assert job["status"] in ("queued", "running")
        assert job["kind"] == "backtest"

        final = _wait_for_terminal(client, job["id"])
        assert final["status"] == "completed"
        assert final["result_id"] is not None

        # The linked run is reachable through the existing Phase 6A endpoint.
        run = client.get(f"/api/backtests/{final['result_id']}")
        assert run.status_code == 200
        assert run.json()["status"] == "completed"

    def test_walk_forward_job_kind(self, client):
        resp = client.post("/api/jobs/backtest", json={
            "strategy_name": "vwap_reversion", "dataset": "data.csv", "walk_forward": True,
        })
        assert resp.json()["kind"] == "walk_forward"

    def test_bad_dataset_fails_the_job_not_the_submission(self, client):
        resp = client.post("/api/jobs/backtest", json={"strategy_name": "vwap_reversion", "dataset": "nope.csv"})
        assert resp.status_code == 200  # submission itself succeeds
        final = _wait_for_terminal(client, resp.json()["id"])
        assert final["status"] == "failed"
        assert final["error_message"]


class TestSubmitOptimizerJob:
    def test_submits_and_completes_with_progress(self, client):
        resp = client.post("/api/jobs/optimizer", json={
            "strategy_name": "vwap_reversion", "dataset": "data.csv",
            "param_grid": {"min_bars": [10, 15]}, "top_n": 2,
        })
        assert resp.status_code == 200
        job_id = resp.json()["id"]
        final = _wait_for_terminal(client, job_id)
        assert final["status"] == "completed"
        assert final["result_id"] is not None

        results = client.get(f"/api/optimizer/results/{final['result_id']}")
        assert results.status_code == 200
        assert len(results.json()) == 2


class TestSubmitCompareJob:
    def test_submits_and_completes_with_payload(self, client):
        resp = client.post("/api/jobs/compare", json={
            "dataset": "data.csv", "strategy_names": ["vwap_reversion", "ema_crossover"],
        })
        assert resp.status_code == 200
        final = _wait_for_terminal(client, resp.json()["id"])
        assert final["status"] == "completed"
        assert final["result_id"] is None  # compare has no runs-table home
        assert final["result_payload"] is not None
        strategies = {e["strategy"] for e in final["result_payload"]["entries"]}
        assert strategies == {"vwap_reversion", "ema_crossover"}


class TestSubmitReportJob:
    def test_submits_and_completes(self, client):
        run = client.post("/api/backtest/run", json={
            "strategy_name": "vwap_reversion", "dataset": "data.csv",
        }).json()

        resp = client.post("/api/jobs/report", json={"run_id": run["id"]})
        assert resp.status_code == 200
        final = _wait_for_terminal(client, resp.json()["id"])
        assert final["status"] == "completed"
        assert final["result_id"] is not None

        view = client.get(f"/api/reports/{final['result_id']}/view")
        assert view.status_code == 200


class TestListAndGetJob:
    def test_list_jobs(self, client):
        resp = client.post("/api/jobs/backtest", json={"strategy_name": "vwap_reversion", "dataset": "data.csv"})
        job_id = resp.json()["id"]
        _wait_for_terminal(client, job_id)

        listing = client.get("/api/jobs")
        assert listing.status_code == 200
        assert any(j["id"] == job_id for j in listing.json())

    def test_get_unknown_job_is_400(self, client):
        resp = client.get("/api/jobs/does-not-exist")
        assert resp.status_code == 400

    def test_filter_by_status(self, client):
        resp = client.post("/api/jobs/backtest", json={"strategy_name": "vwap_reversion", "dataset": "data.csv"})
        _wait_for_terminal(client, resp.json()["id"])
        completed = client.get("/api/jobs?status=completed")
        assert completed.status_code == 200
        assert all(j["status"] == "completed" for j in completed.json())


class TestStreamJob:
    def test_stream_unknown_job_is_400(self, client):
        resp = client.get("/api/jobs/does-not-exist/stream")
        assert resp.status_code == 400

    def test_stream_delivers_at_least_one_frame_and_ends_terminal(self, client):
        submit = client.post("/api/jobs/backtest", json={"strategy_name": "vwap_reversion", "dataset": "data.csv"})
        job_id = submit.json()["id"]
        _wait_for_terminal(client, job_id)  # let it finish first -- still must stream >= 1 frame

        with client.stream("GET", f"/api/jobs/{job_id}/stream") as resp:
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers["content-type"]
            frames = [json.loads(line[5:]) for line in resp.iter_lines() if line.startswith("data:")]

        assert frames
        assert frames[-1]["status"] == "completed"

    def test_stream_reports_progress_for_an_in_flight_job(self, client):
        # A large-enough dataset that the job is still running when we start
        # streaming, so more than one frame should arrive.
        _write_dataset(Path("data_large.csv"), n=15000, seed=4)
        submit = client.post("/api/jobs/backtest", json={"strategy_name": "vwap_reversion", "dataset": "data_large.csv"})
        job_id = submit.json()["id"]

        with client.stream("GET", f"/api/jobs/{job_id}/stream") as resp:
            frames = [json.loads(line[5:]) for line in resp.iter_lines() if line.startswith("data:")]

        assert len(frames) >= 2
        assert frames[-1]["status"] == "completed"
        assert frames[-1]["progress_current"] == frames[-1]["progress_total"]
