from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping

from .execution import (
    execution_scope_key,
    execution_snapshot_from_profile,
)


class LLMPolicyError(ValueError):
    """Raised when an LLM policy override is invalid."""


class LLMRequestKind(str, Enum):
    GRADING = "grading"
    RECOGNITION = "recognition"
    CONFIG_GENERATION = "config_generation"
    TAGGING = "tagging"
    WORKSPACE = "workspace"
    ASSEMBLY = "assembly"


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
            600.0, 2, 1000, (0.5, 1.5, 3.0, 5.0, 8.0)
        ),
        LLMRequestKind.RECOGNITION: LLMRequestPolicy(
            60.0, 2, 1000, (0.5, 1.5, 3.0, 5.0, 8.0)
        ),
        LLMRequestKind.CONFIG_GENERATION: LLMRequestPolicy(
            600.0, 2, 1000, (0.5, 1.5, 3.0, 5.0, 8.0)
        ),
        LLMRequestKind.TAGGING: LLMRequestPolicy(
            480.0, 2, 1000, (0.5, 1.5, 3.0, 5.0, 8.0)
        ),
        LLMRequestKind.WORKSPACE: LLMRequestPolicy(
            120.0, 0, 1000, ()
        ),
        # AI 组卷细目表为单次交互请求：失败不自动重发（用户确认后才再调）。
        LLMRequestKind.ASSEMBLY: LLMRequestPolicy(
            300.0, 0, 1000, ()
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
_EXECUTION_PROFILE_FIELDS = frozenset(
    {
        "request_speed_mode",
        "max_concurrent_requests",
        "requests_per_minute",
    }
)
# Channel-level default for automatic retries.  When present it replaces the
# per-kind default; the finer-grained llm_{kind}_max_retries keys still win.
MAX_AUTO_RETRIES_PROFILE_FIELD = "max_auto_retries"
MAX_AUTO_RETRIES_MIN = 0
MAX_AUTO_RETRIES_MAX = 5
# Channel-level default for a single request timeout on online channels.
# When present it replaces each kind's default; the finer-grained
# llm_{kind}_timeout_seconds keys still win.
REQUEST_TIMEOUT_PROFILE_FIELD = "request_timeout_seconds"
REQUEST_TIMEOUT_MIN = 30.0
REQUEST_TIMEOUT_MAX = 1200.0
EXECUTION_SCOPE_PROFILE_FIELD = "_llm_execution_scope_key"


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


_TIMEOUT_OVERRIDE_CAP = REQUEST_TIMEOUT_MAX

_RETRY_COUNT_CAP = 5


def _retry_delays_for(
    base: LLMRequestPolicy,
    retries: int,
) -> tuple[float, ...]:
    delays = base.retry_delays[:retries]
    if len(delays) < retries:
        tail = delays[-1] if delays else 0.0
        delays = delays + (tail,) * (retries - len(delays))
    return delays


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
        _bounded_float(
            values,
            REQUEST_TIMEOUT_PROFILE_FIELD,
            base.timeout_seconds,
            REQUEST_TIMEOUT_MIN,
            REQUEST_TIMEOUT_MAX,
        ),
        1.0,
        _TIMEOUT_OVERRIDE_CAP,
    )
    retries = _bounded_int(
        values,
        f"{prefix}_max_retries",
        _bounded_int(
            values,
            MAX_AUTO_RETRIES_PROFILE_FIELD,
            base.max_retries,
            MAX_AUTO_RETRIES_MIN,
            MAX_AUTO_RETRIES_MAX,
        ),
        0,
        _RETRY_COUNT_CAP,
    )
    if _EXECUTION_PROFILE_FIELDS.intersection(values):
        requests_per_minute = (
            execution_snapshot_from_profile(values).requests_per_minute
        )
    else:
        legacy_rpm_values = [
            _bounded_int(
                values,
                f"llm_{candidate.value}_requests_per_minute",
                DEFAULT_POLICIES[candidate].requests_per_minute,
                1,
                5000,
            )
            for candidate in LLMRequestKind
        ]
        requests_per_minute = min(legacy_rpm_values)
    return LLMRequestPolicy(
        timeout,
        retries,
        requests_per_minute,
        _retry_delays_for(base, retries),
    )


def policy_overrides_from_profile(
    profile: Mapping[str, object] | None,
) -> dict[str, object]:
    values = profile or {}
    overrides = {
        field: value
        for field, value in values.items()
        if field in _POLICY_OVERRIDE_FIELDS
        or field in _EXECUTION_PROFILE_FIELDS
        or field == MAX_AUTO_RETRIES_PROFILE_FIELD
        or field == REQUEST_TIMEOUT_PROFILE_FIELD
    }
    overrides[EXECUTION_SCOPE_PROFILE_FIELD] = execution_scope_key(values)
    return overrides
