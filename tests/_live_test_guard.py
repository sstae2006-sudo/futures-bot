"""Shared guard for every test module that talks to a REAL Postgres/
TimescaleDB server. Six modules (`test_pg_market_data_store_live.py`,
`test_pg_trade_store_live.py`, `test_pg_collaboration_store_live.py`,
`test_pg_account_store_live.py`, `test_migrate_to_timescaledb.py`,
`test_api_market_data_live.py`) TRUNCATE real tables in their own
cleanup fixtures; a seventh (`test_db_health.py`) only does read-only
connectivity probes but still connects to whatever real server is
configured.

KNOWN_ISSUES.md ISSUE-041: `FUTURES_BOT_DATABASE_URL` being set and
reachable used to be the *only* condition checked (each of the six
`pytestmark`-gated modules duplicated an identical `_live_server_reachable()`
function; `test_db_health.py` went through `conftest.py`'s
`live_database_url` fixture instead, but with the same "just check
reachability" logic). That variable is also the switch a developer sets
simply to BOOT the app in Team Mode (`scripts\\start-team.ps1`), and per
`TEAM_DEPLOYMENT.md`'s Windows persistence guidance, is now commonly a
*persistent* Windows User environment variable rather than something set
fresh in one shell. Conflating "the app should talk to Postgres" with
"it is safe to run a destructive test suite against this exact database"
let a routine `pytest` run in any ordinary team-mode-configured terminal
silently TRUNCATE real, already-migrated production data -- found
2026-07-29, the same day `FUTURES_BOT_DATABASE_URL` was first persisted.

`ALLOW_LIVE_DB_TESTS_ENV` (`FUTURES_BOT_ALLOW_LIVE_DB_TESTS=1`) is a
second, independent, must-be-deliberate opt-in, checked here AND in
`conftest.py::live_database_url` (both layers need it -- the six
`pytestmark`-gated modules decide whether to skip at collection time,
before any fixture runs; `test_db_health.py` decides per-test via the
`live_database_url` fixture). Set it only in a shell you are about to
run this specific live suite in, pointed at a disposable/scratch
database -- never leave it set as an ambient default, and never point it
at a shared team instance with real data.
"""

from __future__ import annotations

import os

ALLOW_LIVE_DB_TESTS_ENV = "FUTURES_BOT_ALLOW_LIVE_DB_TESTS"


def live_tests_allowed() -> bool:
    """Just the opt-in check, no reachability probe -- what
    `conftest.py::live_database_url` needs (a real DB connection attempt
    there would be premature; reachability is checked once the DSN is
    actually restored)."""
    return os.environ.get(ALLOW_LIVE_DB_TESTS_ENV) == "1"


def live_server_reachable() -> bool:
    if not live_tests_allowed():
        return False
    from futures_bot.db.engine import database_url
    from futures_bot.db.health import check_database_health

    if not database_url():
        return False
    return check_database_health().ok


def skip_reason() -> str:
    if not live_tests_allowed():
        return (
            f"Live-database tests are opt-in only -- set {ALLOW_LIVE_DB_TESTS_ENV}=1 (in addition "
            "to FUTURES_BOT_DATABASE_URL) to run this module. Never set this against a shared or "
            "production database -- these tests TRUNCATE real tables in their cleanup fixtures. "
            "See tests/_live_test_guard.py's module docstring / KNOWN_ISSUES.md ISSUE-041."
        )
    return (
        f"No reachable Postgres/TimescaleDB at {os.environ.get('FUTURES_BOT_DATABASE_URL', '<unset>')} "
        "-- start the compose timescaledb service to run this module."
    )
