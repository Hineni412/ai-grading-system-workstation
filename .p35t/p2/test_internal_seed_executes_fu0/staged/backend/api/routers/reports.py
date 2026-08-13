from __future__ import annotations

from math import ceil

from fastapi import APIRouter, Depends, Query

from backend.api.app import ApiError
from backend.api.dependencies import (
    get_grading_db,
    get_job_file_service,
    get_job_manager,
)
from backend.api.routers.jobs import _job_response
from backend.api.routers.sessions import _require_session
from backend.api.schemas.jobs import JobResponse
from backend.api.schemas.reports import (
    ReportExportContextResponse,
    ReportExportHistoryItem,
    ReportExportRequest,
)
from backend.file_access import (
    ControlledFileExpired,
    ControlledFileForbidden,
    ControlledFileTypeError,
)
from backend.files.service import JobFileNotFound, JobFileService, JobFileUnavailable
from backend.jobs.manager import JobManager, UnsupportedJobTypeError
from backend.report_exports import score_revision, submit_report_export
from backend.repositories.access import GradingRepositoryAccess


router = APIRouter(prefix="/api", tags=["reports"])


@router.post(
    "/sessions/{session_id}/reports/export",
    response_model=JobResponse,
    status_code=202,
)
def export_session_report(
    session_id: int,
    request: ReportExportRequest | None = None,
    db: GradingRepositoryAccess = Depends(get_grading_db),
    manager: JobManager = Depends(get_job_manager),
    file_service: JobFileService = Depends(get_job_file_service),
) -> JobResponse:
    _require_session(db, session_id)
    if request is not None and not db.get_session_results(int(session_id)):
        raise ApiError(
            409,
            "report_results_missing",
            "The exam has no grading results to export",
        )
    try:
        if request is None:
            job = manager.submit("report_export", {"session_id": int(session_id)})
        else:
            job = submit_report_export(
                manager=manager,
                file_service=file_service,
                session_id=session_id,
                report_type=request.report_type,
                revision=score_revision(db, session_id),
                force_regenerate=request.force_regenerate,
            )
    except UnsupportedJobTypeError as exc:
        raise ApiError(
            404,
            "job_type_not_supported",
            "Job type is not supported",
            {"job_type": "report_export"},
        ) from exc
    return _job_response(job)


@router.get(
    "/sessions/{session_id}/reports/context",
    response_model=ReportExportContextResponse,
)
def get_session_report_context(
    session_id: int,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=100),
    db: GradingRepositoryAccess = Depends(get_grading_db),
    manager: JobManager = Depends(get_job_manager),
    file_service: JobFileService = Depends(get_job_file_service),
) -> ReportExportContextResponse:
    _require_session(db, session_id)
    revision = score_revision(db, session_id)
    jobs, total = manager.list(
        session_id=int(session_id),
        job_types=("report_export",),
        limit=page_size,
        offset=(page - 1) * page_size,
    )
    return ReportExportContextResponse(
        score_revision=revision,
        has_results=bool(db.get_session_results(int(session_id))),
        jobs=[
            ReportExportHistoryItem(
                **_job_response(job).model_dump(),
                is_current_revision=(
                    str(job.payload.get("score_revision") or "") == revision
                ),
                file_status=_file_status(job, file_service),
            )
            for job in jobs
        ],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=max(1, ceil(total / page_size)),
    )


def _file_status(job, file_service: JobFileService) -> str:
    if job.status in {"queued", "running", "paused"}:
        return "pending"
    if job.status == "failed":
        return "failed"
    if job.status == "cancelled":
        return "cancelled"
    try:
        file_service.resolve(job)
    except ControlledFileExpired:
        return "expired"
    except (
        ControlledFileForbidden,
        ControlledFileTypeError,
        JobFileNotFound,
        JobFileUnavailable,
    ):
        return "unavailable"
    return "available"
