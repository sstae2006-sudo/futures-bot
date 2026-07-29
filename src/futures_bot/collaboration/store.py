"""SQLite storage for the Active Work Registry -- work items (claimable
units of work, human or AI) plus an append-only activity log of every
claim/release/complete/reassign transition. Lives in `research.db`, same
file/connection-per-call convention `accounts/store.py` already
establishes (see that module's docstring for the full rationale).
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from pathlib import Path
from typing import Optional

from ..research.trade_store import default_db_path
from . import MANUAL_STATUSES, OWNER_TYPES, PRIORITIES, WORKER_STATUSES, WORKER_TYPES

#: Same convention `accounts/store.py`/`api/store.py::get_store()` already
#: established.
_DATABASE_URL_ENV = "FUTURES_BOT_DATABASE_URL"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS work_items (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    description     TEXT,
    owner_user_id   TEXT,
    branch          TEXT,
    status          TEXT NOT NULL DEFAULT 'open',
    estimated_files TEXT NOT NULL DEFAULT '[]',
    priority        TEXT NOT NULL DEFAULT 'medium',
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_work_items_status ON work_items(status);

CREATE TABLE IF NOT EXISTS work_item_activity (
    id              TEXT PRIMARY KEY,
    work_item_id    TEXT NOT NULL,
    event           TEXT NOT NULL,
    actor_user_id   TEXT,
    detail          TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (work_item_id) REFERENCES work_items(id)
);
CREATE INDEX IF NOT EXISTS idx_work_item_activity_item ON work_item_activity(work_item_id, created_at);
-- SIL Phase 6 audit finding: fetch_activity(work_item_id=None) (the
-- global-timeline path GET /api/activity/timeline uses) filters on
-- nothing but sorts by created_at -- the composite index above only
-- helps once work_item_id is constrained, so this query was a full
-- table scan + sort with no index to lean on. CREATE INDEX IF NOT
-- EXISTS is itself idempotent/retroactive, unlike a new column -- no
-- ALTER-TABLE-if-missing dance needed the way _WORK_ITEM_COLLABORATION_
-- COLUMNS requires for columns.
CREATE INDEX IF NOT EXISTS idx_work_item_activity_created_at ON work_item_activity(created_at);

-- SIL Phase 6 "Integration Coordinator" Milestone 1. `id` is
-- caller-supplied (not minted via uuid.uuid4() the way work_items.id
-- is) -- a worker (an AI session, a background job) needs a stable id
-- it already knows so repeated heartbeats land on the same row across
-- process restarts. `current_work_item_id` is a soft reference, same
-- "no real FK, informal by convention" precedent work_items.owner_user_id
-- already establishes. `last_heartbeat_at` is the only liveness signal
-- persisted -- staleness itself is always computed live at read time
-- (collaboration_service.py), never stored, matching every other
-- liveness-adjacent computation in this package. `worker_type` is
-- WORKER_TYPES (a generic platform vocabulary), deliberately NOT
-- OWNER_TYPES ("human"/"ai" only) -- this registry must not hardcode an
-- assumption that only Claude Code will ever connect. `capabilities` is
-- a JSON-as-TEXT list of free-form tags, same pattern estimated_files
-- already establishes -- queryable via Python-side filtering, not a SQL
-- JSON query, matching how fetch_draft_work_items already filters over
-- fetch_work_items's result rather than pushing that into SQL.
CREATE TABLE IF NOT EXISTS workers (
    id                    TEXT PRIMARY KEY,
    worker_type           TEXT NOT NULL DEFAULT 'ai_agent',
    display_name          TEXT NOT NULL,
    user_id               TEXT,
    org_id                TEXT,
    status                TEXT NOT NULL DEFAULT 'online',
    current_work_item_id  TEXT,
    subsystem             TEXT,
    capabilities          TEXT NOT NULL DEFAULT '[]',
    last_heartbeat_at     TEXT NOT NULL DEFAULT (datetime('now')),
    created_at            TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at            TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_workers_org_id ON workers(org_id);
CREATE INDEX IF NOT EXISTS idx_workers_status ON workers(status);
"""


class CollaborationError(ValueError):
    """Raised for a bad status/priority, claiming already-claimed work,
    or a reference to a work item that doesn't exist -- callers (the API
    layer) map this to a 400, same convention `accounts.store.AccountError`
    already establishes."""


def _row_with_files(row: dict) -> dict:
    row = dict(row)
    try:
        row["estimated_files"] = json.loads(row["estimated_files"])
    except (TypeError, json.JSONDecodeError):
        row["estimated_files"] = []
    if "is_draft" in row:
        row["is_draft"] = bool(row["is_draft"])  # SQLite has no native boolean -- stored/read as 0/1
    return row


def _row_with_capabilities(row: dict) -> dict:
    row = dict(row)
    try:
        row["capabilities"] = json.loads(row["capabilities"])
    except (TypeError, json.JSONDecodeError):
        row["capabilities"] = []
    return row


class CollaborationStore:
    """Owns one SQLite connection. Not thread-safe -- open one per
    request/script, same convention `TradeStore`/`AccountStore` document
    for themselves."""

    #: SIL Phase 2 ("Workflow Integration", 2026-07-28): `owner_type` added
    #: to `work_items` after that table already shipped -- `CREATE TABLE IF
    #: NOT EXISTS` can't retrofit a column onto an existing table, so it's
    #: added via `ALTER TABLE` here if missing, same "ALTER TABLE if
    #: missing" pattern `research/trade_store.py::ensure_schema` already
    #: established. Every existing row backfills to 'human' via the column
    #: default -- correct for every work item created before this existed,
    #: since Phase 1 had no AI/human distinction at all.
    #: `org_id` added for Registration & Organization Management
    #: (2026-07-28) -- nullable: a work item created before organizations
    #: existed (or by a caller that never supplies one, e.g. the CLI used
    #: without a session) is simply unscoped, visible regardless of which
    #: org is asking. See `fetch_work_items`/`fetch_active_work_items`'s
    #: `org_id` filter for how that's applied.
    #: `is_draft` added for SIL Phase 4's automatic git-watcher
    #: (2026-07-29) -- a draft is a normal work item in every other respect
    #: (visible in `fetch_work_items`/`fetch_active_work_items`, subject to
    #: overlap detection) except that it was created by the background
    #: watcher rather than a human/AI session, and must be explicitly
    #: approved (clears this flag) or discarded (hard-deletes the row --
    #: see `discard_draft_work_item`) rather than silently acted on. Every
    #: existing row backfills to `false` via the column default.
    _WORK_ITEM_COLLABORATION_COLUMNS = (
        ("owner_type", "TEXT NOT NULL DEFAULT 'human'"),
        ("org_id", "TEXT"),
        ("is_draft", "BOOLEAN NOT NULL DEFAULT 0"),
    )

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self.ensure_schema()

    def ensure_schema(self) -> None:
        """Idempotent -- safe to call on every startup, same convention
        every other store in this codebase follows."""
        self._conn.executescript(_SCHEMA)
        existing_columns = {row[1] for row in self._conn.execute("PRAGMA table_info(work_items)")}
        for name, sqltype in self._WORK_ITEM_COLLABORATION_COLUMNS:
            if name not in existing_columns:
                self._conn.execute(f"ALTER TABLE work_items ADD COLUMN {name} {sqltype}")
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def _log_activity(self, work_item_id: str, event: str, actor_user_id: Optional[str], detail: Optional[str]) -> None:
        self._conn.execute(
            "INSERT INTO work_item_activity (id, work_item_id, event, actor_user_id, detail) VALUES (?,?,?,?,?)",
            (uuid.uuid4().hex[:12], work_item_id, event, actor_user_id, detail),
        )

    # --- Work items ---

    def create_work_item(
        self, *, item_id: str, title: str, description: Optional[str] = None,
        owner_user_id: Optional[str] = None, branch: Optional[str] = None,
        estimated_files: Optional[list[str]] = None, priority: str = "medium",
        owner_type: str = "human", org_id: Optional[str] = None, is_draft: bool = False,
    ) -> dict:
        if priority not in PRIORITIES:
            raise CollaborationError(f"Unknown priority {priority!r} -- must be one of {PRIORITIES}.")
        if owner_type not in OWNER_TYPES:
            raise CollaborationError(f"Unknown owner_type {owner_type!r} -- must be one of {OWNER_TYPES}.")
        status = "claimed" if owner_user_id else "open"
        self._conn.execute(
            """
            INSERT INTO work_items (id, title, description, owner_user_id, branch, status, estimated_files, priority, owner_type, org_id, is_draft)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (item_id, title, description, owner_user_id, branch, status,
             json.dumps(estimated_files or []), priority, owner_type, org_id, is_draft),
        )
        self._log_activity(item_id, "created", owner_user_id, f"status={status}" + (" draft=true" if is_draft else ""))
        self._conn.commit()
        return self.fetch_work_item(item_id)  # type: ignore[return-value]

    # --- Drafts (SIL Phase 4's git-watcher) ---

    def fetch_draft_work_items(self, *, org_id: Optional[str] = None) -> list[dict]:
        return [i for i in self.fetch_work_items(org_id=org_id) if i["is_draft"]]

    def approve_draft_work_item(self, item_id: str, *, actor_user_id: Optional[str] = None) -> dict:
        """Promotes a draft to a real work item -- clears `is_draft`,
        nothing else about the row changes. Raises if the item doesn't
        exist or isn't currently a draft, same "explicit precondition,
        explicit error" convention every other lifecycle transition here
        follows (see `claim_work_item`)."""
        item = self.fetch_work_item(item_id)
        if item is None:
            raise CollaborationError(f"No such work item: {item_id!r}.")
        if not item["is_draft"]:
            raise CollaborationError(f"Work item {item_id!r} is not a draft.")
        self._conn.execute(
            "UPDATE work_items SET is_draft = 0, updated_at=datetime('now') WHERE id = ?", (item_id,),
        )
        self._log_activity(item_id, "draft_approved", actor_user_id, None)
        self._conn.commit()
        return self.fetch_work_item(item_id)  # type: ignore[return-value]

    def discard_draft_work_item(self, item_id: str, *, actor_user_id: Optional[str] = None) -> None:
        """Hard-deletes a draft -- the one place this store deletes a work
        item at all. Deliberately refuses on a non-draft item: a real
        (human/AI-claimed or already-approved) work item is never
        deletable through this path, only ever completed/released/
        reassigned, so a caller can't accidentally destroy real work by
        reusing this method."""
        item = self.fetch_work_item(item_id)
        if item is None:
            raise CollaborationError(f"No such work item: {item_id!r}.")
        if not item["is_draft"]:
            raise CollaborationError(f"Work item {item_id!r} is not a draft -- refusing to delete a real work item.")
        self._conn.execute("DELETE FROM work_item_activity WHERE work_item_id = ?", (item_id,))
        self._conn.execute("DELETE FROM work_items WHERE id = ?", (item_id,))
        self._conn.commit()

    def fetch_work_item(self, item_id: str) -> Optional[dict]:
        self._conn.row_factory = sqlite3.Row
        row = self._conn.execute("SELECT * FROM work_items WHERE id = ?", (item_id,)).fetchone()
        self._conn.row_factory = None
        return _row_with_files(dict(row)) if row is not None else None

    def fetch_work_items(self, *, status: Optional[str] = None, org_id: Optional[str] = None) -> list[dict]:
        """`org_id=None` means "every work item regardless of org" (the
        pre-multi-org default, and what a session-less caller like the CLI
        still gets) -- it does NOT mean "only unscoped items." Pass a real
        `org_id` to scope the result to one organization's own work."""
        self._conn.row_factory = sqlite3.Row
        clauses, params = [], []
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if org_id is not None:
            clauses.append("org_id = ?")
            params.append(org_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(f"SELECT * FROM work_items {where} ORDER BY created_at DESC", params).fetchall()
        self._conn.row_factory = None
        return [_row_with_files(dict(r)) for r in rows]

    def fetch_active_work_items(self, *, exclude_id: Optional[str] = None, org_id: Optional[str] = None) -> list[dict]:
        """Every work item not yet completed -- what overlap detection
        checks a proposed new task against."""
        items = [i for i in self.fetch_work_items(org_id=org_id) if i["status"] != "completed"]
        if exclude_id is not None:
            items = [i for i in items if i["id"] != exclude_id]
        return items

    def claim_work_item(self, item_id: str, user_id: str) -> dict:
        """The `UPDATE`'s `WHERE` clause re-checks the same ownership
        condition the initial read already checked in Python, and
        `rowcount` tells us whether it actually applied -- not just a
        defensive-looking duplicate check. Two concurrent callers (two
        humans, or two AI sessions, racing to claim the same item) can
        both pass the initial Python-level check before either writes;
        without the `WHERE` guard here, both unconditional `UPDATE`s would
        each "succeed" and whichever commits last would silently win,
        with *both* callers getting back a 200 claiming ownership even
        though only one of them actually holds it (a lost-update
        anomaly, not a raised error either caller could react to). With
        the guard, the loser's `UPDATE` matches zero rows, and this
        raises the same `CollaborationError` a sequential double-claim
        already raises -- confirmed via a real two-thread regression test
        (Stabilization Mode, 2026-07-28)."""
        item = self.fetch_work_item(item_id)
        if item is None:
            raise CollaborationError(f"No such work item: {item_id!r}.")
        if item["status"] == "claimed" and item["owner_user_id"] not in (None, user_id):
            raise CollaborationError(
                f"Work item {item_id!r} is already claimed by {item['owner_user_id']!r}."
            )
        cursor = self._conn.execute(
            """
            UPDATE work_items SET status='claimed', owner_user_id=?, updated_at=datetime('now')
            WHERE id = ? AND (owner_user_id IS NULL OR owner_user_id = ?)
            """,
            (user_id, item_id, user_id),
        )
        if cursor.rowcount == 0:
            self._conn.commit()  # release the write lock even on the losing path
            current = self.fetch_work_item(item_id)
            owner = current["owner_user_id"] if current is not None else None
            raise CollaborationError(f"Work item {item_id!r} is already claimed by {owner!r}.")
        self._log_activity(item_id, "claimed", user_id, None)
        self._conn.commit()
        return self.fetch_work_item(item_id)  # type: ignore[return-value]

    def release_work_item(self, item_id: str, *, actor_user_id: Optional[str] = None) -> dict:
        if self.fetch_work_item(item_id) is None:
            raise CollaborationError(f"No such work item: {item_id!r}.")
        self._conn.execute(
            "UPDATE work_items SET status='open', owner_user_id=NULL, updated_at=datetime('now') WHERE id = ?",
            (item_id,),
        )
        self._log_activity(item_id, "released", actor_user_id, None)
        self._conn.commit()
        return self.fetch_work_item(item_id)  # type: ignore[return-value]

    def complete_work_item(self, item_id: str, *, actor_user_id: Optional[str] = None) -> dict:
        if self.fetch_work_item(item_id) is None:
            raise CollaborationError(f"No such work item: {item_id!r}.")
        self._conn.execute(
            "UPDATE work_items SET status='completed', updated_at=datetime('now') WHERE id = ?",
            (item_id,),
        )
        self._log_activity(item_id, "completed", actor_user_id, None)
        self._conn.commit()
        return self.fetch_work_item(item_id)  # type: ignore[return-value]

    def reassign_work_item(self, item_id: str, new_owner_user_id: str, *, actor_user_id: Optional[str] = None) -> dict:
        if self.fetch_work_item(item_id) is None:
            raise CollaborationError(f"No such work item: {item_id!r}.")
        self._conn.execute(
            "UPDATE work_items SET status='claimed', owner_user_id=?, updated_at=datetime('now') WHERE id = ?",
            (new_owner_user_id, item_id),
        )
        self._log_activity(item_id, "reassigned", actor_user_id, f"new_owner={new_owner_user_id}")
        self._conn.commit()
        return self.fetch_work_item(item_id)  # type: ignore[return-value]

    def update_status(self, item_id: str, new_status: str, *, actor_user_id: Optional[str] = None) -> dict:
        """Moves a work item to one of the intermediate lifecycle stages
        (`in_progress`, `testing`, `ready_for_review`, etc. -- see
        `collaboration.MANUAL_STATUSES`). Deliberately does NOT validate
        that `new_status` is a "forward" move in `STATUSES`' pipeline
        order: a real workflow needs to go backward too (a review finds a
        problem, `ready_for_review` -> `in_progress`), and a hard state
        machine would just get bypassed via release+reassign anyway. The
        pipeline order in `STATUSES` is advisory (drives the Mission
        Control visualization), not enforced here -- matches this
        package's existing warn-only philosophy (see `overlap.py`'s
        docstring) applied to workflow instead of file conflicts. Use
        `claim_work_item`/`release_work_item`/`complete_work_item` for
        `claimed`/`open`/`completed` -- those three keep their existing,
        separately-tested ownership semantics unchanged."""
        item = self.fetch_work_item(item_id)
        if item is None:
            raise CollaborationError(f"No such work item: {item_id!r}.")
        if new_status not in MANUAL_STATUSES:
            raise CollaborationError(
                f"Unknown status {new_status!r} -- must be one of {MANUAL_STATUSES} "
                "(use claim/release/complete for open/claimed/completed)."
            )
        self._conn.execute(
            "UPDATE work_items SET status=?, updated_at=datetime('now') WHERE id = ?",
            (new_status, item_id),
        )
        self._log_activity(item_id, "status_changed", actor_user_id, f"{item['status']}->{new_status}")
        self._conn.commit()
        return self.fetch_work_item(item_id)  # type: ignore[return-value]

    # --- Activity log ---

    def fetch_activity(self, *, work_item_id: Optional[str] = None, limit: int = 100) -> list[dict]:
        """`ORDER BY created_at DESC, rowid DESC` -- `created_at` alone is
        only second-resolution (SQLite's `datetime('now')`), so two
        events logged within the same second (entirely realistic: an
        automated/AI-driven claim-then-complete can land in the same
        second) would otherwise tie and sort in an unspecified order.
        `rowid` is SQLite's own implicit, monotonically-increasing
        insert-order column (this table's declared `id` is a random UUID
        TEXT primary key, so it doesn't alias rowid the way an `INTEGER
        PRIMARY KEY` would -- the hidden rowid still exists and is free
        to use as a tiebreaker). This is what makes `timeline.py`'s
        "most-recent-first" merge actually correct instead of merely
        usually-correct."""
        self._conn.row_factory = sqlite3.Row
        if work_item_id is not None:
            rows = self._conn.execute(
                "SELECT * FROM work_item_activity WHERE work_item_id = ? ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (work_item_id, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM work_item_activity ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,),
            ).fetchall()
        self._conn.row_factory = None
        return [dict(r) for r in rows]

    # --- Workers (SIL Phase 6 "Integration Coordinator" Milestone 1) ---

    def heartbeat_worker(
        self, *, worker_id: str, worker_type: str = "ai_agent", display_name: str,
        user_id: Optional[str] = None, org_id: Optional[str] = None, status: str = "online",
        current_work_item_id: Optional[str] = None, subsystem: Optional[str] = None,
        capabilities: Optional[list[str]] = None,
    ) -> dict:
        """Upsert, not update-only -- deliberately does NOT mirror
        `accounts/store.py::touch_last_active`'s update-only/404-if-missing
        precedent. That precedent exists because a user has a distinct,
        meaningful registration flow (create-organization then
        create-user) heartbeat must never substitute for; a worker (an
        AI session, a background job) has no equivalent signup moment,
        and there's no auth layer (KNOWN_ISSUES.md ISSUE-036) to make a
        separate "register" call mean anything extra beyond formality.
        Race safety: last-write-wins is *correct* semantics here (unlike
        `claim_work_item`'s guarded `WHERE`, which exists because
        ownership races are genuinely not benign) -- no rowcount-guard
        needed for "who's most recently alive." `current_work_item_id`
        is never validated against `work_items` -- a soft reference, same
        convention `owner_user_id` already establishes."""
        if worker_type not in WORKER_TYPES:
            raise CollaborationError(f"Unknown worker_type {worker_type!r} -- must be one of {WORKER_TYPES}.")
        if status not in WORKER_STATUSES:
            raise CollaborationError(f"Unknown status {status!r} -- must be one of {WORKER_STATUSES}.")
        capabilities_json = json.dumps(capabilities or [])
        existing = self.fetch_worker(worker_id)
        if existing is None:
            self._conn.execute(
                """
                INSERT INTO workers (id, worker_type, display_name, user_id, org_id, status, current_work_item_id, subsystem, capabilities)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (worker_id, worker_type, display_name, user_id, org_id, status, current_work_item_id, subsystem, capabilities_json),
            )
        else:
            self._conn.execute(
                """
                UPDATE workers SET worker_type=?, display_name=?, user_id=?, org_id=?, status=?,
                       current_work_item_id=?, subsystem=?, capabilities=?,
                       last_heartbeat_at=datetime('now'), updated_at=datetime('now')
                WHERE id = ?
                """,
                (worker_type, display_name, user_id, org_id, status, current_work_item_id, subsystem, capabilities_json, worker_id),
            )
        self._conn.commit()
        return self.fetch_worker(worker_id)  # type: ignore[return-value]

    def fetch_worker(self, worker_id: str) -> Optional[dict]:
        self._conn.row_factory = sqlite3.Row
        row = self._conn.execute("SELECT * FROM workers WHERE id = ?", (worker_id,)).fetchone()
        self._conn.row_factory = None
        return _row_with_capabilities(dict(row)) if row is not None else None

    def fetch_workers(
        self, *, org_id: Optional[str] = None, status: Optional[str] = None, capability: Optional[str] = None,
    ) -> list[dict]:
        """`capability` filters in Python, not SQL -- consistent with
        `fetch_draft_work_items`'s existing precedent of filtering over
        an already-fetched list rather than pushing a JSON-membership
        query down into SQLite, which has no native JSON indexing here
        anyway."""
        self._conn.row_factory = sqlite3.Row
        clauses, params = [], []
        if org_id is not None:
            clauses.append("org_id = ?")
            params.append(org_id)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(f"SELECT * FROM workers {where} ORDER BY last_heartbeat_at DESC", params).fetchall()
        self._conn.row_factory = None
        workers = [_row_with_capabilities(dict(r)) for r in rows]
        if capability is not None:
            workers = [w for w in workers if capability in w["capabilities"]]
        return workers


def get_collaboration_store():
    """A fresh store against the configured database -- `PgCollaborationStore`
    when `FUTURES_BOT_DATABASE_URL` is set (team-deployment mode), else
    `CollaborationStore()`. Same factory shape as
    `accounts/store.py::get_account_store()`."""
    if os.environ.get(_DATABASE_URL_ENV):
        from .pg_store import PgCollaborationStore

        store = PgCollaborationStore()
        store.ensure_schema()
        return store
    return CollaborationStore()
