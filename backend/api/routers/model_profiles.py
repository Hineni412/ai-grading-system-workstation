from __future__ import annotations

from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, Path

from api_profiles import ApiProfileStorageError
from backend.api.app import ApiError, ErrorResponse
from backend.api.dependencies import get_model_profile_service
from backend.api.schemas.model_profiles import (
    ModelExecutionStatusResponse,
    ModelProfileStateResponse,
    ModelProfileUpdateRequest,
)
from backend.model_profiles import (
    ModelProfileInvalid,
    ModelProfileNotFound,
    ModelProfileService,
)


router = APIRouter(prefix="/api/model-profiles", tags=["model-profiles"])
MODEL_PROFILE_ERROR_RESPONSES = {
    404: {"model": ErrorResponse, "description": "Model profile not found"},
    422: {"model": ErrorResponse, "description": "Model profile is invalid"},
    503: {
        "model": ErrorResponse,
        "description": "Model profile storage is unavailable",
    },
}


@router.get(
    "",
    response_model=ModelProfileStateResponse,
    responses={503: MODEL_PROFILE_ERROR_RESPONSES[503]},
)
def list_model_profiles(
    service: ModelProfileService = Depends(get_model_profile_service),
) -> ModelProfileStateResponse:
    try:
        state = service.list_state()
    except (ApiProfileStorageError, OSError, TimeoutError) as exc:
        _raise_profile_storage_error(exc)
    return ModelProfileStateResponse(**state)


@router.get(
    "/{profile_name}/execution-status",
    response_model=ModelExecutionStatusResponse,
    responses=MODEL_PROFILE_ERROR_RESPONSES,
)
def get_model_profile_execution_status(
    profile_name: Annotated[str, Path(min_length=1, max_length=80)],
    service: ModelProfileService = Depends(get_model_profile_service),
) -> ModelExecutionStatusResponse:
    try:
        status = service.execution_status(profile_name)
    except ModelProfileInvalid as exc:
        raise ApiError(
            422,
            "model_profile_invalid",
            str(exc),
        ) from exc
    except ModelProfileNotFound as exc:
        raise ApiError(
            404,
            "model_profile_not_found",
            "Model profile not found",
        ) from exc
    except (ApiProfileStorageError, OSError, TimeoutError) as exc:
        _raise_profile_storage_error(exc)
    return ModelExecutionStatusResponse(**status)


@router.put(
    "/{profile_name}",
    response_model=ModelProfileStateResponse,
    responses={
        422: MODEL_PROFILE_ERROR_RESPONSES[422],
        503: MODEL_PROFILE_ERROR_RESPONSES[503],
    },
)
def upsert_model_profile(
    profile_name: Annotated[str, Path(min_length=1, max_length=80)],
    body: ModelProfileUpdateRequest,
    service: ModelProfileService = Depends(get_model_profile_service),
) -> ModelProfileStateResponse:
    try:
        state = service.upsert(
            profile_name,
            body.model_dump(exclude_unset=True),
        )
    except ModelProfileInvalid as exc:
        raise ApiError(
            422,
            "model_profile_invalid",
            str(exc),
        ) from exc
    except (ApiProfileStorageError, OSError, TimeoutError) as exc:
        _raise_profile_storage_error(exc)
    return ModelProfileStateResponse(**state)


@router.post(
    "/{profile_name}/activate",
    response_model=ModelProfileStateResponse,
    responses=MODEL_PROFILE_ERROR_RESPONSES,
)
def activate_model_profile(
    profile_name: Annotated[str, Path(min_length=1, max_length=80)],
    service: ModelProfileService = Depends(get_model_profile_service),
) -> ModelProfileStateResponse:
    try:
        state = service.activate(profile_name)
    except ModelProfileInvalid as exc:
        raise ApiError(
            422,
            "model_profile_invalid",
            str(exc),
        ) from exc
    except ModelProfileNotFound as exc:
        raise ApiError(
            404,
            "model_profile_not_found",
            "Model profile not found",
        ) from exc
    except (ApiProfileStorageError, OSError, TimeoutError) as exc:
        _raise_profile_storage_error(exc)
    return ModelProfileStateResponse(**state)


def _raise_profile_storage_error(exc: Exception) -> NoReturn:
    raise ApiError(
        503,
        "model_profile_storage_unavailable",
        "Model profile storage is unavailable",
    ) from exc
