"""Background job endpoints (Phase 6B). See `api/jobs.py`'s module
docstring for the threading model, and the package docstring for why the
Phase 6A synchronous endpoints (`POST /api/backtest/run`, etc.) are
untouched -- these are new, additive endpoints, not a replacement."""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator, Optional

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from .. import services
from ..schemas import (
    BacktestRunRequest, CompareRequest, JobOut, OptimizerRunRequest, ReportGenerateRequest,
)

router = APIRouter(tags=["jobs"])

#: How often the SSE stream re-checks job status. A local research tool
#: polling its own SQLite file -- sub-second latency isn't worth a more
#: elaborate push mechanism (see docs/RESEARCH_WORKSTATION.md).
_POLL_INTERVAL_SECONDS = 0.4


@router.post("/api/jobs/backtest", response_model=JobOut)
def submit_backtest_job(req: BacktestRunRequest) -> JobOut:
    job_id = services.submit_backtest_job(req)
    return JobOut(**services.get_job(job_id))


@router.post("/api/jobs/optimizer", response_model=JobOut)
def submit_optimizer_job(req: OptimizerRunRequest) -> JobOut:
    job_id = services.submit_optimizer_job(req)
    return JobOut(**services.get_job(job_id))


@router.post("/api/jobs/compare", response_model=JobOut)
def submit_compare_job(req: CompareRequest) -> JobOut:
    job_id = services.submit_compare_job(req)
    return JobOut(**services.get_job(job_id))


@router.post("/api/jobs/report", response_model=JobOut)
def submit_report_job(req: ReportGenerateRequest) -> JobOut:
    job_id = services.submit_report_job(req.run_id)
    return JobOut(**services.get_job(job_id))


@router.get("/api/jobs", response_model=list[JobOut])
def list_jobs(status: Optional[str] = None, limit: int = 100) -> list[JobOut]:
    return [JobOut(**j) for j in services.list_jobs(status=status, limit=limit)]


@router.get("/api/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: str) -> JobOut:
    return JobOut(**services.get_job(job_id))


@router.get("/api/jobs/{job_id}/stream")
def stream_job(job_id: str, request: Request) -> StreamingResponse:
    """Server-Sent Events: one ``data: {...}`` frame per status/progress
    change, ending the stream once the job reaches a terminal state.
    Missing job id fails fast (before the stream opens) rather than as a
    silently-empty stream -- `services.get_job` raises `ApiError` (mapped
    to a 400 by the app's exception handler) if `job_id` doesn't exist.

    `async def` generator + `await request.is_disconnected()`, not a plain
    `def` generator on a `time.sleep`: this endpoint's loop is already
    bounded by the job reaching a terminal state, but a long optimizer/
    walk-forward job plus a client that navigates away mid-run left the
    threadpool thread driving the old generator blocked in `time.sleep`
    until the job finished anyway -- same leak as `live.py`'s stream, just
    bounded by job runtime instead of unbounded. See that module for the
    full explanation of why a sync generator can't be cancelled here.
    """
    services.get_job(job_id)  # 400s immediately if the job doesn't exist

    async def event_stream() -> AsyncIterator[str]:
        last_payload: Optional[str] = None
        while not await request.is_disconnected():
            job = services.get_job(job_id)
            payload = json.dumps(job, default=str)
            if payload != last_payload:
                yield f"data: {payload}\n\n"
                last_payload = payload
            if job["status"] in ("completed", "failed"):
                break
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)

    return StreamingResponse(event_stream(), media_type="text/event-stream")
