"""One process-wide pooled SQLAlchemy Engine for TimescaleDB/Postgres.

SQLAlchemy **Core** only -- no declarative ORM, no `Session`, no model
classes. This project's stores (`market_data/store.py`,
`research/trade_store.py`) are deliberately plain-SQL and minimal-
dependency by design (both modules' own docstrings say so); the ORM
would add a second way to express the same queries for no benefit here.
What Core actually buys, and the only reason this dependency exists at
all: a real connection **pool** (`QueuePool`, the default), which plain
`psycopg.connect()` calls do not give you, plus `Table`/`MetaData`
objects Alembic can diff against for migrations (see `../../alembic/`).

Import this module lazily, from inside the `FUTURES_BOT_DATABASE_URL`-set
branch only (see `api/market_data_store.py`, `api/store.py`) -- never at
package import time. A SQLite-only single-developer setup, and the whole
test suite by default, must never require the `db` extra installed.
"""

from __future__ import annotations

import os
import threading
from typing import Optional

from sqlalchemy import Engine, create_engine

#: Contains credentials -- env var only, never config.yaml. Same
#: convention as MASSIVE_API_KEY/TRADOVATE_* (see config.py's
#: DeploymentSettings docstring). Standard SQLAlchemy Postgres DSN, e.g.
#: postgresql+psycopg://user:pass@100.x.x.x:5432/futures_bot
DATABASE_URL_ENV = "FUTURES_BOT_DATABASE_URL"

_engine: Optional[Engine] = None
_engine_lock = threading.Lock()


def database_url() -> Optional[str]:
    """The configured Postgres/TimescaleDB DSN, or ``None`` if this
    process is running SQLite-only (today's default -- unset unless a
    team deployment deliberately sets it)."""
    return os.environ.get(DATABASE_URL_ENV) or None


def get_engine(
    pool_size: int = 5, max_overflow: int = 10, pool_recycle_seconds: int = 1800,
    connect_timeout_seconds: int = 5, statement_timeout_ms: int = 30_000,
) -> Engine:
    """The one shared pooled ``Engine`` for this process.

    Built once, on first use, and reused for the life of the process --
    an ``Engine`` already *is* a connection pool (that's the entire
    point of using SQLAlchemy Core here instead of raw ``psycopg.connect``
    per call, the same "opened fresh per call" pattern the SQLite stores
    use and that this migration exists specifically to replace). A
    `threading.Lock` guards first construction only -- FastAPI's worker
    threads must not each build their own separate engine/pool.

    ``pool_pre_ping=True`` is the reconnect logic requirement: before
    handing a pooled connection to a caller, SQLAlchemy issues a cheap
    ``SELECT 1`` against it first and transparently discards/reopens it
    if that fails, instead of a caller receiving a dead connection from
    the pool and raising. ``pool_recycle_seconds`` additionally retires a
    connection after a bounded lifetime regardless of health, so a
    connection idled long enough for a stateful NAT/firewall along a
    Tailscale path to silently drop it is never kept indefinitely.

    ``statement_timeout_ms`` (KNOWN_ISSUES.md ISSUE-041, found alongside
    the live-test data-loss issue): ``connect_timeout_seconds`` only
    bounds *establishing* a new physical connection -- it does nothing
    once a connection is open and a query is running. Before this fix,
    nothing bounded query execution at all: a single slow scan, a lock
    wait, or a runaway statement from a concurrent process could hold a
    pool slot forever, and with only ``pool_size + max_overflow`` = 15
    total connections by default, a handful of stuck queries could
    exhaust the pool and make the whole app appear to hang for every
    other request. Passed via ``connect_args={"options": "-c
    statement_timeout=..."}`` -- a psycopg/libpq startup option, applied
    server-side to every connection this pool opens, so a stuck query is
    killed (raises a driver error the caller already has to handle) well
    before a human notices a hang instead of waiting on it indefinitely.

    Raises ``RuntimeError`` (not the raw driver exception) if
    ``FUTURES_BOT_DATABASE_URL`` isn't set -- callers only reach this
    function from the branch that already checked ``database_url()`` is
    truthy, so hitting this means a real bug in that caller, not a
    reachable end-user error path.
    """
    global _engine
    if _engine is not None:
        return _engine
    with _engine_lock:
        if _engine is not None:  # re-check: another thread may have built it while we waited
            return _engine
        url = database_url()
        if not url:
            raise RuntimeError(
                f"get_engine() called but {DATABASE_URL_ENV} is not set. This is a caller bug: "
                f"every caller must check db.engine.database_url() is truthy before reaching here."
            )
        _engine = create_engine(
            url,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_recycle=pool_recycle_seconds,
            pool_pre_ping=True,
            future=True,
            connect_args={
                # psycopg/libpq's own connect timeout (seconds) -- bounds
                # how long establishing a *new* physical connection can
                # take, the scenario that matters for a dead/unreachable
                # host over Tailscale. Applies to every new connection
                # the pool opens, not to queries against an already-open
                # one -- statement_timeout (below) covers that case.
                "connect_timeout": connect_timeout_seconds,
                "options": f"-c statement_timeout={statement_timeout_ms}",
            },
        )
        return _engine


def prime_engine(
    pool_size: int = 5, max_overflow: int = 10, pool_recycle_seconds: int = 1800,
    statement_timeout_ms: int = 30_000,
) -> Optional[Engine]:
    """Builds the shared `Engine` with explicit pool tuning, if one
    doesn't already exist -- the one place `config.py::DeploymentSettings`'s
    `pool_size`/`max_overflow`/`pool_recycle_seconds` actually reach
    `get_engine()`. Without this, every real call site (`PgMarketDataStore`/
    `PgTradeStore`'s `__init__`, `db.health.check_database_health`) calls
    bare `get_engine()` with no arguments the first time any of them run,
    silently locking in `get_engine()`'s own hardcoded defaults regardless
    of what an operator configured in `config.yaml`.

    Call this once, early (`api/app.py`'s startup, before any route can
    reach a store), only when `database_url()` is truthy -- a no-op
    (returns `None`) otherwise, since there's nothing to prime for a
    SQLite-only setup. A no-op if an `Engine` already exists too (e.g. a
    test built one first): `get_engine()` is a true first-call-wins
    singleton, documented on that function itself, and this respects the
    exact same rule rather than fighting it.
    """
    if not database_url():
        return None
    return get_engine(
        pool_size=pool_size, max_overflow=max_overflow, pool_recycle_seconds=pool_recycle_seconds,
        statement_timeout_ms=statement_timeout_ms,
    )


def dispose_engine() -> None:
    """Closes every pooled connection and drops the cached Engine.

    Test-only in practice (a real process just holds one Engine for its
    whole lifetime) -- lets a test point ``FUTURES_BOT_DATABASE_URL`` at
    a different database between cases without a stale pooled connection
    from the previous one leaking through.
    """
    global _engine
    with _engine_lock:
        if _engine is not None:
            _engine.dispose()
            _engine = None
