"""A searchable project activity timeline -- merges the Active Work
Registry's own append-only activity log (`work_item_activity`: created/
claimed/released/completed/reassigned/status_changed) with real git
commits (`git_info.recent_commits`) into one feed, normalized to a common
shape and sortable by timestamp. This is what Mission Control's "Recent
Activity" view and SIL Phase 2's "searchable project timeline"
requirement are built on.

Filtering (`event_type`, `q`, `since`, `until`) happens in Python over an
already-fetched batch rather than as SQL predicates in `CollaborationStore`/
`PgCollaborationStore`: at this project's actual activity volume (a
handful of work items, not millions of rows) that's simpler and avoids
duplicating filter logic across two backend dialects (SQLite text
timestamps vs Postgres `timestamptz`) for a feature that doesn't need
SQL-level selectivity yet. If activity volume ever grows enough that this
matters, push the filters down into the store layer then -- not a
missing piece of this design, a deliberately deferred one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from . import git_info
from .store import get_collaboration_store

#: Generous enough that a typical query is never truncated before
#: filtering, without pulling in this project's entire history at once.
_FETCH_BATCH = 500


@dataclass(frozen=True)
class TimelineEntry:
    kind: str  # "work_item" | "commit"
    timestamp: str  # original, unparsed -- see `_sort_key` for how it's compared
    title: str
    detail: Optional[str]
    actor: Optional[str]
    work_item_id: Optional[str] = None


def _parse_timestamp(raw: str) -> datetime:
    """SQLite stores `datetime('now')` as `YYYY-MM-DD HH:MM:SS` (naive,
    implicitly UTC); Postgres/git both hand back real ISO 8601 with a
    timezone. Normalizing the SQLite shape the same way
    `TeamPanel.tsx::timeAgo()`'s fix already does (space -> 'T', assume
    UTC if no offset) lets every source sort correctly against the
    others."""
    candidate = raw.strip()
    has_timezone = candidate.endswith("Z") or (len(candidate) > 6 and candidate[-6] in "+-" and candidate[-3] == ":")
    normalized = candidate.replace(" ", "T", 1)
    if not has_timezone:
        normalized += "Z"
    return datetime.fromisoformat(normalized.replace("Z", "+00:00"))


def _activity_to_entry(row: dict) -> TimelineEntry:
    return TimelineEntry(
        kind="work_item", timestamp=row["created_at"], title=row["event"], detail=row.get("detail"),
        actor=row.get("actor_user_id"), work_item_id=row.get("work_item_id"),
    )


def _commit_to_entry(commit: "git_info.Commit") -> TimelineEntry:
    return TimelineEntry(
        kind="commit", timestamp=commit.authored_at or "", title=commit.subject,
        detail=commit.short_hash, actor=commit.author,
    )


def build_timeline(
    *, work_item_id: Optional[str] = None, event_type: Optional[str] = None, q: Optional[str] = None,
    since: Optional[str] = None, until: Optional[str] = None, include_commits: bool = True,
    commit_limit: int = 30, limit: int = 100,
) -> list[TimelineEntry]:
    store = get_collaboration_store()
    entries = [_activity_to_entry(a) for a in store.fetch_activity(work_item_id=work_item_id, limit=_FETCH_BATCH)]

    if include_commits and work_item_id is None:  # commits aren't tied to a specific work item
        entries += [_commit_to_entry(c) for c in git_info.recent_commits(limit=commit_limit)]

    if event_type is not None:
        entries = [e for e in entries if e.kind == "work_item" and e.title == event_type]
    if q:
        needle = q.lower()
        entries = [
            e for e in entries
            if needle in e.title.lower() or (e.detail and needle in e.detail.lower()) or (e.actor and needle in e.actor.lower())
        ]

    since_dt = _parse_timestamp(since) if since else None
    until_dt = _parse_timestamp(until) if until else None
    if since_dt is not None or until_dt is not None:
        filtered = []
        for e in entries:
            if not e.timestamp:
                continue
            try:
                ts = _parse_timestamp(e.timestamp)
            except ValueError:
                continue
            if since_dt is not None and ts < since_dt:
                continue
            if until_dt is not None and ts > until_dt:
                continue
            filtered.append(e)
        entries = filtered

    def _sort_key(entry: TimelineEntry):
        try:
            return _parse_timestamp(entry.timestamp) if entry.timestamp else datetime.min
        except ValueError:
            return datetime.min

    entries.sort(key=_sort_key, reverse=True)
    return entries[:limit]
