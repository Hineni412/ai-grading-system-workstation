from __future__ import annotations

from typing import Annotated, Literal, NoReturn

from fastapi import APIRouter, Depends, Query

from backend.api.app import ApiError, ErrorResponse
from backend.api.dependencies import get_authoring_service
from backend.api.schemas.authoring import (
    AuthoringTaskCardCatalogResponse,
    AuthoringTaskCardItem,
    AuthoringVersionCreateRequest,
    AuthoringVersionResponse,
    AuthoringWorkCreateRequest,
    AuthoringWorkDetail,
    AuthoringWorkDeleteResponse,
    AuthoringWorkListResponse,
    AuthoringWorkSummary,
)
from question_bank.authoring import (
    AuthoringNotFound,
    AuthoringService,
    AuthoringSourceNotFound,
    AuthoringStorageUnavailable,
    AuthoringTokenConflict,
    AuthoringValidationError,
    AuthoringVersionConflict,
    task_card_catalog,
)
from question_bank.services.question_read_service import QuestionBankSnapshotError


router = APIRouter(prefix="/api/authoring", tags=["authoring"])

AUTHORING_ERROR_RESPONSES = {
    404: {"model": ErrorResponse, "description": "Authoring work not found"},
    409: {"model": ErrorResponse, "description": "Authoring state conflict"},
    422: {"model": ErrorResponse, "description": "Authoring content is invalid"},
    503: {
        "model": ErrorResponse,
        "description": "Authoring storage is unavailable",
    },
}


def _raise_authoring_error(exc: Exception) -> NoReturn:
    if isinstance(exc, AuthoringVersionConflict):
        raise ApiError(
            409,
            "authoring_version_conflict",
            "This work has newer saved versions; refresh before saving",
            {"current_version": exc.current_version},
        ) from exc
    if isinstance(exc, AuthoringTokenConflict):
        raise ApiError(
            409,
            "authoring_token_conflict",
            "This operation token was already used for a different request",
        ) from exc
    if isinstance(exc, AuthoringSourceNotFound):
        raise ApiError(
            404,
            "authoring_source_not_found",
            "Source question was not found in the question bank",
        ) from exc
    if isinstance(exc, AuthoringNotFound):
        raise ApiError(
            404,
            "authoring_work_not_found",
            "Authoring work was not found",
        ) from exc
    if isinstance(exc, AuthoringStorageUnavailable):
        raise ApiError(
            503,
            "authoring_storage_unavailable",
            "Authoring practice storage is not available yet",
        ) from exc
    if isinstance(exc, QuestionBankSnapshotError):
        raise ApiError(
            503,
            "question_bank_snapshot_unavailable",
            "Question bank snapshot is unavailable",
        ) from exc
    if isinstance(exc, AuthoringValidationError):
        raise ApiError(
            422,
            "authoring_content_invalid",
            "Authoring content is invalid",
        ) from exc
    raise exc


@router.get(
    "/task-cards",
    response_model=AuthoringTaskCardCatalogResponse,
)
def list_authoring_task_cards() -> AuthoringTaskCardCatalogResponse:
    return AuthoringTaskCardCatalogResponse(
        items=[
            AuthoringTaskCardItem(**card) for card in task_card_catalog()
        ]
    )


@router.get(
    "/works",
    response_model=AuthoringWorkListResponse,
    responses={503: AUTHORING_ERROR_RESPONSES[503]},
)
def list_authoring_works(
    kind: Annotated[Literal["decompose", "adapt"] | None, Query()] = None,
    service: AuthoringService = Depends(get_authoring_service),
) -> AuthoringWorkListResponse:
    try:
        items = service.list_works(kind=kind)
    except Exception as exc:
        _raise_authoring_error(exc)
    return AuthoringWorkListResponse(
        items=[AuthoringWorkSummary(**item) for item in items],
        total=len(items),
    )


@router.post(
    "/works",
    response_model=AuthoringWorkDetail,
    responses=AUTHORING_ERROR_RESPONSES,
)
def create_authoring_work(
    body: AuthoringWorkCreateRequest,
    service: AuthoringService = Depends(get_authoring_service),
) -> AuthoringWorkDetail:
    try:
        work = service.create_work(
            kind=body.kind,
            source_question_id=body.source_question_id,
            task_card=body.task_card,
            title=body.title,
            operation_token=body.operation_token.lower(),
        )
    except Exception as exc:
        _raise_authoring_error(exc)
    return AuthoringWorkDetail(**work)


@router.get(
    "/works/{work_id}",
    response_model=AuthoringWorkDetail,
    responses={
        404: AUTHORING_ERROR_RESPONSES[404],
        503: AUTHORING_ERROR_RESPONSES[503],
    },
)
def get_authoring_work(
    work_id: str,
    service: AuthoringService = Depends(get_authoring_service),
) -> AuthoringWorkDetail:
    try:
        work = service.get_work(work_id)
    except Exception as exc:
        _raise_authoring_error(exc)
    return AuthoringWorkDetail(**work)


@router.post(
    "/works/{work_id}/versions",
    response_model=AuthoringVersionResponse,
    responses=AUTHORING_ERROR_RESPONSES,
)
def create_authoring_version(
    work_id: str,
    body: AuthoringVersionCreateRequest,
    service: AuthoringService = Depends(get_authoring_service),
) -> AuthoringVersionResponse:
    try:
        version = service.save_version(
            work_id,
            content=body.content,
            base_version=body.base_version,
            operation_token=body.operation_token.lower(),
        )
    except Exception as exc:
        _raise_authoring_error(exc)
    return AuthoringVersionResponse(**version)


@router.get(
    "/works/{work_id}/versions/{version_no}",
    response_model=AuthoringVersionResponse,
    responses={
        404: AUTHORING_ERROR_RESPONSES[404],
        503: AUTHORING_ERROR_RESPONSES[503],
    },
)
def get_authoring_version(
    work_id: str,
    version_no: int,
    service: AuthoringService = Depends(get_authoring_service),
) -> AuthoringVersionResponse:
    try:
        version = service.get_version(work_id, version_no)
    except Exception as exc:
        _raise_authoring_error(exc)
    return AuthoringVersionResponse(**version)


@router.delete(
    "/works/{work_id}",
    response_model=AuthoringWorkDeleteResponse,
    responses={
        404: AUTHORING_ERROR_RESPONSES[404],
        503: AUTHORING_ERROR_RESPONSES[503],
    },
)
def delete_authoring_work(
    work_id: str,
    service: AuthoringService = Depends(get_authoring_service),
) -> AuthoringWorkDeleteResponse:
    try:
        result = service.delete_work(work_id)
    except Exception as exc:
        _raise_authoring_error(exc)
    return AuthoringWorkDeleteResponse(**result)
