from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter
from fastapi.responses import Response

from .. import services
from ..schemas import (
    AiBacktestComparisonRequest, CorrelationRowOut, DatasetHealthOut, DeploymentOut, DeploymentStatusOut,
    FeatureDistributionOut, JobOut, MlModelOut, ModelNotesUpdate, ModelTrainRequest, ModelTrainResponse,
    PredictRequest, PredictionOut,
)

router = APIRouter(tags=["ml"])


@router.get("/api/ml/dataset")
def get_ml_dataset_info() -> dict[str, Any]:
    """Prepares the research API for future ML work (Phase 6B): dataset
    size, the feature columns actually present across recorded trades (the
    union of every strategy's `Signal.metadata` keys, same convention
    `research.features.write_ml_dataset_csv` already uses), and outcome
    label counts. See `GET /api/ml/dataset-health` for the per-strategy,
    Phase 9 version of this with a READY/WARNING/NOT_ENOUGH_DATA verdict."""
    return services.ml_dataset_info()


@router.get("/api/ml/dataset-health", response_model=DatasetHealthOut)
def get_ml_dataset_health(strategy: str) -> DatasetHealthOut:
    return services.get_ml_dataset_health(strategy)


@router.get("/api/ml/features/{feature}/distribution", response_model=FeatureDistributionOut)
def get_feature_distribution(feature: str, strategy: str) -> FeatureDistributionOut:
    return services.get_feature_distribution(strategy, feature)


@router.get("/api/ml/correlation", response_model=list[CorrelationRowOut])
def get_correlation(strategy: str) -> list[CorrelationRowOut]:
    return services.get_correlation(strategy)


@router.get("/api/ml/dataset/export")
def export_ml_dataset(strategy: str) -> Response:
    csv_text = services.export_ml_dataset_csv(strategy)
    return Response(
        content=csv_text, media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{strategy}_ml_dataset.csv"'},
    )


# --- Models (Phase 9) ---

@router.post("/api/ml/models", response_model=ModelTrainResponse)
def submit_model_training(req: ModelTrainRequest) -> ModelTrainResponse:
    job_id, model_id = services.submit_model_training_job(req)
    return ModelTrainResponse(job_id=job_id, model_id=model_id)


@router.get("/api/ml/models", response_model=list[MlModelOut])
def list_models(
    strategy: Optional[str] = None, model_family: Optional[str] = None, include_archived: bool = False,
) -> list[MlModelOut]:
    return services.list_models(strategy=strategy, model_family=model_family, include_archived=include_archived)


@router.get("/api/ml/models/{model_id}", response_model=MlModelOut)
def get_model(model_id: str) -> MlModelOut:
    return services.get_model(model_id)


@router.get("/api/ml/models/family/{model_family}/versions", response_model=list[MlModelOut])
def get_model_versions(model_family: str) -> list[MlModelOut]:
    return services.get_model_versions(model_family)


@router.post("/api/ml/models/{model_id}/stop", response_model=MlModelOut)
def stop_model(model_id: str) -> MlModelOut:
    return services.stop_model(model_id)


@router.post("/api/ml/models/{model_id}/archive", response_model=MlModelOut)
def archive_model(model_id: str) -> MlModelOut:
    return services.archive_model(model_id, archived=True)


@router.post("/api/ml/models/{model_id}/unarchive", response_model=MlModelOut)
def unarchive_model(model_id: str) -> MlModelOut:
    return services.archive_model(model_id, archived=False)


@router.delete("/api/ml/models/{model_id}")
def delete_model(model_id: str) -> dict[str, bool]:
    services.delete_model(model_id)
    return {"deleted": True}


@router.patch("/api/ml/models/{model_id}/notes", response_model=MlModelOut)
def update_model_notes(model_id: str, req: ModelNotesUpdate) -> MlModelOut:
    return services.update_model_notes(model_id, req.notes)


@router.post("/api/ml/models/{model_id}/deploy", response_model=DeploymentOut)
def deploy_model(model_id: str) -> DeploymentOut:
    return services.deploy_model(model_id)


@router.post("/api/ml/models/{model_id}/rollback", response_model=DeploymentOut)
def rollback_to_model(model_id: str, strategy: str) -> DeploymentOut:
    return services.rollback_model(strategy, model_id)


@router.get("/api/strategies/{strategy}/deployment", response_model=DeploymentStatusOut)
def get_deployment(strategy: str) -> DeploymentStatusOut:
    return services.get_deployment(strategy)


@router.post("/api/ml/models/{model_id}/backtest-metrics")
def compute_model_backtest_metrics(model_id: str, dataset: str) -> dict:
    return services.compute_model_backtest_metrics(model_id, dataset)


@router.post("/api/ml/predict", response_model=PredictionOut)
def predict_trade(req: PredictRequest) -> PredictionOut:
    return services.predict_trade(req)


@router.post("/api/jobs/ai-backtest-compare", response_model=JobOut)
def submit_ai_backtest_comparison(req: AiBacktestComparisonRequest) -> JobOut:
    job_id = services.submit_ai_backtest_comparison_job(req)
    return JobOut(**services.get_job(job_id))
