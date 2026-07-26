"""Model artifact persistence and single-row prediction.

`score()` is the one scoring path shared by the Prediction Sandbox and the
Backtest+AI / live-deployment signal filter (`engine.py`'s `signal_filter`
hook) -- both call this, never a second, ad hoc re-implementation of "turn a
raw feature dict into a probability."
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .dataset import encode_feature_row

ARTIFACT_ROOT = Path("ml_models")


def artifact_dir(model_id: str) -> Path:
    return ARTIFACT_ROOT / model_id


def save_artifacts(
    model_id: str, model_type: str, preprocessor: dict, fitted_model, feature_columns: list,
    numeric_raw_keys: list, dummy_source: dict, train_context: dict,
) -> str:
    """Persists everything `load_model`/`predict_one` need later:
    - `prep.joblib`: the train-fold-fit imputer/scaler plus the raw-dict ->
      column-space encoding info (`numeric_raw_keys`/`dummy_source`), so a
      live `Signal.metadata` dict can be scored without pandas or the
      original training data.
    - `model.*`: the fitted estimator, in whichever format that model type
      round-trips most reliably (xgboost's own binary format, torch's
      `state_dict`, joblib for everything else).
    - `train_context.joblib`: the *scaled* training feature matrix + its
      outcomes (small -- a few hundred rows at this project's data volume),
      so "similar historical trades" can be computed at predict time without
      re-hitting the database.
    """
    import joblib

    directory = artifact_dir(model_id)
    directory.mkdir(parents=True, exist_ok=True)

    joblib.dump(
        {
            "imputer": preprocessor["imputer"], "scaler": preprocessor["scaler"],
            "numeric_raw_keys": numeric_raw_keys, "dummy_source": dummy_source,
        },
        directory / "prep.joblib",
    )
    joblib.dump(train_context, directory / "train_context.joblib")

    if model_type == "xgboost":
        fitted_model.save_model(str(directory / "model.json"))
    elif model_type == "neural_network":
        import torch
        torch.save(fitted_model.state_dict(), directory / "model.pt")
    else:
        joblib.dump(fitted_model, directory / "model.joblib")

    (directory / "feature_columns.json").write_text(json.dumps(feature_columns))
    return str(directory)


@dataclass
class LoadedModel:
    model_id: str
    model_type: str
    feature_columns: list
    numeric_raw_keys: list
    dummy_source: dict
    imputer: object
    scaler: object
    predict_fn: Callable
    train_context: dict


def load_model(model_row: dict) -> LoadedModel:
    """Reconstructs the exact fitted estimator + preprocessing this model
    was trained with. The one path every predict/deploy/comparison call
    goes through -- verified by a save->load->predict round-trip test."""
    import joblib

    directory = Path(model_row["artifact_path"])
    prep = joblib.load(directory / "prep.joblib")
    train_context = joblib.load(directory / "train_context.joblib")
    feature_columns = model_row["feature_columns"]
    model_type = model_row["model_type"]

    if model_type == "xgboost":
        import xgboost as xgb

        booster = xgb.Booster()
        booster.load_model(str(directory / "model.json"))

        def predict_fn(arr):
            return booster.predict(xgb.DMatrix(arr))

    elif model_type == "neural_network":
        import torch

        from .neural_net import build_mlp

        hidden_sizes = tuple(model_row["hyperparameters"].get("hidden_sizes", (32, 16)))
        model = build_mlp(len(feature_columns), hidden_sizes)
        model.load_state_dict(torch.load(directory / "model.pt"))
        model.eval()

        def predict_fn(arr):
            with torch.no_grad():
                return model(torch.tensor(arr, dtype=torch.float32)).squeeze(1).numpy()

    else:
        fitted = joblib.load(directory / "model.joblib")

        def predict_fn(arr):
            return fitted.predict_proba(arr)[:, 1]

    return LoadedModel(
        model_id=model_row["id"], model_type=model_type, feature_columns=feature_columns,
        numeric_raw_keys=prep["numeric_raw_keys"], dummy_source=prep["dummy_source"],
        imputer=prep["imputer"], scaler=prep["scaler"], predict_fn=predict_fn, train_context=train_context,
    )


def score(loaded: LoadedModel, feature_row: dict) -> float:
    """Raw win-probability for one raw feature dict (a live `Signal.metadata`
    or a stored trade's `entry_metadata` + regime labels)."""
    raw = encode_feature_row(feature_row, loaded.numeric_raw_keys, loaded.dummy_source, loaded.feature_columns)
    scaled = loaded.scaler.transform(loaded.imputer.transform(raw))
    return float(loaded.predict_fn(scaled)[0])


def _calibration_bucket(model_row: dict, probability: float) -> Optional[dict]:
    diagnostics = (model_row.get("metrics") or {}).get("diagnostics") or {}
    curve = diagnostics.get("calibration_curve")
    if not curve or not curve.get("predicted"):
        return None
    predicted = curve["predicted"]
    actual = curve["actual"]
    nearest = min(range(len(predicted)), key=lambda i: abs(predicted[i] - probability))
    return {"predicted": predicted[nearest], "actual_win_rate": actual[nearest]}


def _similar_trades(loaded: LoadedModel, scaled_row) -> tuple:
    from sklearn.neighbors import NearestNeighbors

    train_X = loaded.train_context.get("X")
    train_y = loaded.train_context.get("y")
    if train_X is None or len(train_X) == 0:
        return 0, None
    k = min(20, len(train_X))
    neighbors = NearestNeighbors(n_neighbors=k).fit(train_X)
    _, indices = neighbors.kneighbors(scaled_row)
    neighbor_outcomes = [train_y[i] for i in indices[0]]
    win_rate = sum(neighbor_outcomes) / len(neighbor_outcomes) if neighbor_outcomes else None
    return len(neighbor_outcomes), win_rate


def _top_reasons(feature_row: dict, loaded: LoadedModel, importances: dict, top_n: int = 5) -> list:
    """Approximate, importance-weighted feature contributions -- not exact
    SHAP (no new heavy explainability dependency this phase): the features
    with the largest |importance * (value - training_mean)|."""
    train_X = loaded.train_context.get("X")
    means = loaded.train_context.get("means") or {}
    raw = encode_feature_row(feature_row, loaded.numeric_raw_keys, loaded.dummy_source, loaded.feature_columns)[0]

    scored = []
    for i, col in enumerate(loaded.feature_columns):
        value = raw[i]
        mean = means.get(col, 0.0)
        importance = importances.get(col, 0.0)
        if value != value:  # NaN
            continue
        scored.append({"feature": col, "value": float(value), "contribution": float(importance * abs(value - mean))})
    scored.sort(key=lambda r: r["contribution"], reverse=True)
    return scored[:top_n]


def predict_one(model_row: dict, feature_row: dict) -> dict:
    loaded = load_model(model_row)
    raw = encode_feature_row(feature_row, loaded.numeric_raw_keys, loaded.dummy_source, loaded.feature_columns)
    scaled = loaded.scaler.transform(loaded.imputer.transform(raw))
    probability = float(loaded.predict_fn(scaled)[0])
    confidence = max(probability, 1 - probability)

    r_baseline = (model_row.get("metrics") or {}).get("r_multiple_baseline") or {}
    avg_win_r, avg_loss_r = r_baseline.get("avg_win_r"), r_baseline.get("avg_loss_r")
    expected_value_r = (
        probability * avg_win_r - (1 - probability) * avg_loss_r
        if avg_win_r is not None and avg_loss_r is not None else None
    )

    similar_trade_count, similar_win_rate = _similar_trades(loaded, scaled)
    calibration_bucket = _calibration_bucket(model_row, probability)
    importances = {row["feature"]: row["importance"] for row in (model_row.get("feature_importance") or [])}
    top_reasons = _top_reasons(feature_row, loaded, importances)

    return {
        "model_id": model_row["id"],
        "probability": probability,
        "confidence": confidence,
        "expected_win_probability": probability,
        "expected_value_r": expected_value_r,
        "similar_trade_count": similar_trade_count,
        "similar_trade_win_rate": similar_win_rate,
        "calibration_bucket": calibration_bucket,
        "top_reasons": top_reasons,
    }


def build_train_context(X_train_scaled, y_train, feature_columns: list, X_train_raw) -> dict:
    """Bundled alongside the model artifact: the scaled training matrix (for
    similar-trades k-NN) and per-column training means (for the prediction
    sandbox's "top reasons" contribution weighting)."""
    means = {col: float(X_train_raw[col].mean()) for col in feature_columns}
    return {"X": X_train_scaled, "y": list(y_train), "means": means}
