"""Tests for `market_data.store.MarketDataStore`: schema creation, the
idempotent-upsert dedup guarantee, coverage/range queries, active-contract
roll tracking, sync-run checkpointing (the resumability mechanism), and
gap recording/resolution.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from futures_bot.market_data.store import MarketDataStore
from futures_bot.models import Bar


def make_bar(ts: datetime, close: Decimal = Decimal("7500")) -> Bar:
    return Bar(timestamp=ts, open=close, high=close + 1, low=close - 1, close=close, volume=500)


class TestSchema:
    def test_creating_store_creates_tables(self, tmp_path):
        store = MarketDataStore(tmp_path / "market_data.db")
        assert store.coverage("MES", "5min").count == 0
        assert store.fetch_gaps("MES") == []
        assert store.fetch_sync_runs("MES") == []

    def test_ensure_schema_is_idempotent(self, tmp_path):
        path = tmp_path / "market_data.db"
        MarketDataStore(path).close()
        store = MarketDataStore(path)  # re-opening must not raise
        store.ensure_schema()  # calling again must not raise either
        store.close()


class TestBars:
    def test_upsert_and_fetch_round_trips_decimal_exact(self, tmp_path):
        store = MarketDataStore(tmp_path / "market_data.db")
        now = datetime(2026, 7, 21, 14, 0, tzinfo=timezone.utc)
        bars = [make_bar(now, Decimal("7500.25")), make_bar(now + timedelta(minutes=5), Decimal("7501.75"))]

        inserted = store.upsert_bars("MES", "MESU6", "5min", "massive", bars)
        assert inserted == 2

        fetched = store.fetch_bars("MES", "5min")
        assert [b.close for b in fetched] == [Decimal("7500.25"), Decimal("7501.75")]
        assert all(isinstance(b.close, Decimal) for b in fetched)

    def test_upsert_is_idempotent_across_overlapping_contracts(self, tmp_path):
        """The core invariant: at most one bar per (product, resolution,
        timestamp), regardless of which contract claims it -- see
        store.py's module docstring on why this replaces read-time
        stitching entirely."""
        store = MarketDataStore(tmp_path / "market_data.db")
        now = datetime(2026, 6, 18, 14, 0, tzinfo=timezone.utc)
        bar = make_bar(now, Decimal("7500"))

        first = store.upsert_bars("MES", "MESM6", "5min", "massive", [bar])
        # A second, later-detected contract tries to claim the exact same
        # timestamp -- e.g. a backfill re-run, or an overlapping window.
        second = store.upsert_bars("MES", "MESU6", "5min", "massive", [make_bar(now, Decimal("9999"))])

        assert first == 1
        assert second == 0
        stored = store.fetch_bars("MES", "5min")
        assert len(stored) == 1
        assert stored[0].close == Decimal("7500")  # the first writer wins

    def test_fetch_bars_respects_start_end_and_order(self, tmp_path):
        store = MarketDataStore(tmp_path / "market_data.db")
        base = datetime(2026, 7, 21, 14, 0, tzinfo=timezone.utc)
        bars = [make_bar(base + timedelta(minutes=5 * i)) for i in range(5)]
        store.upsert_bars("MES", "MESU6", "5min", "massive", bars)

        subset = store.fetch_bars("MES", "5min", start=base + timedelta(minutes=5), end=base + timedelta(minutes=10))
        assert [b.timestamp for b in subset] == [base + timedelta(minutes=5), base + timedelta(minutes=10)]

    def test_coverage_reports_count_and_range(self, tmp_path):
        store = MarketDataStore(tmp_path / "market_data.db")
        base = datetime(2026, 7, 21, 14, 0, tzinfo=timezone.utc)
        store.upsert_bars("MES", "MESU6", "5min", "massive", [make_bar(base), make_bar(base + timedelta(minutes=5))])

        coverage = store.coverage("MES", "5min")
        assert coverage.count == 2
        assert coverage.earliest == base
        assert coverage.latest == base + timedelta(minutes=5)

    def test_contracts_stored_and_all_products(self, tmp_path):
        store = MarketDataStore(tmp_path / "market_data.db")
        now = datetime(2026, 7, 21, 14, 0, tzinfo=timezone.utc)
        store.upsert_bars("MES", "MESM6", "5min", "massive", [make_bar(now)])
        store.upsert_bars("MES", "MESU6", "5min", "massive", [make_bar(now + timedelta(days=100))])
        store.upsert_bars("MNQ", "MNQU6", "5min", "massive", [make_bar(now)])

        assert store.contracts_stored("MES") == ["MESM6", "MESU6"]
        assert store.all_products() == ["MES", "MNQ"]


class TestProductCoverageSummary:
    """KNOWN_ISSUES.md ISSUE-042 -- the batched replacement for calling
    contracts_stored()/resolutions_stored()/coverage() once per
    all_products() result. Golden-equivalence tested against the
    original per-product primitives, not just checked for a plausible
    shape."""

    def test_matches_per_product_primitives_exactly(self, tmp_path):
        store = MarketDataStore(tmp_path / "market_data.db")
        base = datetime(2026, 7, 21, 14, 0, tzinfo=timezone.utc)
        store.upsert_bars("MES", "MESM6", "5min", "massive", [make_bar(base), make_bar(base + timedelta(minutes=5))])
        store.upsert_bars("MES", "MESU6", "1min", "massive", [make_bar(base + timedelta(days=1))])  # a second, less-complete resolution
        store.upsert_bars("MNQ", "MNQU6", "5min", "massive", [make_bar(base)])

        summary = {row["product_code"]: row for row in store.product_coverage_summary()}

        for product_code in store.all_products():
            contracts = store.contracts_stored(product_code)
            resolutions = store.resolutions_stored(product_code)
            best = max((store.coverage(product_code, r) for r in resolutions), key=lambda c: c.count)

            row = summary[product_code]
            assert row["contracts_stored"] == contracts
            assert row["bars_stored"] == best.count
            assert row["earliest"] == best.earliest
            assert row["latest"] == best.latest

    def test_picks_the_higher_count_resolution_as_best(self, tmp_path):
        store = MarketDataStore(tmp_path / "market_data.db")
        base = datetime(2026, 7, 21, 14, 0, tzinfo=timezone.utc)
        store.upsert_bars("MES", "MESM6", "1min", "massive", [make_bar(base)])  # 1 bar
        store.upsert_bars(
            "MES", "MESM6", "5min", "massive",
            [make_bar(base), make_bar(base + timedelta(minutes=5)), make_bar(base + timedelta(minutes=10))],
        )  # 3 bars -- should win

        row = {r["product_code"]: r for r in store.product_coverage_summary()}["MES"]

        assert row["bars_stored"] == 3

    def test_empty_store_returns_empty_list(self, tmp_path):
        store = MarketDataStore(tmp_path / "market_data.db")
        assert store.product_coverage_summary() == []

    def test_many_products_returns_correct_results_at_scale(self, tmp_path):
        """The actual regression this fix guards against: KNOWN_ISSUES.md
        ISSUE-042 found market_data_overview() making ~2,800+ sequential
        round trips against real production data (~700 distinct
        product_code values from the turtle-trader historical archive,
        ISSUE-001) -- multiple minutes, confirmed via direct timing
        against the live database (product_coverage_summary's own source
        makes exactly two GROUP BY queries total, not one per product --
        readable directly from its implementation). This test instead
        confirms correctness holds up at a product count large enough to
        make an O(N) implementation's mistakes visible."""
        store = MarketDataStore(tmp_path / "market_data.db")
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for i in range(50):
            store.upsert_bars(f"PRODUCT{i}", f"PRODUCT{i}X", "5min", "massive", [make_bar(base)])

        result = store.product_coverage_summary()

        assert len(result) == 50
        assert {r["product_code"] for r in result} == {f"PRODUCT{i}" for i in range(50)}
        assert all(r["bars_stored"] == 1 for r in result)


class TestActiveContractTracking:
    def test_first_set_has_no_previous_and_logs_a_roll(self, tmp_path):
        store = MarketDataStore(tmp_path / "market_data.db")
        assert store.get_active_contract("MES") is None

        store.set_active_contract("MES", "MESU6")

        assert store.get_active_contract("MES") == "MESU6"
        rolls = store.contract_rolls("MES")
        assert len(rolls) == 1
        assert rolls[0]["from_contract"] is None
        assert rolls[0]["to_contract"] == "MESU6"

    def test_setting_the_same_contract_again_does_not_log_a_roll(self, tmp_path):
        store = MarketDataStore(tmp_path / "market_data.db")
        store.set_active_contract("MES", "MESU6")
        store.set_active_contract("MES", "MESU6")

        assert len(store.contract_rolls("MES")) == 1

    def test_setting_a_different_contract_logs_the_roll(self, tmp_path):
        store = MarketDataStore(tmp_path / "market_data.db")
        store.set_active_contract("MES", "MESM6")
        store.set_active_contract("MES", "MESU6")

        assert store.get_active_contract("MES") == "MESU6"
        rolls = store.contract_rolls("MES")
        assert len(rolls) == 2
        assert rolls[0]["from_contract"] == "MESM6"
        assert rolls[0]["to_contract"] == "MESU6"


class TestSyncRuns:
    def test_lifecycle_start_checkpoint_complete(self, tmp_path):
        store = MarketDataStore(tmp_path / "market_data.db")
        now = datetime(2026, 7, 21, 14, 0, tzinfo=timezone.utc)
        store.start_sync_run("run-1", "MES", "5min", "backfill", requested_start=now, requested_end=now)
        store.checkpoint_sync_run("run-1", bars_fetched=10, last_committed_through=now)
        store.complete_sync_run("run-1")

        runs = store.fetch_sync_runs("MES")
        assert runs[0]["status"] == "completed"
        assert runs[0]["bars_fetched"] == 10

    def test_fail_sync_run_records_the_error(self, tmp_path):
        store = MarketDataStore(tmp_path / "market_data.db")
        store.start_sync_run("run-1", "MES", "5min", "incremental")
        store.fail_sync_run("run-1", "boom")

        runs = store.fetch_sync_runs("MES")
        assert runs[0]["status"] == "failed"
        assert runs[0]["error_message"] == "boom"

    def test_last_committed_through_is_the_resume_point(self, tmp_path):
        """The resumability mechanism: an interrupted run's checkpoint is
        readable independent of whether that run ever completed."""
        store = MarketDataStore(tmp_path / "market_data.db")
        now = datetime(2026, 7, 21, 14, 0, tzinfo=timezone.utc)
        store.start_sync_run("run-1", "MES", "5min", "backfill")
        store.checkpoint_sync_run("run-1", bars_fetched=5, last_committed_through=now)
        # No complete_sync_run/fail_sync_run call -- simulates a crash mid-run.

        assert store.last_committed_through("MES", "5min") == now

    def test_last_committed_through_is_none_when_nothing_ever_checkpointed(self, tmp_path):
        store = MarketDataStore(tmp_path / "market_data.db")
        assert store.last_committed_through("MES", "5min") is None


class TestGaps:
    def test_record_and_fetch_unresolved_gaps(self, tmp_path):
        store = MarketDataStore(tmp_path / "market_data.db")
        start = datetime(2026, 7, 21, 14, 0, tzinfo=timezone.utc)
        end = start + timedelta(hours=1)
        store.record_gap("MES", "5min", start, end)

        gaps = store.fetch_gaps("MES")
        assert len(gaps) == 1
        assert gaps[0]["resolved_at"] is None

    def test_resolve_gaps_marks_contained_gaps_resolved(self, tmp_path):
        store = MarketDataStore(tmp_path / "market_data.db")
        start = datetime(2026, 7, 21, 14, 0, tzinfo=timezone.utc)
        end = start + timedelta(hours=1)
        store.record_gap("MES", "5min", start, end)

        store.resolve_gaps("MES", "5min", start, end)

        assert store.fetch_gaps("MES", unresolved_only=True) == []
        assert len(store.fetch_gaps("MES", unresolved_only=False)) == 1
