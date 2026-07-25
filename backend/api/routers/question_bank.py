from __future__ import annotations

from typing import Annotated, Literal, NoReturn

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse

from backend.api.app import ApiError, ErrorResponse
from backend.api.dependencies import (
    get_job_manager,
    get_question_bank_read_service,
    get_question_bank_write_service,
)
from backend.api.routers.jobs import _job_response
from backend.api.schemas.jobs import JobResponse
from backend.api.schemas.question_bank import (
    QuestionDetailResponse,
    QuestionFacetsResponse,
    QuestionListItem,
    QuestionListResponse,
    QuestionPaperListItem,
    QuestionPaperListResponse,
    QuestionImportRequestCreate,
    QuestionImportRequestResponse,
    QuestionImportUploadResponse,
    QuestionJobRetryRequest,
    QuestionStateChangeRequest,
    QuestionTagWriteRequest,
    QuestionTaggingJobRequest,
    QuestionWriteResponse,
    SimilarQuestionItem,
    SimilarQuestionListResponse,
)
from backend.file_access import (
    ControlledFileExpired,
    ControlledFileForbidden,
    ControlledFileTypeError,
)
from backend.jobs.manager import JobManager, UnsupportedJobTypeError
from backend.jobs.store import JobRecord
from question_bank.services.question_read_service import (
    QuestionBankReadService,
    QuestionBankSnapshotBusy,
    QuestionBankSnapshotError,
    QuestionMediaNotFound,
    QuestionReadFilters,
)
from question_bank.services.question_write_service import (
    ConfirmedQuestionTag,
    QuestionBankWriteService,
    QuestionImportTypeNotSupported,
    QuestionImportUploadNotFound,
    QuestionImportStorageForbidden,
    QuestionImportTooLarge,
    QuestionWriteConflict,
    QuestionWriteNotFound,
    QuestionWriteResult,
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


@router.post(
    "/import-requests/{request_id}/jobs",
    response_model=JobResponse,
    status_code=202,
    responses=QUESTION_IMPORT_SUBMIT_RESPONSES,
)
def submit_question_import_job(
    request_id: str,
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
    return _submit_question_bank_job(
        manager,
        "question_import",
        {"request_id": resource.request_id},
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
    return _submit_question_bank_job(
        manager,
        "question_import",
        {"request_id": resource.request_id, "retry_of_job_id": source.id},
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
    payload: dict[str, object] = {"question_ids": question_ids}
    if body.source_job_id is not None:
        payload["source_job_id"] = int(body.source_job_id)
    return _submit_question_bank_job(manager, "tagging_sync", payload)


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
    elif source.status == "succeeded" and bool(source.result.get("retryable")):
        available = _unique_positive_ids(
            source.result.get("failed_question_ids", [])
        )
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
    if source.payload.get("source_job_id") is not None:
        payload["source_job_id"] = int(source.payload["source_job_id"])
    return _submit_question_bank_job(manager, "tagging_sync", payload)


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
    "/papers",
    response_model=QuestionPaperListResponse,
    responses=QUESTION_SNAPSHOT_ERROR_RESPONSES,
)
def list_papers(
    service: QuestionBankReadService = Depends(get_question_bank_read_service),
) -> QuestionPaperListResponse:
    try:
        rows = service.list_papers()
    except QuestionBankSnapshotError as exc:
        _raise_question_snapshot_api_error(exc)
    items = [QuestionPaperListItem(**row) for row in rows]
    return QuestionPaperListResponse(items=items, total=len(items))


@router.get(
    "/facets",
    response_model=QuestionFacetsResponse,
    responses=QUESTION_SNAPSHOT_ERROR_RESPONSES,
)
def list_question_facets(
    question_number: str | None = None,
    keyword: str | None = None,
    knowledge_point: str | None = None,
    difficulty_min: Annotated[int | None, Query(ge=1, le=10)] = None,
    difficulty_max: Annotated[int | None, Query(ge=1, le=10)] = None,
    question_types: Annotated[list[str] | None, Query()] = None,
    paper_ids: Annotated[list[int] | None, Query()] = None,
    years: Annotated[list[str] | None, Query()] = None,
    exam_types: Annotated[list[str] | None, Query()] = None,
    grades: Annotated[list[str] | None, Query()] = None,
    exam_scopes: Annotated[list[str] | None, Query()] = None,
    tag_status: Literal["all", "tagged", "untagged"] = "all",
    service: QuestionBankReadService = Depends(get_question_bank_read_service),
) -> QuestionFacetsResponse:
    _validate_difficulty_range(difficulty_min, difficulty_max)
    try:
        facets = service.list_facets(
            QuestionReadFilters(
                question_number=question_number,
                keyword=keyword,
                knowledge_point=knowledge_point,
                difficulty_min=difficulty_min,
                difficulty_max=difficulty_max,
                question_types=tuple(question_types or ()),
                paper_ids=tuple(paper_ids or ()),
                years=tuple(years or ()),
                exam_types=tuple(exam_types or ()),
                grades=tuple(grades or ()),
                exam_scopes=tuple(exam_scopes or ()),
                tag_status=tag_status,
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
    difficulty_min: Annotated[int | None, Query(ge=1, le=10)] = None,
    difficulty_max: Annotated[int | None, Query(ge=1, le=10)] = None,
    question_types: Annotated[list[str] | None, Query()] = None,
    paper_ids: Annotated[list[int] | None, Query()] = None,
    years: Annotated[list[str] | None, Query()] = None,
    exam_types: Annotated[list[str] | None, Query()] = None,
    grades: Annotated[list[str] | None, Query()] = None,
    exam_scopes: Annotated[list[str] | None, Query()] = None,
    tag_status: Literal["all", "tagged", "untagged"] = "all",
    sort: Literal[
        "newest",
        "difficulty",
        "frequency_midterm",
        "frequency_final",
        "frequency_zhongkao",
        "frequency_contextual",
    ] = "newest",
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
                difficulty_min=difficulty_min,
                difficulty_max=difficulty_max,
                question_types=tuple(question_types or ()),
                paper_ids=tuple(paper_ids or ()),
                years=tuple(years or ()),
                exam_types=tuple(exam_types or ()),
                grades=tuple(grades or ()),
                exam_scopes=tuple(exam_scopes or ()),
                tag_status=tag_status,
                sort=sort,
            )
        )
    except QuestionBankSnapshotError as exc:
        _raise_question_snapshot_api_error(exc)
    return QuestionListResponse(
        items=[QuestionListItem(**item) for item in result.items],
        total=result.total,
        page=result.page,
        page_size=result.page_size,
        total_pages=result.total_pages,
    )


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


def _validate_difficulty_range(
    difficulty_min: int | None,
    difficulty_max: int | None,
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
