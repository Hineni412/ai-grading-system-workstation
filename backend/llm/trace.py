from __future__ import annotations

import hashlib
import json
import math
import re
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from path_manager import get_path_manager

_SAFE_LABEL = re.compile(r"^[A-Za-z0-9._:/-]{1,160}$")
_SAFE_HOST = re.compile(r"^(?:[A-Za-z0-9-]+\.)*[A-Za-z0-9-]+$|^[0-9A-Fa-f:]+$")
_TEXT_FIELDS = frozenset(
    {"content", "input", "instructions", "prompt", "system_prompt", "text"}
)
_SAFE_EXCEPTION_TYPES = frozenset(
    {
        "APIConnectionError",
        "APITimeoutError",
        "AuthenticationError",
        "BadRequestError",
        "ConnectError",
        "ConnectTimeout",
        "ConnectionError",
        "HTTPStatusError",
        "InternalServerError",
        "RateLimitError",
        "ReadError",
        "ReadTimeout",
        "RemoteProtocolError",
        "TimeoutError",
        "TimeoutException",
    }
)
_SPECIFIC_NETWORK_EXCEPTION_TYPES = frozenset(
    {
        "ConnectError",
        "ConnectTimeout",
        "ReadError",
        "ReadTimeout",
        "RemoteProtocolError",
    }
)
_PROVIDER_REQUEST_HEADERS = (
    "x-request-id",
    "request-id",
    "x-amzn-requestid",
)
_TRACE_EVENT_TYPES = frozenset(
    {"request_started", "request_succeeded", "request_failed"}
)
_REQUEST_KINDS = frozenset(
    {"config_generation", "grading", "recognition", "tagging", "workspace"}
)
_PROTOCOLS = frozenset({"chat_completions", "responses"})
_OUTCOMES = frozenset({"", "failure", "success"})
_ERROR_CATEGORIES = frozenset(
    {
        "",
        "authentication",
        "connection",
        "invalid_request",
        "parameter_incompatible",
        "rate_limit",
        "server_transient",
        "timeout",
        "unknown",
    }
)
_NETWORK_PHASES = frozenset(
    {"", "connect", "http", "protocol", "read", "timeout", "unknown"}
)
_FINISH_REASONS = frozenset(
    {
        "",
        "content_filter",
        "function_call",
        "length",
        "max_completion_tokens",
        "max_output_tokens",
        "max_tokens",
        "stop",
        "tool_calls",
        "unknown",
    }
)
_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS: dict[str, threading.Lock] = {}
TRACE_LOG_FILE = Path("logs/llm_api_calls.jsonl")


def trace_log_path() -> Path:
    return get_path_manager().logs_dir / "llm_api_calls.jsonl"


@dataclass(frozen=True, slots=True)
class LLMCallTraceEvent:
    event_type: str
    timestamp_utc: str
    request_id: str
    attempt: int
    request_kind: str
    protocol: str
    model: str
    endpoint_host: str = ""
    timeout_seconds: float = 0.0
    retry_limit: int = 0
    retry_index: int = 0
    pacer_wait_ms: int = 0
    request_bytes_estimate: int = 0
    text_chars: int = 0
    image_count: int = 0
    image_bytes_estimate: int = 0
    elapsed_ms: int = 0
    outcome: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    finish_reason: str = ""
    output_truncated: bool = False
    response_chars: int = 0
    response_bytes_estimate: int = 0
    response_sha256: str = ""
    actual_model: str = ""
    http_status_code: int = 0
    error_category: str = ""
    exception_type: str = ""
    network_phase: str = ""
    provider_request_id: str = ""
    will_retry: bool = False
    retry_delay_ms: int = 0


def utc_timestamp() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def safe_trace_label(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    looks_like_absolute_path = (
        text.startswith(("/", "\\\\"))
        or bool(re.match(r"^[A-Za-z]:[\\/]", text))
        or "://" in text
    )
    if _SAFE_LABEL.fullmatch(text) and not looks_like_absolute_path:
        return text
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return f"redacted-{digest}"


def safe_host_label(value: object) -> str:
    text = str(value or "").strip().lower()
    return (
        text
        if text and len(text) <= 253 and _SAFE_HOST.fullmatch(text)
        else ""
    )


def safe_endpoint_host(base_url: object) -> str:
    try:
        parsed = urlparse(str(base_url or "").strip())
        host = parsed.hostname or ""
    except (TypeError, ValueError):
        return ""
    if parsed.scheme not in {"http", "https"}:
        return ""
    return safe_host_label(host)


def actual_model_label(response: object, fallback_model: object) -> str:
    return safe_trace_label(_member(response, "model") or fallback_model)


def provider_request_id(source: object) -> str:
    direct = _member(source, "_request_id")
    safe_direct = _safe_provider_request_id(direct)
    if safe_direct:
        return safe_direct

    response = _member(source, "response")
    for candidate in (source, response):
        headers = _member(candidate, "headers")
        if headers is None:
            continue
        try:
            items = headers.items()
        except AttributeError:
            continue
        for key, value in items:
            if str(key).strip().lower() not in _PROVIDER_REQUEST_HEADERS:
                continue
            return _safe_provider_request_id(value)
    return ""


def request_diagnostics(kwargs: Mapping[str, object]) -> dict[str, int]:
    totals = {
        "request_bytes_estimate": 2,
        "text_chars": 0,
        "image_count": 0,
        "image_bytes_estimate": 0,
    }
    seen: set[int] = set()

    def visit(value: object, field_name: str = "") -> None:
        if isinstance(value, str):
            encoded_size = len(value.encode("utf-8"))
            totals["request_bytes_estimate"] += encoded_size + 2
            if value.startswith("data:image/") and "," in value:
                totals["image_count"] += 1
                header, payload = value.split(",", 1)
                if ";base64" in header:
                    padding = len(payload) - len(payload.rstrip("="))
                    totals["image_bytes_estimate"] += max(
                        0,
                        (len(payload) * 3) // 4 - padding,
                    )
                return
            if field_name in _TEXT_FIELDS:
                totals["text_chars"] += len(value)
            return
        if isinstance(value, (bytes, bytearray, memoryview)):
            size = len(value)
            totals["request_bytes_estimate"] += size
            totals["image_count"] += 1
            totals["image_bytes_estimate"] += size
            return
        if isinstance(value, Mapping):
            identity = id(value)
            if identity in seen:
                return
            seen.add(identity)
            totals["request_bytes_estimate"] += 2
            for key, item in value.items():
                key_text = str(key)
                totals["request_bytes_estimate"] += len(
                    key_text.encode("utf-8")
                ) + 3
                visit(item, key_text)
            return
        if isinstance(value, (list, tuple)):
            identity = id(value)
            if identity in seen:
                return
            seen.add(identity)
            totals["request_bytes_estimate"] += 2
            for item in value:
                visit(item, field_name)
            return
        if value is None:
            totals["request_bytes_estimate"] += 4
            return
        if isinstance(value, (bool, int, float)):
            totals["request_bytes_estimate"] += len(
                json.dumps(value, allow_nan=False)
            )

    visit(dict(kwargs))
    return totals


def error_diagnostics(error: BaseException) -> dict[str, object]:
    status_code = _status_code(error)
    class_names: list[str] = []
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen and len(class_names) < 4:
        seen.add(id(current))
        class_names.append(type(current).__name__)
        cause = getattr(current, "__cause__", None)
        current = cause if isinstance(cause, BaseException) else None

    exception_type = next(
        (
            name
            for name in reversed(class_names)
            if name in _SPECIFIC_NETWORK_EXCEPTION_TYPES
        ),
        next(
            (name for name in class_names if name in _SAFE_EXCEPTION_TYPES),
            "other",
        ),
    )
    lowered = {name.lower() for name in class_names}
    if any("readtimeout" in name or "readerror" in name for name in lowered):
        network_phase = "read"
    elif any(
        "connecttimeout" in name
        or "connecterror" in name
        or "connectionerror" in name
        or "apiconnectionerror" in name
        for name in lowered
    ):
        network_phase = "connect"
    elif any("protocol" in name for name in lowered):
        network_phase = "protocol"
    elif status_code:
        network_phase = "http"
    elif any("timeout" in name for name in lowered):
        network_phase = "timeout"
    else:
        network_phase = "unknown"

    return {
        "http_status_code": status_code,
        "exception_type": exception_type,
        "network_phase": network_phase,
        "provider_request_id": provider_request_id(error),
    }


def _status_code(error: BaseException) -> int:
    raw_status = getattr(error, "status_code", None)
    if not isinstance(raw_status, int):
        response = getattr(error, "response", None)
        raw_status = getattr(response, "status_code", None)
    if isinstance(raw_status, int) and 100 <= raw_status <= 599:
        return raw_status
    return 0


def _member(source: object, name: str) -> object | None:
    if isinstance(source, Mapping):
        return source.get(name)
    return getattr(source, name, None)


class NullCallTraceSink:
    def write(self, event: LLMCallTraceEvent) -> None:
        return None


class JsonlCallTraceSink:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def write(self, event: LLMCallTraceEvent) -> None:
        record = _trace_record(event)
        line = json.dumps(
            record,
            ensure_ascii=True,
            separators=(",", ":"),
        ) + "\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _path_lock(self.path):
            with self.path.open("a", encoding="utf-8", newline="") as handle:
                handle.write(line)


def _trace_record(event: LLMCallTraceEvent) -> dict[str, object]:
    return {
        "actual_model": safe_trace_label(event.actual_model),
        "attempt": _nonnegative_int(event.attempt),
        "cached_tokens": _nonnegative_int(event.cached_tokens),
        "completion_tokens": _nonnegative_int(event.completion_tokens),
        "elapsed_ms": _nonnegative_int(event.elapsed_ms),
        "endpoint_host": safe_host_label(event.endpoint_host),
        "error_category": _safe_enum(
            event.error_category,
            _ERROR_CATEGORIES,
            "unknown",
        ),
        "event_type": _safe_enum(
            event.event_type,
            _TRACE_EVENT_TYPES,
            "unknown",
        ),
        "exception_type": (
            event.exception_type
            if event.exception_type in _SAFE_EXCEPTION_TYPES | {"", "other"}
            else "other"
        ),
        "finish_reason": _safe_enum(
            event.finish_reason,
            _FINISH_REASONS,
            "unknown",
        ),
        "http_status_code": _http_status(event.http_status_code),
        "image_bytes_estimate": _nonnegative_int(event.image_bytes_estimate),
        "image_count": _nonnegative_int(event.image_count),
        "model": safe_trace_label(event.model),
        "network_phase": _safe_enum(
            event.network_phase,
            _NETWORK_PHASES,
            "unknown",
        ),
        "outcome": _safe_enum(event.outcome, _OUTCOMES, ""),
        "output_truncated": bool(event.output_truncated),
        "pacer_wait_ms": _nonnegative_int(event.pacer_wait_ms),
        "prompt_tokens": _nonnegative_int(event.prompt_tokens),
        "protocol": _safe_enum(event.protocol, _PROTOCOLS, "unknown"),
        "provider_request_id": _safe_provider_request_id(
            event.provider_request_id
        ),
        "reasoning_tokens": _nonnegative_int(event.reasoning_tokens),
        "request_bytes_estimate": _nonnegative_int(
            event.request_bytes_estimate
        ),
        "request_id": safe_trace_label(event.request_id),
        "request_kind": _safe_enum(
            event.request_kind,
            _REQUEST_KINDS,
            "unknown",
        ),
        "response_bytes_estimate": _nonnegative_int(
            event.response_bytes_estimate
        ),
        "response_chars": _nonnegative_int(event.response_chars),
        "response_sha256": (
            event.response_sha256
            if _SHA256.fullmatch(event.response_sha256)
            else ""
        ),
        "retry_delay_ms": _nonnegative_int(event.retry_delay_ms),
        "retry_index": _nonnegative_int(event.retry_index),
        "retry_limit": _nonnegative_int(event.retry_limit),
        "text_chars": _nonnegative_int(event.text_chars),
        "timestamp_utc": (
            event.timestamp_utc
            if _TIMESTAMP.fullmatch(event.timestamp_utc)
            else ""
        ),
        "timeout_seconds": _finite_nonnegative_float(event.timeout_seconds),
        "total_tokens": _nonnegative_int(event.total_tokens),
        "will_retry": bool(event.will_retry),
    }


def _safe_enum(value: object, allowed: frozenset[str], fallback: str) -> str:
    text = str(value or "")
    return text if text in allowed else fallback


def _safe_provider_request_id(value: object) -> str:
    text = str(value or "").strip()
    return text if not text or safe_trace_label(text) == text else ""


def _nonnegative_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, value)


def _http_status(value: object) -> int:
    parsed = _nonnegative_int(value)
    return parsed if 100 <= parsed <= 599 else 0


def _finite_nonnegative_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    parsed = float(value)
    return parsed if math.isfinite(parsed) and parsed >= 0.0 else 0.0


def _path_lock(path: Path) -> threading.Lock:
    key = str(path.absolute())
    with _LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _PATH_LOCKS[key] = lock
        return lock
