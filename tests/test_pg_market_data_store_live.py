"""Behavior verification for `PgMarketDataStore` against a *real*
Postgres/TimescaleDB server -- the companion `test_market_data_store_parity.py`
explicitly defers to. That module only checks method *signatures* match;
this module actually calls every method and checks the SQL does the right
thing (idempotent upserts, roll bookkeeping, gap tracking, hypertable
conversion, etc.) -- see PROJECT_STATE.md's "Manually verify
PgMarketDataStore against live Postgres" writeup for the bug this first
caught (`bars.id` needed `Identity()`, not bare `autoincrement=True`,
since it isn't the table's primary key).

Skipped automatically -- not xfail, not a hard failure -- whenever no
reachable server is configured via `FUTURES_BOT_DATABASE_URL`, so the
default single-developer test run (`pytest -q`, no `db` extra, no Docker)
is completely unaffected. Run against the `timescaledb` compose service
(see `deploy/docker-compose.yml`, `TEAM_DEPLOYMENT.md`) to actually
exercise this module:

    docker compose -f deploy/docker-compose.yml up -d timescaledb
    export FUTURES_BOT_DATABASE_URL=postgresql+psycopg://futures_bot:futures_bot_dev_only@127.0.0.1:5432/futures_bot
    pytest tests/test_pg_market_data_store_live.py -v
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("psycopg")

from futures_bot.db.engine import database_url, dispose_engine  # noqa: E402
from futures_bot.db.health import check_database_health  # noqa: E402
from futures_bot.models import Bar  # noqa: E402


def _live_server_reachable() -> bool:
    if not database_url():
        return False
    return check_database_health().ok


pytestmark = pytest.mark.skipif(
    not _live_server_reachable(),
    reason=(
        f"No reachable Postgres/TimescaleDB at {os.environ.get('FUTURES_BOT_DATABASE_URL', '<unset>')} "
        "-- start the compose timescaledb service to run this module."
    ),
)


def _make_bars(n: int, start: datetime) -> list[Bar]:
    out = []
    for i in range(n):
        ts = start + timedelta(minutes=i)
        price = Decimal("100.00") + Decimal(i)
        out.append(Bar(timestamp=ts, open=price, high=price + 1, low=price - 1, close=price, volume=1000 + i))
    return out


@pytest.fixture()
def store(live_database_url):
    from futures_bot.market_data.pg_store import PgMarketDataStore

    s = PgMarketDataStore()
    s.ensure_schema()
    yield s
    # Truncate rather than drop: this may be a shared team-dev instance,
    # and dropping tables would fight Alembic's ownership of the schema.
    from sqlalchemy import text

    with s._engine.begin() as conn:  # noqa: SLF001 -- test-only, direct cleanup
        conn.execute(text("TRUNCATE bars, sync_runs, gaps, active_contracts, contract_rolls"))
    s.close()


@pytest.fixture(scope="module", autouse=True)
def _dispose_engine_after_module():
    yield
    dispose_engine()


class TestBarsRoundTrip:
    def test_upsert_is_idempotent_and_returns_new_row_count(self, store):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        bars = _make_bars(50, start)

        assert store.upsert_bars("MES", "MESZ26", "1m", "test", bars) == 50
        assert store.upsert_bars("MES", "MESZ26", "1m", "test", bars) == 0

        overlapping = _make_bars(60, start)  # first 50 already stored, 10 new
        assert store.upsert_bars("MES", "MESZ26", "1m", "test", overlapping) == 10

    def test_coverage_and_fetch_reflect_stored_bars(self, store):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        bars = _make_bars(60, start)
        store.upsert_bars("MES", "MESZ26", "1m", "test", bars)

        cov = store.coverage("MES", "1m")
        assert cov.count == 60
        assert cov.earliest == start
        assert cov.latest == start + timedelta(minutes=59)

        fetched = store.fetch_bars("MES", "1m")
        assert len(fetched) == 60
        assert isinstance(fetched[0].open, Decimal)
        assert fetched[0].timestamp < fetched[-1].timestamp

        windowed = store.fetch_bars("MES", "1m", start=start, end=start + timedelta(minutes=9))
        assert len(windowed) == 10

    def test_contracts_products_resolutions_introspection(self, store):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        store.upsert_bars("MES", "MESZ26", "1m", "test", _make_bars(5, start))

        assert "MESZ26" in store.contracts_stored("MES")
        assert "MES" in store.all_products()
        assert "1m" in store.resolutions_stored("MES")


class TestActiveContractAndRolls:
    def test_set_active_contract_records_every_real_transition(self, store):
        assert store.get_active_contract("MES") is None

        store.set_active_contract("MES", "MESZ26")
        assert store.get_active_contract("MES") == "MESZ26"

        store.set_active_contract("MES", "MESH27")
        assert store.get_active_contract("MES") == "MESH27"

        store.set_active_contract("MES", "MESH27")  # no-op: previous == contract

        rolls = store.contract_rolls("MES")
        assert len(rolls) == 2
        assert rolls[0]["from_contract"] == "MESZ26" and rolls[0]["to_contract"] == "MESH27"
        assert rolls[1]["from_contract"] is None and rolls[1]["to_contract"] == "MESZ26"


class TestSyncRunLog:
    def test_full_sync_run_lifecycle(self, store):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        run_id = str(uuid.uuid4())

        store.start_sync_run(run_id, "MES", "1m", "backfill", requested_start=start, requested_end=start + timedelta(days=1))
        store.checkpoint_sync_run(run_id, bars_fetched=60, last_committed_through=start + timedelta(minutes=59))
        store.complete_sync_run(run_id)

        assert store.last_committed_through("MES", "1m") == start + timedelta(minutes=59)

        runs = store.fetch_sync_runs("MES")
        assert len(runs) == 1
        assert runs[0]["status"] == "completed"

    def test_fetch_sync_runs_timestamps_are_strings_not_datetimes(self, store):
        """Regression test: `sync_runs.started_at`/`completed_at` are native
        Postgres TIMESTAMPTZ, but `MarketDataStore`'s SQLite equivalent
        stores/returns TEXT -- every caller up the stack (market_data_service.py,
        api/schemas.py::SyncRunOut) assumes a plain string. Found via a real
        500 (pydantic ValidationError) hitting /api/market-data/overview
        against a live server, not from a signature check."""
        run_id = str(uuid.uuid4())
        store.start_sync_run(run_id, "MES", "1m", "backfill")
        store.complete_sync_run(run_id)

        run = store.fetch_sync_runs("MES")[0]
        assert isinstance(run["started_at"], str)
        assert isinstance(run["completed_at"], str)

    def test_failed_sync_run_records_error_message(self, store):
        run_id = str(uuid.uuid4())
        store.start_sync_run(run_id, "MES", "1m", "backfill")
        store.fail_sync_run(run_id, "simulated vendor timeout")

        runs = [r for r in store.fetch_sync_runs("MES", limit=10) if r["id"] == run_id]
        assert runs[0]["status"] == "failed"
        assert runs[0]["error_message"] == "simulated vendor timeout"


class TestGaps:
    def test_record_and_resolve_gap(self, store):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        gap_start, gap_end = start + timedelta(minutes=100), start + timedelta(minutes=110)

        store.record_gap("MES", "1m", gap_start, gap_end)
        assert len(store.fetch_gaps("MES", unresolved_only=True)) == 1

        store.resolve_gaps("MES", "1m", gap_start, gap_end)
        assert len(store.fetch_gaps("MES", unresolved_only=True)) == 0
        assert len(store.fetch_gaps("MES", unresolved_only=False)) == 1

    def test_fetch_gaps_timestamps_are_strings_not_datetimes(self, store):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        store.record_gap("MES", "1m", start, start + timedelta(minutes=10))

        gap = store.fetch_gaps("MES", unresolved_only=True)[0]
        assert isinstance(gap["gap_start"], str)
        assert isinstance(gap["gap_end"], str)
        assert isinstance(gap["detected_at"], str)


class TestContractRolls:
    def test_contract_rolls_timestamps_are_strings_not_datetimes(self, store):
        store.set_active_contract("MES", "MESZ26")

        roll = store.contract_rolls("MES")[0]
        assert isinstance(roll["rolled_at"], str)


class TestMiscProperties:
    def test_location_is_credential_safe(self, store):
        assert "futures_bot" in store.location
        # Whatever password is configured must never appear in the
        # human-readable string -- check against the actual env value
        # rather than hardcoding the dev-compose default.
        url = database_url() or ""
        if "://" in url and "@" in url:
            creds = url.split("://", 1)[1].split("@", 1)[0]
            if ":" in creds:
                password = creds.split(":", 1)[1]
                if password:
                    assert password not in store.location

    def test_size_bytes_is_positive_once_tables_exist(self, store):
        assert store.size_bytes > 0


class TestHypertableConversion:
    def test_bars_is_a_real_timescaledb_hypertable(self, store):
        from sqlalchemy import text

        with store._engine.connect() as conn:  # noqa: SLF001 -- test-only introspection
            row = conn.execute(
                text("SELECT hypertable_name FROM timescaledb_information.hypertables WHERE hypertable_name = 'bars'")
            ).first()
        assert row is not None, "bars was not converted into a hypertable by ensure_schema()/the Alembic migration"
