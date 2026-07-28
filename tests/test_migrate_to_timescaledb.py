"""Regression coverage for `tools/migrate_to_timescaledb.py`. Loaded
directly by file path -- `tools/` is intentionally not on pytest's
pythonpath (see `tests/test_tools_turtle_import.py`'s identical pattern).

Builds small synthetic `market_data.db`/`research.db` files (never the
real, gitignored production files) and migrates them into the live
compose `timescaledb` service. Skipped automatically when no reachable
server is configured, same pattern as `test_pg_market_data_store_live.py`.

Regression note: an early version of this script always reported "0
newly inserted" for every table, even on the very first real run --
`result.rowcount` is not reliable for a multi-row `INSERT ... ON CONFLICT
DO NOTHING` (the exact pitfall `pg_store.py::upsert_bars`'s own docstring
already documents and avoids via `RETURNING`); the data landed correctly
either way, but the reporting was wrong. Fixed the same way. Covered here
by `test_first_run_reports_real_insert_counts_not_zero`.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("psycopg")

from futures_bot.db.engine import database_url, dispose_engine  # noqa: E402
from futures_bot.db.health import check_database_health  # noqa: E402
from futures_bot.market_data.store import MarketDataStore  # noqa: E402
from futures_bot.models import Bar  # noqa: E402
from futures_bot.research.features import TradeRecord  # noqa: E402
from futures_bot.research.trade_store import TradeStore  # noqa: E402

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"

_MARKET_DATA_TABLES = ("bars", "sync_runs", "gaps", "active_contracts", "contract_rolls")
_RESEARCH_TABLES = (
    "trades", "optimization_trials", "runs", "reports", "jobs", "experiments", "ml_models",
    "model_deployments", "client_profiles", "import_staging", "client_import_fills",
    "client_open_lots", "import_history", "config_deployments",
)


def _load_migrate_module():
    spec = importlib.util.spec_from_file_location("migrate_to_timescaledb", TOOLS_DIR / "migrate_to_timescaledb.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["migrate_to_timescaledb"] = module
    spec.loader.exec_module(module)
    return module


def _live_server_reachable() -> bool:
    if not database_url():
        return False
    return check_database_health().ok


pytestmark = pytest.mark.skipif(
    not _live_server_reachable(),
    reason="No reachable Postgres/TimescaleDB configured via FUTURES_BOT_DATABASE_URL.",
)


@pytest.fixture()
def migrate():
    return _load_migrate_module()


@pytest.fixture()
def fixture_dbs(tmp_path):
    """A small, real (non-production) market_data.db/research.db pair --
    built through the actual store classes, exercising the same
    TEXT/JSON/Decimal-as-string encoding real production data has."""
    md_path = tmp_path / "fixture_market_data.db"
    research_path = tmp_path / "fixture_research.db"

    md_store = MarketDataStore(md_path)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    bars = [
        Bar(timestamp=start + timedelta(minutes=i), open=Decimal("100") + i, high=Decimal("101") + i,
            low=Decimal("99") + i, close=Decimal("100") + i, volume=1000 + i)
        for i in range(5)
    ]
    md_store.upsert_bars("MES", "MESZ26", "1m", "fixture", bars)
    md_store.set_active_contract("MES", "MESZ26")
    md_store.start_sync_run("run-md-1", "MES", "1m", "backfill")
    md_store.complete_sync_run("run-md-1")
    md_store.record_gap("MES", "1m", start + timedelta(minutes=100), start + timedelta(minutes=110))
    md_store.close()

    research_store = TradeStore(research_path)
    entry = datetime(2026, 1, 1, 9, 30, tzinfo=timezone.utc)
    research_store.insert_trades([TradeRecord(
        run_id="run-1", contract="MES", strategy="ema_crossover", strategy_params={"fast": 9},
        entry_time=entry, exit_time=entry + timedelta(minutes=15), side="long",
        entry_price=Decimal("5000.25"), exit_price=Decimal("5005.50"),
        gross_pnl=Decimal("21.00"), commission=Decimal("1.24"), net_pnl=Decimal("19.76"),
        holding_minutes=15.0, exit_reason="target", session_date="2026-01-01", day_of_week="Thursday",
        hour=9, entry_reason="ema_cross_up", entry_metadata={"fast_ema": "4998.00"}, outcome="win",
    )])
    research_store.insert_run(run_id="run-a", kind="backtest", status="running", strategy="ema_crossover",
                               contract="MES", strategy_params={})
    research_store.insert_client_profile(profile_id="prof-1", name="Alice")
    research_store.close()

    return md_path, research_path


@pytest.fixture(autouse=True)
def _clean_destination_around_test(live_database_url):
    dispose_engine()
    yield
    from futures_bot.db.engine import get_engine
    from sqlalchemy import text

    with get_engine().begin() as conn:
        conn.execute(text(f"TRUNCATE {', '.join(_MARKET_DATA_TABLES + _RESEARCH_TABLES)}"))
    dispose_engine()


class TestDryRun:
    def test_dry_run_writes_nothing(self, migrate, fixture_dbs):
        md_path, research_path = fixture_dbs
        exit_code = migrate.main(["--dry-run", "--market-data-db", str(md_path), "--research-db", str(research_path)])
        assert exit_code == 0

        from futures_bot.db.engine import get_engine
        from sqlalchemy import func, select

        from futures_bot.db.schema import bars

        with get_engine().connect() as conn:
            count = conn.execute(select(func.count()).select_from(bars)).scalar_one()
        assert count == 0


class TestRealMigration:
    def test_first_run_reports_real_insert_counts_not_zero(self, migrate, fixture_dbs):
        """Checks the actual `(source_count, inserted)` tuples migrate_table
        returns -- not stdout text (brittle: e.g. "20 newly inserted"
        contains the substring "0 newly inserted", a false negative for a
        naive text search)."""
        from futures_bot.db.engine import get_engine

        md_path, research_path = fixture_dbs
        engine = get_engine()
        md_results = migrate.migrate_market_data(md_path, engine, dry_run=False)
        research_results = migrate.migrate_research(research_path, engine, dry_run=False)

        for table_name, (source_count, inserted) in {**md_results, **research_results}.items():
            assert inserted == source_count, f"{table_name}: expected all {source_count} rows newly inserted, got {inserted}"
        # fixture_dbs only populates these tables -- every one must show a
        # real nonzero insert count (not the "always reports 0" bug).
        for table_name in ("bars", "sync_runs", "gaps", "active_contracts", "trades", "runs", "client_profiles"):
            _source_count, inserted = {**md_results, **research_results}[table_name]
            assert inserted > 0, f"{table_name}: expected a nonzero real insert on the first run"

    def test_data_lands_correctly_with_right_types(self, migrate, fixture_dbs):
        md_path, research_path = fixture_dbs
        migrate.main(["--yes", "--market-data-db", str(md_path), "--research-db", str(research_path)])

        from futures_bot.market_data.pg_store import PgMarketDataStore
        from futures_bot.research.pg_trade_store import PgTradeStore

        md_store = PgMarketDataStore()
        bars = md_store.fetch_bars("MES", "1m")
        assert len(bars) == 5
        assert isinstance(bars[0].open, Decimal)

        rolls = md_store.contract_rolls("MES")
        assert len(rolls) == 1
        assert isinstance(rolls[0]["rolled_at"], str)

        trade_store = PgTradeStore()
        trades = trade_store.fetch_trades(run_id="run-1")
        assert len(trades) == 1
        assert isinstance(trades[0]["entry_price"], Decimal)
        assert trades[0]["strategy_params"] == {"fast": 9}
        assert isinstance(trades[0]["entry_time"], str)

    def test_rerun_is_idempotent_no_duplicate_rows(self, migrate, fixture_dbs):
        from futures_bot.db.engine import get_engine

        md_path, research_path = fixture_dbs
        engine = get_engine()
        migrate.migrate_market_data(md_path, engine, dry_run=False)
        migrate.migrate_research(research_path, engine, dry_run=False)

        second_md = migrate.migrate_market_data(md_path, engine, dry_run=False)
        second_research = migrate.migrate_research(research_path, engine, dry_run=False)
        for table_name, (_source_count, inserted) in {**second_md, **second_research}.items():
            assert inserted == 0, f"{table_name}: re-run should insert 0 new rows, got {inserted}"

        from futures_bot.market_data.pg_store import PgMarketDataStore

        assert PgMarketDataStore().coverage("MES", "1m").count == 5  # not 10

    def test_bars_id_is_freshly_generated_not_copied_from_null_source(self, migrate, fixture_dbs):
        """KNOWN_ISSUES.md ISSUE-004: SQLite's `bars.id` is NULL for every
        row -- this migration must never try to copy that NULL into the
        destination's NOT NULL Identity column."""
        md_path, research_path = fixture_dbs
        migrate.main(["--yes", "--market-data-db", str(md_path), "--research-db", str(research_path)])

        from futures_bot.db.engine import get_engine
        from sqlalchemy import select

        from futures_bot.db.schema import bars

        with get_engine().connect() as conn:
            ids = [row[0] for row in conn.execute(select(bars.c.id))]
        assert all(isinstance(i, int) and i > 0 for i in ids)
