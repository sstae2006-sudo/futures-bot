"""Phase 8B: the autonomous research server's dashboard controls -- status,
manual start/stop (mirrors `market_data.scheduler`'s dashboard controls,
in case `research_server.enabled` was left off in config.yaml but someone
wants to start it for this session anyway), a manual nightly-batch
trigger, and recent findings.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter

from .. import research_server_service as service
from .. import services
from ..schemas import (
    ConfigDeployRequest, ConfigDeploymentOut, InsightOut, JobOut, ParamsComparisonRequest, ResearchServerStatusOut,
)

router = APIRouter(tags=["research-server"])


@router.get("/api/research-server/status", response_model=ResearchServerStatusOut)
def get_status() -> ResearchServerStatusOut:
    return service.research_server_status()


@router.post("/api/research-server/start", response_model=ResearchServerStatusOut)
def start() -> ResearchServerStatusOut:
    return service.start_research_server()


@router.post("/api/research-server/stop", response_model=ResearchServerStatusOut)
def stop() -> ResearchServerStatusOut:
    return service.stop_research_server()


@router.post("/api/research-server/nightly/run-now")
def run_nightly_now() -> dict:
    summary = service.run_nightly_batch_now()
    return {"summary": summary}


@router.get("/api/research-server/findings", response_model=list[InsightOut])
def get_findings() -> list[InsightOut]:
    return service.get_findings()


# --- Phase 10.2: deploy/rollback a "recommendation" finding, or test it first ---

@router.post("/api/research-server/insights/deploy", response_model=ConfigDeploymentOut)
def deploy_finding(req: ConfigDeployRequest) -> ConfigDeploymentOut:
    return service.deploy_strategy_params(req.strategy, req.params, req.run_id)


@router.post("/api/research-server/insights/config-deployments/{deployment_id}/rollback", response_model=ConfigDeploymentOut)
def rollback_finding(deployment_id: str) -> ConfigDeploymentOut:
    return service.rollback_config_deployment(deployment_id)


@router.get("/api/research-server/insights/config-deployments", response_model=list[ConfigDeploymentOut])
def list_config_deployments(strategy: Optional[str] = None) -> list[ConfigDeploymentOut]:
    return service.list_config_deployments(strategy=strategy)


@router.post("/api/research-server/insights/test-params", response_model=JobOut)
def test_params(req: ParamsComparisonRequest) -> JobOut:
    job_id = services.submit_params_comparison_job(req.strategy, req.dataset, req.recommended_params)
    return JobOut(**services.get_job(job_id))
