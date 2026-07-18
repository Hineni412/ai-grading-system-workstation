from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from fastapi import APIRouter, Depends, Query

from backend.api.app import ApiError
from backend.api.dependencies import get_job_manager
from backend.api.schemas.jobs import (
    JobResponse,
    JobSubmitRequest,
    JobSummaryListResponse,
    JobSummaryResponse,
)
from backend.jobs.manager import JobManager, UnsupportedJobTypeError
from backend.jobs.store import JobRecord
from backend.public_data import (
    contains_sensitive_key,
    sanitize_public_diagnostic_text,
    sanitize_public_mapping,
)


router = APIRouter(prefix="/api", tags=["jobs"])

_SOURCE_CONFIG_PUBLIC_DETAILS = {
    "queued": "Waiting to generate grading configuration.",
    "running": "Generating grading configuration.",
    "config_generation": "Generating grading configuration.",
    "Split paper": "Preparing source questions.",
    "Submit per-question requests": "Generating grading configuration.",
    "Parse question": "Generating grading configuration.",
    "Assemble rubric": "Finalizing grading configuration.",
    "AI 赋分": "Finalizing grading configuration.",
    "AI 赋分完成": "Finalizing grading configuration.",
    "AI 赋分失败，使用本地均分兜底": "Finalizing grading configuration.",
    "Word 整卷单次请求": "Generating grading configuration.",
    "旧版整卷生成": "Generating grading configuration.",
    "旧版整卷修复": "Generating grading configuration.",
    "PDF 整卷视觉单次请求": "Generating grading configuration.",
}

def _job_response(job: JobRecord) -> JobResponse:
    return JobResponse(
        id=job.id,
        job_type=job.job_type,
        payload=public_job_payload(job),
        result=public_job_result(job),
        status=job.status,
        progress=job.progress,
        stage=job.stage,
        detail=public_job_detail(job),
        error=public_job_error(job),
        cancel_requested=job.cancel_requested,
        created_at=job.created_at,
        started_at=job.started_at,
        updated_at=job.updated_at,
        finished_at=job.finished_at,
    )


def _job_summary_response(job: JobRecord) -> JobSummaryResponse:
    detail = sanitize_public_diagnostic_text(public_job_detail(job)) or ""
    return JobSummaryResponse(
        id=job.id,
        job_type=job.job_type,
        status=job.status,
        progress=job.progress,
        stage=job.stage,
        detail=str(detail),
        created_at=job.created_at,
        started_at=job.started_at,
        updated_at=job.updated_at,
        finished_at=job.finished_at,
    )


def public_job_detail(job: JobRecord) -> str:
    if (
        job.job_type == "config_generation"
        and str(job.payload.get("source_id") or "").strip()
    ):
        if job.status == "succeeded":
            return "Grading configuration generated."
        return _SOURCE_CONFIG_PUBLIC_DETAILS.get(job.stage, "")
    return job.detail


def public_job_result(job: JobRecord) -> dict[str, Any]:
    if job.job_type.startswith("ops_"):
        allowed = (
            "operation_id",
            "operation",
            "outcome",
            "filename",
            "file_count",
            "backup_filename",
            "target",
        )
        result = {key: job.result[key] for key in allowed if key in job.result}
        if (
            job.job_type in {"ops_backup", "ops_transfer_export"}
            and job.status == "succeeded"
            and str(job.result.get("file_path") or "").strip()
        ):
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
    if job.job_type == "assembly_export":
        result = {
            key: job.result[key]
            for key in (
                "record_id",
                "format",
                "question_count",
                "draft_cleared",
            )
            if key in job.result
        }
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
            "mapping_status",
            "mapping_message",
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
        for key in ("session_id", "report_type", "score_revision"):
            if job.result.get(key) is not None:
                result[key] = job.result[key]
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
    if job.job_type == "report_export":
        allowed = ("session_id", "report_type", "score_revision", "retry_of_job_id")
        return sanitize_public_mapping(
            {key: job.payload[key] for key in allowed if key in job.payload}
        )
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
    if job.job_type == "assembly_export":
        allowed = ("draft_revision", "format", "question_count", "retry_of_job_id")
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
        allowed = ("session_id", "mode")
        if str(job.payload.get("source_id") or "").strip():
            allowed += ("generation_mode", "source_id", "source_revision")
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
        "assembly_export",
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
