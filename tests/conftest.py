"""Session-wide fixtures.

`FUTURES_BOT_DATABASE_URL` is now a real, meaningful switch (team-
deployment mode, see `TEAM_DEPLOYMENT.md`) -- and a developer doing that
work entirely reasonably has it exported in their own shell. Found the
hard way this session: running the *general* test suite in that shell
silently routed dozens of unrelated tests (anything touching
`get_store()`/`get_market_data_store()`) through the live shared Postgres
instance instead of each test's own isolated per-test SQLite tmp file --
41 failures with no connection to whatever was actually being worked on,
purely from ambient shell state. See KNOWN_ISSUES.md.

The fix: an autouse fixture clears it for every test by default, so the
suite is hermetic regardless of the ambient shell. The handful of test
modules that deliberately talk to a live server (`test_pg_market_data_store_live.py`,
`test_pg_trade_store_live.py`, `test_db_health.py`, `test_api_market_data_live.py`,
`test_migrate_to_timescaledb.py`) opt back in explicitly via the
`live_database_url` fixture below, which every one of *their* central
fixtures (`store`/`client`/cleanup autouse fixtures) depends on -- pytest
guarantees an autouse fixture runs before any fixture that explicitly
requests something it affects, so the clear-then-restore ordering here is
deterministic, not a race.
"""

from __future__ import annotations

import os
from typing import Optional

import pytest

#: Captured once, at collection time (module import), before any fixture
#: -- including the autouse one below -- has a chance to touch the
#: environment. This is the *real* value the test session was actually
#: invoked with, if any.
_REAL_DATABASE_URL: Optional[str] = os.environ.get("FUTURES_BOT_DATABASE_URL")


@pytest.fixture(autouse=True)
def _hermetic_database_url(monkeypatch):
    monkeypatch.delenv("FUTURES_BOT_DATABASE_URL", raising=False)
    # `db.engine.get_engine()` caches its Engine as a module-level
    # singleton that does NOT re-check the env var once built -- a live
    # test running earlier in the same pytest process would otherwise
    # leave a real, cached, still-working Engine behind that a later
    # test could pick up even after this fixture clears the env var.
    # Guarded: this conftest fixture runs for *every* test in the suite,
    # including every environment without the `db` extra installed,
    # which must never be required to import sqlalchemy.
    try:
        from futures_bot.db.engine import dispose_engine

        dispose_engine()
    except ImportError:
        pass


@pytest.fixture()
def live_database_url(monkeypatch) -> Optional[str]:
    """Restores the real `FUTURES_BOT_DATABASE_URL` this test session was
    actually invoked with (or `None` if none was configured -- the
    default single-developer case, in which callers of this fixture
    should skip). Depend on this from any fixture/test that needs a real
    database connection -- see this module's own docstring for why the
    ordering against `_hermetic_database_url` above is guaranteed, not
    incidental."""
    if _REAL_DATABASE_URL:
        monkeypatch.setenv("FUTURES_BOT_DATABASE_URL", _REAL_DATABASE_URL)
    return _REAL_DATABASE_URL
