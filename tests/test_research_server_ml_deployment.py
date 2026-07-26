"""Phase 9: `AutonomousPaperTrader` picks up a strategy's currently
deployed model as a live `signal_filter`, and a rollback takes effect the
next time that strategy's engine is (re)built -- reusing the exact same
filter-building shape the Backtest+AI comparison uses, just sourced from
`model_deployments` instead of a request body."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from futures_bot.config import load_settings
from futures_bot.contracts import CME_TZ
from futures_bot.research.trade_store import TradeStore, default_db_path
from futures_bot.research_server.paper_trader import AutonomousPaperTrader

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

session:
  start_ct: "00:00"
  end_ct: "23:59"
  flatten_before_close_minutes: 0
  trade_on_weekends: true

broker:
  name: paper
  slippage_ticks: 1
  commission_per_side: 0.62
  starting_cash: 2500

logging:
  level: WARNING
  directory: {log_dir}
  log_every_decision: false

strategy_name: ema_crossover
strategy_params:
  fast_period: 3
  slow_period: 5
  trend_period: 5
  min_ema_distance: 0.01

research_server:
  enabled: true
  paper_strategies: [ema_crossover, vwap_reversion]
  data_sync_products: [MES]
  resolution: 5min
  poll_seconds: 1

state_file: {state_file}
"""


def write_config(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        CONFIG_YAML.format(
            log_dir=(tmp_path / "logs").as_posix(), state_file=(tmp_path / "state" / "bot_state.json").as_posix(),
        ),
        encoding="utf-8",
    )
    return load_settings(config_path)


class FakeMassiveBarFeed:
    instances: list = []

    def __init__(self, symbol, api_key, resolution="5min"):
        self.symbol = symbol
        self._queued: list = []
        FakeMassiveBarFeed.instances.append(self)

    def poll_new_bars(self):
        return []


class FakeContractsResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class FakeContractsSession:
    def get(self, url, params=None, timeout=None):
        return FakeContractsResponse({
            "status": "OK",
            "results": [{
                "active": True, "date": (params or {}).get("date", "2026-07-22"), "name": "MESU6 Future",
                "product_code": "MES", "ticker": "MESU6", "type": "single",
                "first_trade_date": "2025-06-20", "last_trade_date": "2026-09-18",
            }],
        })


@pytest.fixture(autouse=True)
def _patch_feed(monkeypatch):
    FakeMassiveBarFeed.instances = []
    monkeypatch.setattr("futures_bot.feeds.massive.MassiveBarFeed", FakeMassiveBarFeed)
    monkeypatch.setenv("FUTURES_BOT_MARKET_DATA_DB", "will-be-overridden")


def _train_and_persist_model(strategy: str, store: TradeStore, tmp_path) -> str:
    """Trains a tiny real model and inserts it as `finished`, bypassing the
    job system (which this test doesn't need) -- exercises the same
    `research.ml` training/persistence code every other test does."""
    from futures_bot.research.ml import predict as predict_mod
    from futures_bot.research.ml.split import split_trades_chronologically
    from futures_bot.research.ml.training import train_model

    rng = np.random.default_rng(0)
    n = 150
    trades = [
        {"id": i, "entry_time": (datetime(2024, 1, 1) + timedelta(hours=i)).isoformat(), "created_at": "2026-01-01"}
        for i in range(n)
    ]
    X = pd.DataFrame({"rsi": rng.normal(50, 10, n)})
    y = pd.Series((X["rsi"] + rng.normal(0, 5, n) > 50).astype(int))

    result, metrics = train_model(
        "logistic_regression", X, y, trades, "chronological_split", {}, lambda *a: None, lambda: False,
    )
    split = split_trades_chronologically(trades)
    X_train_scaled = result.preprocessor["scaler"].transform(result.preprocessor["imputer"].transform(X.iloc[split.train]))
    train_context = predict_mod.build_train_context(X_train_scaled, y.iloc[split.train].to_numpy(), list(X.columns), X.iloc[split.train])

    model_id = uuid.uuid4().hex[:12]
    path = predict_mod.save_artifacts(
        model_id, "logistic_regression", result.preprocessor, result.fitted_model,
        list(X.columns), ["rsi"], {}, train_context,
    )
    store.insert_model(
        model_id=model_id, strategy=strategy, model_type="logistic_regression",
        feature_columns=list(X.columns), hyperparameters={}, evaluation_mode="chronological_split",
        dataset_size=n, dataset_version="v1",
    )
    metrics["diagnostics"] = result.diagnostics
    metrics["r_multiple_baseline"] = {"avg_win_r": 1.5, "avg_loss_r": 1.0}
    store.complete_model(
        model_id, metrics=metrics, feature_importance=result.feature_importance,
        artifact_path=path, app_version="0.0.0-test", git_commit=None,
        overfit_warning=result.overfit_warning, overfit_note=result.overfit_note,
    )
    return model_id


class TestDeployedModelWiring:
    def test_no_deployment_means_no_filter(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("FUTURES_BOT_MARKET_DATA_DB", str(tmp_path / "market_data.db"))
        monkeypatch.setenv("FUTURES_BOT_RESEARCH_DB", str(tmp_path / "research.db"))
        settings = write_config(tmp_path)
        trader = AutonomousPaperTrader()

        trader.start(settings, "test-key", session=FakeContractsSession())
        try:
            assert trader._runtimes["ema_crossover"].engine.signal_filter is None
            assert trader._runtimes["vwap_reversion"].engine.signal_filter is None
        finally:
            trader.stop()

    def test_deployed_finished_model_becomes_the_engines_filter(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("FUTURES_BOT_MARKET_DATA_DB", str(tmp_path / "market_data.db"))
        monkeypatch.setenv("FUTURES_BOT_RESEARCH_DB", str(tmp_path / "research.db"))
        settings = write_config(tmp_path)

        store = TradeStore(default_db_path())
        model_id = _train_and_persist_model("ema_crossover", store, tmp_path)
        store.insert_deployment(deployment_id="d1", strategy="ema_crossover", model_id=model_id, action="deploy")
        store.close()

        trader = AutonomousPaperTrader()
        trader.start(settings, "test-key", session=FakeContractsSession())
        try:
            assert trader._runtimes["ema_crossover"].engine.signal_filter is not None
            # The other strategy has no deployment -- unaffected.
            assert trader._runtimes["vwap_reversion"].engine.signal_filter is None
        finally:
            trader.stop()

    def test_rollback_takes_effect_on_next_start(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("FUTURES_BOT_MARKET_DATA_DB", str(tmp_path / "market_data.db"))
        monkeypatch.setenv("FUTURES_BOT_RESEARCH_DB", str(tmp_path / "research.db"))
        settings = write_config(tmp_path)

        store = TradeStore(default_db_path())
        model_id = _train_and_persist_model("ema_crossover", store, tmp_path)
        store.insert_deployment(deployment_id="d1", strategy="ema_crossover", model_id=model_id, action="deploy")
        store.insert_deployment(deployment_id="d2", strategy="ema_crossover", model_id=None, action="undeploy")
        store.close()

        trader = AutonomousPaperTrader()
        trader.start(settings, "test-key", session=FakeContractsSession())
        try:
            assert trader._runtimes["ema_crossover"].engine.signal_filter is None
        finally:
            trader.stop()

    def test_unfinished_model_deployment_is_ignored_not_a_crash(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("FUTURES_BOT_MARKET_DATA_DB", str(tmp_path / "market_data.db"))
        monkeypatch.setenv("FUTURES_BOT_RESEARCH_DB", str(tmp_path / "research.db"))
        settings = write_config(tmp_path)

        store = TradeStore(default_db_path())
        model_id = uuid.uuid4().hex[:12]
        store.insert_model(
            model_id=model_id, strategy="ema_crossover", model_type="logistic_regression",
            feature_columns=["rsi"], hyperparameters={}, evaluation_mode="chronological_split",
            dataset_size=10, dataset_version="v1",
        )  # left at status='queued' -- never completed
        store.insert_deployment(deployment_id="d1", strategy="ema_crossover", model_id=model_id, action="deploy")
        store.close()

        trader = AutonomousPaperTrader()
        status = trader.start(settings, "test-key", session=FakeContractsSession())
        try:
            assert status["running"] is True
            assert trader._runtimes["ema_crossover"].engine.signal_filter is None
        finally:
            trader.stop()
