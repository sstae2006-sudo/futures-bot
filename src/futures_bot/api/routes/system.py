from __future__ import annotations

from fastapi import APIRouter

from .. import services
from ..schemas import InsightOut, LogEntry, SystemOverview

router = APIRouter(tags=["system"])


@router.get("/api/system/overview", response_model=SystemOverview)
def get_overview() -> SystemOverview:
    return services.system_overview()


@router.get("/api/insights", response_model=list[InsightOut])
def get_insights() -> list[InsightOut]:
    return services.generate_insights()


@router.get("/api/logs", response_model=list[LogEntry])
def get_logs(limit: int = 200, kind: str | None = None) -> list[LogEntry]:
    return [LogEntry(**e) for e in services.read_logs(limit=limit, kind=kind)]
