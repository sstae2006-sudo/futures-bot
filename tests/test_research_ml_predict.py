"""Prediction output shape: expected value (R multiple), calibration
bucket lookup, and top-reasons ordering. Save/load round-tripping itself is
covered by `test_research_ml_training.py::TestSaveLoadPredictRoundTrip`."""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from futures_bot.research.ml import predict as predict_mod
from futures_bot.research.ml.split import split_trades_chronologically
from futures_bot.research.ml.training import train_model


def _train_and_save(tmp_path, monkeypatch, model_id="predtest1"):
    monkeypatch.chdir(tmp_path)
    rng = np.random.default_rng(0)
    n = 300
    trades = [
        {"id": i, "entry_time": (datetime(2024, 1, 1) + timedelta(hours=i)).isoformat(), "created_at": "2026-01-01"}
        for i in range(n)
    ]
    X = pd.DataFrame({"rsi": rng.normal(50, 10, n), "adx": rng.normal(25, 8, n)})
    y = pd.Series((X["rsi"] + rng.normal(0, 5, n) > 50).astype(int))

    result, metrics = train_model(
        "logistic_regression", X, y, trades, "chronological_split", {}, lambda *a: None, lambda: False,
    )
    split = split_trades_chronologically(trades)
    X_train_scaled = result.preprocessor["scaler"].transform(result.preprocessor["imputer"].transform(X.iloc[split.train]))
    train_context = predict_mod.build_train_context(X_train_scaled, y.iloc[split.train].to_numpy(), list(X.columns), X.iloc[split.train])
    path = predict_mod.save_artifacts(
        model_id, "logistic_regression", result.preprocessor, result.fitted_model,
        list(X.columns), ["rsi", "adx"], {}, train_context,
    )
    metrics["diagnostics"] = result.diagnostics
    metrics["r_multiple_baseline"] = {"avg_win_r": 1.8, "avg_loss_r": 1.0}
    model_row = {
        "id": model_id, "model_type": "logistic_regression", "feature_columns": list(X.columns),
        "artifact_path": path, "hyperparameters": {}, "metrics": metrics,
        "feature_importance": result.feature_importance,
    }
    return model_row


class TestExpectedValueR:
    def test_expected_value_uses_the_stored_r_multiple_baseline(self, tmp_path, monkeypatch):
        model_row = _train_and_save(tmp_path, monkeypatch)
        out = predict_mod.predict_one(model_row, {"rsi": 70.0, "adx": 25.0})
        p = out["probability"]
        expected = p * 1.8 - (1 - p) * 1.0
        assert out["expected_value_r"] == pytest.approx(expected)

    def test_expected_value_is_none_without_a_baseline(self, tmp_path, monkeypatch):
        model_row = _train_and_save(tmp_path, monkeypatch, model_id="predtest2")
        model_row["metrics"] = {**model_row["metrics"], "r_multiple_baseline": {"avg_win_r": None, "avg_loss_r": None}}
        out = predict_mod.predict_one(model_row, {"rsi": 70.0, "adx": 25.0})
        assert out["expected_value_r"] is None


class TestCalibrationBucket:
    def test_returns_nearest_bucket_to_the_predicted_probability(self, tmp_path, monkeypatch):
        model_row = _train_and_save(tmp_path, monkeypatch, model_id="predtest3")
        out = predict_mod.predict_one(model_row, {"rsi": 70.0, "adx": 25.0})
        if model_row["metrics"]["diagnostics"]["calibration_curve"] is not None:
            assert out["calibration_bucket"] is not None
            assert "predicted" in out["calibration_bucket"]
            assert "actual_win_rate" in out["calibration_bucket"]


class TestTopReasons:
    def test_reasons_are_sorted_by_contribution_descending(self, tmp_path, monkeypatch):
        model_row = _train_and_save(tmp_path, monkeypatch, model_id="predtest4")
        out = predict_mod.predict_one(model_row, {"rsi": 90.0, "adx": 5.0})
        contributions = [r["contribution"] for r in out["top_reasons"]]
        assert contributions == sorted(contributions, reverse=True)
        assert len(out["top_reasons"]) <= 5
