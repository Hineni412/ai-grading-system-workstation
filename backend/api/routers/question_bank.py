from __future__ import annotations

from typing import Annotated, Literal, NoReturn

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse

from backend.api.app import ApiError, ErrorResponse
from backend.api.dependencies import (
    get_question_bank_read_service,
    get_question_bank_write_service,
)
from backend.api.schemas.question_bank import (
    QuestionDetailResponse,
    QuestionListItem,
    QuestionListResponse,
    QuestionPaperListItem,
    QuestionPaperListResponse,
    QuestionImportRequestCreate,
    QuestionImportRequestResponse,
    QuestionImportUploadResponse,
    QuestionStateChangeRequest,
    QuestionTagWriteRequest,
    QuestionWriteResponse,
)
from backend.file_access import (
    ControlledFileExpired,
    ControlledFileForbidden,
    ControlledFileTypeError,
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
