"""API-facing logic for the Worker Registry (SIL Phase 6 "Integration
Coordinator" Milestone 1) -- own module, matching `accounts_service.py`'s
precedent for keeping a domain area modular rather than growing
`services.py`. See `collaboration/store.py::heartbeat_worker`'s
docstring for the full design rationale (upsert semantics, caller-supplied
id, why staleness is computed live).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from ..collaboration import parse_db_timestamp
from ..collaboration.store import CollaborationError, get_collaboration_store
from .schemas import WorkerOut
from .services import ApiError

#: A worker not heard from in this many seconds is reported `is_stale=True`.
#: Deliberately generous relative to the recommended 30-60s+ heartbeat
#: interval (KNOWN_ISSUES.md ISSUE-038's measured SQLite concurrent-writer
#: ceiling favors an infrequent heartbeat) -- a worker should survive
#: missing one or two heartbeats (a slow cycle, a brief network blip)
#: without flapping to "stale."
_STALE_AFTER_SECONDS = 180.0


def _is_worker_stale(worker: dict, now: datetime) -> tuple[bool, float]:
    """Returns `(is_stale, seconds_since_heartbeat)` -- always computed
    fresh, never stored on the row (see this module's own docstring)."""
    heartbeat_at = parse_db_timestamp(worker["last_heartbeat_at"])
    seconds = (now - heartbeat_at).total_seconds()
    return seconds >= _STALE_AFTER_SECONDS, seconds


def _worker_out(worker: dict, now: Optional[datetime] = None) -> WorkerOut:
    now = now or datetime.now(timezone.utc)
    is_stale, seconds = _is_worker_stale(worker, now)
    return WorkerOut(**worker, is_stale=is_stale, seconds_since_heartbeat=seconds)


def heartbeat_worker(
    worker_id: str, *, worker_type: str, display_name: str, user_id: Optional[str] = None,
    org_id: Optional[str] = None, status: str = "online", current_work_item_id: Optional[str] = None,
    subsystem: Optional[str] = None, capabilities: Optional[list[str]] = None,
) -> WorkerOut:
    store = get_collaboration_store()
    try:
        worker = store.heartbeat_worker(
            worker_id=worker_id, worker_type=worker_type, display_name=display_name, user_id=user_id,
            org_id=org_id, status=status, current_work_item_id=current_work_item_id, subsystem=subsystem,
            capabilities=capabilities,
        )
    except CollaborationError as exc:
        raise ApiError(str(exc)) from exc
    return _worker_out(worker)


def get_worker(worker_id: str) -> WorkerOut:
    store = get_collaboration_store()
    worker = store.fetch_worker(worker_id)
    if worker is None:
        raise ApiError(f"No such worker: {worker_id!r}")
    return _worker_out(worker)


def list_workers(org_id: Optional[str] = None, status: Optional[str] = None, capability: Optional[str] = None) -> list[WorkerOut]:
    store = get_collaboration_store()
    now = datetime.now(timezone.utc)
    return [_worker_out(w, now) for w in store.fetch_workers(org_id=org_id, status=status, capability=capability)]
