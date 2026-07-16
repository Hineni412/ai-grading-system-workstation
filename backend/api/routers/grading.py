from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.api.app import ApiError
from backend.api.dependencies import (
    get_grading_db,
    get_job_manager,
    get_scan_grading_workspace,
)
from backend.api.routers.jobs import _job_response
from backend.api.routers.sessions import _require_session
from backend.api.schemas.grading import GradingRunCancelRequest, GradingRunRequest
from backend.api.schemas.jobs import JobResponse
from backend.api.schemas.scan import GradingRunSummaryResponse
from backend.scan_grading.workspace import (
    PendingScanIssuesError,
    ScanGradingWorkspace,
    ScanGradingWorkspaceError,
    UploadBatchRevisionError,
)
from backend.jobs.manager import JobManager, UnsupportedJobTypeError
from db_manager import DBManager


router = APIRouter(prefix="/api", tags=["grading"])


@router.post(
    "/sessions/{session_id}/grading/runs/{run_id}/pause",
    response_model=GradingRunSummaryResponse,
)
def pause_session_grading(
    session_id: int,
    run_id: int,
    db: DBManager = Depends(get_grading_db),
    workspace: ScanGradingWorkspace = Depends(get_scan_grading_workspace),
) -> GradingRunSummaryResponse:
    _require_session(db, session_id)
    try:
        result = workspace.pause_grading_run(session_id, run_id)
    except ScanGradingWorkspaceError as exc:
        raise ApiError(409, "grading_run_not_pausable", "Grading run cannot be paused") from exc
    return GradingRunSummaryResponse.model_validate(result)


def _submit_controlled_grading_job(
    payload: dict[str, object],
    manager: JobManager,
    workspace: ScanGradingWorkspace,
) -> JobResponse:
    session_id = int(payload["session_id"])
    if workspace.upload_batch_exists(session_id):
        payload["exams_dir"] = str(workspace.frozen_scan_dir(session_id))
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


@router.post(
    "/sessions/{session_id}/grading/runs/{run_id}/resume",
    response_model=JobResponse,
    status_code=202,
)
def resume_session_grading(
    session_id: int,
    run_id: int,
    db: DBManager = Depends(get_grading_db),
    manager: JobManager = Depends(get_job_manager),
    workspace: ScanGradingWorkspace = Depends(get_scan_grading_workspace),
) -> JobResponse:
    _require_session(db, session_id)
    try:
        payload = workspace.prepare_resume(session_id, run_id)
        return _submit_controlled_grading_job(payload, manager, workspace)
    except ScanGradingWorkspaceError as exc:
        raise ApiError(409, "grading_run_not_resumable", "Grading run cannot be resumed") from exc


@router.post(
    "/sessions/{session_id}/grading/runs/{run_id}/retry-failed",
    response_model=JobResponse,
    status_code=202,
)
def retry_failed_session_grading(
    session_id: int,
    run_id: int,
    db: DBManager = Depends(get_grading_db),
    manager: JobManager = Depends(get_job_manager),
    workspace: ScanGradingWorkspace = Depends(get_scan_grading_workspace),
) -> JobResponse:
    _require_session(db, session_id)
    try:
        payload = workspace.prepare_failed_retry(session_id, run_id)
        return _submit_controlled_grading_job(payload, manager, workspace)
    except ScanGradingWorkspaceError as exc:
        raise ApiError(409, "grading_run_not_retryable", "Grading run has no retryable failures") from exc


@router.post(
    "/sessions/{session_id}/grading/runs/{run_id}/cancel",
    response_model=GradingRunSummaryResponse,
)
def cancel_session_grading(
    session_id: int,
    run_id: int,
    request: GradingRunCancelRequest,
    db: DBManager = Depends(get_grading_db),
    manager: JobManager = Depends(get_job_manager),
    workspace: ScanGradingWorkspace = Depends(get_scan_grading_workspace),
) -> GradingRunSummaryResponse:
    _require_session(db, session_id)
    job = manager.get(request.job_id)
    if (
        job is None
        or job.job_type != "grading_run"
        or int(job.payload.get("session_id") or 0) != int(session_id)
    ):
        raise ApiError(404, "grading_job_not_found", "Grading job was not found")
    manager.cancel(job.id)
    updated = manager.get(job.id)
    try:
        summary = workspace.record_cancel_request(
            session_id,
            run_id,
            job.id,
            confirmed=bool(updated and updated.status == "cancelled"),
        )
    except ScanGradingWorkspaceError as exc:
        raise ApiError(409, "grading_run_not_cancellable", "Grading run cannot be cancelled") from exc
    return GradingRunSummaryResponse.model_validate(summary)


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
    workspace: ScanGradingWorkspace = Depends(get_scan_grading_workspace),
) -> JobResponse:
    _require_session(db, session_id)
    request = request or GradingRunRequest()
    if workspace.upload_batch_exists(session_id):
        if request.failed_only or request.resume_run_id is not None:
            raise ApiError(
                422,
                "dedicated_grading_control_required",
                "Use the run-specific resume or retry endpoint",
            )
        if request.upload_revision is None or request.decision_revision is None:
            raise ApiError(
                422,
                "grading_confirmation_required",
                "Current upload and preflight revisions are required",
            )
        try:
            payload = workspace.prepare_start(
                session_id,
                grading_mode=request.grading_mode,
                upload_revision=request.upload_revision,
                decision_revision=request.decision_revision,
                confirm_pending_issues=request.confirm_pending_issues,
                enhance_images=request.enhance_images,
                max_workers=request.max_workers,
                requests_per_minute=request.requests_per_minute,
            )
        except PendingScanIssuesError as exc:
            raise ApiError(
                409,
                "pending_scan_issues_not_confirmed",
                "Confirm the pending scan issues before grading",
            ) from exc
        except UploadBatchRevisionError as exc:
            raise ApiError(409, "grading_input_changed", "Grading input changed") from exc
        except ScanGradingWorkspaceError as exc:
            raise ApiError(409, "grading_input_not_ready", "Grading input is not ready") from exc
        return _submit_controlled_grading_job(payload, manager, workspace)

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
    if request.resume_run_id is not None:
        payload["resume_run_id"] = request.resume_run_id
    return _submit_controlled_grading_job(payload, manager, workspace)
