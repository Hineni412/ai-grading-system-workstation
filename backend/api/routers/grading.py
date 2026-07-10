from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.api.app import ApiError
from backend.api.dependencies import get_grading_db, get_job_manager
from backend.api.routers.jobs import _job_response
from backend.api.routers.sessions import _require_session
from backend.api.schemas.grading import GradingRunRequest
from backend.api.schemas.jobs import JobResponse
from backend.jobs.manager import JobManager, UnsupportedJobTypeError
from db_manager import DBManager


router = APIRouter(prefix="/api", tags=["grading"])


@router.post(
    "/sessions/{session_id}/grading/run",
    response_model=JobResponse,
    status_code=202,
)
def run_session_grading(
    session_id: int,
    request: GradingRunRequest | None = None,
    db: DBManager = Depends(get_grading_db),
    manager: JobManager = Depends(get_job_manager),
) -> JobResponse:
    _require_session(db, session_id)
    request = request or GradingRunRequest()
    payload: dict[str, object] = {
        "session_id": int(session_id),
        "grading_mode": request.grading_mode,
        "failed_only": request.failed_only,
        "enhance_images": request.enhance_images,
    }
    if request.max_workers is not None:
        payload["max_workers"] = request.max_workers
    if request.requests_per_minute is not None:
        payload["requests_per_minute"] = request.requests_per_minute
    if request.exams_dir:
        payload["exams_dir"] = request.exams_dir
    if request.resume_run_id is not None:
        payload["resume_run_id"] = request.resume_run_id
    try:
        job = manager.submit("grading_run", payload)
    except UnsupportedJobTypeError as exc:
        raise ApiError(
            404,
            "job_type_not_supported",
            "Job type is not supported",
            {"job_type": "grading_run"},
        ) from exc
    return _job_response(job)
