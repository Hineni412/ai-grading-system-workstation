from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends

from backend.api.app import ApiError
from backend.api.dependencies import get_grading_db, get_upload_config_dir
from backend.api.routers.sessions import _require_session
from backend.api.schemas.config import SessionConfigRequest, SessionConfigResponse
from db_manager import DBManager
from path_manager import resolve_stored_file_path
from session_manager import save_generated_config


router = APIRouter(prefix="/api", tags=["config"])


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
