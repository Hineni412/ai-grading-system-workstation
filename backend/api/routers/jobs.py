from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from fastapi import APIRouter, Depends

from backend.api.app import ApiError
from backend.api.dependencies import get_job_manager
from backend.api.schemas.jobs import JobResponse, JobSubmitRequest
from backend.jobs.manager import JobManager, UnsupportedJobTypeError
from backend.jobs.store import JobRecord
from backend.public_data import contains_sensitive_key, sanitize_public_mapping


router = APIRouter(prefix="/api", tags=["jobs"])

def _job_response(job: JobRecord) -> JobResponse:
    return JobResponse(
        id=job.id,
        job_type=job.job_type,
        payload=public_job_payload(job),
        result=public_job_result(job),
        status=job.status,
        progress=job.progress,
        stage=job.stage,
        detail=job.detail,
        error=public_job_error(job),
        cancel_requested=job.cancel_requested,
        created_at=job.created_at,
        started_at=job.started_at,
        updated_at=job.updated_at,
        finished_at=job.finished_at,
    )


def public_job_result(job: JobRecord) -> dict[str, Any]:
    if job.job_type in {"ops_backup", "ops_transfer_export"}:
        allowed = ("operation_id", "operation", "outcome", "filename", "file_count")
        result = {key: job.result[key] for key in allowed if key in job.result}
        if job.status == "succeeded" and str(job.result.get("file_path") or "").strip():
            filename = _safe_filename(
                job.result.get("filename") or job.result.get("file_path")
            )
            if filename:
                result["filename"] = filename
            result["download_url"] = f"/api/jobs/{job.id}/download"
        return sanitize_public_mapping(result)
    if job.job_type == "training_export":
        result: dict[str, Any] = {}
        for key in ("task_id", "variant_id", "export_ids"):
            if job.result.get(key) is not None:
                result[key] = job.result[key]
        if (
            job.status == "succeeded"
            and str(job.result.get("file_path") or "").strip()
        ):
            filename = _safe_filename(
                job.result.get("filename") or job.result.get("file_path")
            )
            if filename:
                result["filename"] = filename
            result["download_url"] = f"/api/jobs/{job.id}/download"
        return sanitize_public_mapping(result)
    if job.job_type in {"question_import", "tagging_sync"}:
        allowed = (
            "request_id",
            "outcome",
            "imported_papers",
            "question_count",
            "requested_count",
            "skipped_complete_count",
            "tagged_count",
            "failed_count",
            "successful_question_ids",
            "failed_question_ids",
            "failure_category",
            "failures",
            "retryable",
        )
        return sanitize_public_mapping(
            {key: job.result[key] for key in allowed if key in job.result}
        )
    if job.job_type == "config_generation":
        allowed = (
            "session_id",
            "outcome",
            "total_questions",
            "generated_questions",
            "failed_count",
            "failed_question_ids",
            "retryable",
        )
        return sanitize_public_mapping(
            {key: job.result[key] for key in allowed if key in job.result}
        )
    if (
        job.job_type == "report_export"
        and job.status == "succeeded"
        and str(job.result.get("file_path") or "").strip()
    ):
        result: dict[str, Any] = {}
        if job.result.get("session_id") is not None:
            result["session_id"] = job.result["session_id"]
        filename = _safe_filename(
            job.result.get("filename") or job.result.get("file_path")
        )
        if filename:
            result["filename"] = filename
        result["download_url"] = f"/api/jobs/{job.id}/download"
        return result
    return sanitize_public_mapping(job.result)


def public_job_error(job: JobRecord) -> str | None:
    if not job.error:
        return None
    if job.status == "failed":
        return "Job failed; see local logs for details."
    return "Job ended with an internal error; see local logs for details."


def public_job_payload(job: JobRecord) -> dict[str, Any]:
    if job.job_type == "training_export":
        allowed = (
            "task_id",
            "variant_id",
            "format",
            "audience",
            "retry_of_job_id",
        )
        return sanitize_public_mapping(
            {key: job.payload[key] for key in allowed if key in job.payload}
        )
    if job.job_type.startswith("ops_"):
        allowed = ("operation_id", "operation")
        return sanitize_public_mapping(
            {key: job.payload[key] for key in allowed if key in job.payload}
        )
    if job.job_type == "question_import":
        allowed = ("request_id", "retry_of_job_id")
        return sanitize_public_mapping(
            {key: job.payload[key] for key in allowed if key in job.payload}
        )
    if job.job_type == "tagging_sync":
        allowed = ("question_ids", "source_job_id", "retry_of_job_id")
        return sanitize_public_mapping(
            {key: job.payload[key] for key in allowed if key in job.payload}
        )
    if job.job_type == "config_generation":
        allowed = (
            "session_id",
            "mode",
            "source_job_id",
            "retry_question_ids",
        )
        return sanitize_public_mapping(
            {key: job.payload[key] for key in allowed if key in job.payload}
        )
    return sanitize_public_mapping(job.payload)


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
        "tagging_sync",
        "training_export",
        "ops_backup",
        "ops_restore_prepare",
        "ops_migration_prepare",
        "ops_transfer_import_prepare",
        "ops_transfer_export",
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
