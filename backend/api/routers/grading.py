from __future__ import annotations

import json

from fastapi import APIRouter, Depends

from backend.api.app import ApiError
from backend.api.dependencies import (
    get_grading_db,
    get_job_manager,
    get_scan_grading_workspace,
)
from backend.api.routers.jobs import _job_response
from backend.api.routers.sessions import _require_session
from backend.api.schemas.grading import (
    GradingPlanRequest,
    GradingPlanResponse,
    GradingRunCancelRequest,
    GradingRunRequest,
)
from backend.api.schemas.jobs import JobResponse
from backend.api.schemas.scan import GradingRunSummaryResponse
from backend.config_workspace.publish import load_editor_config
from backend.exam_intake import exam_intake_blocks_progress
from backend.grading_workflow import build_grading_plan
from backend.scan_grading.workspace import (
    GradingConfigChangedError,
    LegacyGradingModeError,
    PendingScanIssuesError,
    ScanGradingWorkspace,
    ScanGradingWorkspaceError,
    ScanMatchConflictError,
    UploadBatchRevisionError,
)

LEGACY_GRADING_MODE_MESSAGE = "旧批改方式已停用，请用 AI 批改重新开始未完成的部分"
from backend.jobs.manager import ActiveJobExistsError, JobManager, UnsupportedJobTypeError
from backend.repositories.access import GradingRepositoryAccess
from path_manager import resolve_stored_file_path


router = APIRouter(prefix="/api", tags=["grading"])


def _require_exam_intake_complete(
    db: GradingRepositoryAccess,
    session_id: int,
) -> None:
    session = db.sessions.get_grading_session(int(session_id))
    if not exam_intake_blocks_progress(session):
        return
    details: dict[str, object] = {}
    if isinstance(session, dict):
        raw_details = session.get("question_bank_sync_details")
        if isinstance(raw_details, dict):
            details = dict(raw_details)
        else:
            raw = session.get("question_bank_sync_details_json")
            if isinstance(raw, str) and raw.strip():
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    parsed = {}
                if isinstance(parsed, dict):
                    details = parsed
    raise ApiError(
        409,
        "exam_intake_incomplete",
        str(details.get("intake_message") or "题库入库未完整成功，当前不能开始批改"),
        {
            "category": str(details.get("intake_category") or "exam_intake_incomplete"),
            "failed_question_ids": list(
                details.get("intake_failed_question_ids") or []
            ),
            "retryable": bool(details.get("intake_retryable", True)),
        },
    )


def _require_current_preflight_config(
    *,
    session_id: int,
    db: GradingRepositoryAccess,
    workspace: ScanGradingWorkspace,
) -> None:
    try:
        revision = load_editor_config(db, session_id).revision
        workspace.require_preflight_config_revision(session_id, revision)
    except (GradingConfigChangedError, KeyError, OSError, TypeError, ValueError) as exc:
        raise ApiError(
            409,
            "grading_preflight_stale",
            "The grading configuration changed; run scan preflight again",
        ) from exc


@router.post(
    "/sessions/{session_id}/grading/plan",
    response_model=GradingPlanResponse,
)
def preview_session_grading(
    session_id: int,
    request: GradingPlanRequest,
    db: GradingRepositoryAccess = Depends(get_grading_db),
    workspace: ScanGradingWorkspace = Depends(get_scan_grading_workspace),
) -> GradingPlanResponse:
    session = _require_session(db.sessions, session_id)
    _require_exam_intake_complete(db, session_id)
    try:
        workspace_state = workspace.get_workspace(session_id)
        upload_batch = workspace_state["upload_batch"]
        if str(upload_batch.get("state") or "") != "frozen":
            raise ScanGradingWorkspaceError("scan upload batch is not frozen")
        preflight = workspace.get_preflight(session_id)
    except ScanGradingWorkspaceError as exc:
        raise ApiError(
            409,
            "grading_input_not_ready",
            "Grading input is not ready",
        ) from exc
    _require_current_preflight_config(
        session_id=session_id,
        db=db,
        workspace=workspace,
    )

    data_root = (
        db.db_path.parent.parent
        if db.db_path.parent.name == "databases"
        else None
    )
    try:
        rubric_path = resolve_stored_file_path(
            session.get("rubric_path"),
            data_root=data_root,
        )
        rubric = json.loads(rubric_path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError) as exc:
        raise ApiError(
            409,
            "grading_rubric_not_ready",
            "Grading rubric is not ready",
        ) from exc
    if not isinstance(rubric, dict):
        raise ApiError(
            409,
            "grading_rubric_not_ready",
            "Grading rubric is not ready",
        )

    scan_batch_id = str(upload_batch["batch_id"])
    teacher_locks = db.reviews.list_teacher_score_locks(
        session_id,
        scan_batch_id,
    )
    plan = build_grading_plan(
        session_id=session_id,
        mode=request.grading_mode,
        scan_batch_id=scan_batch_id,
        upload_revision=int(upload_batch["revision"]),
        preflight=preflight,
        rubric=rubric,
        teacher_locks=teacher_locks,
    )
    return GradingPlanResponse.model_validate(plan)


@router.post(
    "/sessions/{session_id}/grading/runs/{run_id}/pause",
    response_model=GradingRunSummaryResponse,
)
def pause_session_grading(
    session_id: int,
    run_id: int,
    db: GradingRepositoryAccess = Depends(get_grading_db),
    workspace: ScanGradingWorkspace = Depends(get_scan_grading_workspace),
) -> GradingRunSummaryResponse:
    _require_session(db.sessions, session_id)
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
    if workspace.upload_batch_exists(session_id) and not bool(payload.get("failed_only")):
        payload["exams_dir"] = str(workspace.frozen_scan_dir(session_id))
    try:
        job = manager.submit_unique_active("grading_run", payload)
    except ActiveJobExistsError as exc:
        raise ApiError(
            409,
            "grading_job_already_active",
            "A grading job is already active for this session",
        ) from exc
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
    db: GradingRepositoryAccess = Depends(get_grading_db),
    workspace: ScanGradingWorkspace = Depends(get_scan_grading_workspace),
) -> JobResponse:
    _require_session(db.sessions, session_id)
    try:
        job = workspace.submit_resume(session_id, run_id)
        return _job_response(job)
    except LegacyGradingModeError as exc:
        raise ApiError(
            409,
            "legacy_grading_mode_disabled",
            LEGACY_GRADING_MODE_MESSAGE,
        ) from exc
    except ActiveJobExistsError as exc:
        raise ApiError(
            409,
            "grading_job_already_active",
            "A grading job is already active",
        ) from exc
    except UnsupportedJobTypeError as exc:
        raise ApiError(
            404,
            "job_type_not_supported",
            "Job type is not supported",
        ) from exc
    except GradingConfigChangedError as exc:
        raise ApiError(
            409,
            "grading_config_changed",
            "Grading configuration changed; this run cannot be resumed",
        ) from exc
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
    db: GradingRepositoryAccess = Depends(get_grading_db),
    workspace: ScanGradingWorkspace = Depends(get_scan_grading_workspace),
) -> JobResponse:
    _require_session(db.sessions, session_id)
    try:
        job = workspace.submit_failed_retry(session_id, run_id)
        return _job_response(job)
    except LegacyGradingModeError as exc:
        raise ApiError(
            409,
            "legacy_grading_mode_disabled",
            LEGACY_GRADING_MODE_MESSAGE,
        ) from exc
    except ActiveJobExistsError as exc:
        raise ApiError(
            409,
            "grading_job_already_active",
            "A grading job is already active",
        ) from exc
    except UnsupportedJobTypeError as exc:
        raise ApiError(
            404,
            "job_type_not_supported",
            "Job type is not supported",
        ) from exc
    except ScanGradingWorkspaceError as exc:
        raise ApiError(409, "grading_run_not_retryable", "Grading run has no retryable failures") from exc


@router.post(
    "/sessions/{session_id}/grading/runs/{run_id}/supplement-new-matches",
    response_model=JobResponse,
    status_code=202,
)
def supplement_session_grading(
    session_id: int,
    run_id: int,
    db: GradingRepositoryAccess = Depends(get_grading_db),
    workspace: ScanGradingWorkspace = Depends(get_scan_grading_workspace),
) -> JobResponse:
    _require_session(db.sessions, session_id)
    try:
        job = workspace.submit_supplement(session_id, run_id)
        return _job_response(job)
    except LegacyGradingModeError as exc:
        raise ApiError(
            409,
            "legacy_grading_mode_disabled",
            LEGACY_GRADING_MODE_MESSAGE,
        ) from exc
    except ActiveJobExistsError as exc:
        raise ApiError(
            409,
            "grading_job_already_active",
            "A grading job is already active",
        ) from exc
    except UnsupportedJobTypeError as exc:
        raise ApiError(
            404,
            "job_type_not_supported",
            "Job type is not supported",
        ) from exc
    except GradingConfigChangedError as exc:
        raise ApiError(
            409,
            "grading_config_changed",
            "Grading configuration changed; newly matched scans cannot be supplemented",
        ) from exc
    except ScanGradingWorkspaceError as exc:
        raise ApiError(
            409,
            "grading_run_not_supplementable",
            "Grading run cannot supplement newly matched scans",
        ) from exc


@router.post(
    "/sessions/{session_id}/grading/runs/{run_id}/cancel",
    response_model=GradingRunSummaryResponse,
)
def cancel_session_grading(
    session_id: int,
    run_id: int,
    request: GradingRunCancelRequest,
    db: GradingRepositoryAccess = Depends(get_grading_db),
    manager: JobManager = Depends(get_job_manager),
    workspace: ScanGradingWorkspace = Depends(get_scan_grading_workspace),
) -> GradingRunSummaryResponse:
    _require_session(db.sessions, session_id)
    summary = workspace.get_grading_run(session_id)
    if summary is None or int(summary["run_id"]) != int(run_id):
        raise ApiError(409, "grading_run_not_cancellable", "Grading run cannot be cancelled")
    active_job_id = summary.get("job_id")
    job = manager.get(request.job_id) if request.job_id is not None else None
    if summary["state"] in {"running", "pause_requested", "cancel_requested"}:
        if (
            job is None
            or job.id != active_job_id
            or job.job_type != "grading_run"
            or int(job.payload.get("session_id") or 0) != int(session_id)
        ):
            raise ApiError(409, "grading_job_run_mismatch", "Grading job does not belong to this run")
        manager.cancel(job.id)
        updated = manager.get(job.id)
        confirmed = bool(updated and updated.status == "cancelled")
    elif summary["state"] in {"paused", "interrupted"}:
        confirmed = True
    else:
        raise ApiError(409, "grading_run_not_cancellable", "Grading run cannot be cancelled")
    try:
        summary = workspace.record_cancel_request(
            session_id,
            run_id,
            job.id if job is not None else None,
            confirmed=confirmed,
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
    db: GradingRepositoryAccess = Depends(get_grading_db),
    manager: JobManager = Depends(get_job_manager),
    workspace: ScanGradingWorkspace = Depends(get_scan_grading_workspace),
) -> JobResponse:
    _require_session(db.sessions, session_id)
    request = request or GradingRunRequest()
    _require_exam_intake_complete(db, session_id)
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
        _require_current_preflight_config(
            session_id=session_id,
            db=db,
            workspace=workspace,
        )
        try:
            job = workspace.submit_start(
                session_id,
                grading_mode=request.grading_mode,
                upload_revision=request.upload_revision,
                decision_revision=request.decision_revision,
                confirm_pending_issues=request.confirm_pending_issues,
                enhance_images=request.enhance_images,
                max_workers=request.max_workers,
                requests_per_minute=request.requests_per_minute,
            )
        except ScanMatchConflictError as exc:
            raise ApiError(409, "scan_match_conflict", str(exc)) from exc
        except PendingScanIssuesError as exc:
            raise ApiError(
                409,
                "pending_scan_issues_not_confirmed",
                "Confirm the pending scan issues before grading",
            ) from exc
        except UploadBatchRevisionError as exc:
            raise ApiError(409, "grading_input_changed", "Grading input changed") from exc
        except GradingConfigChangedError as exc:
            raise ApiError(
                409,
                "grading_preflight_stale",
                "The grading configuration changed; run scan preflight again",
            ) from exc
        except LegacyGradingModeError as exc:
            raise ApiError(
                409,
                "legacy_grading_mode_disabled",
                LEGACY_GRADING_MODE_MESSAGE,
            ) from exc
        except ScanGradingWorkspaceError as exc:
            raise ApiError(409, "grading_input_not_ready", "Grading input is not ready") from exc
        except ActiveJobExistsError as exc:
            raise ApiError(
                409,
                "grading_job_already_active",
                "A grading job is already active for this session",
            ) from exc
        except UnsupportedJobTypeError as exc:
            raise ApiError(
                404,
                "job_type_not_supported",
                "Job type is not supported",
                {"job_type": "grading_run"},
            ) from exc
        return _job_response(job)

    if request.resume_run_id is not None:
        from grading_run_store import GradingRunStore

        stored_run = GradingRunStore(db.db_path).get_run(int(request.resume_run_id))
        if stored_run is not None and str(stored_run.grading_mode) != "ai":
            raise ApiError(
                409,
                "legacy_grading_mode_disabled",
                LEGACY_GRADING_MODE_MESSAGE,
            )
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
