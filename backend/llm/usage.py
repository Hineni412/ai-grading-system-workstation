from __future__ import annotations

import hashlib
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping

from usage_logger import log_llm_usage


logger = logging.getLogger(__name__)
_SAFE_FINISH_REASONS = frozenset(
    {
        "stop",
        "length",
        "content_filter",
        "tool_calls",
        "function_call",
        "max_tokens",
        "max_completion_tokens",
        "max_output_tokens",
    }
)
_TRUNCATION_FINISH_REASONS = frozenset(
    {
        "length",
        "max_tokens",
        "max_completion_tokens",
        "max_output_tokens",
    }
)


@dataclass(frozen=True, slots=True)
class LLMUsageEvent:
    request_id: str
    attempt: int
    request_kind: str
    protocol: str
    model: str
    latency_ms: int
    success: bool
    error_category: str = ""
    compatibility_fallback: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    finish_reason: str = ""
    output_truncated: bool = False
    response_chars: int = 0
    response_sha256: str = ""


def _value(source: object, *names: str) -> object:
    for name in names:
        if isinstance(source, Mapping):
            value = source.get(name)
        else:
            value = getattr(source, name, None)
        if value is not None:
            return value
    return 0


def usage_fields(response_or_usage: object) -> dict[str, int]:
    nested_usage = (
        response_or_usage.get("usage")
        if isinstance(response_or_usage, Mapping)
        else getattr(response_or_usage, "usage", None)
    )
    usage = nested_usage or response_or_usage
    if not usage:
        return {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "cached_tokens": 0,
            "reasoning_tokens": 0,
            "total_tokens": 0,
        }

    prompt_details = _value(
        usage, "prompt_tokens_details", "input_tokens_details"
    )
    completion_details = _value(
        usage, "completion_tokens_details", "output_tokens_details"
    )
    return {
        "prompt_tokens": int(_value(usage, "prompt_tokens", "input_tokens") or 0),
        "completion_tokens": int(
            _value(usage, "completion_tokens", "output_tokens") or 0
        ),
        "cached_tokens": int(
            _value(prompt_details, "cached_tokens")
            or _value(usage, "cached_tokens")
            or 0
        ),
        "reasoning_tokens": int(
            _value(completion_details, "reasoning_tokens")
            or _value(usage, "reasoning_tokens")
            or 0
        ),
        "total_tokens": int(_value(usage, "total_tokens") or 0),
    }


def _optional_value(source: object, name: str) -> object | None:
    if isinstance(source, Mapping):
        return source.get(name)
    return getattr(source, name, None)


def _normalized_response_text(response: object) -> str:
    choices = _optional_value(response, "choices") or []
    if choices:
        first = choices[0]
        message = _optional_value(first, "message")
        content = _optional_value(message, "content") if message is not None else None
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                    continue
                text = _optional_value(item, "text")
                if isinstance(text, str):
                    parts.append(text)
            return "\n".join(parts)
    output_text = _optional_value(response, "output_text")
    return output_text if isinstance(output_text, str) else ""


def is_truncation_finish_reason(value: object) -> bool:
    return str(value or "").strip().lower() in _TRUNCATION_FINISH_REASONS


def looks_like_truncated_json_object(text: str) -> bool:
    cleaned = str(text or "").strip().lstrip("\ufeff")
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    if cleaned.lower().startswith("json\n"):
        cleaned = cleaned[5:].strip()
    if not cleaned.startswith("{"):
        return False

    stack: list[str] = []
    pairs = {"}": "{", "]": "["}
    in_string = False
    escape = False
    for char in cleaned:
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char in "{[":
            stack.append(char)
            continue
        if char in "}]":
            if not stack or stack[-1] != pairs[char]:
                return False
            stack.pop()
            if not stack:
                return False
    return bool(stack)


def response_diagnostics(response: object) -> dict[str, object]:
    choices = _optional_value(response, "choices") or []
    finish_reason = ""
    if choices:
        raw_reason = _optional_value(choices[0], "finish_reason")
        if raw_reason is not None:
            finish_reason = str(raw_reason)
    if not finish_reason:
        incomplete = _optional_value(response, "incomplete_details")
        raw_reason = (
            _optional_value(incomplete, "reason")
            if incomplete is not None
            else None
        )
        if raw_reason is not None:
            finish_reason = str(raw_reason)

    text = _normalized_response_text(response)
    normalized_reason = finish_reason.strip().lower()
    if normalized_reason and normalized_reason not in _SAFE_FINISH_REASONS:
        normalized_reason = "unknown"
    finish_reason = normalized_reason
    output_truncated = is_truncation_finish_reason(
        normalized_reason
    ) or (
        not normalized_reason and looks_like_truncated_json_object(text)
    )
    return {
        "finish_reason": finish_reason,
        "output_truncated": output_truncated,
        "response_chars": len(text),
        "response_sha256": (
            hashlib.sha256(text.encode("utf-8")).hexdigest() if text else ""
        ),
    }


def response_content_bytes(response: object) -> int:
    """Return the UTF-8 size of normalized response text without exposing it."""
    return len(_normalized_response_text(response).encode("utf-8"))


class JsonlUsageSink:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def write(self, event: LLMUsageEvent) -> None:
        try:
            log_llm_usage(asdict(event), log_file=self.path)
        except Exception:
            logger.warning("Failed to record LLM usage metadata")


class NullUsageSink:
    def write(self, event: LLMUsageEvent) -> None:
        return None
