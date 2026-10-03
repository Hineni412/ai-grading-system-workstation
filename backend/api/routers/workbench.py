from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query

from backend.api.dependencies import (
    get_grading_db,
    get_scan_grading_workspace,
    get_workbench_service,
)
from backend.api.routers.sessions import _require_session
from backend.api.schemas.workbench import (
    SessionAnomalyListResponse,
    SessionAnomalyResponse,
    WorkbenchOverviewResponse,
)
from backend.repositories.access import GradingRepositoryAccess
from backend.review.manual_context import current_manual_context
from backend.scan_grading.workspace import ScanGradingWorkspace
from backend.workbench.service import WorkbenchService

router = APIRouter(prefix="/api", tags=["workbench"])


@router.get("/workbench/overview", response_model=WorkbenchOverviewResponse)
def get_workbench_overview(
    session_id: int | None = Query(None, gt=0),
    recent_limit: int = Query(5, ge=1, le=20),
    curriculum_volume_id: str | None = Query(None),
    db: GradingRepositoryAccess = Depends(get_grading_db),
    service: WorkbenchService = Depends(get_workbench_service),
    workspace: ScanGradingWorkspace = Depends(get_scan_grading_workspace),
) -> WorkbenchOverviewResponse:
    manual_context = None
    if session_id is not None:
        _require_session(db.sessions, session_id)
        manual_context = current_manual_context(session_id, workspace)
    return WorkbenchOverviewResponse(
        **service.overview(session_id, recent_limit, manual_context=manual_context,
                           curriculum_volume_id=curriculum_volume_id)
    )


@router.get(
    "/sessions/{session_id}/anomalies",
    response_model=SessionAnomalyListResponse,
)
def list_session_anomalies(
    session_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    anomaly_type: Literal[
        "unmatched_paper",
        "scan_issue",
        "grading_failed",
    ]
    | None = None,
    db: GradingRepositoryAccess = Depends(get_grading_db),
    service: WorkbenchService = Depends(get_workbench_service),
) -> SessionAnomalyListResponse:
    _require_session(db.sessions, session_id)
    rows = service.list_anomalies(session_id)
    if anomaly_type is not None:
        rows = [row for row in rows if row["anomaly_type"] == anomaly_type]
    total = len(rows)
    offset = (page - 1) * page_size
    items = [
        SessionAnomalyResponse(**row)
        for row in rows[offset : offset + page_size]
    ]
    return SessionAnomalyListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size,
    )
