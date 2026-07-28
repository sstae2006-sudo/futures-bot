"""An honest, no-auth-system "how many people are using this right now"
estimate for `/api/system/health`.

There is deliberately no real session/auth system in this codebase (see
`api/live_session.py`'s module docstring and `deploy/DEPLOYMENT.md`'s "No
authentication" section) -- so "connected users" cannot mean anything more
precise than "distinct client IPs seen recently." This tracker is:

* **Process-local**: an in-memory dict, not backed by any database. A
  multi-worker deployment (this project only ever runs single-worker, see
  `api/app.py`'s module docstring) would need one per worker, undercounting
  the true total -- not a concern today, but worth stating rather than
  silently wrong if that ever changes.
* **Approximate**: identifies a "user" by client IP, so one person behind
  a NAT and several people behind the same NAT are indistinguishable, and
  the same person on two devices counts twice.
* **Reset on restart**: no persistence, by design -- a restart is a new
  count, not a resumed one.

This is exactly what team-deployment mode needs and no more: "is anyone
else on the network currently poking at this," not a real presence system.
"""

from __future__ import annotations

import threading
import time
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

#: How far back a client IP still counts as "connected." 15 minutes is
#: long enough to survive someone reading a dashboard page without
#: clicking anything, short enough that yesterday's visitor doesn't still
#: count today.
DEFAULT_WINDOW_SECONDS = 15 * 60


class ConnectedUsersTracker:
    """Thread-safe sliding-window IP tracker. A module-level singleton
    (`TRACKER` below) is what the middleware and the health route actually
    share -- constructible on its own (this class takes no global state)
    purely so tests can build an isolated instance instead of fighting the
    shared one."""

    def __init__(self, window_seconds: int = DEFAULT_WINDOW_SECONDS) -> None:
        self._window_seconds = window_seconds
        self._lock = threading.Lock()
        self._last_seen: dict[str, float] = {}

    def record(self, client_ip: Optional[str]) -> None:
        if not client_ip:
            return
        with self._lock:
            self._last_seen[client_ip] = time.monotonic()

    def count(self) -> int:
        """Purges anything outside the window, then returns how many
        distinct IPs are left -- purging on read (not on a background
        timer) keeps this dependency-free and correct regardless of how
        long the process has been idle."""
        cutoff = time.monotonic() - self._window_seconds
        with self._lock:
            stale = [ip for ip, last_seen in self._last_seen.items() if last_seen < cutoff]
            for ip in stale:
                del self._last_seen[ip]
            return len(self._last_seen)


#: The process-wide tracker every request feeds and `/api/system/health`
#: reads. A fresh `ConnectedUsersTracker()` per test (see
#: `tests/test_connected_users.py`) avoids cross-test bleed through this
#: singleton.
TRACKER = ConnectedUsersTracker()


class ConnectedUsersMiddleware(BaseHTTPMiddleware):
    """Records the calling IP on every request, and only that -- no
    request/response body access, no added latency beyond a dict write
    behind a lock."""

    async def dispatch(self, request: Request, call_next):
        client = request.client
        TRACKER.record(client.host if client else None)
        return await call_next(request)
