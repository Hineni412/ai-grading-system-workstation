from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse

from answer_region_commit_service import AnswerRegionCommitService, AnswerRegionCommitResult
from answer_region_draft_service import (
    AnswerRegionDraftService,
    DraftLoadResult,
    DraftRevisionConflictError,
    DraftTemplateMismatchError,
)
from answer_region_models import load_question_binding_catalog, normalize_regions, validate_regions
from backend.api.app import ApiError
from backend.config_workspace.publish import load_editor_config
from backend.api.dependencies import (
    get_grading_db,
    get_template_upload_service,
    get_templates_dir,
)
from backend.api.routers.sessions import _require_session, _template_response
from backend.api.schemas.sessions import SessionTemplateResponse
from backend.api.schemas.templates import (
    RegionCommitRequest,
    RegionCommitResponse,
    RegionDraftRequest,
    RegionDraftResponse,
    RegionWorkspaceResponse,
    RegionIssueResponse,
    RegionReadinessResponse,
    RegionSnapshotRetryRequest,
    TemplateUpdateRequest,
    TemplateUploadSubmissionResponse,
    TemplateUploadResponse,
)
from db_manager import DBManager
from path_manager import resolve_stored_file_path
from template_upload_service import (
    TemplateUploadError,
    TemplateUploadInProgressError,
    TemplateUploadService,
    TemplateUploadSubmissionConflictError,
    TemplateUploadTooLargeError,
)


router = APIRouter(prefix="/api", tags=["templates"])
LOGGER = logging.getLogger("ai_grading.api.templates")


def _scoring_configured(db: DBManager, session_id: int) -> bool:
    try:
        return bool(load_editor_config(db, int(session_id)).configured)
    except (KeyError, OSError, ValueError):
        return False


def _region_lock_timeout_error(session_id: int) -> ApiError:
    return ApiError(
        503,
        "answer_region_lock_timeout",
        "Answer region work is temporarily busy; retry shortly",
        {"session_id": int(session_id)},
        headers={"Retry-After": "1"},
    )


def _finish_template_submission_best_effort(
    service: TemplateUploadService,
    *,
    session_id: int,
    request_token: str,
    succeeded: bool,
    template: dict[str, object] | None = None,
) -> None:
    try:
        service.finish_submission(
            session_id=session_id,
            request_token=request_token,
            succeeded=succeeded,
            template=template,
        )
    except Exception:
        # Activation and its manifest are authoritative. A receipt failure must
        # not rewrite an already activated template as a failed upload.
        LOGGER.warning(
            "template_upload_submission_receipt_failed session_id=%s succeeded=%s",
            int(session_id),
            succeeded,
        )


def _upload_response(result: Any) -> TemplateUploadResponse:
    return TemplateUploadResponse(
        session_id=result.session_id,
        template_id=result.template_id,
        template_fingerprint=result.template_fingerprint,
        first_page_role=result.first_page_role,
        pages={
            page: {
                "url": f"/api/sessions/{result.session_id}/template/pages/{page}",
                "width": getattr(result, page).width,
                "height": getattr(result, page).height,
            }
            for page in ("front", "back")
        },
        is_confirmed=result.is_confirmed,
        regions_snapshot_pending=result.regions_snapshot_pending,
    )


def _public_regions(regions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = (
        "region_uuid",
        "page",
        "region_order",
        "x",
        "y",
        "w",
        "h",
        "mapped_question_id",
        "mapping_status",
        "is_confirmed",
        "multi_region_confirmed",
    )
    return [{key: region.get(key) for key in keys} for region in normalize_regions(regions)]


@router.post(
    "/sessions/{session_id}/template",
    response_model=TemplateUploadResponse,
    status_code=201,
    openapi_extra={
        "parameters": [
            {
                "name": "X-Client-Request-Token",
                "in": "header",
                "required": True,
                "schema": {"type": "string", "pattern": "^[0-9a-fA-F]{32}$"},
            },
            {
                "name": "X-Content-SHA256",
                "in": "header",
                "required": True,
                "schema": {"type": "string", "pattern": "^[0-9a-fA-F]{64}$"},
            },
            {
                "name": "X-Upload-Filename",
                "in": "header",
                "required": True,
                "schema": {"type": "string", "minLength": 5},
            },
        ],
        "requestBody": {
            "required": True,
            "content": {
                "application/pdf": {
                    "schema": {"type": "string", "format": "binary"}
                }
            },
        },
    },
)
async def upload_session_template(
    session_id: int,
    request: Request,
    first_page_role: Literal["front", "back"],
    db: DBManager = Depends(get_grading_db),
    service: TemplateUploadService = Depends(get_template_upload_service),
) -> TemplateUploadResponse:
    _require_session(db, session_id)
    if not _scoring_configured(db, session_id):
        raise ApiError(
            409,
            "scoring_config_required",
            "Scoring configuration must be saved before uploading a template",
        )
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/pdf":
        raise ApiError(415, "template_upload_type_invalid", "Template upload must be a PDF")
    filename = str(request.headers.get("x-upload-filename") or "").strip().lower()
    if not filename.endswith(".pdf"):
        raise ApiError(422, "template_upload_invalid", "Template upload is invalid")
    raw_length = request.headers.get("content-length")
    try:
        content_length = int(raw_length) if raw_length is not None else None
    except ValueError:
        raise ApiError(422, "template_upload_invalid", "Template upload is invalid") from None
    if content_length is not None and content_length > service.max_upload_bytes:
        raise ApiError(413, "template_upload_too_large", "Template upload is too large")
    request_token = str(request.headers.get("x-client-request-token") or "").strip()
    content_sha256 = str(request.headers.get("x-content-sha256") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", content_sha256):
        raise ApiError(422, "template_upload_invalid", "Template upload is invalid")
    try:
        submission_state = service.begin_submission(
            session_id=session_id,
            request_token=request_token,
            filename=filename,
            content_length=content_length,
            first_page_role=first_page_role,
            content_sha256=content_sha256,
            db=db,
        )
    except TemplateUploadInProgressError:
        raise ApiError(
            409,
            "template_upload_in_progress",
            "Another template upload is already active for this session",
        ) from None
    except TemplateUploadSubmissionConflictError:
        raise ApiError(
            409,
            "template_upload_token_conflict",
            "Template upload token was used for another request",
        ) from None
    except TemplateUploadError:
        raise ApiError(422, "template_upload_invalid", "Template upload is invalid") from None
    if submission_state != "started":
        if submission_state == "abandoned":
            raise ApiError(
                409,
                "template_upload_abandoned",
                "Template upload request was abandoned",
            )
        raise ApiError(
            409,
            "template_upload_already_submitted",
            "Template upload was already submitted; query its result",
        )
    response: TemplateUploadResponse | None = None
    try:
        chunks: list[bytes] = []
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > service.max_upload_bytes:
                raise TemplateUploadTooLargeError("template upload is too large")
            chunks.append(chunk)
        pdf_bytes = b"".join(chunks)
        if hashlib.sha256(pdf_bytes).hexdigest() != content_sha256:
            raise TemplateUploadError("template upload content digest does not match")
        result = service.upload(
            db=db,
            session_id=session_id,
            pdf_bytes=pdf_bytes,
            first_page_role=first_page_role,
            request_token=request_token,
        )
        response = _upload_response(result)
    except TemplateUploadTooLargeError:
        raise ApiError(413, "template_upload_too_large", "Template upload is too large") from None
    except TemplateUploadError:
        raise ApiError(422, "template_upload_invalid", "Template upload is invalid") from None
    except Exception:
        raise ApiError(500, "template_upload_failed", "Template upload failed") from None
    finally:
        _finish_template_submission_best_effort(
            service,
            session_id=session_id,
            request_token=request_token,
            succeeded=response is not None,
            template=response.model_dump(mode="json") if response is not None else None,
        )
    if response is None:
        raise RuntimeError("template upload response is missing")
    return response


@router.get(
    "/sessions/{session_id}/regions/readiness",
    response_model=RegionReadinessResponse,
)
def get_region_readiness(
    session_id: int,
    db: DBManager = Depends(get_grading_db),
) -> RegionReadinessResponse:
    _require_session(db, session_id)
    return RegionReadinessResponse(
        session_id=int(session_id),
        scoring_configured=_scoring_configured(db, session_id),
        template_present=db.get_session_template(int(session_id)) is not None,
        template_ready=db.is_template_ready(int(session_id)),
    )


@router.get(
    "/sessions/{session_id}/template/submissions/{request_token}",
    response_model=TemplateUploadSubmissionResponse,
)
def get_template_upload_submission(
    session_id: int,
    request_token: str,
    db: DBManager = Depends(get_grading_db),
    service: TemplateUploadService = Depends(get_template_upload_service),
) -> dict[str, object]:
    _require_session(db, session_id)
    try:
        return service.submission_public(
            session_id=session_id, request_token=request_token, db=db
        )
    except FileNotFoundError:
        raise ApiError(
            404, "template_upload_not_found", "Template upload submission not found"
        ) from None
    except TemplateUploadError:
        raise ApiError(422, "template_upload_invalid", "Template upload is invalid") from None


@router.post("/sessions/{session_id}/template/submissions/{request_token}/abandon")
def abandon_template_upload_submission(
    session_id: int,
    request_token: str,
    db: DBManager = Depends(get_grading_db),
    service: TemplateUploadService = Depends(get_template_upload_service),
) -> dict[str, str]:
    _require_session(db, session_id)
    try:
        service.abandon_submission(
            session_id=session_id, request_token=request_token, db=db
        )
    except TemplateUploadSubmissionConflictError:
        raise ApiError(
            409,
            "template_upload_already_submitted",
            "Template upload was already submitted",
        ) from None
    except TemplateUploadError:
        raise ApiError(422, "template_upload_invalid", "Template upload is invalid") from None
    return {"status": "abandoned"}


@router.get(
    "/sessions/{session_id}/regions/workspace",
    response_model=RegionWorkspaceResponse,
)
def get_region_workspace(
    session_id: int,
    db: DBManager = Depends(get_grading_db),
    templates_dir: Path = Depends(get_templates_dir),
    upload_service: TemplateUploadService = Depends(get_template_upload_service),
) -> RegionWorkspaceResponse:
    session = _require_session(db, session_id)
    try:
        template_result = upload_service.load_current(db=db, session_id=session_id)
    except FileNotFoundError:
        raise ApiError(404, "template_not_found", "Session template not found") from None
    except TemplateUploadError:
        raise ApiError(404, "template_file_not_found", "Template file not found") from None
    session_dir = _session_dir(templates_dir, session_id)
    draft_service = AnswerRegionDraftService(session_dir)
    draft_result = draft_service.load(
        expected_template_fingerprint=template_result.template_fingerprint
    )
    formal_regions = _public_regions(db.list_answer_regions(int(session_id)))
    draft_payload = draft_result.draft if draft_result.status == "compatible" else None
    draft_regions = _public_regions(
        list(draft_payload.get("regions", [])) if isinstance(draft_payload, dict) else []
    )
    revision = (
        int(draft_payload.get("revision", 0)) if isinstance(draft_payload, dict) else 0
    )
    rubric_path = resolve_stored_file_path(
        session.get("rubric_path"),
        search_roots=[session_dir, templates_dir, Path(db.db_path).parent],
    )
    catalog = load_question_binding_catalog(rubric_path)
    active_regions = draft_regions if draft_result.status == "compatible" else formal_regions
    validation = validate_regions(
        active_regions,
        image_sizes={
            "front": (template_result.front.width, template_result.front.height),
            "back": (template_result.back.width, template_result.back.height),
        },
        template_matches=draft_result.status != "incompatible",
    )
    return RegionWorkspaceResponse(
        session_id=int(session_id),
        template=_upload_response(template_result),
        formal_regions=formal_regions,
        draft={
            "status": draft_result.status,
            "revision": revision,
            "regions": draft_regions,
        },
        automatic_candidates=list(catalog.automatic_candidates),
        manual_question_options=[
            {"value": option.value, "label": option.label}
            for option in catalog.manual_options
        ],
        issues=[
            {
                "code": issue.code,
                "message": issue.message,
                "region_uuid": issue.region_uuid,
                "question_id": issue.question_id,
            }
            for issue in validation.issues
        ],
        template_ready=db.is_template_ready(int(session_id)),
    )


@router.get("/sessions/{session_id}/template/pages/{page}")
def get_session_template_page(
    session_id: int,
    page: Literal["front", "back"],
    db: DBManager = Depends(get_grading_db),
    templates_dir: Path = Depends(get_templates_dir),
) -> FileResponse:
    template = _require_template(db, session_id)
    front_path, back_path = _template_paths(
        template, _session_dir(templates_dir, session_id)
    )
    path = front_path if page == "front" else back_path
    return FileResponse(path, media_type="image/jpeg", filename=f"template-{page}.jpg")


def _session_dir(templates_dir: Path, session_id: int) -> Path:
    path = Path(templates_dir) / f"session_{int(session_id)}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _require_template(db: DBManager, session_id: int) -> dict[str, Any]:
    _require_session(db, session_id)
    template = db.get_session_template(int(session_id))
    if template is None:
        raise ApiError(
            404,
            "template_not_found",
            "Session template not found",
            {"session_id": int(session_id)},
        )
    return template


def _template_paths(template: dict[str, Any], session_dir: Path) -> tuple[Path, Path]:
    paths = []
    for field_name in ("front_template_path", "back_template_path"):
        raw_path = str(template.get(field_name) or "").strip()
        path = resolve_stored_file_path(
            raw_path,
            search_roots=[session_dir, session_dir.parent],
        )
        if not path.is_file():
            raise ApiError(
                404,
                "template_file_not_found",
                "Template file not found",
                {"field": field_name},
            )
        paths.append(path)
    return paths[0], paths[1]


def _draft_service(templates_dir: Path, session_id: int) -> AnswerRegionDraftService:
    return AnswerRegionDraftService(_session_dir(templates_dir, session_id))


def _template_fingerprint(
    template: dict[str, Any],
    draft_service: AnswerRegionDraftService,
) -> str:
    front_path, back_path = _template_paths(
        template,
        Path(draft_service.draft_path).parent,
    )
    return draft_service.compute_template_fingerprint(front_path, back_path)


def _draft_response(
    *,
    session_id: int,
    template: dict[str, Any],
    draft_service: AnswerRegionDraftService,
    load_result: DraftLoadResult,
    template_fingerprint: str,
) -> RegionDraftResponse:
    public_draft = None
    if load_result.status == "compatible" and load_result.draft is not None:
        public_draft = {
            "revision": int(load_result.draft.get("revision", 0)),
            "regions": _public_regions(list(load_result.draft.get("regions", []))),
        }
    return RegionDraftResponse(
        status=load_result.status,
        session_id=int(session_id),
        template_id=int(template["id"]),
        template_fingerprint=template_fingerprint,
        draft=public_draft,
    )


def _commit_response(result: AnswerRegionCommitResult, region_count: int) -> RegionCommitResponse:
    return RegionCommitResponse(
        committed=result.committed,
        snapshot_pending=result.snapshot_pending,
        error=result.error,
        issues=[
            RegionIssueResponse(
                code=issue.code,
                message=issue.message,
                region_uuid=issue.region_uuid,
                question_id=issue.question_id,
            )
            for issue in result.validation.issues
        ],
        region_count=region_count,
    )


@router.put("/sessions/{session_id}/template", response_model=SessionTemplateResponse)
def update_session_template(
    session_id: int,
    request: TemplateUpdateRequest,
    db: DBManager = Depends(get_grading_db),
) -> SessionTemplateResponse:
    _require_session(db, session_id)
    db.upsert_session_template(
        int(session_id),
        request.front_template_path,
        request.back_template_path,
    )
    if (
        request.ai_analysis_path is not None
        or request.template_config_path is not None
        or request.regions_path is not None
    ):
        db.update_session_template_analysis(
            int(session_id),
            ai_analysis_path=request.ai_analysis_path,
            template_config_path=request.template_config_path,
            regions_path=request.regions_path,
        )
    return _template_response(_require_template(db, session_id))


@router.get("/sessions/{session_id}/regions/draft", response_model=RegionDraftResponse)
def get_answer_region_draft(
    session_id: int,
    db: DBManager = Depends(get_grading_db),
    templates_dir: Path = Depends(get_templates_dir),
) -> RegionDraftResponse:
    template = _require_template(db, session_id)
    draft_service = _draft_service(templates_dir, session_id)
    fingerprint = _template_fingerprint(template, draft_service)
    load_result = draft_service.load(expected_template_fingerprint=fingerprint)
    return _draft_response(
        session_id=session_id,
        template=template,
        draft_service=draft_service,
        load_result=load_result,
        template_fingerprint=fingerprint,
    )


@router.put("/sessions/{session_id}/regions/draft", response_model=RegionDraftResponse)
def save_answer_region_draft(
    session_id: int,
    request: RegionDraftRequest,
    db: DBManager = Depends(get_grading_db),
    templates_dir: Path = Depends(get_templates_dir),
) -> RegionDraftResponse:
    template = _require_template(db, session_id)
    draft_service = _draft_service(templates_dir, session_id)
    fingerprint = _template_fingerprint(template, draft_service)
    if (
        request.expected_template_fingerprint is not None
        and request.expected_template_fingerprint != fingerprint
    ):
        raise ApiError(
            409,
            "region_draft_template_changed",
            "Session template changed before the draft was saved",
        )
    try:
        if request.expected_revision is None:
            draft_service.save(
                session_id=int(session_id),
                template_fingerprint=fingerprint,
                revision=request.revision,
                regions=request.regions,
            )
        else:
            draft_service.save(
                session_id=int(session_id),
                template_fingerprint=fingerprint,
                expected_revision=request.expected_revision,
                revision=request.revision,
                regions=request.regions,
            )
    except DraftRevisionConflictError:
        raise ApiError(
            409,
            "region_draft_revision_conflict",
            "Answer region draft changed before it was saved",
        ) from None
    except DraftTemplateMismatchError:
        raise ApiError(
            409,
            "region_draft_template_changed",
            "Session template changed before the draft was saved",
        ) from None
    load_result = draft_service.load(expected_template_fingerprint=fingerprint)
    return _draft_response(
        session_id=session_id,
        template=template,
        draft_service=draft_service,
        load_result=load_result,
        template_fingerprint=fingerprint,
    )


@router.delete("/sessions/{session_id}/regions/draft")
def discard_answer_region_draft(
    session_id: int,
    db: DBManager = Depends(get_grading_db),
    templates_dir: Path = Depends(get_templates_dir),
) -> dict[str, str]:
    _require_template(db, session_id)
    _draft_service(templates_dir, session_id).discard()
    return {"status": "discarded"}


@router.post("/sessions/{session_id}/regions/commit", response_model=RegionCommitResponse)
def commit_answer_regions(
    session_id: int,
    request: RegionCommitRequest,
    db: DBManager = Depends(get_grading_db),
    templates_dir: Path = Depends(get_templates_dir),
) -> RegionCommitResponse:
    template = _require_template(db, session_id)
    draft_service = _draft_service(templates_dir, session_id)
    _template_fingerprint(template, draft_service)
    service = AnswerRegionCommitService(
        db,
        Path(draft_service.draft_path).parent,
        draft_service,
    )
    try:
        result = service.commit(
            session_id=int(session_id),
            template_id=int(template["id"]),
            regions=request.regions,
            image_sizes=request.image_sizes,
            template_matches=request.template_matches,
            expected_template_fingerprint=request.expected_template_fingerprint,
        )
    except TimeoutError:
        raise _region_lock_timeout_error(session_id) from None
    region_count = len(db.list_answer_regions(int(session_id))) if result.committed else 0
    return _commit_response(result, region_count)


@router.post(
    "/sessions/{session_id}/regions/snapshot/retry",
    response_model=RegionCommitResponse,
)
def retry_answer_region_snapshot(
    session_id: int,
    request: RegionSnapshotRetryRequest,
    db: DBManager = Depends(get_grading_db),
    templates_dir: Path = Depends(get_templates_dir),
) -> RegionCommitResponse:
    template = _require_template(db, session_id)
    draft_service = _draft_service(templates_dir, session_id)
    fingerprint = _template_fingerprint(template, draft_service)
    if fingerprint != request.expected_template_fingerprint:
        raise ApiError(
            409,
            "region_snapshot_template_changed",
            "Session template changed before the snapshot was retried",
        )
    service = AnswerRegionCommitService(
        db,
        Path(draft_service.draft_path).parent,
        draft_service,
    )
    try:
        result = service.retry_pending_snapshot(session_id=int(session_id))
    except TimeoutError:
        raise _region_lock_timeout_error(session_id) from None
    region_count = len(db.list_answer_regions(int(session_id))) if result.committed else 0
    return _commit_response(result, region_count)
