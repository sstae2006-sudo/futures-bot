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
from ..collaboration.merge_readiness import ReadinessFactor, compute_merge_readiness
from ..collaboration.overlap_v2 import OverlapWarningV2, compute_all_conflicts, compute_overlap_v2
from ..collaboration.store import CollaborationError, get_collaboration_store
from ..collaboration.timeline import build_timeline
from .schemas import (
    BranchInfoOut, CommitOut, ConflictPairOut, MergeReadinessOut, MergeSummaryOut, OverlapWarningOut,
    OverlapWarningV2Out, PreWorkCheckOut, ReadinessFactorOut, TimelineEntryOut, WorkItemActivityOut, WorkItemOut,
)
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


def get_timeline(
    work_item_id: Optional[str] = None, event_type: Optional[str] = None, q: Optional[str] = None,
    since: Optional[str] = None, until: Optional[str] = None, include_commits: bool = True, limit: int = 100,
) -> list[TimelineEntryOut]:
    entries = build_timeline(
        work_item_id=work_item_id, event_type=event_type, q=q, since=since, until=until,
        include_commits=include_commits, limit=limit,
    )
    return [TimelineEntryOut(**e.__dict__) for e in entries]


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


def check_overlap_v2(item_id: str) -> list[OverlapWarningV2Out]:
    """Overlap Engine V2's richer, explainable check for one existing
    work item -- see `overlap_v2.py`'s module docstring for what it adds
    on top of `check_overlap`'s file-path-only V1 result."""
    store = get_collaboration_store()
    item = store.fetch_work_item(item_id)
    if item is None:
        raise ApiError(f"No such work item: {item_id!r}")
    active = store.fetch_active_work_items(exclude_id=item_id)
    warnings = compute_overlap_v2(
        item["estimated_files"], active, proposed_title=item.get("title"), proposed_description=item.get("description"),
    )
    return [OverlapWarningV2Out(**w.__dict__) for w in warnings]


def list_conflicts() -> list[ConflictPairOut]:
    """Every pairwise Overlap V2 conflict across the whole currently-active
    set, in one call -- backs Mission Control's "Conflict Warnings" view."""
    store = get_collaboration_store()
    active = store.fetch_active_work_items()
    pairs = compute_all_conflicts(active)
    return [
        ConflictPairOut(
            item_a=p.item_a["id"], item_a_title=p.item_a["title"], item_b=p.item_b["id"], item_b_title=p.item_b["title"],
            risk=p.warning.risk, confidence=p.warning.confidence, factors=p.warning.factors, reason=p.warning.reason,
        )
        for p in pairs
    ]


def pre_work_check(proposed_files: list[str], title: Optional[str] = None, description: Optional[str] = None) -> PreWorkCheckOut:
    """"Before any AI task begins... inspect active work... if overlap
    exists, explain it, recommend coordination, suggest an alternate
    task" -- callable before a work item even exists, so an AI session
    (or a human) can check *before* committing to a task, not just after
    creating one. Never blocks; `suggested_action` is advice, the caller
    decides."""
    store = get_collaboration_store()
    active = store.fetch_active_work_items()
    warnings = compute_overlap_v2(proposed_files, active, proposed_title=title, proposed_description=description)
    warning_outs = [OverlapWarningV2Out(**w.__dict__) for w in warnings]

    if not warnings:
        suggested_action: str = "proceed"
        recommendation = "No overlap detected with any currently active work item -- clear to proceed."
    else:
        top = warnings[0]
        owner = top.owner_user_id or "an unclaimed item"
        if top.risk == "critical":
            suggested_action = "choose_different_task"
            recommendation = (
                f"This heavily overlaps with '{top.title}' (owner: {owner}, confidence {top.confidence}/100). "
                f"{top.reason} Recommend picking a different task, or coordinating directly with {owner} first."
            )
        elif top.risk in ("high", "medium"):
            suggested_action = "coordinate"
            recommendation = (
                f"This overlaps with '{top.title}' (owner: {owner}, confidence {top.confidence}/100). "
                f"{top.reason} Recommend coordinating with {owner} before starting."
            )
        else:
            suggested_action = "proceed"
            recommendation = (
                f"Minor overlap with '{top.title}' (confidence {top.confidence}/100) -- "
                "safe to proceed, but worth a heads-up to its owner."
            )

    return PreWorkCheckOut(
        overlap_warnings=warning_outs, suggested_action=suggested_action,  # type: ignore[arg-type]
        recommendation=recommendation, branch_info=get_branch_info(),
    )


def _readiness_factor_out(factor: ReadinessFactor) -> ReadinessFactorOut:
    return ReadinessFactorOut(name=factor.name, penalty=factor.penalty, explanation=factor.explanation)


def merge_readiness(changed_files: list[str], branch: Optional[str] = None, work_item_id: Optional[str] = None) -> MergeReadinessOut:
    """Everything `merge_summary` already computes, folded into one
    explainable 0-100 score alongside branch age/behind-count/change-size
    -- see `merge_readiness.py`'s module docstring for why test pass/fail
    is deliberately reported as `"unknown"` rather than guessed at."""
    store = get_collaboration_store()
    exclude_id = work_item_id
    title = description = None
    if work_item_id is not None:
        related = store.fetch_work_item(work_item_id)
        if related is not None:
            title, description = related.get("title"), related.get("description")
        else:
            exclude_id = None

    active = store.fetch_active_work_items(exclude_id=exclude_id)
    warnings = compute_overlap_v2(changed_files, active, proposed_title=title, proposed_description=description)
    info = git_info.get_branch_info(branch=branch)
    readiness = compute_merge_readiness(changed_files, warnings, info)

    return MergeReadinessOut(
        score=readiness.score, level=readiness.level, test_status=readiness.test_status,
        factors=[_readiness_factor_out(f) for f in readiness.factors],
        branch_info=_branch_info_out(info),
        overlap_warnings=[OverlapWarningV2Out(**w.__dict__) for w in warnings],
    )
