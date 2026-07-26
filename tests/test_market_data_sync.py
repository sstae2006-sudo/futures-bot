"""Tests for `market_data.sync` -- backfill (contract-roll-aware),
incremental sync (roll detection + resume-from-coverage), gap
verification (real gaps vs. expected market closures), and gap repair.
Both the Contracts API and the aggs endpoint are mocked via one
`FakeSyncSession` that dispatches on URL, so nothing here touches the
network.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from futures_bot.market_data.contracts_client import MassiveContractsClient
from futures_bot.market_data.store import MarketDataStore
from futures_bot.market_data.sync import backfill, repair_gaps, sync_incremental, verify


def _agg_bar(window_start: datetime, close: float = 7500.0):
    ns = int(window_start.timestamp() * 1e9)
    return {"window_start": ns, "open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 500}


def _single(ticker, first_trade_date, last_trade_date, query_date):
    return {
        "active": True, "date": query_date, "name": f"{ticker} Future", "product_code": "MES",
        "ticker": ticker, "type": "single",
        "first_trade_date": first_trade_date, "last_trade_date": last_trade_date,
    }


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class FakeSyncSession:
    """Routes `.get(url, params, timeout)` to either the aggs endpoint
    (keyed by ticker, returning canned bars for any requested window) or
    the Contracts API (keyed by query date), recording every call made."""

    def __init__(self, aggs_by_ticker: dict[str, list[dict]] | None = None, contracts_by_date: dict[str, dict] | None = None):
        self.aggs_by_ticker = aggs_by_ticker or {}
        self.contracts_by_date = contracts_by_date or {}
        self.calls: list[dict] = []

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": dict(params or {})})
        if "/contracts" in url:
            query_date = (params or {}).get("date")
            return FakeResponse(self.contracts_by_date.get(query_date, {"results": [], "status": "OK"}))
        # aggs endpoint: URL embeds the ticker, e.g. .../aggs/MESU6
        ticker = url.rsplit("/", 1)[-1]
        return FakeResponse({"results": self.aggs_by_ticker.get(ticker, []), "status": "OK"})


class TestBackfill:
    def test_resolves_front_month_per_window_and_stores_correct_contract(self, tmp_path):
        store = MarketDataStore(tmp_path / "market_data.db")
        m6_bars = [_agg_bar(datetime(2026, 6, 1, 14, 0, tzinfo=timezone.utc))]
        u6_bars = [_agg_bar(datetime(2026, 6, 25, 14, 0, tzinfo=timezone.utc))]
        session = FakeSyncSession(
            aggs_by_ticker={"MESM6": m6_bars, "MESU6": u6_bars},
            contracts_by_date={"2026-06-01": {
                "status": "OK",
                "results": [
                    _single("MESM6", "2025-12-19", "2026-06-19", "2026-06-01"),
                    _single("MESU6", "2026-03-20", "2026-09-18", "2026-06-01"),
                ],
            }},
        )
        contracts_client = MassiveContractsClient("key", session=session)

        result = backfill(
            store, "key", "MES", "5min",
            datetime(2026, 6, 1, tzinfo=timezone.utc), datetime(2026, 7, 1, tzinfo=timezone.utc),
            session=session, contracts_client=contracts_client,
        )

        assert result.bars_fetched == 2
        assert set(result.contracts_used) == {"MESM6", "MESU6"}
        stored = store.fetch_bars("MES", "5min")
        assert {b.close for b in stored} == {Decimal("7500.0"), Decimal("7500.0")}
        assert store.contracts_stored("MES") == ["MESM6", "MESU6"]

        runs = store.fetch_sync_runs("MES")
        assert runs[0]["status"] == "completed"
        assert runs[0]["kind"] == "backfill"

    def test_a_failed_backfill_leaves_a_resumable_checkpoint(self, tmp_path):
        store = MarketDataStore(tmp_path / "market_data.db")

        class ExplodingContractsClient:
            def front_month_schedule(self, *a, **k):
                raise RuntimeError("Contracts API is down")

        with pytest.raises(RuntimeError, match="Contracts API is down"):
            backfill(
                store, "key", "MES", "5min",
                datetime(2026, 6, 1, tzinfo=timezone.utc), datetime(2026, 7, 1, tzinfo=timezone.utc),
                session=FakeSyncSession(), contracts_client=ExplodingContractsClient(),
            )

        runs = store.fetch_sync_runs("MES")
        assert runs[0]["status"] == "failed"
        assert "Contracts API is down" in runs[0]["error_message"]

    def test_resumes_from_the_last_checkpoint_not_the_original_start(self, tmp_path):
        """Simulates an interrupted backfill by pre-seeding a checkpoint
        past the requested start, then confirms the next call only asks
        the Contracts API about the remaining window."""
        store = MarketDataStore(tmp_path / "market_data.db")
        store.start_sync_run("prior-run", "MES", "5min", "backfill")
        store.checkpoint_sync_run(
            "prior-run", bars_fetched=1, last_committed_through=datetime(2026, 6, 19, 23, 59, 59, tzinfo=timezone.utc)
        )

        session = FakeSyncSession(
            aggs_by_ticker={"MESU6": [_agg_bar(datetime(2026, 6, 25, 14, 0, tzinfo=timezone.utc))]},
            contracts_by_date={"2026-06-20": {
                "status": "OK",
                "results": [_single("MESU6", "2026-03-20", "2026-09-18", "2026-06-20")],
            }},
        )
        contracts_client = MassiveContractsClient("key", session=session)

        result = backfill(
            store, "key", "MES", "5min",
            datetime(2026, 6, 1, tzinfo=timezone.utc), datetime(2026, 7, 1, tzinfo=timezone.utc),
            session=session, contracts_client=contracts_client,
        )

        # Only the schedule query for the resumed window (2026-06-20 onward)
        # should have happened -- never one for 2026-06-01.
        queried_dates = {c["params"].get("date") for c in session.calls if "/contracts" in c["url"]}
        assert "2026-06-01" not in queried_dates
        assert result.contracts_used == ["MESU6"]


class TestSyncIncremental:
    def test_fetches_forward_from_existing_coverage(self, tmp_path):
        store = MarketDataStore(tmp_path / "market_data.db")
        existing_latest = datetime(2026, 7, 22, 14, 0, tzinfo=timezone.utc)
        store.upsert_bars("MES", "MESU6", "5min", "massive", [
            _fake_bar(existing_latest)
        ])

        now = datetime(2026, 7, 22, 15, 0, tzinfo=timezone.utc)
        session = FakeSyncSession(
            aggs_by_ticker={"MESU6": [_agg_bar(existing_latest + timedelta(minutes=5))]},
            contracts_by_date={"2026-07-22": {
                "status": "OK",
                "results": [_single("MESU6", "2025-06-20", "2026-09-18", "2026-07-22")],
            }},
        )
        contracts_client = MassiveContractsClient("key", session=session)

        result = sync_incremental(store, "key", "MES", "5min", now=now, session=session, contracts_client=contracts_client)

        assert result.bars_fetched == 1
        assert result.rolled == (None, "MESU6")  # first time this product's active contract is set
        coverage = store.coverage("MES", "5min")
        assert coverage.count == 2

        # The aggs request should have started strictly after existing coverage.
        aggs_call = next(c for c in session.calls if "/aggs/" in c["url"])
        assert aggs_call["params"]["window_start.gte"] > existing_latest.isoformat().replace("+00:00", "Z")

    def test_detects_a_roll(self, tmp_path):
        store = MarketDataStore(tmp_path / "market_data.db")
        store.set_active_contract("MES", "MESM6")

        now = datetime(2026, 7, 22, 15, 0, tzinfo=timezone.utc)
        session = FakeSyncSession(
            aggs_by_ticker={"MESU6": []},
            contracts_by_date={"2026-07-22": {
                "status": "OK",
                "results": [_single("MESU6", "2025-06-20", "2026-09-18", "2026-07-22")],
            }},
        )
        contracts_client = MassiveContractsClient("key", session=session)

        result = sync_incremental(store, "key", "MES", "5min", now=now, session=session, contracts_client=contracts_client)

        assert result.rolled == ("MESM6", "MESU6")
        assert store.get_active_contract("MES") == "MESU6"

    def test_a_failure_fails_the_sync_run(self, tmp_path):
        store = MarketDataStore(tmp_path / "market_data.db")

        class ExplodingContractsClient:
            def active_contract(self, *a, **k):
                raise RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            sync_incremental(
                store, "key", "MES", "5min",
                session=FakeSyncSession(), contracts_client=ExplodingContractsClient(),
            )

        runs = store.fetch_sync_runs("MES")
        assert runs[0]["status"] == "failed"


def _fake_bar(ts: datetime):
    from futures_bot.models import Bar
    return Bar(timestamp=ts, open=Decimal("7500"), high=Decimal("7501"), low=Decimal("7499"), close=Decimal("7500"), volume=500)


class TestVerify:
    def test_flags_a_real_intraday_gap(self, tmp_path):
        store = MarketDataStore(tmp_path / "market_data.db")
        # A Wednesday during normal CME trading hours (10:00/10:20 CT == 15:00/15:20 UTC in July).
        base = datetime(2026, 7, 22, 15, 0, tzinfo=timezone.utc)
        store.upsert_bars("MES", "MESU6", "5min", "massive", [_fake_bar(base), _fake_bar(base + timedelta(minutes=20))])

        report = verify(store, "MES", "5min")

        assert len(report.new_gaps) == 1
        assert store.fetch_gaps("MES", unresolved_only=True)

    def test_does_not_flag_the_daily_maintenance_halt(self, tmp_path):
        """15:55 CT -> 17:00 CT (20:55 UTC -> 22:00 UTC) is the ordinary
        16:00-17:00 CT maintenance halt, not a missed sync."""
        store = MarketDataStore(tmp_path / "market_data.db")
        before_halt = datetime(2026, 7, 22, 20, 55, tzinfo=timezone.utc)
        after_halt = datetime(2026, 7, 22, 22, 0, tzinfo=timezone.utc)
        store.upsert_bars("MES", "MESU6", "5min", "massive", [_fake_bar(before_halt), _fake_bar(after_halt)])

        report = verify(store, "MES", "5min")

        assert report.new_gaps == []

    def test_does_not_flag_a_weekend_closure(self, tmp_path):
        store = MarketDataStore(tmp_path / "market_data.db")
        # Last real bar before the Friday close (window 15:55-16:00 CT) and
        # the first real bar after Sunday's reopen (window 17:00-17:05 CT)
        # -- the normal shape of a healthy dataset spanning a weekend, not
        # an artificial boundary sitting exactly on the closure instant.
        friday_last_bar = datetime(2026, 7, 24, 20, 55, tzinfo=timezone.utc)
        sunday_first_bar = datetime(2026, 7, 26, 22, 0, tzinfo=timezone.utc)
        store.upsert_bars("MES", "MESU6", "5min", "massive", [_fake_bar(friday_last_bar), _fake_bar(sunday_first_bar)])

        report = verify(store, "MES", "5min")

        assert report.new_gaps == []

    def test_does_not_flag_a_holiday_closure(self, tmp_path):
        """2026-11-26 (Thanksgiving, a Thursday) is a full CME closure --
        picked instead of a fixed-date holiday like Christmas because it
        falls mid-week, so the gap it produces can't be confused with (or
        silently pass because of) the separate weekend-closure check."""
        store = MarketDataStore(tmp_path / "market_data.db")
        # Session close on Wednesday 2026-11-25 (15:55-16:00 CT == 21:55-22:00 UTC,
        # standard time -- DST ended 2026-11-01) and the next session's reopen
        # at 17:00 CT Thursday 2026-11-26 (23:00 UTC).
        last_bar_before_holiday = datetime(2026, 11, 25, 21, 55, tzinfo=timezone.utc)
        first_bar_after_holiday = datetime(2026, 11, 26, 23, 0, tzinfo=timezone.utc)
        store.upsert_bars(
            "MES", "MESZ6", "5min", "massive",
            [_fake_bar(last_bar_before_holiday), _fake_bar(first_bar_after_holiday)],
        )

        report = verify(store, "MES", "5min")

        assert report.new_gaps == []

    def test_flags_a_gap_that_barely_exceeds_the_threshold(self, tmp_path):
        """Regression: with the default step_minutes=15 (5min resolution),
        a gap of 15m24s used to slip through unflagged -- `int(15.4)` truncates
        to exactly 15, and the old `range(15, 15, ...)` probe window was
        empty, so `any()` over it was vacuously False and the gap was
        (wrongly) treated as an expected closure rather than a real one."""
        store = MarketDataStore(tmp_path / "market_data.db")
        base = datetime(2026, 7, 22, 15, 0, tzinfo=timezone.utc)  # 10:00 CT, mid-session
        store.upsert_bars(
            "MES", "MESU6", "5min", "massive",
            [_fake_bar(base), _fake_bar(base + timedelta(minutes=15, seconds=24))],
        )

        report = verify(store, "MES", "5min")

        assert len(report.new_gaps) == 1
        assert store.fetch_gaps("MES", unresolved_only=True)

    def test_does_not_re_record_an_already_known_gap(self, tmp_path):
        store = MarketDataStore(tmp_path / "market_data.db")
        base = datetime(2026, 7, 22, 15, 0, tzinfo=timezone.utc)
        store.upsert_bars("MES", "MESU6", "5min", "massive", [_fake_bar(base), _fake_bar(base + timedelta(minutes=20))])

        first = verify(store, "MES", "5min")
        second = verify(store, "MES", "5min")

        assert len(first.new_gaps) == 1
        assert len(second.new_gaps) == 0  # already known, not reported as "new" again
        assert second.total_open_gaps == 1


class TestRepairGaps:
    def test_refetches_and_resolves_a_gap(self, tmp_path):
        store = MarketDataStore(tmp_path / "market_data.db")
        gap_start = datetime(2026, 7, 22, 15, 0, tzinfo=timezone.utc)
        gap_end = datetime(2026, 7, 22, 15, 20, tzinfo=timezone.utc)
        store.record_gap("MES", "5min", gap_start, gap_end)

        session = FakeSyncSession(
            aggs_by_ticker={"MESU6": [_agg_bar(gap_start + timedelta(minutes=5)), _agg_bar(gap_start + timedelta(minutes=10))]},
            contracts_by_date={"2026-07-22": {
                "status": "OK",
                "results": [_single("MESU6", "2025-06-20", "2026-09-18", "2026-07-22")],
            }},
        )
        contracts_client = MassiveContractsClient("key", session=session)

        report = repair_gaps(store, "key", "MES", "5min", session=session, contracts_client=contracts_client)

        assert report.gaps_attempted == 1
        assert report.gaps_resolved == 1
        assert report.bars_recovered == 2
        assert store.fetch_gaps("MES", unresolved_only=True) == []

    def test_a_gap_that_recovers_nothing_stays_open(self, tmp_path):
        """A real market holiday: re-fetching finds nothing, so the gap
        must not be silently marked resolved."""
        store = MarketDataStore(tmp_path / "market_data.db")
        gap_start = datetime(2026, 7, 22, 15, 0, tzinfo=timezone.utc)
        gap_end = datetime(2026, 7, 22, 15, 20, tzinfo=timezone.utc)
        store.record_gap("MES", "5min", gap_start, gap_end)

        session = FakeSyncSession(
            aggs_by_ticker={"MESU6": []},
            contracts_by_date={"2026-07-22": {
                "status": "OK",
                "results": [_single("MESU6", "2025-06-20", "2026-09-18", "2026-07-22")],
            }},
        )
        contracts_client = MassiveContractsClient("key", session=session)

        report = repair_gaps(store, "key", "MES", "5min", session=session, contracts_client=contracts_client)

        assert report.gaps_resolved == 0
        assert len(store.fetch_gaps("MES", unresolved_only=True)) == 1
