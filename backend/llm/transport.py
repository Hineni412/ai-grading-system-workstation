from __future__ import annotations

import hashlib
from typing import Mapping
from urllib.parse import urlparse

import usage_logger
from openai import OpenAI

from .diagnostics import DIAGNOSTIC_LOG_FILE, JsonlDiagnosticJournal
from .gateway import LLMGateway
from .policy import LLMRequestKind
from .usage import JsonlUsageSink
from .trace import (
    JsonlCallTraceSink,
    safe_endpoint_host,
    TRACE_LOG_FILE,
)


_GATEWAY_CONFIG_SALT = "ai-grading-llm-gateway-config-v1"


def normalize_openai_base_url(base_url: str) -> str:
    value = str(base_url or "").strip().rstrip("/")
    if not value:
        return value
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return value
    if parsed.path.strip("/"):
        return value
    return f"{value}/v1"


def gateway_config_key(api_key: str, base_url: str) -> str:
    material = "\0".join(
        (
            _GATEWAY_CONFIG_SALT,
            str(api_key or "").strip(),
            normalize_openai_base_url(base_url),
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def create_openai_client(
    api_key: str,
    base_url: str,
    *,
    client_factory=OpenAI,
) -> object:
    normalized_base_url = normalize_openai_base_url(base_url)
    kwargs: dict[str, object] = {
        "api_key": str(api_key or "").strip(),
        "timeout": 120.0,
        "max_retries": 0,
    }
    if normalized_base_url:
        kwargs["base_url"] = normalized_base_url
    return client_factory(**kwargs)


def _default_usage_sink() -> JsonlUsageSink:
    return JsonlUsageSink(usage_logger.LOG_FILE)


def _default_trace_sink() -> JsonlCallTraceSink:
    return JsonlCallTraceSink(TRACE_LOG_FILE)


def _default_diagnostic_sink() -> JsonlDiagnosticJournal:
    return JsonlDiagnosticJournal(DIAGNOSTIC_LOG_FILE)


class LLMProtocolAdapter:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        policy_profile: Mapping[str, object] | None = None,
        client: object | None = None,
        gateway_factory=LLMGateway,
        usage_sink_factory=_default_usage_sink,
        trace_sink_factory=_default_trace_sink,
        diagnostic_sink_factory=_default_diagnostic_sink,
    ) -> None:
        self.client = (
            client
            if client is not None
            else create_openai_client(api_key, base_url)
        )
        self.gateway = gateway_factory(
            profile=dict(policy_profile or {}),
            config_key=gateway_config_key(api_key, base_url),
            usage_sink=usage_sink_factory(),
            trace_sink=trace_sink_factory(),
            diagnostic_sink=diagnostic_sink_factory(),
            endpoint_host=safe_endpoint_host(base_url),
        )

    def chat_completions(
        self,
        *,
        request_kind: LLMRequestKind,
        model: str,
        kwargs: Mapping[str, object],
        request_id: object | None = None,
        operation_id: object | None = None,
        allow_retry: bool = True,
        timeout_override_seconds: float | None = None,
    ) -> object:
        return self.gateway.chat_completions(
            request_kind=request_kind,
            client=self.client,
            model=model,
            kwargs=dict(kwargs),
            request_id=request_id,
            operation_id=operation_id,
            allow_retry=allow_retry,
            timeout_override_seconds=timeout_override_seconds,
        )

    def responses(
        self,
        *,
        request_kind: LLMRequestKind,
        model: str,
        kwargs: Mapping[str, object],
        request_id: object | None = None,
        operation_id: object | None = None,
        allow_retry: bool = True,
        timeout_override_seconds: float | None = None,
    ) -> object:
        return self.gateway.responses(
            request_kind=request_kind,
            client=self.client,
            model=model,
            kwargs=dict(kwargs),
            request_id=request_id,
            operation_id=operation_id,
            allow_retry=allow_retry,
            timeout_override_seconds=timeout_override_seconds,
        )
