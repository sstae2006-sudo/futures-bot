"""TimescaleDB/Postgres-backed market-data store.

**Verified against a real server** (2026-07-27, `docker compose up -d
timescaledb` + `alembic upgrade head` + `tests/test_pg_market_data_store_live.py`,
10 tests, real Postgres round trips, not signature checks) -- see
PROJECT_STATE.md for the one real bug this first run caught (`bars.id`
needed `Identity()`, not bare `autoincrement=True`; fixed in
`db/schema.py`).

Same public method signatures as `store.py::MarketDataStore` (that
module's own docstring names this exact file as the seam it was built
for) -- selected instead of it by `api/market_data_store.py::get_market_data_store()`
whenever `FUTURES_BOT_DATABASE_URL` is set. Every caller elsewhere in the
codebase goes through that one factory function, never constructs either
store class directly, so nothing outside these two files needs to know
which database is actually in use.

Built on the shared pooled `db.engine.get_engine()` Postgres/ANSI SQL
throughout (`ON CONFLICT ... DO NOTHING` instead of `INSERT OR IGNORE`,
`now()` instead of `datetime('now')`, no `PRAGMA`s -- Postgres has no
per-connection journal mode to set and always enforces foreign keys).
Money fields are native `NUMERIC` (psycopg maps this to Python `Decimal`
automatically -- same exact-precision guarantee `models.py`'s docstring
requires, just backed by a real type instead of `store.py`'s TEXT-string
convention); timestamps are native `TIMESTAMPTZ` (psycopg returns
timezone-aware `datetime` objects directly, matching what `models.Bar`
already expects).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, Sequence

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine

from ..models import Bar
from ..db.engine import get_engine
from ..db.schema import HYPERTABLE_STATEMENTS, active_contracts, bars, contract_rolls, gaps, metadata, sync_runs


def _isoformat_datetimes(row: dict) -> dict:
    """`contract_rolls`/`fetch_sync_runs`/`fetch_gaps` return dicts meant to
    be schema/JSON-compatible drop-ins for `MarketDataStore`'s identically-
    shaped methods -- callers (`market_data_service.py`, `api/schemas.py`'s
    `SyncRunOut`/`GapOut`) already assume every timestamp field in those
    dicts is a plain string, because SQLite's TEXT-encoded columns always
    were one. Postgres's native `TIMESTAMPTZ` columns come back from
    psycopg as real `datetime` objects instead -- found via a real 500
    (`pydantic.ValidationError` on `MarketDataOverviewOut.last_sync_at`)
    the first time `/api/market-data/overview` was hit against a live
    server, not just from a signature/method-existence check. Converting
    here, once, keeps every dict-returning method's actual output
    identical across both backends, matching what `test_market_data_store_parity.py`
    already promises callers.
    """
    return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in row.items()}


@dataclass(frozen=True)
class Coverage:
    """Identical shape to `store.py::Coverage` -- kept as a separate
    class (not imported from there) so this module has zero dependency
    on the SQLite implementation, matching the "two independent store
    classes, selected by the factory" design."""

    count: int
    earliest: Optional[datetime]
    latest: Optional[datetime]


class PgMarketDataStore:
    """Pooled, Postgres-backed. Unlike `MarketDataStore` (one
    `sqlite3.Connection` owned per instance), this class holds no
    connection of its own at all -- every method borrows one from the
    shared `Engine`'s pool for the duration of that one call and returns
    it immediately after, via `with engine.connect() as conn:`. Cheap and
    safe to construct many of these (one per request, matching every
    existing call site's habit) since construction does no I/O beyond
    grabbing the already-open shared `Engine`.
    """

    def __init__(self, engine: Optional[Engine] = None) -> None:
        self._engine = engine or get_engine()

    def ensure_schema(self) -> None:
        """Creates every table/index if missing (`checkfirst=True`,
        SQLAlchemy's own `IF NOT EXISTS` equivalent) and converts `bars`
        into a TimescaleDB hypertable. Idempotent, safe to call on every
        process start -- mirrors `MarketDataStore.ensure_schema`'s
        contract exactly. Real migrations (column additions/changes to an
        existing deployed table) go through Alembic (`../../alembic/`),
        not this method -- this only ever creates what's missing, never
        alters what exists, the same boundary `CREATE TABLE IF NOT
        EXISTS` already drew in the SQLite version.
        """
        with self._engine.begin() as conn:
            metadata.create_all(conn, checkfirst=True)
            for stmt in HYPERTABLE_STATEMENTS:
                conn.execute(text(stmt))

    def close(self) -> None:
        """No-op: this class owns no connection of its own to close (see
        the class docstring) -- kept only so callers that do
        `store.close()` unconditionally (matching the SQLite class's
        `__exit__`) don't need an `isinstance` check."""

    def __enter__(self) -> "PgMarketDataStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def location(self) -> str:
        """Human-readable, safe-to-display "where is this data" string --
        the uniform property both this class and `MarketDataStore`
        expose. Password is always redacted (`render_as_string(hide_password=True)`
        is SQLAlchemy's own safe-display method for a URL, not a
        hand-rolled string edit) -- this can be shown to any user of the
        dashboard without leaking the database credential."""
        return self._engine.url.render_as_string(hide_password=True)

    @property
    def size_bytes(self) -> int:
        """Total on-disk size of every table this store owns, via
        Postgres's own `pg_total_relation_size` (includes indexes/TOAST,
        the fairest equivalent of SQLite's whole-file `st_size`)."""
        with self._engine.connect() as conn:
            total = conn.execute(
                text(
                    "SELECT COALESCE(SUM(pg_total_relation_size(quote_ident(tablename))), 0) "
                    "FROM pg_tables WHERE tablename = ANY(:names)"
                ),
                {"names": ["bars", "sync_runs", "gaps", "active_contracts", "contract_rolls"]},
            ).scalar_one()
            return int(total)

    # --- Bars ---

    def upsert_bars(self, product_code: str, contract: str, resolution: str, source: str, bars_: Sequence[Bar]) -> int:
        """Same contract as `MarketDataStore.upsert_bars`: idempotent,
        returns the count of rows *actually newly inserted*. `RETURNING
        id` (rather than trusting driver-reported `rowcount`, which is
        not reliably "rows actually affected" for a multi-row `ON
        CONFLICT DO NOTHING` under every driver) is the exact count --
        a row that hit the conflict returns nothing, so
        `len(result.fetchall())` is precisely the new-row count."""
        if not bars_:
            return 0
        rows = [
            {
                "product_code": product_code, "contract": contract, "resolution": resolution,
                "timestamp": b.timestamp.astimezone(timezone.utc),
                "open": b.open, "high": b.high, "low": b.low, "close": b.close,
                "volume": b.volume, "source": source,
            }
            for b in bars_
        ]
        stmt = (
            pg_insert(bars)
            .values(rows)
            .on_conflict_do_nothing(index_elements=["product_code", "resolution", "timestamp"])
            .returning(bars.c.id)
        )
        with self._engine.begin() as conn:
            result = conn.execute(stmt)
            return len(result.fetchall())

    def coverage(self, product_code: str, resolution: str) -> Coverage:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(
                    text("COUNT(*)"), text("MIN(timestamp)"), text("MAX(timestamp)"),
                ).select_from(bars).where(bars.c.product_code == product_code, bars.c.resolution == resolution)
            ).one()
        count, earliest, latest = row
        return Coverage(count=count, earliest=earliest, latest=latest)

    def fetch_bars(
        self, product_code: str, resolution: str,
        start: Optional[datetime] = None, end: Optional[datetime] = None,
    ) -> list[Bar]:
        stmt = (
            select(bars.c.timestamp, bars.c.open, bars.c.high, bars.c.low, bars.c.close, bars.c.volume)
            .where(bars.c.product_code == product_code, bars.c.resolution == resolution)
        )
        if start is not None:
            stmt = stmt.where(bars.c.timestamp >= start.astimezone(timezone.utc))
        if end is not None:
            stmt = stmt.where(bars.c.timestamp <= end.astimezone(timezone.utc))
        stmt = stmt.order_by(bars.c.timestamp.asc())

        with self._engine.connect() as conn:
            rows = conn.execute(stmt).all()
        return [
            Bar(
                timestamp=ts, open=Decimal(o), high=Decimal(h), low=Decimal(lo), close=Decimal(c), volume=v,
            )
            for ts, o, h, lo, c, v in rows
        ]

    def contracts_stored(self, product_code: str) -> list[str]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(bars.c.contract).distinct().where(bars.c.product_code == product_code).order_by(bars.c.contract)
            ).all()
        return [r[0] for r in rows]

    def all_products(self) -> list[str]:
        with self._engine.connect() as conn:
            rows = conn.execute(select(bars.c.product_code).distinct().order_by(bars.c.product_code)).all()
        return [r[0] for r in rows]

    def resolutions_stored(self, product_code: str) -> list[str]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(bars.c.resolution).distinct().where(bars.c.product_code == product_code).order_by(bars.c.resolution)
            ).all()
        return [r[0] for r in rows]

    # --- Active contract / rollover tracking ---

    def get_active_contract(self, product_code: str) -> Optional[str]:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(active_contracts.c.contract).where(active_contracts.c.product_code == product_code)
            ).first()
        return row[0] if row else None

    def set_active_contract(self, product_code: str, contract: str) -> None:
        previous = self.get_active_contract(product_code)
        if previous == contract:
            return
        upsert = (
            pg_insert(active_contracts)
            .values(product_code=product_code, contract=contract)
            .on_conflict_do_update(
                index_elements=["product_code"],
                set_={"contract": contract, "updated_at": text("now()")},
            )
        )
        with self._engine.begin() as conn:
            conn.execute(upsert)
            conn.execute(
                contract_rolls.insert().values(product_code=product_code, from_contract=previous, to_contract=contract)
            )

    def contract_rolls(self, product_code: Optional[str] = None) -> list[dict]:
        stmt = select(
            contract_rolls.c.product_code, contract_rolls.c.from_contract,
            contract_rolls.c.to_contract, contract_rolls.c.rolled_at,
        )
        if product_code is not None:
            stmt = stmt.where(contract_rolls.c.product_code == product_code)
        # `id` (a real BIGSERIAL here, unlike SQLite's implicit `rowid`
        # this originally relied on) still breaks a same-instant tie by
        # actual insertion order -- direct equivalent, no gap.
        stmt = stmt.order_by(contract_rolls.c.rolled_at.desc(), contract_rolls.c.id.desc())
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).all()
        return [
            _isoformat_datetimes({"product_code": r[0], "from_contract": r[1], "to_contract": r[2], "rolled_at": r[3]})
            for r in rows
        ]

    # --- Sync run log ---

    def start_sync_run(
        self, run_id: str, product_code: str, resolution: str, kind: str,
        requested_start: Optional[datetime] = None, requested_end: Optional[datetime] = None,
    ) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                sync_runs.insert().values(
                    id=run_id, product_code=product_code, resolution=resolution, kind=kind, status="running",
                    requested_start=requested_start, requested_end=requested_end,
                )
            )

    def checkpoint_sync_run(self, run_id: str, *, bars_fetched: int, last_committed_through: datetime) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                sync_runs.update()
                .where(sync_runs.c.id == run_id)
                .values(bars_fetched=bars_fetched, last_committed_through=last_committed_through)
            )

    def complete_sync_run(self, run_id: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                sync_runs.update().where(sync_runs.c.id == run_id).values(status="completed", completed_at=text("now()"))
            )

    def fail_sync_run(self, run_id: str, error_message: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                sync_runs.update().where(sync_runs.c.id == run_id)
                .values(status="failed", error_message=error_message, completed_at=text("now()"))
            )

    def last_committed_through(self, product_code: str, resolution: str) -> Optional[datetime]:
        # No `rowid`-equivalent tiebreaker needed: `sync_runs.id` is a
        # caller-supplied run-id string (a UUID), not an insertion-order
        # surrogate, so it can't serve as one -- but Postgres `now()` has
        # microsecond resolution (vs. SQLite `datetime('now')`'s second
        # resolution, which is *why* the original needed a `rowid`
        # tiebreak at all), so an exact `started_at` collision here is
        # not a realistic concern.
        stmt = (
            select(sync_runs.c.last_committed_through)
            .where(
                sync_runs.c.product_code == product_code, sync_runs.c.resolution == resolution,
                sync_runs.c.last_committed_through.is_not(None),
            )
            .order_by(sync_runs.c.started_at.desc())
            .limit(1)
        )
        with self._engine.connect() as conn:
            row = conn.execute(stmt).first()
        return row[0] if row else None

    def fetch_sync_runs(self, product_code: Optional[str] = None, limit: int = 50) -> list[dict]:
        cols = (
            sync_runs.c.id, sync_runs.c.product_code, sync_runs.c.resolution, sync_runs.c.kind,
            sync_runs.c.status, sync_runs.c.bars_fetched, sync_runs.c.error_message,
            sync_runs.c.started_at, sync_runs.c.completed_at,
        )
        stmt = select(*cols)
        if product_code is not None:
            stmt = stmt.where(sync_runs.c.product_code == product_code)
        stmt = stmt.order_by(sync_runs.c.started_at.desc()).limit(limit)
        col_names = ("id", "product_code", "resolution", "kind", "status", "bars_fetched", "error_message", "started_at", "completed_at")
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).all()
        return [_isoformat_datetimes(dict(zip(col_names, row))) for row in rows]

    # --- Gaps ---

    def record_gap(self, product_code: str, resolution: str, gap_start: datetime, gap_end: datetime) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                gaps.insert().values(product_code=product_code, resolution=resolution, gap_start=gap_start, gap_end=gap_end)
            )

    def resolve_gaps(self, product_code: str, resolution: str, gap_start: datetime, gap_end: datetime) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                gaps.update()
                .where(
                    gaps.c.product_code == product_code, gaps.c.resolution == resolution,
                    gaps.c.resolved_at.is_(None), gaps.c.gap_start >= gap_start, gaps.c.gap_end <= gap_end,
                )
                .values(resolved_at=text("now()"))
            )

    def fetch_gaps(self, product_code: Optional[str] = None, unresolved_only: bool = True) -> list[dict]:
        cols = (
            gaps.c.id, gaps.c.product_code, gaps.c.resolution, gaps.c.gap_start,
            gaps.c.gap_end, gaps.c.detected_at, gaps.c.resolved_at,
        )
        stmt = select(*cols)
        if product_code is not None:
            stmt = stmt.where(gaps.c.product_code == product_code)
        if unresolved_only:
            stmt = stmt.where(gaps.c.resolved_at.is_(None))
        stmt = stmt.order_by(gaps.c.gap_start.asc())
        col_names = ("id", "product_code", "resolution", "gap_start", "gap_end", "detected_at", "resolved_at")
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).all()
        return [_isoformat_datetimes(dict(zip(col_names, row))) for row in rows]
