from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from backend.api.app import ApiError, ErrorResponse
from backend.api.dependencies import (
    get_job_manager,
    get_ops_self_check_service,
    get_ops_write_service,
)
from backend.api.routers.jobs import _job_response
from backend.api.schemas.jobs import JobResponse
from backend.api.schemas.ops import (
    OpsBackupListResponse,
    OpsImportUploadResponse,
    OpsJobSubmitRequest,
    OpsOperationResponse,
    OpsPreflightRequest,
    OpsPreflightResponse,
    OpsSelfCheckResponse,
)
from backend.jobs.manager import JobManager, UnsupportedJobTypeError
from backend.ops.service import OpsSelfCheckService
from backend.ops.archive import OpsArchiveInvalid, OpsArchiveTooLarge
from backend.ops.journal import OpsOperationBusy
from backend.ops.plan_store import (
    OpsConfirmationExpired,
    OpsConfirmationInvalid,
    OpsConfirmationUsed,
)
from backend.ops.write_service import (
    OpsPreflightStale,
    OpsRequestInvalid,
    OpsResourceNotFound,
    OpsWriteService,
)


router = APIRouter(prefix="/api/ops", tags=["ops"])
OPS_UNAVAILABLE_RESPONSE = {
    503: {
        "model": ErrorResponse,
        "description": "Ops status is temporarily unavailable",
    }
}
OPS_MAX_UPLOAD_BYTES = 200 * 1024 * 1024


@router.get(
    "/self-check",
    response_model=OpsSelfCheckResponse,
    responses=OPS_UNAVAILABLE_RESPONSE,
)
def get_self_check(
    service: OpsSelfCheckService = Depends(get_ops_self_check_service),
) -> OpsSelfCheckResponse:
    try:
        return OpsSelfCheckResponse.model_validate(service.build_snapshot())
    except Exception as exc:
        raise ApiError(
            503,
            "ops_self_check_unavailable",
            "System self-check is temporarily unavailable",
        ) from exc


@router.get(
    "/backups",
    response_model=OpsBackupListResponse,
    responses=OPS_UNAVAILABLE_RESPONSE,
)
def get_backups(
    limit: int = Query(default=50, ge=1, le=100),
    service: OpsSelfCheckService = Depends(get_ops_self_check_service),
) -> OpsBackupListResponse:
    try:
        return OpsBackupListResponse.model_validate(service.list_backups(limit))
    except Exception as exc:
        raise ApiError(
            503,
            "ops_backup_list_unavailable",
            "Backup list is temporarily unavailable",
        ) from exc


@router.post(
    "/transfer-import/uploads",
    response_model=OpsImportUploadResponse,
    status_code=201,
)
async def stage_transfer_import_upload(
    request: Request,
    filename: str = Query(min_length=1, max_length=255),
    service: OpsWriteService = Depends(get_ops_write_service),
) -> OpsImportUploadResponse:
    content_length = request.headers.get("content-length")
    if content_length is not None and int(content_length) > OPS_MAX_UPLOAD_BYTES:
        raise ApiError(413, "ops_upload_too_large", "Ops upload is too large")
    content_type = str(request.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    if content_type not in {"application/zip", "application/x-zip-compressed", "application/octet-stream"}:
        raise ApiError(415, "ops_upload_type_not_supported", "Ops upload type is not supported")
    try:
        return OpsImportUploadResponse.model_validate(
            await service.stage_import_upload(filename=filename, chunks=request.stream())
        )
    except OpsArchiveTooLarge as exc:
        raise ApiError(413, "ops_upload_too_large", "Ops upload is too large") from exc
    except OpsArchiveInvalid as exc:
        raise ApiError(422, "ops_archive_invalid", "Ops archive is invalid") from exc


@router.post("/preflights", response_model=OpsPreflightResponse)
def create_ops_preflight(
    body: OpsPreflightRequest,
    service: OpsWriteService = Depends(get_ops_write_service),
) -> OpsPreflightResponse:
    try:
        return OpsPreflightResponse.model_validate(service.preflight(body))
    except Exception as exc:
        raise _ops_write_api_error(exc) from exc


@router.post("/jobs", response_model=JobResponse, status_code=202)
def submit_ops_job(
    body: OpsJobSubmitRequest,
    service: OpsWriteService = Depends(get_ops_write_service),
    manager: JobManager = Depends(get_job_manager),
) -> JobResponse:
    try:
        return _job_response(service.submit(body.confirmation_token, manager))
    except UnsupportedJobTypeError as exc:
        raise ApiError(503, "ops_job_unavailable", "Ops job is temporarily unavailable") from exc
    except Exception as exc:
        raise _ops_write_api_error(exc) from exc


@router.get("/operations/{operation_id}", response_model=OpsOperationResponse)
def get_ops_operation(
    operation_id: UUID,
    service: OpsWriteService = Depends(get_ops_write_service),
) -> OpsOperationResponse:
    try:
        return OpsOperationResponse.model_validate(service.operation_status(str(operation_id)))
    except Exception as exc:
        raise _ops_write_api_error(exc) from exc


@router.post(
    "/operations/{operation_id}/cancel",
    response_model=OpsOperationResponse,
)
def cancel_ops_operation(
    operation_id: UUID,
    service: OpsWriteService = Depends(get_ops_write_service),
) -> OpsOperationResponse:
    try:
        return OpsOperationResponse.model_validate(service.cancel_operation(str(operation_id)))
    except Exception as exc:
        raise _ops_write_api_error(exc) from exc


def _ops_write_api_error(exc: Exception) -> ApiError:
    if isinstance(exc, OpsResourceNotFound):
        return ApiError(404, "ops_resource_not_found", "Ops resource not found")
    if isinstance(exc, OpsConfirmationInvalid):
        return ApiError(404, "ops_confirmation_invalid", "Ops confirmation is invalid")
    if isinstance(exc, OpsConfirmationExpired):
        return ApiError(409, "ops_confirmation_expired", "Ops confirmation has expired")
    if isinstance(exc, OpsConfirmationUsed):
        return ApiError(409, "ops_confirmation_used", "Ops confirmation has already been used")
    if isinstance(exc, OpsPreflightStale):
        return ApiError(409, "ops_preflight_stale", "Ops preflight is stale")
    if isinstance(exc, OpsOperationBusy):
        return ApiError(409, "ops_operation_busy", "Ops operation has already started")
    if isinstance(exc, (OpsRequestInvalid, OpsArchiveInvalid)):
        return ApiError(422, "ops_request_invalid", "Ops request is invalid")
    return ApiError(503, "ops_write_unavailable", "Ops operation is temporarily unavailable")


__all__ = ["router"]
