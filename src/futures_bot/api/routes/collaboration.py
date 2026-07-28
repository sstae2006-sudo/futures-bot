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
    BranchInfoOut, ClaimWorkItemRequest, MergeSummaryOut, MergeSummaryRequest, OverlapWarningOut,
    ReassignWorkItemRequest, UpdateWorkItemStatusRequest, WorkItemActivityOut, WorkItemCreatedOut,
    WorkItemCreateRequest, WorkItemOut,
)

router = APIRouter(tags=["collaboration"])


@router.post("/api/work-items", response_model=WorkItemCreatedOut)
def create_work_item(req: WorkItemCreateRequest) -> WorkItemCreatedOut:
    item, warnings = services.create_work_item(
        title=req.title, description=req.description, owner_user_id=req.owner_user_id,
        branch=req.branch, estimated_files=req.estimated_files, priority=req.priority,
        owner_type=req.owner_type,
    )
    return WorkItemCreatedOut(work_item=item, overlap_warnings=warnings)


@router.get("/api/work-items", response_model=list[WorkItemOut])
def list_work_items(status: Optional[str] = None) -> list[WorkItemOut]:
    return services.list_work_items(status=status)


@router.get("/api/work-items/{item_id}", response_model=WorkItemOut)
def get_work_item(item_id: str) -> WorkItemOut:
    return services.get_work_item(item_id)


@router.get("/api/work-items/{item_id}/overlap", response_model=list[OverlapWarningOut])
def check_overlap(item_id: str) -> list[OverlapWarningOut]:
    return services.check_overlap(item_id)


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


@router.get("/api/work-items-activity", response_model=list[WorkItemActivityOut])
def list_activity(work_item_id: Optional[str] = None, limit: int = 100) -> list[WorkItemActivityOut]:
    return services.list_activity(work_item_id=work_item_id, limit=limit)


@router.post("/api/work-items/merge-summary", response_model=MergeSummaryOut)
def merge_summary(req: MergeSummaryRequest) -> MergeSummaryOut:
    return services.merge_summary(req.changed_files, req.work_item_id)


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
