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
    "并行生成评分标准": "正在并行生成评分依据批次。",
    "AI 统一配分": "评分依据批次已完成，正在统一配分。",
    "AI 统一配分失败": "模型已返回或调用已结束，但统一配分校验未通过。",
    "本地统一配分": "评分依据批次已完成，正在本地统一配分。",
    "本地统一配分失败": "评分依据批次已完成，但本地统一配分校验未通过。",
}
_CONFIG_TRUNCATION_PUBLIC_ERRORS = {
    "模型因输出长度上限停止": (
        "模型因输出长度上限停止，当前批次结果不完整；未发布配置，也未自动重试。"
        "请手动重试失败批次；已经成功的批次不会重复请求。"
    ),
    "模型返回的 JSON 结构未闭合": (
        "模型返回的 JSON 结构未闭合，当前批次结果疑似被截断；"
        "未发布配置，也未自动重试。请手动重试失败批次；"
        "已经成功的批次不会重复请求。"
    ),
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
    if job.job_type.startswith("workspace_ai."):
        details = {
            "queued": "工作台 AI 任务已排队。",
            "workspace_ai": "工作台 AI 任务正在处理。",
        }
        if job.status == "succeeded":
            return "工作台 AI 任务的本地处理已结束。"
        if job.status == "failed":
            return "工作台 AI 任务没有完成；请从任务抽屉查看安全说明。"
        if job.status == "cancelled":
            return "工作台 AI 任务的本地后续处理已停止。"
        return details.get(job.stage, "")
    if (
        job.job_type == "config_generation"
        and str(job.payload.get("source_id") or "").strip()
    ):
        if job.status == "succeeded":
            if job.result.get("exam_intake_complete") is False:
                return "题库入库未完成，统一赋分尚未开始；已完成的分析保存在本机。"
            if job.result.get("outcome") == "partial":
                return "评分依据生成流程已结束，但本地校验尚未全部通过，未发布正式版本。"
            return "Grading configuration generated."
        return _SOURCE_CONFIG_PUBLIC_DETAILS.get(job.stage, "")
    return job.detail


def public_job_result(job: JobRecord) -> dict[str, Any]:
    if job.job_type.startswith("workspace_ai."):
        allowed = ("task_id", "status")
        return sanitize_public_mapping(
            {key: job.result[key] for key in allowed if key in job.result}
        )
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
            "complete_tagged_count",
            "evidence_count",
            "evidence_succeeded_question_ids",
            "evidence_failed_question_ids",
            "criteria_count",
            "criteria_succeeded_question_ids",
            "criteria_failed_question_ids",
            "criteria_needs_review_count",
            "criteria_needs_review_question_ids",
            "analysis_contract",
            "failed_count",
            "successful_question_ids",
            "failed_question_ids",
            "taxonomy_review_count",
            "taxonomy_review_question_ids",
            "taxonomy_review_source_refs",
            "review_count",
            "review_question_ids",
            "proposal_ids",
            "failure_category",
            "failures",
            "retryable",
            "restore_required",
            "restore_paper_id",
            "exact_duplicate_count",
            "analysis_reused_count",
            "near_duplicate_hints",
        )
        return sanitize_public_mapping(
            {key: job.result[key] for key in allowed if key in job.result}
        )
    if job.job_type == "criterion_backfill":
        allowed = (
            "run_id",
            "status",
            "successful_question_ids",
            "failed_question_ids",
            "retryable",
        )
        return sanitize_public_mapping(
            {key: job.result[key] for key in allowed if key in job.result}
        )
    if job.job_type == "question_bank_sync":
        allowed = (
            "session_id",
            "outcome",
            "imported_count",
            "question_count",
            "tagged_count",
            "complete_tagged_count",
            "evidence_count",
            "criteria_count",
            "criteria_failed_question_ids",
            "linked_count",
            "failed_count",
            "successful_question_ids",
            "failed_question_ids",
            "unresolved_question_ids",
            "review_count",
            "proposal_ids",
            "taxonomy_review_count",
            "taxonomy_review_question_ids",
            "taxonomy_review_source_refs",
            "failure_category",
            "retryable",
        )
        return sanitize_public_mapping(
            {key: job.result[key] for key in allowed if key in job.result}
        )
    if job.job_type == "taxonomy_suggestion":
        allowed = (
            "run_id",
            "status",
            "taxonomy_revision",
            "progress",
            "stale",
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
            "total_batch_count",
            "failed_count",
            "failed_question_ids",
            "failed_batch_count",
            "failed_batches",
            "uncertain_count",
            "uncertain_question_ids",
            "needs_teacher_resolution",
            "taxonomy_review_count",
            "taxonomy_review_question_ids",
            "local_json_repairs",
            "local_structure_repairs",
            "score_allocation_pending",
            "score_allocation_failed",
            "score_allocation_error",
            "score_allocation_failure_category",
            "question_bank_sync_requested",
            "question_bank_sync_state",
            "question_bank_sync_job_id",
            "question_bank_sync_error",
            "question_bank_imported_count",
            "question_bank_tagged_count",
            "question_bank_criteria_count",
            "exam_intake_complete",
            "exam_intake_category",
            "exam_intake_error",
            "exam_intake_failed_question_ids",
            "exam_intake_retryable",
            "config_revision",
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
    if job.job_type == "ai_assembly_spec":
        # 只暴露细目表与模型名；prompt 与题库概况原文不进 result。
        allowed = ("spec", "model_name")
        return sanitize_public_mapping(
            {key: job.result[key] for key in allowed if key in job.result}
        )
    return sanitize_public_mapping(job.result)


def public_job_error(job: JobRecord) -> str | None:
    if not job.error:
        return None
    if job.job_type.startswith("workspace_ai."):
        return "工作台 AI 任务没有完成；请从任务抽屉查看安全说明。"
    if job.job_type == "config_generation" and job.status == "failed":
        for prefix, public_error in _CONFIG_TRUNCATION_PUBLIC_ERRORS.items():
            if job.error.startswith(prefix):
                return public_error
    if job.status == "failed":
        return "Job failed; see local logs for details."
    return "Job ended with an internal error; see local logs for details."


def public_job_payload(job: JobRecord) -> dict[str, Any]:
    if job.job_type.startswith("workspace_ai."):
        task_id = job.payload.get("task_id")
        return sanitize_public_mapping(
            {"task_id": task_id} if task_id is not None else {}
        )
    if job.job_type == "report_export":
        allowed = (
            "session_id",
            "report_type",
            "score_revision",
            "report_options_fingerprint",
            "score_excel_options",
            "retry_of_job_id",
        )
        return sanitize_public_mapping(
            {key: job.payload[key] for key in allowed if key in job.payload}
        )
    if job.job_type == "assembly_export":
        allowed = (
            "draft_revision",
            "format",
            "question_count",
            "source",
            "retry_of_job_id",
        )
        return sanitize_public_mapping(
            {key: job.payload[key] for key in allowed if key in job.payload}
        )
    if job.job_type == "ai_assembly_spec":
        # 脱敏红线：不暴露 prompt、题库概况、口语与新指令原文。
        raw_request = job.payload.get("request")
        request = raw_request if isinstance(raw_request, dict) else {}
        raw_type_counts = request.get("type_counts")
        locked_ids = request.get("locked_question_ids")
        template_paper_id = request.get("template_paper_id")
        public: dict[str, Any] = {
            "type_counts": (
                {
                    str(key): value
                    for key, value in raw_type_counts.items()
                }
                if isinstance(raw_type_counts, dict)
                else {}
            ),
            "has_free_text": bool(str(request.get("free_text") or "").strip()),
            "locked_count": (
                len(locked_ids) if isinstance(locked_ids, (list, tuple)) else 0
            ),
        }
        if template_paper_id is not None:
            public["template_paper_id"] = template_paper_id
        return sanitize_public_mapping(public)
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
        allowed = (
            "question_ids",
            "curriculum_volume_id",
            "source_job_id",
            "retry_of_job_id",
            "retry_evidence_question_ids",
            "force_retag_question_ids",
            "client_request_token",
        )
        return sanitize_public_mapping(
            {key: job.payload[key] for key in allowed if key in job.payload}
        )
    if job.job_type == "criterion_backfill":
        allowed = ("run_id", "retry_of_run_id")
        return sanitize_public_mapping(
            {key: job.payload[key] for key in allowed if key in job.payload}
        )
    if job.job_type == "question_bank_sync":
        allowed = (
            "session_id",
            "mode",
            "config_revision",
            "curriculum_volume_id",
            "retry_of_job_id",
            "question_ids",
        )
        return sanitize_public_mapping(
            {key: job.payload[key] for key in allowed if key in job.payload}
        )
    if job.job_type == "taxonomy_suggestion":
        allowed = ("run_id", "operation", "retry_of_job_id")
        return sanitize_public_mapping(
            {key: job.payload[key] for key in allowed if key in job.payload}
        )
    if job.job_type == "config_generation":
        allowed = ("session_id", "mode")
        if str(job.payload.get("source_id") or "").strip():
            allowed += (
                "generation_mode",
                "source_id",
                "source_revision",
                "sync_to_question_bank",
                "curriculum_volume_id",
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
        "question_bank_sync",
        "tagging_sync",
        "taxonomy_suggestion",
        "assembly_export",
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
