from __future__ import annotations

import logging
import time
from uuid import uuid4

from fastapi import FastAPI, Request
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
    ) -> None:
        super().__init__(message)
        self.status_code = int(status_code)
        self.code = str(code)
        self.message = str(message)
        self.details = dict(details or {})


def create_app() -> FastAPI:
    api = FastAPI(
        title="AI 阅卷系统 API",
        version=get_path_manager().version,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
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
        return JSONResponse(
            status_code=exc.status_code,
            headers={"x-request-id": request_id},
            content=ErrorResponse(
                error=ErrorPayload(
                    code=exc.code,
                    message=exc.message,
                    details=exc.details,
                    request_id=request_id,
                )
            ).model_dump(),
        )

    @api.get("/healthz", response_model=HealthResponse)
    @api.get("/api/healthz", response_model=HealthResponse)
    def healthz() -> HealthResponse:
        return HealthResponse(version=get_path_manager().version)

    return api


app = create_app()
