from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping

from usage_logger import log_llm_usage


logger = logging.getLogger(__name__)


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
