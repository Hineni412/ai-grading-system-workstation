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
    get_personalized_recommendation_module,
    get_question_bank_db_path,
    get_question_bank_read_service,
    get_request_diagnosis_profile_service,
)
from backend.api.routers.jobs import _job_response
from backend.api.schemas.assembly import (
    AssemblyAssistantRequest,
    AssemblyExamQuestionsRequest,
    AssemblyQuickDraftRequest,
    AssemblyAssistantResponse,
    AssemblyDraftResponse,
    AssemblyDraftWriteRequest,
    AssemblyExportSubmitRequest,
    AssemblyQuestionListResponse,
    AssemblyRecordDeleteResponse,
    AssemblyRecordListResponse,
    AssemblyRecordResponse,
    AssemblyRecordRestoreRequest,
)
from backend.api.schemas.jobs import JobResponse
from backend.api.schemas.training import RecommendationRulesRequest
from backend.jobs.manager import JobManager, UnsupportedJobTypeError
from integration.data_generation import commit_generation
from integration.diagnosis_profile_service import DiagnosisProfileService
from integration.result_cache import ResultCache
from integration.training_prewarm import record_request
from question_bank.recommendation.personalized import PersonalizedRecommendationModule, PersonalizedRecommendationError
from question_bank.services.assembly_assistant import shortlist_candidates
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
            class_ids=body.class_ids,
            session_ids=body.session_ids,
            curriculum_volume_id=body.curriculum_volume_id,
            chapter_id=body.chapter_id,
            teaching_progress_chapter_id=body.teaching_progress_chapter_id,
            target_keys=body.target_keys,
            question_type=body.question_type,
            difficulty_min=body.difficulty_min,
            difficulty_max=body.difficulty_max,
            exclude_exam_originals=body.exclude_exam_originals,
            exclude_recent=body.exclude_recent,
            recent_activity_count=body.recent_activity_count, purpose=body.purpose,
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
    class_id: str = "",
    class_ids: list[str] | None = None,
    session_ids: list[int] | None = None,
    curriculum_volume_id: str,
    chapter_id: str = "",
    teaching_progress_chapter_id: str = "",
    target_keys: list[str] | None = None,
    question_type: str = "",
    difficulty_min: float = 1,
    difficulty_max: float = 8,
    exclude_exam_originals: bool = True,
    exclude_recent: bool = True,
    recent_activity_count: int = 3, purpose: str = "training",
) -> dict:
    """Single compute path shared by the endpoint and the prewarm worker."""
    if difficulty_min > difficulty_max:
        raise ValueError("Difficulty range is reversed")
    class_ids = sorted(set(class_ids or [class_id]))
    session_ids = sorted(set(session_ids or []))
    scope = {"mode": "class", "class_ids": class_ids,
             "use_historical_fallback": False}
    exam_scope = {"mode": "manual" if session_ids else "semester", "session_ids": session_ids,
                  "curriculum_volume_id": curriculum_volume_id}
    record_request(
        "assistant",
        scope=scope,
        exam_scope=exam_scope,
        params={
            "class_id": class_id,
            "class_ids": class_ids,
            "session_ids": session_ids,
            "curriculum_volume_id": curriculum_volume_id,
            "chapter_id": chapter_id,
            "teaching_progress_chapter_id": teaching_progress_chapter_id,
            "target_keys": target_keys,
            "question_type": question_type,
            "difficulty_min": difficulty_min,
            "difficulty_max": difficulty_max,
            "exclude_exam_originals": exclude_exam_originals,
            "exclude_recent": exclude_recent,
            "recent_activity_count": recent_activity_count, "purpose": purpose,
        },
    )
    diagnosis = diagnosis_service.build_profiles(
        scope=scope, exam_scope=exam_scope,
    )
    graded_activities = diagnosis_service.graded_activities(
        [str(item["student_id"]) for item in diagnosis.get("students", [])]
    )
    # Compatibility flags no longer select different history definitions.
    excluded = recommendations.current_exam_question_ids(
        diagnosis, graded_activities=graded_activities, recent_activity_count=recent_activity_count, purpose=purpose,
    )

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
            graded_activities=graded_activities,
            recent_activity_count=recent_activity_count, purpose=purpose,
        )

    if not callable(key_fn):
        return _compute()
    return _ASSISTANT_CACHE.get_or_compute(
        (
            "assistant-shortlist-v5-direct-targets",
            str(read_service.db_path.resolve(strict=False)),
            commit_generation(read_service.db_path),
            diagnosis_key,
            tuple(class_ids), tuple(session_ids),
            chapter_id,
            teaching_progress_chapter_id,
            None if target_keys is None else tuple(target_keys),
            question_type,
            difficulty_min,
            difficulty_max,
            tuple(sorted(excluded)),
            recent_activity_count, purpose,
        ),
        _compute,
    )


@router.post("/assistant/exam-questions")
def get_assistant_exam_questions(
    body: AssemblyExamQuestionsRequest,
    service: DiagnosisProfileService = Depends(get_request_diagnosis_profile_service),
) -> dict:
    try:
        return service.assembly_exam_questions(class_ids=body.class_ids, volume_id=body.curriculum_volume_id)
    except ValueError as exc:
        raise ApiError(422, "assembly_assistant_scope_invalid", "请确认班级与教学学期") from exc
    except (OSError, sqlite3.Error, QuestionBankSnapshotError) as exc:
        raise ApiError(503, "assembly_assistant_unavailable", "考试依据暂时无法读取") from exc


@router.post("/assistant/quick-draft")
def quick_draft(
    body: AssemblyQuickDraftRequest,
    diagnosis_service: DiagnosisProfileService = Depends(get_request_diagnosis_profile_service),
    read_service: QuestionBankReadService = Depends(get_question_bank_read_service),
    recommendations: PersonalizedRecommendationModule = Depends(get_personalized_recommendation_module),
    workspace: AssemblyWorkspaceService = Depends(get_assembly_workspace_service),
) -> dict:
    rules = body.rules.model_dump()
    evidence = diagnosis_service.assembly_exam_questions(class_ids=body.class_ids, volume_id=body.curriculum_volume_id)
    questions = [q for e in evidence['exams'] if not body.session_ids or e['session_id'] in body.session_ids
                 for q in e['questions'] if q['class_rate'] is not None and (body.threshold == 100 or q['class_rate'] < body.threshold / 100)]
    if body.sort == 'loss':
        questions.sort(key=lambda q: (q['class_rate'], q['key']))
    selected = list(dict.fromkeys(body.question_ids))
    descriptors, _, _ = recommendations._source_snapshot(question_ids=selected) if selected else ([], [], '')
    covered = {key for q in descriptors for key in q['stable_keys'] if key.startswith('sk_')}
    additions, skipped = [], {k: 0 for k in ('skill', 'written', 'difficulty', 'similar', 'unavailable')}
    pools = {}
    for question in questions:
        if len(selected) >= body.rules.question_count:
            break
        keys = tuple(question['skill_keys'])
        if set(keys) & covered:
            skipped['skill'] += 1
            continue
        if not keys:
            skipped['unavailable'] += 1
            continue
        if keys not in pools:
            pools[keys] = compute_assistant_candidates(diagnosis_service=diagnosis_service, read_service=read_service,
                recommendations=recommendations, workspace=workspace, class_ids=body.class_ids, session_ids=body.session_ids,
                curriculum_volume_id=body.curriculum_volume_id, target_keys=list(keys), difficulty_max=10,
                recent_activity_count=body.rules.recent_activity_count, purpose=body.rules.purpose)['candidates']
        rejected = set()
        for candidate in pools[keys]:
            qid = candidate['question_id']
            if qid in selected:
                continue
            violations = recommendations.paper_rule_violations([*selected, qid], rules)
            if violations:
                rejected.update(v['code'] for v in violations)
                continue
            selected.append(qid)
            added_descriptors, _, _ = recommendations._source_snapshot(question_ids=[qid])
            covered.update(k for q in added_descriptors for k in q['stable_keys'] if k.startswith('sk_'))
            additions.append({'question_id': qid, 'source': question})
            break
        else:
            for code in rejected or {'unavailable'}:
                skipped[code if code in skipped else 'unavailable'] += 1
    return {'question_ids': [a['question_id'] for a in additions], 'additions': additions, 'skipped': skipped}


@router.get("/draft", response_model=AssemblyDraftResponse)
def get_assembly_draft(
    service: AssemblyWorkspaceService = Depends(get_assembly_workspace_service),
    db_path: Path = Depends(get_question_bank_db_path),
    diagnosis_service: DiagnosisProfileService = Depends(get_request_diagnosis_profile_service),
) -> AssemblyDraftResponse:
    draft = service.load_draft()
    violations = _assembly_violations(draft.to_payload(), db_path, service.data_root, diagnosis_service)
    return AssemblyDraftResponse(**_draft_payload(draft), rule_violations=violations)


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
    diagnosis_service: DiagnosisProfileService = Depends(get_request_diagnosis_profile_service),
) -> AssemblyDraftResponse:
    previous = service.load_draft()
    if body.expected_revision != previous.revision:
        raise ApiError(409, 'assembly_draft_conflict', 'Assembly draft has changed', {'current_revision':previous.revision})
    if previous.practice_rules and body.draft.basket_ids and not body.draft.practice_rules:
        body.draft.practice_rules = RecommendationRulesRequest.model_validate(previous.practice_rules)
    rules = body.draft.practice_rules.model_dump() if hasattr(body.draft.practice_rules, 'model_dump') else body.draft.practice_rules
    # Changing limits or removing questions must remain possible on an invalid paper.
    violations = _assembly_violations({**body.draft.model_dump(), 'practice_rules': rules}, db_path, service.data_root, diagnosis_service)
    if violations and set(body.draft.basket_ids) - set(previous.basket_ids):
        raise ApiError(422, 'assembly_practice_rule', violations[0]['message'])
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
    return AssemblyDraftResponse(**_draft_payload(saved), rule_violations=violations)


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
    diagnosis_service: DiagnosisProfileService = Depends(get_request_diagnosis_profile_service),
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
        violations = _assembly_violations(draft.to_payload(), db_path, service.data_root, diagnosis_service)
        if violations:
            raise ApiError(422, 'assembly_practice_rule', violations[0]['message'])
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


def _assembly_violations(draft, db_path: Path, data_root: Path, diagnosis_service) -> list[dict]:
    rules = draft.get('practice_rules')
    if not rules or not draft.get('order_ids'):
        return []
    try:
        module = PersonalizedRecommendationModule(db_path=db_path, data_root=data_root)
        context = draft.get('assembly_context') or {}
        recent = diagnosis_service.assembly_recent_question_ids(module, context, rules) if context.get('class_ids') else set()
        return module.paper_rule_violations(draft['order_ids'], rules, recent_question_ids=recent)
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
    try:
        descriptors, _, _ = PersonalizedRecommendationModule(db_path=service.db_path, data_root=service.data_root or service.db_path.parent.parent)._source_snapshot(question_ids=ordered_ids)
        skills = {q['question_id']: [k for k in q['stable_keys'] if k.startswith('sk_')] for q in descriptors}
    except (ValueError, PersonalizedRecommendationError):
        skills = {}
    items = [
        {
            "id": row["id"],
            "skill_keys": skills.get(row['id'], []),
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
