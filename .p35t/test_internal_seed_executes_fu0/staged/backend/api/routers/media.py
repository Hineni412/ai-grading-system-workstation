from __future__ import annotations

from typing import Literal, NoReturn

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import FileResponse

from backend.api.app import ApiError
from backend.api.dependencies import (
    get_media_service,
    get_scan_grading_workspace,
)
from backend.file_access import (
    ControlledFileExpired,
    ControlledFileForbidden,
    ControlledFileTypeError,
)
from backend.media.service import (
    ReviewMediaNotFound,
    ReviewMediaService,
    ReviewMediaUnreadable,
)
from backend.scan_grading.workspace import (
    ScanGradingWorkspace,
    ScanGradingWorkspaceError,
)


router = APIRouter(prefix="/api", tags=["media"])
NO_STORE_HEADERS = {"Cache-Control": "no-store"}
BINARY_SCHEMA = {"type": "string", "format": "binary"}
PAGE_IMAGE_CONTENT = {
    media_type: {"schema": BINARY_SCHEMA}
    for media_type in ("image/jpeg", "image/png", "image/webp", "image/bmp")
}


@router.get(
    "/sessions/{session_id}/results/{result_id}/pages/{page}",
    response_class=FileResponse,
    responses={200: {"content": PAGE_IMAGE_CONTENT}},
)
def get_result_page(
    session_id: int,
    result_id: int,
    page: Literal["front", "back"],
    variant: Literal["original", "annotated"] = "original",
    media_service: ReviewMediaService = Depends(get_media_service),
) -> FileResponse:
    details = {
        "session_id": int(session_id),
        "result_id": int(result_id),
        "page": page,
        "variant": variant,
    }
    try:
        resolved = media_service.resolve_result_page(
            session_id,
            result_id,
            page,
            variant,
        )
    except (
        ReviewMediaNotFound,
        ControlledFileExpired,
        ControlledFileForbidden,
        ControlledFileTypeError,
        ReviewMediaUnreadable,
    ) as exc:
        _raise_media_api_error(exc, details)
    return FileResponse(
        resolved.path,
        media_type=resolved.media_type,
        headers={"Cache-Control": "no-store"},
    )


@router.get(
    "/sessions/{session_id}/results/{result_id}/details/{detail_id}/crop",
    response_class=Response,
    responses={
        200: {
            "content": {
                "image/jpeg": {"schema": BINARY_SCHEMA},
            }
        }
    },
)
def get_review_detail_crop(
    session_id: int,
    result_id: int,
    detail_id: int,
    media_service: ReviewMediaService = Depends(get_media_service),
) -> Response:
    details = {
        "session_id": int(session_id),
        "result_id": int(result_id),
        "detail_id": int(detail_id),
    }
    try:
        payload = media_service.render_detail_crop(
            session_id,
            result_id,
            detail_id,
        )
    except (
        ReviewMediaNotFound,
        ControlledFileExpired,
        ControlledFileForbidden,
        ControlledFileTypeError,
        ReviewMediaUnreadable,
    ) as exc:
        _raise_media_api_error(exc, details)
    return Response(
        content=payload,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store"},
    )


@router.get(
    (
        "/sessions/{session_id}/review/preflight/"
        "{target_type}/{target_id}/{question_id}/crop"
    ),
    response_class=Response,
    responses={
        200: {
            "content": {
                "image/jpeg": {"schema": BINARY_SCHEMA},
            }
        }
    },
)
def get_preflight_review_crop(
    session_id: int,
    target_type: Literal["group", "issue"],
    target_id: str,
    question_id: str,
    source_region_id: int | None = Query(default=None, gt=0),
    media_service: ReviewMediaService = Depends(get_media_service),
    workspace: ScanGradingWorkspace = Depends(get_scan_grading_workspace),
) -> Response:
    details = {
        "session_id": int(session_id),
        "target_type": target_type,
        "target_id": target_id,
        "question_id": question_id,
        "source_region_id": source_region_id,
    }
    try:
        front_source = workspace.resolve_preflight_media(
            session_id,
            f"{target_type}:{target_id}:front",
        )
        try:
            back_source = workspace.resolve_preflight_media(
                session_id,
                f"{target_type}:{target_id}:back",
            )
        except ScanGradingWorkspaceError:
            back_source = None
        payload = media_service.render_preflight_crop(
            session_id,
            question_id,
            source_region_id=source_region_id,
            front_source=front_source,
            back_source=back_source,
        )
    except ScanGradingWorkspaceError as exc:
        _raise_media_api_error(
            ReviewMediaNotFound(str(exc)),
            details,
        )
    except (
        ReviewMediaNotFound,
        ControlledFileExpired,
        ControlledFileForbidden,
        ControlledFileTypeError,
        ReviewMediaUnreadable,
    ) as exc:
        _raise_media_api_error(exc, details)
    return Response(
        content=payload,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store"},
    )


def _raise_media_api_error(
    exc: Exception,
    details: dict[str, object],
) -> NoReturn:
    if isinstance(exc, ReviewMediaNotFound):
        raise ApiError(
            404,
            "media_not_found",
            "Media resource not found",
            details,
            headers=NO_STORE_HEADERS,
        ) from exc
    if isinstance(exc, ControlledFileExpired):
        raise ApiError(
            410,
            "media_expired",
            "Media resource is no longer available",
            details,
            headers=NO_STORE_HEADERS,
        ) from exc
    if isinstance(exc, ControlledFileForbidden):
        raise ApiError(
            403,
            "media_path_forbidden",
            "Media resource is outside the allowed storage boundary",
            details,
            headers=NO_STORE_HEADERS,
        ) from exc
    if isinstance(exc, ControlledFileTypeError):
        raise ApiError(
            415,
            "media_type_not_supported",
            "Media type is not supported",
            details,
            headers=NO_STORE_HEADERS,
        ) from exc
    raise ApiError(
        422,
        "media_unreadable",
        "Media resource could not be decoded",
        details,
        headers=NO_STORE_HEADERS,
    ) from exc
