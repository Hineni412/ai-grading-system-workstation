from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from backend.api.app import ApiError, ErrorResponse
from backend.api.dependencies import (
    get_config_source_service,
    get_grading_db,
    get_job_manager,
    get_upload_config_dir,
)
from backend.api.routers.jobs import _job_response
from backend.api.routers.sessions import _require_session
from backend.api.schemas.config import (
    ConfigGenerationRequest,
    ConfigGenerationRetryRequest,
    ConfigSourceResponse,
    SessionConfigRequest,
    SessionConfigResponse,
)
from backend.config_workspace.sources import (
    ConfigAssetNotFoundError,
    ConfigSourceChangedError,
    ConfigSourceError,
    ConfigSourceInvalidError,
    ConfigSourceNotFoundError,
    ConfigSourceService,
    ConfigSourceTooLargeError,
    ConfigSourceTypeUnsupportedError,
    decode_upload_filename,
)
from backend.api.schemas.jobs import JobResponse
from backend.jobs.config_generation import (
    discard_config_generation_input,
    stage_config_generation_input,
)
from backend.jobs.manager import JobManager, UnsupportedJobTypeError
from backend.jobs.store import ConfigRetryAlreadySubmittedError
from backend.public_data import (
    contains_filesystem_reference,
    contains_path_key,
    contains_sensitive_key,
)
from db_manager import DBManager
from path_manager import resolve_stored_file_path
from session_manager import save_generated_config


router = APIRouter(prefix="/api", tags=["config"])

CONFIG_GENERATION_ERROR_RESPONSES = {
    404: {"model": ErrorResponse, "description": "Session not found"},
    503: {"model": ErrorResponse, "description": "Config generation unavailable"},
}

CONFIG_GENERATION_RETRY_ERROR_RESPONSES = {
    **CONFIG_GENERATION_ERROR_RESPONSES,
    409: {"model": ErrorResponse, "description": "Config generation retry conflict"},
}

CONFIG_SOURCE_ERROR_RESPONSES = {
    404: {"model": ErrorResponse, "description": "Config source not found"},
    409: {"model": ErrorResponse, "description": "Config source changed"},
    413: {"model": ErrorResponse, "description": "Config source too large"},
    415: {"model": ErrorResponse, "description": "Config source type unsupported"},
    422: {"model": ErrorResponse, "description": "Config source invalid"},
}


def _source_api_error(exc: ConfigSourceError) -> ApiError:
    if isinstance(exc, ConfigSourceTooLargeError):
        return ApiError(413, "config_source_too_large", "Config source is too large")
    if isinstance(exc, ConfigSourceTypeUnsupportedError):
        return ApiError(
            415,
            "config_source_type_unsupported",
            "Config source type is unsupported",
        )
    if isinstance(exc, ConfigSourceChangedError):
        return ApiError(409, "config_source_changed", "Config source has changed")
    if isinstance(exc, ConfigAssetNotFoundError):
        return ApiError(404, "config_asset_not_found", "Config asset not found")
    if isinstance(exc, ConfigSourceNotFoundError):
        return ApiError(404, "config_source_not_found", "Config source not found")
    return ApiError(422, "config_source_invalid", "Config source is invalid")


@router.post(
    "/sessions/{session_id}/config/sources",
    response_model=ConfigSourceResponse,
    status_code=201,
    responses=CONFIG_SOURCE_ERROR_RESPONSES,
)
async def upload_config_source(
    session_id: int,
    request: Request,
    db: DBManager = Depends(get_grading_db),
    source_service: ConfigSourceService = Depends(get_config_source_service),
) -> dict[str, Any]:
    _require_session(db, session_id)
    try:
        raw_length = request.headers.get("content-length")
        if raw_length is not None and int(raw_length) > source_service.max_upload_bytes:
            raise ConfigSourceTooLargeError()
        filename = decode_upload_filename(request.headers.get("x-upload-filename"))
        record = await source_service.stage_and_parse(
            session_id=session_id,
            filename=filename,
            chunks=request.stream(),
        )
    except (TypeError, ValueError):
        raise _source_api_error(ConfigSourceInvalidError()) from None
    except ConfigSourceError as exc:
        raise _source_api_error(exc) from None
    return record.public_snapshot()


@router.get(
    "/sessions/{session_id}/config/sources/{source_id}",
    response_model=ConfigSourceResponse,
    responses=CONFIG_SOURCE_ERROR_RESPONSES,
)
def get_config_source(
    session_id: int,
    source_id: str,
    db: DBManager = Depends(get_grading_db),
    source_service: ConfigSourceService = Depends(get_config_source_service),
) -> dict[str, Any]:
    _require_session(db, session_id)
    try:
        return source_service.load(
            session_id=session_id,
            source_id=source_id,
        ).public_snapshot()
    except ConfigSourceError as exc:
        raise _source_api_error(exc) from None


@router.get(
    "/sessions/{session_id}/config/sources/{source_id}/questions/{question_id}/assets/{asset_kind}",
    responses=CONFIG_SOURCE_ERROR_RESPONSES,
)
def get_config_source_asset(
    session_id: int,
    source_id: str,
    question_id: str,
    asset_kind: str,
    db: DBManager = Depends(get_grading_db),
    source_service: ConfigSourceService = Depends(get_config_source_service),
) -> Response:
    _require_session(db, session_id)
    if asset_kind not in {"question", "answer"}:
        raise ApiError(404, "config_asset_not_found", "Config asset not found")
    try:
        content, media_type = source_service.read_asset(
            session_id=session_id,
            source_id=source_id,
            question_id=question_id,
            asset_kind=asset_kind,
        )
    except ConfigSourceError as exc:
        raise _source_api_error(exc) from None
    return Response(
        content,
        media_type=media_type,
        headers={"Cache-Control": "private, no-store"},
    )


def _read_json_config(path_value: Any, *, field_name: str) -> dict[str, Any]:
    raw_path = str(path_value or "").strip()
    if not raw_path:
        raise ApiError(
            404,
            "config_file_not_found",
            "Config file not found",
            {"field": field_name},
        )
    path = resolve_stored_file_path(raw_path)
    if not path.exists():
        raise ApiError(
            404,
            "config_file_not_found",
            "Config file not found",
            {"field": field_name},
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ApiError(
            500,
            "stored_config_invalid",
            "Stored config file is not valid JSON",
            {"field": field_name},
        ) from exc
    if not isinstance(payload, dict):
        raise ApiError(
            500,
            "stored_config_invalid",
            "Stored config file must contain a JSON object",
            {"field": field_name},
        )
    return payload


def _config_response(session: dict[str, Any]) -> SessionConfigResponse:
    return SessionConfigResponse(
        session_id=int(session["id"]),
        rubric_path=str(session.get("rubric_path") or ""),
        answer_key_path=str(session.get("answer_key_path") or ""),
        template_config_path=session.get("template_config_path"),
        source_paper_path=session.get("source_paper_path"),
        source_paper_sha256=session.get("source_paper_sha256"),
        rubric=_read_json_config(session.get("rubric_path"), field_name="rubric_path"),
        answer_key=_read_json_config(session.get("answer_key_path"), field_name="answer_key_path"),
    )


@router.get("/sessions/{session_id}/config", response_model=SessionConfigResponse)
def get_session_config(
    session_id: int,
    db: DBManager = Depends(get_grading_db),
) -> SessionConfigResponse:
    return _config_response(_require_session(db, session_id))


@router.put("/sessions/{session_id}/config", response_model=SessionConfigResponse)
def save_session_config(
    session_id: int,
    request: SessionConfigRequest,
    db: DBManager = Depends(get_grading_db),
    upload_config_dir: Path = Depends(get_upload_config_dir),
) -> SessionConfigResponse:
    _require_session(db, session_id)
    payload = request.model_dump()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    try:
        rubric_path, answer_key_path = save_generated_config(
            upload_config_dir,
            payload,
            timestamp,
        )
    except ValueError as exc:
        raise ApiError(
            422,
            "invalid_config",
            "Config payload is invalid",
            {"session_id": int(session_id)},
        ) from exc
    db.update_grading_session_config(
        int(session_id),
        rubric_path=str(rubric_path),
        answer_key_path=str(answer_key_path),
    )
    return _config_response(_require_session(db, session_id))


def _require_active_session(db: DBManager, session_id: int) -> dict[str, Any]:
    session = _require_session(db, session_id)
    if bool(int(session.get("is_deleted") or 0)):
        raise ApiError(
            404,
            "session_not_found",
            "Session not found",
            {"session_id": int(session_id)},
        )
    return session


def _contains_embedded_image_reference(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key) in {"question_html", "answer_html", "analysis_html"}:
                text = str(item or "").casefold()
                if "<img" in text or "[[image:" in text:
                    return True
            if _contains_embedded_image_reference(item):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_contains_embedded_image_reference(item) for item in value)
    return False


def _submit_config_generation(
    manager: JobManager,
    payload: dict[str, object],
) -> JobResponse:
    try:
        return _job_response(manager.submit("config_generation", payload))
    except UnsupportedJobTypeError as exc:
        raise ApiError(
            503,
            "job_type_not_supported",
            "Config generation is temporarily unavailable",
            {"job_type": "config_generation"},
        ) from exc


@router.post(
    "/sessions/{session_id}/config/generate",
    response_model=JobResponse,
    status_code=202,
    responses=CONFIG_GENERATION_ERROR_RESPONSES,
)
def generate_session_config(
    session_id: int,
    request: ConfigGenerationRequest,
    db: DBManager = Depends(get_grading_db),
    manager: JobManager = Depends(get_job_manager),
    upload_config_dir: Path = Depends(get_upload_config_dir),
) -> JobResponse:
    session = _require_active_session(db, session_id)
    request_payload = request.model_dump()
    if (
        contains_sensitive_key(request_payload)
        or contains_path_key(request_payload)
        or contains_filesystem_reference(
            {
                "confirmed_blocks": request.confirmed_blocks,
                "document_text": request.document_text,
            }
        )
        or _contains_embedded_image_reference(request_payload)
    ):
        raise ApiError(
            422,
            "invalid_config_generation_request",
            "Config generation request contains forbidden fields",
        )
    input_id = stage_config_generation_input(
        upload_config_dir,
        session_id=int(session_id),
        expected_rubric_path=str(session.get("rubric_path") or ""),
        expected_answer_key_path=str(session.get("answer_key_path") or ""),
        **request_payload,
    )
    try:
        return _submit_config_generation(
            manager,
            {
                "session_id": int(session_id),
                "mode": "generate",
                "input_id": input_id,
            },
        )
    except Exception:
        discard_config_generation_input(upload_config_dir, input_id)
        raise


@router.post(
    "/sessions/{session_id}/config/generate/retry",
    response_model=JobResponse,
    status_code=202,
    responses=CONFIG_GENERATION_RETRY_ERROR_RESPONSES,
)
def retry_session_config_generation(
    session_id: int,
    request: ConfigGenerationRetryRequest,
    db: DBManager = Depends(get_grading_db),
    manager: JobManager = Depends(get_job_manager),
) -> JobResponse:
    _require_active_session(db, session_id)
    source = manager.get(request.source_job_id)
    if source is None:
        raise ApiError(
            404,
            "config_generation_job_not_found",
            "Config generation job not found",
            {"source_job_id": int(request.source_job_id)},
        )
    if (
        source.job_type != "config_generation"
        or source.status != "succeeded"
        or source.result.get("outcome") != "partial"
        or int(source.payload.get("session_id") or 0) != int(session_id)
    ):
        raise ApiError(
            409,
            "config_generation_retry_not_available",
            "Config generation job is not available for retry",
            {"source_job_id": int(request.source_job_id)},
        )
    failed_ids = [str(item) for item in source.result.get("failed_question_ids") or []]
    if request.retry_question_ids is not None:
        unknown = [item for item in request.retry_question_ids if item not in failed_ids]
        if unknown:
            raise ApiError(
                409,
                "config_generation_retry_not_available",
                "Requested questions are not currently failed",
                {"source_job_id": int(request.source_job_id)},
            )
    payload: dict[str, object] = {
        "session_id": int(session_id),
        "mode": "retry",
        "source_job_id": int(request.source_job_id),
        "input_id": str(source.payload.get("input_id") or ""),
    }
    if request.retry_question_ids is not None:
        payload["retry_question_ids"] = request.retry_question_ids
    try:
        return _job_response(manager.submit_config_retry(payload))
    except ConfigRetryAlreadySubmittedError as exc:
        raise ApiError(
            409,
            "config_generation_retry_not_available",
            "A retry has already been submitted for this config generation job",
            {"source_job_id": int(request.source_job_id)},
        ) from exc
    except UnsupportedJobTypeError as exc:
        raise ApiError(
            503,
            "job_type_not_supported",
            "Config generation is temporarily unavailable",
            {"job_type": "config_generation"},
        ) from exc
