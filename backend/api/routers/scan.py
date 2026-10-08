from __future__ import annotations

from tempfile import SpooledTemporaryFile
from urllib.parse import unquote

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import FileResponse

from backend.api.app import ApiError
from backend.api.dependencies import (
    get_grading_db,
    get_scan_grading_workspace,
    get_template_upload_service,
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
    ScanStudentMatchOptionsResponse,
    ScanUploadBatchResponse,
    ScanUploadFreezeRequest,
    ScanUploadResponse,
)
from backend.config_workspace.publish import load_editor_config
from backend.jobs.manager import ActiveJobExistsError, UnsupportedJobTypeError
from backend.name_pinyin import (
    student_name_initials as _student_name_initials,
)
from backend.name_pinyin import (
    student_name_pinyin as _student_name_pinyin,
)
from backend.repositories.access import GradingRepositoryAccess
from backend.repositories.sessions import SessionDeletionActiveWork
from backend.scan_grading.workspace import (
    ActiveScanAnalysisError,
    FrozenUploadBatchError,
    InvalidScanUploadError,
    ScanGradingWorkspace,
    ScanGradingWorkspaceError,
    ScanMatchConflictError,
    ScanReplacementCleanupIncompleteError,
    ScanUploadTooLargeError,
    UploadBatchRevisionError,
)
from backend.files.session_cleanup import SessionDerivedTrainingDataExists
from backend.exam_intake.template_upload_service import TemplateUploadError, TemplateUploadService

from backend.files.session_originals import ScanSourcesReleased

router = APIRouter(prefix="/api", tags=["scan"])


@router.get(
    "/sessions/{session_id}/grading-workspace",
    response_model=ScanGradingWorkspaceResponse,
)
def get_grading_workspace(
    session_id: int,
    db: GradingRepositoryAccess = Depends(get_grading_db),
    workspace: ScanGradingWorkspace = Depends(get_scan_grading_workspace),
) -> ScanGradingWorkspaceResponse:
    _require_session(db.sessions, session_id)
    return ScanGradingWorkspaceResponse.model_validate(workspace.get_workspace(session_id))


@router.get(
    "/sessions/{session_id}/scan/student-options",
    response_model=ScanStudentMatchOptionsResponse,
)
def get_scan_student_options(
    session_id: int,
    db: GradingRepositoryAccess = Depends(get_grading_db),
) -> ScanStudentMatchOptionsResponse:
    _require_session(db.sessions, session_id)
    return ScanStudentMatchOptionsResponse.model_validate(
        {
            "items": [
                {
                    "id": int(student["id"]),
                    "student_code": str(student.get("student_code") or ""),
                    "name": str(student.get("name") or ""),
                    "class_name": student.get("class_name"),
                    "pinyin_initials": _student_name_initials(student.get("name")),
                    "pinyin_full": _student_name_pinyin(student.get("name")),
                }
                for student in db.students.list_students()
            ]
        }
    )


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
    replacement: bool = False,
    append: bool = False,
    db: GradingRepositoryAccess = Depends(get_grading_db),
    workspace: ScanGradingWorkspace = Depends(get_scan_grading_workspace),
) -> ScanUploadResponse:
    _require_session(db.sessions, session_id)
    if replacement and append:
        raise ApiError(422, "scan_upload_mode_conflict", "Conflicting upload modes")
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
                replacement=replacement,
                append=append,
            )
    except ActiveScanAnalysisError as exc:
        raise ApiError(
            409,
            "scan_analysis_still_active",
            "预检任务仍在运行，结束后才能追加答卷文件。",
        ) from exc
    except FrozenUploadBatchError as exc:
        raise ApiError(409, "scan_upload_batch_frozen", "Scan upload batch is frozen") from exc
    except ScanUploadTooLargeError as exc:
        raise ApiError(413, "scan_upload_too_large", "Scan upload is too large") from exc
    except InvalidScanUploadError as exc:
        raise ApiError(415, "invalid_scan_upload", "Scan upload type or metadata is invalid") from exc
    except ScanGradingWorkspaceError as exc:
        if append and "frozen" in str(exc):
            raise ApiError(
                409,
                "scan_append_requires_frozen_batch",
                "只有已冻结的答卷批次才能追加文件。",
            ) from exc
        if append and "active" in str(exc):
            raise ApiError(
                409,
                "scan_append_blocked",
                "当前有预检或批改任务未结束，结束后才能追加答卷文件。",
            ) from exc
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
    replacement: bool = False,
    append: bool = False,
    db: GradingRepositoryAccess = Depends(get_grading_db),
    workspace: ScanGradingWorkspace = Depends(get_scan_grading_workspace),
) -> ScanUploadBatchResponse:
    _require_session(db.sessions, session_id)
    if not append:
        return _mutate_draft_uploads(
            lambda: workspace.remove_upload(
                session_id,
                upload_id,
                expected_revision=expected_revision,
                replacement=replacement,
            )
        )
    try:
        batch = workspace.remove_upload(
            session_id,
            upload_id,
            expected_revision=expected_revision,
            append=True,
        )
    except UploadBatchRevisionError as exc:
        raise ApiError(409, "scan_upload_revision_conflict", "Scan upload batch changed") from exc
    except FrozenUploadBatchError as exc:
        raise ApiError(
            409,
            "scan_append_file_not_removable",
            "冻结批次中只能移除本次新增的文件。",
        ) from exc
    except ActiveScanAnalysisError as exc:
        raise ApiError(
            409,
            "scan_analysis_still_active",
            "预检任务仍在运行，结束后才能移除新增文件。",
        ) from exc
    except ScanGradingWorkspaceError as exc:
        raise ApiError(
            409,
            "scan_append_blocked",
            "当前答卷状态不能移除新增文件。",
        ) from exc
    return ScanUploadBatchResponse.model_validate(batch)


@router.delete(
    "/sessions/{session_id}/scan-uploads",
    response_model=ScanUploadBatchResponse,
)
def clear_session_scan_uploads(
    session_id: int,
    expected_revision: int,
    replacement: bool = False,
    db: GradingRepositoryAccess = Depends(get_grading_db),
    workspace: ScanGradingWorkspace = Depends(get_scan_grading_workspace),
) -> ScanUploadBatchResponse:
    _require_session(db.sessions, session_id)
    return _mutate_draft_uploads(
        lambda: workspace.clear_uploads(
            session_id,
            expected_revision=expected_revision,
            replacement=replacement,
        )
    )


@router.post(
    "/sessions/{session_id}/scan-uploads/freeze",
    response_model=ScanUploadBatchResponse,
)
def freeze_session_scan_uploads(
    session_id: int,
    request: ScanUploadFreezeRequest,
    db: GradingRepositoryAccess = Depends(get_grading_db),
    workspace: ScanGradingWorkspace = Depends(get_scan_grading_workspace),
) -> ScanUploadBatchResponse:
    _require_session(db.sessions, session_id)
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
    db: GradingRepositoryAccess = Depends(get_grading_db),
    workspace: ScanGradingWorkspace = Depends(get_scan_grading_workspace),
) -> ScanUploadBatchResponse:
    _require_session(db.sessions, session_id)
    try:
        batch = workspace.start_new_upload_batch(session_id)
    except ActiveScanAnalysisError as exc:
        raise ApiError(
            409,
            "scan_analysis_still_active",
            "Active scan analysis must finish before starting a new batch",
        ) from exc
    except ScanGradingWorkspaceError as exc:
        raise ApiError(409, "grading_run_still_active", "Active grading run must be resolved first") from exc
    return ScanUploadBatchResponse.model_validate(batch)


@router.post(
    "/sessions/{session_id}/scan-uploads/replacement",
    response_model=ScanUploadBatchResponse,
)
def begin_session_scan_replacement(
    session_id: int,
    db: GradingRepositoryAccess = Depends(get_grading_db),
    workspace: ScanGradingWorkspace = Depends(get_scan_grading_workspace),
) -> ScanUploadBatchResponse:
    _require_session(db.sessions, session_id)
    try:
        batch = workspace.begin_replacement_upload(session_id)
    except ActiveScanAnalysisError as exc:
        raise ApiError(409, "scan_analysis_still_active", "Scan analysis is still active") from exc
    except SessionDerivedTrainingDataExists as exc:
        raise ApiError(
            409,
            "scan_replacement_training_snapshot_exists",
            "A saved training task still references this exam",
        ) from exc
    except SessionDeletionActiveWork as exc:
        raise ApiError(409, "scan_replacement_active_work", "Exam work is still active") from exc
    except ScanGradingWorkspaceError as exc:
        raise ApiError(409, "grading_run_still_active", "Grading run is still active") from exc
    return ScanUploadBatchResponse.model_validate(batch)


@router.post(
    "/sessions/{session_id}/scan-uploads/replacement/cancel",
)
def cancel_session_scan_replacement(
    session_id: int,
    db: GradingRepositoryAccess = Depends(get_grading_db),
    workspace: ScanGradingWorkspace = Depends(get_scan_grading_workspace),
) -> dict[str, bool]:
    _require_session(db.sessions, session_id)
    workspace.cancel_replacement_upload(session_id)
    return {"cancelled": True}


@router.post(
    "/sessions/{session_id}/scan-uploads/replacement/commit",
    response_model=ScanUploadBatchResponse,
)
def commit_session_scan_replacement(
    session_id: int,
    request: ScanUploadFreezeRequest,
    db: GradingRepositoryAccess = Depends(get_grading_db),
    workspace: ScanGradingWorkspace = Depends(get_scan_grading_workspace),
) -> ScanUploadBatchResponse:
    _require_session(db.sessions, session_id)
    try:
        batch = workspace.commit_replacement_upload(
            session_id,
            expected_revision=request.expected_revision,
        )
    except UploadBatchRevisionError as exc:
        raise ApiError(409, "scan_upload_revision_conflict", "Replacement batch changed") from exc
    except ActiveScanAnalysisError as exc:
        raise ApiError(409, "scan_analysis_still_active", "Scan analysis is still active") from exc
    except SessionDerivedTrainingDataExists as exc:
        raise ApiError(
            409,
            "scan_replacement_training_snapshot_exists",
            "A saved training task still references this exam",
        ) from exc
    except SessionDeletionActiveWork as exc:
        raise ApiError(409, "scan_replacement_active_work", "Exam work is still active") from exc
    except ScanReplacementCleanupIncompleteError as exc:
        raise ApiError(
            409,
            "scan_replacement_cleanup_pending",
            "最新答卷已经替换，但部分旧文件尚未清理；"
            "请关闭正在查看的答卷或报告后刷新页面重试。",
        ) from exc
    except ScanGradingWorkspaceError as exc:
        raise ApiError(409, "scan_replacement_rejected", "Scan replacement was rejected") from exc
    return ScanUploadBatchResponse.model_validate(batch)


@router.get(
    "/sessions/{session_id}/scan/preflight",
    response_model=ScanPreflightResponse,
)
def get_session_scan_preflight(
    session_id: int,
    db: GradingRepositoryAccess = Depends(get_grading_db),
    workspace: ScanGradingWorkspace = Depends(get_scan_grading_workspace),
) -> ScanPreflightResponse:
    _require_session(db.sessions, session_id)
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
    db: GradingRepositoryAccess = Depends(get_grading_db),
    workspace: ScanGradingWorkspace = Depends(get_scan_grading_workspace),
) -> ScanDecisionResponse:
    _require_session(db.sessions, session_id)
    valid_student_ids = {
        int(student["id"])
        for student in db.students.list_students()
        if student.get("id") is not None
    }
    try:
        result = workspace.save_decisions(
            session_id,
            expected_revision=request.expected_revision,
            valid_student_ids=valid_student_ids,
            decisions=[item.model_dump() for item in request.decisions],
            allow_partial_matches=request.allow_partial_matches,
        )
    except UploadBatchRevisionError as exc:
        raise ApiError(409, "scan_decision_revision_conflict", "Scan decisions changed") from exc
    except ScanMatchConflictError as exc:
        raise ApiError(422, "scan_match_conflict", str(exc), {"conflicts": exc.conflicts}) from exc
    except ScanGradingWorkspaceError as exc:
        raise ApiError(422, "scan_decision_invalid", "Scan decision is invalid") from exc
    return ScanDecisionResponse.model_validate(result)


@router.get("/sessions/{session_id}/scan/preflight/media/{media_ref}")
def get_session_scan_preflight_media(
    session_id: int,
    media_ref: str,
    db: GradingRepositoryAccess = Depends(get_grading_db),
    workspace: ScanGradingWorkspace = Depends(get_scan_grading_workspace),
) -> FileResponse:
    _require_session(db.sessions, session_id)
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
    db: GradingRepositoryAccess = Depends(get_grading_db),
    workspace: ScanGradingWorkspace = Depends(get_scan_grading_workspace),
    template_service: TemplateUploadService = Depends(get_template_upload_service),
) -> JobResponse:
    _require_session(db.sessions, session_id)
    request = request or ScanAnalyzeRequest()
    try:
        template = template_service.load_current(db=db, session_id=session_id)
    except FileNotFoundError:
        raise ApiError(
            409,
            "scan_template_not_found",
            "Upload a sample paper before running scan preflight",
        ) from None
    except TemplateUploadError:
        raise ApiError(
            409,
            "scan_template_unavailable",
            "The current sample paper is unavailable",
        ) from None
    if not template.is_confirmed or template.regions_snapshot_pending:
        raise ApiError(
            409,
            "scan_template_not_confirmed",
            "Confirm the sample paper regions before running scan preflight",
        )
    first_page_role = template.first_page_role
    front_page_parity = "odd" if first_page_role == "front" else "even"
    if (
        request.front_page_parity is not None
        and request.front_page_parity != front_page_parity
    ):
        raise ApiError(
            409,
            "scan_template_page_assignment_changed",
            "The sample paper page assignment changed; start preflight again",
        )
    try:
        config_revision = load_editor_config(db, session_id).revision
    except (KeyError, OSError, TypeError, ValueError):
        raise ApiError(
            409,
            "scan_grading_config_not_ready",
            "Save a complete grading rubric before running scan preflight",
        ) from None
    payload: dict[str, object] = {
        "session_id": int(session_id),
        "enhance_images": request.enhance_images,
        "front_page_parity": front_page_parity,
        "template_first_page_role": first_page_role,
        "template_id": template.template_id,
        "template_fingerprint": template.template_fingerprint,
        "config_revision": config_revision,
    }
    if request.ocr_workers is not None:
        payload["ocr_workers"] = request.ocr_workers
    try:
        job = workspace.submit_scan_analysis(session_id, payload)
    except ScanSourcesReleased as exc:
        raise ApiError(409, "scan_sources_released", str(exc)) from exc
    except ScanGradingWorkspaceError as exc:
        raise ApiError(
            409,
            "scan_upload_batch_not_frozen",
            "Freeze the scan upload batch before preflight",
        ) from exc
    except ActiveJobExistsError as exc:
        raise ApiError(
            409,
            "scan_analysis_already_active",
            "A scan analysis job is already active for this session",
        ) from exc
    except UnsupportedJobTypeError as exc:
        raise ApiError(
            404,
            "job_type_not_supported",
            "Job type is not supported",
            {"job_type": "scan_analysis"},
        ) from exc
    return _job_response(job)
