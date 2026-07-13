from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping


class LLMPolicyError(ValueError):
    """Raised when an LLM policy override is invalid."""


class LLMRequestKind(str, Enum):
    GRADING = "grading"
    RECOGNITION = "recognition"
    CONFIG_GENERATION = "config_generation"
    TAGGING = "tagging"


class LLMProtocol(str, Enum):
    CHAT_COMPLETIONS = "chat_completions"
    RESPONSES = "responses"


@dataclass(frozen=True, slots=True)
class LLMRequestPolicy:
    timeout_seconds: float
    max_retries: int
    requests_per_minute: int
    retry_delays: tuple[float, ...]


DEFAULT_POLICIES: Mapping[LLMRequestKind, LLMRequestPolicy] = MappingProxyType(
    {
        LLMRequestKind.GRADING: LLMRequestPolicy(
            300.0, 2, 1000, (0.5, 1.5, 3.0, 5.0, 8.0)
        ),
        LLMRequestKind.RECOGNITION: LLMRequestPolicy(
            60.0, 2, 1000, (0.5, 1.5, 3.0, 5.0, 8.0)
        ),
        LLMRequestKind.CONFIG_GENERATION: LLMRequestPolicy(
            120.0, 2, 1000, (0.5, 1.5, 3.0, 5.0, 8.0)
        ),
        LLMRequestKind.TAGGING: LLMRequestPolicy(
            120.0, 2, 1000, (0.5, 1.5, 3.0, 5.0, 8.0)
        ),
    }
)

_POLICY_SUFFIXES = (
    "timeout_seconds",
    "max_retries",
    "requests_per_minute",
)
_POLICY_OVERRIDE_FIELDS = frozenset(
    f"llm_{kind.value}_{suffix}"
    for kind in LLMRequestKind
    for suffix in _POLICY_SUFFIXES
)


def _bounded_float(
    values: Mapping[str, object],
    field: str,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    if field not in values:
        return default
    value = values[field]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LLMPolicyError(f"{field} must be a finite number")
    parsed = float(value)
    if not math.isfinite(parsed) or not minimum <= parsed <= maximum:
        raise LLMPolicyError(f"{field} must be between {minimum} and {maximum}")
    return parsed


def _bounded_int(
    values: Mapping[str, object],
    field: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    if field not in values:
        return default
    value = values[field]
    if isinstance(value, bool) or not isinstance(value, int):
        raise LLMPolicyError(f"{field} must be an integer")
    if not minimum <= value <= maximum:
        raise LLMPolicyError(f"{field} must be between {minimum} and {maximum}")
    return value


def policy_from_profile(
    kind: LLMRequestKind,
    profile: Mapping[str, object] | None,
) -> LLMRequestPolicy:
    request_kind = LLMRequestKind(kind)
    base = DEFAULT_POLICIES[request_kind]
    values = dict(profile or {})
    prefix = f"llm_{request_kind.value}"
    timeout = _bounded_float(
        values,
        f"{prefix}_timeout_seconds",
        base.timeout_seconds,
        1.0,
        600.0,
    )
    retries = _bounded_int(
        values,
        f"{prefix}_max_retries",
        base.max_retries,
        0,
        5,
    )
    requests_per_minute = _bounded_int(
        values,
        f"{prefix}_requests_per_minute",
        base.requests_per_minute,
        1,
        5000,
    )
    return LLMRequestPolicy(
        timeout,
        retries,
        requests_per_minute,
        base.retry_delays[:retries],
    )


def policy_overrides_from_profile(
    profile: Mapping[str, object] | None,
) -> dict[str, object]:
    values = profile or {}
    return {
        field: value
        for field, value in values.items()
        if field in _POLICY_OVERRIDE_FIELDS
    }
