from __future__ import annotations

import json
import math
import gzip
import pickle
import sqlite3
import zlib
from dataclasses import asdict
from datetime import datetime, UTC
from pathlib import Path
from tempfile import SpooledTemporaryFile
from typing import Any, Callable, NoReturn
from urllib.parse import unquote

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, TypeAdapter

from backend.api.app import ApiError, ErrorResponse
from backend.api.dependencies import (
    get_diagnosis_profile_service,
    get_assembly_workspace_service,
    get_job_manager,
    get_personalized_paper_module,
    get_personalized_recommendation_module,
    get_request_diagnosis_profile_service,
    get_training_assessment_module,
    get_training_submission_module,
)
from backend.api.schemas.training import (
    PersonalizedPaperBatchCancelRequest,
    PersonalizedPaperBatchCreateRequest,
    PersonalizedPaperBatchListResponse,
    PersonalizedPaperBatchResponse,
    PersonalizedPaperBatchRetryRequest,
    PersonalizedPaperCreateRequest,
    PersonalizedPaperInstanceListResponse,
    PersonalizedPaperInstanceResponse,
    PersonalizedRecommendationCreateRequest,
    PersonalizedRecommendationDraftResponse,
    PersonalizedRecommendationEditRequest,
    PersonalizedHandoutExportRequest,
    TrainingFromAssemblyRequest,
    TrainingAssessmentActionRequest,
    TrainingAssessmentOutcomeResponse,
    TrainingAssessmentReviewRequest,
    TrainingAssessmentStartRequest,
    TrainingDiagnosisRequest,
    TrainingDiagnosisResponse,
    TrainingEvidenceReplayRequest,
    TrainingEvidenceReplayResponse,
    TrainingEvidenceSyncRequest,
    TrainingFeedbackResponse,
    TrainingOverviewRequest,
    TrainingOverviewResponse,
    TrainingPendingSummary,
    TrainingScanBatchCreateRequest,
    TrainingScanBatchListResponse,
    TrainingScanBatchResponse,
    TrainingScanPageResolveRequest,
    TrainingSubmissionCancelRequest,
)
from backend.api.routers.jobs import _job_response
from backend.api.schemas.jobs import JobResponse
from backend.jobs.manager import JobManager
from backend.jobs.training_handout import checked_handout_draft
from backend.training_assessment import (
    AssessmentActionCommand,
    AssessmentInputInvalid,
    AssessmentOperationConflict,
    AssessmentReviewConflict,
    AssessmentRevisionConflict,
    EvidenceReviewConflict,
    EvidenceSourceInvalid,
    EvidenceSyncCommand,
    EvidenceSyncConflict,
    ReviewPointCommand,
    SubmissionAssessmentNotFound,
    TrainingAssessmentError,
    TrainingAssessmentModule,
    TrainingEvidenceError,
)
from integration.data_generation import commit_generation
from integration.diagnosis_profile_service import DiagnosisProfileService
from integration.mastery_overview import build_mastery_overview, overview_payload
from integration.result_cache import ResultCache
from integration.training_prewarm import record_request
from question_bank.personalized_papers import (
    CreatePaperCommand,
    FreezePaperCommand,
    PaperArtifactNotFound,
    PaperBudgetExceeded,
    PaperInstanceNotFound,
    PaperInvalid,
    PaperRenderUnavailable,
    PaperRequestConflict,
    PaperRevisionConflict,
    PaperSourceChanged,
    PersonalizedPaperError,
    PersonalizedPaperModule,
)
from question_bank.recommendation.personalized import (
    PersonalizedRecommendationConfig,
    PersonalizedRecommendationModule,
    RecommendationDraftNotFound,
    RecommendationEditCommand,
    RecommendationEditInvalid,
    RecommendationRequestConflict,
    RecommendationRevisionConflict,
    RecommendationSourceChanged,
)
from question_bank.training_submissions import (
    MAX_UPLOAD_BYTES,
    CancelSubmissionCommand,
    CreateScanBatchCommand,
    IngestUploadCommand,
    InvalidSubmissionUpload,
    ResolvePageCommand,
    SubmissionNotFound,
    SubmissionRequestConflict,
    SubmissionRevisionConflict,
    TrainingSubmissionError,
    TrainingSubmissionModule,
)

router = APIRouter(prefix="/api/training", tags=["training"])
TRAINING_DATABASE_RESPONSES = {
    503: {
        "model": ErrorResponse,
        "description": "Training data is temporarily unavailable",
    }
}
PERSONALIZED_PAPER_UPLOAD_LIMIT = 50 * 1024 * 1024
NO_STORE_HEADERS = {"Cache-Control": "no-store"}
_GROUPING_RESULT_CACHE = ResultCache(limit=8)
_DIAGNOSIS_RESPONSE_CACHE = ResultCache(limit=8)
_GROUPING_RESPONSE_ADAPTER = TypeAdapter(TrainingDiagnosisResponse.model_fields['grouping'].annotation)


def _validated_diagnosis_json(public) -> bytes:
    model = TrainingDiagnosisResponse.model_validate(public)
    # Preserve JSONResponse's rejection of NaN/Infinity, including a string
    # converted to float by a typed response field. Untyped values were
    # already checked during the public-data walk.
    pending = [model]
    while pending:
        for value in pending.pop().__dict__.values():
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError('non-finite diagnosis number')
            if isinstance(value, BaseModel):
                pending.append(value)
            elif isinstance(value, list):
                pending.extend(item for item in value if isinstance(item, BaseModel))
    return model.__pydantic_serializer__.to_json(model, exclude_none=True)


def _diagnosis_response_bytes(service: DiagnosisProfileService, *, scope, exam_scope,
                             diagnosis=None, grouping_cache_key=None, diagnosis_factory=None) -> bytes:
    """Reuse a validated, complete response under the existing source contract."""
    root = Path(__file__).resolve().parents[3]
    files = (Path(__file__), root / 'backend/api/schemas/training.py',
             root / 'backend/public_data.py', root / 'question_bank/services/rich_content_service.py',
             root / 'question_bank/recommendation/personalized.py',
             root / 'question_bank/taxonomy/curriculum_catalog.py')
    semantics = tuple((str(path), path.stat().st_mtime_ns, path.stat().st_size) for path in files)
    key = (*service.tag_profile_cache_key(scope=scope, exam_scope=exam_scope),
           'api-diagnosis-json-v1', semantics, grouping_cache_key)

    def compute():
        entry = service._read_local_profile(key)
        if entry is not None:
            try:
                compressed = pickle.loads(entry[0])['json_gzip']
                if isinstance(compressed, bytes):
                    gzip.decompress(compressed)
                    return compressed
            except (KeyError, TypeError, OSError, EOFError, zlib.error):
                pass
        if grouping_cache_key is not None:
            # Grouping only adds the final field to this same source snapshot.
            # Reuse the validated base bytes rather than walking all evidence again.
            base = _diagnosis_response_bytes(service, scope=scope, exam_scope=exam_scope)
            source = diagnosis if diagnosis is not None else diagnosis_factory()
            public_group = _public_training_mapping({'grouping': source['grouping']}, reject_nonfinite=True)
            grouping = _GROUPING_RESPONSE_ADAPTER.validate_python(public_group.get('grouping'))
            if grouping is None:
                return gzip.compress(base, compresslevel=1, mtime=0)
            group_payload = _GROUPING_RESPONSE_ADAPTER.dump_python(grouping, mode='json', exclude_none=True)
            tail = json.dumps(group_payload, ensure_ascii=False, allow_nan=False,
                              separators=(',', ':')).encode('utf-8')
            return gzip.compress(base[:-1] + b',"grouping":' + tail + b'}', compresslevel=1, mtime=0)
        source = diagnosis if diagnosis is not None else service.build_profiles(scope=scope, exam_scope=exam_scope)
        public = _public_training_mapping({k: v for k, v in source.items() if not str(k).startswith('_')},
                                          reject_nonfinite=True)
        if public.get('diagnosis_identity') != 'question_tag':
            raise ValueError('unexpected diagnosis identity')
        # Direct serialization avoids another full dict construction and walk.
        encoded = _validated_diagnosis_json(public)
        compressed = gzip.compress(encoded, compresslevel=1, mtime=0)
        return compressed

    compressed = _DIAGNOSIS_RESPONSE_CACHE.get_or_compute(key, compute)
    if service.persist_snapshots:
        service._save_local_profile(key, (pickle.dumps({'json_gzip': compressed}, pickle.HIGHEST_PROTOCOL),
                                         pickle.dumps({}, pickle.HIGHEST_PROTOCOL)))
    return gzip.decompress(compressed)


def _build_grouped_diagnosis(service, grouping_module, *, scope_dump, exam_scope_dump, grouping,
                             asset_state=()):
    diagnosis = service.build_profiles(
        scope=scope_dump,
        exam_scope=exam_scope_dump,
    )
    if grouping is not None:
        assert grouping_module is not None
        grouping_config = PersonalizedRecommendationConfig(
            paper_mode="shared", scope_keys=tuple(grouping.scope_keys),
            group_scope_keys=tuple(grouping.scope_keys), question_count=grouping.question_count,
            purpose=grouping.purpose, max_questions_per_skill=grouping.max_questions_per_skill,
            max_written_questions=grouping.max_written_questions, recent_activity_count=grouping.recent_activity_count,
            expected_minutes=grouping.expected_minutes, difficulty_min=grouping.difficulty_min,
            difficulty_max=grouping.difficulty_max,
            exclude_current_exam_originals=grouping.exclude_current_exam_originals,
            curriculum_volume_id=grouping.curriculum_volume_id,
            training_intent=grouping.training_intent,
            teaching_progress_chapter_id=grouping.teaching_progress_chapter_id,
        )

        def _grouping_compute() -> dict[str, Any]:
            return grouping_module.chapter_groups(
                diagnosis=diagnosis,
                config=grouping_config,
                member_ids=grouping.member_ids,
                target_keys=grouping.target_keys,
                graded_activities=service.graded_activities(
                    tuple(
                        str(item["student_id"])
                        for item in diagnosis.get("students", [])
                    )
                ),
            )

        grouping_key_fn = getattr(service, "tag_profile_cache_key", None)
        grouping_release = str(
            getattr(
                getattr(grouping_module, "current_knowledge", None),
                "release_id",
                "",
            )
        )
        if callable(grouping_key_fn) and asset_state is not None:
            grouping_cache_key = (
                "grouping-v1",
                grouping_key_fn(scope=scope_dump, exam_scope=exam_scope_dump),
                json.dumps(asdict(grouping_config), sort_keys=True, default=str),
                tuple(grouping.member_ids),
                tuple(grouping.target_keys),
                commit_generation(grouping_module.db_path),
                grouping_release,
                # _recent_question_ids depends on the current day.
                str(grouping_module.clock().date()),
                asset_state,
            )
            diagnosis["grouping"] = _GROUPING_RESULT_CACHE.get_or_compute(
                grouping_cache_key,
                _grouping_compute,
            )
        else:
            diagnosis["grouping"] = _grouping_compute()
    return diagnosis


def _grouped_diagnosis_response_bytes(service, module, *, scope, exam_scope, grouping) -> bytes:
    # Reuse the existing complete asset manifest. Database signatures alone do
    # not detect edits to rich content, image fallbacks or preview files.
    from question_bank.services.question_read_service import _skill_asset_manifest
    manifest = _skill_asset_manifest(service.data_root)
    factory = lambda: _build_grouped_diagnosis(service, module() if callable(module) else module, scope_dump=scope,
        exam_scope_dump=exam_scope, grouping=grouping,
        asset_state=tuple(sorted(manifest.items())) if manifest is not None else None)
    if manifest is None:
        public = _public_training_mapping({k: v for k, v in factory().items() if not str(k).startswith('_')},
                                          reject_nonfinite=True)
        return _validated_diagnosis_json(public)
    # The outer response key already covers both source database generations.
    # The saved entry is checked against their logical content revisions, so
    # process-local counters must not appear again in this persistent suffix.
    day = datetime.now(UTC).date() if callable(module) else module.clock().date()
    key = ('grouping-public-v2', json.dumps(grouping.model_dump(), sort_keys=True), str(day),
           tuple(sorted(manifest.items())))
    record_request('grouped_diagnosis', scope=scope, exam_scope=exam_scope,
                   params={'grouping': grouping.model_dump()})
    return _diagnosis_response_bytes(service, scope=scope, exam_scope=exam_scope,
        grouping_cache_key=key, diagnosis_factory=factory)


def _grouping_module(body: TrainingDiagnosisRequest) -> Callable[[], PersonalizedRecommendationModule] | None:
    # A complete response hit needs source checks, not another resolver/module.
    return get_personalized_recommendation_module if body.grouping else None


@router.post(
    "/diagnosis",
    response_model=TrainingDiagnosisResponse,
    response_model_exclude_none=True,
    responses=TRAINING_DATABASE_RESPONSES,
)
def build_training_diagnosis(
    body: TrainingDiagnosisRequest,
    service: DiagnosisProfileService = Depends(
        get_request_diagnosis_profile_service
    ),
    grouping_module: PersonalizedRecommendationModule | Callable[[], PersonalizedRecommendationModule] | None = Depends(_grouping_module),
) -> TrainingDiagnosisResponse | Response:
    try:
        scope_dump = body.scope.model_dump(exclude_none=True)
        exam_scope_dump = body.exam_scope.model_dump(exclude_none=True)
        record_request("diagnosis", scope=scope_dump, exam_scope=exam_scope_dump, params={})
        if body.grouping is None and isinstance(service, DiagnosisProfileService):
            return Response(content=_diagnosis_response_bytes(service, scope=scope_dump,
                            exam_scope=exam_scope_dump), media_type='application/json')
        if body.grouping is not None and isinstance(service, DiagnosisProfileService):
            assert grouping_module is not None
            return Response(content=_grouped_diagnosis_response_bytes(service, grouping_module,
                scope=scope_dump, exam_scope=exam_scope_dump, grouping=body.grouping),
                media_type='application/json')
        resolved_module = grouping_module() if callable(grouping_module) else grouping_module
        diagnosis = _build_grouped_diagnosis(service, resolved_module, scope_dump=scope_dump,
            exam_scope_dump=exam_scope_dump, grouping=body.grouping)
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

    public = _public_training_mapping(
        {k: v for k, v in diagnosis.items() if not str(k).startswith("_")}
    )
    if public.get("diagnosis_identity") != "question_tag":
        raise ApiError(
            422,
            "training_scope_invalid",
            "Training scope is invalid",
        )
    return TrainingDiagnosisResponse.model_validate(public)


@router.post(
    "/overview",
    response_model=TrainingOverviewResponse,
    responses=TRAINING_DATABASE_RESPONSES,
)
def build_training_overview(
    body: TrainingOverviewRequest,
    service: DiagnosisProfileService = Depends(
        get_request_diagnosis_profile_service
    ),
) -> TrainingOverviewResponse:
    try:
        if body.exam_scope.mode != "semester":
            raise ValueError("overview requires a semester exam scope")
        scope_dump = body.scope.model_dump(exclude_none=True)
        exam_scope_dump = body.exam_scope.model_dump(exclude_none=True)
        record_request(
            "overview",
            scope=scope_dump,
            exam_scope=exam_scope_dump,
            params={"volume_id": body.exam_scope.curriculum_volume_id or ""},
        )
        key_fn = getattr(service, "tag_profile_cache_key", None)
        if callable(key_fn):
            overview = overview_payload(
                service,
                scope=scope_dump,
                exam_scope=exam_scope_dump,
                volume_id=body.exam_scope.curriculum_volume_id or "",
            )
        else:
            diagnosis = service.build_profiles(
                scope=scope_dump,
                exam_scope=exam_scope_dump,
            )
            overview = build_mastery_overview(
                diagnosis,
                volume_id=body.exam_scope.curriculum_volume_id or "",
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
    if not body.include_student_detail:
        # The overview payload can be a shared cached object; build a new
        # dict instead of mutating it.
        overview = {
            **overview,
            "associations": [],
            "students": [],
            "nodes": [{**node, "students": []} for node in overview.get("nodes", [])],
        }
    return TrainingOverviewResponse.model_validate(overview)


@router.post(
    "/personalized-drafts",
    response_model=PersonalizedRecommendationDraftResponse,
    responses={
        409: {
            "model": ErrorResponse,
            "description": "Recommendation request conflicts with an existing draft",
        },
        422: {
            "model": ErrorResponse,
            "description": "Personalized recommendation request is invalid",
        },
        **TRAINING_DATABASE_RESPONSES,
    },
)
def create_personalized_recommendation_draft(
    body: PersonalizedRecommendationCreateRequest,
    diagnosis_service: DiagnosisProfileService = Depends(
        get_diagnosis_profile_service
    ),
    module: PersonalizedRecommendationModule = Depends(
        get_personalized_recommendation_module
    ),
) -> PersonalizedRecommendationDraftResponse:
    try:
        diagnosis = diagnosis_service.build_profiles(
            scope=body.scope.model_dump(exclude_none=True),
            exam_scope=body.exam_scope.model_dump(exclude_none=True),
        )
        # Fetch before the weak-points filter: the stored activities cover every
        # scope student, matching the list build_profiles used to embed.
        graded_activities = diagnosis_service.graded_activities(
            [str(item["student_id"]) for item in diagnosis.get("students", [])]
        )
        explicitly_included = set(body.scope.include_student_ids)
        diagnosis["students"] = [
            student
            for student in diagnosis.get("students", [])
            if student.get("weak_points")
            or (body.paper_mode == "individual" and (body.max_unmeasured_questions > 0
                or body.purpose == "handout" and body.max_consolidation_questions > 0))
            or str(student.get("student_id") or "") in explicitly_included
        ]
        if not diagnosis["students"]:
            raise ValueError(
                "no students with evidence were selected for a recommendation draft"
            )
        draft = module.create(
            request_token=body.request_token.lower(),
            diagnosis=diagnosis,
            config=PersonalizedRecommendationConfig(
                paper_mode=body.paper_mode,
                remediation_only=body.remediation_only and body.paper_mode == "individual",
                max_unmeasured_questions=body.max_unmeasured_questions if body.paper_mode == "individual" else 0,
                max_consolidation_questions=body.max_consolidation_questions if body.paper_mode == "individual" and body.purpose == "handout" else 0,
                question_count=body.question_count,
                purpose=body.purpose, max_questions_per_skill=body.max_questions_per_skill,
                max_written_questions=body.max_written_questions, recent_activity_count=body.recent_activity_count,
                expected_minutes=body.expected_minutes,
                difficulty_min=body.difficulty_min,
                difficulty_max=body.difficulty_max,
                target_keys=(
                    tuple(body.target_keys)
                    if body.target_keys
                    else module.resolve_target_names(body.target_names)
                ),
                scope_keys=tuple(body.scope_keys),
                exclude_current_exam_originals=(
                    body.exclude_current_exam_originals
                ),
                curriculum_volume_id=body.curriculum_volume_id or "",
                group_scope_keys=tuple(body.group_scope_keys),
                group_source_version=body.group_source_version,
                training_intent=body.training_intent,
                teaching_progress_chapter_id=body.teaching_progress_chapter_id,
            ),
            actor_ref="local_teacher",
            graded_activities=graded_activities,
        )
    except RecommendationSourceChanged as exc:
        raise ApiError(409, "personalized_recommendation_source_changed",
                       "Group evidence or question sources changed; refresh the group") from exc
    except RecommendationRequestConflict as exc:
        raise ApiError(
            409,
            "personalized_recommendation_request_conflict",
            "This recommendation request token was already used",
        ) from exc
    except (TypeError, ValueError) as exc:
        raise ApiError(
            422,
            "personalized_recommendation_invalid",
            "Personalized recommendation request is invalid",
        ) from exc
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(
            503,
            "training_database_unavailable",
            "Training data is temporarily unavailable",
        ) from exc
    return PersonalizedRecommendationDraftResponse.model_validate(
        _public_training_mapping(draft)
    )


@router.get("/personalized-drafts/by-request/{request_token}",
            response_model=PersonalizedRecommendationDraftResponse,
            responses={404: {"model": ErrorResponse}, **TRAINING_DATABASE_RESPONSES})
def get_personalized_draft_by_request(
    request_token: str,
    module: PersonalizedRecommendationModule = Depends(get_personalized_recommendation_module),
) -> PersonalizedRecommendationDraftResponse:
    try:
        draft = module.get_by_request_token(request_token)
    except (RecommendationDraftNotFound, ValueError) as exc:
        raise ApiError(404, "personalized_recommendation_not_found",
                       "No saved draft was found for this request") from exc
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(503, "training_database_unavailable",
                       "Training data is temporarily unavailable") from exc
    return PersonalizedRecommendationDraftResponse.model_validate(_public_training_mapping(draft))


@router.post('/personalized-drafts/from-assembly', response_model=PersonalizedRecommendationDraftResponse)
def create_training_from_assembly(
    body: TrainingFromAssemblyRequest,
    workspace=Depends(get_assembly_workspace_service),
    diagnosis_service: DiagnosisProfileService = Depends(get_request_diagnosis_profile_service),
    module: PersonalizedRecommendationModule = Depends(get_personalized_recommendation_module),
):
    draft = workspace.load_draft()
    if draft.revision != body.draft_revision:
        raise ApiError(409, 'assembly_draft_conflict', '试卷草稿已变化，请重新加载。')
    rules = body.rules.model_dump()
    if rules['purpose'] != 'training' or draft.practice_rules != rules or not 8 <= len(draft.order_ids) <= 12:
        raise ApiError(422, 'assembly_practice_rule', '请保存训练卷设置，并选入 8–12 道题。')
    context = draft.assembly_context or {}
    classes = sorted(set(body.class_ids))
    if classes != sorted(context.get('class_ids', [])) or not context.get('curriculum_volume_id'):
        raise ApiError(422, 'assembly_practice_rule', '班级或考试依据已变化，请返回组卷页核对。')
    sessions = context.get('session_ids', [])
    snapshot = {'source': '班级组卷 · 全班', 'revision': draft.revision, 'class_ids': classes,
                'session_ids': sessions, 'question_ids': list(draft.order_ids), 'title': draft.title}
    try:
        diagnosis = diagnosis_service.build_profiles(scope={'mode': 'class', 'class_ids': classes, 'use_historical_fallback': False},
            exam_scope={'mode': 'manual' if sessions else 'semester', 'session_ids': sessions, 'curriculum_volume_id': context['curriculum_volume_id']})
        if not diagnosis.get('students'):
            raise ValueError('所选班级没有学生。')
        descriptors, _, _ = module._source_snapshot(question_ids=draft.order_ids)
        targets = tuple(sorted({key for q in descriptors for key in q['stable_keys']}))
        if not targets:
            raise ValueError('所选题目没有有效知识或技能资料，请返回组卷页调整。')
        result = module.create(request_token=body.request_token.lower(), diagnosis=diagnosis,
            config=PersonalizedRecommendationConfig(paper_mode='shared', target_keys=targets, curriculum_volume_id=context['curriculum_volume_id'], **rules),
            actor_ref='teacher', assembly_snapshot=snapshot,
            graded_activities=diagnosis_service.graded_activities([str(s['student_id']) for s in diagnosis['students']]))
    except (RecommendationRequestConflict, RecommendationSourceChanged) as exc:
        raise ApiError(409, 'personalized_recommendation_request_conflict', '训练草稿来源或操作令牌已变化，请重新核对。') from exc
    except (TypeError, ValueError) as exc:
        raise ApiError(422, 'assembly_practice_rule', str(exc)) from exc
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(503, 'training_database_unavailable', '训练数据暂时无法读取。') from exc
    return PersonalizedRecommendationDraftResponse.model_validate(_public_training_mapping(result))


@router.get(
    "/personalized-drafts/{draft_id}",
    response_model=PersonalizedRecommendationDraftResponse,
    responses={
        404: {
            "model": ErrorResponse,
            "description": "Personalized recommendation draft was not found",
        },
        **TRAINING_DATABASE_RESPONSES,
    },
)
def get_personalized_recommendation_draft(
    draft_id: str,
    module: PersonalizedRecommendationModule = Depends(
        get_personalized_recommendation_module
    ),
) -> PersonalizedRecommendationDraftResponse:
    try:
        draft = module.get(draft_id)
    except (RecommendationDraftNotFound, ValueError) as exc:
        raise ApiError(
            404,
            "personalized_recommendation_not_found",
            "Personalized recommendation draft was not found",
        ) from exc
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(
            503,
            "training_database_unavailable",
            "Training data is temporarily unavailable",
        ) from exc
    return PersonalizedRecommendationDraftResponse.model_validate(
        _public_training_mapping(draft)
    )


@router.post(
    "/personalized-drafts/{draft_id}/edits",
    response_model=PersonalizedRecommendationDraftResponse,
    responses={
        404: {
            "model": ErrorResponse,
            "description": "Personalized recommendation draft was not found",
        },
        409: {
            "model": ErrorResponse,
            "description": "Recommendation draft changed or its sources changed",
        },
        422: {
            "model": ErrorResponse,
            "description": "Recommendation draft edit is invalid",
        },
        **TRAINING_DATABASE_RESPONSES,
    },
)
def edit_personalized_recommendation_draft(
    draft_id: str,
    body: PersonalizedRecommendationEditRequest,
    module: PersonalizedRecommendationModule = Depends(
        get_personalized_recommendation_module
    ),
) -> PersonalizedRecommendationDraftResponse:
    try:
        draft = module.edit(
            draft_id,
            RecommendationEditCommand(
                request_token=body.request_token.lower(),
                expected_revision=body.expected_revision,
                action=body.action,
                student_id=body.student_id,
                item_id=body.item_id,
                actor_ref="local_teacher",
                reason=body.reason,
                replacement_question_id=body.replacement_question_id,
            ),
        )
    except RecommendationDraftNotFound as exc:
        raise ApiError(
            404,
            "personalized_recommendation_not_found",
            "Personalized recommendation draft was not found",
        ) from exc
    except RecommendationRevisionConflict as exc:
        raise ApiError(
            409,
            "personalized_recommendation_revision_conflict",
            "Recommendation draft changed; refresh before editing",
            {"current_revision": exc.current_revision},
        ) from exc
    except RecommendationSourceChanged as exc:
        raise ApiError(
            409,
            "personalized_recommendation_source_changed",
            "Recommendation sources changed; generate a new draft",
        ) from exc
    except RecommendationRequestConflict as exc:
        raise ApiError(
            409,
            "personalized_recommendation_request_conflict",
            "This recommendation edit token was already used",
        ) from exc
    except (RecommendationEditInvalid, TypeError, ValueError) as exc:
        raise ApiError(
            422,
            "personalized_recommendation_edit_invalid",
            "Personalized recommendation edit is invalid",
        ) from exc
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(
            503,
            "training_database_unavailable",
            "Training data is temporarily unavailable",
        ) from exc
    return PersonalizedRecommendationDraftResponse.model_validate(
        _public_training_mapping(draft)
    )


@router.post("/personalized-drafts/{draft_id}/handout-exports", response_model=JobResponse, status_code=202)
def export_personalized_handout(
    draft_id: str, body: PersonalizedHandoutExportRequest,
    module: PersonalizedRecommendationModule = Depends(get_personalized_recommendation_module),
    manager: JobManager = Depends(get_job_manager),
) -> JobResponse:
    payload = {"draft_id": draft_id, "expected_revision": body.expected_revision,
               "client_request_token": body.request_token.lower()}
    try:
        existing = manager.store.find_export_job_by_request_token("personalized_handout_export", body.request_token.lower())
        if existing is not None and existing.payload != payload:
            raise ApiError(409, "personalized_handout_request_conflict", "本次请求编号已用于其他导出，请重新发起。")
        if existing is None:
            checked_handout_draft(module, draft_id, body.expected_revision)
        job, _created = manager.submit_idempotent_export("personalized_handout_export", payload)
    except RecommendationDraftNotFound as exc:
        raise ApiError(404, "personalized_recommendation_not_found", "讲义草稿不存在。") from exc
    except RecommendationRevisionConflict as exc:
        raise ApiError(409, "personalized_recommendation_revision_conflict", "草稿已改变，请刷新后导出。",
                       {"current_revision": exc.current_revision}) from exc
    except RecommendationSourceChanged as exc:
        raise ApiError(409, "personalized_recommendation_source_changed", "题目来源已变化，请先重新核对草稿。") from exc
    except ValueError as exc:
        raise ApiError(422, "personalized_handout_invalid", str(exc)) from exc
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(503, "training_database_unavailable", "训练数据暂时不可用。") from exc
    return _job_response(job)


@router.get("/personalized-drafts/{draft_id}/handout-exports/by-request/{request_token}", response_model=JobResponse)
def get_handout_export_by_request(draft_id: str, request_token: str,
    manager: JobManager = Depends(get_job_manager)) -> JobResponse:
    try:
        job = manager.store.find_export_job_by_request_token("personalized_handout_export", request_token.lower())
    except ValueError as exc:
        raise ApiError(422, "personalized_handout_invalid", "请求编号无效。") from exc
    if job is None or job.payload.get("draft_id") != draft_id:
        raise ApiError(404, "personalized_handout_not_found", "未找到本次讲义导出任务。")
    return _job_response(job)


@router.post(
    "/personalized-drafts/{draft_id}/paper-instances",
    response_model=PersonalizedPaperInstanceResponse,
    status_code=201,
    responses={
        404: {"model": ErrorResponse, "description": "Draft was not found"},
        409: {"model": ErrorResponse, "description": "Draft or sources changed"},
        422: {"model": ErrorResponse, "description": "Paper preflight failed"},
        **TRAINING_DATABASE_RESPONSES,
    },
)
def create_personalized_paper_instance(
    draft_id: str,
    body: PersonalizedPaperCreateRequest,
    module: PersonalizedPaperModule = Depends(
        get_personalized_paper_module
    ),
) -> PersonalizedPaperInstanceResponse:
    try:
        instance = module.create_review_instance(
            draft_id,
            CreatePaperCommand(
                operation_token=body.operation_token.lower(),
                expected_draft_revision=body.expected_draft_revision,
                student_id=body.student_id,
                actor_ref="local_teacher",
                context_window_tokens=body.context_window_tokens,
                direct_freeze=body.direct_freeze,
            ),
        )
    except (
        PersonalizedPaperError,
        OSError,
        sqlite3.Error,
        TypeError,
        ValueError,
    ) as exc:
        _raise_paper_api_error(exc)
    return _paper_instance_response(instance)


@router.post(
    "/personalized-drafts/{draft_id}/paper-batches",
    response_model=PersonalizedPaperBatchResponse,
    status_code=201,
    responses={
        404: {"model": ErrorResponse, "description": "Draft was not found"},
        409: {"model": ErrorResponse, "description": "Draft or sources changed"},
        422: {"model": ErrorResponse, "description": "Paper preflight failed"},
        **TRAINING_DATABASE_RESPONSES,
    },
)
def create_personalized_paper_batch(
    draft_id: str,
    body: PersonalizedPaperBatchCreateRequest,
    module: PersonalizedPaperModule = Depends(get_personalized_paper_module),
) -> PersonalizedPaperBatchResponse:
    try:
        result = module.create_review_batch(
            draft_id,
            operation_token=body.operation_token.lower(),
            expected_draft_revision=body.expected_draft_revision,
            student_ids=body.student_ids,
            actor_ref="local_teacher",
            context_window_tokens=body.context_window_tokens,
            direct_freeze=body.direct_freeze,
        )
    except (
        PersonalizedPaperError,
        OSError,
        sqlite3.Error,
        TypeError,
        ValueError,
    ) as exc:
        _raise_paper_api_error(exc)
    return _paper_batch_response(result)


@router.get(
    "/personalized-drafts/{draft_id}/paper-batches",
    response_model=PersonalizedPaperBatchListResponse,
    responses=TRAINING_DATABASE_RESPONSES,
)
def list_personalized_paper_batches(
    draft_id: str,
    module: PersonalizedPaperModule = Depends(get_personalized_paper_module),
) -> PersonalizedPaperBatchListResponse:
    try:
        batches = module.list_batches_for_draft(draft_id)
    except (PersonalizedPaperError, OSError, sqlite3.Error, ValueError) as exc:
        _raise_paper_api_error(exc)
    return PersonalizedPaperBatchListResponse(
        items=[_paper_batch_response(item) for item in batches]
    )


@router.get(
    "/paper-batches/{batch_run_id}",
    response_model=PersonalizedPaperBatchResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Batch was not found"},
        **TRAINING_DATABASE_RESPONSES,
    },
)
def get_personalized_paper_batch(
    batch_run_id: str,
    module: PersonalizedPaperModule = Depends(get_personalized_paper_module),
) -> PersonalizedPaperBatchResponse:
    try:
        batch = module.get_batch(batch_run_id)
    except (PersonalizedPaperError, OSError, sqlite3.Error, ValueError) as exc:
        _raise_paper_api_error(exc)
    return _paper_batch_response(batch)


@router.post(
    "/paper-batches/{batch_run_id}/cancel",
    response_model=PersonalizedPaperBatchResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Batch was not found"},
        **TRAINING_DATABASE_RESPONSES,
    },
)
def cancel_personalized_paper_batch(
    batch_run_id: str,
    _body: PersonalizedPaperBatchCancelRequest,
    module: PersonalizedPaperModule = Depends(get_personalized_paper_module),
) -> PersonalizedPaperBatchResponse:
    try:
        batch = module.cancel_batch(batch_run_id)
    except (PersonalizedPaperError, OSError, sqlite3.Error, ValueError) as exc:
        _raise_paper_api_error(exc)
    return _paper_batch_response(batch)


@router.post(
    "/paper-batches/{batch_run_id}/retry",
    response_model=PersonalizedPaperBatchResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Batch was not found"},
        422: {"model": ErrorResponse, "description": "Retry scope was invalid"},
        **TRAINING_DATABASE_RESPONSES,
    },
)
def retry_personalized_paper_batch(
    batch_run_id: str,
    body: PersonalizedPaperBatchRetryRequest,
    module: PersonalizedPaperModule = Depends(get_personalized_paper_module),
) -> PersonalizedPaperBatchResponse:
    try:
        batch = module.retry_batch(
            batch_run_id,
            student_ids=body.student_ids,
        )
    except (PersonalizedPaperError, OSError, sqlite3.Error, ValueError) as exc:
        _raise_paper_api_error(exc)
    return _paper_batch_response(batch)


@router.get(
    "/personalized-drafts/{draft_id}/paper-instances",
    response_model=PersonalizedPaperInstanceListResponse,
    responses=TRAINING_DATABASE_RESPONSES,
)
def list_personalized_paper_instances(
    draft_id: str,
    module: PersonalizedPaperModule = Depends(
        get_personalized_paper_module
    ),
) -> PersonalizedPaperInstanceListResponse:
    try:
        items = module.list_for_draft(draft_id)
    except (
        PersonalizedPaperError,
        OSError,
        sqlite3.Error,
        TypeError,
        ValueError,
    ) as exc:
        _raise_paper_api_error(exc)
    return PersonalizedPaperInstanceListResponse(
        items=[_paper_instance_response(item) for item in items]
    )


@router.get(
    "/paper-instances/{paper_instance_id}",
    response_model=PersonalizedPaperInstanceResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Paper was not found"},
        **TRAINING_DATABASE_RESPONSES,
    },
)
def get_personalized_paper_instance(
    paper_instance_id: str,
    module: PersonalizedPaperModule = Depends(
        get_personalized_paper_module
    ),
) -> PersonalizedPaperInstanceResponse:
    try:
        instance = module.get(paper_instance_id)
    except (
        PersonalizedPaperError,
        OSError,
        sqlite3.Error,
        TypeError,
        ValueError,
    ) as exc:
        _raise_paper_api_error(exc)
    return _paper_instance_response(instance)


@router.post(
    "/paper-instances/{paper_instance_id}/freeze",
    response_model=PersonalizedPaperInstanceResponse,
    responses={
        409: {"model": ErrorResponse, "description": "Paper changed"},
        413: {"model": ErrorResponse, "description": "DOCX is too large"},
        415: {"model": ErrorResponse, "description": "DOCX is invalid"},
        422: {"model": ErrorResponse, "description": "Paper preflight failed"},
        **TRAINING_DATABASE_RESPONSES,
    },
    openapi_extra={
        "parameters": [
            {
                "name": "X-Operation-Token",
                "in": "header",
                "required": True,
                "schema": {"type": "string", "pattern": "^[0-9a-fA-F]{32}$"},
            },
            {
                "name": "X-Content-SHA256",
                "in": "header",
                "required": True,
                "schema": {"type": "string", "pattern": "^[0-9a-fA-F]{64}$"},
            },
            {
                "name": "X-Upload-Filename",
                "in": "header",
                "required": True,
                "schema": {"type": "string"},
            },
        ],
        "requestBody": {
            "required": True,
            "content": {
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document": {
                    "schema": {"type": "string", "format": "binary"}
                }
            },
        },
    },
)
async def freeze_personalized_paper_instance(
    paper_instance_id: str,
    request: Request,
    expected_revision: int = Query(ge=1),
    module: PersonalizedPaperModule = Depends(
        get_personalized_paper_module
    ),
) -> PersonalizedPaperInstanceResponse:
    media_type = (
        str(request.headers.get("content-type") or "")
        .split(";", 1)[0]
        .strip()
        .casefold()
    )
    expected_media_type = (
        "application/vnd.openxmlformats-officedocument."
        "wordprocessingml.document"
    )
    if media_type != expected_media_type:
        raise ApiError(
            415,
            "personalized_paper_docx_invalid",
            "Reviewed file must be a DOCX document",
        )
    try:
        declared_size = int(request.headers.get("content-length") or 0)
    except ValueError:
        declared_size = 0
    if declared_size > PERSONALIZED_PAPER_UPLOAD_LIMIT:
        raise ApiError(
            413,
            "personalized_paper_docx_too_large",
            "Reviewed DOCX is too large",
        )
    try:
        command = FreezePaperCommand(
            operation_token=str(
                request.headers.get("x-operation-token") or ""
            ).lower(),
            expected_revision=expected_revision,
            content_sha256=str(
                request.headers.get("x-content-sha256") or ""
            ).lower(),
            filename=unquote(
                str(request.headers.get("x-upload-filename") or "")
            ),
            actor_ref="local_teacher",
        )
        with SpooledTemporaryFile(
            max_size=1024 * 1024,
            mode="w+b",
        ) as upload:
            received = 0
            async for chunk in request.stream():
                received += len(chunk)
                if received > PERSONALIZED_PAPER_UPLOAD_LIMIT:
                    raise ApiError(
                        413,
                        "personalized_paper_docx_too_large",
                        "Reviewed DOCX is too large",
                    )
                upload.write(chunk)
            upload.seek(0)
            instance = module.freeze(
                paper_instance_id,
                command,
                upload,
            )
    except ApiError:
        raise
    except (
        PersonalizedPaperError,
        OSError,
        sqlite3.Error,
        TypeError,
        ValueError,
    ) as exc:
        _raise_paper_api_error(exc)
    return _paper_instance_response(instance)


@router.get(
    "/paper-instances/{paper_instance_id}/files/{kind}",
    response_class=FileResponse,
    responses={
        404: {"model": ErrorResponse, "description": "File was not found"},
        **TRAINING_DATABASE_RESPONSES,
    },
)
def download_personalized_paper_artifact(
    paper_instance_id: str,
    kind: str,
    module: PersonalizedPaperModule = Depends(
        get_personalized_paper_module
    ),
) -> FileResponse:
    if kind not in {"review-docx", "reviewed-docx", "frozen-pdf"}:
        raise ApiError(
            404,
            "personalized_paper_file_not_found",
            "Personalized paper file was not found",
        )
    try:
        path, media_type = module.artifact_path(paper_instance_id, kind)
        instance = module.get(paper_instance_id)
    except (
        PersonalizedPaperError,
        OSError,
        sqlite3.Error,
        TypeError,
        ValueError,
    ) as exc:
        _raise_paper_api_error(exc)
    extension = ".pdf" if kind == "frozen-pdf" else ".docx"
    filename = (
        f"personalized-paper-{paper_instance_id[:12]}-"
        f"v{int(instance['series_version'])}{extension}"
    )
    return FileResponse(
        path,
        filename=filename,
        media_type=media_type,
        headers=NO_STORE_HEADERS,
    )


@router.get(
    "/paper-batches/{batch_run_id}/files/{kind}",
    response_class=FileResponse,
    responses={404: {"model": ErrorResponse, "description": "File was not found"}},
)
def download_personalized_paper_batch(
    batch_run_id: str,
    kind: str,
    module: PersonalizedPaperModule = Depends(get_personalized_paper_module),
) -> FileResponse:
    try:
        path, media_type = module.batch_artifact_path(batch_run_id, kind)
    except (
        PersonalizedPaperError,
        OSError,
        sqlite3.Error,
        TypeError,
        ValueError,
    ) as exc:
        _raise_paper_api_error(exc)
    filename = "个性化训练卷-批量审核稿.zip" if kind == "bundle" else "个性化训练卷-生成清单.json"
    return FileResponse(
        path,
        filename=filename,
        media_type=media_type,
        headers=NO_STORE_HEADERS,
    )


@router.get("/pending-summary", response_model=TrainingPendingSummary,
            responses=TRAINING_DATABASE_RESPONSES)
def training_pending_summary(
    module: TrainingAssessmentModule = Depends(get_training_assessment_module),
) -> TrainingPendingSummary:
    try:
        summary = module.pending_summary()
    except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
        _raise_assessment_api_error(exc)
    return TrainingPendingSummary.model_validate(summary)


@router.get(
    "/scan-batches",
    response_model=TrainingScanBatchListResponse,
    responses=TRAINING_DATABASE_RESPONSES,
)
def list_training_scan_batches(
    paper_batch_id: str = Query(pattern=r"^[0-9a-fA-F]{64}$"),
    module: TrainingSubmissionModule = Depends(get_training_submission_module),
) -> TrainingScanBatchListResponse:
    try:
        items = module.list_batches(paper_batch_id)
    except (TrainingSubmissionError, OSError, sqlite3.Error, TypeError, ValueError) as exc:
        _raise_submission_api_error(exc)
    return TrainingScanBatchListResponse.model_validate({"items": items})


@router.post(
    "/scan-batches",
    response_model=TrainingScanBatchResponse,
    status_code=201,
    responses={
        409: {"model": ErrorResponse, "description": "Operation conflict"},
        422: {"model": ErrorResponse, "description": "Paper selection is invalid"},
        **TRAINING_DATABASE_RESPONSES,
    },
)
def create_training_scan_batch(
    body: TrainingScanBatchCreateRequest,
    module: TrainingSubmissionModule = Depends(
        get_training_submission_module
    ),
) -> TrainingScanBatchResponse:
    try:
        result = module.create_batch(
            CreateScanBatchCommand(
                operation_token=body.operation_token.lower(),
                paper_instance_ids=tuple(body.paper_instance_ids),
                actor_ref="local_teacher",
            )
        )
    except (
        TrainingSubmissionError,
        PersonalizedPaperError,
        OSError,
        sqlite3.Error,
        TypeError,
        ValueError,
    ) as exc:
        _raise_submission_api_error(exc)
    return TrainingScanBatchResponse.model_validate(result)


@router.get(
    "/scan-batches/{batch_id}",
    response_model=TrainingScanBatchResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Scan batch was not found"},
        **TRAINING_DATABASE_RESPONSES,
    },
)
def get_training_scan_batch(
    batch_id: str,
    module: TrainingSubmissionModule = Depends(
        get_training_submission_module
    ),
) -> TrainingScanBatchResponse:
    try:
        result = module.get_batch(batch_id)
    except (
        TrainingSubmissionError,
        OSError,
        sqlite3.Error,
        TypeError,
        ValueError,
    ) as exc:
        _raise_submission_api_error(exc)
    return TrainingScanBatchResponse.model_validate(result)


@router.post(
    "/scan-batches/{batch_id}/uploads",
    response_model=TrainingScanBatchResponse,
    responses={
        409: {"model": ErrorResponse, "description": "Scan batch changed"},
        413: {"model": ErrorResponse, "description": "Scan file is too large"},
        415: {"model": ErrorResponse, "description": "Scan file is invalid"},
        **TRAINING_DATABASE_RESPONSES,
    },
    openapi_extra={
        "parameters": [
            {
                "name": "X-Operation-Token",
                "in": "header",
                "required": True,
                "schema": {"type": "string", "pattern": "^[0-9a-fA-F]{32}$"},
            },
            {
                "name": "X-Content-SHA256",
                "in": "header",
                "required": True,
                "schema": {"type": "string", "pattern": "^[0-9a-fA-F]{64}$"},
            },
            {
                "name": "X-Upload-Filename",
                "in": "header",
                "required": True,
                "schema": {"type": "string"},
            },
        ],
        "requestBody": {
            "required": True,
            "content": {
                "application/pdf": {"schema": {"type": "string", "format": "binary"}},
                "image/jpeg": {"schema": {"type": "string", "format": "binary"}},
                "image/png": {"schema": {"type": "string", "format": "binary"}},
            },
        },
    },
)
async def ingest_training_scan_upload(
    batch_id: str,
    request: Request,
    expected_revision: int = Query(ge=1),
    module: TrainingSubmissionModule = Depends(
        get_training_submission_module
    ),
) -> TrainingScanBatchResponse:
    media_type = (
        str(request.headers.get("content-type") or "")
        .split(";", 1)[0]
        .strip()
        .casefold()
    )
    try:
        declared_size = int(request.headers.get("content-length") or 0)
    except ValueError:
        declared_size = 0
    if declared_size > MAX_UPLOAD_BYTES:
        raise ApiError(
            413,
            "training_scan_too_large",
            "Training scan is too large",
        )
    try:
        command = IngestUploadCommand(
            operation_token=str(
                request.headers.get("x-operation-token") or ""
            ).lower(),
            expected_revision=expected_revision,
            filename=unquote(
                str(request.headers.get("x-upload-filename") or "")
            ),
            media_type=media_type,
            content_sha256=str(
                request.headers.get("x-content-sha256") or ""
            ).lower(),
            actor_ref="local_teacher",
        )
        with SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b") as upload:
            received = 0
            async for chunk in request.stream():
                received += len(chunk)
                if received > MAX_UPLOAD_BYTES:
                    raise ApiError(
                        413,
                        "training_scan_too_large",
                        "Training scan is too large",
                    )
                upload.write(chunk)
            upload.seek(0)
            result = module.ingest(batch_id, command, upload)
    except ApiError:
        raise
    except (
        TrainingSubmissionError,
        OSError,
        sqlite3.Error,
        TypeError,
        ValueError,
    ) as exc:
        _raise_submission_api_error(exc)
    return TrainingScanBatchResponse.model_validate(result)


@router.post(
    "/scan-batches/{batch_id}/pages/{scan_page_id}/resolve",
    response_model=TrainingScanBatchResponse,
    responses={
        409: {"model": ErrorResponse, "description": "Scan batch changed"},
        422: {"model": ErrorResponse, "description": "Page resolution is invalid"},
        **TRAINING_DATABASE_RESPONSES,
    },
)
def resolve_training_scan_page(
    batch_id: str,
    scan_page_id: str,
    body: TrainingScanPageResolveRequest,
    module: TrainingSubmissionModule = Depends(
        get_training_submission_module
    ),
) -> TrainingScanBatchResponse:
    try:
        result = module.resolve_page(
            batch_id,
            ResolvePageCommand(
                operation_token=body.operation_token.lower(),
                expected_revision=body.expected_revision,
                scan_page_id=scan_page_id,
                action=body.action,
                actor_ref="local_teacher",
                paper_instance_id=body.paper_instance_id,
                page_number=body.page_number,
            ),
        )
    except (
        TrainingSubmissionError,
        OSError,
        sqlite3.Error,
        TypeError,
        ValueError,
    ) as exc:
        _raise_submission_api_error(exc)
    return TrainingScanBatchResponse.model_validate(result)


@router.post(
    "/submissions/{submission_id}/cancel",
    response_model=TrainingScanBatchResponse,
    responses={
        409: {"model": ErrorResponse, "description": "Scan batch changed"},
        **TRAINING_DATABASE_RESPONSES,
    },
)
def cancel_training_submission(
    submission_id: str,
    body: TrainingSubmissionCancelRequest,
    module: TrainingSubmissionModule = Depends(
        get_training_submission_module
    ),
) -> TrainingScanBatchResponse:
    try:
        result = module.cancel_submission(
            submission_id,
            CancelSubmissionCommand(
                operation_token=body.operation_token.lower(),
                expected_revision=body.expected_revision,
                actor_ref="local_teacher",
                reason=body.reason,
            ),
        )
    except (
        TrainingSubmissionError,
        OSError,
        sqlite3.Error,
        TypeError,
        ValueError,
    ) as exc:
        _raise_submission_api_error(exc)
    return TrainingScanBatchResponse.model_validate(result)


@router.get(
    "/scan-batches/{batch_id}/pages/{scan_page_id}/preview",
    response_class=FileResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Scan page was not found"},
        **TRAINING_DATABASE_RESPONSES,
    },
)
def preview_training_scan_page(
    batch_id: str,
    scan_page_id: str,
    module: TrainingSubmissionModule = Depends(
        get_training_submission_module
    ),
) -> FileResponse:
    try:
        path = module.page_artifact_path(scan_page_id, batch_id=batch_id)
    except (
        TrainingSubmissionError,
        OSError,
        sqlite3.Error,
        TypeError,
        ValueError,
    ) as exc:
        _raise_submission_api_error(exc)
    return FileResponse(path, media_type="image/png", headers=NO_STORE_HEADERS)


@router.post(
    "/submissions/{submission_id}/assessment",
    response_model=TrainingAssessmentOutcomeResponse,
    responses={
        409: {"model": ErrorResponse, "description": "Assessment changed"},
        422: {"model": ErrorResponse, "description": "Assessment is invalid"},
        **TRAINING_DATABASE_RESPONSES,
    },
)
def start_training_assessment(
    submission_id: str,
    body: TrainingAssessmentStartRequest,
    response: Response,
    module: TrainingAssessmentModule = Depends(
        get_training_assessment_module
    ),
) -> TrainingAssessmentOutcomeResponse:
    try:
        outcome = module.assess(submission_id, body.expected_revision)
    except (
        TrainingAssessmentError,
        OSError,
        sqlite3.Error,
        TypeError,
        ValueError,
    ) as exc:
        _raise_assessment_api_error(exc)
    response.headers.update(NO_STORE_HEADERS)
    return TrainingAssessmentOutcomeResponse.model_validate(
        _public_training_mapping(outcome.to_dict())
    )


@router.get(
    "/submissions/{submission_id}/assessment",
    response_model=TrainingAssessmentOutcomeResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Assessment was not found"},
        **TRAINING_DATABASE_RESPONSES,
    },
)
def get_training_assessment(
    submission_id: str,
    response: Response,
    submission_revision: int = Query(ge=1),
    module: TrainingAssessmentModule = Depends(
        get_training_assessment_module
    ),
) -> TrainingAssessmentOutcomeResponse:
    try:
        outcome = module.get_outcome(submission_id, submission_revision)
        if outcome is None:
            raise SubmissionAssessmentNotFound(
                "training assessment was not found"
            )
    except (
        TrainingAssessmentError,
        OSError,
        sqlite3.Error,
        TypeError,
        ValueError,
    ) as exc:
        _raise_assessment_api_error(exc)
    response.headers.update(NO_STORE_HEADERS)
    return TrainingAssessmentOutcomeResponse.model_validate(
        _public_training_mapping(outcome.to_dict())
    )


@router.post(
    "/submissions/{submission_id}/assessment/reviews",
    response_model=TrainingAssessmentOutcomeResponse,
    responses={
        409: {"model": ErrorResponse, "description": "Review changed"},
        422: {"model": ErrorResponse, "description": "Review is invalid"},
        **TRAINING_DATABASE_RESPONSES,
    },
)
def review_training_assessment_point(
    submission_id: str,
    body: TrainingAssessmentReviewRequest,
    response: Response,
    module: TrainingAssessmentModule = Depends(
        get_training_assessment_module
    ),
) -> TrainingAssessmentOutcomeResponse:
    try:
        outcome = module.review_point(
            submission_id,
            body.submission_revision,
            ReviewPointCommand(
                operation_token=body.operation_token.lower(),
                expected_review_revision=body.expected_review_revision,
                task_item_code=body.task_item_code,
                point_id=body.point_id,
                final_state=body.final_state,
                teacher_evidence=body.teacher_evidence,
                teacher_reason=body.teacher_reason,
                actor_ref="local_teacher",
            ),
        )
    except (
        TrainingAssessmentError,
        OSError,
        sqlite3.Error,
        TypeError,
        ValueError,
    ) as exc:
        _raise_assessment_api_error(exc)
    response.headers.update(NO_STORE_HEADERS)
    return TrainingAssessmentOutcomeResponse.model_validate(
        _public_training_mapping(outcome.to_dict())
    )


@router.post(
    "/submissions/{submission_id}/assessment/actions",
    response_model=TrainingAssessmentOutcomeResponse,
    responses={
        409: {"model": ErrorResponse, "description": "Assessment changed"},
        422: {"model": ErrorResponse, "description": "Action is invalid"},
        **TRAINING_DATABASE_RESPONSES,
    },
)
def control_training_assessment(
    submission_id: str,
    body: TrainingAssessmentActionRequest,
    response: Response,
    module: TrainingAssessmentModule = Depends(
        get_training_assessment_module
    ),
) -> TrainingAssessmentOutcomeResponse:
    command = AssessmentActionCommand(
        operation_token=body.operation_token.lower(),
        expected_review_revision=body.expected_review_revision,
        actor_ref="local_teacher",
        reason=body.reason,
    )
    handlers = {
        "pause": module.pause,
        "resume": module.resume,
        "cancel": module.cancel,
        "recover": module.recover,
        "retry": module.retry_failed,
    }
    try:
        outcome = handlers[body.action](
            submission_id,
            body.submission_revision,
            command,
        )
    except (
        TrainingAssessmentError,
        OSError,
        sqlite3.Error,
        TypeError,
        ValueError,
    ) as exc:
        _raise_assessment_api_error(exc)
    response.headers.update(NO_STORE_HEADERS)
    return TrainingAssessmentOutcomeResponse.model_validate(
        _public_training_mapping(outcome.to_dict())
    )


@router.post(
    "/submissions/{submission_id}/evidence",
    response_model=TrainingFeedbackResponse,
    responses={
        409: {"model": ErrorResponse, "description": "Evidence changed"},
        422: {"model": ErrorResponse, "description": "Evidence is invalid"},
        **TRAINING_DATABASE_RESPONSES,
    },
)
def sync_training_evidence(
    submission_id: str,
    body: TrainingEvidenceSyncRequest,
    response: Response,
    module: TrainingAssessmentModule = Depends(
        get_training_assessment_module
    ),
) -> TrainingFeedbackResponse:
    try:
        feedback = module.sync_evidence(
            submission_id,
            body.submission_revision,
            EvidenceSyncCommand(
                operation_token=body.operation_token.lower(),
                expected_review_revision=body.expected_review_revision,
                action=body.action,
                actor_ref="local_teacher",
                reason=body.reason,
            ),
        )
    except (
        TrainingAssessmentError,
        TrainingEvidenceError,
        OSError,
        sqlite3.Error,
        TypeError,
        ValueError,
    ) as exc:
        _raise_assessment_api_error(exc)
    response.headers.update(NO_STORE_HEADERS)
    return TrainingFeedbackResponse.model_validate(
        _public_training_mapping(feedback)
    )


@router.get(
    "/submissions/{submission_id}/feedback",
    response_model=TrainingFeedbackResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Feedback was not found"},
        **TRAINING_DATABASE_RESPONSES,
    },
)
def get_training_feedback(
    submission_id: str,
    response: Response,
    submission_revision: int = Query(ge=1),
    module: TrainingAssessmentModule = Depends(
        get_training_assessment_module
    ),
) -> TrainingFeedbackResponse:
    try:
        feedback = module.get_feedback(
            submission_id, submission_revision
        )
        if feedback is None:
            raise SubmissionAssessmentNotFound(
                "training feedback was not found"
            )
    except (
        TrainingAssessmentError,
        TrainingEvidenceError,
        OSError,
        sqlite3.Error,
        TypeError,
        ValueError,
    ) as exc:
        _raise_assessment_api_error(exc)
    response.headers.update(NO_STORE_HEADERS)
    return TrainingFeedbackResponse.model_validate(
        _public_training_mapping(feedback)
    )


@router.post(
    "/evidence/replay",
    response_model=TrainingEvidenceReplayResponse,
    responses=TRAINING_DATABASE_RESPONSES,
)
def replay_training_evidence(
    body: TrainingEvidenceReplayRequest,
    response: Response,
    module: TrainingAssessmentModule = Depends(
        get_training_assessment_module
    ),
) -> TrainingEvidenceReplayResponse:
    try:
        result = module.replay_evidence_outbox(body.max_items)
    except (
        TrainingAssessmentError,
        TrainingEvidenceError,
        OSError,
        sqlite3.Error,
        TypeError,
        ValueError,
    ) as exc:
        _raise_assessment_api_error(exc)
    response.headers.update(NO_STORE_HEADERS)
    return TrainingEvidenceReplayResponse.model_validate(
        _public_training_mapping(result)
    )


def _paper_instance_response(
    instance: dict[str, Any],
) -> PersonalizedPaperInstanceResponse:
    return PersonalizedPaperInstanceResponse.model_validate(
        _public_training_mapping(instance)
    )


def _paper_batch_response(
    batch: dict[str, Any],
) -> PersonalizedPaperBatchResponse:
    return PersonalizedPaperBatchResponse.model_validate({
        **_public_training_mapping(batch),
        "items": [
            _paper_instance_response(item).model_dump()
            for item in batch["items"]
        ],
    })


def _raise_paper_api_error(exc: Exception) -> NoReturn:
    if isinstance(exc, (PaperInstanceNotFound, PaperArtifactNotFound)):
        raise ApiError(
            404,
            "personalized_paper_not_found",
            "Personalized paper or file was not found",
        ) from exc
    if isinstance(exc, PaperRevisionConflict):
        raise ApiError(
            409,
            "personalized_paper_revision_conflict",
            "Personalized paper changed; refresh before continuing",
            {"current_revision": exc.current_revision},
        ) from exc
    if isinstance(exc, PaperSourceChanged):
        raise ApiError(
            409,
            "personalized_paper_source_changed",
            "Recommendation sources changed; generate a new paper version",
        ) from exc
    if isinstance(exc, PaperRequestConflict):
        raise ApiError(
            409,
            "personalized_paper_request_conflict",
            "This paper operation token was already used",
        ) from exc
    if isinstance(exc, PaperBudgetExceeded):
        raise ApiError(
            422,
            "personalized_paper_budget_exceeded",
            "Whole-paper assessment budget was exceeded",
            {"budget": _public_training_mapping(exc.budget)},
        ) from exc
    if isinstance(exc, PaperInvalid):
        raise ApiError(
            422,
            "personalized_paper_invalid",
            "Personalized paper request or reviewed DOCX is invalid",
        ) from exc
    if isinstance(exc, (PaperRenderUnavailable, OSError, sqlite3.Error)):
        raise ApiError(
            503,
            "personalized_paper_unavailable",
            "Personalized paper generation is temporarily unavailable",
        ) from exc
    raise ApiError(
        422,
        "personalized_paper_invalid",
        "Personalized paper request is invalid",
    ) from exc


def _raise_submission_api_error(exc: Exception) -> NoReturn:
    if isinstance(exc, SubmissionNotFound):
        raise ApiError(
            404,
            "training_submission_not_found",
            "Training scan batch, submission, or page was not found",
        ) from exc
    if isinstance(exc, SubmissionRevisionConflict):
        raise ApiError(
            409,
            "training_submission_revision_conflict",
            "Training scan batch changed; refresh before continuing",
            {"current_revision": exc.current_revision},
        ) from exc
    if isinstance(exc, SubmissionRequestConflict):
        raise ApiError(
            409,
            "training_submission_request_conflict",
            "This training scan operation conflicts with current state",
        ) from exc
    if isinstance(exc, InvalidSubmissionUpload):
        raise ApiError(
            422,
            "training_submission_invalid",
            "Training scan or page resolution is invalid",
        ) from exc
    if isinstance(exc, PersonalizedPaperError):
        raise ApiError(
            422,
            "training_submission_paper_invalid",
            "Expected personalized paper is not available",
        ) from exc
    if isinstance(exc, (OSError, sqlite3.Error)):
        raise ApiError(
            503,
            "training_submission_unavailable",
            "Training scan grouping is temporarily unavailable",
        ) from exc
    raise ApiError(
        422,
        "training_submission_invalid",
        "Training scan request is invalid",
    ) from exc


def _raise_assessment_api_error(exc: Exception) -> NoReturn:
    if isinstance(exc, SubmissionAssessmentNotFound):
        raise ApiError(
            404,
            "training_assessment_not_found",
            "Training assessment or feedback was not found",
        ) from exc
    if isinstance(exc, AssessmentRevisionConflict):
        raise ApiError(
            409,
            "training_assessment_revision_conflict",
            "Training submission changed; refresh before continuing",
            {"current_revision": exc.current_revision},
        ) from exc
    if isinstance(
        exc,
        (
            AssessmentReviewConflict,
            EvidenceReviewConflict,
        ),
    ):
        raise ApiError(
            409,
            "training_assessment_review_conflict",
            "Training review changed; refresh before continuing",
            {"current_revision": exc.current_revision},
        ) from exc
    if isinstance(
        exc,
        (
            AssessmentOperationConflict,
            EvidenceSyncConflict,
        ),
    ):
        raise ApiError(
            409,
            "training_assessment_operation_conflict",
            "This training assessment operation conflicts with current state",
        ) from exc
    if isinstance(
        exc,
        (
            AssessmentInputInvalid,
            EvidenceSourceInvalid,
            ValueError,
            TypeError,
        ),
    ):
        raise ApiError(
            422,
            "training_assessment_invalid",
            "Training assessment, review, or evidence request is invalid",
        ) from exc
    if isinstance(exc, (OSError, sqlite3.Error)):
        raise ApiError(
            503,
            "training_assessment_unavailable",
            "Training assessment data is temporarily unavailable",
        ) from exc
    raise ApiError(
        422,
        "training_assessment_invalid",
        "Training assessment action is not available in the current state",
    ) from exc


def _public_training_mapping(value: dict[str, object], *, reject_nonfinite=False) -> dict[str, object]:
    from backend.public_data import (
        _OMIT, _sanitize_public_value, is_path_public_key, is_sensitive_public_key,
    )
    from question_bank.services.rich_content_service import strip_question_source_score

    excluded = {"error_message", "source_file", "paper_source_file", "output_path"}
    key_checks: dict[str, bool] = {}
    key_decisions: dict[str, tuple[bool, bool]] = {}
    text_checks: dict[str, bool] = {}
    score_texts: dict[tuple[str, str], str] = {}

    def public(item: Any) -> Any:
        if item is None or type(item) in (int, float, bool):
            if reject_nonfinite and isinstance(item, float) and not math.isfinite(item):
                raise ValueError('non-finite diagnosis number')
            return item
        if isinstance(item, str) and item in text_checks:
            return _OMIT if text_checks[item] else item
        if isinstance(item, dict):
            result = {}
            retained = 0
            for key, child in item.items():
                name = str(key)
                decision = key_decisions.get(name)
                if decision is None:
                    decision = (name.casefold() in excluded,
                                is_sensitive_public_key(name) or is_path_public_key(name))
                    key_decisions[name] = decision
                if decision[0]:
                    continue
                retained += 1
                if decision[1]:
                    continue
                if name == "question_text" and isinstance(child, str):
                    score_key = (str(item.get("question_number") or ""), child)
                    if score_key not in score_texts:
                        score_texts[score_key] = strip_question_source_score(child, question_number=score_key[0])
                    child = score_texts[score_key]
                cleaned = public(child)
                if cleaned is not _OMIT:
                    result[name] = cleaned
            # 原先先移除训练字段，再清理公共数据。仅训练字段的容器
            # 保持为空；仍有公共待清理字段的空容器按原规则省略。
            return _OMIT if retained and not result else result
        if isinstance(item, (list, tuple)):
            result = []
            for child in item:
                cleaned = public(child)
                if cleaned is not _OMIT:
                    result.append(cleaned)
            return result
        return _sanitize_public_value(item, key_checks=key_checks, text_checks=text_checks)

    cleaned = public(value)
    return cleaned if isinstance(cleaned, dict) else {}


__all__ = ["router"]
