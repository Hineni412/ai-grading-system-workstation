from __future__ import annotations

import hashlib
import logging
import os
import re
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Protocol

from backend.llm.diagnostics import NullDiagnosticSink
from backend.llm.errors import classify_llm_error
from backend.llm.gateway import LLMGateway
from backend.llm.policy import LLMProtocol, LLMRequestKind
from backend.llm.trace import NullCallTraceSink, safe_trace_label
from backend.llm.usage import NullUsageSink, usage_fields

from .contracts import WorkspaceContext


LOGGER = logging.getLogger("ai_grading.workspaces.llm")
_SAFE_TOKEN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SAFE_MODULE_ID = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_SAFE_OPERATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")


class WorkspaceModelPolicyError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class WorkspaceModelRequest:
    purpose: str
    data_classification: str
    operation_id: str


@dataclass(frozen=True, slots=True)
class WorkspaceModelAuditEvent:
    module_id: str
    purpose: str
    data_classification: str
    operation_id: str
    model: str
    latency_ms: int
    success: bool
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    error_category: str = ""


class WorkspaceModelAuditSink(Protocol):
    def record(self, event: WorkspaceModelAuditEvent) -> None:
        ...


class LoggingWorkspaceModelAuditSink:
    def record(self, event: WorkspaceModelAuditEvent) -> None:
        level = logging.INFO if event.success else logging.WARNING
        LOGGER.log(
            level,
            (
                "workspace_model_call module=%s purpose=%s classification=%s "
                "operation_id=%s model=%s elapsed_ms=%s success=%s "
                "prompt_tokens=%s completion_tokens=%s cached_tokens=%s "
                "reasoning_tokens=%s total_tokens=%s error_category=%s"
            ),
            event.module_id,
            event.purpose,
            event.data_classification,
            event.operation_id,
            event.model,
            event.latency_ms,
            event.success,
            event.prompt_tokens,
            event.completion_tokens,
            event.cached_tokens,
            event.reasoning_tokens,
            event.total_tokens,
            event.error_category,
        )


class _WorkspaceOperationClaimStore:
    """Durable, metadata-only claims that make one operation fail closed."""

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root
        self.claim_root = workspace_root / ".model-operations"

    @staticmethod
    def _is_reparse_point(path: Path) -> bool:
        try:
            attributes = getattr(path.lstat(), "st_file_attributes", 0)
        except FileNotFoundError:
            return False
        return bool(
            attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        )

    def claim(self, operation_id: str) -> None:
        try:
            self.workspace_root.mkdir(parents=True, exist_ok=True)
            if self._is_reparse_point(self.workspace_root):
                raise WorkspaceModelPolicyError(
                    "workspace model operation path is unsafe"
                )
            self.claim_root.mkdir(exist_ok=True)
            if self._is_reparse_point(self.claim_root):
                raise WorkspaceModelPolicyError(
                    "workspace model operation path is unsafe"
                )
            self.claim_root.resolve(strict=True).relative_to(
                self.workspace_root.resolve(strict=True)
            )
        except WorkspaceModelPolicyError:
            raise
        except (OSError, ValueError) as exc:
            raise WorkspaceModelPolicyError(
                "workspace model operation path is unavailable"
            ) from exc

        digest = hashlib.sha256(operation_id.encode("utf-8")).hexdigest()
        claim_path = self.claim_root / f"{digest}.claim"
        try:
            descriptor = os.open(
                claim_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
        except FileExistsError as exc:
            raise WorkspaceModelPolicyError(
                "operation ID has already used its model request"
            ) from exc
        except OSError as exc:
            raise WorkspaceModelPolicyError(
                "workspace model operation claim failed"
            ) from exc

        try:
            with os.fdopen(descriptor, "wb") as claim_file:
                claim_file.write(b"claimed\n")
                claim_file.flush()
                os.fsync(claim_file.fileno())
        except OSError as exc:
            raise WorkspaceModelPolicyError(
                "workspace model operation claim could not be persisted"
            ) from exc


class WorkspaceModelGateway:
    """Controlled one-request model seam for A/B workspaces."""

    def __init__(
        self,
        *,
        context: WorkspaceContext,
        profile: Mapping[str, object] | None = None,
        config_key: str = "workspace",
        gateway: LLMGateway | None = None,
        audit_sink: WorkspaceModelAuditSink | None = None,
        clock: Callable[[], float] = time.monotonic,
        metadata_only: bool = True,
        claim_operations: bool = True,
        allow_retry: bool = False,
    ) -> None:
        if not isinstance(context, WorkspaceContext):
            raise WorkspaceModelPolicyError(
                "controlled workspace context is required"
            )
        clean_module_id = str(context.module_id or "").strip()
        if not _SAFE_MODULE_ID.fullmatch(clean_module_id):
            raise WorkspaceModelPolicyError("workspace module ID is invalid")
        workspace_dir = getattr(context.paths, "workspace_dir", None)
        if not callable(workspace_dir):
            raise WorkspaceModelPolicyError(
                "controlled workspace path is unavailable"
            )
        controlled_root = Path(
            workspace_dir(clean_module_id, create=False)
        )
        if Path(context.root).resolve(strict=False) != controlled_root.resolve(
            strict=False
        ):
            raise WorkspaceModelPolicyError(
                "workspace context path is not controlled"
            )
        self.module_id = clean_module_id
        self._operation_claims = (
            _WorkspaceOperationClaimStore(controlled_root)
            if claim_operations
            else None
        )
        self.gateway = gateway or LLMGateway(
            profile=profile,
            config_key=config_key,
            **(
                {
                    "diagnostic_sink": NullDiagnosticSink(),
                    "trace_sink": NullCallTraceSink(),
                    "usage_sink": NullUsageSink(),
                }
                if metadata_only
                else {}
            ),
        )
        if metadata_only:
            # A supplied gateway is also forced onto the metadata-only policy.
            self.gateway.diagnostic_sink = NullDiagnosticSink()
            self.gateway.trace_sink = NullCallTraceSink()
            self.gateway.usage_sink = NullUsageSink()
        self.allow_retry = bool(allow_retry)
        self.audit_sink = audit_sink or LoggingWorkspaceModelAuditSink()
        self.clock = clock
        self.physical_request_count = 0

    def chat_completions(
        self,
        *,
        request: WorkspaceModelRequest,
        client: object,
        model: str,
        kwargs: Mapping[str, object],
        timeout_override_seconds: float | None = None,
    ) -> object:
        return self._execute(
            protocol=LLMProtocol.CHAT_COMPLETIONS,
            request=request,
            client=client,
            model=model,
            kwargs=kwargs,
            timeout_override_seconds=timeout_override_seconds,
        )

    def responses(
        self,
        *,
        request: WorkspaceModelRequest,
        client: object,
        model: str,
        kwargs: Mapping[str, object],
        timeout_override_seconds: float | None = None,
    ) -> object:
        return self._execute(
            protocol=LLMProtocol.RESPONSES,
            request=request,
            client=client,
            model=model,
            kwargs=kwargs,
            timeout_override_seconds=timeout_override_seconds,
        )

    def _execute(
        self,
        *,
        protocol: LLMProtocol,
        request: WorkspaceModelRequest,
        client: object,
        model: str,
        kwargs: Mapping[str, object],
        timeout_override_seconds: float | None,
    ) -> object:
        purpose, classification, operation_id = self._validate_request(request)
        safe_model = safe_trace_label(model)
        if not safe_model:
            raise WorkspaceModelPolicyError("model is required")
        if self._operation_claims is not None:
            self._operation_claims.claim(operation_id)

        started = self.clock()
        physical_request_count = 0

        def next_attempt() -> int:
            nonlocal physical_request_count
            physical_request_count += 1
            self.physical_request_count = physical_request_count
            return physical_request_count

        try:
            call = (
                self.gateway.chat_completions
                if protocol is LLMProtocol.CHAT_COMPLETIONS
                else self.gateway.responses
            )
            response = call(
                request_kind=LLMRequestKind.WORKSPACE,
                client=client,
                model=safe_model,
                kwargs=dict(kwargs),
                request_id=operation_id,
                operation_id=operation_id,
                allow_retry=self.allow_retry,
                timeout_override_seconds=timeout_override_seconds,
                _next_attempt=next_attempt,
            )
        except Exception as exc:
            try:
                error_category = classify_llm_error(exc).value
            except Exception:
                error_category = "unknown"
            self.audit_sink.record(
                WorkspaceModelAuditEvent(
                    module_id=self.module_id,
                    purpose=purpose,
                    data_classification=classification,
                    operation_id=operation_id,
                    model=safe_model,
                    latency_ms=self._latency_ms(started),
                    success=False,
                    error_category=error_category,
                )
            )
            raise

        fields = usage_fields(response)
        self.audit_sink.record(
            WorkspaceModelAuditEvent(
                module_id=self.module_id,
                purpose=purpose,
                data_classification=classification,
                operation_id=operation_id,
                model=safe_model,
                latency_ms=self._latency_ms(started),
                success=True,
                **fields,
            )
        )
        return response

    def _latency_ms(self, started: float) -> int:
        return int(round(max(0.0, self.clock() - started) * 1000.0))

    @staticmethod
    def _validate_request(
        request: WorkspaceModelRequest,
    ) -> tuple[str, str, str]:
        if not isinstance(request, WorkspaceModelRequest):
            raise WorkspaceModelPolicyError(
                "workspace model request metadata is required"
            )
        purpose = str(request.purpose or "").strip()
        classification = str(request.data_classification or "").strip()
        operation_id = str(request.operation_id or "").strip()
        if not _SAFE_TOKEN.fullmatch(purpose):
            raise WorkspaceModelPolicyError("model purpose is invalid")
        if not _SAFE_TOKEN.fullmatch(classification):
            raise WorkspaceModelPolicyError("data classification is invalid")
        if not _SAFE_OPERATION_ID.fullmatch(operation_id):
            raise WorkspaceModelPolicyError("operation ID is invalid")
        return purpose, classification, operation_id


__all__ = [
    "LoggingWorkspaceModelAuditSink",
    "WorkspaceModelAuditEvent",
    "WorkspaceModelAuditSink",
    "WorkspaceModelGateway",
    "WorkspaceModelPolicyError",
    "WorkspaceModelRequest",
]
