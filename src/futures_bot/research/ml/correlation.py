"""Feature correlation vs. win/loss, net P&L, and an approximate R-multiple.

**R-multiple caveat**: entry stop distance isn't persisted per trade today
(the `trades` table has no stop-loss column), so R here is
``net_pnl / avg_loss_size_for_strategy`` -- a standard practical proxy for
"how many average losses this trade was worth" when the true initial risk
wasn't logged. This is an approximation, documented here and surfaced in
the UI as such, not a substitute for logging real per-trade risk. It is the
one definition used everywhere R-multiple appears in this package (here and
in `predict.expected_value_r`), never a second, divergent one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .dataset import FeatureMatrix, build_feature_matrix


@dataclass(frozen=True)
class CorrelationRow:
    feature: str
    corr_vs_win: Optional[float]
    corr_vs_pnl: Optional[float]
    corr_vs_r: Optional[float]


def average_loss_size(trades: list) -> Optional[float]:
    losses = [abs(float(t["net_pnl"])) for t in trades if float(t["net_pnl"]) < 0]
    return (sum(losses) / len(losses)) if losses else None


def average_win_size(trades: list) -> Optional[float]:
    wins = [float(t["net_pnl"]) for t in trades if float(t["net_pnl"]) > 0]
    return (sum(wins) / len(wins)) if wins else None


def r_multiples(trades: list) -> list:
    avg_loss = average_loss_size(trades)
    if not avg_loss:
        return [None] * len(trades)
    return [float(t["net_pnl"]) / avg_loss for t in trades]


def r_multiple_baseline(trades: list) -> dict:
    """Average win/loss R-multiples over the whole trade set -- used by
    `predict.expected_value_r` as the payoff sizes in an expected-value
    calculation. Whole-dataset rather than train-fold-only: this is a
    reporting/context statistic, not a trained model parameter, so it
    isn't subject to the same train/test leakage discipline."""
    avg_loss = average_loss_size(trades)
    avg_win = average_win_size(trades)
    return {
        "avg_win_r": (avg_win / avg_loss) if avg_win is not None and avg_loss else None,
        "avg_loss_r": 1.0 if avg_loss else None,
    }


def compute_correlations(strategy: str, fm: Optional[FeatureMatrix] = None) -> list:
    import pandas as pd

    fm = fm or build_feature_matrix(strategy)
    pnl = pd.Series([float(t["net_pnl"]) for t in fm.trades])
    r = pd.Series(r_multiples(fm.trades))

    rows = []
    for col in fm.feature_columns:
        series = fm.X[col]
        if series.nunique(dropna=True) < 2:
            rows.append(CorrelationRow(feature=col, corr_vs_win=None, corr_vs_pnl=None, corr_vs_r=None))
            continue
        corr_win = series.corr(fm.y) if fm.y.nunique() > 1 else None
        corr_pnl = series.corr(pnl)
        corr_r = series.corr(r) if r.notna().any() else None
        rows.append(CorrelationRow(
            feature=col,
            corr_vs_win=None if corr_win is None or pd.isna(corr_win) else round(float(corr_win), 4),
            corr_vs_pnl=None if pd.isna(corr_pnl) else round(float(corr_pnl), 4),
            corr_vs_r=None if corr_r is None or pd.isna(corr_r) else round(float(corr_r), 4),
        ))
    return rows
