"""Universal client trade importer routes (Phase 10.1). See
`research/trade_import.py`'s module docstring for the format-detection/
FIFO-matching design, and `api/services.py`'s `stage_import_upload`/
`submit_import_confirmation` for the upload-preview-confirm flow."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, File, Form, UploadFile

from .. import services
from ..schemas import (
    ClientProfileCreateRequest, ClientProfileOut, ImportConfirmRequest, ImportHistoryOut, ImportUploadResponse, JobOut,
)

router = APIRouter(tags=["imports"])


@router.post("/api/imports/profiles", response_model=ClientProfileOut)
def create_client_profile(req: ClientProfileCreateRequest) -> ClientProfileOut:
    return services.create_client_profile(req)


@router.get("/api/imports/profiles", response_model=list[ClientProfileOut])
def list_client_profiles() -> list[ClientProfileOut]:
    return services.list_client_profiles()


@router.post("/api/imports/upload", response_model=ImportUploadResponse)
async def upload_import_file(profile_id: str = Form(...), file: UploadFile = File(...)) -> ImportUploadResponse:
    content = await file.read()
    return services.stage_import_upload(profile_id, file.filename or "upload.csv", content)


@router.post("/api/imports/{import_id}/confirm", response_model=JobOut)
def confirm_import(import_id: str, req: ImportConfirmRequest) -> JobOut:
    job_id = services.submit_import_confirmation(import_id, req.mapping)
    return JobOut(**services.get_job(job_id))


@router.delete("/api/imports/staging/{import_id}")
def cancel_import_staging(import_id: str) -> dict[str, bool]:
    services.cancel_import_staging(import_id)
    return {"cancelled": True}


@router.get("/api/imports/history", response_model=list[ImportHistoryOut])
def list_import_history(profile_id: Optional[str] = None, limit: int = 100) -> list[ImportHistoryOut]:
    return services.list_import_history(profile_id=profile_id, limit=limit)
