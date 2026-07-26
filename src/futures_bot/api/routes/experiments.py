from __future__ import annotations

from typing import Optional

from fastapi import APIRouter

from .. import services
from ..schemas import ExperimentCreateRequest, ExperimentNotesUpdate, ExperimentOut

router = APIRouter(tags=["experiments"])


@router.post("/api/experiments", response_model=ExperimentOut)
def create_experiment(req: ExperimentCreateRequest) -> ExperimentOut:
    return services.create_experiment(req)


@router.get("/api/experiments", response_model=list[ExperimentOut])
def list_experiments(strategy: Optional[str] = None, limit: int = 100) -> list[ExperimentOut]:
    return services.list_experiments(strategy=strategy, limit=limit)


@router.get("/api/experiments/{experiment_id}", response_model=ExperimentOut)
def get_experiment(experiment_id: str) -> ExperimentOut:
    return services.get_experiment(experiment_id)


@router.patch("/api/experiments/{experiment_id}/notes", response_model=ExperimentOut)
def update_experiment_notes(experiment_id: str, req: ExperimentNotesUpdate) -> ExperimentOut:
    return services.update_experiment_notes(experiment_id, req.notes)
