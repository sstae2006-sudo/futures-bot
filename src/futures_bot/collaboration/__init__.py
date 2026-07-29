"""Team Collaboration Platform -- an Active Work Registry so multiple
humans and AI agents working on this codebase at once can see who's
doing what, move work through a real lifecycle, and get warned (never
blocked) about overlap with other active work before they start.

Started as a small MVP (file-path overlap only, three statuses). SIL
Phase 2 ("Workflow Integration", 2026-07-28) extended it in place --
the lifecycle in `STATUSES` below, `overlap_v2.py`'s deeper-than-filename
analysis, `git_info.py`'s live branch introspection, `merge_readiness.py`,
and `timeline.py` -- without touching the original schema/API contract:
every Phase 1 status/route/field still means exactly what it did before.
See `overlap.py`/`overlap_v2.py` for the detection logic and `store.py`
for the schema/CRUD.

Still deliberately out of scope: real architecture/dependency-graph
analysis, semantic merge assistance, and a persistent AI-worker execution
layer (this package tracks/coordinates work, it doesn't run it) -- see
ROADMAP.md's "Future" section.
"""

from __future__ import annotations

from datetime import datetime, timezone


def parse_db_timestamp(value: str) -> datetime:
    """Both stores' timestamp columns end up as ISO-ish strings by the
    time they reach Python, but not identically: `CollaborationStore`
    (SQLite) writes `datetime('now')`, which comes back space-separated
    and naive (`"2026-07-29 12:00:00"`, implicitly UTC); `PgCollaborationStore`
    (Postgres `TIMESTAMPTZ`) comes back already `T`-separated with a real
    offset. Normalizing both to an aware UTC `datetime` here, once, is
    what `maintenance.py`'s staleness check and `draft_changelog.py`'s
    "completed since X" filter both need and would otherwise each
    reimplement slightly differently."""
    parsed = datetime.fromisoformat(value.replace(" ", "T"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


#: Fixed vocabulary, validated at the store layer -- same convention
#: `accounts.ROLES` already established. `open` and `planned` are
#: synonyms for "not yet claimed" (kept both for backward compatibility --
#: every work item created before 2026-07-28 has status='open'); every
#: other value is a new lifecycle stage a claimed item moves through on
#: its way to `completed`. Order here is the pipeline's canonical order,
#: used to drive Mission Control's lifecycle visualization -- it is not
#: an enforced state machine (see `store.py::update_status`'s docstring
#: for why transitions are advisory, not validated against this order).
STATUSES = (
    "open", "planned", "claimed", "in_progress", "testing",
    "ready_for_review", "merged", "completed",
)
#: The subset `STATUSES` a work item can be manually moved through via
#: `update_status` -- excludes `open`/`claimed`/`completed`, which stay
#: reachable only via claim/release/complete/reassign (unchanged Phase 1
#: behavior) so existing callers/tests keep working unmodified.
MANUAL_STATUSES = ("planned", "in_progress", "testing", "ready_for_review", "merged")
PRIORITIES = ("low", "medium", "high", "critical")
RISK_LEVELS = ("no_risk", "low", "medium", "high", "critical")
#: Who owns a work item -- `ai` lets Mission Control's "AI Workers" view
#: and the pre-work-check's coordination advice distinguish an
#: AI-assisted session's own claimed work from a human teammate's.
OWNER_TYPES = ("human", "ai")
#: SIL Phase 6 "Integration Coordinator" Milestone 1 -- a worker's
#: self-reported liveness state. `offline` is never set automatically by
#: a background scheduler in Milestone 1 (staleness is computed live at
#: read time from `last_heartbeat_at`, never written back) -- a worker
#: only reports `offline` itself, e.g. on graceful shutdown.
WORKER_STATUSES = ("online", "idle", "offline")
#: Deliberately NOT `OWNER_TYPES` ("human"/"ai" only) -- the Worker
#: Registry is a generic platform component, not something specific to
#: Claude Code, and must not hardcode an assumption that only Claude Code
#: sessions will ever connect. A plain validated Python tuple (not a DB
#: CHECK constraint), so recognizing a new kind of worker later is a
#: one-line addition here, never a migration. Extend this list, don't
#: replace it, as new worker kinds show up in practice.
WORKER_TYPES = (
    "human",                     # a human developer, tracked directly (not through accounts/ -- no auth to tie to yet)
    "claude_code_session",       # an interactive Claude Code session (this project's own primary AI worker today)
    "ai_agent",                  # any other AI agent/assistant, present or future, not specifically Claude Code
    "validation_worker",         # a process whose job is running tests/checks, not authoring changes
    "research_worker",           # an automated research/backtesting/optimization job
    "background_service",        # this process's own schedulers (git_watcher/maintenance/git_sync) self-reporting, if ever wired in
    "distributed_compute_worker", # a future networked compute contributor (see ROADMAP.md's "Future" section)
)

#: A worker not heard from in this many seconds is considered stale.
#: Originally defined only in `api/worker_service.py` (Milestone 1, where
#: staleness was only ever *read*); moved here in SIL Phase 6 Milestone 2
#: so `collaboration/maintenance.py`'s new stale-worker cleanup and the
#: API layer's read-time `is_stale` computation share one definition
#: instead of two independently-tunable copies. `maintenance.py` living in
#: this package (not `api/`) means the shared definition has to live here
#: too, not in `api/worker_service.py` -- `collaboration/` must not import
#: from `api/` (see docs/ARCHITECTURE.md's dependency-direction rules).
#: Deliberately generous relative to the recommended 30-60s+ heartbeat
#: interval (KNOWN_ISSUES.md ISSUE-038's measured SQLite concurrent-writer
#: ceiling favors an infrequent heartbeat) -- a worker should survive
#: missing one or two heartbeats (a slow cycle, a brief network blip)
#: without flapping to "stale."
WORKER_STALE_AFTER_SECONDS = 180.0


def is_worker_stale(worker: dict, now: datetime) -> tuple[bool, float]:
    """Returns `(is_stale, seconds_since_heartbeat)` -- always computed
    fresh from `last_heartbeat_at`, never stored on the row itself (see
    `store.py::heartbeat_worker`'s docstring)."""
    heartbeat_at = parse_db_timestamp(worker["last_heartbeat_at"])
    seconds = (now - heartbeat_at).total_seconds()
    return seconds >= WORKER_STALE_AFTER_SECONDS, seconds
