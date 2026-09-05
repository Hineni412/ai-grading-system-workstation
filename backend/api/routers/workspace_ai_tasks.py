from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query

from backend.api.app import ApiError
from backend.api.dependencies import get_workspace_ai_task_service
from backend.api.schemas.workspace_ai_tasks import (
    DispatchWorkspaceAITaskRequest,
    OpaqueRefPayload,
    PrepareWorkspaceAITaskRequest,
    UpdateWorkspaceAIHandoffRequest,
    WorkspaceAIHandoffResponse,
    WorkspaceAITaskResponse,
)
from backend.workspaces.ai_tasks.models import (
    OpaqueRef,
    OperationConflictError,
    PrepareRequest,
    TaskNotFoundError,
    WorkspaceAITaskError,
)
from backend.workspaces.ai_tasks.service import WorkspaceAITaskService


router = APIRouter(prefix="/api/workspace-ai-tasks", tags=["workspace-ai-tasks"])


def _response(snapshot: object) -> WorkspaceAITaskResponse:
    return WorkspaceAITaskResponse.model_validate(snapshot)


def _ref(value: OpaqueRefPayload) -> OpaqueRef:
    return OpaqueRef(kind=value.kind, id=value.id, revision=value.revision)


def _raise_public(error: Exception) -> None:
    if isinstance(error, TaskNotFoundError):
        raise ApiError(404, error.code, "Workspace AI task not found", {}) from error
    if isinstance(error, OperationConflictError):
        raise ApiError(
            409,
            error.code,
            "Operation ID conflicts with the prepared request",
            {},
        ) from error
    if isinstance(error, (WorkspaceAITaskError, ValueError)):
        raise ApiError(
            422,
            getattr(error, "code", "invalid_workspace_ai_task"),
            "Workspace AI task request is invalid",
            {},
        ) from error
    raise error


@router.post("/prepare", response_model=WorkspaceAITaskResponse, status_code=201)
def prepare_task(
    request: PrepareWorkspaceAITaskRequest,
    service: WorkspaceAITaskService = Depends(get_workspace_ai_task_service),
) -> WorkspaceAITaskResponse:
    try:
        return _response(
            service.prepare(
                request.operation_id,
                PrepareRequest(
                    module=request.module,
                    task_kind=request.task_kind,
                    source_ref=_ref(request.source_ref),
                    context_refs=tuple(_ref(item) for item in request.context_refs),
                    prompt_contract_version=request.prompt_contract_version,
                    model_destination_fingerprint=request.model_destination_fingerprint,
                    return_target=request.return_target,
                ),
            )
        )
    except Exception as error:
        _raise_public(error)
        raise AssertionError("unreachable")


@router.post(
    "/operations/{operation_id}/dispatch",
    response_model=WorkspaceAITaskResponse,
    status_code=202,
)
def dispatch_task(
    operation_id: str,
    request: DispatchWorkspaceAITaskRequest,
    service: WorkspaceAITaskService = Depends(get_workspace_ai_task_service),
) -> WorkspaceAITaskResponse:
    try:
        return _response(
            service.dispatch(
                operation_id,
                prepared_task_id=request.prepared_task_id,
                request_fingerprint=request.request_fingerprint,
            )
        )
    except Exception as error:
        _raise_public(error)
        raise AssertionError("unreachable")


@router.get("", response_model=list[WorkspaceAITaskResponse])
def list_tasks(
    module: Literal["class_teacher"] = Query(),
    service: WorkspaceAITaskService = Depends(get_workspace_ai_task_service),
) -> list[WorkspaceAITaskResponse]:
    try:
        return [_response(task) for task in service.list_module_tasks(module)]
    except Exception as error:
        _raise_public(error)
        raise AssertionError("unreachable")


@router.get("/{task_id}", response_model=WorkspaceAITaskResponse)
def get_task(
    task_id: str,
    service: WorkspaceAITaskService = Depends(get_workspace_ai_task_service),
) -> WorkspaceAITaskResponse:
    try:
        return _response(service.get(task_id=task_id))
    except Exception as error:
        _raise_public(error)
        raise AssertionError("unreachable")


@router.get("/operations/{operation_id}/current", response_model=WorkspaceAITaskResponse)
def get_task_by_operation(
    operation_id: str,
    service: WorkspaceAITaskService = Depends(get_workspace_ai_task_service),
) -> WorkspaceAITaskResponse:
    try:
        return _response(service.get(operation_id=operation_id))
    except Exception as error:
        _raise_public(error)
        raise AssertionError("unreachable")


@router.post("/operations/{operation_id}/cancel", response_model=WorkspaceAITaskResponse)
def cancel_task(
    operation_id: str,
    service: WorkspaceAITaskService = Depends(get_workspace_ai_task_service),
) -> WorkspaceAITaskResponse:
    try:
        return _response(service.cancel(operation_id))
    except Exception as error:
        _raise_public(error)
        raise AssertionError("unreachable")


@router.post("/operations/{operation_id}/discard", response_model=WorkspaceAITaskResponse)
def discard_result_unknown_task(
    operation_id: str,
    service: WorkspaceAITaskService = Depends(get_workspace_ai_task_service),
) -> WorkspaceAITaskResponse:
    try:
        return _response(service.discard_result_unknown(operation_id))
    except Exception as error:
        _raise_public(error)
        raise AssertionError("unreachable")


@router.post(
    "/handoffs/{handoff_id}/state",
    response_model=WorkspaceAIHandoffResponse,
)
def update_handoff_state(
    handoff_id: str,
    request: UpdateWorkspaceAIHandoffRequest,
    service: WorkspaceAITaskService = Depends(get_workspace_ai_task_service),
) -> WorkspaceAIHandoffResponse:
    try:
        return WorkspaceAIHandoffResponse.model_validate(
            service.mark_handoff(handoff_id, request.state)
        )
    except Exception as error:
        _raise_public(error)
        raise AssertionError("unreachable")
