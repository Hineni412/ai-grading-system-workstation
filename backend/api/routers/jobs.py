from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from fastapi import APIRouter, Depends, Query

from backend.api.app import ApiError
from backend.api.dependencies import get_job_manager
from backend.api.schemas.jobs import (
    JobResponse,
    JobStatusBatchItem,
    JobStatusBatchRequest,
    JobStatusBatchResponse,
    JobSubmitRequest,
    JobSummaryListResponse,
    JobSummaryResponse,
)
from backend.jobs.manager import JobManager, UnsupportedJobTypeError
from backend.jobs.store import JobRecord
from backend.public_data import contains_sensitive_key

router = APIRouter(prefix="/api", tags=["jobs"])

_DOWNLOADABLE_JOB_TYPES = {
    "personalized_handout_export",
    "ops_backup",
    "ops_transfer_export",
    "personal_report_bundle",
    "wrong_question_export",
    "assembly_export",
    "report_export",
}


def _job_response(job: JobRecord) -> JobResponse:
    return JobResponse(
        id=job.id,
        job_type=job.job_type,
        payload=dict(job.payload),
        result=_job_result(job),
        status=job.status,
        progress=job.progress,
        stage=job.stage,
        detail=job.detail,
        error=job.error or None,
        cancel_requested=job.cancel_requested,
        created_at=job.created_at,
        started_at=job.started_at,
        updated_at=job.updated_at,
        finished_at=job.finished_at,
    )


def _job_result(job: JobRecord) -> dict[str, Any]:
    result = dict(job.result)
    if (
        job.job_type in _DOWNLOADABLE_JOB_TYPES
        and job.status == "succeeded"
        and str(job.result.get("file_path") or "").strip()
    ):
        filename = _safe_filename(
            job.result.get("filename") or job.result.get("file_path")
        )
        if filename:
            result["filename"] = filename
        result["download_url"] = f"/api/jobs/{job.id}/download"
    return result


def _job_summary_response(job: JobRecord) -> JobSummaryResponse:
    return JobSummaryResponse(
        id=job.id,
        job_type=job.job_type,
        status=job.status,
        progress=job.progress,
        stage=job.stage,
        detail=job.detail,
        created_at=job.created_at,
        started_at=job.started_at,
        updated_at=job.updated_at,
        finished_at=job.finished_at,
    )


def _safe_filename(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if "\\" in text:
        return PureWindowsPath(text).name
    return PurePosixPath(text).name


def _require_job(manager: JobManager, job_id: int) -> JobRecord:
    job = manager.get(int(job_id))
    if job is None:
        raise ApiError(
            404,
            "job_not_found",
            "Job not found",
            {"job_id": int(job_id)},
        )
    return job


@router.get("/jobs", response_model=JobSummaryListResponse)
def list_jobs(
    session_id: int | None = Query(None, gt=0),
    job_type: list[str] = Query(default=[]),
    status: list[str] = Query(default=[]),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    manager: JobManager = Depends(get_job_manager),
) -> JobSummaryListResponse:
    try:
        jobs, total = manager.list(
            session_id=session_id,
            job_types=tuple(job_type),
            statuses=tuple(status),
            limit=page_size,
            offset=(page - 1) * page_size,
        )
    except ValueError as exc:
        raise ApiError(
            422,
            "invalid_job_filter",
            "Job filter is invalid",
            {},
        ) from exc
    return JobSummaryListResponse(
        items=[_job_summary_response(job) for job in jobs],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size,
    )


@router.post("/jobs/status-batch", response_model=JobStatusBatchResponse)
def get_job_status_batch(
    request: JobStatusBatchRequest,
    manager: JobManager = Depends(get_job_manager),
) -> JobStatusBatchResponse:
    """One query for many pollers; per-job shape matches GET /jobs/{id}."""
    unique_ids = list(dict.fromkeys(int(value) for value in request.ids))
    records = manager.store.get_jobs(unique_ids)
    return JobStatusBatchResponse(
        items=[
            JobStatusBatchItem(
                id=job_id,
                found=job_id in records,
                job=(
                    _job_response(records[job_id])
                    if job_id in records
                    else None
                ),
            )
            for job_id in unique_ids
        ],
    )


@router.post("/jobs/{job_type}", response_model=JobResponse, status_code=202)
def submit_job(
    job_type: str,
    request: JobSubmitRequest,
    manager: JobManager = Depends(get_job_manager),
) -> JobResponse:
    clean_job_type = str(job_type).strip()
    if clean_job_type in {
        "config_generation",
        "question_import",
        "chapter_type_organize",
        "question_bank_sync",
        "tagging_sync",
        "taxonomy_suggestion",
        "skill_candidate",
        "assembly_export",
        "wrong_question_export",
        "personalized_handout_export",
        "ops_backup",
        "ops_restore_prepare",
        "ops_migration_prepare",
        "ops_transfer_import_prepare",
        "ops_transfer_export",
        "workspace_ai.run",
    }:
        raise ApiError(
            422,
            "dedicated_job_endpoint_required",
            "Use the dedicated endpoint for this job type",
            {"job_type": clean_job_type},
        )
    if contains_sensitive_key(request.payload):
        raise ApiError(
            422,
            "unsafe_job_payload",
            "Job payload contains fields that must not be persisted",
            {"job_type": str(job_type)},
        )
    try:
        job = manager.submit(job_type, request.payload)
    except UnsupportedJobTypeError as exc:
        raise ApiError(
            404,
            "job_type_not_supported",
            "Job type is not supported",
            {"job_type": str(job_type)},
        ) from exc
    return _job_response(job)


@router.get("/jobs/{job_id}", response_model=JobResponse)
def get_job(
    job_id: int,
    manager: JobManager = Depends(get_job_manager),
) -> JobResponse:
    return _job_response(_require_job(manager, job_id))


@router.post("/jobs/{job_id}/cancel", response_model=JobResponse)
def cancel_job(
    job_id: int,
    manager: JobManager = Depends(get_job_manager),
) -> JobResponse:
    _require_job(manager, job_id)
    manager.cancel(int(job_id))
    return _job_response(_require_job(manager, job_id))
