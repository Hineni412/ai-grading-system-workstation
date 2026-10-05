from __future__ import annotations

from uuid import UUID
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request

from backend.api.app import ApiError, ErrorResponse
from backend.api.dependencies import (
    get_job_manager,
    get_ops_self_check_service,
    get_ops_write_service,
    get_data_root,
    get_grading_db,
    get_scan_grading_workspace,
    get_review_application_service,
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
from backend.ops.archive import OpsArchiveInvalid, OpsArchiveTooLarge
from backend.ops.journal import OpsOperationBusy
from backend.ops.plan_store import (
    OpsConfirmationExpired,
    OpsConfirmationInvalid,
    OpsConfirmationUsed,
)
from backend.ops.service import OpsSelfCheckService
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


@router.get("/storage")
def get_storage(db=Depends(get_grading_db), data_root: Path=Depends(get_data_root),
                workspace=Depends(get_scan_grading_workspace), review_service=Depends(get_review_application_service)):
    from backend.files.session_originals import storage_overview
    from backend.api.routers.sessions import _originals_snapshot
    from backend.files.session_cleanup import session_lifecycle_guard, _collect_session_file_paths
    overview = storage_overview(data_root)
    sessions = db.sessions.list_grading_sessions(include_deleted=True)
    # Reuse file ownership only within this read. Clear/release actions still
    # collect current references independently under their lifecycle guards.
    references = {int(session["id"]): _collect_session_file_paths(db, int(session["id"]), data_root)
                  for session in sessions}
    rows = []
    for session in sorted(sessions, key=lambda s: (str(s.get("created_at") or ""), int(s["id"])), reverse=True):
        if session.get("is_deleted"):
            continue
        sid = int(session["id"])
        shared = set().union(*(paths for owner, paths in references.items() if owner != sid))
        with session_lifecycle_guard(sid):
            snapshot = _originals_snapshot(sid, db, data_root, workspace, review_service, shared_refs=shared)
        rows.append({"session_id": sid, "name": session["session_name"], "created_at": session.get("created_at"),
                     "status_label": {"completed": "已完成", "graded": "已完成", "grading": "批改中", "reviewing": "复核中"}.get(session.get("status"), "未开始"),
                     "originals_state": snapshot["originals_state"], "scan_bytes": snapshot["scan_bytes"],
                     "page_bytes": snapshot["page_bytes"] + snapshot["annotation_bytes"],
                     "release_bytes": snapshot["release_bytes"], "clear_bytes": snapshot["clear_bytes"],
                     "can_release_scans": snapshot["can_release_scans"], "can_clear": snapshot["can_clear"],
                     "blocked_reason": snapshot["blocked_reason"]})
    return {**overview, "sessions": rows}


@router.post("/storage/legacy-annotations/clear")
def clear_old_annotations(db=Depends(get_grading_db), data_root: Path=Depends(get_data_root)):
    from backend.files.session_originals import clear_legacy_annotations
    try:
        return clear_legacy_annotations(db, data_root)
    except OSError as exc:
        raise ApiError(409, "legacy_annotations_clear_incomplete", "部分旧批注图正在使用，请关闭图片后刷新状态。") from exc


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
