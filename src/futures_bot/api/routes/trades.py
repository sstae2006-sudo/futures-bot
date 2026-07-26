from __future__ import annotations

from typing import Optional

from fastapi import APIRouter

from .. import services
from ..schemas import PerformanceOut, RegimePerformanceOut, TradeAnalyticsSummary, TradeOut

router = APIRouter(tags=["trades"])


@router.get("/api/trades", response_model=list[TradeOut])
def list_trades(
    run_id: Optional[str] = None, strategy: Optional[str] = None,
    side: Optional[str] = None, outcome: Optional[str] = None,
) -> list[TradeOut]:
    return services.list_trades(run_id=run_id, strategy=strategy, side=side, outcome=outcome)


@router.get("/api/trades/analytics", response_model=TradeAnalyticsSummary)
def get_trade_analytics(
    run_id: Optional[str] = None, strategy: Optional[str] = None, top_n: int = 10,
) -> TradeAnalyticsSummary:
    return services.trade_analytics_summary(run_id=run_id, strategy=strategy, top_n=top_n)


@router.get("/api/performance/{run_id}", response_model=PerformanceOut)
def get_performance(run_id: str) -> PerformanceOut:
    return services.get_performance(run_id)


@router.get("/api/regime/performance", response_model=RegimePerformanceOut)
def get_regime_performance(strategy: Optional[str] = None) -> RegimePerformanceOut:
    return services.regime_performance(strategy=strategy)
