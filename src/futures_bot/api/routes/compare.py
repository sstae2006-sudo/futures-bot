from __future__ import annotations

from fastapi import APIRouter

from .. import services
from ..schemas import CompareRequest, CompareResult

router = APIRouter(tags=["compare"])


@router.post("/api/compare/run", response_model=CompareResult)
def run_compare(req: CompareRequest) -> CompareResult:
    return services.run_compare(req)
