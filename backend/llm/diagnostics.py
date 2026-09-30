"""Local-only, content-bearing LLM diagnostics.

The current JSONL file rolls at roughly 32 MiB. Three older files are kept;
the oldest rotated file is deleted as part of this bounded retention policy.
Image bodies, credentials and local paths are never written.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import threading
from collections import deque
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .json_repair import parse_json_object_locally
from .trace import safe_host_label, safe_trace_label, utc_timestamp
from .usage import response_diagnostics

DIAGNOSTIC_LOG_FILE = Path("logs/llm_diagnostics.jsonl")
DIAGNOSTIC_SCHEMA_VERSION = 1
DIAGNOSTIC_MAX_FILE_BYTES = 32 * 1024 * 1024
DIAGNOSTIC_ROTATED_FILE_COUNT = 3
DIAGNOSTIC_MAX_EVENT_BYTES = 1024 * 1024
_MAX_READ_EVENTS = 10_000
_HEAVY_EVENT_KEYS = ("request", "raw_response", "parsed_result")
_SECRET_KEYS = frozenset(
    {
        "apikey",
        "authorization",
        "accesstoken",
        "configapikey",
        "cookie",
        "idtoken",
        "openaiapikey",
        "password",
        "passwd",
        "proxyauthorization",
        "refreshtoken",
        "sessiontoken",
        "setcookie",
        "xapikey",
    }
)
_DATA_URL = re.compile(
    r"^data:(?P<mime>[-\w.+/]+)?(?P<base64>;base64)?,(?P<data>.*)$",
    re.DOTALL,
)
_WINDOWS_PATH = re.compile(
    r"(?<![\w])(?:[A-Za-z]:[\\/]|\\\\)[^\r\n\t<>|\"']+"
)
_FILE_URL = re.compile(r"(?i)\bfile://[^\s<>\"']+")
_HTTP_URL = re.compile(r"(?i)\bhttps?://[^\s<>\"']+")
_BEARER_SECRET = re.compile(
    r"(?i)\b(authorization\s*[:=]\s*)(?:bearer\s+)?[^\s,;]+"
)
_COOKIE_HEADER_SECRET = re.compile(
    r"(?im)\b((?:set-)?cookie\s*[:=]\s*)[^\r\n]*"
)
_JSON_SECRET_VALUE = re.compile(
    r'(?i)("(?:api[_ -]?key|config[_ -]?api[_ -]?key|authorization|cookie|'
    r'set-cookie|password|passwd|access[_ -]?token|refresh[_ -]?token|'
    r'id[_ -]?token|session[_ -]?token|proxy-authorization|x-api-key)"'
    r'\s*:\s*)"(?:\\.|[^"\\])*"'
)
_NAMED_SECRET = re.compile(
    r"(?i)\b(api[_ -]?key|x-api-key|proxy-authorization|cookie|set-cookie|"
    r"password|passwd|access[_ -]?token|refresh[_ -]?token|id[_ -]?token|"
    r"session[_ -]?token)"
    r"(\s*[:=]\s*)[^\s,;]+"
)
_OPENAI_SECRET = re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b")
_LONG_BASE64 = re.compile(
    r"(?<![A-Za-z0-9+/_-])[A-Za-z0-9+/_-]{128,}={0,2}"
    r"(?![A-Za-z0-9+/_=-])"
)
_WORKSPACE_LABEL = re.compile(r"[a-z][a-z0-9_]{0,79}")
_VALIDATION_ISSUE_CODES = frozenset(
    {"student_revision_mismatch", "unknown_student_reference"}
)
_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS: dict[str, threading.RLock] = {}


def _path_lock(path: Path) -> threading.RLock:
    key = str(path.absolute())
    with _LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[key] = lock
        return lock


def _compact_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _is_secret_key(value: object) -> bool:
    compact = _compact_key(value)
    return compact in _SECRET_KEYS or compact.endswith("apikey")


def _sanitize_text(value: object) -> str:
    text = str(value or "")
    text = _JSON_SECRET_VALUE.sub(r'\1"[REDACTED]"', text)
    text = _BEARER_SECRET.sub(r"\1[REDACTED]", text)
    text = _COOKIE_HEADER_SECRET.sub(r"\1[REDACTED]", text)
    text = _NAMED_SECRET.sub(r"\1\2[REDACTED]", text)
    text = _OPENAI_SECRET.sub("[REDACTED]", text)
    text = _FILE_URL.sub("[LOCAL_PATH_REDACTED]", text)
    text = _HTTP_URL.sub("[URL_REDACTED]", text)
    text = _WINDOWS_PATH.sub("[LOCAL_PATH_REDACTED]", text)

    def redact_base64(match: re.Match[str]) -> str:
        candidate = match.group(0)
        try:
            payload = base64.b64decode(
                candidate,
                altchars=b"-_",
                validate=True,
            )
        except (ValueError, TypeError):
            return "[BASE64_REDACTED]"
        return (
            f"[BASE64_REDACTED bytes={len(payload)} "
            f"sha256={hashlib.sha256(payload).hexdigest()}]"
        )

    return _LONG_BASE64.sub(redact_base64, text)


def _json_safe(value: object, *, depth: int = 0) -> Any:
    if depth > 24:
        return "[DEPTH_LIMIT]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _sanitize_text(value)
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for raw_key, child in value.items():
            key = _sanitize_text(raw_key)
            normalized[key] = (
                "[REDACTED]"
                if _is_secret_key(raw_key)
                else _json_safe(child, depth=depth + 1)
            )
        return normalized
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item, depth=depth + 1) for item in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _json_safe(model_dump(), depth=depth + 1)
        except Exception:
            return _sanitize_text(type(value).__name__)
    return _sanitize_text(value)


def _decode_attachment(value: str) -> tuple[str, bytes] | None:
    match = _DATA_URL.fullmatch(value)
    if match is None:
        return None
    mime = str(match.group("mime") or "application/octet-stream").lower()
    raw_data = match.group("data")
    try:
        if match.group("base64"):
            payload = base64.b64decode(raw_data, validate=False)
        else:
            payload = raw_data.encode("utf-8")
    except (ValueError, TypeError):
        payload = b""
    return mime, payload


def _decode_long_base64(value: str) -> bytes | None:
    candidate = str(value or "").strip()
    if _LONG_BASE64.fullmatch(candidate) is None:
        return None
    try:
        return base64.b64decode(candidate, altchars=b"-_", validate=True)
    except (ValueError, TypeError):
        return b""


def sanitize_request_payload(
    value: object,
) -> tuple[Any, list[dict[str, object]]]:
    attachments: list[dict[str, object]] = []

    def add_attachment(
        payload: bytes,
        *,
        mime_type: str,
        purpose: str,
    ) -> dict[str, str]:
        attachment_id = f"a{len(attachments) + 1}"
        attachments.append(
            {
                "id": attachment_id,
                "purpose": _sanitize_text(purpose)[:80],
                "mime_type": _sanitize_text(mime_type)[:120],
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
        return {"attachment_ref": attachment_id}

    def walk(
        child: object,
        *,
        depth: int = 0,
        purpose: str = "input_image",
        attachment_context: bool = False,
    ) -> Any:
        if depth > 24:
            return "[DEPTH_LIMIT]"
        if isinstance(child, str):
            decoded = _decode_attachment(child)
            long_base64 = _decode_long_base64(child)
            if decoded is None and long_base64 is None and not attachment_context:
                return _sanitize_text(child)
            mime, payload = (
                decoded
                if decoded is not None
                else (
                    ("application/octet-stream", long_base64)
                    if long_base64 is not None
                    else ("application/x-external-attachment", b"")
                )
            )
            return add_attachment(
                payload,
                mime_type=mime,
                purpose=purpose,
            )
        if isinstance(child, (bytes, bytearray, memoryview)):
            payload = bytes(child)
            return add_attachment(
                payload,
                mime_type="application/octet-stream",
                purpose=purpose,
            )
        if child is None or isinstance(child, (bool, int, float)):
            return child
        if isinstance(child, Mapping):
            item_type = _sanitize_text(child.get("type") or purpose)[:80]
            container_is_attachment = _compact_key(item_type) in {
                "attachment",
                "audio",
                "b64json",
                "file",
                "image",
                "imageurl",
                "inputaudio",
                "inputfile",
                "inputimage",
            }
            normalized: dict[str, Any] = {}
            for raw_key, nested in child.items():
                key = _sanitize_text(raw_key)
                if _is_secret_key(raw_key):
                    normalized[key] = "[REDACTED]"
                    continue
                compact_key = _compact_key(raw_key)
                nested_is_attachment = (
                    compact_key in {
                        "attachment",
                        "audio",
                        "b64json",
                        "file",
                        "filedata",
                        "image",
                        "imageurl",
                        "inputaudio",
                        "inputfile",
                        "inputimage",
                    }
                    or (
                        container_is_attachment
                        and compact_key in {"content", "data", "filedata", "url"}
                    )
                )
                nested_purpose = (
                    item_type
                    if nested_is_attachment and container_is_attachment
                    else key
                )
                normalized[key] = walk(
                    nested,
                    depth=depth + 1,
                    purpose=nested_purpose,
                    attachment_context=nested_is_attachment,
                )
            return normalized
        if isinstance(child, (list, tuple, set, frozenset)):
            return [
                walk(
                    item,
                    depth=depth + 1,
                    purpose=purpose,
                    attachment_context=attachment_context,
                )
                for item in child
            ]
        model_dump = getattr(child, "model_dump", None)
        if callable(model_dump):
            try:
                return walk(
                    model_dump(),
                    depth=depth + 1,
                    purpose=purpose,
                    attachment_context=attachment_context,
                )
            except Exception:
                return _sanitize_text(type(child).__name__)
        return _sanitize_text(child)

    return walk(value), attachments


def _member(source: object, name: str) -> object | None:
    if isinstance(source, Mapping):
        return source.get(name)
    return getattr(source, name, None)


def _content_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, (list, tuple)):
        return ""
    parts: list[str] = []
    for item in value:
        text = _member(item, "text")
        if isinstance(text, str):
            parts.append(text)
    return "\n".join(parts)


def response_text(response: object) -> str:
    choices = _member(response, "choices") or []
    if isinstance(choices, (list, tuple)) and choices:
        message = _member(choices[0], "message")
        text = _content_text(_member(message, "content"))
        if text:
            return text
    output_text = _member(response, "output_text")
    if isinstance(output_text, str):
        return output_text
    output = _member(response, "output") or []
    if isinstance(output, (list, tuple)):
        parts: list[str] = []
        for item in output:
            text = _content_text(_member(item, "content"))
            if text:
                parts.append(text)
        if parts:
            return "\n".join(parts)
    return ""


def _parse_response_text(
    raw_text: str,
    *,
    output_truncated: bool = False,
) -> tuple[str, Any, list[str], str]:
    if not raw_text:
        return "empty", None, [], ""
    if output_truncated:
        return (
            "truncated_json",
            None,
            [],
            "模型输出达到长度上限，JSON 没有完整结束。",
        )
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        try:
            repaired = parse_json_object_locally(raw_text)
        except (TypeError, ValueError):
            return "not_json", None, [], "Model response is not valid JSON"
        return (
            "locally_repaired" if repaired.report.repaired else "parsed",
            _json_safe(repaired.payload),
            list(repaired.report.operations),
            "",
        )
    return "parsed", _json_safe(parsed), [], ""


def _call_id(request_id: str, attempt: int) -> str:
    material = f"{request_id}\0{max(0, int(attempt))}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:24]


def _safe_id(value: object) -> str:
    return safe_trace_label(value)


def _safe_workspace_label(value: object) -> str:
    candidate = str(value or "").strip().lower().replace("-", "_")
    return candidate if _WORKSPACE_LABEL.fullmatch(candidate) else ""


def _base_event(
    *,
    event: str,
    operation_id: object,
    request_id: object,
    attempt: int,
    request_kind: object,
    protocol: object,
    model: object,
    endpoint_host: object,
    workspace_module: object = "",
    workspace_task_kind: object = "",
) -> dict[str, Any]:
    safe_request_id = _safe_id(request_id)
    safe_operation_id = _safe_id(operation_id) or safe_request_id
    safe_attempt = max(0, int(attempt))
    return {
        "schema_version": DIAGNOSTIC_SCHEMA_VERSION,
        "event": event,
        "timestamp_utc": utc_timestamp(),
        "call_id": _call_id(safe_request_id, safe_attempt),
        "operation_id": safe_operation_id,
        "request_id": safe_request_id,
        "attempt": safe_attempt,
        "request_kind": _sanitize_text(request_kind)[:80],
        "protocol": _sanitize_text(protocol)[:80],
        "model": _safe_id(model),
        "endpoint_host": safe_host_label(endpoint_host),
        "workspace_module": _safe_workspace_label(workspace_module),
        "workspace_task_kind": _safe_workspace_label(workspace_task_kind),
    }


def _encoded_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _content_truncation(value: object) -> dict[str, object]:
    encoded = _encoded_json(value)
    return {
        "content_truncated": True,
        "original_bytes": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }


def _truncate_utf8(value: object, maximum_bytes: int) -> str:
    encoded = str(value or "").encode("utf-8")
    safe_maximum = max(0, int(maximum_bytes))
    if len(encoded) <= safe_maximum:
        return str(value or "")
    return encoded[:safe_maximum].decode("utf-8", errors="ignore")


def _bounded_event_line(record: Mapping[str, object]) -> str:
    safe = _json_safe(record)
    if not isinstance(safe, dict):
        safe = {"event": "invalid", "record": safe}
    encoded = _encoded_json(safe)
    if len(encoded) + 1 <= DIAGNOSTIC_MAX_EVENT_BYTES:
        return encoded.decode("utf-8") + "\n"

    bounded = dict(safe)
    bounded["event_truncated"] = True
    bounded["original_event_bytes"] = len(encoded) + 1
    bounded["event_sha256"] = hashlib.sha256(encoded).hexdigest()
    for key in ("request", "parsed_result"):
        if key in bounded:
            bounded[key] = _content_truncation(bounded[key])
    if "raw_response" in bounded:
        bounded["raw_response"] = _truncate_utf8(
            bounded["raw_response"],
            DIAGNOSTIC_MAX_EVENT_BYTES // 4,
        )
    if "parse_error" in bounded:
        bounded["parse_error"] = _truncate_utf8(bounded["parse_error"], 4096)
    error = bounded.get("error")
    if isinstance(error, dict) and "message" in error:
        bounded["error"] = {
            **error,
            "message": _truncate_utf8(error.get("message"), 16_384),
        }

    encoded = _encoded_json(bounded)
    if len(encoded) + 1 > DIAGNOSTIC_MAX_EVENT_BYTES:
        if "attachments" in bounded:
            bounded["attachments"] = _content_truncation(
                bounded["attachments"]
            )
        bounded["raw_response"] = ""
        bounded["parse_operations"] = []
        encoded = _encoded_json(bounded)
    if len(encoded) + 1 > DIAGNOSTIC_MAX_EVENT_BYTES:
        identity_keys = {
            "schema_version",
            "event",
            "timestamp_utc",
            "call_id",
            "operation_id",
            "request_id",
            "attempt",
            "request_kind",
            "protocol",
            "model",
            "endpoint_host",
            "workspace_module",
            "workspace_task_kind",
            "outcome",
            "event_truncated",
            "original_event_bytes",
            "event_sha256",
        }
        bounded = {
            key: value
            for key, value in bounded.items()
            if key in identity_keys
        }
        bounded["content"] = {
            "content_truncated": True,
            "reason": "event_size_limit",
        }
        encoded = _encoded_json(bounded)
    if len(encoded) + 1 > DIAGNOSTIC_MAX_EVENT_BYTES:
        raise ValueError("diagnostic event metadata exceeds size limit")
    return encoded.decode("utf-8") + "\n"


class NullDiagnosticSink:
    def record_request(self, **_: object) -> None:
        return None

    def record_response(self, **_: object) -> None:
        return None

    def record_failure(self, **_: object) -> None:
        return None

    def record_validation(self, **_: object) -> None:
        return None


class _WorkspaceDiagnosticSink:
    def __init__(
        self,
        journal: JsonlDiagnosticJournal,
        *,
        workspace_module: object,
        workspace_task_kind: object,
    ) -> None:
        self.journal = journal
        self.workspace_module = _safe_workspace_label(workspace_module)
        self.workspace_task_kind = _safe_workspace_label(workspace_task_kind)

    def record_request(self, **kwargs: object) -> None:
        self.journal.record_request(
            **kwargs,
            workspace_module=self.workspace_module,
            workspace_task_kind=self.workspace_task_kind,
        )

    def record_response(self, **kwargs: object) -> None:
        self.journal.record_response(
            **kwargs,
            workspace_module=self.workspace_module,
            workspace_task_kind=self.workspace_task_kind,
        )

    def record_failure(self, **kwargs: object) -> None:
        self.journal.record_failure(
            **kwargs,
            workspace_module=self.workspace_module,
            workspace_task_kind=self.workspace_task_kind,
        )


def _line_mentions_call_id(line: str, call_id: str) -> bool:
    return (
        f'"call_id":"{call_id}"' in line
        or f'"call_id": "{call_id}"' in line
    )


class JsonlDiagnosticJournal:
    def __init__(self, path: str | Path = DIAGNOSTIC_LOG_FILE) -> None:
        self.path = Path(path)

    def for_workspace(
        self,
        *,
        workspace_module: object,
        workspace_task_kind: object,
    ) -> _WorkspaceDiagnosticSink:
        return _WorkspaceDiagnosticSink(
            self,
            workspace_module=workspace_module,
            workspace_task_kind=workspace_task_kind,
        )

    def record_request(
        self,
        *,
        operation_id: object,
        request_id: object,
        attempt: int,
        request_kind: object,
        protocol: object,
        model: object,
        endpoint_host: object,
        kwargs: Mapping[str, object],
        retry_limit: int,
        retry_index: int,
        timeout_seconds: float,
        workspace_module: object = "",
        workspace_task_kind: object = "",
    ) -> None:
        payload, attachments = sanitize_request_payload(dict(kwargs))
        event = _base_event(
            event="request",
            operation_id=operation_id,
            request_id=request_id,
            attempt=attempt,
            request_kind=request_kind,
            protocol=protocol,
            model=model,
            endpoint_host=endpoint_host,
            workspace_module=workspace_module,
            workspace_task_kind=workspace_task_kind,
        )
        event.update(
            {
                "request": payload,
                "attachments": attachments,
                "retry_limit": max(0, int(retry_limit)),
                "retry_index": max(0, int(retry_index)),
                "timeout_seconds": max(0.0, float(timeout_seconds)),
            }
        )
        self._append(event)

    def record_response(
        self,
        *,
        operation_id: object,
        request_id: object,
        attempt: int,
        request_kind: object,
        protocol: object,
        model: object,
        endpoint_host: object,
        response: object,
        elapsed_ms: int,
        workspace_module: object = "",
        workspace_task_kind: object = "",
    ) -> None:
        raw_text = _sanitize_text(response_text(response))
        diagnostics = response_diagnostics(response)
        parse_status, parsed_result, operations, parse_error = (
            _parse_response_text(
                raw_text,
                output_truncated=bool(
                    diagnostics.get("output_truncated")
                ),
            )
        )
        event = _base_event(
            event="response",
            operation_id=operation_id,
            request_id=request_id,
            attempt=attempt,
            request_kind=request_kind,
            protocol=protocol,
            model=model,
            endpoint_host=endpoint_host,
            workspace_module=workspace_module,
            workspace_task_kind=workspace_task_kind,
        )
        event.update(
            {
                "outcome": "success",
                "elapsed_ms": max(0, int(elapsed_ms)),
                "raw_response": raw_text,
                "response_chars": len(raw_text),
                "response_sha256": (
                    hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
                    if raw_text
                    else ""
                ),
                "parse_status": parse_status,
                "parse_operations": operations,
                "parse_error": parse_error,
                "parsed_result": parsed_result,
                "will_retry": False,
                "retry_delay_ms": 0,
                "error": None,
            }
        )
        self._append(event)

    def record_failure(
        self,
        *,
        operation_id: object,
        request_id: object,
        attempt: int,
        request_kind: object,
        protocol: object,
        model: object,
        endpoint_host: object,
        elapsed_ms: int,
        error_category: object,
        exception_type: object,
        http_status_code: int,
        error_message: object,
        will_retry: bool,
        retry_delay_ms: int,
        workspace_module: object = "",
        workspace_task_kind: object = "",
    ) -> None:
        event = _base_event(
            event="failure",
            operation_id=operation_id,
            request_id=request_id,
            attempt=attempt,
            request_kind=request_kind,
            protocol=protocol,
            model=model,
            endpoint_host=endpoint_host,
            workspace_module=workspace_module,
            workspace_task_kind=workspace_task_kind,
        )
        event.update(
            {
                "outcome": "failure",
                "elapsed_ms": max(0, int(elapsed_ms)),
                "raw_response": "",
                "response_chars": 0,
                "response_sha256": "",
                "parse_status": "not_available",
                "parse_operations": [],
                "parse_error": "",
                "parsed_result": None,
                "will_retry": bool(will_retry),
                "retry_delay_ms": max(0, int(retry_delay_ms)),
                "error": {
                    "category": _sanitize_text(error_category)[:80],
                    "exception_type": _sanitize_text(exception_type)[:120],
                    "http_status_code": max(0, int(http_status_code)),
                    "message": _sanitize_text(error_message),
                },
            }
        )
        self._append(event)

    def record_validation(
        self,
        *,
        operation_id: object,
        validation_issue_codes: object,
        workspace_module: object,
        workspace_task_kind: object,
    ) -> None:
        if not isinstance(validation_issue_codes, (list, tuple)):
            raise ValueError("validation issue codes are invalid")
        codes = list(dict.fromkeys(str(item) for item in validation_issue_codes))
        if not codes or any(
            code not in _VALIDATION_ISSUE_CODES for code in codes
        ):
            raise ValueError("validation issue code is not approved")
        safe_operation_id = _safe_id(operation_id)
        if not safe_operation_id:
            raise ValueError("validation operation ID is invalid")
        event = _base_event(
            event="validation",
            request_id=safe_operation_id,
            operation_id=safe_operation_id,
            attempt=1,
            request_kind="workspace",
            protocol="validation",
            model="",
            endpoint_host="",
            workspace_module=workspace_module,
            workspace_task_kind=workspace_task_kind,
        )
        event["validation_issue_codes"] = codes
        self._append(event)

    def _append(self, record: Mapping[str, object]) -> None:
        line = _bounded_event_line(record)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _path_lock(self.path):
            self._rotate_if_needed(len(line.encode("utf-8")))
            with self.path.open("a", encoding="utf-8", newline="") as handle:
                handle.write(line)

    def _rotate_if_needed(self, incoming_bytes: int) -> None:
        try:
            current_bytes = self.path.stat().st_size
        except FileNotFoundError:
            return
        if current_bytes + max(0, int(incoming_bytes)) <= (
            DIAGNOSTIC_MAX_FILE_BYTES
        ):
            return
        for index in range(DIAGNOSTIC_ROTATED_FILE_COUNT, 0, -1):
            target = self._rotated_path(index)
            source = (
                self.path
                if index == 1
                else self._rotated_path(index - 1)
            )
            if target.exists():
                target.unlink()
            if source.exists():
                source.replace(target)

    def _rotated_path(self, index: int) -> Path:
        return self.path.with_name(f"{self.path.name}.{max(1, int(index))}")

    def _read_paths(self) -> list[Path]:
        paths = [
            self._rotated_path(index)
            for index in range(DIAGNOSTIC_ROTATED_FILE_COUNT, 0, -1)
        ]
        paths.append(self.path)
        return paths

    def list_calls(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        request_kind: str = "",
        outcome: str = "",
        workspace_module: str = "",
        workspace_task_kind: str = "",
    ) -> dict[str, object]:
        calls, scanned_event_count, read_truncated = self._merged_calls(
            include_bodies=False,
        )
        normalized_kind = str(request_kind or "").strip()
        normalized_outcome = str(outcome or "").strip()
        normalized_module = _safe_workspace_label(workspace_module)
        normalized_task_kind = _safe_workspace_label(workspace_task_kind)
        filtered = [
            call
            for call in calls.values()
            if (
                not normalized_kind
                or call.get("request_kind") == normalized_kind
            )
            and (
                not normalized_outcome
                or call.get("outcome") == normalized_outcome
            )
            and (
                not normalized_module
                or call.get("workspace_module") == normalized_module
            )
            and (
                not normalized_task_kind
                or call.get("workspace_task_kind") == normalized_task_kind
            )
        ]
        filtered.sort(
            key=lambda item: str(item.get("started_at_utc") or ""),
            reverse=True,
        )
        safe_limit = min(100, max(1, int(limit)))
        safe_offset = max(0, int(offset))
        items = [
            self._summary(call)
            for call in filtered[safe_offset:safe_offset + safe_limit]
        ]
        return {
            "items": items,
            "returned": len(items),
            "matching": len(filtered),
            "scanned_event_count": scanned_event_count,
            "truncated": read_truncated or len(filtered) > safe_offset + safe_limit,
        }

    def get_call(self, call_id: str) -> dict[str, object] | None:
        safe_call_id = str(call_id or "").strip()
        if not re.fullmatch(r"[0-9a-f]{24}", safe_call_id):
            return None
        calls, _, _ = self._merged_calls(
            include_bodies=True,
            only_call_id=safe_call_id,
        )
        call = calls.get(safe_call_id)
        if call is None:
            return None
        return _json_safe(call)

    def clear_workspace(self, workspace_module: str) -> dict[str, object]:
        normalized_module = _safe_workspace_label(workspace_module)
        if not normalized_module:
            raise ValueError("workspace module is invalid")
        deleted = 0
        retained = 0
        unclassified = 0
        temporary_paths: list[Path] = []
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _path_lock(self.path):
            try:
                for path in self._read_paths():
                    if not path.exists():
                        continue
                    temporary = path.with_name(f".{path.name}.rewrite.tmp")
                    temporary.unlink(missing_ok=True)
                    temporary_paths.append(temporary)
                    with (
                        path.open("r", encoding="utf-8") as source,
                        temporary.open("w", encoding="utf-8", newline="") as target,
                    ):
                        for line in source:
                            try:
                                event = json.loads(line)
                            except (json.JSONDecodeError, UnicodeDecodeError):
                                event = None
                            event_module = (
                                _safe_workspace_label(event.get("workspace_module"))
                                if isinstance(event, Mapping)
                                else ""
                            )
                            if event_module == normalized_module:
                                deleted += 1
                                continue
                            if not event_module:
                                unclassified += 1
                            retained += 1
                            target.write(line)
                    temporary.replace(path)
                    temporary_paths.remove(temporary)
            finally:
                for temporary in temporary_paths:
                    temporary.unlink(missing_ok=True)
        return {
            "workspace_module": normalized_module,
            "deleted_event_count": deleted,
            "retained_event_count": retained,
            "unclassified_event_count": unclassified,
        }

    def _merged_calls(
        self,
        *,
        include_bodies: bool = True,
        only_call_id: str | None = None,
    ) -> tuple[dict[str, dict[str, Any]], int, bool]:
        recent: deque[dict[str, Any]] = deque(maxlen=_MAX_READ_EVENTS)
        scanned = 0
        target_call_id = str(only_call_id or "").strip()
        with _path_lock(self.path):
            try:
                for path in self._read_paths():
                    if not path.exists():
                        continue
                    with path.open("r", encoding="utf-8") as handle:
                        for line in handle:
                            scanned += 1
                            if (
                                target_call_id
                                and not _line_mentions_call_id(
                                    line,
                                    target_call_id,
                                )
                            ):
                                continue
                            try:
                                record = json.loads(line)
                            except (
                                json.JSONDecodeError,
                                UnicodeDecodeError,
                            ):
                                continue
                            if not isinstance(record, dict):
                                continue
                            if not include_bodies:
                                for key in _HEAVY_EVENT_KEYS:
                                    record.pop(key, None)
                            recent.append(record)
            except OSError:
                return {}, 0, False
        calls: dict[str, dict[str, Any]] = {}
        for event in recent:
            call_id = str(event.get("call_id") or "")
            if not re.fullmatch(r"[0-9a-f]{24}", call_id):
                continue
            if target_call_id and call_id != target_call_id:
                continue
            current = calls.setdefault(
                call_id,
                {
                    "call_id": call_id,
                    "operation_id": str(event.get("operation_id") or ""),
                    "request_id": str(event.get("request_id") or ""),
                    "attempt": max(0, int(event.get("attempt") or 0)),
                    "request_kind": str(event.get("request_kind") or ""),
                    "protocol": str(event.get("protocol") or ""),
                    "model": str(event.get("model") or ""),
                    "endpoint_host": str(event.get("endpoint_host") or ""),
                    "workspace_module": str(event.get("workspace_module") or ""),
                    "workspace_task_kind": str(event.get("workspace_task_kind") or ""),
                    "started_at_utc": "",
                    "finished_at_utc": "",
                    "outcome": "pending",
                    "elapsed_ms": 0,
                    "image_count": 0,
                    "retry_limit": 0,
                    "retry_index": 0,
                    "will_retry": False,
                    "retry_delay_ms": 0,
                    "request": {},
                    "attachments": [],
                    "raw_response": "",
                    "response_chars": 0,
                    "response_sha256": "",
                    "parse_status": "not_available",
                    "parse_operations": [],
                    "parse_error": "",
                    "parsed_result": None,
                    "validation_issue_codes": [],
                    "error": None,
                },
            )
            event_type = str(event.get("event") or "")
            if event_type == "request":
                current["started_at_utc"] = str(
                    event.get("timestamp_utc") or ""
                )
                attachments = event.get("attachments") or []
                current["image_count"] = (
                    len(attachments)
                    if isinstance(attachments, list)
                    else 0
                )
                current["retry_limit"] = max(
                    0,
                    int(event.get("retry_limit") or 0),
                )
                current["retry_index"] = max(
                    0,
                    int(event.get("retry_index") or 0),
                )
                if include_bodies:
                    current["request"] = event.get("request") or {}
                    current["attachments"] = attachments
            elif event_type in {"response", "failure"}:
                current["finished_at_utc"] = str(
                    event.get("timestamp_utc") or ""
                )
                for key in (
                    "outcome",
                    "elapsed_ms",
                    "will_retry",
                    "retry_delay_ms",
                    "response_chars",
                    "response_sha256",
                    "parse_status",
                    "parse_operations",
                    "parse_error",
                    "error",
                ):
                    current[key] = event.get(key)
                if include_bodies:
                    current["raw_response"] = event.get("raw_response")
                    current["parsed_result"] = event.get("parsed_result")
            elif event_type == "validation":
                codes = event.get("validation_issue_codes")
                if isinstance(codes, list):
                    current["validation_issue_codes"] = [
                        str(item) for item in codes
                    ]
        return calls, scanned, scanned > len(recent)

    @staticmethod
    def _summary(call: Mapping[str, object]) -> dict[str, object]:
        image_count = max(0, int(call.get("image_count") or 0))
        attachments = call.get("attachments")
        if isinstance(attachments, list) and attachments:
            image_count = len(attachments)
        return {
            "call_id": str(call.get("call_id") or ""),
            "operation_id": str(call.get("operation_id") or ""),
            "request_id": str(call.get("request_id") or ""),
            "attempt": max(0, int(call.get("attempt") or 0)),
            "request_kind": str(call.get("request_kind") or ""),
            "protocol": str(call.get("protocol") or ""),
            "model": str(call.get("model") or ""),
            "workspace_module": str(call.get("workspace_module") or ""),
            "workspace_task_kind": str(call.get("workspace_task_kind") or ""),
            "started_at_utc": str(call.get("started_at_utc") or ""),
            "finished_at_utc": str(call.get("finished_at_utc") or ""),
            "outcome": str(call.get("outcome") or "pending"),
            "elapsed_ms": max(0, int(call.get("elapsed_ms") or 0)),
            "image_count": image_count,
            "response_chars": max(
                0,
                int(call.get("response_chars") or 0),
            ),
            "will_retry": bool(call.get("will_retry")),
        }
