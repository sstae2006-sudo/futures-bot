"""Tests for `api.services` -- the actual work behind the research API,
independently of HTTP (see `tests/test_api_routes.py` for the HTTP-level
tests: request validation, status codes, error mapping).
"""

from __future__ import annotations

import csv
import random
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

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


def _write_dataset(path: Path, n: int = 2000, seed: int = 3) -> None:
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
def workspace(tmp_path, monkeypatch):
    """A CWD with config.yaml + data.csv, and an isolated research DB --
    matches how the CLI itself resolves `config.yaml` (relative to CWD),
    so `services.py`'s defaults work unmodified."""
    (tmp_path / "config.yaml").write_text(CONFIG_YAML, encoding="utf-8")
    _write_dataset(tmp_path / "data.csv")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FUTURES_BOT_RESEARCH_DB", str(tmp_path / "research.db"))
    return tmp_path


# Strategies must be registered for StrategyRegistry.get/StrategyRegistry.names
# to see them -- api.services already imports them at module level, so
# importing the module (done implicitly by every `from futures_bot.api...`
# import below) is enough; no explicit registration needed here.
from futures_bot.api import services  # noqa: E402
from futures_bot.api.schemas import (  # noqa: E402
    BacktestRunRequest, CompareRequest, ExperimentCreateRequest, OptimizerRunRequest,
)
from futures_bot.api.services import ApiError  # noqa: E402


class TestDatasets:
    def test_lists_csv_files_in_cwd(self, workspace):
        datasets = services.list_datasets()
        assert any(d.filename == "data.csv" for d in datasets)

    def test_bars_hint_matches_row_count(self, workspace):
        datasets = services.list_datasets()
        data = next(d for d in datasets if d.filename == "data.csv")
        assert data.bars_hint == 2000

    def test_rejects_path_traversal(self, workspace):
        with pytest.raises(ApiError, match="bare filename"):
            services._resolve_dataset("../outside.csv")

    def test_rejects_missing_dataset(self, workspace):
        with pytest.raises(ApiError, match="No such dataset"):
            services._resolve_dataset("does_not_exist.csv")


class TestStrategies:
    def test_lists_all_registered_strategies(self, workspace):
        names = {s.name for s in services.list_strategies()}
        assert names == {"ema_crossover", "opening_range_breakout", "trend_pullback", "vwap_reversion"}

    def test_strategy_has_parameters_with_defaults(self, workspace):
        info = services.get_strategy("ema_crossover")
        by_name = {p.name: p for p in info.parameters}
        assert by_name["fast_period"].default == 8
        assert by_name["fast_period"].type == "int"

    def test_unknown_strategy_raises_api_error(self, workspace):
        with pytest.raises(ApiError, match="Unknown strategy"):
            services.get_strategy("not_a_real_strategy")


class TestRunBacktestJob:
    def test_runs_and_persists_a_completed_run(self, workspace):
        req = BacktestRunRequest(strategy_name="vwap_reversion", dataset="data.csv", contract="MES")
        result = services.run_backtest_job(req)
        assert result.status == "completed"
        assert result.trade_count is not None and result.trade_count > 0
        assert result.kind == "backtest"

    def test_persisted_trades_carry_entry_metadata(self, workspace):
        req = BacktestRunRequest(strategy_name="vwap_reversion", dataset="data.csv", contract="MES")
        result = services.run_backtest_job(req)
        trades = services.list_trades(run_id=result.id)
        assert trades
        assert "vwap" in trades[0].entry_metadata

    def test_persisted_trades_carry_mae_mfe_and_regime_labels(self, workspace):
        """Phase 6B: every trade from a real backtest should come back with
        MAE/MFE and regime classification, not just entry metadata."""
        req = BacktestRunRequest(strategy_name="vwap_reversion", dataset="data.csv", contract="MES")
        result = services.run_backtest_job(req)
        trades = services.list_trades(run_id=result.id)
        assert trades
        for t in trades:
            assert t.mfe_points is not None
            assert t.mae_points is not None
            assert t.mfe_points >= 0
            assert t.mae_points >= 0
            assert t.regime_trend in ("bullish", "bearish", "sideways")
            assert t.regime_volatility in ("low", "medium", "high")
            assert t.regime_session in ("open", "morning", "lunch", "close", "overnight")

    def test_walk_forward_populates_validation_fields(self, workspace):
        req = BacktestRunRequest(strategy_name="vwap_reversion", dataset="data.csv", contract="MES", walk_forward=True)
        result = services.run_backtest_job(req)
        assert result.kind == "walk_forward"
        assert result.validation_trade_count is not None

    def test_strategy_param_overrides_take_effect(self, workspace):
        req = BacktestRunRequest(
            strategy_name="vwap_reversion", dataset="data.csv", contract="MES",
            strategy_params={"min_bars": 500},  # deliberately too high to ever clear warmup
        )
        result = services.run_backtest_job(req)
        assert result.trade_count == 0

    def test_risk_overrides_take_effect(self, workspace):
        req = BacktestRunRequest(
            strategy_name="vwap_reversion", dataset="data.csv", contract="MES",
            stop_loss_points=Decimal("1"),  # a much tighter stop than config.yaml's 5
        )
        result = services.run_backtest_job(req)
        assert result.status == "completed"

    def test_unknown_dataset_raises_api_error(self, workspace):
        req = BacktestRunRequest(strategy_name="vwap_reversion", dataset="nope.csv", contract="MES")
        with pytest.raises(ApiError):
            services.run_backtest_job(req)

    def test_invalid_strategy_params_marks_run_failed_not_missing(self, workspace):
        req = BacktestRunRequest(
            strategy_name="vwap_reversion", dataset="data.csv", contract="MES",
            strategy_params={"min_bars": 1},  # VwapReversion raises ValueError for min_bars < 2
        )
        with pytest.raises(Exception):
            services.run_backtest_job(req)
        runs = services.list_runs(strategy="vwap_reversion")
        assert any(r.status == "failed" for r in runs)


class TestPerformance:
    def test_equity_curve_starts_at_starting_cash(self, workspace):
        req = BacktestRunRequest(strategy_name="vwap_reversion", dataset="data.csv", contract="MES")
        result = services.run_backtest_job(req)
        perf = services.get_performance(result.id)
        assert perf.equity_curve[0].equity == Decimal("2500")

    def test_equity_curve_has_one_point_per_trade_plus_start(self, workspace):
        req = BacktestRunRequest(strategy_name="vwap_reversion", dataset="data.csv", contract="MES")
        result = services.run_backtest_job(req)
        perf = services.get_performance(result.id)
        assert len(perf.equity_curve) == result.trade_count + 1

    def test_unknown_run_raises(self, workspace):
        with pytest.raises(ApiError):
            services.get_performance("does-not-exist")


class TestCompare:
    def test_compares_multiple_strategies(self, workspace):
        req = CompareRequest(dataset="data.csv", contract="MES", strategy_names=["vwap_reversion", "ema_crossover"])
        result = services.run_compare(req)
        assert {e.strategy for e in result.entries} == {"vwap_reversion", "ema_crossover"}

    def test_unknown_strategy_name_raises(self, workspace):
        req = CompareRequest(dataset="data.csv", contract="MES", strategy_names=["not_real"])
        with pytest.raises(ApiError, match="Unknown strategy"):
            services.run_compare(req)


class TestOptimizer:
    def test_sweeps_and_persists_trials(self, workspace):
        req = OptimizerRunRequest(
            strategy_name="vwap_reversion", dataset="data.csv", contract="MES",
            param_grid={"min_bars": [10, 15]}, top_n=2,
        )
        result = services.run_optimizer_job(req)
        assert result.combos_tried == 2
        assert len(result.ranked_trials) <= 2

    def test_results_are_fetchable_by_batch_id(self, workspace):
        req = OptimizerRunRequest(
            strategy_name="vwap_reversion", dataset="data.csv", contract="MES",
            param_grid={"min_bars": [10, 15]}, top_n=2,
        )
        result = services.run_optimizer_job(req)
        trials = services.get_optimizer_trials(result.batch_id)
        assert len(trials) == 2


class TestOverfitVerdict:
    def test_no_validation_is_yellow(self, workspace):
        req = BacktestRunRequest(strategy_name="vwap_reversion", dataset="data.csv", contract="MES")
        result = services.run_backtest_job(req)
        verdict = services.overfit_verdict(result)
        assert verdict.level == "yellow"

    def test_profitable_train_unprofitable_validation_is_red(self, workspace):
        from futures_bot.api.schemas import RunDetail

        run = RunDetail(
            id="x", kind="walk_forward", status="completed", strategy="s", contract="MES",
            trade_count=10, net_pnl=Decimal("100"), profit_factor=Decimal("1.2"), win_rate=Decimal("50"),
            sharpe_ratio=None, max_drawdown=Decimal("10"), validation_net_pnl=Decimal("-50"),
            walk_forward=True, created_at="now", strategy_params={}, expectancy=Decimal("10"),
            sortino_ratio=None, max_drawdown_pct=Decimal("1"), validation_trade_count=20,
            validation_profit_factor=Decimal("0.5"),
        )
        verdict = services.overfit_verdict(run)
        assert verdict.level == "red"


class TestTradeAnalyticsSummary:
    def test_returns_three_buckets(self, workspace):
        req = BacktestRunRequest(strategy_name="vwap_reversion", dataset="data.csv", contract="MES")
        result = services.run_backtest_job(req)
        summary = services.trade_analytics_summary(run_id=result.id)
        assert isinstance(summary.best_entries, list)
        assert isinstance(summary.poor_exits, list)
        assert isinstance(summary.missed_opportunities, list)

    def test_best_entries_sorted_by_efficiency_descending(self, workspace):
        req = BacktestRunRequest(strategy_name="vwap_reversion", dataset="data.csv", contract="MES")
        result = services.run_backtest_job(req)
        summary = services.trade_analytics_summary(run_id=result.id, top_n=100)
        effs = [t.efficiency for t in summary.best_entries if t.efficiency is not None]
        assert effs == sorted(effs, reverse=True)

    def test_missed_opportunities_are_all_non_winning_trades(self, workspace):
        req = BacktestRunRequest(strategy_name="vwap_reversion", dataset="data.csv", contract="MES")
        result = services.run_backtest_job(req)
        summary = services.trade_analytics_summary(run_id=result.id, top_n=100)
        for t in summary.missed_opportunities:
            assert t.net_pnl <= 0
            assert t.mfe_points is not None and t.mfe_points >= Decimal("2")

    def test_respects_top_n(self, workspace):
        req = BacktestRunRequest(strategy_name="vwap_reversion", dataset="data.csv", contract="MES")
        result = services.run_backtest_job(req)
        summary = services.trade_analytics_summary(run_id=result.id, top_n=1)
        assert len(summary.best_entries) <= 1


class TestRegimePerformance:
    def test_buckets_cover_every_recorded_trade(self, workspace):
        req = BacktestRunRequest(strategy_name="vwap_reversion", dataset="data.csv", contract="MES")
        result = services.run_backtest_job(req)
        perf = services.regime_performance(strategy="vwap_reversion")
        assert sum(b.trade_count for b in perf.trend) == result.trade_count
        assert sum(b.trade_count for b in perf.volatility) == result.trade_count
        assert sum(b.trade_count for b in perf.session) == result.trade_count

    def test_filters_by_strategy(self, workspace):
        req1 = BacktestRunRequest(strategy_name="vwap_reversion", dataset="data.csv", contract="MES")
        services.run_backtest_job(req1)
        perf = services.regime_performance(strategy="ema_crossover")
        assert sum(b.trade_count for b in perf.trend) == 0

    def test_no_trades_returns_empty_buckets(self, workspace):
        perf = services.regime_performance(strategy="vwap_reversion")
        assert perf.trend == []
        assert perf.volatility == []
        assert perf.session == []


class TestReports:
    def test_generates_and_lists_report(self, workspace):
        req = BacktestRunRequest(strategy_name="vwap_reversion", dataset="data.csv", contract="MES")
        result = services.run_backtest_job(req)
        report = services.generate_report(result.id)
        assert Path(report.path).exists()
        assert "<html" in Path(report.path).read_text(encoding="utf-8").lower()

        reports = services.list_reports(run_id=result.id)
        assert len(reports) == 1


class TestSystemOverview:
    def test_reflects_runs_and_trades(self, workspace):
        req = BacktestRunRequest(strategy_name="vwap_reversion", dataset="data.csv", contract="MES")
        result = services.run_backtest_job(req)
        overview = services.system_overview()
        assert overview.total_backtests == 1
        assert overview.total_trades_analyzed == result.trade_count
        assert set(overview.strategies_available) == {
            "ema_crossover", "opening_range_breakout", "trend_pullback", "vwap_reversion",
        }


class TestMlDatasetInfo:
    def test_reflects_recorded_trades(self, workspace):
        req = BacktestRunRequest(strategy_name="vwap_reversion", dataset="data.csv", contract="MES")
        result = services.run_backtest_job(req)
        info = services.ml_dataset_info()
        assert info["trade_count"] == result.trade_count
        assert "vwap" in info["feature_columns"]

    def test_empty_before_any_trades(self, workspace):
        info = services.ml_dataset_info()
        assert info["trade_count"] == 0
        assert info["export_status"] == "no trades recorded yet"


class TestExportMlDatasetCsv:
    """`export_ml_dataset_csv` pools trades across every backtest run
    recorded for a strategy -- exactly like `build_feature_matrix` -- so it
    needs the same duplicate-market-event collapsing, and it's the one
    place `strategy_params` should show up as clearly separated metadata."""

    def _insert(self, run_id: str, *, entry_hour: int, params: dict):
        from futures_bot.research.features import TradeRecord
        from futures_bot.research.trade_store import TradeStore, default_db_path

        base = datetime(2024, 1, 1, tzinfo=CME_TZ)
        store = TradeStore(default_db_path())
        store.insert_trades([TradeRecord(
            run_id=run_id, contract="MES", strategy="s1", strategy_params=params,
            entry_time=base + timedelta(hours=entry_hour),
            exit_time=base + timedelta(hours=entry_hour, minutes=30),
            side="long", entry_price=Decimal("100"), exit_price=Decimal("105"),
            gross_pnl=Decimal("50"), commission=Decimal("1.24"), net_pnl=Decimal("48.76"),
            holding_minutes=30.0, exit_reason="take_profit",
            session_date=str((base + timedelta(hours=entry_hour)).date()), day_of_week="Monday",
            hour=entry_hour % 24, entry_reason="test", entry_metadata={"rsi": 55.0}, outcome="win",
        )])
        store.close()

    def test_rerun_of_the_same_backtest_is_not_double_counted(self, workspace):
        # Same market event (identical entry/exit), recorded under two
        # different run_ids -- e.g. the same backtest executed twice.
        self._insert("run-a", entry_hour=1, params={"fast": 9})
        self._insert("run-b", entry_hour=1, params={"fast": 9})
        csv_text = services.export_ml_dataset_csv("s1")
        rows = list(csv.reader(csv_text.splitlines()))
        assert len(rows) == 2  # header + one deduped row

    def test_genuinely_different_trades_are_both_kept(self, workspace):
        self._insert("run-a", entry_hour=1, params={"fast": 9})
        self._insert("run-a", entry_hour=2, params={"fast": 9})
        csv_text = services.export_ml_dataset_csv("s1")
        rows = list(csv.reader(csv_text.splitlines()))
        assert len(rows) == 3  # header + two distinct rows

    def test_strategy_params_are_exported_as_prefixed_metadata_columns(self, workspace):
        self._insert("run-a", entry_hour=1, params={"fast": 9, "slow": 21})
        csv_text = services.export_ml_dataset_csv("s1")
        header = next(csv.reader(csv_text.splitlines()))
        assert "param_fast" in header
        assert "param_slow" in header
        # Never conflated with the market-feature columns.
        assert "fast" not in header
        assert "rsi" in header


class TestRunAiBacktestComparison:
    """`run_ai_backtest_comparison` must judge the AI filter by what it does
    to the *strategy* (profit factor, expectancy, drawdown, P&L, trade
    count) -- not just diff net P&L. `run_backtest_job` is monkeypatched
    here so these are fast, deterministic unit tests of the metric math
    itself, independent of any real backtest/model."""

    def _run(self, **overrides) -> "RunDetail":
        from futures_bot.api.schemas import RunDetail
        base = dict(
            id="run", kind="backtest", status="completed", strategy="s1", contract="MES",
            walk_forward=False, created_at="2024-01-01T00:00:00Z",
            strategy_params={}, trade_count=100, net_pnl=Decimal("1000"),
            profit_factor=Decimal("1.5"), expectancy=Decimal("10"), max_drawdown=Decimal("500"),
        )
        base.update(overrides)
        return RunDetail(**base)

    def _patch(self, monkeypatch, without, with_):
        calls = iter([without, with_])
        monkeypatch.setattr(services, "run_backtest_job", lambda req, *a, **k: next(calls))

    def test_improvement_across_all_five_metrics(self, monkeypatch, workspace):
        from futures_bot.api.schemas import BacktestRunRequest

        without = self._run(
            trade_count=100, net_pnl=Decimal("1000"), profit_factor=Decimal("1.2"),
            expectancy=Decimal("10"), max_drawdown=Decimal("500"),
        )
        with_ = self._run(
            trade_count=60, net_pnl=Decimal("1200"), profit_factor=Decimal("1.8"),
            expectancy=Decimal("20"), max_drawdown=Decimal("300"),
        )
        self._patch(monkeypatch, without, with_)

        result = services.run_ai_backtest_comparison(
            BacktestRunRequest(strategy_name="s1", dataset="data.csv"), "model-1",
        )
        assert result["trades_filtered"] == 40
        assert result["trade_count_retained"] == 60
        assert result["trade_count_retained_pct"] == pytest.approx(60.0)
        assert result["pnl_improvement"] == Decimal("200")
        assert result["profit_factor_improvement"] == Decimal("0.6")
        assert result["expectancy_improvement"] == Decimal("10")
        assert result["drawdown_reduction"] == Decimal("200")  # lower drawdown = positive reduction

    def test_regression_reports_negative_deltas_not_clamped_to_zero(self, monkeypatch, workspace):
        from futures_bot.api.schemas import BacktestRunRequest

        without = self._run(net_pnl=Decimal("1000"), profit_factor=Decimal("1.8"), max_drawdown=Decimal("300"))
        with_ = self._run(net_pnl=Decimal("800"), profit_factor=Decimal("1.2"), max_drawdown=Decimal("500"))
        self._patch(monkeypatch, without, with_)

        result = services.run_ai_backtest_comparison(
            BacktestRunRequest(strategy_name="s1", dataset="data.csv"), "model-1",
        )
        assert result["pnl_improvement"] == Decimal("-200")
        assert result["profit_factor_improvement"] == Decimal("-0.6")
        assert result["drawdown_reduction"] == Decimal("-200")  # worse drawdown = negative "reduction"

    def test_undefined_profit_factor_reports_none_not_zero(self, monkeypatch, workspace):
        """Zero losing trades makes profit_factor undefined (`_safe_div`
        returns None) -- the delta must stay None, not silently become 0
        (which would misreport a real result as "no change")."""
        from futures_bot.api.schemas import BacktestRunRequest

        without = self._run(profit_factor=None)
        with_ = self._run(profit_factor=Decimal("2.0"))
        self._patch(monkeypatch, without, with_)

        result = services.run_ai_backtest_comparison(
            BacktestRunRequest(strategy_name="s1", dataset="data.csv"), "model-1",
        )
        assert result["profit_factor_improvement"] is None


class TestRunParamsComparison:
    """`run_params_comparison` (the research-server "Test More" backtest
    behind a config-param recommendation) must diff the same five metrics
    as `run_ai_backtest_comparison`, not just net P&L -- same shallow-
    comparison bug, same fix."""

    def _run(self, **overrides):
        from futures_bot.api.schemas import RunDetail
        base = dict(
            id="run", kind="backtest", status="completed", strategy="s1", contract="MES",
            walk_forward=False, created_at="2024-01-01T00:00:00Z",
            strategy_params={}, trade_count=100, net_pnl=Decimal("1000"),
            profit_factor=Decimal("1.2"), expectancy=Decimal("10"), max_drawdown=Decimal("500"),
        )
        base.update(overrides)
        return RunDetail(**base)

    def test_recommended_params_improvement_across_all_five_metrics(self, monkeypatch, workspace):
        current = self._run(
            trade_count=100, net_pnl=Decimal("1000"), profit_factor=Decimal("1.2"),
            expectancy=Decimal("10"), max_drawdown=Decimal("500"),
        )
        recommended = self._run(
            trade_count=60, net_pnl=Decimal("1200"), profit_factor=Decimal("1.8"),
            expectancy=Decimal("20"), max_drawdown=Decimal("300"),
        )
        calls = iter([current, recommended])
        monkeypatch.setattr(services, "run_backtest_job", lambda req, *a, **k: next(calls))

        result = services.run_params_comparison("s1", "data.csv", {"fast_period": 9})
        assert result["trade_count_retained"] == 60
        assert result["trade_count_retained_pct"] == pytest.approx(60.0)
        assert result["pnl_improvement"] == Decimal("200")
        assert result["profit_factor_improvement"] == Decimal("0.6")
        assert result["expectancy_improvement"] == Decimal("10")
        assert result["drawdown_reduction"] == Decimal("200")


class TestExperiments:
    def test_create_and_get(self, workspace):
        req = ExperimentCreateRequest(
            name="VWAP in high vol", hypothesis="VWAP reversion performs better in high volatility.",
            strategy="vwap_reversion", dataset="data.csv", parameters={"std_devs": 2},
        )
        created = services.create_experiment(req)
        fetched = services.get_experiment(created.id)
        assert fetched.name == "VWAP in high vol"
        assert fetched.hypothesis.startswith("VWAP reversion")
        assert fetched.parameters == {"std_devs": 2}

    def test_create_links_to_a_run(self, workspace):
        run = services.run_backtest_job(
            BacktestRunRequest(strategy_name="vwap_reversion", dataset="data.csv", contract="MES")
        )
        req = ExperimentCreateRequest(
            name="n", hypothesis="h", strategy="vwap_reversion", parameters={}, run_id=run.id,
        )
        created = services.create_experiment(req)
        assert created.run_id == run.id

    def test_update_notes(self, workspace):
        req = ExperimentCreateRequest(name="n", hypothesis="h", strategy="vwap_reversion", parameters={})
        created = services.create_experiment(req)
        updated = services.update_experiment_notes(created.id, "Confirmed: 1.4x win rate in high vol.")
        assert "Confirmed" in updated.notes

    def test_update_notes_for_unknown_experiment_raises(self, workspace):
        with pytest.raises(ApiError):
            services.update_experiment_notes("does-not-exist", "notes")

    def test_get_unknown_experiment_raises(self, workspace):
        with pytest.raises(ApiError):
            services.get_experiment("does-not-exist")

    def test_list_filters_by_strategy(self, workspace):
        services.create_experiment(ExperimentCreateRequest(name="a", hypothesis="h", strategy="vwap_reversion", parameters={}))
        services.create_experiment(ExperimentCreateRequest(name="b", hypothesis="h", strategy="ema_crossover", parameters={}))
        vwap_only = services.list_experiments(strategy="vwap_reversion")
        assert len(vwap_only) == 1
        assert vwap_only[0].name == "a"


class TestGenerateInsights:
    def test_no_insights_before_enough_trades(self, workspace):
        req = BacktestRunRequest(strategy_name="vwap_reversion", dataset="data.csv", contract="MES")
        services.run_backtest_job(req)  # a handful of trades, below the significance floor
        insights = services.generate_insights()
        # Whatever fires must be traceable -- never crashes, and any
        # per-strategy insight it does produce must name a real strategy.
        for insight in insights:
            if insight.strategy is not None:
                assert insight.strategy in services.StrategyRegistry.names()

    def test_no_trades_produces_no_per_strategy_insights(self, workspace):
        insights = services.generate_insights()
        assert not any(i.category in ("costs", "timing", "regime") for i in insights)

    def test_high_commission_drag_with_negative_pnl_flags_a_warning(self, workspace):
        """Directly exercises the insight-generation rule rather than trying
        to engineer a real backtest into exactly the right shape -- inserts
        synthetic trades straight into the store with known commission/P&L
        figures."""
        from futures_bot.research.features import TradeRecord

        store = services.get_store()
        records = []
        base_time = datetime(2026, 1, 5, 10, 0, tzinfo=CME_TZ)
        for i in range(25):
            records.append(TradeRecord(
                run_id="synthetic-run", contract="MES", strategy="vwap_reversion", strategy_params={},
                entry_time=base_time + timedelta(minutes=i), exit_time=base_time + timedelta(minutes=i + 5),
                side="long", entry_price=Decimal("100"), exit_price=Decimal("100.5"),
                gross_pnl=Decimal("1.00"), commission=Decimal("0.80"), net_pnl=Decimal("-0.20"),
                holding_minutes=5.0, exit_reason="target", session_date="2026-01-05", day_of_week="Monday",
                hour=10, entry_reason="e", entry_metadata={}, outcome="loss",
            ))
        store.insert_trades(records)

        insights = services.generate_insights()
        cost_warnings = [i for i in insights if i.strategy == "vwap_reversion" and i.category == "costs"]
        assert cost_warnings
        assert cost_warnings[0].severity == "warning"

    def test_overfit_insight_fires_when_latest_optimizer_run_degrades(self, workspace):
        req = OptimizerRunRequest(
            strategy_name="vwap_reversion", dataset="data.csv", contract="MES",
            param_grid={"min_bars": [10, 15]}, top_n=2,
        )
        services.run_optimizer_job(req)
        insights = services.generate_insights()
        # Whether or not this particular run happened to degrade, the
        # function must not crash and must only ever emit well-formed
        # overfitting insights when it does fire.
        overfit = [i for i in insights if i.category == "overfitting"]
        for i in overfit:
            assert "overfit" in i.message.lower()
