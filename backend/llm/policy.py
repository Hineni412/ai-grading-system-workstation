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
    GRADING_BATCH = "grading_batch"
    RECOGNITION = "recognition"
    CONFIG_GENERATION = "config_generation"
    TAGGING = "tagging"
    TAGGING_BATCH = "tagging_batch"
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
    # Batch endpoints queue requests server-side, so a client-side timeout
    # must not trigger a silent resend (the original request may still be
    # processed and billed).  Online channels keep the legacy behaviour.
    retry_on_timeout: bool = True


DEFAULT_POLICIES: Mapping[LLMRequestKind, LLMRequestPolicy] = MappingProxyType(
    {
        LLMRequestKind.GRADING: LLMRequestPolicy(
            300.0, 2, 1000, (0.5, 1.5, 3.0, 5.0, 8.0)
        ),
        LLMRequestKind.GRADING_BATCH: LLMRequestPolicy(
            3600.0, 10, 1000, (2.0, 5.0, 10.0, 20.0, 40.0), False
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
        LLMRequestKind.TAGGING_BATCH: LLMRequestPolicy(
            3600.0, 10, 1000, (2.0, 5.0, 10.0, 20.0, 40.0), False
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
# When present it replaces each online kind's default; the finer-grained
# llm_{kind}_timeout_seconds keys still win, and batch channels ignore it.
REQUEST_TIMEOUT_PROFILE_FIELD = "request_timeout_seconds"
REQUEST_TIMEOUT_MIN = 30.0
REQUEST_TIMEOUT_MAX = 1200.0
_BATCH_REQUEST_KINDS = frozenset(
    {LLMRequestKind.GRADING_BATCH, LLMRequestKind.TAGGING_BATCH}
)
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


# Batch inference requests may legitimately pend for tens of minutes, so the
# batch channel accepts much longer timeout overrides than online channels.
_TIMEOUT_OVERRIDE_CAPS: Mapping[LLMRequestKind, float] = MappingProxyType(
    {
        LLMRequestKind.GRADING_BATCH: 7200.0,
        LLMRequestKind.TAGGING_BATCH: 7200.0,
    }
)
_DEFAULT_TIMEOUT_OVERRIDE_CAP = REQUEST_TIMEOUT_MAX

# A rejected (429/503) batch attempt is never billed, so batch channels may
# keep drawing tickets far longer than online channels.
_RETRY_COUNT_CAPS: Mapping[LLMRequestKind, int] = MappingProxyType(
    {
        LLMRequestKind.GRADING_BATCH: 20,
        LLMRequestKind.TAGGING_BATCH: 20,
    }
)
_DEFAULT_RETRY_COUNT_CAP = 5


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
        (
            base.timeout_seconds
            if request_kind in _BATCH_REQUEST_KINDS
            else _bounded_float(
                values,
                REQUEST_TIMEOUT_PROFILE_FIELD,
                base.timeout_seconds,
                REQUEST_TIMEOUT_MIN,
                REQUEST_TIMEOUT_MAX,
            )
        ),
        1.0,
        _TIMEOUT_OVERRIDE_CAPS.get(request_kind, _DEFAULT_TIMEOUT_OVERRIDE_CAP),
    )
    retries = _bounded_int(
        values,
        f"{prefix}_max_retries",
        _bounded_int(
            values,
            MAX_AUTO_RETRIES_PROFILE_FIELD,
            base.max_retries,
            MAX_AUTO_RETRIES_MIN,
            min(
                MAX_AUTO_RETRIES_MAX,
                _RETRY_COUNT_CAPS.get(request_kind, _DEFAULT_RETRY_COUNT_CAP),
            ),
        ),
        0,
        _RETRY_COUNT_CAPS.get(request_kind, _DEFAULT_RETRY_COUNT_CAP),
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
        base.retry_on_timeout,
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
