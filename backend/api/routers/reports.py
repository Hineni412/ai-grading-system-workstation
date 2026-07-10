from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.api.app import ApiError
from backend.api.dependencies import get_grading_db, get_job_manager
from backend.api.routers.jobs import _job_response
from backend.api.routers.sessions import _require_session
from backend.api.schemas.jobs import JobResponse
from backend.jobs.manager import JobManager, UnsupportedJobTypeError
from db_manager import DBManager


router = APIRouter(prefix="/api", tags=["reports"])


@router.post(
    "/sessions/{session_id}/reports/export",
    response_model=JobResponse,
    status_code=202,
)
def export_session_report(
    session_id: int,
    db: DBManager = Depends(get_grading_db),
    manager: JobManager = Depends(get_job_manager),
) -> JobResponse:
    _require_session(db, session_id)
    try:
        job = manager.submit("report_export", {"session_id": int(session_id)})
    except UnsupportedJobTypeError as exc:
        raise ApiError(
            404,
            "job_type_not_supported",
            "Job type is not supported",
            {"job_type": "report_export"},
        ) from exc
    return _job_response(job)
