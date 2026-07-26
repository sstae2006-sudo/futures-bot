"""Per-strategy ML feature matrix construction and dataset health checks.

Scoped to one strategy at a time, deliberately: strategies write wildly
different `entry_metadata` keys (`ema_crossover`'s `fast`/`slow` vs.
`opening_range_breakout`'s `range_high`/`range_low`), so a single
cross-strategy feature matrix would be mostly missing values for every row.
One model per strategy, using only that strategy's own feature columns,
avoids that sparsity entirely.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Optional

from ..trade_store import TradeStore, default_db_path

#: Regime labels already persisted per-trade (`research.regime.compute_regimes`,
#: written at trade-record build time) -- reused as extra categorical
#: features rather than recomputed.
_REGIME_COLUMNS = ("regime_trend", "regime_volatility", "regime_session")

#: Columns that identify *which backtest run recorded this row*, never what
#: the market did -- excluded from the feature matrix by construction (only
#: `_REGIME_COLUMNS` and `entry_metadata` ever feed `feature_columns_raw`
#: below). Kept here as documentation of the boundary, and reused by
#: `api.services.export_ml_dataset_csv` so the CSV export draws the same
#: line between "feature" and "backtest lineage" as training does.
BACKTEST_METADATA_COLUMNS = ("id", "trade_id", "run_id", "contract", "created_at")

#: One row per underlying market opportunity: two backtest runs (a rerun, an
#: overlapping walk-forward window, an optimizer sweep that happens to fire
#: on the same bar) that produced the exact same trade must not double that
#: trade's weight in training. `run_id`/`trade_id`/`strategy_params` are
#: deliberately excluded from this key -- they identify *which run* found the
#: trade, not *what the market did*, so two runs (same or different config)
#: landing on an identical entry/exit are the same market event by
#: definition and collapse to one row.
_MARKET_EVENT_KEY_FIELDS = ("entry_time", "side", "entry_price", "exit_time", "exit_price", "exit_reason")

MIN_TRADES_FOR_TRAINING = 60
MIN_CLASSES = 2
MISSING_VALUE_WARN_RATIO = 0.2
CLASS_IMBALANCE_WARN_RATIO = 0.9


@dataclass(frozen=True)
class DedupedTrades:
    trades: list[dict]
    total_rows: int
    unique_timestamps: int
    duplicate_count: int


def dedupe_market_events(trades: list[dict]) -> DedupedTrades:
    """Collapses repeated recordings of the same market opportunity across
    separate backtest/walk-forward/optimizer runs before it ever reaches a
    feature matrix or CSV export -- see `_MARKET_EVENT_KEY_FIELDS`. Keeps the
    first occurrence in the input's order (callers pass rows already sorted
    by `entry_time`, so that's the earliest-recorded copy). Also prevents a
    subtler failure mode: an exact-duplicate row surviving into
    `split_trades_chronologically` could land its two copies on opposite
    sides of the train/test cut, which is direct data leakage (the model
    would effectively see the test row during training)."""
    total_rows = len(trades)
    unique_timestamps = len({str(t["entry_time"]) for t in trades})

    seen: set[tuple] = set()
    deduped: list[dict] = []
    for t in trades:
        key = tuple(str(t[field]) for field in _MARKET_EVENT_KEY_FIELDS)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(t)

    return DedupedTrades(
        trades=deduped, total_rows=total_rows, unique_timestamps=unique_timestamps,
        duplicate_count=total_rows - len(deduped),
    )


@dataclass(frozen=True)
class FeatureMatrix:
    trades: list[dict]
    feature_columns: list[str]  # final, post-encoding columns -- what the model actually trains on
    numeric_raw_keys: list[str]  # raw entry_metadata/regime keys treated as numeric
    #: one-hot column name -> (raw_key, category value, or None for the
    #: "missing"/NaN dummy) -- lets a single raw feature dict (e.g. a live
    #: `Signal.metadata`) be re-encoded into this exact column space at
    #: prediction time without needing pandas or the training data again.
    dummy_source: dict
    X: object  # pandas.DataFrame
    y: object  # pandas.Series, 1 = win, 0 = loss/scratch
    dataset_size: int  # final training dataset size, after dedup -- one row per unique market opportunity
    dataset_version: str
    #: Duplicate-detection stats from `dedupe_market_events`, kept alongside
    #: the matrix so callers (dataset_health, the CSV export) can report
    #: them without re-fetching/re-deduping the same trades a second time.
    total_rows: int = 0
    unique_timestamps: int = 0
    duplicate_count: int = 0


def _dataset_version(trades: list[dict]) -> str:
    """Hash of (trade id set + latest created_at) -- changes whenever the
    underlying trade set changes, so retraining on updated data is visibly
    a new dataset version even when the trade *count* happens to match."""
    ids = sorted(t["id"] for t in trades)
    latest = max((t["created_at"] for t in trades), default="")
    digest = hashlib.sha256(f"{ids}|{latest}".encode()).hexdigest()
    return digest[:16]


def build_feature_matrix(strategy: str, store: Optional[TradeStore] = None) -> FeatureMatrix:
    import pandas as pd

    owns_store = store is None
    store = store or TradeStore(default_db_path())
    try:
        trades_raw = store.fetch_trades(strategy=strategy)
    finally:
        if owns_store:
            store.close()

    deduped = dedupe_market_events(trades_raw)
    trades = deduped.trades

    feature_columns_raw = sorted({key for t in trades for key in t["entry_metadata"]})
    feature_columns_raw += [c for c in _REGIME_COLUMNS if any(t.get(c) is not None for t in trades)]

    rows = []
    for t in trades:
        row = {}
        for key in feature_columns_raw:
            row[key] = t.get(key) if key in _REGIME_COLUMNS else t["entry_metadata"].get(key)
        rows.append(row)
    X_raw = pd.DataFrame(rows, columns=feature_columns_raw)

    # Every `entry_metadata` value round-trips through JSON as a string
    # (see `trade_store._jsonable` -- Decimals are stringified before
    # storage), so dtype alone can't distinguish "numeric" from
    # "categorical" here. Instead: a column is numeric if coercing it
    # produces no *more* missing values than it already had; a column like
    # `trend_direction` ("bullish"/"bearish") fails that coercion almost
    # entirely and is treated as categorical instead.
    numeric_raw_keys, categorical_raw_keys = [], []
    for c in feature_columns_raw:
        coerced = pd.to_numeric(X_raw[c], errors="coerce")
        if coerced.isna().sum() <= X_raw[c].isna().sum():
            numeric_raw_keys.append(c)
        else:
            categorical_raw_keys.append(c)

    X_numeric = (
        X_raw[numeric_raw_keys].apply(pd.to_numeric, errors="coerce")
        if numeric_raw_keys else pd.DataFrame(index=X_raw.index)
    )

    dummy_source: dict = {}
    if categorical_raw_keys:
        X_categorical = pd.get_dummies(X_raw[categorical_raw_keys], columns=categorical_raw_keys, dummy_na=True)
        # Longest raw key first, so e.g. `trend_direction_bullish` resolves
        # to raw key `trend_direction` rather than a shorter colliding
        # prefix like `trend` (both are real feature names in this
        # dataset -- ema_crossover's `trend` vs. trend_pullback's
        # `trend_direction`).
        sorted_keys = sorted(categorical_raw_keys, key=len, reverse=True)
        for col in X_categorical.columns:
            for raw_key in sorted_keys:
                prefix = raw_key + "_"
                if col.startswith(prefix):
                    suffix = col[len(prefix):]
                    dummy_source[col] = (raw_key, None if suffix == "nan" else suffix)
                    break
    else:
        X_categorical = pd.DataFrame(index=X_raw.index)

    X = pd.concat([X_numeric, X_categorical], axis=1)
    y = pd.Series([1 if t["outcome"] == "win" else 0 for t in trades], name="outcome")

    return FeatureMatrix(
        trades=trades, feature_columns=list(X.columns),
        numeric_raw_keys=numeric_raw_keys, dummy_source=dummy_source,
        X=X, y=y, dataset_size=len(trades), dataset_version=_dataset_version(trades),
        total_rows=deduped.total_rows, unique_timestamps=deduped.unique_timestamps,
        duplicate_count=deduped.duplicate_count,
    )


def encode_feature_row(feature_row: dict, numeric_raw_keys: list, dummy_source: dict, feature_columns: list):
    """Re-encodes one raw feature dict (a live `Signal.metadata`, or a
    stored trade's `entry_metadata` + regime labels) into the exact column
    space `feature_columns` was trained on. The single scoring path shared
    by the backtest/live-trading AI filter and the Prediction Sandbox --
    both call this, never a second, ad hoc encoding."""
    import numpy as np

    values = []
    for col in feature_columns:
        if col in numeric_raw_keys:
            raw_val = feature_row.get(col)
            try:
                values.append(float(raw_val) if raw_val is not None else float("nan"))
            except (TypeError, ValueError):
                values.append(float("nan"))
        elif col in dummy_source:
            raw_key, category_value = dummy_source[col]
            raw_val = feature_row.get(raw_key)
            if category_value is None:
                match = raw_val is None
            else:
                match = raw_val is not None and str(raw_val) == category_value
            values.append(1.0 if match else 0.0)
        else:
            values.append(float("nan"))
    return np.array([values], dtype=float)


@dataclass(frozen=True)
class DatasetHealth:
    status: str  # 'READY' | 'WARNING' | 'NOT_ENOUGH_DATA'
    reasons: list = field(default_factory=list)
    trade_count: int = 0
    win_count: int = 0
    loss_count: int = 0
    win_rate: Optional[float] = None
    date_range: Optional[tuple] = None
    feature_count: int = 0
    missing_value_count: int = 0
    missing_value_ratio: float = 0.0
    #: Duplicate-market-event audit (see `dedupe_market_events`): how many
    #: rows existed across every backtest run recorded for this strategy
    #: before collapsing reruns/overlapping windows down to one row per
    #: unique market opportunity. `trade_count` above is already the
    #: post-dedup figure -- these three exist so that's visibly not an
    #: unexplained drop.
    total_rows: int = 0
    unique_timestamps: int = 0
    duplicate_market_events: int = 0


def dataset_health(strategy: str, store: Optional[TradeStore] = None) -> DatasetHealth:
    fm = build_feature_matrix(strategy, store=store)
    trades = fm.trades
    win_count = sum(1 for t in trades if t["outcome"] == "win")
    loss_count = len(trades) - win_count
    n_classes = len({t["outcome"] == "win" for t in trades}) if trades else 0

    missing = int(fm.X.isna().sum().sum()) if fm.X.shape[1] else 0
    total_cells = fm.X.shape[0] * fm.X.shape[1] if fm.X.shape[1] else 0
    missing_ratio = (missing / total_cells) if total_cells else 0.0

    reasons: list[str] = []
    status = "READY"

    if fm.dataset_size < MIN_TRADES_FOR_TRAINING or n_classes < MIN_CLASSES:
        status = "NOT_ENOUGH_DATA"
        if fm.dataset_size < MIN_TRADES_FOR_TRAINING:
            reasons.append(f"Only {fm.dataset_size} trades (need at least {MIN_TRADES_FOR_TRAINING}).")
        if n_classes < MIN_CLASSES:
            reasons.append("Only one outcome class present (need both wins and losses).")
    else:
        majority_share = max(win_count, loss_count) / fm.dataset_size
        if missing_ratio > MISSING_VALUE_WARN_RATIO:
            status = "WARNING"
            reasons.append(f"{missing_ratio:.0%} of feature cells are missing.")
        if majority_share > CLASS_IMBALANCE_WARN_RATIO:
            status = "WARNING"
            reasons.append(f"Class imbalance: {majority_share:.0%} of trades share one outcome.")

    date_range = (trades[0]["entry_time"], trades[-1]["entry_time"]) if trades else None

    return DatasetHealth(
        status=status, reasons=reasons, trade_count=fm.dataset_size,
        win_count=win_count, loss_count=loss_count,
        win_rate=(win_count / fm.dataset_size) if fm.dataset_size else None,
        date_range=date_range, feature_count=len(fm.feature_columns),
        missing_value_count=missing, missing_value_ratio=missing_ratio,
        total_rows=fm.total_rows, unique_timestamps=fm.unique_timestamps,
        duplicate_market_events=fm.duplicate_count,
    )


def feature_distribution(strategy: str, feature: str, bins: int = 12, store: Optional[TradeStore] = None) -> dict:
    """Server-side histogram binning for the Feature Explorer -- avoids
    shipping every raw trade row to the browser just to draw one chart."""
    import numpy as np

    fm = build_feature_matrix(strategy, store=store)
    if feature not in fm.X.columns:
        raise ValueError(f"Unknown feature {feature!r} for strategy {strategy!r}.")

    values = fm.X[feature].astype(float)
    valid = values.dropna()
    wins = values[fm.y == 1].dropna()
    losses = values[fm.y == 0].dropna()

    if valid.empty or valid.nunique() < 2:
        return {
            "feature": feature, "bins": [], "counts": [], "win_counts": [], "loss_counts": [],
            "mean": float(valid.mean()) if not valid.empty else None,
            "std": None, "min": float(valid.min()) if not valid.empty else None,
            "max": float(valid.max()) if not valid.empty else None,
            "win_average": float(wins.mean()) if len(wins) else None,
            "loss_average": float(losses.mean()) if len(losses) else None,
        }

    edges = np.histogram_bin_edges(valid, bins=bins)
    counts, _ = np.histogram(valid, bins=edges)
    win_counts = np.histogram(wins, bins=edges)[0] if len(wins) else np.zeros(len(edges) - 1, dtype=int)
    loss_counts = np.histogram(losses, bins=edges)[0] if len(losses) else np.zeros(len(edges) - 1, dtype=int)

    return {
        "feature": feature, "bins": edges.tolist(), "counts": counts.tolist(),
        "win_counts": win_counts.tolist(), "loss_counts": loss_counts.tolist(),
        "mean": float(valid.mean()), "std": float(valid.std()) if len(valid) > 1 else 0.0,
        "min": float(valid.min()), "max": float(valid.max()),
        "win_average": float(wins.mean()) if len(wins) else None,
        "loss_average": float(losses.mean()) if len(losses) else None,
    }
