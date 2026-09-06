from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Response

from backend.api.app import ApiError, ErrorResponse
from backend.api.schemas.ai_diagnostics import (
    AiDiagnosticDetail,
    AiDiagnosticListResponse,
)
from backend.llm.diagnostics import JsonlDiagnosticJournal


router = APIRouter(
    prefix="/api/ai-diagnostics",
    tags=["ai-diagnostics"],
)
_JOURNAL = JsonlDiagnosticJournal()
_NO_STORE = "no-store, max-age=0"


@router.get(
    "",
    response_model=AiDiagnosticListResponse,
    responses={
        503: {
            "model": ErrorResponse,
            "description": "Diagnostic journal is unavailable",
        }
    },
)
def list_ai_diagnostics(
    response: Response,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    request_kind: Annotated[str, Query(max_length=80)] = "",
    outcome: Annotated[
        str,
        Query(pattern=r"^(?:|pending|success|failure)$"),
    ] = "",
    workspace_module: Annotated[
        str,
        Query(pattern=r"^(?:|[a-z][a-z0-9_]{0,79})$"),
    ] = "",
    workspace_task_kind: Annotated[
        str,
        Query(pattern=r"^(?:|[a-z][a-z0-9_]{0,79})$"),
    ] = "",
) -> AiDiagnosticListResponse:
    response.headers["Cache-Control"] = _NO_STORE
    try:
        payload = _JOURNAL.list_calls(
            limit=limit,
            request_kind=request_kind,
            outcome=outcome,
            workspace_module=workspace_module,
            workspace_task_kind=workspace_task_kind,
        )
    except (OSError, UnicodeError, ValueError) as exc:
        raise ApiError(
            503,
            "ai_diagnostics_unavailable",
            "AI 调用记录暂时无法读取。",
        ) from exc
    return AiDiagnosticListResponse(**payload)




@router.get(
    "/{call_id}",
    response_model=AiDiagnosticDetail,
    responses={
        404: {
            "model": ErrorResponse,
            "description": "Diagnostic call not found",
        },
        503: {
            "model": ErrorResponse,
            "description": "Diagnostic journal is unavailable",
        },
    },
)
def get_ai_diagnostic(
    call_id: str,
    response: Response,
) -> AiDiagnosticDetail:
    response.headers["Cache-Control"] = _NO_STORE
    try:
        payload = _JOURNAL.get_call(call_id)
    except (OSError, UnicodeError, ValueError) as exc:
        raise ApiError(
            503,
            "ai_diagnostics_unavailable",
            "AI 调用记录暂时无法读取。",
        ) from exc
    if payload is None:
        raise ApiError(
            404,
            "ai_diagnostic_not_found",
            "没有找到这次 AI 调用记录。",
        )
    return AiDiagnosticDetail(**payload)
