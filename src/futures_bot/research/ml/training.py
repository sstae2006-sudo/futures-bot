"""Model training: logistic regression, random forest, xgboost, and a small
PyTorch neural network -- one `train_<model_type>` function each, all
sharing the same evaluation discipline:

* chronological split only (`split.py`), never shuffled
* a `SimpleImputer`/`StandardScaler` pair fit on the train fold only, then
  applied (never re-fit) to validation/test/walk-forward-test folds --
  the leakage rule lives in one place (`_fit_preprocessor`), not four
* feature importance via `sklearn.inspection.permutation_importance` on the
  validation split (never test), computed identically regardless of model
  type
* automatic overfitting detection (train-vs-validation metric gap)
* confusion matrix / ROC / PR / calibration diagnostics from one
  validation-set prediction pass

`train_model`/`train_model_walk_forward` are the two entry points
`api/services.py` calls; everything else here is an implementation detail.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

from .split import ChronologicalSplit, split_trades_chronologically, walk_forward_folds

ProgressFn = Callable[[int, int, str], None]
ShouldStopFn = Callable[[], bool]

#: Train-vs-validation gap (accuracy, or ROC AUC when both classes are
#: present) above this triggers the overfit warning.
OVERFIT_GAP_THRESHOLD = 0.15


class TrainingStopped(Exception):
    """Raised when `should_stop()` fires mid-training -- caught by the
    job-submission wrapper in `api/services.py`, which marks the model row
    `status='stopped'` rather than `'failed'`."""


@dataclass
class ClassificationMetrics:
    accuracy: float
    precision: float
    recall: float
    f1: float
    roc_auc: Optional[float]
    trade_count: int

    def as_dict(self) -> dict:
        return {
            "accuracy": self.accuracy, "precision": self.precision, "recall": self.recall,
            "f1": self.f1, "roc_auc": self.roc_auc, "trade_count": self.trade_count,
        }


def _binary_metrics(y_true, y_prob, threshold: float = 0.5) -> ClassificationMetrics:
    from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score, roc_auc_score
    import numpy as np

    y_true = np.asarray(y_true)
    y_prob = np.asarray(y_prob)
    y_pred = (y_prob >= threshold).astype(int)
    roc_auc = None
    if len(set(y_true.tolist())) > 1:
        try:
            roc_auc = float(roc_auc_score(y_true, y_prob))
        except ValueError:
            roc_auc = None
    return ClassificationMetrics(
        accuracy=float(accuracy_score(y_true, y_pred)),
        precision=float(precision_score(y_true, y_pred, zero_division=0)),
        recall=float(recall_score(y_true, y_pred, zero_division=0)),
        f1=float(f1_score(y_true, y_pred, zero_division=0)),
        roc_auc=roc_auc,
        trade_count=int(len(y_true)),
    )


def _safe_auc(y_true, y_prob) -> Optional[float]:
    from sklearn.metrics import roc_auc_score
    if len(set(y_true.tolist() if hasattr(y_true, "tolist") else list(y_true))) < 2:
        return None
    try:
        return float(roc_auc_score(y_true, y_prob))
    except ValueError:
        return None


def _diagnostics(y_true, y_prob) -> dict:
    """Confusion matrix, ROC curve, PR curve, calibration curve -- all from
    one validation-set prediction pass, computed once at training time and
    stored, never recomputed per page view."""
    import numpy as np
    from sklearn.calibration import calibration_curve
    from sklearn.metrics import confusion_matrix, precision_recall_curve, roc_curve

    y_true = np.asarray(y_true)
    y_prob = np.asarray(y_prob)
    y_pred = (y_prob >= 0.5).astype(int)

    tn, fp, fn, tp = confusion_matrix(y_true, y_pred, labels=[0, 1]).ravel()
    out: dict = {"confusion_matrix": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)}}

    if len(set(y_true.tolist())) > 1:
        fpr, tpr, _ = roc_curve(y_true, y_prob)
        out["roc_curve"] = {"fpr": fpr.tolist(), "tpr": tpr.tolist()}
        precision, recall, _ = precision_recall_curve(y_true, y_prob)
        out["pr_curve"] = {"precision": precision.tolist(), "recall": recall.tolist()}
        try:
            n_bins = min(10, max(2, len(y_true) // 5))
            frac_pos, mean_pred = calibration_curve(y_true, y_prob, n_bins=n_bins, strategy="quantile")
            out["calibration_curve"] = {"predicted": mean_pred.tolist(), "actual": frac_pos.tolist()}
        except ValueError:
            out["calibration_curve"] = None
    else:
        out["roc_curve"] = None
        out["pr_curve"] = None
        out["calibration_curve"] = None
    return out


def _overfit_check(train_metrics: ClassificationMetrics, validation_metrics: ClassificationMetrics):
    primary_train = train_metrics.roc_auc if train_metrics.roc_auc is not None else train_metrics.accuracy
    primary_val = validation_metrics.roc_auc if validation_metrics.roc_auc is not None else validation_metrics.accuracy
    gap = primary_train - primary_val
    if gap > OVERFIT_GAP_THRESHOLD:
        metric_name = "ROC AUC" if train_metrics.roc_auc is not None else "accuracy"
        return True, (
            f"Train {metric_name} {primary_train:.2f} vs. validation {primary_val:.2f} "
            f"(gap {gap:.2f}) -- model likely overfit. Consider simpler hyperparameters, "
            f"fewer features, or more training data."
        )
    return False, None


@dataclass
class TrainingResult:
    fitted_model: object
    train_metrics: ClassificationMetrics
    validation_metrics: ClassificationMetrics
    test_metrics: Optional[ClassificationMetrics]
    feature_importance: list
    diagnostics: dict
    overfit_warning: bool
    overfit_note: Optional[str]
    preprocessor: dict  # {"imputer":..., "scaler":...} -- fit on train fold only
    epoch_history: Optional[list] = None
    training_seconds: float = 0.0
    #: Row indices (into the caller's X/y/trades) actually used to fit
    #: `preprocessor`/`fitted_model` -- set by `train_model` after calling
    #: the per-model-type trainer, so `services.py` can persist the matching
    #: scaled training matrix (for similar-trades k-NN) without recomputing
    #: the split itself.
    train_indices: Optional[list] = None


def _fit_preprocessor(X, split: ChronologicalSplit):
    from sklearn.impute import SimpleImputer
    from sklearn.preprocessing import StandardScaler

    X_train_raw = X.iloc[split.train]
    imputer = SimpleImputer(strategy="median").fit(X_train_raw)
    scaler = StandardScaler().fit(imputer.transform(X_train_raw))

    def transform(indices):
        if not indices:
            return None
        raw = X.iloc[indices]
        return scaler.transform(imputer.transform(raw))

    return transform, {"imputer": imputer, "scaler": scaler}


def _permutation_importance(X_val, y_val, feature_names, predict_fn) -> list:
    from sklearn.base import BaseEstimator
    from sklearn.inspection import permutation_importance
    import numpy as np

    class _Estimator(BaseEstimator):
        def fit(self, *a, **k):
            return self

        def predict(self, X):
            return predict_fn(X)

    result = permutation_importance(
        _Estimator(), X_val, y_val, scoring="accuracy", n_repeats=8, random_state=0,
    )
    order = np.argsort(result.importances_mean)[::-1]
    return [{"feature": feature_names[i], "importance": float(result.importances_mean[i])} for i in order]


def _evaluate(
    predict_proba: Callable, X_train, y_train, X_val, y_val, X_test, y_test, feature_names,
) -> tuple:
    train_metrics = _binary_metrics(y_train, predict_proba(X_train))
    validation_metrics = _binary_metrics(y_val, predict_proba(X_val))
    test_metrics = _binary_metrics(y_test, predict_proba(X_test)) if X_test is not None and len(y_test) else None
    diagnostics = _diagnostics(y_val, predict_proba(X_val))
    overfit_warning, overfit_note = _overfit_check(train_metrics, validation_metrics)
    importance = _permutation_importance(
        X_val, y_val, feature_names, predict_fn=lambda arr: (predict_proba(arr) >= 0.5).astype(int),
    )
    return train_metrics, validation_metrics, test_metrics, diagnostics, overfit_warning, overfit_note, importance


def train_logistic_regression(X, y, split: ChronologicalSplit, hyperparameters: dict, progress: ProgressFn, should_stop: ShouldStopFn) -> TrainingResult:
    from sklearn.linear_model import LogisticRegression

    start = time.time()
    transform, preprocessor = _fit_preprocessor(X, split)
    X_train, y_train = transform(split.train), y.iloc[split.train].to_numpy()
    X_val, y_val = transform(split.validation), y.iloc[split.validation].to_numpy()
    X_test, y_test = transform(split.test), y.iloc[split.test].to_numpy() if split.test else None

    progress(0, 1, "Fitting logistic regression...")
    if should_stop():
        raise TrainingStopped()
    model = LogisticRegression(
        max_iter=int(hyperparameters.get("max_iter", 1000)), C=float(hyperparameters.get("C", 1.0)),
    ).fit(X_train, y_train)
    progress(1, 1, "Logistic regression fit complete.")

    def predict_proba(arr):
        return model.predict_proba(arr)[:, 1]

    train_m, val_m, test_m, diag, overfit, note, importance = _evaluate(
        predict_proba, X_train, y_train, X_val, y_val, X_test, y_test, list(X.columns),
    )
    return TrainingResult(
        fitted_model=model, train_metrics=train_m, validation_metrics=val_m, test_metrics=test_m,
        feature_importance=importance, diagnostics=diag, overfit_warning=overfit, overfit_note=note,
        preprocessor=preprocessor, training_seconds=time.time() - start,
    )


def train_random_forest(X, y, split: ChronologicalSplit, hyperparameters: dict, progress: ProgressFn, should_stop: ShouldStopFn) -> TrainingResult:
    from sklearn.ensemble import RandomForestClassifier

    start = time.time()
    transform, preprocessor = _fit_preprocessor(X, split)
    X_train, y_train = transform(split.train), y.iloc[split.train].to_numpy()
    X_val, y_val = transform(split.validation), y.iloc[split.validation].to_numpy()
    X_test, y_test = transform(split.test), y.iloc[split.test].to_numpy() if split.test else None

    total_trees = int(hyperparameters.get("n_estimators", 200))
    chunk = max(total_trees // 20, 1)
    model = RandomForestClassifier(
        n_estimators=chunk, warm_start=True,
        max_depth=hyperparameters.get("max_depth"),
        min_samples_leaf=int(hyperparameters.get("min_samples_leaf", 1)),
        random_state=0,
    )
    grown = 0
    while grown < total_trees:
        if should_stop():
            raise TrainingStopped()
        grown = min(grown + chunk, total_trees)
        model.n_estimators = grown
        model.fit(X_train, y_train)
        progress(grown, total_trees, f"grew {grown}/{total_trees} trees")

    def predict_proba(arr):
        return model.predict_proba(arr)[:, 1]

    train_m, val_m, test_m, diag, overfit, note, importance = _evaluate(
        predict_proba, X_train, y_train, X_val, y_val, X_test, y_test, list(X.columns),
    )
    return TrainingResult(
        fitted_model=model, train_metrics=train_m, validation_metrics=val_m, test_metrics=test_m,
        feature_importance=importance, diagnostics=diag, overfit_warning=overfit, overfit_note=note,
        preprocessor=preprocessor, training_seconds=time.time() - start,
    )


def train_xgboost(X, y, split: ChronologicalSplit, hyperparameters: dict, progress: ProgressFn, should_stop: ShouldStopFn) -> TrainingResult:
    import xgboost as xgb

    start = time.time()
    transform, preprocessor = _fit_preprocessor(X, split)
    X_train, y_train = transform(split.train), y.iloc[split.train].to_numpy()
    X_val, y_val = transform(split.validation), y.iloc[split.validation].to_numpy()
    X_test, y_test = transform(split.test), y.iloc[split.test].to_numpy() if split.test else None

    dtrain = xgb.DMatrix(X_train, label=y_train)
    dval = xgb.DMatrix(X_val, label=y_val)
    total_rounds = int(hyperparameters.get("n_estimators", 200))
    params = {
        "objective": "binary:logistic", "eval_metric": "auc",
        "max_depth": int(hyperparameters.get("max_depth", 4)),
        "eta": float(hyperparameters.get("learning_rate", 0.1)),
        "verbosity": 0,
    }
    report_every = max(total_rounds // 20, 1)
    booster = None
    for round_num in range(1, total_rounds + 1):
        if should_stop():
            raise TrainingStopped()
        booster = xgb.train(params, dtrain, num_boost_round=1, xgb_model=booster)
        if round_num % report_every == 0 or round_num == total_rounds:
            val_auc = _safe_auc(y_val, booster.predict(dval))
            auc_str = f"{val_auc:.3f}" if val_auc is not None else "n/a"
            progress(round_num, total_rounds, f"round {round_num}/{total_rounds} val_auc={auc_str}")

    def predict_proba(arr):
        return booster.predict(xgb.DMatrix(arr))

    train_m, val_m, test_m, diag, overfit, note, importance = _evaluate(
        predict_proba, X_train, y_train, X_val, y_val, X_test, y_test, list(X.columns),
    )
    return TrainingResult(
        fitted_model=booster, train_metrics=train_m, validation_metrics=val_m, test_metrics=test_m,
        feature_importance=importance, diagnostics=diag, overfit_warning=overfit, overfit_note=note,
        preprocessor=preprocessor, training_seconds=time.time() - start,
    )


def train_neural_network(X, y, split: ChronologicalSplit, hyperparameters: dict, progress: ProgressFn, should_stop: ShouldStopFn) -> TrainingResult:
    import torch
    import torch.nn as nn

    from .neural_net import build_mlp

    start = time.time()
    transform, preprocessor = _fit_preprocessor(X, split)
    X_train, y_train = transform(split.train), y.iloc[split.train].to_numpy()
    X_val, y_val = transform(split.validation), y.iloc[split.validation].to_numpy()
    X_test, y_test = transform(split.test), y.iloc[split.test].to_numpy() if split.test else None

    torch.manual_seed(0)
    hidden_sizes = tuple(hyperparameters.get("hidden_sizes", (32, 16)))
    model = build_mlp(X_train.shape[1], hidden_sizes)
    optimizer = torch.optim.Adam(model.parameters(), lr=float(hyperparameters.get("learning_rate", 0.001)))
    loss_fn = nn.BCELoss()

    X_train_t = torch.tensor(X_train, dtype=torch.float32)
    y_train_t = torch.tensor(y_train, dtype=torch.float32).unsqueeze(1)
    X_val_t = torch.tensor(X_val, dtype=torch.float32)
    y_val_t = torch.tensor(y_val, dtype=torch.float32).unsqueeze(1)

    total_epochs = int(hyperparameters.get("epochs", 50))
    epoch_history: list = []
    for epoch in range(1, total_epochs + 1):
        if should_stop():
            raise TrainingStopped()
        model.train()
        optimizer.zero_grad()
        pred = model(X_train_t)
        loss = loss_fn(pred, y_train_t)
        loss.backward()
        optimizer.step()

        model.eval()
        with torch.no_grad():
            val_pred = model(X_val_t)
            val_loss = float(loss_fn(val_pred, y_val_t).item())
            val_acc = float(((val_pred >= 0.5).float() == y_val_t).float().mean().item())
        epoch_history.append({"epoch": epoch, "loss": float(loss.item()), "val_loss": val_loss, "val_accuracy": val_acc})
        progress(epoch, total_epochs, f"epoch {epoch}/{total_epochs} loss={loss.item():.4f} val_loss={val_loss:.4f}")

    model.eval()

    def predict_proba(arr):
        with torch.no_grad():
            return model(torch.tensor(arr, dtype=torch.float32)).squeeze(1).numpy()

    train_m, val_m, test_m, diag, overfit, note, importance = _evaluate(
        predict_proba, X_train, y_train, X_val, y_val, X_test, y_test, list(X.columns),
    )
    return TrainingResult(
        fitted_model=model, train_metrics=train_m, validation_metrics=val_m, test_metrics=test_m,
        feature_importance=importance, diagnostics=diag, overfit_warning=overfit, overfit_note=note,
        preprocessor=preprocessor, epoch_history=epoch_history, training_seconds=time.time() - start,
    )


TRAINERS = {
    "logistic_regression": train_logistic_regression,
    "random_forest": train_random_forest,
    "xgboost": train_xgboost,
    "neural_network": train_neural_network,
}


def _aggregate_fold_metrics(metrics_list: Sequence[ClassificationMetrics]) -> dict:
    if not metrics_list:
        return {}
    aucs = [m.roc_auc for m in metrics_list if m.roc_auc is not None]
    return {
        "accuracy": sum(m.accuracy for m in metrics_list) / len(metrics_list),
        "precision": sum(m.precision for m in metrics_list) / len(metrics_list),
        "recall": sum(m.recall for m in metrics_list) / len(metrics_list),
        "f1": sum(m.f1 for m in metrics_list) / len(metrics_list),
        "roc_auc": (sum(aucs) / len(aucs)) if aucs else None,
        "trade_count": sum(m.trade_count for m in metrics_list),
        "fold_count": len(metrics_list),
    }


def train_model(
    model_type: str, X, y, trades: Sequence[dict], evaluation_mode: str, hyperparameters: dict,
    progress: ProgressFn, should_stop: ShouldStopFn,
) -> tuple:
    """Returns `(TrainingResult, metrics_dict)`. `metrics_dict` is always
    explicitly namespaced (`{"train": ..., "validation": ..., "test": ...}`
    or `{"train": ..., "walk_forward_out_of_sample": ...}`) so in-sample and
    out-of-sample numbers can never be confused downstream."""
    trainer = TRAINERS[model_type]

    if evaluation_mode == "chronological_split":
        split = split_trades_chronologically(trades)
        result = trainer(X, y, split, hyperparameters, progress, should_stop)
        result.train_indices = split.train
        metrics = {
            "train": result.train_metrics.as_dict(),
            "validation": result.validation_metrics.as_dict(),
            "test": result.test_metrics.as_dict() if result.test_metrics else None,
        }
        return result, metrics

    if evaluation_mode == "walk_forward":
        folds = walk_forward_folds(trades)
        n_folds = len(folds)
        train_results, val_results = [], []
        last_result: Optional[TrainingResult] = None
        for i, fold in enumerate(folds, start=1):
            if should_stop():
                raise TrainingStopped()
            sub_split = ChronologicalSplit(train=fold.train, validation=fold.test, test=[])

            def fold_progress(current, total, message, i=i, n_folds=n_folds):
                progress(i, n_folds, f"fold {i}/{n_folds}: {message}")

            fold_result = trainer(X, y, sub_split, hyperparameters, fold_progress, should_stop)
            fold_result.train_indices = fold.train
            train_results.append(fold_result.train_metrics)
            val_results.append(fold_result.validation_metrics)
            last_result = fold_result

        metrics = {
            "train": _aggregate_fold_metrics(train_results),
            "walk_forward_out_of_sample": _aggregate_fold_metrics(val_results),
        }
        assert last_result is not None
        return last_result, metrics

    raise ValueError(f"Unknown evaluation_mode {evaluation_mode!r}")
