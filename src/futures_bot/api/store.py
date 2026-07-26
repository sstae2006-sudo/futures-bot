"""Access to the shared `TradeStore` (SQLite) for the API process.

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
tool -- this is not a high-QPS production API. A connection-pool-per-thread
would shave that overhead further; not worth the added complexity unless
profiling ever shows this mattering (Phase 6B candidate if so).
"""

from __future__ import annotations

from pathlib import Path

from ..research.trade_store import TradeStore, default_db_path


def _db_path() -> Path:
    return default_db_path()


def get_store() -> TradeStore:
    """A fresh `TradeStore` against the configured database path.

    Callers are expected to use the returned instance for one logical
    operation (a single request) and let it go -- exactly how every
    `services.py` function already uses it (one `get_store()` call at the
    top, the local variable reused for that function's writes/reads, no
    caching across calls).
    """
    return TradeStore(_db_path())


def reset_store() -> None:
    """Test-only no-op, kept so existing test fixtures that call this don't
    need editing: there is no cached instance to drop anymore now that
    `get_store()` always opens fresh. Tests get isolation by pointing
    `FUTURES_BOT_RESEARCH_DB` at a per-test temp file instead."""
