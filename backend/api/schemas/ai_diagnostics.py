from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _DiagnosticModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AiDiagnosticAttachment(_DiagnosticModel):
    id: str
    purpose: str
    mime_type: str
    bytes: int = Field(ge=0)
    sha256: str


class AiDiagnosticError(_DiagnosticModel):
    category: str
    exception_type: str
    http_status_code: int = Field(ge=0, le=599)
    message: str


class AiDiagnosticSummary(_DiagnosticModel):
    call_id: str
    operation_id: str
    request_id: str
    attempt: int = Field(ge=0)
    request_kind: str
    workspace_module: str = ""
    workspace_task_kind: str = ""
    protocol: str
    model: str
    started_at_utc: str
    finished_at_utc: str
    outcome: Literal["pending", "success", "failure"]
    elapsed_ms: int = Field(ge=0)
    image_count: int = Field(ge=0)
    response_chars: int = Field(ge=0)
    will_retry: bool


class AiDiagnosticListResponse(_DiagnosticModel):
    items: list[AiDiagnosticSummary]
    returned: int = Field(ge=0)
    matching: int = Field(ge=0)
    scanned_event_count: int = Field(ge=0)
    truncated: bool




class AiDiagnosticDetail(AiDiagnosticSummary):
    endpoint_host: str
    retry_limit: int = Field(ge=0)
    retry_index: int = Field(ge=0)
    retry_delay_ms: int = Field(ge=0)
    request: dict[str, Any]
    attachments: list[AiDiagnosticAttachment]
    raw_response: str
    response_chars: int = Field(ge=0)
    response_sha256: str
    parse_status: str
    parse_operations: list[str]
    parse_error: str
    parsed_result: Any = None
    validation_issue_codes: list[str] = Field(default_factory=list)
    error: AiDiagnosticError | None = None
