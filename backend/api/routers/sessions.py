from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends

from backend.api.app import ApiError
from backend.api.dependencies import (
    get_grading_db,
    get_session_repository,
    get_upload_config_dir,
)
from backend.api.schemas.sessions import (
    AnswerRegionListResponse,
    AnswerRegionResponse,
    CreateSessionDraftRequest,
    CreateSessionRequest,
    RenameSessionRequest,
    SessionDetail,
    SessionListResponse,
    SessionProgress,
    SessionSummary,
    SessionTemplateResponse,
)
from backend.config_workspace.drafts import create_session_draft
from backend.repositories.sessions import SessionRepositoryGateway
from backend.repositories.access import GradingRepositoryAccess


router = APIRouter(prefix="/api", tags=["sessions"])


def _bool(value: Any) -> bool:
    return bool(int(value or 0))


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _require_session(
    sessions: SessionRepositoryGateway,
    session_id: int,
) -> dict[str, Any]:
    session = sessions.get_grading_session(int(session_id))
    if session is None:
        raise ApiError(
            404,
            "session_not_found",
            "Session not found",
            {"session_id": int(session_id)},
        )
    return session


def _session_summary(row: dict[str, Any]) -> SessionSummary:
    return SessionSummary(
        id=int(row["id"]),
        name=str(row["session_name"]),
        status=str(row["status"]),
        is_deleted=_bool(row.get("is_deleted")),
        deleted_at=row.get("deleted_at"),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


def _session_detail(row: dict[str, Any]) -> SessionDetail:
    summary = _session_summary(row).model_dump()
    return SessionDetail(
        **summary,
        rubric_path=str(row.get("rubric_path") or ""),
        answer_key_path=str(row.get("answer_key_path") or ""),
        template_config_path=row.get("template_config_path"),
        source_paper_path=row.get("source_paper_path"),
        source_paper_sha256=row.get("source_paper_sha256"),
        question_bank_sync_state=str(row.get("question_bank_sync_state") or "not_started"),
        question_bank_sync_details=_json_object(row.get("question_bank_sync_details_json")),
        question_bank_sync_error=row.get("question_bank_sync_error"),
        question_bank_sync_updated_at=row.get("question_bank_sync_updated_at"),
    )


def _template_response(row: dict[str, Any]) -> SessionTemplateResponse:
    return SessionTemplateResponse(
        id=int(row["id"]),
        session_id=int(row["session_id"]),
        pages={
            page: {"url": f"/api/sessions/{int(row['session_id'])}/template/pages/{page}"}
            for page in ("front", "back")
        },
        is_confirmed=_bool(row.get("is_confirmed")),
        regions_snapshot_pending=_bool(row.get("regions_snapshot_pending")),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


def _region_response(row: dict[str, Any]) -> AnswerRegionResponse:
    return AnswerRegionResponse(
        id=int(row["id"]),
        region_uuid=str(row["region_uuid"]),
        session_id=int(row["session_id"]),
        template_id=int(row["template_id"]),
        page=str(row.get("page") or ""),
        region_order=int(row.get("region_order") or 0),
        x=int(row.get("x") or 0),
        y=int(row.get("y") or 0),
        w=int(row.get("w") or 0),
        h=int(row.get("h") or 0),
        detected_question_id=row.get("detected_question_id"),
        mapped_question_id=row.get("mapped_question_id"),
        confidence=float(row.get("confidence") or 0.0),
        is_confirmed=_bool(row.get("is_confirmed")),
        mapping_status=str(row.get("mapping_status") or ""),
        multi_region_confirmed=_bool(row.get("multi_region_confirmed")),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


@router.get("/sessions", response_model=SessionListResponse)
def list_sessions(
    include_deleted: bool = False,
    sessions: SessionRepositoryGateway = Depends(get_session_repository),
) -> SessionListResponse:
    items = [
        _session_summary(row)
        for row in sessions.list_grading_sessions(include_deleted)
    ]
    return SessionListResponse(items=items, total=len(items))


@router.get("/sessions/{session_id}", response_model=SessionDetail)
def get_session(
    session_id: int,
    sessions: SessionRepositoryGateway = Depends(get_session_repository),
) -> SessionDetail:
    return _session_detail(_require_session(sessions, session_id))


@router.get("/sessions/{session_id}/progress", response_model=SessionProgress)
def get_session_progress(
    session_id: int,
    db: GradingRepositoryAccess = Depends(get_grading_db),
    sessions: SessionRepositoryGateway = Depends(get_session_repository),
) -> SessionProgress:
    _require_session(sessions, session_id)
    return SessionProgress(**db.get_session_progress(int(session_id)))


@router.get("/sessions/{session_id}/template", response_model=SessionTemplateResponse)
def get_session_template(
    session_id: int,
    db: GradingRepositoryAccess = Depends(get_grading_db),
    sessions: SessionRepositoryGateway = Depends(get_session_repository),
) -> SessionTemplateResponse:
    _require_session(sessions, session_id)
    template = db.get_session_template(int(session_id))
    if template is None:
        raise ApiError(
            404,
            "template_not_found",
            "Session template not found",
            {"session_id": int(session_id)},
        )
    return _template_response(template)


@router.get("/sessions/{session_id}/regions", response_model=AnswerRegionListResponse)
def list_answer_regions(
    session_id: int,
    db: GradingRepositoryAccess = Depends(get_grading_db),
    sessions: SessionRepositoryGateway = Depends(get_session_repository),
) -> AnswerRegionListResponse:
    _require_session(sessions, session_id)
    items = [_region_response(row) for row in db.list_answer_regions(int(session_id))]
    return AnswerRegionListResponse(items=items, total=len(items))


@router.post("/sessions", response_model=SessionDetail, status_code=201)
def create_session(
    request: CreateSessionRequest,
    sessions: SessionRepositoryGateway = Depends(get_session_repository),
) -> SessionDetail:
    try:
        session_id = sessions.create_grading_session(
            request.name,
            request.rubric_path,
            request.answer_key_path,
            source_paper_path=request.source_paper_path,
            source_paper_sha256=request.source_paper_sha256,
        )
    except ValueError as exc:
        raise ApiError(400, "invalid_session", str(exc)) from exc
    return _session_detail(_require_session(sessions, session_id))


@router.post("/sessions/drafts", response_model=SessionSummary, status_code=201)
def create_session_draft_route(
    request: CreateSessionDraftRequest,
    sessions: SessionRepositoryGateway = Depends(get_session_repository),
    upload_config_dir: Path = Depends(get_upload_config_dir),
) -> SessionSummary:
    try:
        session_id = create_session_draft(
            sessions,
            upload_config_dir,
            name=request.name,
        )
    except ValueError as exc:
        raise ApiError(400, "invalid_session_draft", str(exc)) from exc
    return _session_summary(_require_session(sessions, session_id))


@router.patch("/sessions/{session_id}", response_model=SessionDetail)
def rename_session(
    session_id: int,
    request: RenameSessionRequest,
    sessions: SessionRepositoryGateway = Depends(get_session_repository),
) -> SessionDetail:
    _require_session(sessions, session_id)
    sessions.rename_grading_session(int(session_id), request.name)
    return _session_detail(_require_session(sessions, session_id))


@router.delete("/sessions/{session_id}", response_model=SessionDetail)
def soft_delete_session(
    session_id: int,
    sessions: SessionRepositoryGateway = Depends(get_session_repository),
) -> SessionDetail:
    _require_session(sessions, session_id)
    sessions.soft_delete_grading_session(int(session_id))
    return _session_detail(_require_session(sessions, session_id))


@router.post("/sessions/{session_id}/restore", response_model=SessionDetail)
def restore_session(
    session_id: int,
    sessions: SessionRepositoryGateway = Depends(get_session_repository),
) -> SessionDetail:
    _require_session(sessions, session_id)
    sessions.restore_grading_session(int(session_id))
    return _session_detail(_require_session(sessions, session_id))
