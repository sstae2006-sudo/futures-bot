from __future__ import annotations

from fastapi import APIRouter

from .. import services
from ..schemas import DatasetInfo, StrategyInfo

router = APIRouter(tags=["strategies"])


@router.get("/api/strategies", response_model=list[StrategyInfo])
def list_strategies() -> list[StrategyInfo]:
    return services.list_strategies()


@router.get("/api/strategies/{name}", response_model=StrategyInfo)
def get_strategy(name: str) -> StrategyInfo:
    return services.get_strategy(name)


@router.get("/api/datasets", response_model=list[DatasetInfo])
def list_datasets() -> list[DatasetInfo]:
    return services.list_datasets()
