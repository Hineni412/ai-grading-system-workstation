from __future__ import annotations

import logging
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, nullcontext
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from backend.api.frontend import mount_frontend
from backend.performance.metrics import PerformanceSink, request_performance_scope
from backend.workspaces.registry import (
    WorkspaceRegistry,
    load_default_workspace_registry,
)
from path_manager import PathManager, get_path_manager as get_default_path_manager


LOGGER = logging.getLogger("ai_grading.api")
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "ai-grading-api"
    version: str
    preview_instance_id: str | None = None
    preview_head: str | None = None


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
    from backend.api.dependencies import (
        create_job_manager,
        create_ops_write_service,
        get_job_manager,
        get_ops_write_service,
    )
    from backend.schema_migrations import ensure_application_schema

    manager = None
    ops_service = None
    workspace_services: dict[str, object] = {}
    try:
        owns_manager = get_job_manager not in api.dependency_overrides
        owns_ops_service = get_ops_write_service not in api.dependency_overrides
        paths = api.state.path_manager
        registry: WorkspaceRegistry = api.state.workspace_registry
        if hasattr(paths, "db_path") and hasattr(paths, "qb_db_path"):
            ensure_application_schema(paths)
            from question_bank.current_knowledge import (
                ensure_checked_in_current_standard,
            )

            ensure_checked_in_current_standard(Path(paths.qb_db_path))
        registry.run_migrations()
        workspace_services = registry.create_services()
        manager = create_job_manager(paths) if owns_manager else None
        ops_service = (
            create_ops_write_service(paths) if owns_ops_service else None
        )
        if manager is not None:
            from backend.workspaces.ai_tasks.job_adapter import (
                register_workspace_ai_job,
            )
            from backend.workspaces.ai_tasks.service import WorkspaceAITaskService
            from backend.workspaces.ai_tasks.store import WorkspaceAITaskStore

            workspace_ai_task_service = WorkspaceAITaskService(
                store=WorkspaceAITaskStore(manager.store.db_path),
                manager=manager,
            )
            register_workspace_ai_job(manager, workspace_ai_task_service)
            registry.register_jobs(manager, workspace_services)
            registry.register_ai_tasks(
                workspace_ai_task_service,
                workspace_services,
            )
            workspace_ai_task_service.recover_interrupted()
            api.state.job_manager = manager
            api.state.workspace_ai_task_service = workspace_ai_task_service
        if ops_service is not None:
            api.state.ops_write_service = ops_service
        api.state.workspace_services = workspace_services
        yield
    finally:
        if manager is not None:
            manager.shutdown()
            if hasattr(api.state, "job_manager"):
                del api.state.job_manager
        if ops_service is not None:
            if hasattr(api.state, "ops_write_service"):
                del api.state.ops_write_service
        if hasattr(api.state, "workspace_services"):
            del api.state.workspace_services
        if hasattr(api.state, "workspace_ai_task_service"):
            del api.state.workspace_ai_task_service


def create_app(
    *,
    performance_sink: PerformanceSink | None = None,
    path_manager: PathManager | None = None,
    workspace_registry: WorkspaceRegistry | None = None,
) -> FastAPI:
    from backend.api import dependencies

    paths = path_manager or dependencies.get_path_manager()
    app_version = getattr(paths, "version", None)
    if app_version is None:
        app_version = get_default_path_manager().version
    preview_instance_id = os.getenv("AI_GRADING_PREVIEW_INSTANCE_ID") or None
    preview_head = os.getenv("AI_GRADING_PREVIEW_HEAD") or None
    api = FastAPI(
        lifespan=_lifespan,
        title="AI 阅卷系统 API",
        version=app_version,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        responses={
            422: {
                "model": ErrorResponse,
                "description": "Invalid request",
            }
        },
    )
    api.state.path_manager = paths
    registry = workspace_registry or load_default_workspace_registry(paths)
    api.state.workspace_registry = registry
    project_root = getattr(paths, "project_root", None)
    if project_root is None:
        project_root = PROJECT_ROOT
    mount_frontend(api, project_root / "frontend" / "dist")

    @api.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid4().hex
        request.state.request_id = request_id
        started = time.perf_counter()
        status_code = 500
        performance_scope = (
            request_performance_scope(request_id)
            if performance_sink is not None
            else nullcontext(None)
        )
        with performance_scope as recorder:
            try:
                response = await call_next(request)
                status_code = response.status_code
            finally:
                elapsed_ms = (time.perf_counter() - started) * 1000
                LOGGER.info(
                    "api_request method=%s path=%s request_id=%s elapsed_ms=%.1f",
                    request.method,
                    request.url.path,
                    request_id,
                    elapsed_ms,
                )
                if recorder is not None:
                    try:
                        route = request.scope.get("route")
                        route_template = getattr(route, "path", None)
                        if not isinstance(route_template, str):
                            route_template = "<unmatched>"
                        record = recorder.finish(
                            method=request.method,
                            route_template=route_template,
                            status_code=status_code,
                            elapsed_ms=elapsed_ms,
                        )
                        performance_sink.record(record)
                    except Exception:
                        LOGGER.warning("api_performance_record_failed")
        response.headers["x-request-id"] = request_id
        return response

    @api.exception_handler(ApiError)
    async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", "") or uuid4().hex
        if exc.status_code >= 500:
            LOGGER.warning(
                "api_error status=%s code=%s request_id=%s",
                exc.status_code,
                exc.code,
                request_id,
            )
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

    @api.get(
        "/healthz",
        response_model=HealthResponse,
        response_model_exclude_none=True,
    )
    @api.get(
        "/api/healthz",
        response_model=HealthResponse,
        response_model_exclude_none=True,
    )
    def healthz() -> HealthResponse:
        return HealthResponse(
            version=app_version,
            preview_instance_id=preview_instance_id,
            preview_head=preview_head,
        )

    from backend.api.routers import (
        ai_diagnostics_router,
        analytics_router,
        assembly_router,
        config_router,
        files_router,
        grading_router,
        graph_router,
        jobs_router,
        media_router,
        model_profiles_router,
        ops_router,
        question_bank_router,
        reports_router,
        results_center_router,
        review_router,
        scan_router,
        sessions_router,
        students_router,
        templates_router,
        training_router,
        workbench_router,
        workspace_ai_tasks_router,
    )

    api.include_router(ai_diagnostics_router)
    api.include_router(analytics_router)
    api.include_router(assembly_router)
    api.include_router(config_router)
    api.include_router(files_router)
    api.include_router(grading_router)
    api.include_router(graph_router)
    api.include_router(jobs_router)
    api.include_router(media_router)
    api.include_router(model_profiles_router)
    api.include_router(ops_router)
    api.include_router(question_bank_router)
    api.include_router(reports_router)
    api.include_router(results_center_router)
    api.include_router(review_router)
    api.include_router(scan_router)
    api.include_router(sessions_router)
    api.include_router(students_router)
    api.include_router(templates_router)
    api.include_router(training_router)
    api.include_router(workbench_router)
    api.include_router(workspace_ai_tasks_router)
    registry.include_routers(api)

    return api


app = create_app()
