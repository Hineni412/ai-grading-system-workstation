from __future__ import annotations

import hashlib
import json
import sqlite3
from math import ceil
from tempfile import SpooledTemporaryFile
from typing import Any, NoReturn
from urllib.parse import unquote

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import ValidationError

from backend.api.app import ApiError, ErrorResponse
from backend.api.dependencies import (
    get_diagnosis_profile_service,
    get_job_manager,
    get_personalized_paper_module,
    get_personalized_recommendation_module,
    get_practice_plan_service,
    get_request_diagnosis_profile_service,
    get_request_practice_plan_service,
    get_training_submission_module,
    get_training_assessment_module,
    get_training_task_service,
)
from backend.api.routers.jobs import _job_response
from backend.api.schemas.jobs import JobResponse
from backend.api.schemas.training import (
    PersonalizedPaperBatchCreateRequest,
    PersonalizedPaperBatchCancelRequest,
    PersonalizedPaperBatchRetryRequest,
    PersonalizedPaperBatchListResponse,
    PersonalizedPaperBatchResponse,
    PersonalizedPaperCreateRequest,
    PersonalizedPaperInstanceListResponse,
    PersonalizedPaperInstanceResponse,
    TrainingScanBatchCreateRequest,
    TrainingScanBatchResponse,
    TrainingScanPageResolveRequest,
    TrainingSubmissionCancelRequest,
    TrainingAssessmentActionRequest,
    TrainingAssessmentOutcomeResponse,
    TrainingAssessmentReviewRequest,
    TrainingAssessmentStartRequest,
    TrainingEvidenceReplayRequest,
    TrainingEvidenceReplayResponse,
    TrainingEvidenceSyncRequest,
    TrainingFeedbackResponse,
    PersonalizedRecommendationCreateRequest,
    PersonalizedRecommendationDraftResponse,
    PersonalizedRecommendationEditRequest,
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
from question_bank.services.training_task_service import TrainingTaskService
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


router = APIRouter(prefix="/api/training", tags=["training"])
TRAINING_DATABASE_RESPONSES = {
    503: {
        "model": ErrorResponse,
        "description": "Training data is temporarily unavailable",
    }
}
PERSONALIZED_PAPER_UPLOAD_LIMIT = 50 * 1024 * 1024
NO_STORE_HEADERS = {"Cache-Control": "no-store"}


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
        get_request_diagnosis_profile_service
    ),
    practice_service: PracticePlanService = Depends(
        get_request_practice_plan_service
    ),
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
        explicitly_included = set(body.scope.include_student_ids)
        diagnosis["students"] = [
            student
            for student in diagnosis.get("students", [])
            if student.get("weak_points")
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
                question_count=body.question_count,
                expected_minutes=body.expected_minutes,
                difficulty_min=body.difficulty_min,
                difficulty_max=body.difficulty_max,
                direct_ratio=body.stage_ratios.direct,
                prerequisite_ratio=body.stage_ratios.prerequisite,
                transfer_ratio=body.stage_ratios.transfer,
                target_keys=(
                    tuple(body.target_keys)
                    if body.target_keys
                    else module.resolve_target_names(body.target_names)
                ),
                scope_keys=tuple(body.scope_keys),
                exclude_current_exam_originals=(
                    body.exclude_current_exam_originals
                ),
            ),
            actor_ref="local_teacher",
        )
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
            {"budget": _strip_training_storage_fields(exc.budget)},
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
