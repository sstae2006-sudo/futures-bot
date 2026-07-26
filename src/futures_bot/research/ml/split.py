"""Chronological train/validation/test splitting and walk-forward folds for
ML model evaluation -- the trade-row analogue of `backtest.runner`'s
`split_bars`/`rolling_walk_forward`. Never shuffles: rows are always cut by
`entry_time` order, so no information from a later trade can leak into an
earlier one's training fold (the same doctrine `split_bars`'s docstring
states for bars: "Shuffling price data lets information from the future
leak into the training set, which is the most flattering mistake available
in backtesting").

Both functions return *index positions* into whatever sequence was passed
in, not the rows themselves -- so the same split can be applied identically
to a list of trade dicts and to the feature matrix (`X`/`y`) built from
them, since `research.ml.dataset.build_feature_matrix` preserves row order
1:1 against `TradeStore.fetch_trades`'s chronological `entry_time` order.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence


def _entry_time(row: dict) -> datetime:
    value = row["entry_time"]
    return value if isinstance(value, datetime) else datetime.fromisoformat(value)


@dataclass(frozen=True)
class ChronologicalSplit:
    train: list[int]
    validation: list[int]
    test: list[int]


def split_trades_chronologically(
    trades: Sequence[dict], train_fraction: float = 0.7, validation_fraction: float = 0.15,
) -> ChronologicalSplit:
    """Three contiguous, non-overlapping, time-ordered blocks. The test
    block is the honest out-of-sample check -- nothing here, or in any
    caller, may look at it before final reporting."""
    if not (0 < train_fraction < 1) or not (0 < validation_fraction < 1) or train_fraction + validation_fraction >= 1:
        raise ValueError(
            f"train_fraction ({train_fraction}) and validation_fraction ({validation_fraction}) must "
            f"each be between 0 and 1 and sum to less than 1."
        )
    order = sorted(range(len(trades)), key=lambda i: _entry_time(trades[i]))
    total = len(order)
    train_cut = int(total * train_fraction)
    validation_cut = train_cut + int(total * validation_fraction)
    return ChronologicalSplit(
        train=order[:train_cut], validation=order[train_cut:validation_cut], test=order[validation_cut:],
    )


@dataclass(frozen=True)
class WalkForwardFold:
    window: int
    train: list[int]
    test: list[int]


def walk_forward_folds(
    trades: Sequence[dict], train_fraction: float = 0.7, test_fraction: float = 0.15,
) -> list[WalkForwardFold]:
    """Rolling train/test windows, shifted forward by one test window each
    time -- ports `backtest.runner.rolling_walk_forward`'s exact windowing
    logic from bars to trade rows."""
    order = sorted(range(len(trades)), key=lambda i: _entry_time(trades[i]))
    total = len(order)
    train_size = int(total * train_fraction)
    test_size = int(total * test_fraction)
    if train_size <= 0 or test_size <= 0:
        raise ValueError("Dataset too small for walk-forward validation at these fractions.")

    folds: list[WalkForwardFold] = []
    start = 0
    window = 1
    while start + train_size + test_size <= total:
        folds.append(
            WalkForwardFold(
                window=window,
                train=order[start : start + train_size],
                test=order[start + train_size : start + train_size + test_size],
            )
        )
        start += test_size
        window += 1
    if not folds:
        raise ValueError("Dataset too small for walk-forward validation at these fractions.")
    return folds
