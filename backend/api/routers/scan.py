from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.api.app import ApiError
from backend.api.dependencies import get_grading_db, get_job_manager
from backend.api.routers.jobs import _job_response
from backend.api.routers.sessions import _require_session
from backend.api.schemas.jobs import JobResponse
from backend.api.schemas.scan import ScanAnalyzeRequest
from backend.jobs.manager import JobManager, UnsupportedJobTypeError
from db_manager import DBManager


router = APIRouter(prefix="/api", tags=["scan"])


@router.post(
    "/sessions/{session_id}/scan/analyze",
    response_model=JobResponse,
    status_code=202,
)
def analyze_session_scans(
    session_id: int,
    request: ScanAnalyzeRequest | None = None,
    db: DBManager = Depends(get_grading_db),
    manager: JobManager = Depends(get_job_manager),
) -> JobResponse:
    _require_session(db, session_id)
    request = request or ScanAnalyzeRequest()
    payload: dict[str, object] = {
        "session_id": int(session_id),
        "enhance_images": request.enhance_images,
    }
    if request.ocr_workers is not None:
        payload["ocr_workers"] = request.ocr_workers
    if request.exams_dir:
        payload["exams_dir"] = request.exams_dir
    if request.front_page_parity:
        payload["front_page_parity"] = request.front_page_parity
    try:
        job = manager.submit("scan_analysis", payload)
    except UnsupportedJobTypeError as exc:
        raise ApiError(
            404,
            "job_type_not_supported",
            "Job type is not supported",
            {"job_type": "scan_analysis"},
        ) from exc
    return _job_response(job)
