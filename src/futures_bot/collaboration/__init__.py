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
