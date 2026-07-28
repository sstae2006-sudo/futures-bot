"""Access to the shared `TradeStore` (SQLite) or `PgTradeStore` (Postgres/
TimescaleDB, team-deployment mode) for the API process.

FastAPI runs sync route handlers in a worker thread pool -- even a single
`uvicorn` worker process fans requests out across several OS threads via
`anyio.to_thread.run_sync`. `TradeStore` wraps one `sqlite3.Connection`,
and `sqlite3` connections may only be used from the thread that created
them by default (confirmed the hard way: an earlier version of this module
cached one `TradeStore` at import time and every second request -- served
from a different pool thread -- raised `sqlite3.ProgrammingError`).

The fix here is the simplest one that's actually correct under that
threading model: open a fresh connection per call rather than caching one.
`sqlite3.connect()` against an existing file is fast (no table scan, no
data read), so the overhead is negligible for a single-user local research
tool -- this is not a high-QPS production API. `PgTradeStore` holds no
connection of its own either (borrows one from the shared pooled `Engine`
per call, see `db/engine.py`), so the identical "construct fresh, let it
go" habit is correct for both backends.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..research.trade_store import TradeStore, default_db_path

#: Same convention `market_data/store.py::get_market_data_store()`
#: already established for `market_data.db` -- kept as a plain module-
#: level constant (not re-imported from `db.engine`) so this module can
#: check it without importing `db.engine` (and therefore SQLAlchemy) at
#: module level, matching that module's own "SQLite-only setups never
#: need the `db` extra installed" requirement.
_DATABASE_URL_ENV = "FUTURES_BOT_DATABASE_URL"


def _db_path() -> Path:
    return default_db_path()


def get_store():
    """A fresh store against the configured database -- `PgTradeStore`
    when `FUTURES_BOT_DATABASE_URL` is set (team-deployment mode), else
    today's `TradeStore(default_db_path())`, completely unchanged for
    every existing single-developer setup. Lazy import of `PgTradeStore`
    (and therefore SQLAlchemy) only inside this branch -- see
    `db/engine.py`'s module docstring for why that import must never
    happen at module level.

    Callers are expected to use the returned instance for one logical
    operation (a single request) and let it go -- exactly how every
    `services.py` function already uses it (one `get_store()` call at the
    top, the local variable reused for that function's writes/reads, no
    caching across calls).
    """
    if os.environ.get(_DATABASE_URL_ENV):
        from ..research.pg_trade_store import PgTradeStore

        store = PgTradeStore()
        store.ensure_schema()
        return store
    return TradeStore(_db_path())


def reset_store() -> None:
    """Test-only no-op, kept so existing test fixtures that call this don't
    need editing: there is no cached instance to drop anymore now that
    `get_store()` always opens fresh. Tests get isolation by pointing
    `FUTURES_BOT_RESEARCH_DB` at a per-test temp file instead."""
