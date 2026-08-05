from __future__ import annotations

import threading
from collections.abc import Mapping

from backend.llm.diagnostics import NullDiagnosticSink
from backend.llm.trace import NullCallTraceSink
from backend.llm.usage import NullUsageSink
from backend.workspaces.model_policy import (
    WorkspaceModelGateway,
    WorkspaceModelPolicyError,
    WorkspaceModelRequest,
)


class WorkspaceAITaskModelGateway:
    """The only model-call capability handed to workspace AI Task adapters.

    It applies the shared privacy policy immediately before the physical call,
    so an adapter cannot turn diagnostics or automatic retries back on through
    constructor flags. A task operation can cross this gateway only once.
    """

    def __init__(self) -> None:
        self._claimed_operations: set[str] = set()
        self._lock = threading.Lock()

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
        gateway.gateway.diagnostic_sink = NullDiagnosticSink()
        gateway.gateway.trace_sink = NullCallTraceSink()
        gateway.gateway.usage_sink = NullUsageSink()
        gateway.allow_retry = False


__all__ = ["WorkspaceAITaskModelGateway"]
