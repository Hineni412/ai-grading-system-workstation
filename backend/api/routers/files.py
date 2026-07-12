from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from backend.api.app import ApiError
from backend.api.dependencies import get_job_file_service, get_job_manager
from backend.api.routers.jobs import _require_job
from backend.file_access import (
    ControlledFileExpired,
    ControlledFileForbidden,
    ControlledFileTypeError,
)
from backend.files.service import JobFileNotFound, JobFileService, JobFileUnavailable
from backend.jobs.manager import JobManager


router = APIRouter(prefix="/api", tags=["files"])
NO_STORE_HEADERS = {"Cache-Control": "no-store"}
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MARKDOWN_MEDIA_TYPE = "text/markdown"
ZIP_MEDIA_TYPE = "application/zip"
BINARY_SCHEMA = {"type": "string", "format": "binary"}


@router.get(
    "/jobs/{job_id}/download",
    response_class=FileResponse,
    responses={
        200: {
            "content": {
                XLSX_MEDIA_TYPE: {"schema": BINARY_SCHEMA},
                DOCX_MEDIA_TYPE: {"schema": BINARY_SCHEMA},
                MARKDOWN_MEDIA_TYPE: {"schema": BINARY_SCHEMA},
                ZIP_MEDIA_TYPE: {"schema": BINARY_SCHEMA},
            }
        }
    },
)
def download_job_file(
    job_id: int,
    manager: JobManager = Depends(get_job_manager),
    file_service: JobFileService = Depends(get_job_file_service),
) -> FileResponse:
    try:
        job = _require_job(manager, job_id)
    except ApiError as exc:
        exc.headers.update(NO_STORE_HEADERS)
        raise
    try:
        resolved = file_service.resolve(job)
    except JobFileUnavailable as exc:
        raise ApiError(
            409,
            "job_file_unavailable",
            "Job file is not available",
            {"job_id": int(job_id)},
            headers=NO_STORE_HEADERS,
        ) from exc
    except JobFileNotFound as exc:
        raise ApiError(
            404,
            "job_file_not_found",
            "Job file not found",
            {"job_id": int(job_id)},
            headers=NO_STORE_HEADERS,
        ) from exc
    except ControlledFileExpired as exc:
        raise ApiError(
            410,
            "job_file_expired",
            "Job file is no longer available",
            {"job_id": int(job_id)},
            headers=NO_STORE_HEADERS,
        ) from exc
    except ControlledFileForbidden as exc:
        raise ApiError(
            403,
            "job_file_forbidden",
            "Job file is outside the allowed storage boundary",
            {"job_id": int(job_id)},
            headers=NO_STORE_HEADERS,
        ) from exc
    except ControlledFileTypeError as exc:
        raise ApiError(
            415,
            "job_file_type_not_supported",
            "Job file type is not supported",
            {"job_id": int(job_id)},
            headers=NO_STORE_HEADERS,
        ) from exc
    return FileResponse(
        resolved.path,
        filename=resolved.path.name,
        media_type=resolved.media_type,
        headers={"Cache-Control": "no-store"},
    )
