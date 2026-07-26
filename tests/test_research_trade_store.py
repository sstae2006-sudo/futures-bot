"""Tests for `research.trade_store`: schema creation, and round-tripping
trades and optimization trials through SQLite without losing precision."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from futures_bot.contracts import CME_TZ
from futures_bot.models import Side, Trade
from futures_bot.research.features import build_trade_records
from futures_bot.research.trade_store import TradeStore
from futures_bot.backtest.runner import EntryRecord


def make_records(n: int, run_id: str = "run-1") -> list:
    when = datetime(2026, 7, 21, 10, 0, tzinfo=CME_TZ)
    trades, entries = [], []
    for i in range(n):
        net = Decimal("10.33") if i % 2 == 0 else Decimal("-5.11")
        trades.append(
            Trade(
                side=Side.LONG if i % 2 == 0 else Side.SHORT, quantity=1,
                entry_price=Decimal("7500.25"), exit_price=Decimal("7510.50"),
                entry_time=when + timedelta(minutes=i), exit_time=when + timedelta(minutes=i + 30),
                gross_pnl=net, commission=Decimal("1.24"), exit_reason="take_profit",
            )
        )
        entries.append(
            EntryRecord(
                timestamp=when + timedelta(minutes=i), side="long", reason=f"entry {i}",
                metadata={"rsi": Decimal("55.123456789"), "trend_direction": "bullish"},
            )
        )
    return build_trade_records(
        trades, entries, run_id=run_id, contract="MES", strategy="trend_pullback",
        strategy_params={"adx_min": 20, "rsi_long_min": 55},
    )


class TestSchema:
    def test_creating_store_creates_tables(self, tmp_path):
        store = TradeStore(tmp_path / "trades.db")
        # No exception, and both tables are queryable.
        assert store.trade_count() == 0
        assert store.fetch_optimization_trials("nonexistent") == []
        store.close()

    def test_ensure_schema_is_idempotent(self, tmp_path):
        db = tmp_path / "trades.db"
        store = TradeStore(db)
        store.insert_trades(make_records(2))
        store.ensure_schema()  # must not wipe or error on existing data
        store.ensure_schema()
        assert store.trade_count() == 2
        store.close()

    def test_reopening_the_same_file_preserves_data(self, tmp_path):
        db = tmp_path / "trades.db"
        with TradeStore(db) as store:
            store.insert_trades(make_records(3))
        with TradeStore(db) as store2:
            assert store2.trade_count() == 3


class TestTrades:
    def test_insert_and_fetch_round_trips_decimal_precision(self, tmp_path):
        with TradeStore(tmp_path / "trades.db") as store:
            records = make_records(1)
            store.insert_trades(records)
            fetched = store.fetch_trades()

        assert len(fetched) == 1
        assert fetched[0]["net_pnl"] == records[0].net_pnl
        assert isinstance(fetched[0]["net_pnl"], Decimal)
        assert fetched[0]["entry_price"] == Decimal("7500.25")

    def test_insert_empty_list_is_a_noop(self, tmp_path):
        with TradeStore(tmp_path / "trades.db") as store:
            store.insert_trades([])
            assert store.trade_count() == 0

    def test_fetch_filters_by_run_id(self, tmp_path):
        with TradeStore(tmp_path / "trades.db") as store:
            store.insert_trades(make_records(2, run_id="run-a"))
            store.insert_trades(make_records(3, run_id="run-b"))

            assert store.trade_count() == 5
            assert store.trade_count("run-a") == 2
            assert len(store.fetch_trades(run_id="run-b")) == 3

    def test_fetch_filters_by_strategy(self, tmp_path):
        with TradeStore(tmp_path / "trades.db") as store:
            store.insert_trades(make_records(2))
            assert len(store.fetch_trades(strategy="trend_pullback")) == 2
            assert len(store.fetch_trades(strategy="vwap_reversion")) == 0

    def test_fetch_filters_by_side_and_outcome_in_sql(self, tmp_path):
        """`side`/`outcome` are pushed into the SQL WHERE clause (not
        filtered in Python after fetching every row) -- `make_records`
        alternates long/win and short/loss, so this also confirms the two
        filters combine with AND, not OR."""
        with TradeStore(tmp_path / "trades.db") as store:
            store.insert_trades(make_records(4))  # long/win, short/loss, long/win, short/loss
            assert len(store.fetch_trades(side="long")) == 2
            assert len(store.fetch_trades(side="short")) == 2
            assert len(store.fetch_trades(outcome="win")) == 2
            assert len(store.fetch_trades(outcome="loss")) == 2
            assert len(store.fetch_trades(side="long", outcome="win")) == 2
            assert len(store.fetch_trades(side="long", outcome="loss")) == 0

    def test_entry_metadata_round_trips_as_dict(self, tmp_path):
        with TradeStore(tmp_path / "trades.db") as store:
            store.insert_trades(make_records(1))
            fetched = store.fetch_trades()[0]
        assert fetched["entry_metadata"]["trend_direction"] == "bullish"
        assert fetched["strategy_params"] == {"adx_min": 20, "rsi_long_min": 55}


class TestOptimizationTrials:
    def test_insert_and_fetch_round_trip(self, tmp_path):
        with TradeStore(tmp_path / "trials.db") as store:
            store.insert_optimization_trial(
                batch_id="batch-1", strategy="ema_crossover", params={"fast_period": 9},
                train_trades=40, train_net_pnl=Decimal("500.5"), train_profit_factor=Decimal("1.42"),
                train_max_drawdown=Decimal("120"),
                validation_trades=15, validation_net_pnl=Decimal("90"),
                validation_profit_factor=Decimal("1.1"), validation_max_drawdown=Decimal("60"), rank=1,
            )
            trials = store.fetch_optimization_trials("batch-1")

        assert len(trials) == 1
        assert trials[0]["params"] == {"fast_period": 9}
        assert trials[0]["train_net_pnl"] == Decimal("500.5")
        assert trials[0]["rank"] == 1

    def test_null_profit_factor_round_trips_as_none(self, tmp_path):
        """A strategy with zero losing trades has an undefined profit factor
        (division by zero in `BacktestMetrics.profit_factor`) -- must store
        and read back as NULL/None, not a crash or a fabricated 0."""
        with TradeStore(tmp_path / "trials.db") as store:
            store.insert_optimization_trial(
                batch_id="batch-1", strategy="ema_crossover", params={},
                train_trades=5, train_net_pnl=Decimal("50"), train_profit_factor=None,
                train_max_drawdown=Decimal("0"),
            )
            trial = store.fetch_optimization_trials("batch-1")[0]
        assert trial["train_profit_factor"] is None
        assert trial["validation_net_pnl"] is None

    def test_filters_by_batch_id(self, tmp_path):
        with TradeStore(tmp_path / "trials.db") as store:
            store.insert_optimization_trial(
                batch_id="batch-a", strategy="x", params={}, train_trades=1,
                train_net_pnl=Decimal("1"), train_profit_factor=None, train_max_drawdown=Decimal("0"),
            )
            store.insert_optimization_trial(
                batch_id="batch-b", strategy="x", params={}, train_trades=1,
                train_net_pnl=Decimal("1"), train_profit_factor=None, train_max_drawdown=Decimal("0"),
            )
            assert len(store.fetch_optimization_trials("batch-a")) == 1
            assert len(store.fetch_optimization_trials("batch-b")) == 1


class TestRuns:
    """The Phase 6A research API's backing table: one row per backtest/
    walk-forward/optimizer/compare run, created before completion is known
    so a crash mid-run still leaves a visible row."""

    def test_insert_and_fetch_run(self, tmp_path):
        with TradeStore(tmp_path / "runs.db") as store:
            store.insert_run(
                run_id="run-1", kind="backtest", status="running",
                strategy="ema_crossover", contract="MES",
                strategy_params={"fast_period": 8}, csv_path="data.csv",
            )
            run = store.fetch_run("run-1")
        assert run["status"] == "running"
        assert run["strategy"] == "ema_crossover"
        assert run["strategy_params"] == {"fast_period": 8}
        assert run["walk_forward"] is False

    def test_fetch_missing_run_returns_none(self, tmp_path):
        with TradeStore(tmp_path / "runs.db") as store:
            assert store.fetch_run("does-not-exist") is None

    def test_complete_run_fills_in_metrics(self, tmp_path):
        with TradeStore(tmp_path / "runs.db") as store:
            store.insert_run(
                run_id="run-1", kind="backtest", status="running",
                strategy="ema_crossover", contract="MES", strategy_params={},
            )
            store.complete_run(
                "run-1",
                starting_equity=Decimal("2500"), trade_count=42, net_pnl=Decimal("312.50"),
                profit_factor=Decimal("1.35"), win_rate=Decimal("55.0"), expectancy=Decimal("7.44"),
                sharpe_ratio=Decimal("0.42"), sortino_ratio=Decimal("0.61"),
                max_drawdown=Decimal("120.00"), max_drawdown_pct=Decimal("4.8"),
                caveats=["Only 42 trades."],
            )
            run = store.fetch_run("run-1")
        assert run["status"] == "completed"
        assert run["trade_count"] == 42
        assert run["net_pnl"] == Decimal("312.50")
        assert run["profit_factor"] == Decimal("1.35")
        assert run["caveats"] == ["Only 42 trades."]
        assert run["completed_at"] is not None

    def test_complete_run_with_undefined_profit_factor_stores_none(self, tmp_path):
        with TradeStore(tmp_path / "runs.db") as store:
            store.insert_run(
                run_id="run-1", kind="backtest", status="running",
                strategy="ema_crossover", contract="MES", strategy_params={},
            )
            store.complete_run(
                "run-1", starting_equity=Decimal("2500"), trade_count=0, net_pnl=Decimal("0"),
                profit_factor=None, win_rate=None, expectancy=None,
                sharpe_ratio=None, sortino_ratio=None,
                max_drawdown=Decimal("0"), max_drawdown_pct=None, caveats=["No trades were taken."],
            )
            run = store.fetch_run("run-1")
        assert run["profit_factor"] is None
        assert run["win_rate"] is None

    def test_fail_run_records_error(self, tmp_path):
        with TradeStore(tmp_path / "runs.db") as store:
            store.insert_run(
                run_id="run-1", kind="backtest", status="running",
                strategy="ema_crossover", contract="MES", strategy_params={},
            )
            store.fail_run("run-1", "No bars loaded from data.csv")
            run = store.fetch_run("run-1")
        assert run["status"] == "failed"
        assert run["error_message"] == "No bars loaded from data.csv"

    def test_fetch_runs_filters_by_strategy(self, tmp_path):
        with TradeStore(tmp_path / "runs.db") as store:
            store.insert_run(
                run_id="run-1", kind="backtest", status="completed",
                strategy="ema_crossover", contract="MES", strategy_params={},
            )
            store.insert_run(
                run_id="run-2", kind="backtest", status="completed",
                strategy="vwap_reversion", contract="MES", strategy_params={},
            )
            emas = store.fetch_runs(strategy="ema_crossover")
        assert len(emas) == 1
        assert emas[0]["id"] == "run-1"

    def test_fetch_runs_filters_by_kind(self, tmp_path):
        with TradeStore(tmp_path / "runs.db") as store:
            store.insert_run(
                run_id="run-1", kind="backtest", status="completed",
                strategy="ema_crossover", contract="MES", strategy_params={},
            )
            store.insert_run(
                run_id="run-2", kind="optimizer", status="completed",
                strategy="ema_crossover", contract="MES", strategy_params={},
            )
            optimizer_runs = store.fetch_runs(kind="optimizer")
        assert len(optimizer_runs) == 1
        assert optimizer_runs[0]["id"] == "run-2"

    def test_fetch_runs_orders_most_recent_first(self, tmp_path):
        with TradeStore(tmp_path / "runs.db") as store:
            store.insert_run(
                run_id="run-1", kind="backtest", status="completed",
                strategy="ema_crossover", contract="MES", strategy_params={},
            )
            store.insert_run(
                run_id="run-2", kind="backtest", status="completed",
                strategy="ema_crossover", contract="MES", strategy_params={},
            )
            runs = store.fetch_runs()
        assert [r["id"] for r in runs] == ["run-2", "run-1"]

    def test_fetch_runs_respects_limit(self, tmp_path):
        with TradeStore(tmp_path / "runs.db") as store:
            for i in range(5):
                store.insert_run(
                    run_id=f"run-{i}", kind="backtest", status="completed",
                    strategy="ema_crossover", contract="MES", strategy_params={},
                )
            runs = store.fetch_runs(limit=2)
        assert len(runs) == 2

    def test_walk_forward_flag_and_validation_fields_round_trip(self, tmp_path):
        with TradeStore(tmp_path / "runs.db") as store:
            store.insert_run(
                run_id="run-1", kind="walk_forward", status="running",
                strategy="ema_crossover", contract="MES", strategy_params={}, walk_forward=True,
            )
            store.complete_run(
                "run-1", starting_equity=Decimal("2500"), trade_count=10, net_pnl=Decimal("50"),
                profit_factor=Decimal("1.1"), win_rate=Decimal("50"), expectancy=Decimal("5"),
                sharpe_ratio=None, sortino_ratio=None, max_drawdown=Decimal("20"), max_drawdown_pct=Decimal("1"),
                caveats=[], validation_trade_count=8, validation_net_pnl=Decimal("30"),
                validation_profit_factor=Decimal("1.05"),
            )
            run = store.fetch_run("run-1")
        assert run["walk_forward"] is True
        assert run["validation_trade_count"] == 8
        assert run["validation_net_pnl"] == Decimal("30")


class TestReports:
    def test_insert_and_fetch_reports(self, tmp_path):
        with TradeStore(tmp_path / "reports.db") as store:
            store.insert_report(report_id="rep-1", run_id="run-1", format="html", path="/reports/rep-1.html")
            reports = store.fetch_reports()
        assert len(reports) == 1
        assert reports[0]["path"] == "/reports/rep-1.html"

    def test_fetch_reports_filters_by_run_id(self, tmp_path):
        with TradeStore(tmp_path / "reports.db") as store:
            store.insert_report(report_id="rep-1", run_id="run-1", format="html", path="a.html")
            store.insert_report(report_id="rep-2", run_id="run-2", format="html", path="b.html")
            reports = store.fetch_reports(run_id="run-2")
        assert len(reports) == 1
        assert reports[0]["id"] == "rep-2"


class TestTradeAnalyticsColumns:
    """Phase 6B: MAE/MFE/efficiency/regime columns, added to `trades` via an
    ALTER TABLE migration in `ensure_schema` since the table already
    existed. All nullable -- a trade persisted without this data reads back
    as None, not a crash."""

    def test_excursion_and_regime_fields_round_trip(self, tmp_path):
        records = make_records(1)
        record = records[0]
        with_analytics = replace(
            record, mfe_points=Decimal("12.5"), mae_points=Decimal("3.25"), efficiency=Decimal("0.8"),
            regime_trend="bullish", regime_volatility="high", regime_session="morning",
        )
        with TradeStore(tmp_path / "trades.db") as store:
            store.insert_trades([with_analytics])
            fetched = store.fetch_trades()[0]
        assert fetched["mfe_points"] == Decimal("12.5")
        assert fetched["mae_points"] == Decimal("3.25")
        assert fetched["efficiency"] == Decimal("0.8")
        assert fetched["regime_trend"] == "bullish"
        assert fetched["regime_volatility"] == "high"
        assert fetched["regime_session"] == "morning"

    def test_missing_analytics_fields_are_none(self, tmp_path):
        with TradeStore(tmp_path / "trades.db") as store:
            store.insert_trades(make_records(1))
            fetched = store.fetch_trades()[0]
        assert fetched["mfe_points"] is None
        assert fetched["regime_trend"] is None

    def test_migration_adds_columns_to_a_pre_phase_6b_database(self, tmp_path):
        """Simulates a database created before these columns existed --
        insert a trade with the pre-6B schema (no ALTER yet), then reopen
        with the current TradeStore and confirm the migration runs cleanly
        and old rows read back with None for the new columns."""
        db_path = tmp_path / "old.db"
        conn = sqlite3.connect(db_path)
        old_schema = (
            "CREATE TABLE trades ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, contract TEXT NOT NULL, "
            "strategy TEXT NOT NULL, strategy_params TEXT NOT NULL, entry_time TEXT NOT NULL, "
            "exit_time TEXT NOT NULL, side TEXT NOT NULL, entry_price TEXT NOT NULL, "
            "exit_price TEXT NOT NULL, gross_pnl TEXT NOT NULL, commission TEXT NOT NULL, "
            "net_pnl TEXT NOT NULL, holding_minutes REAL NOT NULL, exit_reason TEXT NOT NULL, "
            "session_date TEXT NOT NULL, day_of_week TEXT NOT NULL, hour INTEGER NOT NULL, "
            "entry_reason TEXT NOT NULL, entry_metadata TEXT NOT NULL, outcome TEXT NOT NULL, "
            "created_at TEXT NOT NULL DEFAULT (datetime('now'))"
            ");"
        )
        conn.executescript(old_schema)
        insert_sql = (
            "INSERT INTO trades (run_id, contract, strategy, strategy_params, entry_time, exit_time, "
            "side, entry_price, exit_price, gross_pnl, commission, net_pnl, holding_minutes, "
            "exit_reason, session_date, day_of_week, hour, entry_reason, entry_metadata, outcome) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
        )
        conn.execute(insert_sql, (
            "r1", "MES", "x", "{}", "2026-01-01T00:00:00+00:00", "2026-01-01T00:30:00+00:00",
            "long", "1", "2", "1", "0.1", "0.9", 30.0, "target", "2026-01-01", "Thursday", 9,
            "e", "{}", "win",
        ))
        conn.commit()
        conn.close()

        with TradeStore(db_path) as store:
            fetched = store.fetch_trades()
        assert len(fetched) == 1
        assert fetched[0]["mfe_points"] is None
        assert fetched[0]["regime_session"] is None


class TestJobs:
    def test_insert_and_fetch_job_is_queued(self, tmp_path):
        with TradeStore(tmp_path / "jobs.db") as store:
            store.insert_job(job_id="job-1", kind="backtest", request={"strategy_name": "ema_crossover"})
            job = store.fetch_job("job-1")
        assert job["status"] == "queued"
        assert job["request"] == {"strategy_name": "ema_crossover"}

    def test_start_job_sets_running_and_total(self, tmp_path):
        with TradeStore(tmp_path / "jobs.db") as store:
            store.insert_job(job_id="job-1", kind="backtest", request={})
            store.start_job("job-1", progress_total=100)
            job = store.fetch_job("job-1")
        assert job["status"] == "running"
        assert job["progress_total"] == 100
        assert job["started_at"] is not None

    def test_update_progress(self, tmp_path):
        with TradeStore(tmp_path / "jobs.db") as store:
            store.insert_job(job_id="job-1", kind="backtest", request={})
            store.start_job("job-1", progress_total=100)
            store.update_job_progress("job-1", current=50, message="50/100 bars")
            job = store.fetch_job("job-1")
        assert job["progress_current"] == 50
        assert job["progress_message"] == "50/100 bars"

    def test_complete_job_records_result_id(self, tmp_path):
        with TradeStore(tmp_path / "jobs.db") as store:
            store.insert_job(job_id="job-1", kind="backtest", request={})
            store.complete_job("job-1", result_id="run-42")
            job = store.fetch_job("job-1")
        assert job["status"] == "completed"
        assert job["result_id"] == "run-42"
        assert job["completed_at"] is not None

    def test_fail_job_records_error(self, tmp_path):
        with TradeStore(tmp_path / "jobs.db") as store:
            store.insert_job(job_id="job-1", kind="backtest", request={})
            store.fail_job("job-1", "No bars loaded")
            job = store.fetch_job("job-1")
        assert job["status"] == "failed"
        assert job["error_message"] == "No bars loaded"

    def test_fetch_jobs_filters_by_status(self, tmp_path):
        with TradeStore(tmp_path / "jobs.db") as store:
            store.insert_job(job_id="job-1", kind="backtest", request={})
            store.insert_job(job_id="job-2", kind="backtest", request={})
            store.complete_job("job-2")
            running = store.fetch_jobs(status="queued")
        assert [j["id"] for j in running] == ["job-1"]

    def test_fetch_jobs_orders_newest_first(self, tmp_path):
        with TradeStore(tmp_path / "jobs.db") as store:
            store.insert_job(job_id="job-1", kind="backtest", request={})
            store.insert_job(job_id="job-2", kind="backtest", request={})
            jobs = store.fetch_jobs()
        assert [j["id"] for j in jobs] == ["job-2", "job-1"]

    def test_fetch_missing_job_returns_none(self, tmp_path):
        with TradeStore(tmp_path / "jobs.db") as store:
            assert store.fetch_job("nope") is None


class TestExperiments:
    def test_insert_and_fetch(self, tmp_path):
        with TradeStore(tmp_path / "exp.db") as store:
            store.insert_experiment(
                experiment_id="exp-1", name="VWAP in high vol",
                hypothesis="VWAP reversion performs better in high volatility",
                strategy="vwap_reversion", dataset="data.csv", parameters={"std_devs": 2}, run_id="run-1",
            )
            exp = store.fetch_experiment("exp-1")
        assert exp["name"] == "VWAP in high vol"
        assert exp["parameters"] == {"std_devs": 2}
        assert exp["run_id"] == "run-1"

    def test_update_notes(self, tmp_path):
        with TradeStore(tmp_path / "exp.db") as store:
            store.insert_experiment(experiment_id="exp-1", name="n", hypothesis="h", strategy="s", parameters={})
            store.update_experiment_notes("exp-1", "Confirmed: high-vol trades had 1.4x the win rate.")
            exp = store.fetch_experiment("exp-1")
        assert "Confirmed" in exp["notes"]

    def test_fetch_experiments_filters_by_strategy(self, tmp_path):
        with TradeStore(tmp_path / "exp.db") as store:
            store.insert_experiment(experiment_id="exp-1", name="a", hypothesis="h", strategy="vwap_reversion", parameters={})
            store.insert_experiment(experiment_id="exp-2", name="b", hypothesis="h", strategy="ema_crossover", parameters={})
            vwap_only = store.fetch_experiments(strategy="vwap_reversion")
        assert [e["id"] for e in vwap_only] == ["exp-1"]

    def test_fetch_missing_experiment_returns_none(self, tmp_path):
        with TradeStore(tmp_path / "exp.db") as store:
            assert store.fetch_experiment("nope") is None
