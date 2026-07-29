"""Worker Registry (SIL Phase 6 "Integration Coordinator" Milestone 1) --
every human developer, Claude Code session, other AI agent, or background
job self-reports its liveness here via heartbeat. See
`collaboration/store.py::heartbeat_worker`'s module docstring for the full
design rationale.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter

from .. import worker_service as services
from ..schemas import WorkerHeartbeatRequest, WorkerOut

router = APIRouter(tags=["workers"])


@router.post("/api/workers/{worker_id}/heartbeat", response_model=WorkerOut)
def heartbeat_worker(worker_id: str, req: WorkerHeartbeatRequest) -> WorkerOut:
    """Upsert -- creates the worker on its first heartbeat, otherwise
    updates in place. See `collaboration/store.py::heartbeat_worker`'s
    docstring for why this deliberately does not require a separate
    registration call first."""
    return services.heartbeat_worker(
        worker_id, worker_type=req.worker_type, display_name=req.display_name, user_id=req.user_id,
        org_id=req.org_id, status=req.status, current_work_item_id=req.current_work_item_id,
        subsystem=req.subsystem, capabilities=req.capabilities,
    )


@router.get("/api/workers", response_model=list[WorkerOut])
def list_workers(org_id: Optional[str] = None, status: Optional[str] = None, capability: Optional[str] = None) -> list[WorkerOut]:
    return services.list_workers(org_id=org_id, status=status, capability=capability)


@router.get("/api/workers/{worker_id}", response_model=WorkerOut)
def get_worker(worker_id: str) -> WorkerOut:
    return services.get_worker(worker_id)
