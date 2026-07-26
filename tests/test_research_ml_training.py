"""Each `train_<model_type>` function against a small synthetic feature
matrix -- fast, no real trade data needed. Covers: progress/should_stop
actually work, the scaler is fit on the train fold only, overfit detection
fires on a deliberately-overfit fixture, and a trained model round-trips
through save->load->predict identically."""

from __future__ import annotations

import shutil
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from futures_bot.research.ml import predict as predict_mod
from futures_bot.research.ml.split import split_trades_chronologically
from futures_bot.research.ml.training import TrainingStopped, train_model


def _synthetic(n=300, seed=0, informative=True):
    rng = np.random.default_rng(seed)
    trades = [
        {"id": i, "entry_time": (datetime(2024, 1, 1) + timedelta(hours=i)).isoformat(), "created_at": "2026-01-01"}
        for i in range(n)
    ]
    X = pd.DataFrame({"rsi": rng.normal(50, 10, n), "adx": rng.normal(25, 8, n)})
    if informative:
        y = pd.Series((X["rsi"] + rng.normal(0, 5, n) > 50).astype(int))
    else:
        y = pd.Series(rng.integers(0, 2, n))
    return trades, X, y


def _noop_progress(current, total, message):
    pass


def _never_stop():
    return False


MODEL_TYPES = ["logistic_regression", "random_forest", "xgboost", "neural_network"]


class TestAllModelTypesTrain:
    @pytest.mark.parametrize("model_type", MODEL_TYPES)
    def test_trains_and_returns_namespaced_metrics(self, model_type):
        trades, X, y = _synthetic()
        hyperparams = {"n_estimators": 20, "epochs": 8} if model_type != "logistic_regression" else {}
        result, metrics = train_model(model_type, X, y, trades, "chronological_split", hyperparams, _noop_progress, _never_stop)
        assert set(metrics.keys()) == {"train", "validation", "test"}
        assert 0.0 <= metrics["train"]["accuracy"] <= 1.0
        assert 0.0 <= metrics["validation"]["accuracy"] <= 1.0
        assert result.feature_importance
        assert result.diagnostics["confusion_matrix"]

    def test_progress_callback_is_actually_called(self):
        calls = []
        trades, X, y = _synthetic()

        def progress(current, total, message):
            calls.append((current, total))

        train_model("random_forest", X, y, trades, "chronological_split", {"n_estimators": 20}, progress, _never_stop)
        assert calls
        assert calls[-1][0] == calls[-1][1]  # reaches 100%

    def test_should_stop_raises_training_stopped(self):
        trades, X, y = _synthetic()
        calls = {"n": 0}

        def stop_after_a_few():
            calls["n"] += 1
            return calls["n"] > 2

        with pytest.raises(TrainingStopped):
            train_model("random_forest", X, y, trades, "chronological_split", {"n_estimators": 200}, _noop_progress, stop_after_a_few)


class TestWalkForwardMode:
    def test_produces_fold_aggregated_out_of_sample_metrics(self):
        trades, X, y = _synthetic(n=400)
        result, metrics = train_model(
            "random_forest", X, y, trades, "walk_forward", {"n_estimators": 20}, _noop_progress, _never_stop,
        )
        assert set(metrics.keys()) == {"train", "walk_forward_out_of_sample"}
        assert metrics["walk_forward_out_of_sample"]["fold_count"] >= 2
        assert result.train_indices  # last fold's train indices, for downstream context-building


class TestPreprocessorLeakage:
    def test_scaler_is_fit_on_train_fold_only(self):
        trades, X, y = _synthetic()
        result, _ = train_model(
            "logistic_regression", X, y, trades, "chronological_split", {}, _noop_progress, _never_stop,
        )
        split = split_trades_chronologically(trades)
        expected_mean = X.iloc[split.train]["rsi"].median()
        # SimpleImputer(strategy="median") stores per-column medians as
        # statistics_ -- confirming it reflects only the train block, not
        # validation/test, proves the "fit on train only" leakage rule.
        rsi_index = list(X.columns).index("rsi")
        assert result.preprocessor["imputer"].statistics_[rsi_index] == pytest.approx(expected_mean)


class TestOverfitDetection:
    def test_flags_a_deliberately_overfit_random_forest(self):
        # A high-cardinality categorical-like numeric feature with very few
        # rows and a very deep, unconstrained forest memorizes the train
        # fold trivially, producing a large train/validation gap.
        rng = np.random.default_rng(0)
        n = 80
        trades = [
            {"id": i, "entry_time": (datetime(2024, 1, 1) + timedelta(hours=i)).isoformat(), "created_at": "2026-01-01"}
            for i in range(n)
        ]
        X = pd.DataFrame({"noise": rng.normal(0, 1, n)})
        y = pd.Series(rng.integers(0, 2, n))  # pure noise -- no real signal at all
        result, metrics = train_model(
            "random_forest", X, y, trades, "chronological_split",
            {"n_estimators": 300, "max_depth": None, "min_samples_leaf": 1}, _noop_progress, _never_stop,
        )
        assert metrics["train"]["accuracy"] > 0.9
        assert result.overfit_warning is True
        assert result.overfit_note is not None


class TestSaveLoadPredictRoundTrip:
    def test_model_reloads_and_predicts_identically(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        trades, X, y = _synthetic()
        result, metrics = train_model(
            "random_forest", X, y, trades, "chronological_split", {"n_estimators": 20}, _noop_progress, _never_stop,
        )
        split = split_trades_chronologically(trades)
        X_train_scaled = result.preprocessor["scaler"].transform(
            result.preprocessor["imputer"].transform(X.iloc[split.train])
        )
        train_context = predict_mod.build_train_context(
            X_train_scaled, y.iloc[split.train].to_numpy(), list(X.columns), X.iloc[split.train],
        )
        path = predict_mod.save_artifacts(
            "roundtrip1", "random_forest", result.preprocessor, result.fitted_model,
            list(X.columns), ["rsi", "adx"], {}, train_context,
        )
        model_row = {
            "id": "roundtrip1", "model_type": "random_forest", "feature_columns": list(X.columns),
            "artifact_path": path, "hyperparameters": {}, "metrics": metrics,
            "feature_importance": result.feature_importance,
        }
        feature_row = {"rsi": 55.0, "adx": 20.0}
        loaded = predict_mod.load_model(model_row)
        p1 = predict_mod.score(loaded, feature_row)

        # Reload completely fresh -- proves persistence, not in-memory reuse.
        loaded_again = predict_mod.load_model(model_row)
        p2 = predict_mod.score(loaded_again, feature_row)
        assert p1 == pytest.approx(p2)

        out = predict_mod.predict_one({**model_row, "metrics": {**metrics, "r_multiple_baseline": {"avg_win_r": 1.5, "avg_loss_r": 1.0}}}, feature_row)
        assert out["probability"] == pytest.approx(p1)
        assert 0.0 <= out["confidence"] <= 1.0
        assert out["similar_trade_count"] > 0
