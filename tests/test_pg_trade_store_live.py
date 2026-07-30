"""Behavior verification for `PgTradeStore` against a *real* Postgres/
TimescaleDB server -- mirrors `test_pg_market_data_store_live.py`'s
skip-guard pattern exactly. `test_trade_store_parity.py` only checks
method signatures; this module calls real methods and checks the SQL does
the right thing for a representative case per subsystem.

Skipped automatically unless BOTH a reachable server is configured via
`FUTURES_BOT_DATABASE_URL` AND the destructive-test opt-in is given (see
`tests/_live_test_guard.py`'s module docstring, KNOWN_ISSUES.md
ISSUE-041 -- this module TRUNCATEs real tables in its cleanup fixture,
so `FUTURES_BOT_DATABASE_URL` being set is deliberately not enough on
its own):

    docker compose -f deploy/docker-compose.yml up -d timescaledb
    export FUTURES_BOT_DATABASE_URL=postgresql+psycopg://futures_bot:futures_bot_dev_only@127.0.0.1:5432/futures_bot
    export FUTURES_BOT_ALLOW_LIVE_DB_TESTS=1
    python -m alembic upgrade head
    pytest tests/test_pg_trade_store_live.py -v
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("psycopg")

from futures_bot.db.engine import dispose_engine  # noqa: E402
from futures_bot.research.features import TradeRecord  # noqa: E402
from tests._live_test_guard import live_server_reachable, skip_reason  # noqa: E402

pytestmark = pytest.mark.skipif(not live_server_reachable(), reason=skip_reason())

_TABLES = (
    "trades", "optimization_trials", "runs", "reports", "jobs", "experiments", "ml_models",
    "model_deployments", "client_profiles", "import_staging", "client_import_fills",
    "client_open_lots", "import_history", "config_deployments",
)


def _make_trade_record(**overrides) -> TradeRecord:
    entry = datetime(2026, 1, 1, 9, 30, tzinfo=timezone.utc)
    defaults = dict(
        run_id="run-1", contract="MES", strategy="ema_crossover", strategy_params={"fast": 9, "slow": 21},
        entry_time=entry, exit_time=entry + timedelta(minutes=15), side="long",
        entry_price=Decimal("5000.25"), exit_price=Decimal("5005.50"),
        gross_pnl=Decimal("21.00"), commission=Decimal("1.24"), net_pnl=Decimal("19.76"),
        holding_minutes=15.0, exit_reason="target",
        session_date="2026-01-01", day_of_week="Thursday", hour=9,
        entry_reason="ema_cross_up", entry_metadata={"fast_ema": "4998.00"}, outcome="win",
    )
    defaults.update(overrides)
    return TradeRecord(**defaults)


@pytest.fixture()
def store(live_database_url):
    from futures_bot.research.pg_trade_store import PgTradeStore

    s = PgTradeStore()
    s.ensure_schema()
    yield s
    from sqlalchemy import text

    with s._engine.begin() as conn:  # noqa: SLF001 -- test-only, direct cleanup
        conn.execute(text(f"TRUNCATE {', '.join(_TABLES)}"))
    s.close()


@pytest.fixture(scope="module", autouse=True)
def _dispose_engine_after_module():
    yield
    dispose_engine()


class TestTrades:
    def test_insert_and_fetch_roundtrip(self, store):
        store.insert_trades([_make_trade_record(), _make_trade_record(trade_id="t2", run_id="run-1", outcome="loss")])

        assert store.trade_count() == 2
        assert store.trade_count(run_id="run-1") == 2
        assert store.trade_count(run_id="nope") == 0

        trades = store.fetch_trades(run_id="run-1")
        assert len(trades) == 2
        assert isinstance(trades[0]["entry_price"], Decimal)
        assert isinstance(trades[0]["entry_time"], str), "entry_time must be a string, matching TradeStore's raw passthrough"
        assert trades[0]["strategy_params"] == {"fast": 9, "slow": 21}
        assert trades[0]["entry_slippage"] == Decimal("0")

        wins = store.fetch_trades(outcome="win")
        assert len(wins) == 1


class TestOptimizationTrials:
    def test_insert_update_and_resume_lookup(self, store):
        store.insert_optimization_trial(
            batch_id="batch-1", strategy="ema_crossover", params={"fast": 9, "slow": 21},
            train_trades=10, train_net_pnl=Decimal("100.00"), train_profit_factor=Decimal("1.5"),
            train_max_drawdown=Decimal("20.00"),
        )
        # Duplicate combo -- must be a silent no-op (ON CONFLICT DO NOTHING).
        store.insert_optimization_trial(
            batch_id="batch-1", strategy="ema_crossover", params={"fast": 9, "slow": 21},
            train_trades=999, train_net_pnl=Decimal("0"), train_profit_factor=None,
            train_max_drawdown=Decimal("0"),
        )

        trials = store.fetch_optimization_trials("batch-1")
        assert len(trials) == 1
        assert trials[0]["train_trades"] == 10  # the duplicate insert did not overwrite it
        combo_key = trials[0]["combo_key"]

        store.update_optimization_trial_validation(
            batch_id="batch-1", combo_key=combo_key, validation_trades=5,
            validation_net_pnl=Decimal("40.00"), validation_profit_factor=Decimal("1.2"),
            validation_max_drawdown=Decimal("5.00"), rank=1,
        )
        updated = store.fetch_optimization_trials("batch-1")[0]
        assert updated["validation_trades"] == 5
        assert updated["rank"] == 1

        combos = store.fetch_completed_combos("batch-1")
        assert combo_key in combos


class TestRuns:
    def test_insert_complete_and_fail(self, store):
        store.insert_run(
            run_id="run-a", kind="backtest", status="running", strategy="ema_crossover",
            contract="MES", strategy_params={"fast": 9}, walk_forward=False,
        )
        store.complete_run(
            "run-a", starting_equity=Decimal("50000"), trade_count=10, net_pnl=Decimal("500.00"),
            profit_factor=Decimal("1.8"), win_rate=Decimal("0.6"), expectancy=Decimal("50.00"),
            sharpe_ratio=Decimal("1.1"), sortino_ratio=Decimal("1.3"), max_drawdown=Decimal("100.00"),
            max_drawdown_pct=Decimal("2.0"), caveats=["short history"],
        )
        run = store.fetch_run("run-a")
        assert run["status"] == "completed"
        assert run["caveats"] == ["short history"]
        assert isinstance(run["net_pnl"], Decimal)
        assert isinstance(run["created_at"], str)

        store.insert_run(
            run_id="run-b", kind="backtest", status="running", strategy="ema_crossover",
            contract="MES", strategy_params={},
        )
        store.fail_run("run-b", "no data")
        failed = store.fetch_run("run-b")
        assert failed["status"] == "failed"
        assert failed["error_message"] == "no data"

        runs = store.fetch_runs(strategy="ema_crossover")
        assert {r["id"] for r in runs} == {"run-a", "run-b"}

    def test_run_with_no_caveats_defaults_to_empty_list(self, store):
        store.insert_run(run_id="run-c", kind="backtest", status="running", strategy="x", contract="MES", strategy_params={})
        run = store.fetch_run("run-c")
        assert run["caveats"] == []


class TestReports:
    def test_insert_and_fetch(self, store):
        store.insert_run(run_id="run-r", kind="backtest", status="completed", strategy="x", contract="MES", strategy_params={})
        store.insert_report(report_id="rep-1", run_id="run-r", format="html", path="/reports/rep-1.html")
        reports = store.fetch_reports(run_id="run-r")
        assert len(reports) == 1
        assert reports[0]["path"] == "/reports/rep-1.html"


class TestJobs:
    def test_full_lifecycle(self, store):
        store.insert_job(job_id="job-1", kind="backtest", request={"strategy": "ema_crossover"})
        assert store.fetch_job("job-1")["status"] == "queued"

        store.start_job("job-1", progress_total=100)
        store.update_job_progress("job-1", current=50, message="halfway")
        job = store.fetch_job("job-1")
        assert job["status"] == "running"
        assert job["progress_current"] == 50
        assert job["progress_total"] == 100

        store.complete_job("job-1", result_id="run-a", result_payload={"net_pnl": "500.00"})
        job = store.fetch_job("job-1")
        assert job["status"] == "completed"
        assert job["result_payload"] == {"net_pnl": "500.00"}

        store.insert_job(job_id="job-2", kind="optimizer", request={})
        store.fail_job("job-2", "OOM")
        assert store.fetch_job("job-2")["status"] == "failed"

        jobs = store.fetch_jobs(status="completed")
        assert {j["id"] for j in jobs} == {"job-1"}


class TestExperiments:
    def test_insert_update_and_fetch(self, store):
        store.insert_experiment(
            experiment_id="exp-1", name="Test VWAP in high vol", hypothesis="works better",
            strategy="vwap_reversion", parameters={"std_devs": 2},
        )
        exp = store.fetch_experiment("exp-1")
        assert exp["name"] == "Test VWAP in high vol"
        assert exp["parameters"] == {"std_devs": 2}

        store.update_experiment_notes("exp-1", "confirmed")
        assert store.fetch_experiment("exp-1")["notes"] == "confirmed"

        all_exps = store.fetch_experiments(strategy="vwap_reversion")
        assert len(all_exps) == 1


class TestMlModelsAndDeployments:
    def test_versioning_lifecycle_and_archive(self, store):
        v1 = store.insert_model(
            model_id="model-1", strategy="ema_crossover", model_type="random_forest",
            feature_columns=["rsi", "adx"], hyperparameters={"n_estimators": 100},
            evaluation_mode="chronological_split", dataset_size=1000, dataset_version="v1",
        )
        assert v1 == 1
        v2 = store.insert_model(
            model_id="model-2", strategy="ema_crossover", model_type="random_forest",
            feature_columns=["rsi"], hyperparameters={"n_estimators": 200},
            evaluation_mode="chronological_split", dataset_size=1000, dataset_version="v1",
        )
        assert v2 == 2

        store.update_model_status("model-1", "training")
        store.update_model_job_id("model-1", "job-x")
        store.complete_model(
            "model-1", metrics={"accuracy": 0.7}, feature_importance=[{"feature": "rsi", "importance": 0.6}],
            artifact_path="/models/model-1.pkl", app_version="0.7.0", git_commit="abc123",
        )
        model = store.fetch_model("model-1")
        assert model["status"] == "finished"
        assert model["metrics"] == {"accuracy": 0.7}
        assert model["overfit_warning"] is False

        store.archive_model("model-1")
        assert len(store.fetch_models(strategy="ema_crossover")) == 1  # model-1 excluded by default
        assert len(store.fetch_models(strategy="ema_crossover", include_archived=True)) == 2

        versions = store.fetch_model_versions("ema_crossover:random_forest")
        assert [v["version"] for v in versions] == [1, 2]

        artifact_path = store.delete_model("model-2")
        assert artifact_path is None  # model-2 never completed, so no artifact_path

    def test_deployment_deploy_rollback_undeploy(self, store):
        store.insert_model(
            model_id="model-a", strategy="s1", model_type="logistic_regression",
            feature_columns=[], hyperparameters={}, evaluation_mode="chronological_split",
            dataset_size=1, dataset_version="v1",
        )
        store.insert_deployment(deployment_id="dep-1", strategy="s1", model_id="model-a", action="deploy")
        assert store.fetch_current_deployment("s1")["model_id"] == "model-a"

        store.insert_deployment(deployment_id="dep-2", strategy="s1", model_id=None, action="undeploy")
        assert store.fetch_current_deployment("s1") is None  # latest action is undeploy

        history = store.fetch_deployment_history("s1")
        assert len(history) == 2


class TestClientImportPipeline:
    def test_full_pipeline_with_fifo_matching_and_failure_path(self, store):
        store.insert_client_profile(profile_id="prof-1", name="Alice")
        assert store.fetch_client_profile("prof-1")["name"] == "Alice"
        assert len(store.fetch_client_profiles()) == 1

        store.insert_import_staging(
            staging_id="stg-1", profile_id="prof-1", filename="trades.csv",
            stored_path="/staging/trades.csv", detected_format="tradovate",
            suggested_mapping={"time": "fill_time"},
        )
        staged = store.fetch_import_staging("stg-1")
        assert staged["suggested_mapping"] == {"time": "fill_time"}
        assert store.delete_import_staging("stg-1") == "/staging/trades.csv"
        assert store.fetch_import_staging("stg-1") is None

        # First import: opens a long lot via one new fill, no consumed/updated lots.
        entry_time = datetime(2026, 1, 1, 9, 30, tzinfo=timezone.utc)
        store.commit_client_import(
            import_id="imp-1", profile_id="prof-1", filename="trades1.csv", detected_format="tradovate",
            new_fill_rows=[{
                "fingerprint": "fp-1", "symbol": "MES", "fill_time": entry_time, "side": "buy",
                "quantity": Decimal("1"), "price": Decimal("5000.00"), "raw_row": {"note": "open"},
            }],
            consumed_lot_ids=[], updated_lots=[],
            new_open_lots=[{
                "symbol": "MES", "side": "buy", "quantity_remaining": Decimal("1"),
                "entry_price": Decimal("5000.00"), "entry_time": entry_time,
                "entry_fingerprint": "fp-1", "raw_entry_row": {"note": "open"},
            }],
            trade_records=[], total_fill_rows=1, imported_fill_count=1, duplicate_fill_count=0,
            error_count=0, errors=[], warnings=[],
        )
        assert store.fetch_existing_fingerprints("prof-1", ["fp-1", "fp-2"]) == {"fp-1"}
        open_lots = store.fetch_open_lots("prof-1", "MES")
        assert len(open_lots) == 1
        lot_id = open_lots[0]["id"]
        assert open_lots[0]["quantity_remaining"] == Decimal("1")

        # Second import: closes that lot (FIFO match) via a new fill + a
        # closed TradeRecord, in the same transaction.
        exit_time = entry_time + timedelta(minutes=20)
        store.commit_client_import(
            import_id="imp-2", profile_id="prof-1", filename="trades2.csv", detected_format="tradovate",
            new_fill_rows=[{
                "fingerprint": "fp-2", "symbol": "MES", "fill_time": exit_time, "side": "sell",
                "quantity": Decimal("1"), "price": Decimal("5010.00"), "raw_row": {"note": "close"},
            }],
            consumed_lot_ids=[lot_id], updated_lots=[],
            new_open_lots=[],
            trade_records=[_make_trade_record(
                run_id="import:prof-1", trade_id="closed-1", entry_time=entry_time, exit_time=exit_time,
            )],
            total_fill_rows=1, imported_fill_count=1, duplicate_fill_count=0,
            error_count=0, errors=[], warnings=["fifo matched"],
        )
        assert store.fetch_open_lots("prof-1", "MES") == []  # consumed
        assert store.trade_count(run_id="import:prof-1") == 1

        history = store.fetch_import_history("prof-1")
        assert len(history) == 2
        assert history[0]["status"] == "completed"  # newest-first: imp-2

        # A failed import still leaves a visible history row.
        store.fail_client_import(
            import_id="imp-3", profile_id="prof-1", filename="bad.csv",
            detected_format="unknown", error_message="unparseable file",
        )
        failed = [h for h in store.fetch_import_history("prof-1", limit=10) if h["id"] == "imp-3"][0]
        assert failed["status"] == "failed"
        assert failed["error_message"] == "unparseable file"
        assert failed["warnings"] == []


class TestConfigDeployments:
    def test_insert_and_fetch(self, store):
        store.insert_config_deployment(
            deployment_id="cfg-1", strategy="trend_pullback", action="deploy",
            params={"atr_stop_mult": 1.5}, backup_path="/backups/config_1.yaml",
        )
        dep = store.fetch_config_deployment("cfg-1")
        assert dep["params"] == {"atr_stop_mult": 1.5}

        deployments = store.fetch_config_deployments(strategy="trend_pullback")
        assert len(deployments) == 1


class TestMiscProperties:
    def test_location_and_size_bytes(self, store):
        assert "futures_bot" in store.location
        assert store.size_bytes >= 0
