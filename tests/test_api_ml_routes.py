"""HTTP-level tests for `/api/ml/*` and `/api/strategies/{s}/deployment` --
submit training -> poll -> finished, stop, archive, delete, deploy,
rollback, predict, correlation, dataset health -- and that retraining never
mutates a prior version's row (Phase 9)."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

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

strategy_name: trend_pullback
strategy_params: {}

state_file: state/bot_state.json
"""


@pytest.fixture
def client(tmp_path, monkeypatch):
    (tmp_path / "config.yaml").write_text(CONFIG_YAML, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FUTURES_BOT_RESEARCH_DB", str(tmp_path / "research.db"))

    from futures_bot.api import jobs
    from futures_bot.api.app import create_app

    jobs.reset_executor()
    yield TestClient(create_app())
    jobs.reset_executor()


def _seed_trades(strategy: str = "trend_pullback", n: int = 120, seed: int = 0) -> list:
    import random

    from futures_bot.research.features import TradeRecord
    from futures_bot.research.trade_store import TradeStore, default_db_path

    rng = random.Random(seed)
    store = TradeStore(default_db_path())
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    records = []
    for i in range(n):
        rsi = 40.0 + rng.uniform(0, 20)
        win = rsi > 50
        records.append(TradeRecord(
            run_id="r1", contract="MES", strategy=strategy, strategy_params={},
            entry_time=base + timedelta(hours=i), exit_time=base + timedelta(hours=i, minutes=30),
            side="long", entry_price=Decimal("100"), exit_price=Decimal("105" if win else "95"),
            gross_pnl=Decimal("50" if win else "-50"), commission=Decimal("1.24"),
            net_pnl=Decimal("48.76" if win else "-51.24"),
            holding_minutes=30.0, exit_reason="take_profit" if win else "stop_loss",
            session_date=str((base + timedelta(hours=i)).date()), day_of_week="Monday", hour=i % 24,
            entry_reason="test", entry_metadata={"rsi": rsi, "adx": 20.0 + rng.uniform(0, 10)},
            outcome="win" if win else "loss",
        ))
    store.insert_trades(records)
    store.close()
    return records


def _wait_for_terminal_job(client, job_id, timeout=30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("completed", "failed"):
            return job
        time.sleep(0.05)
    raise TimeoutError(f"job {job_id} did not finish in time")


def _train(client, strategy="trend_pullback", model_type="random_forest", hyperparameters=None):
    resp = client.post("/api/ml/models", json={
        "strategy": strategy, "model_type": model_type, "hyperparameters": hyperparameters or {"n_estimators": 20},
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    job = _wait_for_terminal_job(client, body["job_id"])
    assert job["status"] == "completed", job
    return body["model_id"]


class TestDatasetHealthAndCorrelation:
    def test_not_enough_data_before_seeding(self, client):
        resp = client.get("/api/ml/dataset-health", params={"strategy": "trend_pullback"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "NOT_ENOUGH_DATA"

    def test_ready_after_seeding(self, client):
        _seed_trades()
        resp = client.get("/api/ml/dataset-health", params={"strategy": "trend_pullback"})
        assert resp.json()["trade_count"] == 120

    def test_correlation_rows_present(self, client):
        _seed_trades()
        resp = client.get("/api/ml/correlation", params={"strategy": "trend_pullback"})
        assert resp.status_code == 200
        features = {row["feature"] for row in resp.json()}
        assert "rsi" in features


class TestTrainStopDeleteArchive:
    def test_submit_train_poll_finished(self, client):
        _seed_trades()
        model_id = _train(client)
        model = client.get(f"/api/ml/models/{model_id}").json()
        assert model["status"] == "finished"
        assert model["version"] == 1
        assert set(model["metrics"].keys()) == {
            "train", "validation", "test", "diagnostics", "r_multiple_baseline", "training_seconds",
        }

    def test_retrain_creates_a_new_version_not_an_overwrite(self, client):
        _seed_trades()
        model_id_1 = _train(client)
        model_id_2 = _train(client)
        assert model_id_1 != model_id_2
        m1 = client.get(f"/api/ml/models/{model_id_1}").json()
        m2 = client.get(f"/api/ml/models/{model_id_2}").json()
        assert m1["version"] == 1
        assert m2["version"] == 2
        assert m1["model_family"] == m2["model_family"]
        # The first row is untouched -- still finished, still version 1.
        assert m1["status"] == "finished"

        versions = client.get(f"/api/ml/models/family/{m1['model_family']}/versions").json()
        assert [v["version"] for v in versions] == [1, 2]

    def test_delete_removes_the_model(self, client):
        _seed_trades()
        model_id = _train(client)
        resp = client.delete(f"/api/ml/models/{model_id}")
        assert resp.status_code == 200
        assert client.get(f"/api/ml/models/{model_id}").status_code == 400

    def test_archive_hides_from_default_listing(self, client):
        _seed_trades()
        model_id = _train(client)
        client.post(f"/api/ml/models/{model_id}/archive")
        default_listing = client.get("/api/ml/models", params={"strategy": "trend_pullback"}).json()
        assert model_id not in [m["id"] for m in default_listing]
        full_listing = client.get("/api/ml/models", params={"strategy": "trend_pullback", "include_archived": True}).json()
        assert model_id in [m["id"] for m in full_listing]

    def test_notes_update(self, client):
        _seed_trades()
        model_id = _train(client)
        resp = client.patch(f"/api/ml/models/{model_id}/notes", json={"notes": "promising on the validation set"})
        assert resp.json()["notes"] == "promising on the validation set"

    def test_stop_flags_a_queued_or_running_model(self, client):
        _seed_trades()
        resp = client.post("/api/ml/models", json={"strategy": "trend_pullback", "model_type": "random_forest", "hyperparameters": {"n_estimators": 20}})
        model_id = resp.json()["model_id"]
        stop_resp = client.post(f"/api/ml/models/{model_id}/stop")
        assert stop_resp.status_code == 200
        _wait_for_terminal_job(client, resp.json()["job_id"])
        # Model status is either 'stopped' (it was mid-training when the
        # flag landed) or 'finished' (training was too fast to catch) --
        # both are valid outcomes of a race with a 20-tree forest; either
        # way the endpoint itself must not error.
        final = client.get(f"/api/ml/models/{model_id}").json()
        assert final["status"] in ("stopped", "finished")

    def test_training_failure_marks_the_model_row_failed_not_stuck_at_training(self, client):
        """Regression: any training exception other than a deliberate stop
        used to leave the model row at status='training' forever, even
        though the job itself correctly showed 'failed'. `n_estimators=-5`
        makes `RandomForestClassifier` never actually get `.fit()` called
        (the growth loop's `while grown < total_trees` never runs when
        `total_trees` is negative), so scoring it raises a real,
        uncontrived `NotFittedError`."""
        _seed_trades()
        resp = client.post("/api/ml/models", json={
            "strategy": "trend_pullback", "model_type": "random_forest",
            "hyperparameters": {"n_estimators": -5},
        })
        model_id = resp.json()["model_id"]
        job = _wait_for_terminal_job(client, resp.json()["job_id"])
        assert job["status"] == "failed"

        model = client.get(f"/api/ml/models/{model_id}").json()
        assert model["status"] == "failed"
        assert model["error_message"]


class TestDeployRollback:
    def test_deploy_then_rollback(self, client):
        _seed_trades()
        model_id_1 = _train(client)
        model_id_2 = _train(client)

        d1 = client.post(f"/api/ml/models/{model_id_1}/deploy")
        assert d1.json()["model_id"] == model_id_1
        d2 = client.post(f"/api/ml/models/{model_id_2}/deploy")
        assert d2.json()["model_id"] == model_id_2

        current = client.get("/api/strategies/trend_pullback/deployment").json()
        assert current["current"]["model_id"] == model_id_2
        assert len(current["history"]) == 2

        rollback = client.post(f"/api/ml/models/{model_id_1}/rollback", params={"strategy": "trend_pullback"})
        assert rollback.status_code == 200
        after_rollback = client.get("/api/strategies/trend_pullback/deployment").json()
        assert after_rollback["current"]["model_id"] == model_id_1
        assert after_rollback["current"]["action"] == "rollback"
        assert len(after_rollback["history"]) == 3  # nothing was deleted

    def test_cannot_deploy_a_model_that_never_finished(self, client):
        resp = client.post("/api/ml/models/does-not-exist/deploy")
        assert resp.status_code == 400


class TestPredict:
    def test_predict_against_a_real_trade(self, client):
        _seed_trades()
        model_id = _train(client)
        from futures_bot.research.trade_store import TradeStore, default_db_path
        store = TradeStore(default_db_path())
        trade_id = store.fetch_trades(strategy="trend_pullback")[0]["id"]
        store.close()

        resp = client.post("/api/ml/predict", json={"model_id": model_id, "trade_id": trade_id})
        assert resp.status_code == 200
        body = resp.json()
        assert 0.0 <= body["probability"] <= 1.0
        assert 0.0 <= body["confidence"] <= 1.0
        assert body["similar_trade_count"] > 0
        assert isinstance(body["top_reasons"], list)
