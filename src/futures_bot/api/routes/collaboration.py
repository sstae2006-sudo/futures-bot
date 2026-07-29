"""Active Work Registry (Team Collaboration MVP): work-item CRUD, claim/
release/complete/reassign, file-overlap warnings, and the activity log.
See `collaboration/store.py`'s and `collaboration/overlap.py`'s module
docstrings for the full design rationale -- warn-only, never blocking.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter

from .. import collaboration_service as services
from ..schemas import (
    AutomationStatusOut, BranchInfoOut, ClaimWorkItemRequest, ConflictPairOut, ContextBundleOut, ContextBundleRequest,
    GitSyncStatusOut, GitWatcherStatusOut, IntegrationQueueEntryOut, MergeReadinessOut, MergeReadinessRequest,
    MergeSummaryOut, MergeSummaryRequest, OverlapWarningOut, OverlapWarningV2Out, PreWorkCheckOut,
    PreWorkCheckRequest, ReassignWorkItemRequest, TimelineEntryOut, UpdateWorkItemStatusRequest, WorkItemActivityOut,
    WorkItemCreatedOut, WorkItemCreateRequest, WorkItemOut, WorkItemReviewOut,
)

router = APIRouter(tags=["collaboration"])


@router.post("/api/work-items", response_model=WorkItemCreatedOut)
def create_work_item(req: WorkItemCreateRequest) -> WorkItemCreatedOut:
    item, warnings = services.create_work_item(
        title=req.title, description=req.description, owner_user_id=req.owner_user_id,
        branch=req.branch, estimated_files=req.estimated_files, priority=req.priority,
        owner_type=req.owner_type, org_id=req.org_id,
    )
    return WorkItemCreatedOut(work_item=item, overlap_warnings=warnings)


@router.get("/api/work-items", response_model=list[WorkItemOut])
def list_work_items(status: Optional[str] = None, org_id: Optional[str] = None) -> list[WorkItemOut]:
    return services.list_work_items(status=status, org_id=org_id)


@router.post("/api/work-items/pre-work-check", response_model=PreWorkCheckOut)
def pre_work_check(req: PreWorkCheckRequest) -> PreWorkCheckOut:
    """AI-awareness check, callable *before* a work item is created --
    "before any AI task begins, inspect active work... if overlap exists,
    explain it, recommend coordination, suggest an alternate task."""
    return services.pre_work_check(req.proposed_files, title=req.title, description=req.description, org_id=req.org_id)


@router.get("/api/work-items/conflicts", response_model=list[ConflictPairOut])
def list_conflicts(org_id: Optional[str] = None) -> list[ConflictPairOut]:
    """Every pairwise Overlap V2 conflict across the whole currently-active
    set -- registered ahead of `GET /api/work-items/{item_id}` so
    "conflicts" is never swallowed as a (nonexistent) work item id."""
    return services.list_conflicts(org_id=org_id)


@router.get("/api/work-items/drafts", response_model=list[WorkItemOut])
def list_draft_work_items(org_id: Optional[str] = None) -> list[WorkItemOut]:
    """SIL Phase 4's git-watcher output -- registered ahead of `GET
    /api/work-items/{item_id}` for the same reason `conflicts` is
    (above)."""
    return services.list_draft_work_items(org_id=org_id)


@router.get("/api/work-items/{item_id}", response_model=WorkItemOut)
def get_work_item(item_id: str) -> WorkItemOut:
    return services.get_work_item(item_id)


@router.get("/api/work-items/{item_id}/overlap", response_model=list[OverlapWarningOut])
def check_overlap(item_id: str) -> list[OverlapWarningOut]:
    return services.check_overlap(item_id)


@router.get("/api/work-items/{item_id}/overlap-v2", response_model=list[OverlapWarningV2Out])
def check_overlap_v2(item_id: str) -> list[OverlapWarningV2Out]:
    return services.check_overlap_v2(item_id)


@router.get("/api/work-items/{item_id}/review", response_model=WorkItemReviewOut)
def get_work_item_review(item_id: str) -> WorkItemReviewOut:
    """SIL Phase 6's Continuous Review Pipeline for a single item --
    wires together existing primitives (merge readiness, which already
    includes overlap warnings and branch info), never new intelligence.
    See `docs/ARCHITECTURE.md`'s "SIL Phase 6" section."""
    return services.get_work_item_review(item_id)


@router.post("/api/work-items/{item_id}/claim", response_model=WorkItemOut)
def claim_work_item(item_id: str, req: ClaimWorkItemRequest) -> WorkItemOut:
    return services.claim_work_item(item_id, req.user_id)


@router.post("/api/work-items/{item_id}/release", response_model=WorkItemOut)
def release_work_item(item_id: str) -> WorkItemOut:
    return services.release_work_item(item_id)


@router.post("/api/work-items/{item_id}/complete", response_model=WorkItemOut)
def complete_work_item(item_id: str) -> WorkItemOut:
    return services.complete_work_item(item_id)


@router.post("/api/work-items/{item_id}/reassign", response_model=WorkItemOut)
def reassign_work_item(item_id: str, req: ReassignWorkItemRequest) -> WorkItemOut:
    return services.reassign_work_item(item_id, req.user_id)


@router.post("/api/work-items/{item_id}/status", response_model=WorkItemOut)
def update_work_item_status(item_id: str, req: UpdateWorkItemStatusRequest) -> WorkItemOut:
    """Moves a work item through the SIL Phase 2 lifecycle (planned ->
    in_progress -> testing -> ready_for_review -> merged). Use
    claim/release/complete for open/claimed/completed -- unchanged from
    Phase 1."""
    return services.update_work_item_status(item_id, req.status)


@router.post("/api/work-items/{item_id}/approve-draft", response_model=WorkItemOut)
def approve_draft_work_item(item_id: str) -> WorkItemOut:
    """Promotes a git-watcher draft to a real work item. 400 if the item
    doesn't exist or isn't currently a draft."""
    return services.approve_draft_work_item(item_id)


@router.delete("/api/work-items/{item_id}/draft")
def discard_draft_work_item(item_id: str) -> dict[str, bool]:
    """Hard-deletes a draft. Refuses (400) on a real work item -- see
    `collaboration/store.py::discard_draft_work_item`'s docstring."""
    services.discard_draft_work_item(item_id)
    return {"discarded": True}


@router.get("/api/work-items-activity", response_model=list[WorkItemActivityOut])
def list_activity(work_item_id: Optional[str] = None, limit: int = 100) -> list[WorkItemActivityOut]:
    return services.list_activity(work_item_id=work_item_id, limit=limit)


@router.post("/api/work-items/merge-summary", response_model=MergeSummaryOut)
def merge_summary(req: MergeSummaryRequest) -> MergeSummaryOut:
    return services.merge_summary(req.changed_files, req.work_item_id, org_id=req.org_id)


@router.post("/api/work-items/merge-readiness", response_model=MergeReadinessOut)
def merge_readiness(req: MergeReadinessRequest) -> MergeReadinessOut:
    """A single explainable 0-100 score -- overlap risk, branch age,
    behind-base count, and change size. `test_status` is always
    `"unknown"` (no CI integration exists yet -- see
    `collaboration/merge_readiness.py`'s module docstring)."""
    return services.merge_readiness(req.changed_files, branch=req.branch, work_item_id=req.work_item_id, org_id=req.org_id)


@router.get("/api/integration/queue", response_model=list[IntegrationQueueEntryOut])
def get_integration_queue(org_id: Optional[str] = None) -> list[IntegrationQueueEntryOut]:
    """SIL Phase 6's Integration Queue -- work items in `testing`/
    `ready_for_review`, ordered by merge-readiness confidence score
    (ties broken by priority, then oldest-waiting-first). A *view* over
    `work_items`, not a new persisted queue -- recomputed fresh every
    call. See `docs/ARCHITECTURE.md`'s "SIL Phase 6" section."""
    return services.get_integration_queue(org_id=org_id)


@router.get("/api/activity/timeline", response_model=list[TimelineEntryOut])
def get_timeline(
    work_item_id: Optional[str] = None, event_type: Optional[str] = None, q: Optional[str] = None,
    since: Optional[str] = None, until: Optional[str] = None, include_commits: bool = True, limit: int = 100,
) -> list[TimelineEntryOut]:
    """The merged project activity timeline -- work-item events plus real
    git commits (`collaboration/timeline.py`). `include_commits=false`
    when embedding this in a per-work-item view where commits (which
    aren't tied to a specific item) would just be noise."""
    return services.get_timeline(
        work_item_id=work_item_id, event_type=event_type, q=q, since=since, until=until,
        include_commits=include_commits, limit=limit,
    )


@router.get("/api/git/branch-info", response_model=BranchInfoOut)
def get_branch_info(branch: Optional[str] = None) -> BranchInfoOut:
    """Live git introspection (`collaboration/git_info.py`) -- current
    branch by default, or a named one. Always 200: an unrecognized repo
    state (detached HEAD, no `main`, not a git checkout at all) comes
    back as `notes`, not an error."""
    return services.get_branch_info(branch=branch)


@router.get("/api/work-items/{item_id}/branch-info", response_model=BranchInfoOut)
def get_work_item_branch_info(item_id: str) -> BranchInfoOut:
    return services.get_work_item_branch_info(item_id)


@router.post("/api/collaboration/context-bundle", response_model=ContextBundleOut)
def get_context_bundle(req: ContextBundleRequest) -> ContextBundleOut:
    """SIL Phase 4's "Automatic AI Context" call -- one request that
    gathers active work items, similar past work, recent commits, branch
    info, overlap warnings, and relevant KNOWN_ISSUES/ROADMAP excerpts
    (see `collaboration/context_bundle.py`'s module docstring). Reuses
    existing systems only -- no new persisted index."""
    return services.get_context_bundle(
        req.proposed_files, title=req.title, description=req.description,
        org_id=req.org_id, commit_limit=req.commit_limit,
    )


@router.get("/api/collaboration/git-watcher/status", response_model=GitWatcherStatusOut)
def get_git_watcher_status() -> GitWatcherStatusOut:
    """SIL Phase 4's background git-watcher status -- see
    `collaboration/git_watcher.py`'s module docstring. Returns
    `running=False` and zeroed counters if `automation.enabled` is off
    (the watcher singleton simply never started)."""
    return services.get_git_watcher_status()


@router.get("/api/collaboration/git-sync/status", response_model=GitSyncStatusOut)
def get_git_sync_status() -> GitSyncStatusOut:
    """Pull-only auto-sync status -- see `collaboration/git_sync.py`'s
    module docstring. Returns `running=False` if
    `automation.git_sync_enabled` is off (the default)."""
    return services.get_git_sync_status()


@router.get("/api/automation/status", response_model=AutomationStatusOut)
def get_automation_status() -> AutomationStatusOut:
    """Mission Control's Automation panel -- git-watcher + maintenance
    scheduler status in one call. Both report `running=False` if
    `automation.enabled` is off in config.yaml (the default)."""
    return services.get_automation_status()
