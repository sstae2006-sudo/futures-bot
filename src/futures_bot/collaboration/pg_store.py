"""TimescaleDB/Postgres-backed Active Work Registry -- the team-deployment
counterpart to `collaboration/store.py::CollaborationStore`, same public
method surface, selected instead of it by `get_collaboration_store()`
whenever `FUTURES_BOT_DATABASE_URL` is set. Built on the shared pooled
`db.engine.get_engine()`, same design as `PgAccountStore`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import func, or_, select
from sqlalchemy.engine import Engine

from . import MANUAL_STATUSES, OWNER_TYPES, PRIORITIES
from .store import CollaborationError
from ..db.engine import get_engine
from ..db.research_schema import metadata, work_item_activity, work_items


def _isoformat_datetimes(d: dict) -> dict:
    return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in d.items()}


def _row_dict(row) -> Optional[dict]:
    return _isoformat_datetimes(dict(row._mapping)) if row is not None else None


class PgCollaborationStore:
    """Pooled, Postgres-backed. Holds no connection of its own -- every
    method borrows one from the shared `Engine`'s pool for the duration of
    that call, same design as `PgAccountStore`."""

    def __init__(self, engine: Optional[Engine] = None) -> None:
        self._engine = engine or get_engine()

    def ensure_schema(self) -> None:
        with self._engine.begin() as conn:
            metadata.create_all(conn, checkfirst=True)

    def close(self) -> None:
        """No-op: see `PgAccountStore.close`'s identical docstring."""

    def _log_activity(self, conn, work_item_id: str, event: str, actor_user_id: Optional[str], detail: Optional[str]) -> None:
        conn.execute(work_item_activity.insert().values(
            id=uuid.uuid4().hex[:12], work_item_id=work_item_id, event=event,
            actor_user_id=actor_user_id, detail=detail,
        ))

    # --- Work items ---

    def create_work_item(
        self, *, item_id: str, title: str, description: Optional[str] = None,
        owner_user_id: Optional[str] = None, branch: Optional[str] = None,
        estimated_files: Optional[list[str]] = None, priority: str = "medium",
        owner_type: str = "human", org_id: Optional[str] = None,
    ) -> dict:
        if priority not in PRIORITIES:
            raise CollaborationError(f"Unknown priority {priority!r} -- must be one of {PRIORITIES}.")
        if owner_type not in OWNER_TYPES:
            raise CollaborationError(f"Unknown owner_type {owner_type!r} -- must be one of {OWNER_TYPES}.")
        status = "claimed" if owner_user_id else "open"
        with self._engine.begin() as conn:
            conn.execute(work_items.insert().values(
                id=item_id, title=title, description=description, owner_user_id=owner_user_id,
                branch=branch, status=status, estimated_files=estimated_files or [], priority=priority,
                owner_type=owner_type, org_id=org_id,
            ))
            self._log_activity(conn, item_id, "created", owner_user_id, f"status={status}")
        return self.fetch_work_item(item_id)  # type: ignore[return-value]

    def fetch_work_item(self, item_id: str) -> Optional[dict]:
        with self._engine.connect() as conn:
            row = conn.execute(select(work_items).where(work_items.c.id == item_id)).first()
        return _row_dict(row)

    def fetch_work_items(self, *, status: Optional[str] = None, org_id: Optional[str] = None) -> list[dict]:
        stmt = select(work_items).order_by(work_items.c.created_at.desc())
        if status is not None:
            stmt = stmt.where(work_items.c.status == status)
        if org_id is not None:
            stmt = stmt.where(work_items.c.org_id == org_id)
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).fetchall()
        return [_row_dict(r) for r in rows]

    def fetch_active_work_items(self, *, exclude_id: Optional[str] = None, org_id: Optional[str] = None) -> list[dict]:
        items = [i for i in self.fetch_work_items(org_id=org_id) if i["status"] != "completed"]
        if exclude_id is not None:
            items = [i for i in items if i["id"] != exclude_id]
        return items

    def claim_work_item(self, item_id: str, user_id: str) -> dict:
        """Mirrors `CollaborationStore.claim_work_item`'s atomic-claim fix
        -- see that method's docstring for the concurrent-claim race this
        guards against. The `WHERE` clause re-checks ownership at write
        time; `rowcount == 0` means someone else won the race."""
        item = self.fetch_work_item(item_id)
        if item is None:
            raise CollaborationError(f"No such work item: {item_id!r}.")
        if item["status"] == "claimed" and item["owner_user_id"] not in (None, user_id):
            raise CollaborationError(f"Work item {item_id!r} is already claimed by {item['owner_user_id']!r}.")
        with self._engine.begin() as conn:
            result = conn.execute(work_items.update().where(
                work_items.c.id == item_id,
                or_(work_items.c.owner_user_id.is_(None), work_items.c.owner_user_id == user_id),
            ).values(status="claimed", owner_user_id=user_id, updated_at=func.now()))
            if result.rowcount == 0:
                current = self.fetch_work_item(item_id)
                owner = current["owner_user_id"] if current is not None else None
                raise CollaborationError(f"Work item {item_id!r} is already claimed by {owner!r}.")
            self._log_activity(conn, item_id, "claimed", user_id, None)
        return self.fetch_work_item(item_id)  # type: ignore[return-value]

    def release_work_item(self, item_id: str, *, actor_user_id: Optional[str] = None) -> dict:
        if self.fetch_work_item(item_id) is None:
            raise CollaborationError(f"No such work item: {item_id!r}.")
        with self._engine.begin() as conn:
            conn.execute(work_items.update().where(work_items.c.id == item_id).values(
                status="open", owner_user_id=None, updated_at=func.now(),
            ))
            self._log_activity(conn, item_id, "released", actor_user_id, None)
        return self.fetch_work_item(item_id)  # type: ignore[return-value]

    def complete_work_item(self, item_id: str, *, actor_user_id: Optional[str] = None) -> dict:
        if self.fetch_work_item(item_id) is None:
            raise CollaborationError(f"No such work item: {item_id!r}.")
        with self._engine.begin() as conn:
            conn.execute(work_items.update().where(work_items.c.id == item_id).values(
                status="completed", updated_at=func.now(),
            ))
            self._log_activity(conn, item_id, "completed", actor_user_id, None)
        return self.fetch_work_item(item_id)  # type: ignore[return-value]

    def reassign_work_item(self, item_id: str, new_owner_user_id: str, *, actor_user_id: Optional[str] = None) -> dict:
        if self.fetch_work_item(item_id) is None:
            raise CollaborationError(f"No such work item: {item_id!r}.")
        with self._engine.begin() as conn:
            conn.execute(work_items.update().where(work_items.c.id == item_id).values(
                status="claimed", owner_user_id=new_owner_user_id, updated_at=func.now(),
            ))
            self._log_activity(conn, item_id, "reassigned", actor_user_id, f"new_owner={new_owner_user_id}")
        return self.fetch_work_item(item_id)  # type: ignore[return-value]

    def update_status(self, item_id: str, new_status: str, *, actor_user_id: Optional[str] = None) -> dict:
        """Mirrors `CollaborationStore.update_status` -- see its docstring
        for why transitions are advisory, not a validated state machine."""
        item = self.fetch_work_item(item_id)
        if item is None:
            raise CollaborationError(f"No such work item: {item_id!r}.")
        if new_status not in MANUAL_STATUSES:
            raise CollaborationError(
                f"Unknown status {new_status!r} -- must be one of {MANUAL_STATUSES} "
                "(use claim/release/complete for open/claimed/completed)."
            )
        with self._engine.begin() as conn:
            conn.execute(work_items.update().where(work_items.c.id == item_id).values(
                status=new_status, updated_at=func.now(),
            ))
            self._log_activity(conn, item_id, "status_changed", actor_user_id, f"{item['status']}->{new_status}")
        return self.fetch_work_item(item_id)  # type: ignore[return-value]

    # --- Activity log ---

    def fetch_activity(self, *, work_item_id: Optional[str] = None, limit: int = 100) -> list[dict]:
        """No `rowid`-style tiebreaker needed here the way
        `CollaborationStore.fetch_activity` needs one for SQLite:
        `created_at`'s default here is `func.now()` evaluated per
        `self._engine.begin()` transaction (each store method opens its
        own), giving real microsecond-resolution, effectively-unique
        timestamps for sequential calls -- unlike SQLite's
        `datetime('now')`, which only has second resolution and ties
        easily under rapid/automated activity."""
        stmt = select(work_item_activity).order_by(work_item_activity.c.created_at.desc()).limit(limit)
        if work_item_id is not None:
            stmt = stmt.where(work_item_activity.c.work_item_id == work_item_id)
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).fetchall()
        return [_row_dict(r) for r in rows]
