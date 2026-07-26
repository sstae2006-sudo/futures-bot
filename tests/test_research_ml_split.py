"""Chronological split/walk-forward folds never shuffle and never leak a
later trade into an earlier fold -- see `research/ml/split.py`'s docstring."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from futures_bot.research.ml.split import split_trades_chronologically, walk_forward_folds


def _trades(n: int, shuffled: bool = False):
    base = datetime(2024, 1, 1)
    trades = [{"id": i, "entry_time": (base + timedelta(hours=i)).isoformat()} for i in range(n)]
    if shuffled:
        # Deliberately out of chronological order in the input list --
        # the split must still respect entry_time, not list position.
        trades = trades[::-1]
    return trades


class TestChronologicalSplit:
    def test_splits_are_contiguous_and_cover_everything(self):
        trades = _trades(100)
        split = split_trades_chronologically(trades, train_fraction=0.7, validation_fraction=0.15)
        assert len(split.train) + len(split.validation) + len(split.test) == 100
        assert set(split.train) | set(split.validation) | set(split.test) == set(range(100))
        assert not (set(split.train) & set(split.validation))
        assert not (set(split.validation) & set(split.test))

    def test_train_block_is_strictly_earlier_than_validation_and_test(self):
        trades = _trades(100)
        split = split_trades_chronologically(trades)
        train_times = [trades[i]["entry_time"] for i in split.train]
        validation_times = [trades[i]["entry_time"] for i in split.validation]
        test_times = [trades[i]["entry_time"] for i in split.test]
        assert max(train_times) <= min(validation_times)
        assert max(validation_times) <= min(test_times)

    def test_never_shuffles_even_if_input_order_is_reversed(self):
        trades = _trades(50, shuffled=True)
        split = split_trades_chronologically(trades)
        train_times = sorted(trades[i]["entry_time"] for i in split.train)
        assert train_times == [trades[i]["entry_time"] for i in split.train]
        # The earliest 35 (70%) chronological trades are exactly the train block,
        # regardless of the reversed input list order.
        all_times_sorted = sorted(t["entry_time"] for t in trades)
        assert set(train_times) == set(all_times_sorted[:35])

    def test_rejects_invalid_fractions(self):
        trades = _trades(10)
        with pytest.raises(ValueError):
            split_trades_chronologically(trades, train_fraction=0.8, validation_fraction=0.3)


class TestWalkForwardFolds:
    def test_folds_are_rolling_and_never_overlap_within_a_fold(self):
        trades = _trades(200)
        folds = walk_forward_folds(trades, train_fraction=0.5, test_fraction=0.1)
        assert len(folds) >= 2
        for fold in folds:
            assert not (set(fold.train) & set(fold.test))

        # Each fold's test window starts later than the previous fold's.
        starts = [min(fold.test) for fold in folds]
        assert starts == sorted(starts)

    def test_too_small_dataset_raises(self):
        trades = _trades(5)
        with pytest.raises(ValueError):
            walk_forward_folds(trades, train_fraction=0.7, test_fraction=0.15)
