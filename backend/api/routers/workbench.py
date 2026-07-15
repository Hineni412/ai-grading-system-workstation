from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query

from backend.api.dependencies import get_grading_db, get_workbench_service
from backend.api.routers.sessions import _require_session
from backend.api.schemas.workbench import (
    SessionAnomalyListResponse,
    SessionAnomalyResponse,
    WorkbenchOverviewResponse,
)
from backend.workbench.service import WorkbenchService
from db_manager import DBManager


router = APIRouter(prefix="/api", tags=["workbench"])


@router.get("/workbench/overview", response_model=WorkbenchOverviewResponse)
def get_workbench_overview(
    session_id: int | None = Query(None, gt=0),
    recent_limit: int = Query(5, ge=1, le=20),
    db: DBManager = Depends(get_grading_db),
    service: WorkbenchService = Depends(get_workbench_service),
) -> WorkbenchOverviewResponse:
    if session_id is not None:
        _require_session(db, session_id)
    return WorkbenchOverviewResponse(**service.overview(session_id, recent_limit))


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
    db: DBManager = Depends(get_grading_db),
    service: WorkbenchService = Depends(get_workbench_service),
) -> SessionAnomalyListResponse:
    _require_session(db, session_id)
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
