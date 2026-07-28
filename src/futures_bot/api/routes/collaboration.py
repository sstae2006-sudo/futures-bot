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
    ClaimWorkItemRequest, MergeSummaryOut, MergeSummaryRequest, OverlapWarningOut, ReassignWorkItemRequest,
    WorkItemActivityOut, WorkItemCreatedOut, WorkItemCreateRequest, WorkItemOut,
)

router = APIRouter(tags=["collaboration"])


@router.post("/api/work-items", response_model=WorkItemCreatedOut)
def create_work_item(req: WorkItemCreateRequest) -> WorkItemCreatedOut:
    item, warnings = services.create_work_item(
        title=req.title, description=req.description, owner_user_id=req.owner_user_id,
        branch=req.branch, estimated_files=req.estimated_files, priority=req.priority,
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


@router.get("/api/work-items-activity", response_model=list[WorkItemActivityOut])
def list_activity(work_item_id: Optional[str] = None, limit: int = 100) -> list[WorkItemActivityOut]:
    return services.list_activity(work_item_id=work_item_id, limit=limit)


@router.post("/api/work-items/merge-summary", response_model=MergeSummaryOut)
def merge_summary(req: MergeSummaryRequest) -> MergeSummaryOut:
    return services.merge_summary(req.changed_files, req.work_item_id)
