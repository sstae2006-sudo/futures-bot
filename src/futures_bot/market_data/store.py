"""The local market-data database.

SQLite via the standard library, plain SQL, no ORM -- the same choice
`research/trade_store.py` already made and for the same reason (this
project's dependency list is deliberately minimal, and the schema here is
small enough that an ORM buys nothing back). All SQL lives inside
`MarketDataStore`; nothing outside this module writes a query. That's the
seam a future PostgreSQL backend would slot behind -- a second store class
with the same method signatures, swapped in behind `get_market_data_store()`
in `api/market_data_store.py`-style wiring, not a rewrite of every caller.

**One row per (product, resolution, timestamp), not per contract.** A
naive schema keyed on the specific expiry ticker (e.g. "MESU6") would let
two overlapping contracts both claim the same calendar timestamp during a
rollover window -- exactly the manual problem this session's own MES
dataset hit at the M6->U6 rollover, worked around by hand with a fixed
cutover date. Here that's a write-time invariant instead of a read-time
stitching problem: `sync.py`'s backfill/incremental logic decides, for each
timestamp, which single contract was actually front-month that day (via
`contracts_client.py`), and only ever writes *that* contract's bar for it.
`contract` is stored for traceability (which ticker this bar actually came
from) but is not part of the identity key -- querying a product's bars
across contracts is a plain range scan, no stitching required at read time.

Money fields are TEXT (exact Decimal string), matching `models.Bar` and
`trade_store.py`'s own convention -- SQLite's REAL would reintroduce the
float-precision risk `models.py`'s docstring exists to avoid.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional, Sequence

from ..models import Bar

#: Separate from `research.db` (see `api/store.py`) on purpose: research.db
#: is a research artifact -- experiment/backtest/job history -- that's
#: reasonable to delete and rebuild; this database is meant to become the
#: platform's primary source of historical bars, with a different (much
#: longer, "never delete this") lifecycle. Overridable so tests, and anyone
#: running more than one instance of this project, don't collide on the
#: default path.
DEFAULT_DB_PATH = Path("market_data.db")


def default_db_path() -> Path:
    return Path(os.environ.get("FUTURES_BOT_MARKET_DATA_DB", str(DEFAULT_DB_PATH)))


def get_market_data_store(db_path: Optional[Path] = None):
    """The one seam every caller should use instead of constructing
    `MarketDataStore(default_db_path())` directly -- this module's own
    docstring above names this exact function as the extension point a
    Postgres backend would slot behind. Lives here (not in `api/`),
    mirroring exactly how `research/trade_store.py::default_db_path`
    stays in that module rather than only in `api/store.py`: consumers
    below `api/` in the dependency direction (`market_data/scheduler.py`,
    `research_server/paper_trader.py`) need this too, and per
    `docs/ARCHITECTURE.md`'s one-way dependency rule they must never
    import from `api/`. `api/market_data_store.py` re-exports this
    unchanged, as a thin wrapper, purely so `api/`-layer code can spell
    the import path the way the rest of that package's imports read
    (matching `api/store.py`'s identical thin-wrapper role over this
    same function's sibling, `TradeStore`'s `get_store()`).

    Returns a fresh store per call, never cached (SQLite connections
    aren't safe to share across threads -- same reasoning `api/store.py`
    documents for `TradeStore`). Which class comes back:

    - `FUTURES_BOT_DATABASE_URL` unset (default, every existing single-
      developer setup): today's `MarketDataStore(db_path or
      default_db_path())`, byte-identical to before this function
      existed.
    - `FUTURES_BOT_DATABASE_URL` set (team/production deployment):
      `PgMarketDataStore`, backed by the shared pooled Engine --
      `db_path` is meaningless there (Postgres has no per-call file
      path) and is ignored.

    ``db_path`` exists only so a caller that needs a *specific* SQLite
    file rather than the env-resolved default -- `MarketDataScheduler`,
    whose constructor takes an explicit path precisely so tests can
    point it at an isolated `tmp_path` -- can still get that exact
    behavior through this one seam instead of bypassing it. Every other
    caller omits it.

    Both imports of the Postgres-only pieces (`..db.engine`,
    `.pg_store`) happen inside this function, only on the branch that
    needs them, so a plain SQLite setup -- including the entire existing
    test suite -- never needs the `db` extra installed.
    """
    from ..db.engine import database_url

    if database_url():
        from .pg_store import PgMarketDataStore

        return PgMarketDataStore()
    return MarketDataStore(db_path or default_db_path())

_SCHEMA = """
CREATE TABLE IF NOT EXISTS bars (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    product_code  TEXT NOT NULL,   -- generic product, e.g. 'MES' (contracts.CONTRACTS key)
    contract      TEXT NOT NULL,   -- the specific expiry ticker this bar actually came from, e.g. 'MESU6'
    resolution    TEXT NOT NULL,   -- e.g. '5min', '1h'
    timestamp     TEXT NOT NULL,   -- ISO 8601, UTC
    open          TEXT NOT NULL,
    high          TEXT NOT NULL,
    low           TEXT NOT NULL,
    close         TEXT NOT NULL,
    volume        INTEGER NOT NULL,
    source        TEXT NOT NULL,   -- e.g. 'massive'
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
-- The real identity: at most one bar per product+resolution+timestamp,
-- regardless of which contract it came from. INSERT OR IGNORE against this
-- index is what makes "never download duplicate data" a DB guarantee
-- rather than a promise the sync engine has to keep on its own.
CREATE UNIQUE INDEX IF NOT EXISTS idx_bars_identity ON bars(product_code, resolution, timestamp);
CREATE INDEX IF NOT EXISTS idx_bars_contract ON bars(contract);

-- One row per sync attempt (backfill/incremental/repair), updated as it
-- progresses -- not just written once at the end -- so a crash mid-backfill
-- leaves a row a caller can see was interrupted, and `last_committed_through`
-- is exactly where `sync.py` resumes from rather than the start.
CREATE TABLE IF NOT EXISTS sync_runs (
    id                     TEXT PRIMARY KEY,
    product_code           TEXT NOT NULL,
    resolution             TEXT NOT NULL,
    kind                   TEXT NOT NULL,   -- 'backfill' | 'incremental' | 'repair'
    status                 TEXT NOT NULL,   -- 'running' | 'completed' | 'failed'
    requested_start        TEXT,
    requested_end          TEXT,
    bars_fetched           INTEGER NOT NULL DEFAULT 0,
    last_committed_through TEXT,
    error_message          TEXT,
    started_at             TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at           TEXT
);
CREATE INDEX IF NOT EXISTS idx_sync_runs_product ON sync_runs(product_code, resolution, started_at);

-- Detected (and, once repaired, resolved) coverage gaps -- distinct from a
-- *download* failure (see sync_runs.error_message): a gap is "we don't have
-- data for this window and don't know why," which can happen from a market
-- holiday (expected, not a bug) or a missed sync (a real gap).
CREATE TABLE IF NOT EXISTS gaps (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    product_code  TEXT NOT NULL,
    resolution    TEXT NOT NULL,
    gap_start     TEXT NOT NULL,
    gap_end       TEXT NOT NULL,
    detected_at   TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_gaps_product ON gaps(product_code, resolution);

-- The contract `sync.py` believes is currently front-month for a product --
-- compared against a fresh Contracts API lookup on every incremental sync
-- to detect a roll. One row per product, overwritten in place; the
-- append-only history of roll events lives in contract_rolls below.
CREATE TABLE IF NOT EXISTS active_contracts (
    product_code TEXT PRIMARY KEY,
    contract     TEXT NOT NULL,
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS contract_rolls (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    product_code  TEXT NOT NULL,
    from_contract TEXT,
    to_contract   TEXT NOT NULL,
    rolled_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_contract_rolls_product ON contract_rolls(product_code);
"""


@dataclass(frozen=True)
class Coverage:
    """What's already stored for one product+resolution."""

    count: int
    earliest: Optional[datetime]
    latest: Optional[datetime]


class MarketDataStore:
    """Owns one SQLite connection. Not thread-safe -- open one per thread/
    request, the same convention `api/store.py::get_store()` documents for
    `TradeStore` (a cached connection crossing threads raised
    `sqlite3.ProgrammingError` there; the fix was the same one applied here:
    a fresh, cheap `sqlite3.connect()` per logical caller, not a shared one)."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.execute("PRAGMA foreign_keys = ON")
        # See `research.trade_store.TradeStore.__init__`'s comment -- same
        # reasoning, same fix. This file is written by the data scheduler and
        # every `AutonomousPaperTrader` strategy thread concurrently; without
        # WAL + a busy_timeout, a lock collision here raises immediately
        # instead of waiting, and the callers that swallow that exception
        # (`sync.py`'s upsert paths, the live-bar dual-write in
        # `api/live_session.py`/`research_server/paper_trader.py`) would
        # silently lose bars rather than actually persist them.
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self.ensure_schema()

    def ensure_schema(self) -> None:
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "MarketDataStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def size_bytes(self) -> int:
        return self.path.stat().st_size if self.path.exists() else 0

    @property
    def location(self) -> str:
        """Human-readable, safe-to-display "where is this data" string --
        the uniform property both this class and `PgMarketDataStore`
        expose (`.path` itself stays SQLite-only, and is *not* safe to
        call on the Postgres store, since it has no file path at all).
        Callers that want to display this to a user (e.g.
        `MarketDataOverviewOut.database_path`) should use this, not
        `.path`, so the same code works against either backend."""
        return str(self.path)

    # --- Bars ---

    def upsert_bars(self, product_code: str, contract: str, resolution: str, source: str, bars: Sequence[Bar]) -> int:
        """Idempotent: a bar whose (product, resolution, timestamp) is
        already stored is silently skipped (`INSERT OR IGNORE` against
        `idx_bars_identity`), so re-fetching an overlapping window (a
        resumed backfill, a repair re-pulling a window that turned out to
        be only partially missing) can never create a duplicate or corrupt
        an existing row -- the caller doesn't need to pre-filter anything.
        Returns how many rows were *actually new* (SQLite's `rowcount` after
        an ignored conflict is 0 for that row, not the batch size)."""
        if not bars:
            return 0
        rows = [
            (
                product_code, contract, resolution, b.timestamp.astimezone(timezone.utc).isoformat(),
                str(b.open), str(b.high), str(b.low), str(b.close), b.volume, source,
            )
            for b in bars
        ]
        cur = self._conn.executemany(
            """
            INSERT OR IGNORE INTO bars
                (product_code, contract, resolution, timestamp, open, high, low, close, volume, source)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            rows,
        )
        self._conn.commit()
        return cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else len(rows)

    def coverage(self, product_code: str, resolution: str) -> Coverage:
        cur = self._conn.execute(
            "SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM bars WHERE product_code = ? AND resolution = ?",
            (product_code, resolution),
        )
        count, earliest, latest = cur.fetchone()
        return Coverage(
            count=count,
            earliest=datetime.fromisoformat(earliest) if earliest else None,
            latest=datetime.fromisoformat(latest) if latest else None,
        )

    def fetch_bars(
        self, product_code: str, resolution: str,
        start: Optional[datetime] = None, end: Optional[datetime] = None,
    ) -> list[Bar]:
        """A plain range scan, not a stitch -- see the module docstring for
        why overlapping-contract data never reaches this table in the first
        place."""
        query = "SELECT timestamp, open, high, low, close, volume FROM bars WHERE product_code = ? AND resolution = ?"
        params: list = [product_code, resolution]
        if start is not None:
            query += " AND timestamp >= ?"
            params.append(start.astimezone(timezone.utc).isoformat())
        if end is not None:
            query += " AND timestamp <= ?"
            params.append(end.astimezone(timezone.utc).isoformat())
        query += " ORDER BY timestamp ASC"

        cur = self._conn.execute(query, params)
        return [
            Bar(
                timestamp=datetime.fromisoformat(ts), open=Decimal(o), high=Decimal(h),
                low=Decimal(lo), close=Decimal(c), volume=v,
            )
            for ts, o, h, lo, c, v in cur.fetchall()
        ]

    def contracts_stored(self, product_code: str) -> list[str]:
        cur = self._conn.execute(
            "SELECT DISTINCT contract FROM bars WHERE product_code = ? ORDER BY contract", (product_code,)
        )
        return [row[0] for row in cur.fetchall()]

    def all_products(self) -> list[str]:
        cur = self._conn.execute("SELECT DISTINCT product_code FROM bars ORDER BY product_code")
        return [row[0] for row in cur.fetchall()]

    def resolutions_stored(self, product_code: str) -> list[str]:
        cur = self._conn.execute(
            "SELECT DISTINCT resolution FROM bars WHERE product_code = ? ORDER BY resolution", (product_code,)
        )
        return [row[0] for row in cur.fetchall()]

    # --- Active contract / rollover tracking ---

    def get_active_contract(self, product_code: str) -> Optional[str]:
        cur = self._conn.execute("SELECT contract FROM active_contracts WHERE product_code = ?", (product_code,))
        row = cur.fetchone()
        return row[0] if row else None

    def set_active_contract(self, product_code: str, contract: str) -> None:
        """Updates the tracked active contract, and -- only when it's
        actually changing -- appends a row to `contract_rolls` so "when did
        we roll, and from what" stays queryable after the fact."""
        previous = self.get_active_contract(product_code)
        if previous == contract:
            return
        self._conn.execute(
            """INSERT INTO active_contracts (product_code, contract, updated_at) VALUES (?, ?, datetime('now'))
               ON CONFLICT(product_code) DO UPDATE SET contract = excluded.contract, updated_at = excluded.updated_at""",
            (product_code, contract),
        )
        self._conn.execute(
            "INSERT INTO contract_rolls (product_code, from_contract, to_contract) VALUES (?, ?, ?)",
            (product_code, previous, contract),
        )
        self._conn.commit()

    def contract_rolls(self, product_code: Optional[str] = None) -> list[dict]:
        query = "SELECT product_code, from_contract, to_contract, rolled_at FROM contract_rolls"
        params: list = []
        if product_code is not None:
            query += " WHERE product_code = ?"
            params.append(product_code)
        # `rolled_at` is SQLite's `datetime('now')`, only second-resolution --
        # two rolls recorded within the same second (routine in tests, and
        # possible in production if a caller replays history quickly) would
        # otherwise sort in an undefined order relative to each other.
        # `id DESC` breaks the tie by actual insertion order.
        query += " ORDER BY rolled_at DESC, id DESC"
        cur = self._conn.execute(query, params)
        return [
            {"product_code": r[0], "from_contract": r[1], "to_contract": r[2], "rolled_at": r[3]}
            for r in cur.fetchall()
        ]

    # --- Sync run log (resumability + observability) ---

    def start_sync_run(
        self, run_id: str, product_code: str, resolution: str, kind: str,
        requested_start: Optional[datetime] = None, requested_end: Optional[datetime] = None,
    ) -> None:
        self._conn.execute(
            """INSERT INTO sync_runs (id, product_code, resolution, kind, status, requested_start, requested_end)
               VALUES (?, ?, ?, ?, 'running', ?, ?)""",
            (
                run_id, product_code, resolution, kind,
                requested_start.isoformat() if requested_start else None,
                requested_end.isoformat() if requested_end else None,
            ),
        )
        self._conn.commit()

    def checkpoint_sync_run(self, run_id: str, *, bars_fetched: int, last_committed_through: datetime) -> None:
        """Called incrementally *during* a sync, not just at the end -- this
        is the resumability checkpoint: if the process dies mid-backfill,
        the next run reads this back and resumes from
        `last_committed_through` instead of `requested_start`."""
        self._conn.execute(
            "UPDATE sync_runs SET bars_fetched = ?, last_committed_through = ? WHERE id = ?",
            (bars_fetched, last_committed_through.isoformat(), run_id),
        )
        self._conn.commit()

    def complete_sync_run(self, run_id: str) -> None:
        self._conn.execute(
            "UPDATE sync_runs SET status = 'completed', completed_at = datetime('now') WHERE id = ?", (run_id,)
        )
        self._conn.commit()

    def fail_sync_run(self, run_id: str, error_message: str) -> None:
        self._conn.execute(
            "UPDATE sync_runs SET status = 'failed', error_message = ?, completed_at = datetime('now') WHERE id = ?",
            (error_message, run_id),
        )
        self._conn.commit()

    def last_committed_through(self, product_code: str, resolution: str) -> Optional[datetime]:
        """The resume point for an interrupted run: the most recent
        checkpoint among *any* run (running, failed, or completed) for this
        product+resolution. Falls back to `None` (caller then falls back to
        `coverage().latest`, i.e. "resume from whatever's actually stored")
        if no run ever checkpointed."""
        cur = self._conn.execute(
            """SELECT last_committed_through FROM sync_runs
               WHERE product_code = ? AND resolution = ? AND last_committed_through IS NOT NULL
               ORDER BY started_at DESC, rowid DESC LIMIT 1""",
            (product_code, resolution),
        )
        row = cur.fetchone()
        return datetime.fromisoformat(row[0]) if row and row[0] else None

    def fetch_sync_runs(self, product_code: Optional[str] = None, limit: int = 50) -> list[dict]:
        query = "SELECT id, product_code, resolution, kind, status, bars_fetched, error_message, started_at, completed_at FROM sync_runs"
        params: list = []
        if product_code is not None:
            query += " WHERE product_code = ?"
            params.append(product_code)
        query += " ORDER BY started_at DESC, rowid DESC LIMIT ?"
        params.append(limit)
        cur = self._conn.execute(query, params)
        cols = ("id", "product_code", "resolution", "kind", "status", "bars_fetched", "error_message", "started_at", "completed_at")
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    # --- Gaps ---

    def record_gap(self, product_code: str, resolution: str, gap_start: datetime, gap_end: datetime) -> None:
        self._conn.execute(
            "INSERT INTO gaps (product_code, resolution, gap_start, gap_end) VALUES (?, ?, ?, ?)",
            (product_code, resolution, gap_start.isoformat(), gap_end.isoformat()),
        )
        self._conn.commit()

    def resolve_gaps(self, product_code: str, resolution: str, gap_start: datetime, gap_end: datetime) -> None:
        """Marks any previously-recorded gap fully contained in
        [gap_start, gap_end] as resolved -- called by `sync.repair_gaps`
        after it successfully re-fetches that window."""
        self._conn.execute(
            """UPDATE gaps SET resolved_at = datetime('now')
               WHERE product_code = ? AND resolution = ? AND resolved_at IS NULL
                 AND gap_start >= ? AND gap_end <= ?""",
            (product_code, resolution, gap_start.isoformat(), gap_end.isoformat()),
        )
        self._conn.commit()

    def fetch_gaps(self, product_code: Optional[str] = None, unresolved_only: bool = True) -> list[dict]:
        query = "SELECT id, product_code, resolution, gap_start, gap_end, detected_at, resolved_at FROM gaps WHERE 1=1"
        params: list = []
        if product_code is not None:
            query += " AND product_code = ?"
            params.append(product_code)
        if unresolved_only:
            query += " AND resolved_at IS NULL"
        query += " ORDER BY gap_start ASC"
        cur = self._conn.execute(query, params)
        cols = ("id", "product_code", "resolution", "gap_start", "gap_end", "detected_at", "resolved_at")
        return [dict(zip(cols, row)) for row in cur.fetchall()]
