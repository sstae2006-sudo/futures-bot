"""API-facing logic for the Active Work Registry (Team Collaboration MVP)
-- own module, matching `accounts_service.py`'s precedent for keeping this
feature area modular rather than growing the already-large `services.py`.
"""

from __future__ import annotations

import uuid
from typing import Optional

from ..collaboration import git_info
from ..collaboration.git_info import BranchInfo, Commit
from ..collaboration.overlap import detect_overlap
from ..collaboration.store import CollaborationError, get_collaboration_store
from .schemas import BranchInfoOut, CommitOut, MergeSummaryOut, OverlapWarningOut, WorkItemActivityOut, WorkItemOut
from .services import ApiError

_RISK_SEVERITY = {"critical": 4, "high": 3, "medium": 2, "low": 1, "no_risk": 0}


def _commit_out(commit: Optional[Commit]) -> Optional[CommitOut]:
    if commit is None:
        return None
    return CommitOut(hash=commit.hash, short_hash=commit.short_hash, subject=commit.subject,
                      author=commit.author, authored_at=commit.authored_at)


def _branch_info_out(info: BranchInfo) -> BranchInfoOut:
    return BranchInfoOut(
        branch=info.branch, is_detached=info.is_detached, base_branch=info.base_branch,
        branch_age_days=info.branch_age_days, ahead=info.ahead, behind=info.behind,
        last_commit=_commit_out(info.last_commit), notes=list(info.notes),
    )


def get_branch_info(branch: Optional[str] = None) -> BranchInfoOut:
    return _branch_info_out(git_info.get_branch_info(branch=branch))


def get_work_item_branch_info(item_id: str) -> BranchInfoOut:
    """The work item's own `branch` field (set at creation time) is what
    ties it to a real git branch -- resolved live against the repo rather
    than trusting a value that could be stale by the time anyone looks."""
    store = get_collaboration_store()
    item = store.fetch_work_item(item_id)
    if item is None:
        raise ApiError(f"No such work item: {item_id!r}")
    return get_branch_info(branch=item.get("branch"))


def create_work_item(
    *, title: str, description: Optional[str] = None, owner_user_id: Optional[str] = None,
    branch: Optional[str] = None, estimated_files: Optional[list[str]] = None, priority: str = "medium",
    owner_type: str = "human",
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
            branch=branch, estimated_files=estimated_files, priority=priority, owner_type=owner_type,
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


def update_work_item_status(item_id: str, new_status: str, actor_user_id: Optional[str] = None) -> WorkItemOut:
    store = get_collaboration_store()
    try:
        item = store.update_status(item_id, new_status, actor_user_id=actor_user_id)
    except CollaborationError as exc:
        raise ApiError(str(exc)) from exc
    return WorkItemOut(**item)


def list_activity(work_item_id: Optional[str] = None, limit: int = 100) -> list[WorkItemActivityOut]:
    store = get_collaboration_store()
    return [WorkItemActivityOut(**a) for a in store.fetch_activity(work_item_id=work_item_id, limit=limit)]


def merge_summary(changed_files: list[str], work_item_id: Optional[str] = None) -> MergeSummaryOut:
    """"Before merging, generate a summary showing overlap with active
    work, potential conflicts, related tasks" -- reuses the exact same
    overlap detection a new work item gets checked against, applied to a
    real diff's changed files instead of a task's own estimate. Never
    blocks a merge; a caller (CI, a human, an AI agent) decides what to
    do with `highest_risk`."""
    store = get_collaboration_store()
    related_item = None
    exclude_id = work_item_id
    if work_item_id is not None:
        related = store.fetch_work_item(work_item_id)
        if related is not None:
            related_item = WorkItemOut(**related)
        else:
            exclude_id = None  # nothing to exclude if the id doesn't exist

    active = store.fetch_active_work_items(exclude_id=exclude_id)
    warnings = [OverlapWarningOut(**w.__dict__) for w in detect_overlap(changed_files, active)]
    highest_risk = max((w.risk for w in warnings), key=lambda r: _RISK_SEVERITY[r], default="no_risk")

    return MergeSummaryOut(related_work_item=related_item, overlap_warnings=warnings, highest_risk=highest_risk)
