"""API-facing logic for the Active Work Registry (Team Collaboration MVP)
-- own module, matching `accounts_service.py`'s precedent for keeping this
feature area modular rather than growing the already-large `services.py`.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Optional

from ..collaboration import PRIORITIES, git_info
from ..collaboration.architecture_map import affected_subsystems
from ..collaboration.conflict_resolution import ConflictResolution, build_conflict_resolutions
from ..collaboration.context_bundle import build_context_bundle
from ..collaboration.git_info import BranchInfo, Commit
from ..collaboration.git_sync import get_git_sync_scheduler
from ..collaboration.git_watcher import get_git_watcher
from ..collaboration.maintenance import get_maintenance_scheduler
from ..collaboration.overlap import detect_overlap
from ..collaboration.merge_readiness import ReadinessFactor, compute_merge_readiness
from ..collaboration.overlap_v2 import compute_all_conflicts, compute_overlap_v2
from ..collaboration.store import CollaborationError, get_collaboration_store
from ..collaboration.timeline import build_timeline
from ..collaboration.validation_planning import ValidationPlan, plan_validation
from .schemas import (
    AutomationStatusOut, BranchInfoOut, CommitOut, ConflictPairOut, ConflictResolutionOut, ContextBundleOut,
    DocExcerptOut, GitSyncStatusOut, GitWatcherStatusOut, IntegrationQueueEntryOut, IntegrationReviewOut,
    MaintenanceStatusOut, MergeReadinessOut, MergeSummaryOut, OverlapWarningOut, OverlapWarningV2Out,
    PreWorkCheckOut, ReadinessFactorOut, TimelineEntryOut, ValidationRecommendationOut, WorkItemActivityOut,
    WorkItemOut, WorkItemReviewOut,
)
from .services import ApiError

#: Templated recommendation text keyed by `MergeReadiness.level` -- SIL
#: Phase 6 Milestone 2's Integration Review recommendation field. Plain
#: advice, not an action this package ever takes on its own (see
#: ROADMAP.md's "Future" section for why autonomous merging stays out of
#: scope).
_RECOMMENDATION_BY_LEVEL = {
    "ready": "Recommend proceeding to integration -- no significant blockers detected.",
    "needs_review": "Recommend a human review before integrating -- some risk factors are present.",
    "risky": "Recommend addressing the flagged risk factors before integrating.",
    "not_ready": "Not recommended for integration yet -- significant risk factors are present.",
}

#: Work item lifecycle stages the Integration Queue surfaces -- see
#: `docs/ARCHITECTURE.md`'s "SIL Phase 6" section for why this is a
#: *view* over `work_items`, not a new persisted queue table.
_INTEGRATION_QUEUE_STATUSES = ("testing", "ready_for_review")

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
    owner_type: str = "human", org_id: Optional[str] = None,
) -> tuple[WorkItemOut, list[OverlapWarningOut]]:
    """Creates the work item, then checks it for file overlap against
    every other currently-active item -- "before any task begins,
    analyze... assign a risk level... do not block work, warn only" is
    exactly this call's contract: the item is created either way, the
    warnings are informational. Overlap is checked within `org_id`'s own
    scope when supplied (see `fetch_active_work_items`'s docstring) --
    two unrelated organizations sharing one instance shouldn't warn each
    other about "conflicts" that are really just coincidence."""
    store = get_collaboration_store()
    try:
        item_id = uuid.uuid4().hex[:12]
        item = store.create_work_item(
            item_id=item_id, title=title, description=description, owner_user_id=owner_user_id,
            branch=branch, estimated_files=estimated_files, priority=priority, owner_type=owner_type,
            org_id=org_id,
        )
    except CollaborationError as exc:
        raise ApiError(str(exc)) from exc

    active = store.fetch_active_work_items(exclude_id=item_id, org_id=org_id)
    warnings = detect_overlap(estimated_files or [], active)
    return WorkItemOut(**item), [OverlapWarningOut(**w.__dict__) for w in warnings]


def list_work_items(status: Optional[str] = None, org_id: Optional[str] = None) -> list[WorkItemOut]:
    store = get_collaboration_store()
    return [WorkItemOut(**i) for i in store.fetch_work_items(status=status, org_id=org_id)]


def get_work_item(item_id: str) -> WorkItemOut:
    store = get_collaboration_store()
    item = store.fetch_work_item(item_id)
    if item is None:
        raise ApiError(f"No such work item: {item_id!r}")
    return WorkItemOut(**item)


def check_overlap(item_id: str) -> list[OverlapWarningOut]:
    """Recomputes overlap for an existing item against every other
    currently-active item -- lets a client re-check after other work
    items have come and gone, not just at creation time. Scoped to the
    item's own `org_id` (`None` for a legacy/unscoped item, which then
    compares against every other unscoped item plus every org's -- see
    `fetch_active_work_items`'s docstring for why `org_id=None` means
    "no filter," not "only unscoped")."""
    store = get_collaboration_store()
    item = store.fetch_work_item(item_id)
    if item is None:
        raise ApiError(f"No such work item: {item_id!r}")
    active = store.fetch_active_work_items(exclude_id=item_id, org_id=item.get("org_id"))
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


def merge_summary(changed_files: list[str], work_item_id: Optional[str] = None, org_id: Optional[str] = None) -> MergeSummaryOut:
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
            org_id = org_id or related.get("org_id")
        else:
            exclude_id = None  # nothing to exclude if the id doesn't exist

    active = store.fetch_active_work_items(exclude_id=exclude_id, org_id=org_id)
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
    active = store.fetch_active_work_items(exclude_id=item_id, org_id=item.get("org_id"))
    warnings = compute_overlap_v2(
        item["estimated_files"], active, proposed_title=item.get("title"), proposed_description=item.get("description"),
    )
    return [OverlapWarningV2Out(**w.__dict__) for w in warnings]


def list_conflicts(org_id: Optional[str] = None) -> list[ConflictPairOut]:
    """Every pairwise Overlap V2 conflict across the whole currently-active
    set, in one call -- backs Mission Control's "Conflict Warnings" view.
    Scoped to `org_id` when supplied, same "None means no filter"
    contract every other listing here follows."""
    store = get_collaboration_store()
    active = store.fetch_active_work_items(org_id=org_id)
    pairs = compute_all_conflicts(active)
    return [
        ConflictPairOut(
            item_a=p.item_a["id"], item_a_title=p.item_a["title"], item_b=p.item_b["id"], item_b_title=p.item_b["title"],
            risk=p.warning.risk, confidence=p.warning.confidence, factors=p.warning.factors, reason=p.warning.reason,
        )
        for p in pairs
    ]


def pre_work_check(
    proposed_files: list[str], title: Optional[str] = None, description: Optional[str] = None,
    org_id: Optional[str] = None,
) -> PreWorkCheckOut:
    """"Before any AI task begins... inspect active work... if overlap
    exists, explain it, recommend coordination, suggest an alternate
    task" -- callable before a work item even exists, so an AI session
    (or a human) can check *before* committing to a task, not just after
    creating one. Never blocks; `suggested_action` is advice, the caller
    decides."""
    store = get_collaboration_store()
    active = store.fetch_active_work_items(org_id=org_id)
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


def merge_readiness(
    changed_files: list[str], branch: Optional[str] = None, work_item_id: Optional[str] = None,
    org_id: Optional[str] = None, *,
    active_items: Optional[list[dict]] = None, file_cache: Optional[dict] = None,
) -> MergeReadinessOut:
    """Everything `merge_summary` already computes, folded into one
    explainable 0-100 score alongside branch age/behind-count/change-size
    -- see `merge_readiness.py`'s module docstring for why test pass/fail
    is deliberately reported as `"unknown"` rather than guessed at.

    `active_items`/`file_cache` are an opt-in performance path (KNOWN_ISSUES.md
    ISSUE-039): `get_integration_queue` below calls this once per queued
    item, and every call independently re-fetching the full active-item
    set from the database, then re-reading and re-parsing every one of
    those items' files from scratch, made building the whole queue
    O(N^2) in the number of queued items. A caller that already has the
    active set (and a shared per-file cache) can pass both here to skip
    the redundant fetch and reuse already-parsed files -- the resulting
    score is identical either way, only the cost changes. `None` (every
    caller before this fix) is unaffected."""
    store = get_collaboration_store()
    exclude_id = work_item_id
    title = description = None
    if work_item_id is not None:
        related = store.fetch_work_item(work_item_id)
        if related is not None:
            title, description = related.get("title"), related.get("description")
            org_id = org_id or related.get("org_id")
        else:
            exclude_id = None

    active = active_items if active_items is not None else store.fetch_active_work_items(exclude_id=exclude_id, org_id=org_id)
    warnings = compute_overlap_v2(
        changed_files, active, proposed_title=title, proposed_description=description, file_cache=file_cache,
    )
    info = git_info.get_branch_info(branch=branch)
    readiness = compute_merge_readiness(changed_files, warnings, info)

    return MergeReadinessOut(
        score=readiness.score, level=readiness.level, test_status=readiness.test_status,
        factors=[_readiness_factor_out(f) for f in readiness.factors],
        branch_info=_branch_info_out(info),
        overlap_warnings=[OverlapWarningV2Out(**w.__dict__) for w in warnings],
    )


def _readiness_note(item: dict) -> Optional[str]:
    """`merge_readiness.py` was built to score a real `git diff`'s
    `changed_files`, not a work item's self-reported `estimated_files`.
    Every queued item that isn't the currently-checked-out branch (i.e.
    essentially all of them, since `git_info` only introspects the
    working tree, not arbitrary other branches) has its score computed
    from that weaker proxy -- flagged here rather than silently
    presented at the same confidence as a real-diff score, same honesty
    `test_status="unknown"` already models elsewhere in this package."""
    branch = item.get("branch")
    current = git_info.current_branch()
    if branch is None or branch != current:
        return (
            "Score computed from this item's self-reported estimated_files, not a real git diff "
            "(its branch isn't the one currently checked out here)."
        )
    return None


def get_integration_queue(org_id: Optional[str] = None) -> list[IntegrationQueueEntryOut]:
    """The Integration Queue -- `work_items` in `testing`/`ready_for_review`,
    scored via the existing `merge_readiness` call above and ordered by
    that score. No new table: the ordering *is* the queue position,
    recomputed fresh every request, matching this package's "compute
    live" philosophy throughout. Ties (equal score) break on `priority`
    descending, then `updated_at` ascending (oldest-waiting-first) --
    stated explicitly rather than left to incidental sort stability.

    KNOWN_ISSUES.md ISSUE-039 (measured, fixed 2026-07-29): computing
    each item's readiness via a *fully independent* `merge_readiness`
    call -- its own DB fetch of every other active item, its own
    from-scratch read+parse of every one of those items' files -- made
    this endpoint O(N^2) in the number of queued items (23s for 50
    items, measured). The active-item set is now fetched exactly ONCE
    here and reused for every queued item's comparison (mathematically
    identical to each item's own independent fetch minus itself, just
    not repeated N times), and `file_cache` is shared across every one
    of those comparisons so each distinct file is read/parsed at most
    once for the whole request, not once per item that references it.
    Every item's resulting score is unchanged -- same inputs, same
    computation, only the redundant work is gone."""
    store = get_collaboration_store()
    items = [i for i in store.fetch_work_items(org_id=org_id) if i["status"] in _INTEGRATION_QUEUE_STATUSES]
    all_active = store.fetch_active_work_items(org_id=org_id)
    file_cache: dict = {}

    entries: list[tuple[int, int, str, IntegrationQueueEntryOut]] = []
    for item in items:
        other_active = [i for i in all_active if i["id"] != item["id"]]
        readiness = merge_readiness(
            item["estimated_files"], branch=item.get("branch"), work_item_id=item["id"], org_id=org_id,
            active_items=other_active, file_cache=file_cache,
        )
        entry = IntegrationQueueEntryOut(
            work_item=WorkItemOut(**item), merge_readiness=readiness, readiness_note=_readiness_note(item),
        )
        priority_rank = PRIORITIES.index(item["priority"]) if item["priority"] in PRIORITIES else 0
        entries.append((-readiness.score, -priority_rank, item["updated_at"], entry))

    entries.sort(key=lambda t: (t[0], t[1], t[2]))
    return [e[3] for e in entries]


def get_work_item_review(item_id: str) -> WorkItemReviewOut:
    """The Continuous Review Pipeline's per-item output -- wires together
    `merge_readiness` (which already includes overlap warnings and
    branch info) rather than computing new intelligence. See
    `docs/ARCHITECTURE.md`'s "SIL Phase 6" section."""
    store = get_collaboration_store()
    item = store.fetch_work_item(item_id)
    if item is None:
        raise ApiError(f"No such work item: {item_id!r}")
    readiness = merge_readiness(
        item["estimated_files"], branch=item.get("branch"), work_item_id=item_id, org_id=item.get("org_id"),
    )
    return WorkItemReviewOut(
        work_item=WorkItemOut(**item), merge_readiness=readiness, readiness_note=_readiness_note(item),
    )


def list_draft_work_items(org_id: Optional[str] = None) -> list[WorkItemOut]:
    """Every `is_draft=True` work item -- SIL Phase 4's git-watcher output
    awaiting human/AI review. Never includes a real work item."""
    store = get_collaboration_store()
    return [WorkItemOut(**i) for i in store.fetch_draft_work_items(org_id=org_id)]


def approve_draft_work_item(item_id: str, actor_user_id: Optional[str] = None) -> WorkItemOut:
    store = get_collaboration_store()
    try:
        item = store.approve_draft_work_item(item_id, actor_user_id=actor_user_id)
    except CollaborationError as exc:
        raise ApiError(str(exc)) from exc
    return WorkItemOut(**item)


def discard_draft_work_item(item_id: str, actor_user_id: Optional[str] = None) -> None:
    store = get_collaboration_store()
    try:
        store.discard_draft_work_item(item_id, actor_user_id=actor_user_id)
    except CollaborationError as exc:
        raise ApiError(str(exc)) from exc


def get_git_watcher_status() -> GitWatcherStatusOut:
    return GitWatcherStatusOut(**get_git_watcher().status())


def get_automation_status() -> AutomationStatusOut:
    return AutomationStatusOut(
        git_watcher=GitWatcherStatusOut(**get_git_watcher().status()),
        maintenance=MaintenanceStatusOut(**get_maintenance_scheduler().status()),
        git_sync=GitSyncStatusOut(**get_git_sync_scheduler().status()),
    )


def get_git_sync_status() -> GitSyncStatusOut:
    return GitSyncStatusOut(**get_git_sync_scheduler().status())


def _conflict_resolution_out(resolution: ConflictResolution) -> ConflictResolutionOut:
    w = resolution.warning
    return ConflictResolutionOut(
        work_item_id=w.work_item_id, title=w.title, risk=w.risk, confidence=w.confidence, reason=w.reason,
        architecture_components_affected=resolution.architecture_components_affected,
        suggested_resolution=resolution.suggested_resolution,
    )


def _validation_recommendation_out(plan: ValidationPlan) -> ValidationRecommendationOut:
    return ValidationRecommendationOut(
        recommended_tests=plan.matched_tests, unmapped_files=plan.unmapped_files,
        recommend_full_suite=plan.recommend_full_suite, frontend_validation_recommended=plan.frontend_changed,
    )


def _integration_review_summary(
    item: dict, readiness: MergeReadinessOut, subsystems: list[str], validation: ValidationRecommendationOut,
) -> str:
    parts = [
        f"'{item['title']}' is currently {item['status']} with a merge-readiness score of "
        f"{readiness.score}/100 ({readiness.level})."
    ]
    if readiness.overlap_warnings:
        top = readiness.overlap_warnings[0]
        parts.append(f"Highest overlap risk is {top.risk} against '{top.title}' (confidence {top.confidence}/100).")
    else:
        parts.append("No overlap detected against other active work.")
    if subsystems:
        parts.append(f"Affects: {', '.join(subsystems)}.")
    if validation.recommend_full_suite:
        parts.append("Recommends the full backend suite (some changed files have no direct test mapping).")
    elif validation.recommended_tests:
        parts.append(f"Recommends running {len(validation.recommended_tests)} mapped test file(s).")
    else:
        parts.append("No backend test files mapped to these changes.")
    return " ".join(parts)


def _integration_review_out(row: dict) -> IntegrationReviewOut:
    return IntegrationReviewOut(
        id=row["id"], work_item_id=row["work_item_id"], worker_id=row.get("worker_id"), branch=row.get("branch"),
        status_at_review=row["status_at_review"], confidence_score=row["confidence_score"],
        risk_level=row["risk_level"], level=row["level"], related_work_item_ids=row["related_work_item_ids"],
        affected_subsystems=row["affected_subsystems"],
        conflict_resolutions=[ConflictResolutionOut(**c) for c in row["conflict_resolutions"]],
        validation_recommendation=ValidationRecommendationOut(**row["validation_recommendation"]),
        readiness_note=row.get("readiness_note"), summary=row["summary"], recommendation=row["recommendation"],
        created_at=row["created_at"],
    )


def generate_integration_review(item_id: str, worker_id: Optional[str] = None) -> IntegrationReviewOut:
    """SIL Phase 6 Milestone 2's Intelligent Review Pipeline -- generates
    and PERMANENTLY stores a new Integration Review for `item_id`. Unlike
    `get_work_item_review` above (Milestone 1's live, ephemeral
    single-item check), every call here inserts a new, immutable row
    (see `collaboration/store.py::create_integration_review`'s
    docstring for why persistence is a deliberate exception to this
    package's usual "compute live, never cache" rule). Wires together
    existing primitives only: `merge_readiness` (overlap + branch info),
    the Conflict Resolution Assistant, the minimal subsystem lookup, and
    the shared validation-planning heuristic -- no new scoring model.

    Fetches the active-item set once and reuses `readiness.overlap_warnings`
    for the Conflict Resolution Assistant instead of calling
    `compute_overlap_v2` a second time on the same inputs (KNOWN_ISSUES.md
    ISSUE-039 -- a smaller, 2x-not-N^2 instance of the same redundant-
    recomputation pattern fixed in `get_integration_queue`).
    `readiness.overlap_warnings` are `OverlapWarningV2Out` (pydantic), not
    `overlap_v2.OverlapWarningV2` (the dataclass) -- `build_conflict_resolutions`
    accepts either via `OverlapWarningLike`'s structural typing."""
    store = get_collaboration_store()
    item = store.fetch_work_item(item_id)
    if item is None:
        raise ApiError(f"No such work item: {item_id!r}")

    other_items_by_id = {
        i["id"]: i for i in store.fetch_active_work_items(exclude_id=item_id, org_id=item.get("org_id"))
    }
    readiness = merge_readiness(
        item["estimated_files"], branch=item.get("branch"), work_item_id=item_id, org_id=item.get("org_id"),
        active_items=list(other_items_by_id.values()),
    )
    warnings_v2 = readiness.overlap_warnings
    resolutions = build_conflict_resolutions(item, warnings_v2, other_items_by_id)
    subsystems = affected_subsystems(item["estimated_files"])
    plan = plan_validation(item["estimated_files"], git_info.repo_root() or Path("."))
    validation_out = _validation_recommendation_out(plan)
    note = _readiness_note(item)
    risk_level = warnings_v2[0].risk if warnings_v2 else "no_risk"
    summary = _integration_review_summary(item, readiness, subsystems, validation_out)
    recommendation = _RECOMMENDATION_BY_LEVEL[readiness.level]

    row = store.create_integration_review(
        review_id=uuid.uuid4().hex[:12], work_item_id=item_id, worker_id=worker_id, branch=item.get("branch"),
        status_at_review=item["status"], confidence_score=readiness.score, risk_level=risk_level,
        level=readiness.level, related_work_item_ids=[w.work_item_id for w in warnings_v2],
        affected_subsystems=subsystems,
        conflict_resolutions=[_conflict_resolution_out(r).model_dump() for r in resolutions],
        validation_recommendation=validation_out.model_dump(),
        readiness_note=note, summary=summary, recommendation=recommendation,
    )
    return _integration_review_out(row)


def list_integration_reviews(item_id: str, limit: int = 50) -> list[IntegrationReviewOut]:
    """The full historical review trail for one work item, newest first
    -- Milestone 2's "complete historical review trail" success
    criterion."""
    store = get_collaboration_store()
    if store.fetch_work_item(item_id) is None:
        raise ApiError(f"No such work item: {item_id!r}")
    return [_integration_review_out(r) for r in store.fetch_integration_reviews(item_id, limit=limit)]


def get_context_bundle(
    proposed_files: list[str], title: Optional[str] = None, description: Optional[str] = None,
    org_id: Optional[str] = None, commit_limit: int = 10,
) -> ContextBundleOut:
    """SIL Phase 4's "Automatic AI Context" call -- see
    `collaboration/context_bundle.py`'s module docstring for what each
    field is (and, just as importantly, isn't -- no architecture graph,
    no semantic search)."""
    bundle = build_context_bundle(
        proposed_files=proposed_files, title=title, description=description,
        org_id=org_id, commit_limit=commit_limit,
    )
    return ContextBundleOut(
        active_work_items=[WorkItemOut(**i) for i in bundle.active_work_items],
        similar_past_work=[WorkItemOut(**i) for i in bundle.similar_past_work],
        recent_commits=[_commit_out(c) for c in bundle.recent_commits],
        branch_info=_branch_info_out(bundle.branch_info),
        overlap_warnings=[OverlapWarningV2Out(**w.__dict__) for w in bundle.overlap_warnings],
        relevant_docs=[DocExcerptOut(**d.__dict__) for d in bundle.relevant_docs],
    )
