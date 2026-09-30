from __future__ import annotations

import hashlib
import json
import logging
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from backend.api.app import ApiError, ErrorResponse
from backend.api.dependencies import (
    get_config_mapping_output_dir,
    get_config_source_service,
    get_data_root,
    get_grading_db,
    get_job_manager,
    get_question_bank_db_path,
    get_upload_config_dir,
)
from backend.api.routers.jobs import _job_response
from backend.api.routers.sessions import _require_session
from backend.api.schemas.config import (
    ConfigEditorResponse,
    ConfigEditorSaveRequest,
    ConfigEditorSaveResponse,
    ConfigGenerationQuestionStatesResponse,
    ConfigGenerationRetryRequest,
    ConfigSourceDuplicatesResponse,
    ConfigSourceGenerationRequest,
    ConfigSourceResponse,
    ConfigSourceSubmissionResponse,
    SessionConfigRequest,
    SessionConfigResponse,
)
from backend.api.schemas.jobs import JobResponse
from backend.config_generation.status_projection import project_question_states
from backend.config_workspace.editor import (
    ConfigEditorEdit,
    ConfigEditorValidationError,
    ManualPartInput,
    ManualQuestionPartInput,
    ManualStepInput,
    ReplaceQuestionStructureCommand,
    ReplaceScoringUnitsCommand,
    SplitScoringUnitCommand,
)
from backend.config_workspace.locks import session_config_lock
from backend.config_workspace.publish import (
    ConfigRevisionConflict,
    editor_response,
    load_editor_config,
    refresh_template_mapping_from_session,
    remove_published_config,
    save_editor_config_and_refresh_mapping,
    save_generated_config,
)
from backend.config_workspace.sources import (
    AmbiguousAssetDecision,
    ConfigAssetNotFoundError,
    ConfigSourceActivationBusyError,
    ConfigSourceChangedError,
    ConfigSourceError,
    ConfigSourceInvalidError,
    ConfigSourceNotFoundError,
    ConfigSourceParseError,
    ConfigSourceService,
    ConfigSourceSubmissionConflictError,
    ConfigSourceTooLargeError,
    ConfigSourceTypeUnsupportedError,
    QuestionDecision,
    decode_upload_filename,
)
from backend.jobs.config_generation import (
    discard_config_generation_input,
    load_config_generation_input,
    read_config_generation_draft,
    stage_config_source_generation_input,
)
from backend.jobs.manager import JobManager, UnsupportedJobTypeError
from backend.jobs.store import (
    ConfigRequestTokenConflictError,
    ConfigRetryAlreadySubmittedError,
    ConfigSessionBusyError,
    JobStore,
)
from backend.repositories.access import GradingRepositoryAccess
from path_manager import resolve_stored_file_path

LOGGER = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["config"])

CONFIG_GENERATION_ERROR_RESPONSES = {
    404: {"model": ErrorResponse, "description": "Session not found"},
    503: {"model": ErrorResponse, "description": "Config generation unavailable"},
}

CONFIG_GENERATION_RETRY_ERROR_RESPONSES = {
    **CONFIG_GENERATION_ERROR_RESPONSES,
    409: {"model": ErrorResponse, "description": "Config generation retry conflict"},
}

CONFIG_EDITOR_ERROR_RESPONSES = {
    404: {"model": ErrorResponse, "description": "Session not found"},
    409: {"model": ErrorResponse, "description": "Configuration revision conflict"},
    422: {"model": ErrorResponse, "description": "Invalid editor change"},
}

CONFIG_SOURCE_ERROR_RESPONSES = {
    404: {"model": ErrorResponse, "description": "Config source not found"},
    409: {"model": ErrorResponse, "description": "Config source changed"},
    413: {"model": ErrorResponse, "description": "Config source too large"},
    415: {"model": ErrorResponse, "description": "Config source type unsupported"},
    422: {"model": ErrorResponse, "description": "Config source invalid"},
}


def _source_api_error(exc: ConfigSourceError) -> ApiError:
    if isinstance(exc, ConfigSourceParseError):
        return ApiError(422, "config_source_invalid", str(exc))
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
    if isinstance(exc, ConfigSourceSubmissionConflictError):
        return ApiError(
            409,
            "config_source_submission_conflict",
            "Config source submission token was reused for a different upload",
        )
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
    db: GradingRepositoryAccess = Depends(get_grading_db),
    source_service: ConfigSourceService = Depends(get_config_source_service),
    manager: JobManager = Depends(get_job_manager),
) -> dict[str, Any]:
    _require_active_session(db, session_id)
    try:
        manager.store.assert_config_session_idle(session_id)
    except ConfigSessionBusyError as exc:
        raise _config_busy_error(session_id) from exc
    try:
        raw_length = request.headers.get("content-length")
        content_length = int(raw_length) if raw_length is not None else None
        if content_length is not None and content_length < 0:
            raise ValueError("negative content length")
        if (
            content_length is not None
            and content_length > source_service.max_upload_bytes
        ):
            raise ConfigSourceTooLargeError()
        filename = decode_upload_filename(request.headers.get("x-upload-filename"))
    except (TypeError, ValueError):
        raise _source_api_error(ConfigSourceInvalidError()) from None
    except ConfigSourceError as exc:
        raise _source_api_error(exc) from None

    request_token = str(request.headers.get("x-client-request-token") or "").strip()
    submission_started = False
    if request_token:
        try:
            submission_state = source_service.begin_submission(
                session_id=session_id,
                request_token=request_token,
                filename=filename,
                content_length=content_length,
            )
            if submission_state == "succeeded":
                raise ApiError(
                    409,
                    "config_source_upload_already_submitted",
                    "Config source upload was already submitted; query its result",
                )
            if submission_state == "replaced":
                raise ApiError(
                    409,
                    "config_source_upload_replaced",
                    "Config source upload was replaced by a newer source",
                )
            if submission_state in {"processing", "failed"}:
                raise ApiError(
                    409,
                    "config_source_upload_in_progress"
                    if submission_state == "processing"
                    else "config_source_upload_failed",
                    "Config source upload is still processing"
                    if submission_state == "processing"
                    else "Config source upload failed",
                )
        except ConfigSourceError as exc:
            raise _source_api_error(exc) from None
        submission_started = submission_state == "started"
    try:
        source_service.cleanup_inactive(
            session_id=session_id,
            referenced_source_ids=JobStore(db.db_path).referenced_config_source_ids(
                session_id
            ),
        )
        record = await source_service.stage_and_parse(
            session_id=session_id,
            filename=filename,
            chunks=request.stream(),
            source_id=request_token or None,
            activation_guard=lambda: _source_activation_guard(manager, session_id),
        )
        if request_token:
            try:
                source_service.finish_submission(
                    session_id=session_id,
                    request_token=request_token,
                    succeeded=True,
                )
            except BaseException:
                recovered = source_service.submission_public(
                    session_id=session_id,
                    request_token=request_token,
                )
                if recovered["status"] == "succeeded" and recovered["source"]:
                    return dict(recovered["source"])
                raise
        return record.public_snapshot()
    except BaseException as exc:
        if request_token and submission_started:
            try:
                source_service.finish_submission(
                    session_id=session_id,
                    request_token=request_token,
                    succeeded=False,
                )
            except BaseException:
                pass
        if isinstance(exc, (TypeError, ValueError)):
            raise _source_api_error(ConfigSourceInvalidError()) from None
        if isinstance(exc, ConfigSourceActivationBusyError):
            raise _config_busy_error(session_id) from exc
        if isinstance(exc, ConfigSourceError):
            raise _source_api_error(exc) from None
        raise


@router.get(
    "/sessions/{session_id}/config/sources/submissions/{request_token}",
    response_model=ConfigSourceSubmissionResponse,
    responses=CONFIG_SOURCE_ERROR_RESPONSES,
)
def get_config_source_submission(
    session_id: int,
    request_token: str,
    db: GradingRepositoryAccess = Depends(get_grading_db),
    source_service: ConfigSourceService = Depends(get_config_source_service),
) -> dict[str, Any]:
    _require_session(db.sessions, session_id)
    try:
        return source_service.submission_public(
            session_id=session_id,
            request_token=request_token,
        )
    except ConfigSourceError as exc:
        raise _source_api_error(exc) from None


@router.post(
    "/sessions/{session_id}/config/sources/submissions/{request_token}/abandon",
    responses={409: {"model": ErrorResponse, "description": "Submission already exists"}},
)
def abandon_config_source_submission(
    session_id: int,
    request_token: str,
    db: GradingRepositoryAccess = Depends(get_grading_db),
    source_service: ConfigSourceService = Depends(get_config_source_service),
) -> dict[str, str]:
    _require_active_session(db, session_id)
    try:
        source_service.abandon_submission(
            session_id=session_id,
            request_token=request_token,
        )
    except ConfigSourceError as exc:
        raise _source_api_error(exc) from None
    return {"status": "abandoned"}


@router.get(
    "/sessions/{session_id}/config/sources/active",
    response_model=ConfigSourceResponse,
    responses=CONFIG_SOURCE_ERROR_RESPONSES,
)
def get_active_config_source(
    session_id: int,
    db: GradingRepositoryAccess = Depends(get_grading_db),
    source_service: ConfigSourceService = Depends(get_config_source_service),
) -> dict[str, Any]:
    _require_session(db.sessions, session_id)
    try:
        return source_service.load_active_public(session_id=session_id)
    except ConfigSourceError as exc:
        raise _source_api_error(exc) from None


@router.get(
    "/sessions/{session_id}/config/sources/active/duplicates",
    response_model=ConfigSourceDuplicatesResponse,
    responses={
        **CONFIG_SOURCE_ERROR_RESPONSES,
        503: {"model": ErrorResponse, "description": "Duplicate check unavailable"},
    },
)
def get_active_config_source_duplicates(
    session_id: int,
    db: GradingRepositoryAccess = Depends(get_grading_db),
    source_service: ConfigSourceService = Depends(get_config_source_service),
    data_root: Path = Depends(get_data_root),
    question_bank_db_path: Path = Depends(get_question_bank_db_path),
) -> dict[str, Any]:
    from backend.config_workspace.duplicates import source_duplicate_preview

    session = _require_session(db.sessions, session_id)
    try:
        return source_duplicate_preview(
            service=source_service,
            session_id=int(session_id),
            session_name=str(session.get("name") or ""),
            question_bank_db_path=Path(question_bank_db_path),
            data_root=Path(data_root),
        )
    except ConfigSourceError as exc:
        raise _source_api_error(exc) from None
    except ValueError as exc:
        raise ApiError(422, "config_source_invalid", str(exc)) from None
    except Exception:
        LOGGER.exception("config source duplicate check failed")
        raise ApiError(
            503,
            "config_duplicates_unavailable",
            "题库查重暂不可用，不影响试卷配置，可以稍后刷新重试。",
        ) from None


@router.get(
    "/sessions/{session_id}/config/sources/{source_id}",
    response_model=ConfigSourceResponse,
    responses=CONFIG_SOURCE_ERROR_RESPONSES,
)
def get_config_source(
    session_id: int,
    source_id: str,
    db: GradingRepositoryAccess = Depends(get_grading_db),
    source_service: ConfigSourceService = Depends(get_config_source_service),
) -> dict[str, Any]:
    _require_session(db.sessions, session_id)
    try:
        return source_service.load_public(
            session_id=session_id,
            source_id=source_id,
        )
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
    db: GradingRepositoryAccess = Depends(get_grading_db),
    source_service: ConfigSourceService = Depends(get_config_source_service),
) -> Response:
    _require_session(db.sessions, session_id)
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


@router.get(
    "/sessions/{session_id}/config/sources/{source_id}/questions/{question_id}/assets/{asset_kind}/{asset_index}",
    responses=CONFIG_SOURCE_ERROR_RESPONSES,
)
def get_indexed_config_source_asset(
    session_id: int,
    source_id: str,
    question_id: str,
    asset_kind: str,
    asset_index: int,
    db: GradingRepositoryAccess = Depends(get_grading_db),
    source_service: ConfigSourceService = Depends(get_config_source_service),
) -> Response:
    _require_session(db.sessions, session_id)
    if asset_kind not in {"question", "answer"} or asset_index < 0:
        raise ApiError(404, "config_asset_not_found", "Config asset not found")
    try:
        content, media_type = source_service.read_asset(
            session_id=session_id,
            source_id=source_id,
            question_id=question_id,
            asset_kind=asset_kind,
            asset_index=asset_index,
        )
    except ConfigSourceError as exc:
        raise _source_api_error(exc) from None
    return Response(
        content,
        media_type=media_type,
        headers={"Cache-Control": "private, no-store"},
    )


@router.get(
    "/sessions/{session_id}/config/sources/{source_id}/ambiguous-assets/{candidate_id}",
    responses=CONFIG_SOURCE_ERROR_RESPONSES,
)
def get_config_source_ambiguous_asset(
    session_id: int,
    source_id: str,
    candidate_id: str,
    db: GradingRepositoryAccess = Depends(get_grading_db),
    source_service: ConfigSourceService = Depends(get_config_source_service),
) -> Response:
    _require_session(db.sessions, session_id)
    try:
        content, media_type = source_service.read_ambiguous_asset(
            session_id=session_id,
            source_id=source_id,
            candidate_id=candidate_id,
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
    db: GradingRepositoryAccess = Depends(get_grading_db),
) -> SessionConfigResponse:
    return _config_response(_require_session(db.sessions, session_id))


@router.put("/sessions/{session_id}/config", response_model=SessionConfigResponse)
def save_session_config(
    session_id: int,
    request: SessionConfigRequest,
    db: GradingRepositoryAccess = Depends(get_grading_db),
    manager: JobManager = Depends(get_job_manager),
    upload_config_dir: Path = Depends(get_upload_config_dir),
) -> SessionConfigResponse:
    _require_active_session(db, session_id)
    payload = request.model_dump()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    try:
        manager.store.assert_config_session_idle(session_id)
        with session_config_lock(upload_config_dir, session_id):
            rubric_path, answer_key_path = save_generated_config(
                upload_config_dir,
                payload,
                timestamp,
            )
            try:
                updated = manager.store.update_session_config_if_idle(
                    session_id,
                    rubric_path=str(rubric_path),
                    answer_key_path=str(answer_key_path),
                )
            except BaseException:
                remove_published_config(
                    upload_config_dir,
                    (Path(rubric_path), Path(answer_key_path)),
                )
                raise
            if not updated:
                remove_published_config(
                    upload_config_dir,
                    (Path(rubric_path), Path(answer_key_path)),
                )
                raise ApiError(404, "session_not_found", "Session not found")
    except ConfigSessionBusyError as exc:
        raise _config_busy_error(session_id) from exc
    except ValueError as exc:
        raise ApiError(
            422,
            "invalid_config",
            "Config payload is invalid",
            {"session_id": int(session_id)},
        ) from exc
    return _config_response(_require_session(db.sessions, session_id))


def _editor_commands(values: list[Any]) -> tuple[Any, ...]:
    commands: list[Any] = []
    for value in values:
        if value.kind == "split":
            commands.append(SplitScoringUnitCommand(**value.model_dump()))
        elif value.kind == "replace_parts":
            commands.append(
                ReplaceScoringUnitsCommand(
                    kind="replace_parts",
                    question_id=value.question_id,
                    parts=tuple(ManualPartInput(**part.model_dump()) for part in value.parts),
                )
            )
        else:
            commands.append(
                ReplaceQuestionStructureCommand(
                    kind="replace_question_structure",
                    question_id=value.question_id,
                    parts=tuple(
                        ManualQuestionPartInput(
                            part_id=part.part_id,
                            steps=tuple(
                                ManualStepInput(**step.model_dump())
                                for step in part.steps
                            ),
                        )
                        for part in value.parts
                    ),
                )
            )
    return tuple(commands)


def _editor_edits(values: list[Any]) -> tuple[ConfigEditorEdit, ...]:
    return tuple(
        ConfigEditorEdit(
            **{
                key: (
                    tuple(value)
                    if key in {
                        "accepted_answers",
                        "required_elements",
                        "deduction_rules",
                        "part_deduction_rules",
                    }
                    and value is not None
                    else value
                )
                for key, value in item.model_dump().items()
            }
        )
        for item in values
    )


def _editor_api_error(exc: Exception) -> ApiError:
    if isinstance(exc, ConfigRevisionConflict):
        return ApiError(409, "config_revision_conflict", "Configuration has changed")
    if isinstance(exc, ConfigEditorValidationError):
        return ApiError(
            422,
            "invalid_config_editor",
            "Configuration editor changes are invalid",
            {"issues": [dict(issue) for issue in exc.issues]},
        )
    raise exc


@router.get(
    "/sessions/{session_id}/config/editor",
    response_model=ConfigEditorResponse,
    responses=CONFIG_EDITOR_ERROR_RESPONSES,
)
def get_config_editor(
    session_id: int,
    db: GradingRepositoryAccess = Depends(get_grading_db),
) -> dict[str, Any]:
    _require_active_session(db, session_id)
    try:
        return editor_response(load_editor_config(db, session_id))
    except (OSError, ValueError, json.JSONDecodeError):
        raise ApiError(
            500,
            "stored_config_invalid",
            "Stored configuration is invalid",
        ) from None


@router.put(
    "/sessions/{session_id}/config/editor",
    response_model=ConfigEditorSaveResponse,
    responses=CONFIG_EDITOR_ERROR_RESPONSES,
)
def save_config_editor(
    session_id: int,
    request: ConfigEditorSaveRequest,
    db: GradingRepositoryAccess = Depends(get_grading_db),
    manager: JobManager = Depends(get_job_manager),
    upload_config_dir: Path = Depends(get_upload_config_dir),
    templates_dir: Path = Depends(get_config_mapping_output_dir),
) -> dict[str, Any]:
    _require_active_session(db, session_id)
    try:
        current, saved, mapping_result = save_editor_config_and_refresh_mapping(
            db,
            upload_config_dir,
            session_id=session_id,
            expected_revision=request.revision,
            edits=_editor_edits(request.edits),
            commands=_editor_commands(request.commands),
            job_store=manager.store,
            mapping_output_dir=templates_dir,
            mapping_refresher=lambda: refresh_template_mapping_from_session(
                db, session_id, output_root=templates_dir
            ),
        )
    except ConfigSessionBusyError as exc:
        raise _config_busy_error(session_id) from exc
    except (ConfigRevisionConflict, ConfigEditorValidationError) as exc:
        raise _editor_api_error(exc) from None
    except Exception:
        raise ApiError(
            500,
            "config_save_failed",
            "Configuration could not be saved",
        ) from None
    body = editor_response(current)
    body["save_result"] = {
        "config_saved": saved,
        "mapping_status": mapping_result.mapping_status,
        "mapping_message": mapping_result.mapping_message,
    }
    return body


def _require_active_session(
    db: GradingRepositoryAccess,
    session_id: int,
) -> dict[str, Any]:
    session = _require_session(db.sessions, session_id)
    if bool(int(session.get("is_deleted") or 0)):
        raise ApiError(
            404,
            "session_not_found",
            "Session not found",
            {"session_id": int(session_id)},
        )
    return session


def _config_busy_error(session_id: int) -> ApiError:
    return ApiError(
        409,
        "config_generation_in_progress",
        "Configuration work is already active for this session",
        {"session_id": int(session_id)},
    )


@contextmanager
def _source_activation_guard(manager: JobManager, session_id: int):
    try:
        with manager.store.config_session_mutation_guard(session_id):
            yield
    except ConfigSessionBusyError as exc:
        raise ConfigSourceActivationBusyError() from exc


def _submit_config_generation(
    manager: JobManager,
    payload: dict[str, object],
) -> tuple[JobResponse, bool]:
    try:
        if payload.get("client_request_token"):
            job, created = manager.submit_idempotent_config(payload)
            return _job_response(job), created
        return _job_response(manager.submit("config_generation", payload)), True
    except ConfigRequestTokenConflictError as exc:
        raise ApiError(
            409,
            "config_request_token_conflict",
            "Config request token was already used for another request",
        ) from exc
    except ConfigSessionBusyError as exc:
        raise ApiError(
            409,
            "config_generation_in_progress",
            "Configuration work is already active for this session",
            {"session_id": int(payload.get("session_id") or 0)},
        ) from exc
    except UnsupportedJobTypeError as exc:
        raise ApiError(
            503,
            "job_type_not_supported",
            "Config generation is temporarily unavailable",
            {"job_type": "config_generation"},
        ) from exc


def _request_identity(request: Any) -> dict[str, str]:
    token = str(getattr(request, "client_request_token", None) or "").strip()
    if not token:
        return {}
    payload = request.model_dump(exclude={"client_request_token"})
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "client_request_token": token,
        "client_request_fingerprint": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }


def _replay_config_request(
    manager: JobManager,
    session_id: int,
    request: Any,
) -> JobResponse | None:
    identity = _request_identity(request)
    token = identity.get("client_request_token")
    if token is None:
        return None
    existing = manager.store.find_config_job_by_request_token(
        session_id=int(session_id),
        request_token=token,
    )
    if existing is None:
        return None
    if existing.payload.get("client_request_fingerprint") != identity.get(
        "client_request_fingerprint"
    ):
        raise ApiError(
            409,
            "config_request_token_conflict",
            "Config request token was already used for another request",
        )
    return _job_response(existing)


@router.get(
    "/sessions/{session_id}/config/generation-jobs/latest",
    response_model=JobResponse,
    responses={404: {"model": ErrorResponse, "description": "Job not found"}},
)
def get_latest_config_generation_job(
    session_id: int,
    source_id: str,
    source_revision: str,
    generation_mode: str,
    db: GradingRepositoryAccess = Depends(get_grading_db),
    manager: JobManager = Depends(get_job_manager),
) -> JobResponse:
    _require_active_session(db, session_id)
    job = manager.store.find_latest_config_generation_job(
        session_id=session_id,
        source_id=source_id,
        source_revision=source_revision,
        generation_mode=generation_mode,
    )
    if job is None:
        raise ApiError(
            404,
            "config_generation_job_not_found",
            "Config generation job not found",
        )
    return _job_response(job)


@router.get(
    "/sessions/{session_id}/config/generation-jobs/{job_id}/question-states",
    response_model=ConfigGenerationQuestionStatesResponse,
    responses={404: {"model": ErrorResponse, "description": "Job not found"}},
)
def get_config_generation_question_states(
    session_id: int,
    job_id: int,
    db: GradingRepositoryAccess = Depends(get_grading_db),
    manager: JobManager = Depends(get_job_manager),
    upload_config_dir: Path = Depends(get_upload_config_dir),
    source_service: ConfigSourceService = Depends(get_config_source_service),
) -> ConfigGenerationQuestionStatesResponse:
    _require_active_session(db, session_id)
    job = manager.store.get_job(job_id)
    if (
        job is None
        or job.job_type != "config_generation"
        or int(job.payload.get("session_id") or 0) != int(session_id)
    ):
        raise ApiError(404, "config_generation_job_not_found", "Config generation job not found")
    draft = read_config_generation_draft(upload_config_dir, job.id)
    question_ids: list[str] = []
    if draft is not None:
        meta = draft.get("meta") if isinstance(draft.get("meta"), dict) else {}
        question_ids = [
            str(item.get("question_id") or "").strip()
            for item in meta.get("question_states") or []
            if isinstance(item, dict) and str(item.get("question_id") or "").strip()
        ]
    input_id = str(job.payload.get("input_id") or "").strip()
    if not question_ids and input_id:
        try:
            staged = load_config_generation_input(upload_config_dir, input_id)
            blocks = staged.get("confirmed_blocks")
            if isinstance(blocks, list):
                question_ids = [
                    str(item.get("question_id") or "").strip()
                    for item in blocks
                    if isinstance(item, dict) and str(item.get("question_id") or "").strip()
                ]
            if not question_ids and str(staged.get("source_id") or ""):
                source = source_service.load_for_generation(
                    session_id=session_id,
                    source_id=str(staged.get("source_id") or ""),
                    source_revision=str(staged.get("source_revision") or ""),
                )
                excluded = {
                    str(item.get("question_id") or "")
                    for item in staged.get("decisions") or []
                    if isinstance(item, dict) and item.get("excluded") is True
                }
                question_ids = [
                    item.question_id for item in source.questions
                    if item.question_id not in excluded
                ]
        except Exception:
            question_ids = []
    result_questions = job.result.get("questions") if isinstance(job.result, dict) else None
    if not question_ids and isinstance(result_questions, list):
        question_ids = [
            str(item.get("question_id") or "").strip()
            for item in result_questions
            if isinstance(item, dict) and str(item.get("question_id") or "").strip()
        ]
    projection_payload = draft or {
        "meta": {
            "question_states": result_questions or [],
            "failed_question_ids": (
                job.result.get("failed_question_ids", [])
                if isinstance(job.result, dict)
                else []
            ),
        }
    }
    return ConfigGenerationQuestionStatesResponse(
        job_id=job.id,
        questions=project_question_states(
            question_ids,
            projection_payload,
            job_status=job.status,
        ),
    )


@router.get(
    "/sessions/{session_id}/config/generation-jobs/requests/{request_token}",
    response_model=JobResponse,
    responses={404: {"model": ErrorResponse, "description": "Job not found"}},
)
def get_config_generation_job_by_request_token(
    session_id: int,
    request_token: str,
    db: GradingRepositoryAccess = Depends(get_grading_db),
    manager: JobManager = Depends(get_job_manager),
) -> JobResponse:
    _require_active_session(db, session_id)
    try:
        job = manager.store.find_config_job_by_request_token(
            session_id=session_id,
            request_token=request_token,
        )
    except ValueError:
        job = None
    if job is None or job.payload.get("abandoned") is True:
        raise ApiError(
            404,
            "config_generation_job_not_found",
            "Config generation job not found",
        )
    return _job_response(job)


@router.post(
    "/sessions/{session_id}/config/generation-jobs/requests/{request_token}/abandon",
    responses={409: {"model": ErrorResponse, "description": "Request already exists"}},
)
def abandon_config_generation_request(
    session_id: int,
    request_token: str,
    db: GradingRepositoryAccess = Depends(get_grading_db),
    manager: JobManager = Depends(get_job_manager),
    upload_config_dir: Path = Depends(get_upload_config_dir),
) -> dict[str, str]:
    _require_active_session(db, session_id)
    try:
        with session_config_lock(upload_config_dir, session_id):
            manager.store.abandon_config_request(
                session_id=session_id,
                request_token=request_token,
            )
    except ConfigRequestTokenConflictError as exc:
        raise ApiError(
            409,
            "config_request_token_conflict",
            "Config request token already has a job",
        ) from exc
    return {"status": "abandoned"}


@router.post(
    "/sessions/{session_id}/config/generate-from-source",
    response_model=JobResponse,
    status_code=202,
    responses={**CONFIG_GENERATION_ERROR_RESPONSES, **CONFIG_SOURCE_ERROR_RESPONSES},
)
def generate_session_config_from_source(
    session_id: int,
    request: ConfigSourceGenerationRequest,
    db: GradingRepositoryAccess = Depends(get_grading_db),
    manager: JobManager = Depends(get_job_manager),
    upload_config_dir: Path = Depends(get_upload_config_dir),
    source_service: ConfigSourceService = Depends(get_config_source_service),
) -> JobResponse:
    session = _require_active_session(db, session_id)
    if request.generation_mode != "batched" or not request.sync_to_question_bank:
        raise ApiError(
            409,
            "config_generation_mode_disabled",
            "旧的评分依据生成方式已停用，请用“分析并入库”重新生成。",
        )
    replay = _replay_config_request(manager, session_id, request)
    if replay is not None:
        return replay
    try:
        record = source_service.load_for_generation(
            session_id=session_id,
            source_id=request.source_id,
            source_revision=request.source_revision,
        )
        decisions = [
            QuestionDecision(**decision.model_dump(exclude_defaults=True))
            for decision in request.decisions
        ]
        asset_decisions = [
            AmbiguousAssetDecision(**decision.model_dump(exclude_defaults=True))
            for decision in request.asset_decisions
        ]
        prepared = source_service.prepare_generation_input(
            record,
            decisions,
            request.generation_mode,
            asset_decisions,
        )
    except ConfigSourceError as exc:
        raise _source_api_error(exc) from None
    except (TypeError, ValueError):
        raise ApiError(
            422,
            "invalid_config_generation_request",
            "Config generation request is invalid",
        ) from None

    loaded_regeneration = None
    if request.regenerate_question_ids is not None:
        loaded_regeneration = load_editor_config(db, session_id)
        if (
            not loaded_regeneration.configured
            or loaded_regeneration.revision != request.base_revision
        ):
            raise ApiError(
                409,
                "config_revision_conflict",
                "Configuration changed before regeneration started",
            )
        published_ids = {
            str(question.get("question_id") or "").strip()
            for question in (
                (loaded_regeneration.payload or {}).get("questions") or []
            )
            if str(question.get("question_id") or "").strip()
        }
        requested_ids = set(request.regenerate_question_ids)
        available_ids = {
            str(block.get("question_id") or "").strip()
            for block in prepared.confirmed_blocks
        }
        if (
            not requested_ids
            or not requested_ids.issubset(published_ids)
            or not requested_ids.issubset(available_ids)
        ):
            raise ApiError(
                409,
                "config_question_regeneration_not_available",
                "Requested questions are not in the current rubric",
            )

    input_id = stage_config_source_generation_input(
        upload_config_dir,
        session_id=int(session_id),
        expected_rubric_path=str(session.get("rubric_path") or ""),
        expected_answer_key_path=str(session.get("answer_key_path") or ""),
        generation_mode=request.generation_mode,
        source_id=record.source_id,
        source_revision=record.source_revision,
        decisions=[
            decision.model_dump(exclude_defaults=True)
            for decision in request.decisions
        ],
        asset_decisions=[
            decision.model_dump(exclude_defaults=True)
            for decision in request.asset_decisions
        ],
        sync_to_question_bank=bool(request.sync_to_question_bank),
        curriculum_volume_id=request.curriculum_volume_id,
        existing_payload=(
            loaded_regeneration.payload
            if loaded_regeneration is not None
            else None
        ),
        regenerate_question_ids=request.regenerate_question_ids,
        expected_revision=request.base_revision,
    )
    try:
        response, created = _submit_config_generation(
            manager,
            {
                "session_id": int(session_id),
                "mode": (
                    "regenerate_questions"
                    if request.regenerate_question_ids is not None
                    else "generate"
                ),
                "generation_mode": request.generation_mode,
                "input_id": input_id,
                "source_id": record.source_id,
                "source_revision": record.source_revision,
                "sync_to_question_bank": bool(
                    request.sync_to_question_bank
                ),
                **_request_identity(request),
            },
        )
        if not created:
            discard_config_generation_input(upload_config_dir, input_id)
        return response
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
    db: GradingRepositoryAccess = Depends(get_grading_db),
    manager: JobManager = Depends(get_job_manager),
) -> JobResponse:
    _require_active_session(db, session_id)
    replay = _replay_config_request(manager, session_id, request)
    if replay is not None:
        return replay
    source = manager.get(request.source_job_id)
    if source is None:
        raise ApiError(
            404,
            "config_generation_job_not_found",
            "Config generation job not found",
            {"source_job_id": int(request.source_job_id)},
        )
    source_failed_batches = source.result.get("failed_batches")
    retry_failed_batches = (
        source.result.get("outcome") == "partial"
        and isinstance(source_failed_batches, list)
        and bool(source_failed_batches)
    )
    raw_uncertain_ids = source.result.get("uncertain_question_ids")
    uncertain_ids = [
        str(item).strip()
        for item in raw_uncertain_ids
        if str(item).strip()
    ] if isinstance(raw_uncertain_ids, list) else []
    retry_uncertain_results = (
        source.result.get("outcome") == "partial"
        and bool(uncertain_ids)
        and request.confirm_uncertain_retry
        and request.retry_question_ids is not None
    )
    if request.confirm_uncertain_retry and (
        request.retry_question_ids is None
        or set(request.retry_question_ids) != set(uncertain_ids)
    ):
        raise ApiError(
            409,
            "config_generation_retry_not_available",
            "All uncertain questions must be retried together after confirmation",
            {"source_job_id": int(request.source_job_id)},
        )
    retry_score_allocation = (
        source.result.get("outcome") == "partial"
        and bool(source.result.get("score_allocation_pending"))
        and not bool(source_failed_batches)
        and request.retry_question_ids is None
    )
    resume_complete_draft = (
        source.result.get("outcome") == "complete"
        and source.status in {"failed", "cancelled"}
        and request.retry_question_ids is None
    )
    if (
        source.job_type == "config_generation"
        and str(source.payload.get("generation_mode") or "")
        not in {"batched", "per_question"}
    ):
        raise ApiError(
            409,
            "config_generation_retry_not_available",
            "旧的评分依据生成方式已停用，请用“分析并入库”重新生成。",
            {"source_job_id": int(request.source_job_id)},
        )
    if (
        source.job_type != "config_generation"
        or str(source.payload.get("mode") or "") == "regenerate_questions"
        or source.status not in {"succeeded", "failed", "cancelled"}
        or not (
            retry_failed_batches
            or retry_uncertain_results
            or retry_score_allocation
            or resume_complete_draft
        )
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
        selected = set(request.retry_question_ids)
        if (
            not request.confirm_uncertain_retry
            and any(item not in failed_ids for item in request.retry_question_ids)
        ):
            raise ApiError(
                409,
                "config_generation_retry_not_available",
                "Requested questions are not currently failed",
                {"source_job_id": int(request.source_job_id)},
            )
        if not request.confirm_uncertain_retry:
            raw_batches = source_failed_batches
            failed_batches = [item for item in raw_batches or [] if isinstance(item, dict)]
            complete_selection = {
                str(qid)
                for item in failed_batches
                if {
                    str(value)
                    for value in item.get("question_ids") or []
                    if str(value).strip()
                }.issubset(selected)
                for qid in item.get("question_ids") or []
            }
            if failed_batches and selected != complete_selection:
                raise ApiError(
                    409,
                    "config_generation_retry_not_available",
                    "Requested questions must contain complete failed batches",
                    {"source_job_id": int(request.source_job_id)},
                )
    payload: dict[str, object] = {
        "session_id": int(session_id),
        "mode": "retry",
        "generation_mode": "batched",
        "source_job_id": int(request.source_job_id),
        "input_id": str(source.payload.get("input_id") or ""),
        "sync_to_question_bank": bool(
            source.payload.get("sync_to_question_bank")
        ),
        **_request_identity(request),
    }
    source_id = str(source.payload.get("source_id") or "").strip()
    source_revision = str(source.payload.get("source_revision") or "").strip()
    if source_id and source_revision:
        payload["source_id"] = source_id
        payload["source_revision"] = source_revision
    if request.retry_question_ids is not None:
        payload["retry_question_ids"] = request.retry_question_ids
    if request.confirm_uncertain_retry:
        payload["confirm_uncertain_retry"] = True
    try:
        if request.client_request_token:
            job, _created = manager.submit_idempotent_config_retry(payload)
            return _job_response(job)
        return _job_response(manager.submit_config_retry(payload))
    except ConfigRequestTokenConflictError as exc:
        raise ApiError(
            409,
            "config_request_token_conflict",
            "Config request token was already used for another request",
        ) from exc
    except ConfigSessionBusyError as exc:
        raise ApiError(
            409,
            "config_generation_in_progress",
            "Configuration work is already active for this session",
            {"session_id": int(session_id)},
        ) from exc
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
