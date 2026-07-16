from __future__ import annotations

from tempfile import SpooledTemporaryFile
from urllib.parse import unquote

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import FileResponse

from backend.api.app import ApiError
from backend.api.dependencies import (
    get_grading_db,
    get_job_manager,
    get_scan_grading_workspace,
)
from backend.api.routers.jobs import _job_response
from backend.api.routers.sessions import _require_session
from backend.api.schemas.jobs import JobResponse
from backend.api.schemas.scan import (
    ScanAnalyzeRequest,
    ScanDecisionRequest,
    ScanDecisionResponse,
    ScanGradingWorkspaceResponse,
    ScanPreflightResponse,
    ScanUploadBatchResponse,
    ScanUploadFreezeRequest,
    ScanUploadResponse,
)
from backend.scan_grading.workspace import (
    FrozenUploadBatchError,
    InvalidScanUploadError,
    ScanGradingWorkspace,
    ScanGradingWorkspaceError,
    ScanUploadTooLargeError,
    UploadBatchRevisionError,
)
from backend.jobs.manager import JobManager, UnsupportedJobTypeError
from db_manager import DBManager


router = APIRouter(prefix="/api", tags=["scan"])


@router.get(
    "/sessions/{session_id}/grading-workspace",
    response_model=ScanGradingWorkspaceResponse,
)
def get_grading_workspace(
    session_id: int,
    db: DBManager = Depends(get_grading_db),
    workspace: ScanGradingWorkspace = Depends(get_scan_grading_workspace),
) -> ScanGradingWorkspaceResponse:
    _require_session(db, session_id)
    return ScanGradingWorkspaceResponse.model_validate(workspace.get_workspace(session_id))


@router.post(
    "/sessions/{session_id}/scan-uploads",
    response_model=ScanUploadResponse,
    status_code=201,
    openapi_extra={
        "parameters": [
            {
                "name": "X-Upload-Filename",
                "in": "header",
                "required": True,
                "schema": {"type": "string", "minLength": 5},
            },
            {
                "name": "X-Content-SHA256",
                "in": "header",
                "required": True,
                "schema": {"type": "string", "pattern": "^[0-9a-fA-F]{64}$"},
            },
        ],
        "requestBody": {
            "required": True,
            "content": {
                "application/pdf": {"schema": {"type": "string", "format": "binary"}},
                "image/jpeg": {"schema": {"type": "string", "format": "binary"}},
                "image/png": {"schema": {"type": "string", "format": "binary"}},
            },
        },
    },
)
async def upload_session_scan(
    session_id: int,
    request: Request,
    response: Response,
    db: DBManager = Depends(get_grading_db),
    workspace: ScanGradingWorkspace = Depends(get_scan_grading_workspace),
) -> ScanUploadResponse:
    _require_session(db, session_id)
    filename = unquote(str(request.headers.get("x-upload-filename") or ""))
    digest = str(request.headers.get("x-content-sha256") or "")
    media_type = str(request.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    try:
        try:
            declared_size = int(request.headers.get("content-length") or 0)
        except ValueError:
            declared_size = 0
        if declared_size > workspace.max_file_bytes:
            raise ScanUploadTooLargeError("scan upload is too large")
        with SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b") as upload:
            received = 0
            async for chunk in request.stream():
                received += len(chunk)
                if received > workspace.max_file_bytes:
                    raise ScanUploadTooLargeError("scan upload is too large")
                upload.write(chunk)
            upload.seek(0)
            result = workspace.add_upload(
                session_id,
                filename=filename,
                media_type=media_type,
                content_sha256=digest,
                source=upload,
            )
    except FrozenUploadBatchError as exc:
        raise ApiError(409, "scan_upload_batch_frozen", "Scan upload batch is frozen") from exc
    except ScanUploadTooLargeError as exc:
        raise ApiError(413, "scan_upload_too_large", "Scan upload is too large") from exc
    except InvalidScanUploadError as exc:
        raise ApiError(415, "invalid_scan_upload", "Scan upload type or metadata is invalid") from exc
    except ScanGradingWorkspaceError as exc:
        raise ApiError(422, "scan_upload_rejected", "Scan upload was rejected") from exc
    if result["duplicate"]:
        response.status_code = 200
    return ScanUploadResponse.model_validate(result)


def _mutate_draft_uploads(operation) -> ScanUploadBatchResponse:
    try:
        return ScanUploadBatchResponse.model_validate(operation())
    except UploadBatchRevisionError as exc:
        raise ApiError(409, "scan_upload_revision_conflict", "Scan upload batch changed") from exc
    except FrozenUploadBatchError as exc:
        raise ApiError(409, "scan_upload_batch_frozen", "Scan upload batch is frozen") from exc
    except ScanGradingWorkspaceError as exc:
        raise ApiError(404, "scan_upload_not_found", "Scan upload was not found") from exc


@router.delete(
    "/sessions/{session_id}/scan-uploads/{upload_id}",
    response_model=ScanUploadBatchResponse,
)
def remove_session_scan_upload(
    session_id: int,
    upload_id: str,
    expected_revision: int,
    db: DBManager = Depends(get_grading_db),
    workspace: ScanGradingWorkspace = Depends(get_scan_grading_workspace),
) -> ScanUploadBatchResponse:
    _require_session(db, session_id)
    return _mutate_draft_uploads(
        lambda: workspace.remove_upload(
            session_id,
            upload_id,
            expected_revision=expected_revision,
        )
    )


@router.delete(
    "/sessions/{session_id}/scan-uploads",
    response_model=ScanUploadBatchResponse,
)
def clear_session_scan_uploads(
    session_id: int,
    expected_revision: int,
    db: DBManager = Depends(get_grading_db),
    workspace: ScanGradingWorkspace = Depends(get_scan_grading_workspace),
) -> ScanUploadBatchResponse:
    _require_session(db, session_id)
    return _mutate_draft_uploads(
        lambda: workspace.clear_uploads(
            session_id,
            expected_revision=expected_revision,
        )
    )


@router.post(
    "/sessions/{session_id}/scan-uploads/freeze",
    response_model=ScanUploadBatchResponse,
)
def freeze_session_scan_uploads(
    session_id: int,
    request: ScanUploadFreezeRequest,
    db: DBManager = Depends(get_grading_db),
    workspace: ScanGradingWorkspace = Depends(get_scan_grading_workspace),
) -> ScanUploadBatchResponse:
    _require_session(db, session_id)
    try:
        batch = workspace.freeze_uploads(
            session_id,
            expected_revision=request.expected_revision,
        )
    except UploadBatchRevisionError as exc:
        raise ApiError(409, "scan_upload_revision_conflict", "Scan upload batch changed") from exc
    except FrozenUploadBatchError as exc:
        raise ApiError(409, "scan_upload_batch_frozen", "Scan upload batch is frozen") from exc
    except ScanGradingWorkspaceError as exc:
        raise ApiError(422, "scan_upload_batch_invalid", "Scan upload batch cannot be frozen") from exc
    return ScanUploadBatchResponse.model_validate(batch)


@router.post(
    "/sessions/{session_id}/scan-uploads/new-batch",
    response_model=ScanUploadBatchResponse,
)
def start_new_session_scan_batch(
    session_id: int,
    db: DBManager = Depends(get_grading_db),
    workspace: ScanGradingWorkspace = Depends(get_scan_grading_workspace),
) -> ScanUploadBatchResponse:
    _require_session(db, session_id)
    try:
        batch = workspace.start_new_upload_batch(session_id)
    except ScanGradingWorkspaceError as exc:
        raise ApiError(409, "grading_run_still_active", "Active grading run must be resolved first") from exc
    return ScanUploadBatchResponse.model_validate(batch)


@router.get(
    "/sessions/{session_id}/scan/preflight",
    response_model=ScanPreflightResponse,
)
def get_session_scan_preflight(
    session_id: int,
    db: DBManager = Depends(get_grading_db),
    workspace: ScanGradingWorkspace = Depends(get_scan_grading_workspace),
) -> ScanPreflightResponse:
    _require_session(db, session_id)
    try:
        payload = workspace.get_preflight(session_id)
    except ScanGradingWorkspaceError as exc:
        raise ApiError(404, "scan_preflight_not_found", "Scan preflight is not available") from exc
    return ScanPreflightResponse.model_validate(payload)


@router.put(
    "/sessions/{session_id}/scan/preflight/decisions",
    response_model=ScanDecisionResponse,
)
def save_session_scan_decisions(
    session_id: int,
    request: ScanDecisionRequest,
    db: DBManager = Depends(get_grading_db),
    workspace: ScanGradingWorkspace = Depends(get_scan_grading_workspace),
) -> ScanDecisionResponse:
    _require_session(db, session_id)
    valid_student_ids = {
        int(student["id"])
        for student in db.list_students()
        if student.get("id") is not None
    }
    try:
        result = workspace.save_decisions(
            session_id,
            expected_revision=request.expected_revision,
            valid_student_ids=valid_student_ids,
            decisions=[item.model_dump() for item in request.decisions],
        )
    except UploadBatchRevisionError as exc:
        raise ApiError(409, "scan_decision_revision_conflict", "Scan decisions changed") from exc
    except ScanGradingWorkspaceError as exc:
        raise ApiError(422, "scan_decision_invalid", "Scan decision is invalid") from exc
    return ScanDecisionResponse.model_validate(result)


@router.get("/sessions/{session_id}/scan/preflight/media/{media_ref}")
def get_session_scan_preflight_media(
    session_id: int,
    media_ref: str,
    db: DBManager = Depends(get_grading_db),
    workspace: ScanGradingWorkspace = Depends(get_scan_grading_workspace),
) -> FileResponse:
    _require_session(db, session_id)
    try:
        path = workspace.resolve_preflight_media(session_id, media_ref)
    except ScanGradingWorkspaceError as exc:
        raise ApiError(404, "scan_preflight_media_not_found", "Scan preview is not available") from exc
    return FileResponse(
        path,
        media_type="image/png" if path.suffix.lower() == ".png" else "image/jpeg",
        headers={"Cache-Control": "no-store"},
    )


@router.post(
    "/sessions/{session_id}/scan/analyze",
    response_model=JobResponse,
    status_code=202,
)
def analyze_session_scans(
    session_id: int,
    request: ScanAnalyzeRequest | None = None,
    db: DBManager = Depends(get_grading_db),
    manager: JobManager = Depends(get_job_manager),
    workspace: ScanGradingWorkspace = Depends(get_scan_grading_workspace),
) -> JobResponse:
    _require_session(db, session_id)
    request = request or ScanAnalyzeRequest()
    payload: dict[str, object] = {
        "session_id": int(session_id),
        "enhance_images": request.enhance_images,
    }
    if request.ocr_workers is not None:
        payload["ocr_workers"] = request.ocr_workers
    if workspace.upload_batch_exists(session_id):
        try:
            payload["exams_dir"] = str(workspace.frozen_scan_dir(session_id))
        except ScanGradingWorkspaceError as exc:
            raise ApiError(
                409,
                "scan_upload_batch_not_frozen",
                "Freeze the scan upload batch before preflight",
            ) from exc
    if request.front_page_parity:
        payload["front_page_parity"] = request.front_page_parity
    try:
        job = manager.submit("scan_analysis", payload)
    except UnsupportedJobTypeError as exc:
        raise ApiError(
            404,
            "job_type_not_supported",
            "Job type is not supported",
            {"job_type": "scan_analysis"},
        ) from exc
    return _job_response(job)
