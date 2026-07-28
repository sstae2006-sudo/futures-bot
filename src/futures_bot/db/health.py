"""A cheap, timed database round-trip -- used by `/api/system/health`
and by anything that wants to fail gracefully (a clear "database
unreachable" response) rather than let a dead pool surface as an
unhandled exception deep in a route.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import text

from .engine import database_url, get_engine


@dataclass(frozen=True)
class DatabaseHealth:
    #: False when FUTURES_BOT_DATABASE_URL isn't set at all -- this
    #: process is running SQLite-only, "TimescaleDB" isn't applicable,
    #: not the same thing as "configured but unreachable" (see `ok`).
    configured: bool
    #: True only when `configured` and the round-trip actually succeeded.
    ok: bool
    latency_ms: Optional[float]
    error: Optional[str]


def check_database_health(timeout_seconds: float = 3.0) -> DatabaseHealth:
    """Times a trivial ``SELECT 1`` against the pooled engine.

    Never raises -- a connection failure, a pool timeout, or any other
    driver error is caught and reported in ``error`` instead, so a
    caller (the health route, a startup check) can always render
    *something* rather than 500 when the database is the thing that's
    down. Matches this codebase's existing "a dependency being down must
    never take the whole process down" posture (see
    `api/app.py::_maybe_start_research_server`'s identical reasoning for
    a bad config file).
    """
    url = database_url()
    if not url:
        return DatabaseHealth(configured=False, ok=False, latency_ms=None, error=None)

    start = time.perf_counter()
    try:
        # get_engine() is a process-wide singleton (see its own docstring)
        # -- connect_timeout was fixed at whichever call first built it,
        # not adjustable per call here. `timeout_seconds` therefore isn't
        # honored today; kept as a documented parameter for a future
        # per-call statement-timeout (SET statement_timeout, a real
        # per-query knob, unlike connect_timeout) rather than silently
        # doing nothing under a name that implies it works.
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        latency_ms = (time.perf_counter() - start) * 1000.0
        return DatabaseHealth(configured=True, ok=True, latency_ms=latency_ms, error=None)
    except Exception as exc:  # noqa: BLE001 -- reported, never raised past this point.
        latency_ms = (time.perf_counter() - start) * 1000.0
        return DatabaseHealth(configured=True, ok=False, latency_ms=latency_ms, error=str(exc))
