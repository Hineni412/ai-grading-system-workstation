from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse

from backend.api.app import ApiError, ErrorResponse
from backend.api.dependencies import (
    get_assembly_workspace_service,
    get_job_manager,
    get_question_bank_read_service,
    get_question_bank_db_path,
    get_request_diagnosis_profile_service,
    get_personalized_recommendation_module,
)
from backend.api.schemas.assembly import (
    AssemblyDraftResponse,
    AssemblyDraftWriteRequest,
    AssemblyExportSubmitRequest,
    AssemblyQuestionListResponse,
    AssemblyRecordDeleteResponse,
    AssemblyRecordListResponse,
    AssemblyRecordRestoreRequest,
    AssemblyRecordResponse,
    AssemblyAssistantRequest,
    AssemblyAssistantResponse,
)
from backend.api.schemas.jobs import JobResponse
from backend.api.routers.jobs import _job_response
from backend.jobs.manager import JobManager, UnsupportedJobTypeError
from question_bank.services.assembly_workspace_service import (
    AssemblyDraft,
    AssemblyDraftConflict,
    AssemblyRecord,
    AssemblyRecordFileExpired,
    AssemblyRecordFileForbidden,
    AssemblyRecordFileTypeError,
    AssemblyWorkspaceService,
)
from question_bank.services.question_read_service import (
    QuestionBankReadService,
    QuestionBankSnapshotError,
)
from integration.data_generation import commit_generation
from integration.diagnosis_profile_service import DiagnosisProfileService
from integration.result_cache import ResultCache
from integration.training_prewarm import record_request
from question_bank.recommendation.personalized import PersonalizedRecommendationModule
from question_bank.services.assembly_assistant import shortlist_candidates


router = APIRouter(prefix="/api/question-assembly", tags=["question-assembly"])
NO_STORE_HEADERS = {"Cache-Control": "no-store"}
_LEADING_SCORE = re.compile(r"^[（(]\s*(\d+)\s*分\s*[）)]")
# Whole-result cache: keyed on the diagnosis key + request body + exclusions +
# question-bank commit generation, stored as pickle bytes with single-flight.
_ASSISTANT_CACHE = ResultCache(limit=16)


@router.post("/assistant/candidates", response_model=AssemblyAssistantResponse)
def get_assistant_candidates(
    body: AssemblyAssistantRequest,
    diagnosis_service: DiagnosisProfileService = Depends(get_request_diagnosis_profile_service),
    read_service: QuestionBankReadService = Depends(get_question_bank_read_service),
    recommendations: PersonalizedRecommendationModule = Depends(get_personalized_recommendation_module),
    workspace: AssemblyWorkspaceService = Depends(get_assembly_workspace_service),
) -> AssemblyAssistantResponse:
    try:
        result = compute_assistant_candidates(
            diagnosis_service=diagnosis_service,
            read_service=read_service,
            recommendations=recommendations,
            workspace=workspace,
            class_id=body.class_id,
            curriculum_volume_id=body.curriculum_volume_id,
            chapter_id=body.chapter_id,
            teaching_progress_chapter_id=body.teaching_progress_chapter_id,
            target_keys=body.target_keys,
            question_type=body.question_type,
            difficulty_min=body.difficulty_min,
            difficulty_max=body.difficulty_max,
            exclude_exam_originals=body.exclude_exam_originals,
            exclude_recent=body.exclude_recent,
        )
    except ValueError as exc:
        raise ApiError(422, "assembly_assistant_scope_invalid", "Class evidence or candidate filters changed") from exc
    except (OSError, sqlite3.Error, QuestionBankSnapshotError) as exc:
        raise ApiError(503, "assembly_assistant_unavailable", "Class evidence or question bank is temporarily unavailable") from exc
    return AssemblyAssistantResponse.model_validate(result)


def compute_assistant_candidates(
    *,
    diagnosis_service: DiagnosisProfileService,
    read_service: QuestionBankReadService,
    recommendations: PersonalizedRecommendationModule,
    workspace: AssemblyWorkspaceService,
    class_id: str,
    curriculum_volume_id: str,
    chapter_id: str = "",
    teaching_progress_chapter_id: str = "",
    target_keys: list[str] | None = None,
    question_type: str = "",
    difficulty_min: float = 1,
    difficulty_max: float = 8,
    exclude_exam_originals: bool = True,
    exclude_recent: bool = True,
) -> dict:
    """Single compute path shared by the endpoint and the prewarm worker."""
    if difficulty_min > difficulty_max:
        raise ValueError("Difficulty range is reversed")
    scope = {"mode": "class", "class_ids": [class_id],
             "use_historical_fallback": False}
    exam_scope = {"mode": "semester", "session_ids": [],
                  "curriculum_volume_id": curriculum_volume_id}
    record_request(
        "assistant",
        scope=scope,
        exam_scope=exam_scope,
        params={
            "class_id": class_id,
            "curriculum_volume_id": curriculum_volume_id,
            "chapter_id": chapter_id,
            "teaching_progress_chapter_id": teaching_progress_chapter_id,
            "target_keys": target_keys,
            "question_type": question_type,
            "difficulty_min": difficulty_min,
            "difficulty_max": difficulty_max,
            "exclude_exam_originals": exclude_exam_originals,
            "exclude_recent": exclude_recent,
        },
    )
    diagnosis = diagnosis_service.build_profiles(
        scope=scope, exam_scope=exam_scope,
    )
    # Compatibility flags no longer select different history definitions.
    excluded = recommendations.current_exam_question_ids(diagnosis)

    key_fn = getattr(diagnosis_service, "tag_profile_cache_key", None)
    diagnosis_key = (
        key_fn(scope=scope, exam_scope=exam_scope) if callable(key_fn) else None
    )

    def _compute() -> dict:
        return shortlist_candidates(
            diagnosis=diagnosis, read_service=read_service,
            volume_id=curriculum_volume_id, chapter_id=chapter_id,
            teaching_progress_chapter_id=teaching_progress_chapter_id,
            target_keys=target_keys, question_type=question_type,
            difficulty_min=difficulty_min, difficulty_max=difficulty_max,
            excluded_question_ids=excluded,
            cache_scope=diagnosis_key, recommendations=recommendations,
        )

    if not callable(key_fn):
        return _compute()
    return _ASSISTANT_CACHE.get_or_compute(
        (
            "assistant-shortlist-v4-evidence-priority",
            str(read_service.db_path.resolve(strict=False)),
            commit_generation(read_service.db_path),
            diagnosis_key,
            chapter_id,
            teaching_progress_chapter_id,
            None if target_keys is None else tuple(target_keys),
            question_type,
            difficulty_min,
            difficulty_max,
            tuple(sorted(excluded)),
        ),
        _compute,
    )


@router.get("/draft", response_model=AssemblyDraftResponse)
def get_assembly_draft(
    service: AssemblyWorkspaceService = Depends(get_assembly_workspace_service),
) -> AssemblyDraftResponse:
    return AssemblyDraftResponse(**_draft_payload(service.load_draft()))


@router.put(
    "/draft",
    response_model=AssemblyDraftResponse,
    responses={
        409: {"model": ErrorResponse, "description": "Assembly draft changed"},
    },
)
def save_assembly_draft(
    body: AssemblyDraftWriteRequest,
    service: AssemblyWorkspaceService = Depends(get_assembly_workspace_service),
    db_path: Path = Depends(get_question_bank_db_path),
) -> AssemblyDraftResponse:
    if service.load_draft().practice_rules and body.draft.basket_ids:
        body.draft.practice_rules = True
    if body.draft.practice_rules:
        _validate_practice_paper(body.draft.basket_ids, db_path, service.data_root)
    try:
        saved = service.save_draft(
            expected_revision=body.expected_revision,
            draft=body.draft.model_dump(),
        )
    except AssemblyDraftConflict as exc:
        raise ApiError(
            409,
            "assembly_draft_conflict",
            "Assembly draft has changed",
            {"current_revision": exc.current_revision},
        ) from exc
    except ValueError as exc:
        raise ApiError(
            422,
            "assembly_draft_invalid",
            "Assembly draft is invalid",
        ) from exc
    return AssemblyDraftResponse(**_draft_payload(saved))


@router.post(
    "/export",
    response_model=JobResponse,
    status_code=202,
    responses={
        409: {"model": ErrorResponse, "description": "Assembly draft changed"},
    },
)
def submit_assembly_export(
    body: AssemblyExportSubmitRequest,
    service: AssemblyWorkspaceService = Depends(get_assembly_workspace_service),
    manager: JobManager = Depends(get_job_manager),
    db_path: Path = Depends(get_question_bank_db_path),
) -> JobResponse:
    draft = service.load_draft()
    if body.draft_revision != draft.revision:
        raise ApiError(
            409,
            "assembly_draft_conflict",
            "Assembly draft has changed",
            {"current_revision": draft.revision},
        )
    if not draft.order_ids:
        raise ApiError(
            422,
            "assembly_draft_empty",
            "Assembly draft has no questions",
        )
    if draft.practice_rules:
        _validate_practice_paper(draft.order_ids, db_path, service.data_root)
    payload: dict[str, object] = {
        "draft_revision": draft.revision,
        "draft": draft.to_payload(),
        "format": body.format,
        "question_count": len(draft.order_ids),
    }
    if body.source is not None:
        payload["source"] = body.source
    try:
        job = manager.submit("assembly_export", payload)
    except UnsupportedJobTypeError as exc:
        raise ApiError(
            404,
            "job_type_not_supported",
            "Job type is not supported",
            {"job_type": "assembly_export"},
        ) from exc
    return _job_response(job)


def _validate_practice_paper(question_ids, db_path: Path, data_root: Path) -> None:
    try:
        PersonalizedRecommendationModule(db_path=db_path, data_root=data_root).validate_paper_questions(question_ids)
    except ValueError as exc:
        raise ApiError(422, "assembly_practice_rule", str(exc)) from exc


@router.post(
    "/exports/{job_id}/retry",
    response_model=JobResponse,
    status_code=202,
)
def retry_assembly_export(
    job_id: int,
    manager: JobManager = Depends(get_job_manager),
) -> JobResponse:
    source = manager.get(int(job_id))
    if source is None:
        raise ApiError(
            404,
            "job_not_found",
            "Job not found",
            {"job_id": int(job_id)},
        )
    if source.job_type != "assembly_export":
        raise ApiError(
            409,
            "assembly_export_retry_not_available",
            "Assembly export job is not available for retry",
            {"job_id": int(job_id)},
        )
    if source.status not in {"failed", "cancelled"}:
        raise ApiError(
            409,
            "assembly_export_retry_not_available",
            "Assembly export job is not available for retry",
            {"job_id": int(job_id), "status": source.status},
        )
    payload = dict(source.payload)
    payload["retry_of_job_id"] = source.id
    try:
        job = manager.submit("assembly_export", payload)
    except UnsupportedJobTypeError as exc:
        raise ApiError(
            404,
            "job_type_not_supported",
            "Job type is not supported",
            {"job_type": "assembly_export"},
        ) from exc
    return _job_response(job)


@router.get(
    "/questions",
    response_model=AssemblyQuestionListResponse,
    responses={
        503: {
            "model": ErrorResponse,
            "description": "Question bank snapshot is temporarily unavailable",
        }
    },
)
def resolve_assembly_questions(
    question_ids: Annotated[list[int], Query(min_length=1, max_length=500)],
    service: QuestionBankReadService = Depends(get_question_bank_read_service),
) -> AssemblyQuestionListResponse:
    ordered_ids: list[int] = []
    for question_id in question_ids:
        if question_id <= 0:
            raise ApiError(
                422,
                "assembly_question_ids_invalid",
                "Question IDs must be positive integers",
            )
        if question_id not in ordered_ids:
            ordered_ids.append(question_id)
    try:
        rows = service.get_questions(ordered_ids)
    except QuestionBankSnapshotError as exc:
        raise ApiError(
            503,
            "question_bank_snapshot_unavailable",
            "Question bank is temporarily unavailable",
        ) from exc
    found = {int(row["id"]) for row in rows}
    items = [
        {
            "id": row["id"],
            "revision": row["revision"],
            "question_number": row["question_number"],
            "question_type": row["question_type"],
            "question_text": row["question_text"],
            "answer_text": row["answer_text"],
            "difficulty": row["difficulty"],
            "paper_title": row["paper_title"],
            "tags": row["tags"],
            "asset_urls": row["asset_urls"],
            "rich_content": row["rich_content"],
            "score_value": _score_value(row["question_text"]),
        }
        for row in rows
    ]
    return AssemblyQuestionListResponse(
        items=items,
        missing_question_ids=[
            question_id for question_id in ordered_ids if question_id not in found
        ],
    )


@router.get("/records", response_model=AssemblyRecordListResponse)
def list_assembly_records(
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
    service: AssemblyWorkspaceService = Depends(get_assembly_workspace_service),
) -> AssemblyRecordListResponse:
    records = service.list_records(limit=limit)
    return AssemblyRecordListResponse(
        items=[_record_response(record) for record in records],
        total=len(records),
    )


@router.delete(
    "/records/{record_id}",
    response_model=AssemblyRecordDeleteResponse,
)
def delete_assembly_record(
    record_id: str,
    service: AssemblyWorkspaceService = Depends(get_assembly_workspace_service),
) -> AssemblyRecordDeleteResponse:
    deleted = service.delete_record(record_id)
    if not deleted:
        raise ApiError(
            404,
            "assembly_record_not_found",
            "Assembly record not found",
            {"record_id": record_id},
        )
    return AssemblyRecordDeleteResponse(record_id=record_id, deleted=True)


@router.post(
    "/records/{record_id}/restore",
    response_model=AssemblyDraftResponse,
    responses={
        409: {"model": ErrorResponse, "description": "Assembly draft changed"},
    },
)
def restore_assembly_record(
    record_id: str,
    body: AssemblyRecordRestoreRequest,
    service: AssemblyWorkspaceService = Depends(get_assembly_workspace_service),
) -> AssemblyDraftResponse:
    record = service.get_record(record_id)
    if record is None:
        raise ApiError(
            404,
            "assembly_record_not_found",
            "Assembly record not found",
            {"record_id": record_id},
        )
    try:
        restored = service.save_draft(
            expected_revision=body.expected_revision,
            draft={
                "basket_ids": list(record.question_ids),
                "order_ids": list(record.order_ids),
                "sections": [section.to_payload() for section in record.sections],
                "title": record.title,
                "header_text": "",
                "include_answer": record.include_answer,
                "layout_mode": "sections" if record.sections else "sequential",
                "preview_mode": "teacher",
            },
        )
    except AssemblyDraftConflict as exc:
        raise ApiError(
            409,
            "assembly_draft_conflict",
            "Assembly draft has changed",
            {"current_revision": exc.current_revision},
        ) from exc
    except ValueError as exc:
        raise ApiError(
            422,
            "assembly_record_restore_invalid",
            "Assembly record cannot be restored",
            {"record_id": record_id},
        ) from exc
    return AssemblyDraftResponse(**_draft_payload(restored))


@router.get("/records/{record_id}/download", response_class=FileResponse)
def download_assembly_record(
    record_id: str,
    service: AssemblyWorkspaceService = Depends(get_assembly_workspace_service),
) -> FileResponse:
    try:
        resolved = service.resolve_record_file(record_id)
    except AssemblyRecordFileExpired as exc:
        raise ApiError(
            410,
            "assembly_record_file_expired",
            "Assembly export file is no longer available",
            {"record_id": record_id},
            headers=NO_STORE_HEADERS,
        ) from exc
    except AssemblyRecordFileForbidden as exc:
        raise ApiError(
            403,
            "assembly_record_file_forbidden",
            "Assembly export file is outside the allowed storage boundary",
            {"record_id": record_id},
            headers=NO_STORE_HEADERS,
        ) from exc
    except AssemblyRecordFileTypeError as exc:
        raise ApiError(
            415,
            "assembly_record_file_type_not_supported",
            "Assembly export file type is not supported",
            {"record_id": record_id},
            headers=NO_STORE_HEADERS,
        ) from exc
    return FileResponse(
        resolved.path,
        filename=resolved.path.name,
        media_type=resolved.media_type,
        headers=NO_STORE_HEADERS,
    )


def _draft_payload(draft: AssemblyDraft) -> dict[str, object]:
    payload = draft.to_payload()
    payload.pop("schema_version", None)
    return {**payload, "revision": draft.revision}


def _record_response(record: AssemblyRecord) -> AssemblyRecordResponse:
    return AssemblyRecordResponse(
        **record.to_public_payload(),
        download_url=f"/api/question-assembly/records/{record.id}/download",
    )


def _score_value(question_text: object) -> int | None:
    match = _LEADING_SCORE.match(str(question_text or "").strip())
    return int(match.group(1)) if match else None
