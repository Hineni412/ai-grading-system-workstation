from __future__ import annotations

import errno
from pathlib import Path
from typing import Annotated, Any, Literal, NoReturn

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse

from backend.api.app import ApiError, ErrorResponse
from backend.api.dependencies import (
    get_data_root,
    get_job_manager,
    get_question_bank_db_path,
    get_question_bank_read_service,
    get_question_bank_write_service,
    get_taxonomy_review_service,
    get_taxonomy_suggestion_service,
    get_training_criterion_module,
)
from backend.api.routers.jobs import _job_response
from backend.api.schemas.jobs import JobResponse
from backend.api.schemas.question_bank import (
    AnswerDraftJobRequest,
    CurriculumCatalog,
    QuestionDetailResponse,
    QuestionErrorPatternEditRequest,
    QuestionFacetsResponse,
    QuestionImportJobSubmitRequest,
    QuestionImportRequestCreate,
    QuestionImportRequestResponse,
    QuestionImportUploadResponse,
    QuestionJobRetryRequest,
    QuestionListItem,
    QuestionListResponse,
    QuestionPaperListItem,
    QuestionPaperListResponse,
    QuestionPaperMetadataUpdateRequest,
    QuestionPaperMetadataWriteResponse,
    QuestionPaperPermanentDeleteImpactRequest,
    QuestionPaperPermanentDeleteImpactResponse,
    QuestionPaperPermanentDeleteRequest,
    QuestionPaperPermanentDeleteResponse,
    QuestionPaperStateChangeRequest,
    QuestionPaperStateWriteResponse,
    QuestionRefListItem,
    QuestionRefListResponse,
    QuestionSolutionEvidenceResponse,
    QuestionStateChangeRequest,
    QuestionTaggingJobRequest,
    QuestionTagWriteRequest,
    QuestionWriteResponse,
    SimilarQuestionItem,
    SimilarQuestionListResponse,
    TaxonomyCatalogResponse,
    TaxonomyProposalApplicationRetryRequest,
    TaxonomyProposalListResponse,
    TaxonomyProposalReviewRequest,
    TaxonomyProposalReviewResponse,
    TaxonomyReviewOperationResponse,
    TaxonomyReviewOperationUndoRequest,
    TaxonomySuggestionBatchApplyRequest,
    TaxonomySuggestionBatchPreviewRequest,
    TaxonomySuggestionBatchPreviewResponse,
    TaxonomySuggestionCreateRequest,
    TaxonomySuggestionRetryRequest,
    TaxonomySuggestionRunResponse,
    TaxonomySuggestionStartResponse,
    TrainingCriterionBackfillCreateRequest,
    TrainingCriterionBackfillRetryRequest,
    TrainingCriterionBackfillRunResponse,
    TrainingCriterionBackfillStartResponse,
    TrainingCriterionDraftWriteRequest,
    TrainingCriterionReviewRequest,
    TrainingCriterionVersionResponse,
    TrainingCriterionWorkspaceResponse,
)
from backend.file_access import (
    ControlledFileExpired,
    ControlledFileForbidden,
    ControlledFileTypeError,
)
from backend.jobs.manager import JobManager, UnsupportedJobTypeError
from backend.jobs.store import (
    JobRecord,
    TaggingSyncJobRequestConflictError,
    TaxonomySuggestionJobBusyError,
    TaxonomySuggestionJobRequestConflictError,
)
from question_bank.services.question_read_service import (
    QuestionBankReadService,
    QuestionBankSnapshotBusy,
    QuestionBankSnapshotError,
    QuestionMediaNotFound,
    QuestionReadFilters,
)
from question_bank.services.question_write_service import (
    ConfirmedQuestionTag,
    PaperMetadataConflict,
    PaperMetadataNotFound,
    PaperMetadataUpdate,
    PaperMetadataWriteResult,
    PaperPermanentDeleteConfirmationMismatch,
    PaperPermanentDeleteConflict,
    PaperPermanentDeleteDependencyConflict,
    PaperPermanentDeleteSelection,
    PaperPermanentDeleteStorageIncomplete,
    PaperStateConflict,
    PaperStateNotFound,
    PaperStateWriteResult,
    QuestionBankWriteService,
    QuestionImportStorageForbidden,
    QuestionImportTooLarge,
    QuestionImportTypeNotSupported,
    QuestionImportUploadNotFound,
    QuestionWriteConflict,
    QuestionWriteNotFound,
    QuestionWriteResult,
)
from question_bank.services.taxonomy_review_service import (
    TaxonomyReviewApplicationNotFound,
    TaxonomyReviewRequestConflict,
    TaxonomyReviewSelectionInvalid,
    TaxonomyReviewService,
    TaxonomyReviewUndoConflict,
)
from question_bank.services.taxonomy_review_suggestions import (
    TaxonomySuggestionInvalid,
    TaxonomySuggestionNotFound,
    TaxonomySuggestionRequestConflict,
    TaxonomySuggestionRevisionConflict,
    TaxonomySuggestionService,
)
from question_bank.solution_evidence.repository import (
    SolutionEvidenceRepository,
)
from question_bank.taxonomy.curriculum_catalog import (
    CurriculumCatalogError,
    curriculum_volume,
    load_curriculum_catalog,
)
from question_bank.taxonomy.governance import (
    TaxonomyProposalNotFound,
    TaxonomyReviewInvalid,
    TaxonomyRevisionConflict,
    TaxonomyStorageError,
    TaxonomyTargetTermNotFound,
    get_taxonomy_governance,
)
from question_bank.training_criteria import (
    CriterionQualityError,
    CriterionRequestConflict,
    CriterionReviewCommand,
    CriterionRevisionConflict,
    CriterionTransitionError,
    CriterionVersionNotFound,
    QuestionAnalysisInput,
    QuestionAnalysisInputLoader,
    TrainingCriterionModule,
    solution_evidence_source_content_hash,
)

router = APIRouter(prefix="/api/question-bank", tags=["question-bank"])
NO_STORE_HEADERS = {"Cache-Control": "no-store"}
BINARY_SCHEMA = {"type": "string", "format": "binary"}
QUESTION_IMAGE_CONTENT = {
    media_type: {"schema": BINARY_SCHEMA}
    for media_type in ("image/jpeg", "image/png", "image/webp", "image/bmp")
}
QUESTION_SNAPSHOT_ERROR_RESPONSES = {
    503: {
        "model": ErrorResponse,
        "description": "Question bank snapshot is temporarily unavailable",
    }
}
QUESTION_WRITE_ERROR_RESPONSES = {
    404: {"model": ErrorResponse, "description": "Question not found"},
    409: {"model": ErrorResponse, "description": "Question state conflict"},
}
PAPER_METADATA_WRITE_ERROR_RESPONSES = {
    404: {"model": ErrorResponse, "description": "Paper not found"},
    409: {"model": ErrorResponse, "description": "Paper metadata conflict"},
    422: {"model": ErrorResponse, "description": "Paper metadata is invalid"},
}
PAPER_STATE_WRITE_ERROR_RESPONSES = {
    404: {"model": ErrorResponse, "description": "Paper not found"},
    409: {"model": ErrorResponse, "description": "Paper state conflict"},
    422: {"model": ErrorResponse, "description": "Paper state is invalid"},
}
TAXONOMY_READ_ERROR_RESPONSES = {
    503: {
        "model": ErrorResponse,
        "description": "Taxonomy storage is temporarily unavailable",
    },
}
TAXONOMY_REVIEW_ERROR_RESPONSES = {
    404: {"model": ErrorResponse, "description": "Taxonomy proposal not found"},
    409: {"model": ErrorResponse, "description": "Taxonomy state conflict"},
    422: {"model": ErrorResponse, "description": "Taxonomy review is invalid"},
    **TAXONOMY_READ_ERROR_RESPONSES,
}
TAXONOMY_SUGGESTION_ERROR_RESPONSES = {
    404: {"model": ErrorResponse, "description": "Suggestion run not found"},
    409: {"model": ErrorResponse, "description": "Taxonomy state conflict"},
    422: {"model": ErrorResponse, "description": "Suggestion request is invalid"},
    **TAXONOMY_READ_ERROR_RESPONSES,
}
CRITERION_WRITE_ERROR_RESPONSES = {
    404: {
        "model": ErrorResponse,
        "description": "Question or criterion version not found",
    },
    409: {
        "model": ErrorResponse,
        "description": "Criterion version changed",
    },
    422: {
        "model": ErrorResponse,
        "description": "Criterion decision is invalid",
    },
}


@router.get("/curriculum", response_model=CurriculumCatalog)
def get_curriculum(
    include_knowledge_points: Annotated[bool, Query()] = True,
) -> CurriculumCatalog:
    try:
        payload = load_curriculum_catalog()
    except CurriculumCatalogError as exc:
        raise ApiError(
            503,
            "curriculum_catalog_unavailable",
            "Curriculum catalog is temporarily unavailable",
        ) from exc
    if not include_knowledge_points:
        payload = {
            **payload,
            "volumes": [
                {
                    **volume,
                    "chapters": [
                        {
                            **chapter,
                            "sections": [
                                {**section, "knowledge_points": []}
                                for section in chapter["sections"]
                            ],
                        }
                        for chapter in volume["chapters"]
                    ],
                }
                for volume in payload["volumes"]
            ],
        }
    return CurriculumCatalog(**payload)


@router.get(
    "/taxonomy/catalog",
    response_model=TaxonomyCatalogResponse,
    responses=TAXONOMY_READ_ERROR_RESPONSES,
)
def get_taxonomy_catalog() -> TaxonomyCatalogResponse:
    governance = get_taxonomy_governance()
    try:
        payload = governance.catalog()
    except (TaxonomyStorageError, OSError, TimeoutError) as exc:
        _raise_taxonomy_storage_api_error(exc)
    return TaxonomyCatalogResponse(**payload)


@router.get("/standard-summary")
def get_standard_summary(
    service: QuestionBankReadService = Depends(get_question_bank_read_service),
) -> dict[str, Any]:
    return service.standard_summary()


@router.get(
    "/taxonomy/proposals",
    response_model=TaxonomyProposalListResponse,
    responses=TAXONOMY_READ_ERROR_RESPONSES,
)
def list_taxonomy_proposals(
    status: Annotated[Literal["pending"], Query()] = "pending",
    summary: Annotated[bool, Query()] = False,
    service: TaxonomySuggestionService = Depends(
        get_taxonomy_suggestion_service
    ),
) -> TaxonomyProposalListResponse:
    governance = get_taxonomy_governance()
    try:
        if summary:
            payload = service.proposal_summary()
        else:
            payload = governance.list_proposals(status=status)
            payload = service.contextualize_proposal_page(payload)
    except (TaxonomyStorageError, OSError, TimeoutError) as exc:
        _raise_taxonomy_storage_api_error(exc)
    return TaxonomyProposalListResponse(**payload)


@router.post(
    "/taxonomy/suggestions",
    response_model=TaxonomySuggestionStartResponse,
    status_code=202,
    responses=TAXONOMY_SUGGESTION_ERROR_RESPONSES,
)
def start_taxonomy_suggestions(
    body: TaxonomySuggestionCreateRequest,
    service: TaxonomySuggestionService = Depends(
        get_taxonomy_suggestion_service
    ),
    manager: JobManager = Depends(get_job_manager),
) -> TaxonomySuggestionStartResponse:
    token = body.request_token.lower()
    try:
        run = service.create_run(
            proposal_ids=body.proposal_ids,
            expected_revision=body.expected_revision,
            request_token=token,
        )
        job, _created = manager.submit_idempotent_taxonomy_suggestion(
            {
                "run_id": run["run_id"],
                "operation": "process",
                "client_request_token": token,
            }
        )
    except TaxonomySuggestionRevisionConflict as exc:
        raise ApiError(
            409,
            "taxonomy_revision_conflict",
            "Taxonomy state changed; refresh and retry",
        ) from exc
    except TaxonomySuggestionRequestConflict as exc:
        raise ApiError(
            409,
            "taxonomy_suggestion_request_conflict",
            "This suggestion request token was already used",
        ) from exc
    except TaxonomySuggestionJobRequestConflictError as exc:
        raise ApiError(
            409,
            "taxonomy_suggestion_job_request_conflict",
            "This suggestion job request token was already used",
        ) from exc
    except TaxonomySuggestionJobBusyError as exc:
        raise ApiError(
            409,
            "taxonomy_suggestion_busy",
            "This suggestion run already has active work",
        ) from exc
    except (TaxonomySuggestionInvalid, ValueError) as exc:
        raise ApiError(
            422,
            "taxonomy_suggestion_invalid",
            "Taxonomy suggestion request is invalid",
        ) from exc
    except UnsupportedJobTypeError as exc:
        raise ApiError(
            503,
            "taxonomy_suggestion_unavailable",
            "Taxonomy suggestion is temporarily unavailable",
        ) from exc
    except (TaxonomyStorageError, OSError, TimeoutError, RuntimeError) as exc:
        _raise_taxonomy_storage_api_error(exc)
    return TaxonomySuggestionStartResponse(
        job=_job_response(job),
        run=TaxonomySuggestionRunResponse(**run),
    )


@router.get(
    "/taxonomy/suggestions/{run_id}",
    response_model=TaxonomySuggestionRunResponse,
    responses=TAXONOMY_SUGGESTION_ERROR_RESPONSES,
)
def get_taxonomy_suggestion_run(
    run_id: str,
    service: TaxonomySuggestionService = Depends(
        get_taxonomy_suggestion_service
    ),
    manager: JobManager = Depends(get_job_manager),
) -> TaxonomySuggestionRunResponse:
    try:
        run = service.get_run(run_id)
        latest_job = _latest_taxonomy_suggestion_job(manager, run_id)
        if (
            run.get("status") in {"queued", "running", "cancelling"}
            and latest_job is not None
            and latest_job.status in {"succeeded", "failed", "cancelled"}
        ):
            run = service.recover_interrupted(
                run_id,
                cancelled=(
                    latest_job.status == "cancelled"
                    or run.get("status") == "cancelling"
                ),
            )
    except TaxonomySuggestionNotFound as exc:
        raise ApiError(
            404,
            "taxonomy_suggestion_not_found",
            "Taxonomy suggestion run not found",
            {"run_id": run_id},
        ) from exc
    except (OSError, TimeoutError, RuntimeError, ValueError) as exc:
        _raise_taxonomy_storage_api_error(exc)
    return TaxonomySuggestionRunResponse(**run)


@router.post(
    "/taxonomy/suggestions/{run_id}/retry",
    response_model=TaxonomySuggestionStartResponse,
    status_code=202,
    responses=TAXONOMY_SUGGESTION_ERROR_RESPONSES,
)
def retry_taxonomy_suggestions(
    run_id: str,
    body: TaxonomySuggestionRetryRequest,
    service: TaxonomySuggestionService = Depends(
        get_taxonomy_suggestion_service
    ),
    manager: JobManager = Depends(get_job_manager),
) -> TaxonomySuggestionStartResponse:
    token = body.request_token.lower()
    try:
        run = service.get_run(run_id)
        if run.get("stale"):
            raise ApiError(
                409,
                "taxonomy_suggestion_stale",
                "Pending proposals changed; start a new suggestion run",
            )
        operation = (
            "process"
            if run.get("status") in {"queued", "running"}
            else "retry"
        )
        job, _created = manager.submit_idempotent_taxonomy_suggestion(
            {
                "run_id": run["run_id"],
                "operation": operation,
                "client_request_token": token,
            }
        )
    except ApiError:
        raise
    except TaxonomySuggestionNotFound as exc:
        raise ApiError(
            404,
            "taxonomy_suggestion_not_found",
            "Taxonomy suggestion run not found",
            {"run_id": run_id},
        ) from exc
    except TaxonomySuggestionJobRequestConflictError as exc:
        raise ApiError(
            409,
            "taxonomy_suggestion_job_request_conflict",
            "This suggestion job request token was already used",
        ) from exc
    except TaxonomySuggestionJobBusyError as exc:
        raise ApiError(
            409,
            "taxonomy_suggestion_busy",
            "This suggestion run already has active work",
        ) from exc
    except ValueError as exc:
        raise ApiError(
            422,
            "taxonomy_suggestion_invalid",
            "Taxonomy suggestion retry is invalid",
        ) from exc
    except UnsupportedJobTypeError as exc:
        raise ApiError(
            503,
            "taxonomy_suggestion_unavailable",
            "Taxonomy suggestion is temporarily unavailable",
        ) from exc
    except (OSError, TimeoutError, RuntimeError) as exc:
        _raise_taxonomy_storage_api_error(exc)
    return TaxonomySuggestionStartResponse(
        job=_job_response(job),
        run=TaxonomySuggestionRunResponse(**run),
    )


@router.post(
    "/taxonomy/suggestions/{run_id}/cancel",
    response_model=TaxonomySuggestionRunResponse,
    responses=TAXONOMY_SUGGESTION_ERROR_RESPONSES,
)
def cancel_taxonomy_suggestions(
    run_id: str,
    service: TaxonomySuggestionService = Depends(
        get_taxonomy_suggestion_service
    ),
    manager: JobManager = Depends(get_job_manager),
) -> TaxonomySuggestionRunResponse:
    try:
        run = service.cancel_run(run_id)
        for job in _active_taxonomy_suggestion_jobs(manager, run_id):
            manager.cancel(job.id)
    except TaxonomySuggestionNotFound as exc:
        raise ApiError(
            404,
            "taxonomy_suggestion_not_found",
            "Taxonomy suggestion run not found",
            {"run_id": run_id},
        ) from exc
    except (OSError, TimeoutError, RuntimeError, ValueError) as exc:
        _raise_taxonomy_storage_api_error(exc)
    return TaxonomySuggestionRunResponse(**run)


@router.post(
    "/taxonomy/suggestions/{run_id}/batch-preview",
    response_model=TaxonomySuggestionBatchPreviewResponse,
    responses=TAXONOMY_REVIEW_ERROR_RESPONSES,
)
def preview_taxonomy_suggestion_batch(
    run_id: str,
    body: TaxonomySuggestionBatchPreviewRequest,
    suggestion_service: TaxonomySuggestionService = Depends(
        get_taxonomy_suggestion_service
    ),
    review_service: TaxonomyReviewService = Depends(
        get_taxonomy_review_service
    ),
) -> TaxonomySuggestionBatchPreviewResponse:
    try:
        run = suggestion_service.get_run(run_id)
        payload = review_service.preview_suggestion_batch(
            run,
            base_revision=body.base_revision,
            policy_version=body.policy_version,
        )
    except TaxonomySuggestionNotFound as exc:
        raise ApiError(404, "taxonomy_suggestion_not_found", "Suggestion run not found") from exc
    except (TaxonomyReviewSelectionInvalid, ValueError) as exc:
        raise ApiError(409, "taxonomy_suggestion_batch_stale", "Suggestion evidence changed") from exc
    except (TaxonomyStorageError, OSError, TimeoutError, RuntimeError) as exc:
        _raise_taxonomy_storage_api_error(exc)
    return TaxonomySuggestionBatchPreviewResponse(**payload)


@router.post(
    "/taxonomy/suggestions/{run_id}/apply",
    response_model=TaxonomyReviewOperationResponse,
    responses=TAXONOMY_REVIEW_ERROR_RESPONSES,
)
def apply_taxonomy_suggestion_batch(
    run_id: str,
    body: TaxonomySuggestionBatchApplyRequest,
    suggestion_service: TaxonomySuggestionService = Depends(
        get_taxonomy_suggestion_service
    ),
    review_service: TaxonomyReviewService = Depends(
        get_taxonomy_review_service
    ),
) -> TaxonomyReviewOperationResponse:
    try:
        run = suggestion_service.get_run(run_id)
        payload = review_service.apply_suggestion_batch(
            run,
            base_revision=body.base_revision,
            request_token=body.request_token.lower(),
            policy_version=body.policy_version,
            accepted_manual_decisions=[
                item.model_dump() for item in body.accepted_manual_decisions
            ],
        )
    except TaxonomySuggestionNotFound as exc:
        raise ApiError(404, "taxonomy_suggestion_not_found", "Suggestion run not found") from exc
    except TaxonomyRevisionConflict as exc:
        raise ApiError(
            409,
            "taxonomy_revision_conflict",
            "Taxonomy state changed; refresh and retry",
            {"current_revision": exc.current_revision},
        ) from exc
    except TaxonomyReviewRequestConflict as exc:
        raise ApiError(409, "taxonomy_review_request_conflict", "Request token was reused") from exc
    except (TaxonomyReviewSelectionInvalid, TaxonomyReviewInvalid, ValueError) as exc:
        raise ApiError(422, "taxonomy_review_invalid", "Taxonomy batch is invalid") from exc
    except (TaxonomyStorageError, OSError, TimeoutError, RuntimeError) as exc:
        _raise_taxonomy_storage_api_error(exc)
    return TaxonomyReviewOperationResponse(**payload)


@router.get(
    "/taxonomy/review-operations/{operation_id}",
    response_model=TaxonomyReviewOperationResponse,
    responses=TAXONOMY_REVIEW_ERROR_RESPONSES,
)
def get_taxonomy_review_operation(
    operation_id: str,
    service: TaxonomyReviewService = Depends(get_taxonomy_review_service),
) -> TaxonomyReviewOperationResponse:
    try:
        payload = service.read_operation(operation_id)
    except TaxonomyReviewApplicationNotFound as exc:
        raise ApiError(404, "taxonomy_review_operation_not_found", "Operation not found") from exc
    except (TaxonomyStorageError, OSError, TimeoutError, RuntimeError) as exc:
        _raise_taxonomy_storage_api_error(exc)
    return TaxonomyReviewOperationResponse(**payload)


@router.post(
    "/taxonomy/review-operations/{operation_id}/undo",
    response_model=TaxonomyReviewOperationResponse,
    responses=TAXONOMY_REVIEW_ERROR_RESPONSES,
)
def undo_taxonomy_review_operation(
    operation_id: str,
    body: TaxonomyReviewOperationUndoRequest,
    service: TaxonomyReviewService = Depends(get_taxonomy_review_service),
) -> TaxonomyReviewOperationResponse:
    try:
        payload = service.undo_operation(
            operation_id=operation_id,
            expected_revision=body.expected_revision,
            request_token=body.request_token.lower(),
        )
    except TaxonomyReviewApplicationNotFound as exc:
        raise ApiError(404, "taxonomy_review_operation_not_found", "Operation not found") from exc
    except (TaxonomyReviewUndoConflict, TaxonomyRevisionConflict) as exc:
        raise ApiError(409, "taxonomy_review_undo_conflict", "Later changes prevent undo") from exc
    except (TaxonomyStorageError, OSError, TimeoutError, RuntimeError) as exc:
        _raise_taxonomy_storage_api_error(exc)
    return TaxonomyReviewOperationResponse(**payload)


@router.post(
    "/taxonomy/proposals/{proposal_id}/review",
    response_model=TaxonomyProposalReviewResponse,
    responses=TAXONOMY_REVIEW_ERROR_RESPONSES,
)
def review_taxonomy_proposal(
    proposal_id: str,
    body: TaxonomyProposalReviewRequest,
    service: TaxonomyReviewService = Depends(get_taxonomy_review_service),
) -> TaxonomyProposalReviewResponse:
    try:
        payload = service.review_proposal(
            proposal_id=proposal_id,
            decision=body.decision,
            edited_name=body.edited_name,
            target_term_ids=[
                *body.target_term_ids,
                *([body.target_term_id] if body.target_term_id else []),
            ],
            question_ids=body.question_ids,
            expected_revision=body.expected_revision,
            request_token=body.request_token.lower(),
        )
    except TaxonomyRevisionConflict as exc:
        raise ApiError(
            409,
            "taxonomy_revision_conflict",
            "Taxonomy state changed; refresh and retry",
            {"current_revision": exc.current_revision},
        ) from exc
    except TaxonomyProposalNotFound as exc:
        raise ApiError(
            404,
            "taxonomy_proposal_not_found",
            "Taxonomy proposal not found",
            {"proposal_id": proposal_id},
        ) from exc
    except TaxonomyTargetTermNotFound as exc:
        raise ApiError(
            422,
            "taxonomy_target_term_not_found",
            "Taxonomy merge target is invalid",
        ) from exc
    except TaxonomyReviewRequestConflict as exc:
        raise ApiError(
            409,
            "taxonomy_review_request_conflict",
            "This review request token was already used for another decision",
        ) from exc
    except (TaxonomyReviewSelectionInvalid, TaxonomyReviewInvalid, ValueError) as exc:
        raise ApiError(
            422,
            "taxonomy_review_invalid",
            "Taxonomy review is invalid",
        ) from exc
    except (TaxonomyStorageError, OSError, TimeoutError, RuntimeError) as exc:
        _raise_taxonomy_storage_api_error(exc)
    return _taxonomy_review_response(payload)


@router.post(
    "/taxonomy/review-applications/retry",
    response_model=TaxonomyProposalReviewResponse,
    responses=TAXONOMY_REVIEW_ERROR_RESPONSES,
)
def retry_taxonomy_review_application(
    body: TaxonomyProposalApplicationRetryRequest,
    service: TaxonomyReviewService = Depends(get_taxonomy_review_service),
) -> TaxonomyProposalReviewResponse:
    try:
        payload = service.retry_application(
            application_token=body.application_token.lower(),
            request_token=body.request_token.lower(),
            question_ids=body.question_ids,
        )
    except TaxonomyReviewApplicationNotFound as exc:
        raise ApiError(
            404,
            "taxonomy_review_application_not_found",
            "Taxonomy review application not found",
        ) from exc
    except TaxonomyReviewRequestConflict as exc:
        raise ApiError(
            409,
            "taxonomy_review_request_conflict",
            "This review request token was already used for another decision",
        ) from exc
    except (TaxonomyReviewSelectionInvalid, ValueError) as exc:
        raise ApiError(
            422,
            "taxonomy_review_invalid",
            "Taxonomy review application retry is invalid",
        ) from exc
    except (TaxonomyStorageError, OSError, TimeoutError, RuntimeError) as exc:
        _raise_taxonomy_storage_api_error(exc)
    return _taxonomy_review_response(payload)


def _taxonomy_review_response(
    payload: dict,
) -> TaxonomyProposalReviewResponse:
    application = payload.get("application")
    status = (
        str(application.get("status") or "not_requested")
        if isinstance(application, dict)
        else "not_requested"
    )
    return TaxonomyProposalReviewResponse(
        revision=payload["revision"],
        proposal=payload["proposal"],
        approved_term=payload.get("approved_term"),
        approved_terms=payload.get("approved_terms") or [],
        application=(
            application
            if isinstance(application, dict)
            else {
                "status": "not_requested",
                "selected_question_ids": [],
                "applied_question_ids": [],
                "failures": [],
            }
        ),
        application_status=status,
        application_token=str(payload.get("request_token") or "").lower()
        or None,
    )


def _active_taxonomy_suggestion_jobs(
    manager: JobManager,
    run_id: str,
) -> list[JobRecord]:
    return _taxonomy_suggestion_jobs(
        manager,
        run_id,
        statuses=("queued", "running", "paused"),
    )


def _latest_taxonomy_suggestion_job(
    manager: JobManager,
    run_id: str,
) -> JobRecord | None:
    jobs = _taxonomy_suggestion_jobs(manager, run_id)
    return jobs[0] if jobs else None


def _taxonomy_suggestion_jobs(
    manager: JobManager,
    run_id: str,
    *,
    statuses: tuple[str, ...] = (),
) -> list[JobRecord]:
    matched: list[JobRecord] = []
    offset = 0
    while True:
        jobs, total = manager.list(
            job_types=("taxonomy_suggestion",),
            statuses=statuses,
            limit=100,
            offset=offset,
        )
        matched.extend(
            job
            for job in jobs
            if str(job.payload.get("run_id") or "") == str(run_id)
        )
        offset += len(jobs)
        if not jobs or offset >= total:
            return matched


@router.put(
    "/questions/{question_id}/tags",
    response_model=QuestionWriteResponse,
    responses=QUESTION_WRITE_ERROR_RESPONSES,
)
def replace_question_tags(
    question_id: int,
    body: QuestionTagWriteRequest,
    service: QuestionBankWriteService = Depends(get_question_bank_write_service),
) -> QuestionWriteResponse:
    try:
        result = service.replace_tags(
            question_id,
            expected_revision=body.expected_revision,
            tags=[
                ConfirmedQuestionTag(tag.tag_type, tag.tag_value, tag.confidence)
                for tag in body.tags
            ],
        )
    except ValueError as exc:
        raise ApiError(
            422,
            "question_tags_invalid",
            "Question tags are invalid",
        ) from exc
    except (QuestionWriteNotFound, QuestionWriteConflict) as exc:
        _raise_question_write_api_error(exc, question_id)
    return _question_write_response(result)


@router.delete(
    "/questions/{question_id}",
    response_model=QuestionWriteResponse,
    responses=QUESTION_WRITE_ERROR_RESPONSES,
)
def delete_question(
    question_id: int,
    body: QuestionStateChangeRequest,
    service: QuestionBankWriteService = Depends(get_question_bank_write_service),
) -> QuestionWriteResponse:
    try:
        result = service.set_deleted(
            question_id,
            expected_revision=body.expected_revision,
            deleted=True,
        )
    except (QuestionWriteNotFound, QuestionWriteConflict) as exc:
        _raise_question_write_api_error(exc, question_id)
    return _question_write_response(result)


@router.post(
    "/questions/{question_id}/restore",
    response_model=QuestionWriteResponse,
    responses=QUESTION_WRITE_ERROR_RESPONSES,
)
def restore_question(
    question_id: int,
    body: QuestionStateChangeRequest,
    service: QuestionBankWriteService = Depends(get_question_bank_write_service),
) -> QuestionWriteResponse:
    try:
        result = service.set_deleted(
            question_id,
            expected_revision=body.expected_revision,
            deleted=False,
        )
    except (QuestionWriteNotFound, QuestionWriteConflict) as exc:
        _raise_question_write_api_error(exc, question_id)
    return _question_write_response(result)


@router.post(
    "/import-uploads",
    response_model=QuestionImportUploadResponse,
    status_code=201,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/octet-stream": {
                    "schema": {"type": "string", "format": "binary"}
                }
            },
        }
    },
    responses={
        403: {
            "model": ErrorResponse,
            "description": "Question import storage is unavailable",
        },
        413: {
            "model": ErrorResponse,
            "description": "Question import upload is too large",
        },
        415: {
            "model": ErrorResponse,
            "description": "Question import type is not supported",
        }
    },
)
async def stage_question_import_upload(
    request: Request,
    filename: str = Query(min_length=1, max_length=255),
    service: QuestionBankWriteService = Depends(get_question_bank_write_service),
) -> QuestionImportUploadResponse:
    try:
        content_length = request.headers.get("content-length")
        if content_length is not None and int(content_length) > service.max_upload_bytes:
            raise QuestionImportTooLarge("Question import upload is too large")
        upload = await service.stage_upload_stream(
            filename=filename,
            chunks=request.stream(),
        )
    except QuestionImportTooLarge as exc:
        raise ApiError(
            413,
            "question_import_too_large",
            "Question import upload is too large",
        ) from exc
    except QuestionImportStorageForbidden as exc:
        raise ApiError(
            403,
            "question_import_storage_forbidden",
            "Question import storage is unavailable",
        ) from exc
    except QuestionImportTypeNotSupported as exc:
        raise ApiError(
            415,
            "question_import_type_not_supported",
            "Question import file type is not supported",
        ) from exc
    except ValueError as exc:
        raise ApiError(
            422,
            "question_import_upload_invalid",
            "Question import upload is invalid",
        ) from exc
    return QuestionImportUploadResponse(**upload.to_dict())


@router.post(
    "/import-requests",
    response_model=QuestionImportRequestResponse,
    status_code=201,
    responses={
        403: {
            "model": ErrorResponse,
            "description": "Question import storage is unavailable",
        },
        404: {
            "model": ErrorResponse,
            "description": "Question import upload not found",
        }
    },
)
def create_question_import_request(
    body: QuestionImportRequestCreate,
    service: QuestionBankWriteService = Depends(get_question_bank_write_service),
) -> QuestionImportRequestResponse:
    try:
        request = service.create_import_request(upload_id=body.upload_id)
    except QuestionImportStorageForbidden as exc:
        raise ApiError(
            403,
            "question_import_storage_forbidden",
            "Question import storage is unavailable",
        ) from exc
    except QuestionImportUploadNotFound as exc:
        raise ApiError(
            404,
            "question_import_upload_not_found",
            "Question import upload not found",
        ) from exc
    return QuestionImportRequestResponse(**request.to_dict())


QUESTION_JOB_RESPONSES = {
    404: {"model": ErrorResponse, "description": "Question bank job source not found"},
    409: {"model": ErrorResponse, "description": "Question bank job cannot be retried"},
    503: {"model": ErrorResponse, "description": "Question bank job type unavailable"},
}
QUESTION_IMPORT_SUBMIT_RESPONSES = {
    404: {"model": ErrorResponse, "description": "Question import request not found"},
    503: {"model": ErrorResponse, "description": "Question import job type unavailable"},
}


def _import_paper_defaults(
    curriculum_volume_id: str | None,
) -> dict[str, str] | None:
    """把可选的教材册别转成导入默认值；文件名已推断的字段仍然优先。"""
    clean_id = str(curriculum_volume_id or "").strip()
    if not clean_id:
        return None
    volume = curriculum_volume(volume_id=clean_id)
    if volume is None:
        raise ApiError(
            422,
            "curriculum_volume_invalid",
            "请选择有效的教材册别后再导入",
        )
    return {
        "grade": str(volume["grade"]),
        "semester": str(volume["semester"]),
        "textbook_version": str(volume["textbook_version"]),
    }


@router.post(
    "/import-requests/{request_id}/jobs",
    response_model=JobResponse,
    status_code=202,
    responses=QUESTION_IMPORT_SUBMIT_RESPONSES,
)
def submit_question_import_job(
    request_id: str,
    body: QuestionImportJobSubmitRequest | None = None,
    service: QuestionBankWriteService = Depends(get_question_bank_write_service),
    manager: JobManager = Depends(get_job_manager),
) -> JobResponse:
    try:
        resource = service.load_import_resource(request_id)
    except QuestionImportUploadNotFound as exc:
        raise ApiError(
            404,
            "question_import_request_not_found",
            "Question import request not found",
        ) from exc
    payload: dict[str, Any] = {"request_id": resource.request_id}
    paper_defaults = _import_paper_defaults(
        None if body is None else body.curriculum_volume_id
    )
    if paper_defaults is not None:
        payload["paper_defaults"] = paper_defaults
    return _submit_question_bank_job(
        manager,
        "question_import",
        payload,
    )


@router.post(
    "/question-import-jobs/{job_id}/retry",
    response_model=JobResponse,
    status_code=202,
    responses=QUESTION_JOB_RESPONSES,
)
def retry_question_import_job(
    job_id: int,
    service: QuestionBankWriteService = Depends(get_question_bank_write_service),
    manager: JobManager = Depends(get_job_manager),
) -> JobResponse:
    source = _require_question_bank_job(
        manager,
        job_id,
        "question_import",
        "question_import_job_not_found",
    )
    retryable = source.status in {"failed", "cancelled"} or bool(
        source.result.get("retryable")
    )
    if not retryable:
        raise ApiError(
            409,
            "question_import_retry_not_available",
            "Question import job cannot be retried",
            {"job_id": int(job_id)},
        )
    request_id = str(source.payload.get("request_id") or "")
    try:
        resource = service.load_import_resource(request_id)
    except QuestionImportUploadNotFound as exc:
        raise ApiError(
            404,
            "question_import_request_not_found",
            "Question import request not found",
        ) from exc
    payload: dict[str, Any] = {
        "request_id": resource.request_id,
        "retry_of_job_id": source.id,
    }
    # 首次提交时选择的教材册别要在重试中保留，否则补齐的元数据会丢失。
    paper_defaults = source.payload.get("paper_defaults")
    if isinstance(paper_defaults, dict):
        payload["paper_defaults"] = dict(paper_defaults)
    return _submit_question_bank_job(
        manager,
        "question_import",
        payload,
    )


@router.post(
    "/tagging-jobs",
    response_model=JobResponse,
    status_code=202,
    responses=QUESTION_JOB_RESPONSES,
)
def submit_tagging_sync_job(
    body: QuestionTaggingJobRequest,
    manager: JobManager = Depends(get_job_manager),
) -> JobResponse:
    question_ids = _unique_positive_ids(body.question_ids)
    volume = curriculum_volume(volume_id=body.curriculum_volume_id)
    if volume is None:
        raise ApiError(
            422,
            "curriculum_volume_invalid",
            "请选择有效的教材册别后再继续分析",
        )
    if body.source_job_id is not None:
        source = _require_question_bank_job(
            manager,
            body.source_job_id,
            "question_import",
            "question_import_job_not_found",
        )
        available = {
            int(item) for item in source.result.get("successful_question_ids", [])
        }
        if source.status != "succeeded" or not set(question_ids).issubset(available):
            raise ApiError(
                409,
                "question_tagging_request_invalid",
                "Question IDs are not available from the import job",
            )
    payload: dict[str, object] = {
        "question_ids": question_ids,
        "curriculum_volume_id": str(volume["id"]),
    }
    if body.force_retag:
        payload["force_retag_question_ids"] = question_ids
    if body.source_job_id is not None:
        payload["source_job_id"] = int(body.source_job_id)
    if body.client_request_token is None:
        return _submit_question_bank_job(manager, "tagging_sync", payload)
    payload["client_request_token"] = body.client_request_token
    try:
        job, _created = manager.submit_idempotent_tagging_sync(payload)
    except TaggingSyncJobRequestConflictError as exc:
        raise ApiError(
            409,
            "question_tagging_request_conflict",
            "This tagging request token was already used for other questions",
        ) from exc
    except UnsupportedJobTypeError as exc:
        raise ApiError(
            503,
            "job_type_not_supported",
            "Question bank job type is unavailable",
            {"job_type": "tagging_sync"},
        ) from exc
    return _job_response(job)


@router.post(
    "/tagging-jobs/{job_id}/retry",
    response_model=JobResponse,
    status_code=202,
    responses=QUESTION_JOB_RESPONSES,
)
def retry_tagging_sync_job(
    job_id: int,
    body: QuestionJobRetryRequest,
    manager: JobManager = Depends(get_job_manager),
) -> JobResponse:
    source = _require_question_bank_job(
        manager,
        job_id,
        "tagging_sync",
        "tagging_sync_job_not_found",
    )
    if source.status in {"failed", "cancelled"}:
        available = _unique_positive_ids(source.payload.get("question_ids", []))
    elif (
        source.status == "succeeded"
        and bool(source.result.get("retryable"))
        and source.result.get("failed_question_ids")
    ):
        available = _unique_positive_ids(source.result["failed_question_ids"])
    else:
        available = []
    if not available:
        raise ApiError(
            409,
            "tagging_sync_retry_not_available",
            "Tagging sync job cannot retry the requested questions",
            {"job_id": int(job_id)},
        )
    selected = _unique_positive_ids(body.question_ids or available)
    if not set(selected).issubset(set(available)):
        raise ApiError(
            409,
            "tagging_sync_retry_not_available",
            "Tagging sync job cannot retry the requested questions",
            {"job_id": int(job_id)},
        )
    payload: dict[str, object] = {
        "question_ids": selected,
        "retry_of_job_id": source.id,
    }
    volume = curriculum_volume(
        volume_id=source.payload.get("curriculum_volume_id")
    )
    if volume is None:
        raise ApiError(
            409,
            "curriculum_volume_required",
            "原任务没有保存教材册别，请从试卷库点击继续完成",
            {"job_id": int(job_id)},
        )
    payload["curriculum_volume_id"] = str(volume["id"])
    raw_evidence_failed = source.result.get("evidence_failed_question_ids")
    evidence_failed = (
        set(_unique_positive_ids(raw_evidence_failed))
        if raw_evidence_failed
        else set()
    )
    retry_evidence_ids = [
        question_id for question_id in selected if question_id in evidence_failed
    ]
    if retry_evidence_ids:
        payload["retry_evidence_question_ids"] = retry_evidence_ids
    if source.payload.get("source_job_id") is not None:
        payload["source_job_id"] = int(source.payload["source_job_id"])
    return _submit_question_bank_job(manager, "tagging_sync", payload)


@router.post(
    "/answer-draft-jobs",
    response_model=JobResponse,
    status_code=202,
    responses=QUESTION_JOB_RESPONSES,
)
def submit_answer_draft_job(
    body: AnswerDraftJobRequest,
    manager: JobManager = Depends(get_job_manager),
) -> JobResponse:
    question_ids = _unique_positive_ids(body.question_ids)
    return _submit_question_bank_job(
        manager,
        "answer_draft",
        {"question_ids": question_ids},
    )


@router.post(
    "/answer-draft-jobs/{job_id}/retry",
    response_model=JobResponse,
    status_code=202,
    responses=QUESTION_JOB_RESPONSES,
)
def retry_answer_draft_job(
    job_id: int,
    body: QuestionJobRetryRequest,
    manager: JobManager = Depends(get_job_manager),
) -> JobResponse:
    source = _require_question_bank_job(
        manager,
        job_id,
        "answer_draft",
        "answer_draft_job_not_found",
    )
    if source.status in {"failed", "cancelled"}:
        raw_available = source.payload.get("question_ids") or []
    elif source.status == "succeeded" and bool(source.result.get("retryable")):
        raw_available = source.result.get("failed_question_ids") or []
    else:
        raw_available = []
    available = _unique_positive_ids(raw_available) if raw_available else []
    if not available:
        raise ApiError(
            409,
            "answer_draft_retry_not_available",
            "Answer draft job cannot retry the requested questions",
            {"job_id": int(job_id)},
        )
    selected = _unique_positive_ids(body.question_ids or available)
    if not set(selected).issubset(set(available)):
        raise ApiError(
            409,
            "answer_draft_retry_not_available",
            "Answer draft job cannot retry the requested questions",
            {"job_id": int(job_id)},
        )
    return _submit_question_bank_job(
        manager,
        "answer_draft",
        {"question_ids": selected, "retry_of_job_id": source.id},
    )


def _submit_question_bank_job(
    manager: JobManager,
    job_type: str,
    payload: dict[str, object],
) -> JobResponse:
    try:
        return _job_response(manager.submit(job_type, payload))
    except UnsupportedJobTypeError as exc:
        raise ApiError(
            503,
            "job_type_not_supported",
            "Question bank job type is unavailable",
            {"job_type": job_type},
        ) from exc


def _require_question_bank_job(
    manager: JobManager,
    job_id: int,
    job_type: str,
    error_code: str,
) -> JobRecord:
    job = manager.get(int(job_id))
    if job is None or job.job_type != job_type:
        raise ApiError(
            404,
            error_code,
            "Question bank job not found",
            {"job_id": int(job_id)},
        )
    return job


def _unique_positive_ids(values) -> list[int]:
    result: list[int] = []
    seen: set[int] = set()
    for value in values:
        question_id = int(value)
        if question_id <= 0:
            raise ApiError(
                422,
                "question_tagging_request_invalid",
                "Question IDs must be positive integers",
            )
        if question_id not in seen:
            seen.add(question_id)
            result.append(question_id)
    if not result:
        raise ApiError(
            422,
            "question_tagging_request_invalid",
            "At least one question ID is required",
        )
    return result


@router.get(
    "/criteria/questions/{question_id}",
    response_model=TrainingCriterionWorkspaceResponse,
    responses={404: {"model": ErrorResponse}},
)
def get_training_criterion_workspace(
    question_id: int,
    module: TrainingCriterionModule = Depends(
        get_training_criterion_module
    ),
    question_bank_db_path: Path = Depends(get_question_bank_db_path),
    data_root: Path = Depends(get_data_root),
) -> TrainingCriterionWorkspaceResponse:
    question = _load_criterion_question(
        question_id,
        db_path=question_bank_db_path,
        data_root=data_root,
    )
    return TrainingCriterionWorkspaceResponse(**module.read(question))


@router.get(
    "/criteria/versions/{version_id}",
    response_model=TrainingCriterionVersionResponse,
    responses={404: {"model": ErrorResponse}},
)
def get_training_criterion_version(
    version_id: str,
    module: TrainingCriterionModule = Depends(
        get_training_criterion_module
    ),
) -> TrainingCriterionVersionResponse:
    try:
        payload = module.get_version(version_id)
    except CriterionVersionNotFound as exc:
        raise ApiError(
            404,
            "criterion_version_not_found",
            "Training criterion version was not found",
        ) from exc
    return TrainingCriterionVersionResponse(**payload)


@router.post(
    "/criteria/questions/{question_id}/drafts",
    response_model=TrainingCriterionWorkspaceResponse,
    responses=CRITERION_WRITE_ERROR_RESPONSES,
)
def edit_training_criterion_draft(
    question_id: int,
    body: TrainingCriterionDraftWriteRequest,
    module: TrainingCriterionModule = Depends(
        get_training_criterion_module
    ),
    question_bank_db_path: Path = Depends(get_question_bank_db_path),
    data_root: Path = Depends(get_data_root),
) -> TrainingCriterionWorkspaceResponse:
    question = _load_criterion_question(
        question_id,
        db_path=question_bank_db_path,
        data_root=data_root,
    )
    try:
        payload = module.edit(
            question=question,
            criteria={
                "schema_version": "training-criteria-draft-v1",
                "question_id": question_id,
                "points": [
                    item.model_dump() for item in body.points
                ],
                "auxiliary_rules": list(body.auxiliary_rules),
                "rationale": body.rationale,
                "confidence": body.confidence,
            },
            expected_revision=body.expected_revision,
            parent_version_id=body.parent_version_id,
            request_token=body.request_token.lower(),
            actor_ref="local_teacher",
            reason=body.reason,
        )
    except CriterionRevisionConflict as exc:
        raise ApiError(
            409,
            "criterion_revision_conflict",
            "Training criteria changed; refresh before saving",
            {"current_revision": exc.current_revision},
        ) from exc
    except CriterionRequestConflict as exc:
        raise ApiError(
            409,
            "criterion_request_conflict",
            "This criterion request token was already used",
        ) from exc
    except CriterionVersionNotFound as exc:
        raise ApiError(
            404,
            "criterion_version_not_found",
            "Parent criterion version was not found",
        ) from exc
    except (TypeError, ValueError) as exc:
        raise ApiError(
            422,
            "criterion_draft_invalid",
            "Training criterion draft is invalid",
        ) from exc
    return TrainingCriterionWorkspaceResponse(**payload)


@router.post(
    "/criteria/questions/{question_id}/review",
    response_model=TrainingCriterionWorkspaceResponse,
    responses=CRITERION_WRITE_ERROR_RESPONSES,
)
def review_training_criterion(
    question_id: int,
    body: TrainingCriterionReviewRequest,
    module: TrainingCriterionModule = Depends(
        get_training_criterion_module
    ),
    question_bank_db_path: Path = Depends(get_question_bank_db_path),
    data_root: Path = Depends(get_data_root),
) -> TrainingCriterionWorkspaceResponse:
    question = _load_criterion_question(
        question_id,
        db_path=question_bank_db_path,
        data_root=data_root,
    )
    try:
        payload = module.review(
            CriterionReviewCommand(
                question_id=question_id,
                version_id=body.version_id,
                expected_revision=body.expected_revision,
                action=body.action,
                actor_ref="local_teacher",
                reason=body.reason,
            ),
            question=question,
        )
    except CriterionRevisionConflict as exc:
        raise ApiError(
            409,
            "criterion_revision_conflict",
            "Training criteria changed; refresh before reviewing",
            {"current_revision": exc.current_revision},
        ) from exc
    except CriterionQualityError as exc:
        raise ApiError(
            422,
            "criterion_quality_failed",
            "Training criteria must pass the quality gate before approval",
            {"quality_codes": list(exc.quality_codes)},
        ) from exc
    except CriterionVersionNotFound as exc:
        raise ApiError(
            404,
            "criterion_version_not_found",
            "Training criterion version was not found",
        ) from exc
    except (CriterionTransitionError, TypeError, ValueError) as exc:
        raise ApiError(
            422,
            "criterion_review_invalid",
            "Training criterion review is invalid",
        ) from exc
    return TrainingCriterionWorkspaceResponse(**payload)


@router.post(
    "/criteria/backfill-runs",
    response_model=TrainingCriterionBackfillStartResponse,
    status_code=202,
    responses=CRITERION_WRITE_ERROR_RESPONSES,
)
def start_training_criterion_backfill(
    body: TrainingCriterionBackfillCreateRequest,
    module: TrainingCriterionModule = Depends(
        get_training_criterion_module
    ),
    manager: JobManager = Depends(get_job_manager),
) -> TrainingCriterionBackfillStartResponse:
    try:
        run, created = module.create_backfill_run(
            question_ids=body.question_ids,
            request_token=body.request_token.lower(),
            mode=body.mode,
        )
    except CriterionRequestConflict as exc:
        raise ApiError(
            409,
            "criterion_backfill_request_conflict",
            "This backfill request token was already used",
        ) from exc
    except CriterionVersionNotFound as exc:
        raise ApiError(
            404,
            "criterion_backfill_question_not_found",
            "One or more selected questions were not found",
        ) from exc
    except (TypeError, ValueError) as exc:
        raise ApiError(
            422,
            "criterion_backfill_invalid",
            "Criterion backfill selection is invalid",
        ) from exc
    job = _active_criterion_backfill_job(manager, run["run_id"])
    if created or (job is None and run["status"] == "pending"):
        try:
            job = manager.submit(
                "criterion_backfill",
                {"run_id": run["run_id"]},
            )
        except UnsupportedJobTypeError as exc:
            raise ApiError(
                503,
                "criterion_backfill_unavailable",
                "Criterion backfill is temporarily unavailable",
            ) from exc
    return TrainingCriterionBackfillStartResponse(
        run=TrainingCriterionBackfillRunResponse(**run),
        job=None if job is None else _job_response(job),
    )


@router.get(
    "/criteria/backfill-runs/{run_id}",
    response_model=TrainingCriterionBackfillRunResponse,
    responses={404: {"model": ErrorResponse}},
)
def get_training_criterion_backfill(
    run_id: str,
    module: TrainingCriterionModule = Depends(
        get_training_criterion_module
    ),
) -> TrainingCriterionBackfillRunResponse:
    try:
        payload = module.get_backfill_run(run_id)
    except CriterionVersionNotFound as exc:
        raise ApiError(
            404,
            "criterion_backfill_not_found",
            "Criterion backfill run was not found",
        ) from exc
    return TrainingCriterionBackfillRunResponse(**payload)


@router.post(
    "/criteria/backfill-runs/{run_id}/cancel",
    response_model=TrainingCriterionBackfillRunResponse,
    responses={404: {"model": ErrorResponse}},
)
def cancel_training_criterion_backfill(
    run_id: str,
    module: TrainingCriterionModule = Depends(
        get_training_criterion_module
    ),
    manager: JobManager = Depends(get_job_manager),
) -> TrainingCriterionBackfillRunResponse:
    job = _active_criterion_backfill_job(manager, run_id)
    try:
        payload = module.get_backfill_run(run_id)
    except CriterionVersionNotFound as exc:
        raise ApiError(
            404,
            "criterion_backfill_not_found",
            "Criterion backfill run was not found",
        ) from exc
    if job is not None:
        manager.cancel(job.id)
    if job is None or job.status == "queued":
        payload = module.cancel_backfill(run_id)
    return TrainingCriterionBackfillRunResponse(**payload)


@router.post(
    "/criteria/backfill-runs/{run_id}/retry",
    response_model=TrainingCriterionBackfillStartResponse,
    status_code=202,
    responses=CRITERION_WRITE_ERROR_RESPONSES,
)
def retry_training_criterion_backfill(
    run_id: str,
    body: TrainingCriterionBackfillRetryRequest,
    module: TrainingCriterionModule = Depends(
        get_training_criterion_module
    ),
    manager: JobManager = Depends(get_job_manager),
) -> TrainingCriterionBackfillStartResponse:
    try:
        source = module.recover_backfill(run_id)
    except CriterionVersionNotFound as exc:
        raise ApiError(
            404,
            "criterion_backfill_not_found",
            "Criterion backfill run was not found",
        ) from exc
    retryable = {
        int(item["question_id"])
        for item in source["items"]
        if item["status"] in {"failed", "cancelled"}
    }
    selected = set(body.question_ids or sorted(retryable))
    if not selected or not selected.issubset(retryable):
        raise ApiError(
            409,
            "criterion_backfill_retry_not_available",
            "Only failed or cancelled questions can be retried",
        )
    try:
        run, _created = module.create_backfill_run(
            question_ids=sorted(selected),
            request_token=body.request_token.lower(),
            mode=source["mode"],
        )
        job = manager.submit(
            "criterion_backfill",
            {"run_id": run["run_id"], "retry_of_run_id": run_id},
        )
    except CriterionRequestConflict as exc:
        raise ApiError(
            409,
            "criterion_backfill_request_conflict",
            "This backfill request token was already used",
        ) from exc
    except UnsupportedJobTypeError as exc:
        raise ApiError(
            503,
            "criterion_backfill_unavailable",
            "Criterion backfill is temporarily unavailable",
        ) from exc
    return TrainingCriterionBackfillStartResponse(
        run=TrainingCriterionBackfillRunResponse(**run),
        job=_job_response(job),
    )


def _load_criterion_question(
    question_id: int,
    *,
    db_path: Path,
    data_root: Path,
) -> QuestionAnalysisInput:
    try:
        return QuestionAnalysisInputLoader(
            db_path=db_path,
            data_root=data_root,
        ).load((int(question_id),))[0]
    except (KeyError, TypeError, ValueError) as exc:
        raise ApiError(
            404,
            "criterion_question_not_found",
            "Question was not found",
        ) from exc


def _active_criterion_backfill_job(
    manager: JobManager,
    run_id: str,
) -> JobRecord | None:
    jobs, _total = manager.list(
        job_types=("criterion_backfill",),
        statuses=("queued", "running"),
        limit=200,
    )
    return next(
        (
            job
            for job in jobs
            if str(job.payload.get("run_id") or "") == run_id
        ),
        None,
    )


@router.get(
    "/papers",
    response_model=QuestionPaperListResponse,
    responses=QUESTION_SNAPSHOT_ERROR_RESPONSES,
)
def list_papers(
    deleted: bool = False,
    service: QuestionBankReadService = Depends(get_question_bank_read_service),
) -> QuestionPaperListResponse:
    try:
        rows = (
            service.list_papers(deleted=True)
            if deleted
            else service.list_papers()
        )
    except QuestionBankSnapshotError as exc:
        _raise_question_snapshot_api_error(exc)
    items = [QuestionPaperListItem(**row) for row in rows]
    return QuestionPaperListResponse(items=items, total=len(items))


@router.post(
    "/papers/permanent-deletion-impact",
    response_model=QuestionPaperPermanentDeleteImpactResponse,
)
def preview_paper_permanent_deletion(
    body: QuestionPaperPermanentDeleteImpactRequest,
    service: QuestionBankWriteService = Depends(get_question_bank_write_service),
) -> QuestionPaperPermanentDeleteImpactResponse:
    try:
        impact = service.preview_paper_permanent_delete(
            PaperPermanentDeleteSelection(
                id=item.id,
                expected_updated_at=item.expected_updated_at,
            )
            for item in body.selections
        )
    except PaperStateNotFound as exc:
        raise ApiError(
            404,
            "paper_not_found",
            "找不到这份试卷。请刷新题库后重试。",
        ) from exc
    except PaperPermanentDeleteConflict as exc:
        raise ApiError(
            409,
            "paper_permanent_delete_conflict",
            "试卷内容已发生变化。请刷新题库，重新查看影响后再删除。",
        ) from exc
    except PaperPermanentDeleteDependencyConflict as exc:
        raise ApiError(
            409,
            "paper_permanent_delete_dependency_conflict",
            "题库中存在当前版本无法安全处理的关联数据。请先更新应用，再重新删除；本次没有删除任何内容。",
        ) from exc
    except PaperPermanentDeleteStorageIncomplete as exc:
        raise ApiError(
            409,
            "paper_permanent_delete_storage_incomplete",
            "试卷文件未通过安全删除检查。请关闭可能占用文件的 Word 或 PDF 后重试；本次没有删除任何内容。",
        ) from exc
    return QuestionPaperPermanentDeleteImpactResponse(**impact.__dict__)


@router.post(
    "/papers/permanent-delete",
    response_model=QuestionPaperPermanentDeleteResponse,
)
def permanently_delete_papers(
    body: QuestionPaperPermanentDeleteRequest,
    service: QuestionBankWriteService = Depends(get_question_bank_write_service),
) -> QuestionPaperPermanentDeleteResponse:
    try:
        result = service.permanently_delete_papers(
            (
                PaperPermanentDeleteSelection(
                    id=item.id,
                    expected_updated_at=item.expected_updated_at,
                )
                for item in body.selections
            ),
            confirmation_phrase=body.confirmation_phrase,
            request_token=body.request_token,
        )
    except PaperStateNotFound as exc:
        raise ApiError(
            404,
            "paper_not_found",
            "找不到这份试卷。请刷新题库后重试。",
        ) from exc
    except PaperPermanentDeleteConflict as exc:
        raise ApiError(
            409,
            "paper_permanent_delete_conflict",
            "试卷内容已发生变化。请刷新题库，重新查看影响后再删除。",
        ) from exc
    except PaperPermanentDeleteDependencyConflict as exc:
        raise ApiError(
            409,
            "paper_permanent_delete_dependency_conflict",
            "题库中存在当前版本无法安全处理的关联数据。请先更新应用，再重新删除；本次没有删除任何内容。",
        ) from exc
    except PaperPermanentDeleteConfirmationMismatch as exc:
        raise ApiError(
            422,
            "paper_permanent_delete_confirmation_mismatch",
            "确认文字不匹配。请按提示完整输入后再删除。",
        ) from exc
    except PaperPermanentDeleteStorageIncomplete as exc:
        raise ApiError(
            409,
            "paper_permanent_delete_storage_incomplete",
            "试卷文件未通过安全删除检查。请关闭可能占用文件的 Word 或 PDF 后重试；本次没有删除任何内容。",
        ) from exc
    return QuestionPaperPermanentDeleteResponse(
        deleted_paper_ids=list(result.deleted_paper_ids),
        deleted_question_count=result.deleted_question_count,
        deleted_tag_count=result.deleted_tag_count,
        deleted_analysis_record_count=result.deleted_analysis_record_count,
        removed_training_link_count=result.removed_training_link_count,
        removed_knowledge_graph_link_count=result.removed_knowledge_graph_link_count,
        deleted_file_count=result.deleted_file_count,
        skipped_shared_file_count=result.skipped_shared_file_count,
        storage_cleanup_pending=result.storage_cleanup_pending,
    )


@router.patch(
    "/papers/{paper_id}",
    response_model=QuestionPaperMetadataWriteResponse,
    responses=PAPER_METADATA_WRITE_ERROR_RESPONSES,
)
def update_paper_metadata(
    paper_id: int,
    body: QuestionPaperMetadataUpdateRequest,
    service: QuestionBankWriteService = Depends(get_question_bank_write_service),
) -> QuestionPaperMetadataWriteResponse:
    try:
        result = service.update_paper_metadata(
            paper_id,
            expected_updated_at=body.expected_updated_at,
            metadata=PaperMetadataUpdate(
                title=body.metadata.title,
                year=body.metadata.year,
                province=body.metadata.province,
                city=body.metadata.city,
                district=body.metadata.district,
                exam_type=body.metadata.exam_type,
                grade=body.metadata.grade,
                semester=body.metadata.semester,
                folder_name=body.metadata.folder_name,
                textbook_version=body.metadata.textbook_version,
            ),
        )
    except PaperMetadataNotFound as exc:
        raise ApiError(
            404,
            "paper_not_found",
            "Paper not found",
            {"paper_id": int(paper_id)},
        ) from exc
    except PaperMetadataConflict as exc:
        raise ApiError(
            409,
            "paper_metadata_conflict",
            "Paper metadata changed; refresh and retry",
            {
                "paper_id": int(paper_id),
                "current_updated_at": exc.current_updated_at,
            },
        ) from exc
    except ValueError as exc:
        raise ApiError(
            422,
            "paper_metadata_invalid",
            "Paper metadata is invalid",
            {"paper_id": int(paper_id)},
        ) from exc
    return _paper_metadata_write_response(result)


@router.post(
    "/papers/{paper_id}/trash",
    response_model=QuestionPaperStateWriteResponse,
    responses=PAPER_STATE_WRITE_ERROR_RESPONSES,
    include_in_schema=False,
)
def trash_paper(
    paper_id: int,
    body: QuestionPaperStateChangeRequest,
    service: QuestionBankWriteService = Depends(get_question_bank_write_service),
) -> QuestionPaperStateWriteResponse:
    return _change_paper_state(
        paper_id,
        expected_updated_at=body.expected_updated_at,
        deleted=True,
        service=service,
    )


@router.post(
    "/papers/{paper_id}/restore",
    response_model=QuestionPaperStateWriteResponse,
    responses=PAPER_STATE_WRITE_ERROR_RESPONSES,
    include_in_schema=False,
)
def restore_paper(
    paper_id: int,
    body: QuestionPaperStateChangeRequest,
    service: QuestionBankWriteService = Depends(get_question_bank_write_service),
) -> QuestionPaperStateWriteResponse:
    return _change_paper_state(
        paper_id,
        expected_updated_at=body.expected_updated_at,
        deleted=False,
        service=service,
    )


@router.get(
    "/facets",
    response_model=QuestionFacetsResponse,
    responses=QUESTION_SNAPSHOT_ERROR_RESPONSES,
)
def list_question_facets(
    question_number: str | None = None,
    keyword: str | None = None,
    knowledge_point: str | None = None,
    knowledge_points: Annotated[list[str] | None, Query()] = None,
    abilities: Annotated[list[str] | None, Query()] = None,
    methods: Annotated[list[str] | None, Query()] = None,
    thoughts: Annotated[list[str] | None, Query()] = None,
    models: Annotated[list[str] | None, Query()] = None,
    special_types: Annotated[list[str] | None, Query()] = None,
    error_types: Annotated[list[str] | None, Query()] = None,
    student_levels: Annotated[list[str] | None, Query()] = None,
    teaching_stages: Annotated[list[str] | None, Query()] = None,
    sub_skills: Annotated[list[str] | None, Query()] = None,
    difficulty_min: Annotated[float | None, Query(ge=1, le=10)] = None,
    difficulty_max: Annotated[float | None, Query(ge=1, le=10)] = None,
    question_types: Annotated[list[str] | None, Query()] = None,
    paper_ids: Annotated[list[int] | None, Query()] = None,
    years: Annotated[list[str] | None, Query()] = None,
    exam_types: Annotated[list[str] | None, Query()] = None,
    grades: Annotated[list[str] | None, Query()] = None,
    curriculum_volume_ids: Annotated[list[str] | None, Query()] = None,
    exam_scopes: Annotated[list[str] | None, Query()] = None,
    curriculum_sections: Annotated[list[str] | None, Query()] = None,
    tag_status: Literal["all", "tagged", "untagged"] = "all",
    analysis_status: Literal["all", "complete", "incomplete"] = "all",
    teaching_progress_chapter: str | None = None,
    collapse_duplicates: bool = False,
    scope_mode: Literal["any", "primary", "strict"] = "primary",
    service: QuestionBankReadService = Depends(get_question_bank_read_service),
) -> QuestionFacetsResponse:
    _validate_difficulty_range(difficulty_min, difficulty_max)
    try:
        facets = service.list_facets(
            QuestionReadFilters(
                question_number=question_number,
                keyword=keyword,
                knowledge_point=knowledge_point,
                knowledge_points=tuple(knowledge_points or ()),
                abilities=tuple(abilities or ()),
                methods=tuple(methods or ()),
                thoughts=tuple(thoughts or ()),
                models=tuple(models or ()),
                special_types=tuple(special_types or ()),
                error_types=tuple(error_types or ()),
                student_levels=tuple(student_levels or ()),
                teaching_stages=tuple(teaching_stages or ()),
                sub_skills=tuple(sub_skills or ()),
                difficulty_min=difficulty_min,
                difficulty_max=difficulty_max,
                question_types=tuple(question_types or ()),
                paper_ids=tuple(paper_ids or ()),
                years=tuple(years or ()),
                exam_types=tuple(exam_types or ()),
                grades=tuple(grades or ()),
                curriculum_volume_ids=tuple(curriculum_volume_ids or ()),
                exam_scopes=tuple(exam_scopes or ()),
                curriculum_sections=tuple(curriculum_sections or ()),
                tag_status=tag_status,
                analysis_status=analysis_status,
                teaching_progress_chapter=teaching_progress_chapter or "",
                collapse_duplicates=collapse_duplicates,
                scope_mode=scope_mode,
            )
        )
    except QuestionBankSnapshotError as exc:
        _raise_question_snapshot_api_error(exc)
    return QuestionFacetsResponse(**facets)


@router.get(
    "/questions",
    response_model=QuestionListResponse,
    responses=QUESTION_SNAPSHOT_ERROR_RESPONSES,
)
def list_questions(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    question_number: str | None = None,
    keyword: str | None = None,
    knowledge_point: str | None = None,
    knowledge_points: Annotated[list[str] | None, Query()] = None,
    abilities: Annotated[list[str] | None, Query()] = None,
    methods: Annotated[list[str] | None, Query()] = None,
    thoughts: Annotated[list[str] | None, Query()] = None,
    models: Annotated[list[str] | None, Query()] = None,
    special_types: Annotated[list[str] | None, Query()] = None,
    error_types: Annotated[list[str] | None, Query()] = None,
    student_levels: Annotated[list[str] | None, Query()] = None,
    teaching_stages: Annotated[list[str] | None, Query()] = None,
    sub_skills: Annotated[list[str] | None, Query()] = None,
    difficulty_min: Annotated[float | None, Query(ge=1, le=10)] = None,
    difficulty_max: Annotated[float | None, Query(ge=1, le=10)] = None,
    question_types: Annotated[list[str] | None, Query()] = None,
    paper_ids: Annotated[list[int] | None, Query()] = None,
    years: Annotated[list[str] | None, Query()] = None,
    exam_types: Annotated[list[str] | None, Query()] = None,
    grades: Annotated[list[str] | None, Query()] = None,
    curriculum_volume_ids: Annotated[list[str] | None, Query()] = None,
    exam_scopes: Annotated[list[str] | None, Query()] = None,
    curriculum_sections: Annotated[list[str] | None, Query()] = None,
    tag_status: Literal["all", "tagged", "untagged"] = "all",
    analysis_status: Literal["all", "complete", "incomplete"] = "all",
    teaching_progress_chapter: str | None = None,
    sort: Literal[
        "difficulty_desc",
        "difficulty_asc",
        "frequency_desc",
        "frequency_asc",
        # Legacy values remain accepted so old bookmarks and local clients do
        # not break; the product UI no longer presents them.
        "newest",
        "paper_order",
        "difficulty",
        "frequency_midterm",
        "frequency_final",
        "frequency_zhongkao",
        "frequency_contextual",
    ] = "newest",
    compact: bool = False,
    criteria_needs_review: bool = False,
    collapse_duplicates: bool = False,
    scope_mode: Literal["any", "primary", "strict"] = "primary",
    service: QuestionBankReadService = Depends(get_question_bank_read_service),
) -> QuestionListResponse:
    _validate_difficulty_range(difficulty_min, difficulty_max)

    try:
        result = service.list_questions(
            QuestionReadFilters(
                page=page,
                page_size=page_size,
                question_number=question_number,
                keyword=keyword,
                knowledge_point=knowledge_point,
                knowledge_points=tuple(knowledge_points or ()),
                abilities=tuple(abilities or ()),
                methods=tuple(methods or ()),
                thoughts=tuple(thoughts or ()),
                models=tuple(models or ()),
                special_types=tuple(special_types or ()),
                error_types=tuple(error_types or ()),
                student_levels=tuple(student_levels or ()),
                teaching_stages=tuple(teaching_stages or ()),
                sub_skills=tuple(sub_skills or ()),
                difficulty_min=difficulty_min,
                difficulty_max=difficulty_max,
                question_types=tuple(question_types or ()),
                paper_ids=tuple(paper_ids or ()),
                years=tuple(years or ()),
                exam_types=tuple(exam_types or ()),
                grades=tuple(grades or ()),
                curriculum_volume_ids=tuple(curriculum_volume_ids or ()),
                exam_scopes=tuple(exam_scopes or ()),
                curriculum_sections=tuple(curriculum_sections or ()),
                tag_status=tag_status,
                analysis_status=analysis_status,
                sort=sort,
                criteria_needs_review=criteria_needs_review,
                teaching_progress_chapter=teaching_progress_chapter or "",
                collapse_duplicates=collapse_duplicates,
                scope_mode=scope_mode,
            )
        )
    except QuestionBankSnapshotError as exc:
        _raise_question_snapshot_api_error(exc)
    items = result.items
    if compact:
        items = [_compact_question_list_item(item) for item in items]
    return QuestionListResponse(
        items=[QuestionListItem(**item) for item in items],
        total=result.total,
        page=result.page,
        page_size=result.page_size,
        total_pages=result.total_pages,
    )


@router.get(
    "/question-refs",
    response_model=QuestionRefListResponse,
    responses=QUESTION_SNAPSHOT_ERROR_RESPONSES,
)
def list_question_refs(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=500)] = 500,
    paper_ids: Annotated[list[int] | None, Query()] = None,
    analysis_status: Literal["all", "complete", "incomplete"] = "all",
    service: QuestionBankReadService = Depends(get_question_bank_read_service),
) -> QuestionRefListResponse:
    """Slim question identity rows for poll-driven UI refresh cascades."""
    try:
        result = service.list_question_refs(
            QuestionReadFilters(
                page=page,
                page_size=page_size,
                paper_ids=tuple(paper_ids or ()),
                analysis_status=analysis_status,
                sort="paper_order",
            )
        )
    except QuestionBankSnapshotError as exc:
        _raise_question_snapshot_api_error(exc)
    return QuestionRefListResponse(
        items=[QuestionRefListItem(**item) for item in result.items],
        total=result.total,
        page=result.page,
        page_size=result.page_size,
        total_pages=result.total_pages,
    )


def _compact_question_list_item(item: dict[str, Any]) -> dict[str, Any]:
    """Drop answer document blocks from list previews; detail remains lossless."""

    compact_item = dict(item)
    rich_content = dict(compact_item.get("rich_content") or {})
    rich_content["answer_blocks"] = []
    rich_content["answer_block_count"] = 0
    compact_item["rich_content"] = rich_content
    return compact_item


@router.get(
    "/questions/{question_id}/similar",
    response_model=SimilarQuestionListResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Question not found"},
        **QUESTION_SNAPSHOT_ERROR_RESPONSES,
    },
)
def list_similar_questions(
    question_id: int,
    limit: Annotated[int, Query(ge=1, le=20)] = 6,
    service: QuestionBankReadService = Depends(get_question_bank_read_service),
) -> SimilarQuestionListResponse:
    try:
        items = service.find_similar_questions(question_id, limit=limit)
    except QuestionBankSnapshotError as exc:
        _raise_question_snapshot_api_error(exc)
    if items is None:
        raise ApiError(
            404,
            "question_not_found",
            "Question not found",
            {"question_id": int(question_id)},
        )
    return SimilarQuestionListResponse(
        question_id=int(question_id),
        items=[SimilarQuestionItem(**item) for item in items],
    )


@router.get(
    "/questions/{question_id}/solution-evidence",
    response_model=QuestionSolutionEvidenceResponse,
    responses={404: {"model": ErrorResponse}},
)
def get_question_solution_evidence(
    question_id: int,
    service: QuestionBankReadService = Depends(get_question_bank_read_service),
    question_bank_db_path: Path = Depends(get_question_bank_db_path),
    data_root: Path = Depends(get_data_root),
) -> QuestionSolutionEvidenceResponse:
    try:
        question = service.get_question(question_id)
    except QuestionBankSnapshotError as exc:
        _raise_question_snapshot_api_error(exc)
    if question is None:
        raise ApiError(
            404,
            "question_not_found",
            "Question not found",
            {"question_id": int(question_id)},
        )
    try:
        current_question = QuestionAnalysisInputLoader(
            db_path=question_bank_db_path,
            data_root=data_root,
        ).load((question_id,))[0]
        current_source_hash = solution_evidence_source_content_hash(
            current_question
        )
    except (KeyError, OSError, TypeError, ValueError):
        # If the current content cannot be reconstructed safely, an old
        # evidence body must never be presented as current.
        current_source_hash = "current-content-unavailable"
    latest = SolutionEvidenceRepository(question_bank_db_path).latest(
        question_id,
        current_source_content_hash=current_source_hash,
    )
    from question_bank.solution_evidence.part_assessments import load_profiles
    profile = load_profiles(question_bank_db_path, [question_id]).get(question_id)
    if profile and profile["available"]:
        return QuestionSolutionEvidenceResponse(
            question_id=int(question_id), available=True,
            evidence_version_id=profile["evidence_version_id"],
            status=profile["evidence_status"], evidence=profile["evidence"],
            part_assessments=profile["parts"], assessment_revision=profile["revision"],
        )
    if latest is None:
        return QuestionSolutionEvidenceResponse(
            question_id=int(question_id),
            available=False,
        )
    if latest["evidence"] is None:
        return QuestionSolutionEvidenceResponse(
            question_id=int(question_id),
            available=False,
            evidence_version_id=str(latest["evidence_version_id"]),
            status=str(latest["status"]),
        )
    return QuestionSolutionEvidenceResponse(
        question_id=int(question_id),
        available=True,
        evidence_version_id=str(latest["evidence_version_id"]),
        status=str(latest["status"]),
        evidence=dict(latest["evidence"]),
    )


@router.get(
    "/questions/{question_id}",
    response_model=QuestionDetailResponse,
    responses=QUESTION_SNAPSHOT_ERROR_RESPONSES,
)
def get_question(
    question_id: int,
    service: QuestionBankReadService = Depends(get_question_bank_read_service),
) -> QuestionDetailResponse:
    try:
        item = service.get_question(question_id)
    except QuestionBankSnapshotError as exc:
        _raise_question_snapshot_api_error(exc)
    if item is None:
        raise ApiError(
            404,
            "question_not_found",
            "Question not found",
            {"question_id": int(question_id)},
        )
    return QuestionDetailResponse(**item)


@router.patch(
    "/questions/{question_id}/error-patterns/{pattern_id}",
    response_model=QuestionDetailResponse,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
def edit_question_error_pattern(
    question_id: int,
    pattern_id: int,
    body: QuestionErrorPatternEditRequest,
    db_path: Path = Depends(get_question_bank_db_path),
    service: QuestionBankReadService = Depends(get_question_bank_read_service),
) -> QuestionDetailResponse:
    from backend.error_causes import CAUSE_CATEGORIES
    from question_bank.database.schema import connect
    from question_bank.services.error_pattern_service import (
        reject_pattern,
        rename_patterns,
    )

    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT question_id, pattern, status FROM question_error_patterns WHERE id=?",
            (int(pattern_id),),
        ).fetchone()
        if row is None or int(row["question_id"]) != int(question_id):
            raise ApiError(404, "error_pattern_not_found", "Typical error not found")
        if row["status"] not in ("confirmed", "candidate") or (
            str(row["pattern"]) != body.expected_pattern
        ):
            raise ApiError(409, "error_pattern_changed", "Typical error has changed")
        if body.action == "reject":
            changed = reject_pattern(
                db_path, pattern_id=pattern_id, question_id=question_id,
                connection=conn,
            )
        else:
            name = str(body.pattern or "").strip() or str(body.expected_pattern)
            category = str(body.category or "").strip() or None
            if len(name) > 80 or (
                category is not None and category not in CAUSE_CATEGORIES[:-1]
            ):
                raise ApiError(422, "error_pattern_invalid", "Typical error name or category is invalid")
            update_skill = "skill_key" in body.model_fields_set
            skill_key = str(body.skill_key).strip() if body.skill_key else None
            if update_skill and skill_key is not None:
                selectable = {
                    item["key"]
                    for item in service.question_skill_options(question_id)
                }
                if skill_key not in selectable:
                    raise ApiError(
                        422,
                        "error_pattern_skill_invalid",
                        "The linked skill is not one of this question's skills",
                    )
            try:
                changed = rename_patterns(
                    db_path, question_ids=[question_id], pattern_id=pattern_id,
                    old_pattern=body.expected_pattern, new_pattern=name,
                    category=category, connection=conn,
                    skill_key=skill_key, update_skill=update_skill,
                ) > 0
            except ValueError as exc:
                raise ApiError(409, "error_pattern_conflict", str(exc)) from exc
        if not changed:
            raise ApiError(409, "error_pattern_changed", "Typical error has changed")
    return get_question(question_id, service)


@router.get(
    "/questions/{question_id}/assets/{asset_index}",
    response_class=FileResponse,
    responses={
        200: {"content": QUESTION_IMAGE_CONTENT},
        **QUESTION_SNAPSHOT_ERROR_RESPONSES,
    },
)
def get_question_asset(
    question_id: int,
    asset_index: int,
    service: QuestionBankReadService = Depends(get_question_bank_read_service),
) -> FileResponse:
    details = {
        "question_id": int(question_id),
        "asset_index": int(asset_index),
    }
    try:
        resolved = service.resolve_asset(question_id, asset_index)
    except QuestionBankSnapshotError as exc:
        _raise_question_snapshot_api_error(exc)
    except (
        QuestionMediaNotFound,
        ControlledFileExpired,
        ControlledFileForbidden,
        ControlledFileTypeError,
    ) as exc:
        _raise_question_media_api_error(exc, details)
    return FileResponse(
        resolved.path,
        media_type=resolved.media_type,
        headers=NO_STORE_HEADERS,
    )


@router.get(
    "/questions/{question_id}/previews/{preview_type}",
    response_class=FileResponse,
    responses={
        200: {"content": QUESTION_IMAGE_CONTENT},
        **QUESTION_SNAPSHOT_ERROR_RESPONSES,
    },
)
def get_question_preview(
    question_id: int,
    preview_type: Literal["question", "answer"],
    service: QuestionBankReadService = Depends(get_question_bank_read_service),
) -> FileResponse:
    details = {
        "question_id": int(question_id),
        "preview_type": preview_type,
    }
    try:
        resolved = service.resolve_preview(question_id, preview_type)
    except QuestionBankSnapshotError as exc:
        _raise_question_snapshot_api_error(exc)
    except (
        QuestionMediaNotFound,
        ControlledFileExpired,
        ControlledFileForbidden,
        ControlledFileTypeError,
    ) as exc:
        _raise_question_media_api_error(exc, details)
    return FileResponse(
        resolved.path,
        media_type=resolved.media_type,
        headers=NO_STORE_HEADERS,
    )


def _raise_question_snapshot_api_error(
    exc: QuestionBankSnapshotError,
) -> NoReturn:
    if isinstance(exc, QuestionBankSnapshotBusy):
        raise ApiError(
            503,
            "question_bank_snapshot_busy",
            "Question bank snapshot is temporarily busy",
            headers={**NO_STORE_HEADERS, "Retry-After": "1"},
        ) from exc
    raise ApiError(
        503,
        "question_bank_snapshot_unavailable",
        "Question bank snapshot is unavailable",
        headers=NO_STORE_HEADERS,
    ) from exc


def _raise_taxonomy_storage_api_error(exc: Exception) -> NoReturn:
    error_number = getattr(exc, "errno", None)
    if isinstance(exc, PermissionError) or error_number in {
        errno.EACCES,
        errno.EPERM,
        errno.EROFS,
    }:
        raise ApiError(
            503,
            "taxonomy_storage_read_only",
            "Taxonomy storage is not writable",
            {
                "category": "read_only",
                "requires_restart": True,
            },
            headers=NO_STORE_HEADERS,
        ) from exc

    message = str(exc).casefold()
    if isinstance(exc, TimeoutError) or (
        isinstance(exc, TaxonomyStorageError)
        and "timed out waiting for taxonomy lock" in message
    ):
        raise ApiError(
            503,
            "taxonomy_storage_busy",
            "Taxonomy review storage is temporarily busy",
            {"category": "busy"},
            headers={**NO_STORE_HEADERS, "Retry-After": "1"},
        ) from exc

    if isinstance(exc, RuntimeError) or (
        isinstance(exc, TaxonomyStorageError)
        and ("invalid" in message or "ambiguous" in message)
    ):
        raise ApiError(
            503,
            "taxonomy_storage_invalid",
            "Taxonomy review storage requires local repair",
            {"category": "invalid"},
            headers=NO_STORE_HEADERS,
        ) from exc

    raise ApiError(
        503,
        "taxonomy_storage_unavailable",
        "Taxonomy storage is temporarily unavailable",
        {"category": "unavailable"},
        headers=NO_STORE_HEADERS,
    ) from exc


def _validate_difficulty_range(
    difficulty_min: float | None,
    difficulty_max: float | None,
) -> None:
    if (difficulty_min is None) != (difficulty_max is None):
        raise ApiError(
            422,
            "invalid_difficulty_range",
            "Both difficulty_min and difficulty_max are required",
        )
    if (
        difficulty_min is not None
        and difficulty_max is not None
        and difficulty_min > difficulty_max
    ):
        raise ApiError(
            422,
            "invalid_difficulty_range",
            "difficulty_min must not exceed difficulty_max",
        )


def _question_write_response(result: QuestionWriteResult) -> QuestionWriteResponse:
    return QuestionWriteResponse(
        question_id=result.question_id,
        revision=result.revision,
        deleted=result.deleted,
        tags=[
            {
                "tag_type": tag.tag_type,
                "tag_value": tag.tag_value,
                "confidence": tag.confidence,
            }
            for tag in result.tags
        ],
    )


def _paper_metadata_write_response(
    result: PaperMetadataWriteResult,
) -> QuestionPaperMetadataWriteResponse:
    return QuestionPaperMetadataWriteResponse(
        id=result.id,
        title=result.title,
        year=result.year,
        province=result.province,
        city=result.city,
        district=result.district,
        exam_type=result.exam_type,
        grade=result.grade,
        semester=result.semester,
        folder_name=result.folder_name,
        textbook_version=result.textbook_version,
        updated_at=result.updated_at,
    )


def _change_paper_state(
    paper_id: int,
    *,
    expected_updated_at: str,
    deleted: bool,
    service: QuestionBankWriteService,
) -> QuestionPaperStateWriteResponse:
    try:
        result = service.set_paper_deleted(
            paper_id,
            expected_updated_at=expected_updated_at,
            deleted=deleted,
        )
    except PaperStateNotFound as exc:
        raise ApiError(
            404,
            "paper_not_found",
            "Paper not found",
            {"paper_id": int(paper_id)},
        ) from exc
    except PaperStateConflict as exc:
        raise ApiError(
            409,
            "paper_state_conflict",
            "Paper state changed; refresh and retry",
            {
                "paper_id": int(paper_id),
                "current_updated_at": exc.current_updated_at,
                "deleted": exc.deleted,
            },
        ) from exc
    except ValueError as exc:
        raise ApiError(
            422,
            "paper_state_invalid",
            "Paper state request is invalid",
            {"paper_id": int(paper_id)},
        ) from exc
    return _paper_state_write_response(result)


def _paper_state_write_response(
    result: PaperStateWriteResult,
) -> QuestionPaperStateWriteResponse:
    return QuestionPaperStateWriteResponse(
        id=result.id,
        deleted=result.deleted,
        import_status=result.import_status,
        updated_at=result.updated_at,
        affected_question_count=result.affected_question_count,
    )


def _raise_question_write_api_error(
    exc: Exception,
    question_id: int,
) -> NoReturn:
    if isinstance(exc, QuestionWriteConflict):
        raise ApiError(
            409,
            "question_write_conflict",
            "Question changed; refresh and retry",
            {
                "question_id": int(question_id),
                "current_revision": exc.current_revision,
            },
        ) from exc
    raise ApiError(
        404,
        "question_not_found",
        "Question not found",
        {"question_id": int(question_id)},
    ) from exc


def _raise_question_media_api_error(
    exc: Exception,
    details: dict[str, object],
) -> NoReturn:
    if isinstance(exc, QuestionMediaNotFound):
        raise ApiError(
            404,
            "question_media_not_found",
            "Question media resource not found",
            details,
            headers=NO_STORE_HEADERS,
        ) from exc
    if isinstance(exc, ControlledFileExpired):
        raise ApiError(
            410,
            "question_media_expired",
            "Question media resource is no longer available",
            details,
            headers=NO_STORE_HEADERS,
        ) from exc
    if isinstance(exc, ControlledFileForbidden):
        raise ApiError(
            403,
            "question_media_forbidden",
            "Question media resource is outside the allowed storage boundary",
            details,
            headers=NO_STORE_HEADERS,
        ) from exc
    raise ApiError(
        415,
        "question_media_type_not_supported",
        "Question media type is not supported",
        details,
        headers=NO_STORE_HEADERS,
    ) from exc
