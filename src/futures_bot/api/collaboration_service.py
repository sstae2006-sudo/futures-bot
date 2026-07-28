"""API-facing logic for the Active Work Registry (Team Collaboration MVP)
-- own module, matching `accounts_service.py`'s precedent for keeping this
feature area modular rather than growing the already-large `services.py`.
"""

from __future__ import annotations

import uuid
from typing import Optional

from ..collaboration.overlap import detect_overlap
from ..collaboration.store import CollaborationError, get_collaboration_store
from .schemas import OverlapWarningOut, WorkItemActivityOut, WorkItemOut
from .services import ApiError


def create_work_item(
    *, title: str, description: Optional[str] = None, owner_user_id: Optional[str] = None,
    branch: Optional[str] = None, estimated_files: Optional[list[str]] = None, priority: str = "medium",
) -> tuple[WorkItemOut, list[OverlapWarningOut]]:
    """Creates the work item, then checks it for file overlap against
    every other currently-active item -- "before any task begins,
    analyze... assign a risk level... do not block work, warn only" is
    exactly this call's contract: the item is created either way, the
    warnings are informational."""
    store = get_collaboration_store()
    try:
        item_id = uuid.uuid4().hex[:12]
        item = store.create_work_item(
            item_id=item_id, title=title, description=description, owner_user_id=owner_user_id,
            branch=branch, estimated_files=estimated_files, priority=priority,
        )
    except CollaborationError as exc:
        raise ApiError(str(exc)) from exc

    active = store.fetch_active_work_items(exclude_id=item_id)
    warnings = detect_overlap(estimated_files or [], active)
    return WorkItemOut(**item), [OverlapWarningOut(**w.__dict__) for w in warnings]


def list_work_items(status: Optional[str] = None) -> list[WorkItemOut]:
    store = get_collaboration_store()
    return [WorkItemOut(**i) for i in store.fetch_work_items(status=status)]


def get_work_item(item_id: str) -> WorkItemOut:
    store = get_collaboration_store()
    item = store.fetch_work_item(item_id)
    if item is None:
        raise ApiError(f"No such work item: {item_id!r}")
    return WorkItemOut(**item)


def check_overlap(item_id: str) -> list[OverlapWarningOut]:
    """Recomputes overlap for an existing item against every other
    currently-active item -- lets a client re-check after other work
    items have come and gone, not just at creation time."""
    store = get_collaboration_store()
    item = store.fetch_work_item(item_id)
    if item is None:
        raise ApiError(f"No such work item: {item_id!r}")
    active = store.fetch_active_work_items(exclude_id=item_id)
    warnings = detect_overlap(item["estimated_files"], active)
    return [OverlapWarningOut(**w.__dict__) for w in warnings]


def claim_work_item(item_id: str, user_id: str) -> WorkItemOut:
    store = get_collaboration_store()
    try:
        item = store.claim_work_item(item_id, user_id)
    except CollaborationError as exc:
        raise ApiError(str(exc)) from exc
    return WorkItemOut(**item)


def release_work_item(item_id: str, actor_user_id: Optional[str] = None) -> WorkItemOut:
    store = get_collaboration_store()
    try:
        item = store.release_work_item(item_id, actor_user_id=actor_user_id)
    except CollaborationError as exc:
        raise ApiError(str(exc)) from exc
    return WorkItemOut(**item)


def complete_work_item(item_id: str, actor_user_id: Optional[str] = None) -> WorkItemOut:
    store = get_collaboration_store()
    try:
        item = store.complete_work_item(item_id, actor_user_id=actor_user_id)
    except CollaborationError as exc:
        raise ApiError(str(exc)) from exc
    return WorkItemOut(**item)


def reassign_work_item(item_id: str, new_owner_user_id: str, actor_user_id: Optional[str] = None) -> WorkItemOut:
    store = get_collaboration_store()
    try:
        item = store.reassign_work_item(item_id, new_owner_user_id, actor_user_id=actor_user_id)
    except CollaborationError as exc:
        raise ApiError(str(exc)) from exc
    return WorkItemOut(**item)


def list_activity(work_item_id: Optional[str] = None, limit: int = 100) -> list[WorkItemActivityOut]:
    store = get_collaboration_store()
    return [WorkItemActivityOut(**a) for a in store.fetch_activity(work_item_id=work_item_id, limit=limit)]
