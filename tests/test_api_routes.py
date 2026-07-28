"""HTTP-level tests for the research API: status codes, request validation,
and error mapping. `tests/test_api_services.py` covers the underlying logic
in more depth; these tests exist to catch wiring bugs a service-level test
can't (a route registered with the wrong method, a response_model mismatch,
a validation error not mapping to a clean 4xx).
"""

from __future__ import annotations

import csv
import random
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
strategy_name: vwap_reversion
strategy_params:
  min_bars: 10
state_file: state/bot_state.json
"""


def _write_dataset(path: Path, n: int = 1500, seed: int = 11) -> None:
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

    from futures_bot.api.app import create_app

    return TestClient(create_app())


class TestHealthAndDocs:
    def test_health(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_openapi_schema_is_served(self, client):
        resp = client.get("/openapi.json")
        assert resp.status_code == 200
        assert "paths" in resp.json()


class TestStrategiesRoutes:
    def test_list_strategies(self, client):
        resp = client.get("/api/strategies")
        assert resp.status_code == 200
        names = {s["name"] for s in resp.json()}
        assert "vwap_reversion" in names

    def test_get_one_strategy(self, client):
        resp = client.get("/api/strategies/opening_range_breakout")
        assert resp.status_code == 200
        params = {p["name"] for p in resp.json()["parameters"]}
        assert "range_minutes" in params

    def test_unknown_strategy_is_400(self, client):
        resp = client.get("/api/strategies/not_a_real_strategy")
        assert resp.status_code == 400

    def test_list_datasets(self, client):
        resp = client.get("/api/datasets")
        assert resp.status_code == 200
        assert any(d["filename"] == "data.csv" for d in resp.json())


class TestBacktestRoutes:
    def test_run_backtest_returns_completed_run(self, client):
        resp = client.post("/api/backtest/run", json={
            "strategy_name": "vwap_reversion", "dataset": "data.csv", "contract": "MES",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "completed"
        assert body["kind"] == "backtest"

    def test_run_backtest_missing_required_field_is_422(self, client):
        resp = client.post("/api/backtest/run", json={"dataset": "data.csv"})  # no strategy_name
        assert resp.status_code == 422

    def test_run_backtest_unknown_dataset_is_400(self, client):
        resp = client.post("/api/backtest/run", json={
            "strategy_name": "vwap_reversion", "dataset": "nope.csv",
        })
        assert resp.status_code == 400

    def test_run_backtest_path_traversal_dataset_is_400(self, client):
        resp = client.post("/api/backtest/run", json={
            "strategy_name": "vwap_reversion", "dataset": "../outside.csv",
        })
        assert resp.status_code == 400

    def test_list_and_get_backtest(self, client):
        # strategy_params isn't tied to config.yaml's own value for a
        # *different* strategy_name than its default -- a request always
        # carries the full params it wants (the frontend pre-fills these
        # from GET /api/strategies' schema, not from config.yaml), so this
        # deliberately passes an explicit value rather than relying on a
        # fallback that wouldn't make sense across strategies.
        run = client.post("/api/backtest/run", json={
            "strategy_name": "vwap_reversion", "dataset": "data.csv", "strategy_params": {"min_bars": 12},
        }).json()

        listing = client.get("/api/backtests")
        assert listing.status_code == 200
        assert any(r["id"] == run["id"] for r in listing.json())

        detail = client.get(f"/api/backtests/{run['id']}")
        assert detail.status_code == 200
        assert detail.json()["strategy_params"] == {"min_bars": 12}

    def test_get_unknown_backtest_is_400(self, client):
        resp = client.get("/api/backtests/does-not-exist")
        assert resp.status_code == 400

    def test_walk_forward_flag(self, client):
        resp = client.post("/api/backtest/run", json={
            "strategy_name": "vwap_reversion", "dataset": "data.csv", "walk_forward": True,
        })
        assert resp.status_code == 200
        assert resp.json()["walk_forward"] is True
        assert resp.json()["kind"] == "walk_forward"

    def test_strategy_param_and_risk_overrides(self, client):
        resp = client.post("/api/backtest/run", json={
            "strategy_name": "vwap_reversion", "dataset": "data.csv",
            "strategy_params": {"min_bars": 20}, "stop_loss_points": 8, "starting_cash": 5000,
        })
        assert resp.status_code == 200
        assert resp.json()["strategy_params"] == {"min_bars": 20}


class TestTradesAndPerformanceRoutes:
    def test_trades_and_performance_for_a_run(self, client):
        run = client.post("/api/backtest/run", json={
            "strategy_name": "vwap_reversion", "dataset": "data.csv",
        }).json()

        trades = client.get(f"/api/trades?run_id={run['id']}")
        assert trades.status_code == 200
        assert len(trades.json()) == run["trade_count"]

        perf = client.get(f"/api/performance/{run['id']}")
        assert perf.status_code == 200
        assert perf.json()["run_id"] == run["id"]

    def test_trades_filterable_by_side(self, client):
        client.post("/api/backtest/run", json={"strategy_name": "vwap_reversion", "dataset": "data.csv"})
        resp = client.get("/api/trades?side=long")
        assert resp.status_code == 200
        assert all(t["side"] == "long" for t in resp.json())

    def test_performance_for_unknown_run_is_400(self, client):
        resp = client.get("/api/performance/does-not-exist")
        assert resp.status_code == 400

    def test_trades_carry_analytics_fields(self, client):
        run = client.post("/api/backtest/run", json={
            "strategy_name": "vwap_reversion", "dataset": "data.csv",
        }).json()
        trades = client.get(f"/api/trades?run_id={run['id']}").json()
        assert trades
        assert trades[0]["mfe_points"] is not None
        assert trades[0]["regime_session"] in ("open", "morning", "lunch", "close", "overnight")

    def test_trade_analytics_endpoint(self, client):
        run = client.post("/api/backtest/run", json={
            "strategy_name": "vwap_reversion", "dataset": "data.csv",
        }).json()
        resp = client.get(f"/api/trades/analytics?run_id={run['id']}")
        assert resp.status_code == 200
        body = resp.json()
        assert "best_entries" in body and "poor_exits" in body and "missed_opportunities" in body

    def test_regime_performance_endpoint(self, client):
        client.post("/api/backtest/run", json={"strategy_name": "vwap_reversion", "dataset": "data.csv"})
        resp = client.get("/api/regime/performance?strategy=vwap_reversion")
        assert resp.status_code == 200
        body = resp.json()
        assert "trend" in body and "volatility" in body and "session" in body


class TestCompareRoute:
    def test_compare_two_strategies(self, client):
        resp = client.post("/api/compare/run", json={
            "dataset": "data.csv", "strategy_names": ["vwap_reversion", "ema_crossover"],
        })
        assert resp.status_code == 200
        strategies = {e["strategy"] for e in resp.json()["entries"]}
        assert strategies == {"vwap_reversion", "ema_crossover"}

    def test_compare_unknown_strategy_is_400(self, client):
        resp = client.post("/api/compare/run", json={"dataset": "data.csv", "strategy_names": ["nope"]})
        assert resp.status_code == 400


class TestOptimizerRoutes:
    def test_run_and_fetch_optimizer_results(self, client):
        resp = client.post("/api/optimizer/run", json={
            "strategy_name": "vwap_reversion", "dataset": "data.csv",
            "param_grid": {"min_bars": [10, 15]}, "top_n": 2,
        })
        assert resp.status_code == 200
        batch_id = resp.json()["batch_id"]
        assert resp.json()["combos_tried"] == 2

        results = client.get(f"/api/optimizer/results/{batch_id}")
        assert results.status_code == 200
        assert len(results.json()) == 2

    def test_overfit_verdict_for_a_run(self, client):
        run = client.post("/api/backtest/run", json={
            "strategy_name": "vwap_reversion", "dataset": "data.csv",
        }).json()
        verdict = client.get(f"/api/walk-forward/{run['id']}/verdict")
        assert verdict.status_code == 200
        assert verdict.json()["level"] in ("green", "yellow", "red")


class TestReportRoutes:
    def test_generate_list_and_view_report(self, client):
        run = client.post("/api/backtest/run", json={
            "strategy_name": "vwap_reversion", "dataset": "data.csv",
        }).json()

        gen = client.post("/api/report/generate", json={"run_id": run["id"]})
        assert gen.status_code == 200
        report_id = gen.json()["id"]

        listing = client.get("/api/reports")
        assert listing.status_code == 200
        assert any(r["id"] == report_id for r in listing.json())

        view = client.get(f"/api/reports/{report_id}/view")
        assert view.status_code == 200
        assert "html" in view.headers["content-type"]

    def test_generate_report_for_unknown_run_is_400(self, client):
        resp = client.post("/api/report/generate", json={"run_id": "does-not-exist"})
        assert resp.status_code == 400


class TestSystemRoutes:
    def test_overview_reflects_activity(self, client):
        client.post("/api/backtest/run", json={"strategy_name": "vwap_reversion", "dataset": "data.csv"})
        resp = client.get("/api/system/overview")
        assert resp.status_code == 200
        assert resp.json()["total_backtests"] == 1

    def test_logs_endpoint(self, client):
        client.post("/api/backtest/run", json={"strategy_name": "vwap_reversion", "dataset": "data.csv"})
        resp = client.get("/api/logs")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_insights_endpoint(self, client):
        client.post("/api/backtest/run", json={"strategy_name": "vwap_reversion", "dataset": "data.csv"})
        resp = client.get("/api/insights")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_infrastructure_endpoint_reports_real_metrics(self, client):
        resp = client.get("/api/system/infrastructure")

        assert resp.status_code == 200
        body = resp.json()
        assert body["memory_total_mb"] > 0
        assert body["disk_total_gb"] > 0
        assert 0.0 <= body["memory_percent"] <= 100.0
        assert body["jobs_queued"] == 0
        assert body["jobs_running"] == 0

    def test_infrastructure_reflects_job_queue_depth(self, client):
        client.post("/api/backtest/run", json={"strategy_name": "vwap_reversion", "dataset": "data.csv"})

        resp = client.get("/api/system/infrastructure")

        # The synchronous /api/backtest/run route (not /api/jobs/backtest)
        # never touches the jobs table at all -- this just confirms the
        # route stays well-formed with real backtest activity present,
        # not that it double-counts a synchronous run as a queued job.
        assert resp.status_code == 200
        assert resp.json()["jobs_queued"] == 0


class TestExperimentRoutes:
    def test_create_and_get(self, client):
        resp = client.post("/api/experiments", json={
            "name": "VWAP in high vol", "hypothesis": "Performs better in high volatility.",
            "strategy": "vwap_reversion", "parameters": {"std_devs": 2},
        })
        assert resp.status_code == 200
        experiment_id = resp.json()["id"]

        fetched = client.get(f"/api/experiments/{experiment_id}")
        assert fetched.status_code == 200
        assert fetched.json()["name"] == "VWAP in high vol"

    def test_list_experiments(self, client):
        client.post("/api/experiments", json={
            "name": "a", "hypothesis": "h", "strategy": "vwap_reversion", "parameters": {},
        })
        resp = client.get("/api/experiments")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_update_notes(self, client):
        created = client.post("/api/experiments", json={
            "name": "a", "hypothesis": "h", "strategy": "vwap_reversion", "parameters": {},
        }).json()
        resp = client.patch(f"/api/experiments/{created['id']}/notes", json={"notes": "Confirmed."})
        assert resp.status_code == 200
        assert resp.json()["notes"] == "Confirmed."

    def test_get_unknown_experiment_is_400(self, client):
        resp = client.get("/api/experiments/does-not-exist")
        assert resp.status_code == 400


class TestMlRoute:
    def test_ml_dataset_info(self, client):
        client.post("/api/backtest/run", json={"strategy_name": "vwap_reversion", "dataset": "data.csv"})
        resp = client.get("/api/ml/dataset")
        assert resp.status_code == 200
        assert "feature_columns" in resp.json()


class TestNoUnsafeTradingControls:
    """This API must never expose a way to place, modify, or cancel a REAL
    order. That boundary is not "no live anything" -- Phase 6C's dashboard
    paper-trading session (`api/live_session.py`) legitimately drives the
    live engine and a live data feed, because `PaperBroker` risks nothing
    real. The line that actually matters, enforced two ways below:

    1. `brokers.tradovate` (the one module that can touch a real account)
       is never imported anywhere under `api/`, full stop -- not even in
       `live_session.py`. Real trading stays terminal-only
       (`python -m futures_bot.cli --live`), where someone has to read
       `brokers/tradovate.py`'s safety checklist and type the command
       themselves.
    2. `feeds.massive` (a live *data* feed, not a broker -- no money risk
       on its own) is allowed, but only inside `live_session.py`, which is
       required to check `broker.name == "paper"` before it ever runs --
       see `TestLiveSessionSafety` in `tests/test_api_live_session.py` for
       the runtime proof that check actually fires.
    """

    def test_no_route_paths_suggest_placing_a_real_order(self, client):
        schema = client.get("/openapi.json").json()
        paths = list(schema["paths"].keys())
        # /api/live/* is the intentional, paper-only exception (see above) --
        # everything else here would mean placing/modifying a real order.
        forbidden_substrings = ("/order", "/trade/execute", "/trade/place", "/broker/connect")
        offending = [p for p in paths if any(bad in p for bad in forbidden_substrings)]
        assert offending == [], f"Route(s) that look like real order placement: {offending}"

    def test_tradovate_is_never_imported_anywhere_under_api(self):
        import ast
        from pathlib import Path as _Path

        api_dir = _Path(__file__).resolve().parents[1] / "src" / "futures_bot" / "api"
        offenders = []
        for py_file in api_dir.rglob("*.py"):
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module and "tradovate" in node.module:
                    offenders.append((py_file.name, node.module))
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if "tradovate" in alias.name:
                            offenders.append((py_file.name, alias.name))
        assert offenders == [], f"api/ imports brokers.tradovate -- real-order risk: {offenders}"

    def test_massive_feed_is_only_imported_by_live_session(self):
        import ast
        from pathlib import Path as _Path

        api_dir = _Path(__file__).resolve().parents[1] / "src" / "futures_bot" / "api"
        offenders = []
        for py_file in api_dir.rglob("*.py"):
            if py_file.name == "live_session.py":
                continue  # the one file allowed to -- see class docstring
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module and "massive" in node.module:
                    offenders.append((py_file.name, node.module))
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if "massive" in alias.name:
                            offenders.append((py_file.name, alias.name))
        assert offenders == [], f"A file other than live_session.py imports the live feed: {offenders}"
