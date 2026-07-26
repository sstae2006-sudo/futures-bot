from __future__ import annotations

from typing import Optional

from fastapi import APIRouter

from .. import services
from ..schemas import BacktestRunRequest, RunDetail, RunSummary

router = APIRouter(tags=["backtests"])


@router.post("/api/backtest/run", response_model=RunDetail)
def run_backtest(req: BacktestRunRequest) -> RunDetail:
    """Runs synchronously and returns the finished result. See
    docs/RESEARCH_INTERFACE.md's Phase 6B notes on why this isn't
    background-job-plus-polling yet -- the incremental indicators from
    Phase 4 make even a full-history backtest fast enough (single-digit
    seconds for hundreds of thousands of bars) that a synchronous request is
    still a reasonable first cut."""
    return services.run_backtest_job(req)


@router.get("/api/backtests", response_model=list[RunSummary])
def list_backtests(strategy: Optional[str] = None, limit: int = 100) -> list[RunSummary]:
    return services.list_backtest_runs(strategy=strategy, limit=limit)


@router.get("/api/backtests/{run_id}", response_model=RunDetail)
def get_backtest(run_id: str) -> RunDetail:
    return services.get_run(run_id)
