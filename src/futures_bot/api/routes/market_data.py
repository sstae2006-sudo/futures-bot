"""Phase 8A: the local market-data pipeline's dashboard controls -- status,
manual sync/backfill/verify/repair (the same operations `--sync-data`/
`--backfill`/`--verify-data`/`--repair-gaps` expose on the CLI), and
scheduler start/stop, mirroring `routes/live.py`'s shape for the
paper-trading session.
"""

from __future__ import annotations

from fastapi import APIRouter

from .. import market_data_service as service
from ..schemas import (
    BackfillRequest, GapOut, MarketDataOverviewOut, SchedulerStartRequest, SchedulerStatusOut,
    SyncRequest, SyncRunOut,
)

router = APIRouter(tags=["market-data"])


@router.get("/api/market-data/overview", response_model=MarketDataOverviewOut)
def get_market_data_overview() -> MarketDataOverviewOut:
    return service.market_data_overview()


@router.get("/api/market-data/runs", response_model=list[SyncRunOut])
def list_sync_runs(product_code: str | None = None, limit: int = 50) -> list[SyncRunOut]:
    return service.list_sync_runs(product_code=product_code, limit=limit)


@router.get("/api/market-data/gaps", response_model=list[GapOut])
def list_gaps(product_code: str | None = None) -> list[GapOut]:
    return service.list_gaps(product_code=product_code)


@router.post("/api/market-data/sync", response_model=SyncRunOut)
def sync_now(req: SyncRequest) -> SyncRunOut:
    return service.run_sync(req.product_code, req.resolution)


@router.post("/api/market-data/backfill", response_model=SyncRunOut)
def backfill_now(req: BackfillRequest) -> SyncRunOut:
    return service.run_backfill(req.product_code, req.resolution, req.start, req.end)


@router.post("/api/market-data/verify")
def verify_now(req: SyncRequest) -> dict:
    return service.run_verify(req.product_code, req.resolution)


@router.post("/api/market-data/repair")
def repair_now(req: SyncRequest) -> dict:
    return service.run_repair(req.product_code, req.resolution)


@router.post("/api/market-data/scheduler/start", response_model=SchedulerStatusOut)
def start_scheduler(req: SchedulerStartRequest) -> SchedulerStatusOut:
    targets = [(t.product_code, t.resolution) for t in req.targets]
    return service.start_scheduler(targets, req.interval_seconds)


@router.post("/api/market-data/scheduler/stop", response_model=SchedulerStatusOut)
def stop_scheduler() -> SchedulerStatusOut:
    return service.stop_scheduler()


@router.get("/api/market-data/scheduler/status", response_model=SchedulerStatusOut)
def get_scheduler_status() -> SchedulerStatusOut:
    return service.scheduler_status()
