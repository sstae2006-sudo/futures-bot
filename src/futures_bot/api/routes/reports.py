from __future__ import annotations

from typing import Optional

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from .. import services
from ..schemas import ReportGenerateRequest, ReportOut

router = APIRouter(tags=["reports"])


@router.post("/api/report/generate", response_model=ReportOut)
def generate_report(req: ReportGenerateRequest) -> ReportOut:
    return services.generate_report(req.run_id)


@router.get("/api/reports", response_model=list[ReportOut])
def list_reports(run_id: Optional[str] = None) -> list[ReportOut]:
    return services.list_reports(run_id=run_id)


@router.get("/api/reports/{report_id}/view", response_class=HTMLResponse)
def view_report(report_id: str) -> HTMLResponse:
    return HTMLResponse(content=services.read_report_html(report_id))
