"""HTTP-level tests for Phase 10.2: deploying/rolling back a research-server
"recommendation" finding's params into config.yaml, and testing them via a
real backtest comparison first. Follows `test_api_research_server.py`'s own
config/client fixture conventions (a plain client, no research-server
lifecycle needed for these routes)."""

from __future__ import annotations

import csv
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

research_server:
  enabled: false
  paper_strategies: [vwap_reversion]

state_file: state/bot_state.json
"""


def _write_dataset(path: Path, n: int = 600, seed: int = 3) -> None:
    rng = random.Random(seed)
    rows = [["timestamp", "open", "high", "low", "close", "volume"]]
    price = Decimal("7500")
    start = datetime(2026, 1, 5, 8, 30, tzinfo=CME_TZ)
    for i in range(n):
        price += Decimal(str(round(rng.uniform(-3, 3), 2)))
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


def _seed_optimizer_recommendation(tmp_path, strategy="vwap_reversion", params=None):
    from futures_bot.research.trade_store import TradeStore, default_db_path

    store = TradeStore(default_db_path())
    store.insert_run(run_id="opt1", kind="optimizer", status="running", strategy=strategy, contract="MES", strategy_params={})
    store.complete_run(
        "opt1", starting_equity=Decimal("2500"), trade_count=50, net_pnl=Decimal("500"),
        profit_factor=Decimal("1.5"), win_rate=Decimal("60"), expectancy=Decimal("10"),
        sharpe_ratio=Decimal("1"), sortino_ratio=Decimal("1"), max_drawdown=Decimal("100"),
        max_drawdown_pct=Decimal("4"), caveats=[],
    )
    store.insert_optimization_trial(
        batch_id="opt1", strategy=strategy, params=params or {"min_bars": 20, "std_devs": 2.5},
        train_trades=50, train_net_pnl=Decimal("500"), train_profit_factor=Decimal("1.5"),
        train_max_drawdown=Decimal("100"), rank=1,
    )
    store.close()


def _wait_for_terminal(client, job_id, timeout=30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("completed", "failed"):
            return job
        time.sleep(0.05)
    raise TimeoutError(f"job {job_id} did not finish in time")


class TestFindingDetailsPayload:
    def test_recommendation_finding_carries_deployable_details(self, client, tmp_path):
        _seed_optimizer_recommendation(tmp_path)
        resp = client.get("/api/research-server/findings")
        assert resp.status_code == 200
        rec = next(f for f in resp.json() if f["category"] == "recommendation")
        assert rec["details"]["is_deployable"] is True
        assert rec["details"]["recommended_params"] == {"min_bars": 20, "std_devs": 2.5}
        assert rec["details"]["current_params"] == {"min_bars": 10}

    def test_finding_for_a_non_active_strategy_is_flagged_not_deployable(self, client, tmp_path):
        # Active strategy_name is ema_crossover; vwap_reversion is only
        # paper-traded alongside it, so its recommendation can't be
        # deployed (only the active strategy has a config-file
        # strategy_params slot -- see deploy_strategy_params's docstring).
        config = CONFIG_YAML.replace("strategy_name: vwap_reversion", "strategy_name: ema_crossover")
        config += "\nresearch_server:\n  enabled: false\n  paper_strategies: [vwap_reversion]\n"
        (tmp_path / "config.yaml").write_text(config, encoding="utf-8")
        _seed_optimizer_recommendation(tmp_path)

        resp = client.get("/api/research-server/findings")
        rec = next(f for f in resp.json() if f["category"] == "recommendation")
        assert rec["details"]["is_deployable"] is False


class TestDeployAndRollback:
    def test_deploy_rewrites_config_yaml(self, client, tmp_path):
        _seed_optimizer_recommendation(tmp_path)
        resp = client.post("/api/research-server/insights/deploy", json={
            "strategy": "vwap_reversion", "params": {"min_bars": 20, "std_devs": 2.5}, "run_id": "opt1",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["action"] == "deploy"
        assert body["backup_path"]

        config_text = (tmp_path / "config.yaml").read_text()
        assert "min_bars: 20" in config_text

    def test_deploy_backs_up_the_original_file_first(self, client, tmp_path):
        original = (tmp_path / "config.yaml").read_text()
        _seed_optimizer_recommendation(tmp_path)
        resp = client.post("/api/research-server/insights/deploy", json={
            "strategy": "vwap_reversion", "params": {"min_bars": 20},
        })
        backup_path = Path(resp.json()["backup_path"])
        assert backup_path.exists()
        assert backup_path.read_text() == original

    def test_deploy_rejects_a_strategy_that_is_not_the_active_one(self, client, tmp_path):
        resp = client.post("/api/research-server/insights/deploy", json={
            "strategy": "ema_crossover", "params": {"fast_period": 3},
        })
        assert resp.status_code == 400
        assert "not the active strategy" in resp.json()["detail"]

    def test_rollback_restores_the_exact_prior_file(self, client, tmp_path):
        original = (tmp_path / "config.yaml").read_text()
        _seed_optimizer_recommendation(tmp_path)
        deploy_resp = client.post("/api/research-server/insights/deploy", json={
            "strategy": "vwap_reversion", "params": {"min_bars": 20, "std_devs": 2.5},
        })
        deployment_id = deploy_resp.json()["id"]

        rollback_resp = client.post(f"/api/research-server/insights/config-deployments/{deployment_id}/rollback")
        assert rollback_resp.status_code == 200
        assert rollback_resp.json()["action"] == "rollback"

        restored = (tmp_path / "config.yaml").read_text()
        assert restored == original

    def test_rollback_of_unknown_deployment_is_400(self, client):
        resp = client.post("/api/research-server/insights/config-deployments/does-not-exist/rollback")
        assert resp.status_code == 400

    def test_deployment_history_is_never_destroyed(self, client, tmp_path):
        _seed_optimizer_recommendation(tmp_path)
        deploy_resp = client.post("/api/research-server/insights/deploy", json={
            "strategy": "vwap_reversion", "params": {"min_bars": 20, "std_devs": 2.5},
        })
        client.post(f"/api/research-server/insights/config-deployments/{deploy_resp.json()['id']}/rollback")

        history = client.get("/api/research-server/insights/config-deployments", params={"strategy": "vwap_reversion"}).json()
        assert len(history) == 2
        assert history[0]["action"] == "rollback"
        assert history[1]["action"] == "deploy"

    def test_concurrent_deploys_never_interleave(self, client, tmp_path):
        """Regression: two deploys landing on different FastAPI worker
        threads at the same instant used to have no lock around
        read -> backup -> write, so the later write could interleave with
        (not just supersede) the earlier one. Both requests must succeed,
        config.yaml must end up valid YAML matching exactly one of the two
        attempted param sets (never a corrupted mix), and both backups must
        themselves be valid, complete snapshots."""
        import yaml
        from concurrent.futures import ThreadPoolExecutor

        _seed_optimizer_recommendation(tmp_path)
        param_sets = [{"min_bars": 20, "std_devs": 2.5}, {"min_bars": 30, "std_devs": 3.0}]

        def deploy(params):
            return client.post(
                "/api/research-server/insights/deploy",
                json={"strategy": "vwap_reversion", "params": params},
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(deploy, param_sets))

        assert all(r.status_code == 200 for r in responses)

        final = yaml.safe_load((tmp_path / "config.yaml").read_text())
        assert final["strategy_params"] in param_sets

        history = client.get(
            "/api/research-server/insights/config-deployments", params={"strategy": "vwap_reversion"},
        ).json()
        assert len(history) == 2
        for entry in history:
            backup_path = Path(entry["backup_path"])
            assert backup_path.exists()
            yaml.safe_load(backup_path.read_text())  # must be complete, parseable YAML, not a partial write


class TestParamsComparison:
    def test_submits_and_completes_with_a_diff(self, client, tmp_path):
        _seed_optimizer_recommendation(tmp_path)
        resp = client.post("/api/research-server/insights/test-params", json={
            "strategy": "vwap_reversion", "dataset": "data.csv",
            "recommended_params": {"min_bars": 20, "std_devs": 2.5},
        })
        assert resp.status_code == 200
        job = resp.json()
        assert job["kind"] == "params_comparison"

        final = _wait_for_terminal(client, job["id"])
        assert final["status"] == "completed"
        payload = final["result_payload"]
        assert "current" in payload and "recommended" in payload
        assert "improvement_pct" in payload

    def test_comparison_does_not_touch_config_yaml(self, client, tmp_path):
        original = (tmp_path / "config.yaml").read_text()
        _seed_optimizer_recommendation(tmp_path)
        resp = client.post("/api/research-server/insights/test-params", json={
            "strategy": "vwap_reversion", "dataset": "data.csv",
            "recommended_params": {"min_bars": 30},
        })
        _wait_for_terminal(client, resp.json()["id"])
        assert (tmp_path / "config.yaml").read_text() == original
