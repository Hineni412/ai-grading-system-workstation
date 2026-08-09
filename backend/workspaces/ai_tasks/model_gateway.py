from __future__ import annotations

import threading
from collections.abc import Mapping

from backend.llm.diagnostics import JsonlDiagnosticJournal
from backend.llm.trace import NullCallTraceSink
from backend.llm.usage import NullUsageSink
from backend.workspaces.model_policy import (
    WorkspaceModelGateway,
    WorkspaceModelPolicyError,
    WorkspaceModelRequest,
)


class WorkspaceAITaskModelGateway:
    """The only model-call capability handed to workspace AI Task adapters.

    It applies the shared privacy policy immediately before the physical call.
    Complete text diagnostics are written to the approved local journal, while
    automatic retries and the secondary trace/usage sinks stay disabled. A task
    operation can cross this gateway only once.
    """

    def __init__(
        self,
        *,
        diagnostic_sink: JsonlDiagnosticJournal | None = None,
    ) -> None:
        self._claimed_operations: set[str] = set()
        self._lock = threading.Lock()
        self._diagnostic_sink = diagnostic_sink or JsonlDiagnosticJournal()

    def chat_completions(
        self,
        *,
        gateway: WorkspaceModelGateway,
        request: WorkspaceModelRequest,
        client: object,
        model: str,
        kwargs: Mapping[str, object],
        timeout_override_seconds: float | None = None,
    ) -> object:
        self._secure_and_claim(gateway, request)
        return gateway.chat_completions(
            request=request,
            client=client,
            model=model,
            kwargs=kwargs,
            timeout_override_seconds=timeout_override_seconds,
        )

    def responses(
        self,
        *,
        gateway: WorkspaceModelGateway,
        request: WorkspaceModelRequest,
        client: object,
        model: str,
        kwargs: Mapping[str, object],
        timeout_override_seconds: float | None = None,
    ) -> object:
        self._secure_and_claim(gateway, request)
        return gateway.responses(
            request=request,
            client=client,
            model=model,
            kwargs=kwargs,
            timeout_override_seconds=timeout_override_seconds,
        )

    def bind(self, gateway: WorkspaceModelGateway) -> "BoundWorkspaceAITaskGateway":
        return BoundWorkspaceAITaskGateway(self, gateway)

    def record_validation(
        self,
        *,
        operation_id: str,
        validation_issue_codes: tuple[str, ...],
        workspace_module: str,
        workspace_task_kind: str,
    ) -> None:
        """Persist only fixed validation metadata; never domain body or identity."""

        self._diagnostic_sink.record_validation(
            operation_id=operation_id,
            validation_issue_codes=validation_issue_codes,
            workspace_module=workspace_module,
            workspace_task_kind=workspace_task_kind,
        )

    def _secure_and_claim(
        self,
        gateway: WorkspaceModelGateway,
        request: WorkspaceModelRequest,
    ) -> None:
        if not isinstance(gateway, WorkspaceModelGateway):
            raise WorkspaceModelPolicyError(
                "workspace AI tasks require the controlled model gateway"
            )
        if not isinstance(request, WorkspaceModelRequest):
            raise WorkspaceModelPolicyError(
                "workspace model request metadata is required"
            )
        _purpose, _classification, operation_id = gateway._validate_request(request)
        with self._lock:
            if operation_id in self._claimed_operations:
                raise WorkspaceModelPolicyError(
                    "workspace AI task already used its model request"
                )
            self._claimed_operations.add(operation_id)

        # Enforce on the actual low-level call path, including supplied gateways.
        # The shared task/job stores remain metadata-only; sensitive request and
        # response bodies live only in the bounded, Git-ignored local journal.
        gateway.gateway.diagnostic_sink = self._diagnostic_sink.for_workspace(
            workspace_module=gateway.module_id,
            workspace_task_kind=request.purpose,
        )
        gateway.gateway.trace_sink = NullCallTraceSink()
        gateway.gateway.usage_sink = NullUsageSink()
        gateway.allow_retry = False


class BoundWorkspaceAITaskGateway:
    """WorkspaceModelGateway-compatible view with Task safety sealed in."""

    def __init__(
        self,
        task_gateway: WorkspaceAITaskModelGateway,
        gateway: WorkspaceModelGateway,
    ) -> None:
        self._task_gateway = task_gateway
        self._gateway = gateway

    def chat_completions(self, **kwargs: object) -> object:
        return self._task_gateway.chat_completions(
            gateway=self._gateway,
            **kwargs,  # type: ignore[arg-type]
        )

    def responses(self, **kwargs: object) -> object:
        return self._task_gateway.responses(
            gateway=self._gateway,
            **kwargs,  # type: ignore[arg-type]
        )


__all__ = ["BoundWorkspaceAITaskGateway", "WorkspaceAITaskModelGateway"]
