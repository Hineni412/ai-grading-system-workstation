from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends

from backend.api.dependencies import (
    get_grading_db,
    get_results_center_service,
    get_scan_grading_workspace,
)
from backend.api.routers.sessions import _require_session
from backend.api.schemas.results_center import ResultsCenterResponse
from backend.repositories.access import GradingRepositoryAccess
from backend.results_center.service import ResultsCenterService
from backend.review.manual_context import current_manual_context
from backend.scan_grading.workspace import ScanGradingWorkspace


router = APIRouter(prefix="/api", tags=["results-center"])


@router.get(
    "/sessions/{session_id}/results-center",
    response_model=ResultsCenterResponse,
)
def get_results_center(
    session_id: int,
    db: GradingRepositoryAccess = Depends(get_grading_db),
    results_service: ResultsCenterService = Depends(
        get_results_center_service
    ),
    workspace: ScanGradingWorkspace = Depends(get_scan_grading_workspace),
) -> ResultsCenterResponse:
    session = _require_session(db, session_id)
    snapshot = results_service.get_snapshot(
        session_id,
        session,
        manual_context=current_manual_context(session_id, workspace),
    )
    return ResultsCenterResponse.model_validate(asdict(snapshot))
