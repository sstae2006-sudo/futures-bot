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
from . import MANUAL_STATUSES, OWNER_TYPES, PRIORITIES

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
    _WORK_ITEM_COLLABORATION_COLUMNS = (
        ("owner_type", "TEXT NOT NULL DEFAULT 'human'"),
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
        owner_type: str = "human",
    ) -> dict:
        if priority not in PRIORITIES:
            raise CollaborationError(f"Unknown priority {priority!r} -- must be one of {PRIORITIES}.")
        if owner_type not in OWNER_TYPES:
            raise CollaborationError(f"Unknown owner_type {owner_type!r} -- must be one of {OWNER_TYPES}.")
        status = "claimed" if owner_user_id else "open"
        self._conn.execute(
            """
            INSERT INTO work_items (id, title, description, owner_user_id, branch, status, estimated_files, priority, owner_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (item_id, title, description, owner_user_id, branch, status,
             json.dumps(estimated_files or []), priority, owner_type),
        )
        self._log_activity(item_id, "created", owner_user_id, f"status={status}")
        self._conn.commit()
        return self.fetch_work_item(item_id)  # type: ignore[return-value]

    def fetch_work_item(self, item_id: str) -> Optional[dict]:
        self._conn.row_factory = sqlite3.Row
        row = self._conn.execute("SELECT * FROM work_items WHERE id = ?", (item_id,)).fetchone()
        self._conn.row_factory = None
        return _row_with_files(dict(row)) if row is not None else None

    def fetch_work_items(self, *, status: Optional[str] = None) -> list[dict]:
        self._conn.row_factory = sqlite3.Row
        if status is not None:
            rows = self._conn.execute(
                "SELECT * FROM work_items WHERE status = ? ORDER BY created_at DESC", (status,),
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM work_items ORDER BY created_at DESC").fetchall()
        self._conn.row_factory = None
        return [_row_with_files(dict(r)) for r in rows]

    def fetch_active_work_items(self, *, exclude_id: Optional[str] = None) -> list[dict]:
        """Every work item not yet completed -- what overlap detection
        checks a proposed new task against."""
        items = [i for i in self.fetch_work_items() if i["status"] != "completed"]
        if exclude_id is not None:
            items = [i for i in items if i["id"] != exclude_id]
        return items

    def claim_work_item(self, item_id: str, user_id: str) -> dict:
        item = self.fetch_work_item(item_id)
        if item is None:
            raise CollaborationError(f"No such work item: {item_id!r}.")
        if item["status"] == "claimed" and item["owner_user_id"] not in (None, user_id):
            raise CollaborationError(
                f"Work item {item_id!r} is already claimed by {item['owner_user_id']!r}."
            )
        self._conn.execute(
            "UPDATE work_items SET status='claimed', owner_user_id=?, updated_at=datetime('now') WHERE id = ?",
            (user_id, item_id),
        )
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
        self._conn.row_factory = sqlite3.Row
        if work_item_id is not None:
            rows = self._conn.execute(
                "SELECT * FROM work_item_activity WHERE work_item_id = ? ORDER BY created_at DESC LIMIT ?",
                (work_item_id, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM work_item_activity ORDER BY created_at DESC LIMIT ?", (limit,),
            ).fetchall()
        self._conn.row_factory = None
        return [dict(r) for r in rows]


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
