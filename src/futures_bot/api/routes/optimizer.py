from __future__ import annotations

from fastapi import APIRouter

from .. import services
from ..schemas import OptimizerResultOut, OptimizerRunRequest, OverfitVerdict, TrialOut

router = APIRouter(tags=["optimizer"])


@router.post("/api/optimizer/run", response_model=OptimizerResultOut)
def run_optimizer(req: OptimizerRunRequest) -> OptimizerResultOut:
    return services.run_optimizer_job(req)


@router.get("/api/optimizer/results/{batch_id}", response_model=list[TrialOut])
def get_optimizer_results(batch_id: str) -> list[TrialOut]:
    return services.get_optimizer_trials(batch_id)


@router.get("/api/walk-forward/{run_id}/verdict", response_model=OverfitVerdict)
def get_overfit_verdict(run_id: str) -> OverfitVerdict:
    run = services.get_run(run_id)
    return services.overfit_verdict(run)
