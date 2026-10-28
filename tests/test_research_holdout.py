"""Tests for `research.holdout.protected_range` -- the hold-out/data-
snooping ledger's read side. Professional-research-pipeline plan Phase 3.
"""

from __future__ import annotations

from datetime import datetime, timezone

from futures_bot.research.holdout import protected_range
from futures_bot.research.trade_store import TradeStore


def _dt(y, m, d):
    return datetime(y, m, d, tzinfo=timezone.utc)


class TestProtectedRange:
    def test_true_when_nothing_has_ever_been_recorded(self, tmp_path):
        store = TradeStore(tmp_path / "t.db")
        status = protected_range(store, "MES", "5min", _dt(2025, 1, 1), _dt(2025, 6, 1))
        assert status.is_protected is True
        assert bool(status) is True
        assert status.overlapping_ranges == []

    def test_false_when_the_exact_range_was_already_used(self, tmp_path):
        store = TradeStore(tmp_path / "t.db")
        store.insert_dataset_usage(
            product_code="MES", resolution="5min",
            start_ts=_dt(2025, 1, 1), end_ts=_dt(2025, 6, 1), purpose="train",
        )
        status = protected_range(store, "MES", "5min", _dt(2025, 1, 1), _dt(2025, 6, 1))
        assert status.is_protected is False
        assert bool(status) is False
        assert len(status.overlapping_ranges) == 1

    def test_false_on_partial_overlap(self, tmp_path):
        store = TradeStore(tmp_path / "t.db")
        store.insert_dataset_usage(
            product_code="MES", resolution="5min",
            start_ts=_dt(2025, 1, 1), end_ts=_dt(2025, 6, 1), purpose="optimizer",
        )
        # Candidate range only partially overlaps the recorded one.
        status = protected_range(store, "MES", "5min", _dt(2025, 5, 1), _dt(2025, 12, 1))
        assert status.is_protected is False

    def test_true_when_a_disjoint_range_was_used_instead(self, tmp_path):
        store = TradeStore(tmp_path / "t.db")
        store.insert_dataset_usage(
            product_code="MES", resolution="5min",
            start_ts=_dt(2025, 1, 1), end_ts=_dt(2025, 6, 1), purpose="train",
        )
        # Genuinely later, non-overlapping range.
        status = protected_range(store, "MES", "5min", _dt(2025, 7, 1), _dt(2025, 12, 1))
        assert status.is_protected is True

    def test_ignores_a_different_product_code(self, tmp_path):
        store = TradeStore(tmp_path / "t.db")
        store.insert_dataset_usage(
            product_code="MNQ", resolution="5min",
            start_ts=_dt(2025, 1, 1), end_ts=_dt(2025, 6, 1), purpose="train",
        )
        status = protected_range(store, "MES", "5min", _dt(2025, 1, 1), _dt(2025, 6, 1))
        assert status.is_protected is True

    def test_ignores_a_different_resolution(self, tmp_path):
        store = TradeStore(tmp_path / "t.db")
        store.insert_dataset_usage(
            product_code="MES", resolution="1min",
            start_ts=_dt(2025, 1, 1), end_ts=_dt(2025, 6, 1), purpose="train",
        )
        status = protected_range(store, "MES", "5min", _dt(2025, 1, 1), _dt(2025, 6, 1))
        assert status.is_protected is True

    def test_overlapping_ranges_reports_the_purpose(self, tmp_path):
        store = TradeStore(tmp_path / "t.db")
        store.insert_dataset_usage(
            product_code="MES", resolution="5min",
            start_ts=_dt(2025, 1, 1), end_ts=_dt(2025, 6, 1), purpose="validation",
        )
        status = protected_range(store, "MES", "5min", _dt(2025, 1, 1), _dt(2025, 6, 1))
        assert status.overlapping_ranges[0][2] == "validation"

    def test_multiple_recorded_ranges_all_reported(self, tmp_path):
        store = TradeStore(tmp_path / "t.db")
        store.insert_dataset_usage(
            product_code="MES", resolution="5min",
            start_ts=_dt(2025, 1, 1), end_ts=_dt(2025, 3, 1), purpose="train",
        )
        store.insert_dataset_usage(
            product_code="MES", resolution="5min",
            start_ts=_dt(2025, 2, 1), end_ts=_dt(2025, 4, 1), purpose="validation",
        )
        status = protected_range(store, "MES", "5min", _dt(2025, 1, 15), _dt(2025, 2, 15))
        assert len(status.overlapping_ranges) == 2


class TestDatasetUsageStore:
    def test_insert_and_fetch_round_trip(self, tmp_path):
        store = TradeStore(tmp_path / "t.db")
        store.insert_dataset_usage(
            product_code="MES", resolution="5min",
            start_ts=_dt(2025, 1, 1), end_ts=_dt(2025, 6, 1), purpose="optimizer",
            strategy="trend_pullback", run_id_or_batch_id="batch-1",
        )
        rows = store.fetch_dataset_usage("MES", "5min")
        assert len(rows) == 1
        assert rows[0]["purpose"] == "optimizer"
        assert rows[0]["strategy"] == "trend_pullback"
        assert rows[0]["run_id_or_batch_id"] == "batch-1"

    def test_strategy_and_batch_id_are_optional(self, tmp_path):
        store = TradeStore(tmp_path / "t.db")
        store.insert_dataset_usage(
            product_code="MES", resolution="5min",
            start_ts=_dt(2025, 1, 1), end_ts=_dt(2025, 6, 1), purpose="exploratory",
        )
        rows = store.fetch_dataset_usage("MES", "5min")
        assert rows[0]["strategy"] is None
        assert rows[0]["run_id_or_batch_id"] is None

    def test_fetch_is_scoped_to_product_and_resolution(self, tmp_path):
        store = TradeStore(tmp_path / "t.db")
        store.insert_dataset_usage(
            product_code="MES", resolution="5min",
            start_ts=_dt(2025, 1, 1), end_ts=_dt(2025, 6, 1), purpose="train",
        )
        store.insert_dataset_usage(
            product_code="MNQ", resolution="5min",
            start_ts=_dt(2025, 1, 1), end_ts=_dt(2025, 6, 1), purpose="train",
        )
        assert len(store.fetch_dataset_usage("MES", "5min")) == 1
        assert len(store.fetch_dataset_usage("MNQ", "5min")) == 1
        assert len(store.fetch_dataset_usage("MES", "1min")) == 0
