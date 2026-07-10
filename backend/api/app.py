from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from path_manager import get_path_manager


LOGGER = logging.getLogger("ai_grading.api")


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "ai-grading-api"
    version: str


class ErrorPayload(BaseModel):
    code: str
    message: str
    details: dict = Field(default_factory=dict)
    request_id: str


class ErrorResponse(BaseModel):
    error: ErrorPayload


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = int(status_code)
        self.code = str(code)
        self.message = str(message)
        self.details = dict(details or {})
        self.headers = dict(headers or {})


def _validation_error_details(exc: RequestValidationError) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    for error in exc.errors():
        location = error.get("loc", ())
        if not isinstance(location, (list, tuple)):
            location = (location,)
        errors.append(
            {
                "loc": jsonable_encoder(list(location)),
                "type": str(error.get("type") or "validation_error"),
            }
        )
    return {"errors": errors}


@asynccontextmanager
async def _lifespan(api: FastAPI) -> AsyncIterator[None]:
    from backend.api.dependencies import create_job_manager, get_job_manager

    if get_job_manager in api.dependency_overrides:
        yield
        return

    manager = create_job_manager()
    api.state.job_manager = manager
    try:
        yield
    finally:
        try:
            manager.shutdown()
        finally:
            del api.state.job_manager


def create_app() -> FastAPI:
    api = FastAPI(
        lifespan=_lifespan,
        title="AI 阅卷系统 API",
        version=get_path_manager().version,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        responses={
            422: {
                "model": ErrorResponse,
                "description": "Invalid request",
            }
        },
    )

    @api.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid4().hex
        request.state.request_id = request_id
        started = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000
            LOGGER.info(
                "api_request method=%s path=%s request_id=%s elapsed_ms=%.1f",
                request.method,
                request.url.path,
                request_id,
                elapsed_ms,
            )
        response.headers["x-request-id"] = request_id
        return response

    @api.exception_handler(ApiError)
    async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", "") or uuid4().hex
        headers = dict(exc.headers)
        headers["x-request-id"] = request_id
        return JSONResponse(
            status_code=exc.status_code,
            headers=headers,
            content=ErrorResponse(
                error=ErrorPayload(
                    code=exc.code,
                    message=exc.message,
                    details=exc.details,
                    request_id=request_id,
                )
            ).model_dump(),
        )

    @api.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        request_id = getattr(request.state, "request_id", "") or uuid4().hex
        return JSONResponse(
            status_code=422,
            headers={"x-request-id": request_id},
            content=ErrorResponse(
                error=ErrorPayload(
                    code="validation_error",
                    message="Invalid request",
                    details=_validation_error_details(exc),
                    request_id=request_id,
                )
            ).model_dump(),
        )

    @api.get("/healthz", response_model=HealthResponse)
    @api.get("/api/healthz", response_model=HealthResponse)
    def healthz() -> HealthResponse:
        return HealthResponse(version=get_path_manager().version)

    from backend.api.routers import (
        config_router,
        files_router,
        grading_router,
        jobs_router,
        media_router,
        question_bank_router,
        reports_router,
        review_router,
        scan_router,
        sessions_router,
        students_router,
        templates_router,
    )

    api.include_router(config_router)
    api.include_router(files_router)
    api.include_router(grading_router)
    api.include_router(jobs_router)
    api.include_router(media_router)
    api.include_router(question_bank_router)
    api.include_router(reports_router)
    api.include_router(review_router)
    api.include_router(scan_router)
    api.include_router(sessions_router)
    api.include_router(students_router)
    api.include_router(templates_router)

    return api


app = create_app()
