from __future__ import annotations

import hashlib
import json
import sqlite3
from math import ceil
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import ValidationError

from backend.api.app import ApiError, ErrorResponse
from backend.api.dependencies import (
    get_diagnosis_profile_service,
    get_job_manager,
    get_practice_plan_service,
    get_training_task_service,
)
from backend.api.routers.jobs import _job_response
from backend.api.schemas.jobs import JobResponse
from backend.api.schemas.training import (
    TrainingDiagnosisRequest,
    TrainingDiagnosisResponse,
    TrainingExportSubmitRequest,
    TrainingPlanRequest,
    TrainingPlanResponse,
    TrainingTaskConfirmRequest,
    TrainingTaskDetail,
    TrainingTaskListResponse,
    TrainingTaskSummary,
)
from backend.jobs.manager import JobManager, UnsupportedJobTypeError
from backend.public_data import sanitize_public_mapping
from integration.diagnosis_profile_service import DiagnosisProfileService
from question_bank.recommendation.practice_plan_service import PracticePlanService
from question_bank.services.training_task_service import TrainingTaskService


router = APIRouter(prefix="/api/training", tags=["training"])
TRAINING_DATABASE_RESPONSES = {
    503: {
        "model": ErrorResponse,
        "description": "Training data is temporarily unavailable",
    }
}


@router.post(
    "/diagnosis",
    response_model=TrainingDiagnosisResponse,
    response_model_exclude_none=True,
    responses=TRAINING_DATABASE_RESPONSES,
)
def build_training_diagnosis(
    body: TrainingDiagnosisRequest,
    service: DiagnosisProfileService = Depends(get_diagnosis_profile_service),
) -> TrainingDiagnosisResponse:
    try:
        diagnosis = service.build_profiles(
            scope=body.scope.model_dump(exclude_none=True),
            exam_scope=body.exam_scope.model_dump(exclude_none=True),
        )
    except ValueError as exc:
        raise ApiError(
            422,
            "training_scope_invalid",
            "Training scope is invalid",
        ) from exc
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(
            503,
            "training_database_unavailable",
            "Training data is temporarily unavailable",
        ) from exc

    public = _public_training_mapping(diagnosis)
    if public.get("diagnosis_identity") != "question_tag":
        raise ApiError(
            422,
            "training_scope_invalid",
            "Training scope is invalid",
        )
    return TrainingDiagnosisResponse.model_validate(public)


@router.post(
    "/plans/preview",
    response_model=TrainingPlanResponse,
    responses={
        422: {"model": ErrorResponse, "description": "Training plan is invalid"},
        **TRAINING_DATABASE_RESPONSES,
    },
)
def preview_training_plan(
    body: TrainingPlanRequest,
    diagnosis_service: DiagnosisProfileService = Depends(
        get_diagnosis_profile_service
    ),
    practice_service: PracticePlanService = Depends(get_practice_plan_service),
) -> TrainingPlanResponse:
    plan = _generate_public_plan(
        body,
        diagnosis_service=diagnosis_service,
        practice_service=practice_service,
    )
    return TrainingPlanResponse(
        plan_revision=_plan_revision(plan),
        plan=plan,
    )


@router.post(
    "/tasks",
    response_model=TrainingTaskDetail,
    response_model_exclude_none=True,
    status_code=201,
    responses={
        409: {
            "model": ErrorResponse,
            "description": "Training confirmation conflicts with the current plan",
        },
        422: {"model": ErrorResponse, "description": "Training plan is invalid"},
        **TRAINING_DATABASE_RESPONSES,
    },
)
def confirm_training_task(
    body: TrainingTaskConfirmRequest,
    diagnosis_service: DiagnosisProfileService = Depends(
        get_diagnosis_profile_service
    ),
    practice_service: PracticePlanService = Depends(get_practice_plan_service),
    task_service: TrainingTaskService = Depends(get_training_task_service),
) -> TrainingTaskDetail:
    plan = _generate_public_plan(
        body,
        diagnosis_service=diagnosis_service,
        practice_service=practice_service,
    )
    current_revision = _plan_revision(plan)
    if current_revision != body.expected_plan_revision:
        _raise_confirmation_conflict(current_revision)
    if not any(
        isinstance(variant, dict) and bool(variant.get("items"))
        for variant in plan.get("variants", [])
    ):
        raise ApiError(
            422,
            "training_plan_invalid",
            "Training plan does not contain any questions",
        )

    task_code = f"TRN-CFM-{body.confirmation_id.hex.upper()}"
    existing = _task_by_code(task_service, task_code)
    if existing is not None:
        return _confirmed_existing_task(existing, current_revision)

    plan_for_storage = dict(plan)
    plan_for_storage["generation_config"] = {
        **dict(plan.get("generation_config") or {}),
        "plan_revision": current_revision,
    }
    try:
        created = task_service.create_task(
            plan_for_storage,
            created_by="teacher",
            task_code=task_code,
        )
        task = task_service.get_task(created.id)
    except sqlite3.IntegrityError:
        task = _task_by_code(task_service, task_code)
        if task is None:
            raise
        return _confirmed_existing_task(task, current_revision)
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(
            503,
            "training_database_unavailable",
            "Training data is temporarily unavailable",
        ) from exc
    return _task_detail_response(task)


@router.get(
    "/tasks",
    response_model=TrainingTaskListResponse,
    responses=TRAINING_DATABASE_RESPONSES,
)
def list_training_tasks(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    task_service: TrainingTaskService = Depends(get_training_task_service),
) -> TrainingTaskListResponse:
    try:
        rows, total = task_service.list_tasks_page(page=page, page_size=page_size)
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(
            503,
            "training_database_unavailable",
            "Training data is temporarily unavailable",
        ) from exc
    items = [
        TrainingTaskSummary.model_validate(_public_training_mapping(row))
        for row in rows
    ]
    return TrainingTaskListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=max(1, ceil(total / page_size)),
    )


@router.get(
    "/tasks/{task_id}",
    response_model=TrainingTaskDetail,
    response_model_exclude_none=True,
    responses={
        404: {"model": ErrorResponse, "description": "Training task not found"},
        **TRAINING_DATABASE_RESPONSES,
    },
)
def get_training_task(
    task_id: int,
    task_service: TrainingTaskService = Depends(get_training_task_service),
) -> TrainingTaskDetail:
    try:
        task = task_service.get_task(task_id)
    except KeyError as exc:
        raise ApiError(
            404,
            "training_task_not_found",
            "Training task not found",
            {"task_id": int(task_id)},
        ) from exc
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(
            503,
            "training_database_unavailable",
            "Training data is temporarily unavailable",
        ) from exc
    return _task_detail_response(task)


@router.post(
    "/tasks/{task_id}/exports",
    response_model=JobResponse,
    status_code=202,
    responses={
        404: {"model": ErrorResponse, "description": "Training task not found"},
        422: {"model": ErrorResponse, "description": "Training export request is invalid"},
        **TRAINING_DATABASE_RESPONSES,
    },
)
def submit_training_export(
    task_id: int,
    body: TrainingExportSubmitRequest,
    task_service: TrainingTaskService = Depends(get_training_task_service),
    manager: JobManager = Depends(get_job_manager),
) -> JobResponse:
    task = _load_export_task(task_service, task_id)
    _validate_export_variant(task, body.variant_id)
    payload = {"task_id": int(task_id), **body.model_dump(exclude_none=True)}
    try:
        return _job_response(manager.submit("training_export", payload))
    except UnsupportedJobTypeError as exc:
        raise ApiError(
            404,
            "job_type_not_supported",
            "Job type is not supported",
            {"job_type": "training_export"},
        ) from exc


@router.post(
    "/exports/jobs/{job_id}/retry",
    response_model=JobResponse,
    status_code=202,
    responses={
        404: {"model": ErrorResponse, "description": "Training export job not found"},
        409: {"model": ErrorResponse, "description": "Training export cannot be retried"},
        **TRAINING_DATABASE_RESPONSES,
    },
)
def retry_training_export(
    job_id: int,
    task_service: TrainingTaskService = Depends(get_training_task_service),
    manager: JobManager = Depends(get_job_manager),
) -> JobResponse:
    source = manager.get(int(job_id))
    if source is None or source.job_type != "training_export":
        raise ApiError(
            404,
            "training_export_job_not_found",
            "Training export job not found",
            {"job_id": int(job_id)},
        )
    if source.status not in {"failed", "cancelled"}:
        raise ApiError(
            409,
            "training_export_retry_not_available",
            "Training export job cannot be retried",
            {"job_id": int(job_id)},
        )
    try:
        task_id = int(source.payload["task_id"])
        body = TrainingExportSubmitRequest.model_validate(
            {
                key: source.payload[key]
                for key in ("variant_id", "format", "audience")
                if key in source.payload
            }
        )
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        raise ApiError(
            409,
            "training_export_retry_not_available",
            "Training export job cannot be retried",
            {"job_id": int(job_id)},
        ) from exc
    task = _load_export_task(task_service, task_id)
    _validate_export_variant(task, body.variant_id)
    payload = {
        "task_id": task_id,
        **body.model_dump(exclude_none=True),
        "retry_of_job_id": int(job_id),
    }
    try:
        return _job_response(manager.submit("training_export", payload))
    except UnsupportedJobTypeError as exc:
        raise ApiError(
            404,
            "job_type_not_supported",
            "Job type is not supported",
            {"job_type": "training_export"},
        ) from exc


def _load_export_task(
    task_service: TrainingTaskService,
    task_id: int,
) -> dict[str, object]:
    try:
        return task_service.get_task(int(task_id))
    except KeyError as exc:
        raise ApiError(
            404,
            "training_task_not_found",
            "Training task not found",
            {"task_id": int(task_id)},
        ) from exc
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(
            503,
            "training_database_unavailable",
            "Training data is temporarily unavailable",
        ) from exc


def _validate_export_variant(
    task: dict[str, object],
    variant_id: int | None,
) -> None:
    if variant_id is None:
        return
    variants = task.get("variants")
    if not isinstance(variants, list) or not any(
        isinstance(item, dict) and int(item.get("id", 0)) == int(variant_id)
        for item in variants
    ):
        raise ApiError(
            422,
            "training_export_request_invalid",
            "Training export request is invalid",
            {"variant_id": int(variant_id)},
        )


def _generate_public_plan(
    body: TrainingPlanRequest,
    *,
    diagnosis_service: DiagnosisProfileService,
    practice_service: PracticePlanService,
) -> dict[str, object]:
    try:
        diagnosis = diagnosis_service.build_profiles(
            scope=body.scope.model_dump(exclude_none=True),
            exam_scope=body.exam_scope.model_dump(exclude_none=True),
        )
        if diagnosis.get("diagnosis_identity") != "question_tag":
            raise ValueError("unsupported diagnosis identity")
        plan = practice_service.generate(
            diagnosis,
            variant_mode=body.variant_mode,
            teacher_groups=body.teacher_groups,
            question_count=body.question_count,
            stage_ratios=body.stage_ratios.model_dump(),
            exclude_current_exam_originals=body.exclude_current_exam_originals,
            allow_broad_fallback=False,
            related_fill_policy="exact_only",
        )
    except ValueError as exc:
        raise ApiError(
            422,
            "training_plan_invalid",
            "Training plan is invalid",
        ) from exc
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(
            503,
            "training_database_unavailable",
            "Training data is temporarily unavailable",
        ) from exc
    public = _public_training_mapping(plan)
    diagnosis_snapshot = public.get("diagnosis_snapshot")
    if not isinstance(diagnosis_snapshot, dict) or diagnosis_snapshot.get(
        "diagnosis_identity"
    ) != "question_tag":
        raise ApiError(
            422,
            "training_plan_invalid",
            "Training plan is invalid",
        )
    return public


def _plan_revision(plan: dict[str, object]) -> str:
    encoded = json.dumps(
        plan,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _task_by_code(
    service: TrainingTaskService,
    task_code: str,
) -> dict[str, object] | None:
    try:
        return service.get_task_by_code(task_code)
    except KeyError:
        return None
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(
            503,
            "training_database_unavailable",
            "Training data is temporarily unavailable",
        ) from exc


def _confirmed_existing_task(
    task: dict[str, object],
    current_revision: str,
) -> TrainingTaskDetail:
    generation_config = task.get("generation_config")
    stored_revision = (
        str(generation_config.get("plan_revision") or "")
        if isinstance(generation_config, dict)
        else ""
    )
    if stored_revision != current_revision:
        _raise_confirmation_conflict(current_revision)
    return _task_detail_response(task)


def _raise_confirmation_conflict(current_revision: str) -> None:
    raise ApiError(
        409,
        "training_confirmation_conflict",
        "Training plan changed; refresh and confirm again",
        {"current_plan_revision": current_revision},
    )


def _task_detail_response(task: dict[str, object]) -> TrainingTaskDetail:
    return TrainingTaskDetail.model_validate(_public_training_mapping(task))


def _public_training_mapping(value: dict[str, object]) -> dict[str, object]:
    stripped = _strip_training_storage_fields(value)
    return sanitize_public_mapping(stripped)


def _strip_training_storage_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _strip_training_storage_fields(item)
            for key, item in value.items()
            if str(key).casefold()
            not in {
                "error_message",
                "source_file",
                "paper_source_file",
                "output_path",
            }
        }
    if isinstance(value, (list, tuple)):
        return [_strip_training_storage_fields(item) for item in value]
    return value


__all__ = ["router"]
