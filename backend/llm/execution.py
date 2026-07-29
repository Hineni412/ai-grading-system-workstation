from __future__ import annotations

import hashlib
import math
import threading
import time
from dataclasses import dataclass
from typing import Mapping

from .errors import LLMErrorCategory


REQUEST_SPEED_MODES = frozenset({"automatic", "conservative", "custom"})
MAX_CONCURRENT_REQUESTS_MIN = 1
MAX_CONCURRENT_REQUESTS_MAX = 100
REQUESTS_PER_MINUTE_MIN = 1
REQUESTS_PER_MINUTE_MAX = 10_000
AUTOMATIC_INITIAL_MAX_IN_FLIGHT = 6
AUTOMATIC_MAX_IN_FLIGHT = 20
AUTOMATIC_REQUESTS_PER_MINUTE = 1000
CONSERVATIVE_MAX_IN_FLIGHT = 1
CONSERVATIVE_REQUESTS_PER_MINUTE = 60
_RECOVERY_SUCCESS_WINDOW = 20
_PROFILE_SCOPE_SALT = "ai-grading-model-profile-execution-v1"
_OVERLOAD_CATEGORIES = frozenset(
    {
        LLMErrorCategory.RATE_LIMIT,
        LLMErrorCategory.SERVER_TRANSIENT,
    }
)


class LLMExecutionSettingsError(ValueError):
    """Raised when public model execution settings are invalid."""


@dataclass(frozen=True, slots=True)
class LLMExecutionSnapshot:
    mode: str
    initial_max_in_flight: int
    max_in_flight: int
    requests_per_minute: int

    def as_public_dict(self) -> dict[str, int | str]:
        return {
            "mode": self.mode,
            "initial_max_in_flight": self.initial_max_in_flight,
            "max_in_flight": self.max_in_flight,
            "requests_per_minute": self.requests_per_minute,
        }


def validate_execution_profile_updates(
    values: Mapping[str, object],
) -> dict[str, int | str]:
    mode = str(values.get("request_speed_mode") or "automatic").strip()
    if mode not in REQUEST_SPEED_MODES:
        raise LLMExecutionSettingsError("Request speed mode is invalid")

    normalized: dict[str, int | str] = {"request_speed_mode": mode}
    max_in_flight = _strict_int(
        values.get("max_concurrent_requests", AUTOMATIC_MAX_IN_FLIGHT),
        "max_concurrent_requests",
        MAX_CONCURRENT_REQUESTS_MIN,
        MAX_CONCURRENT_REQUESTS_MAX,
    )
    requests_per_minute = _strict_int(
        values.get("requests_per_minute", AUTOMATIC_REQUESTS_PER_MINUTE),
        "requests_per_minute",
        REQUESTS_PER_MINUTE_MIN,
        REQUESTS_PER_MINUTE_MAX,
    )
    normalized["max_concurrent_requests"] = max_in_flight
    normalized["requests_per_minute"] = requests_per_minute
    return normalized


def execution_snapshot_from_profile(
    profile: Mapping[str, object] | None,
) -> LLMExecutionSnapshot:
    values = dict(profile or {})
    mode = str(values.get("request_speed_mode") or "automatic").strip()
    if mode == "conservative":
        return LLMExecutionSnapshot(
            mode="conservative",
            initial_max_in_flight=CONSERVATIVE_MAX_IN_FLIGHT,
            max_in_flight=CONSERVATIVE_MAX_IN_FLIGHT,
            requests_per_minute=CONSERVATIVE_REQUESTS_PER_MINUTE,
        )
    if mode == "custom":
        try:
            normalized = validate_execution_profile_updates(values)
        except LLMExecutionSettingsError:
            return LLMExecutionSnapshot(
                mode="conservative",
                initial_max_in_flight=CONSERVATIVE_MAX_IN_FLIGHT,
                max_in_flight=CONSERVATIVE_MAX_IN_FLIGHT,
                requests_per_minute=CONSERVATIVE_REQUESTS_PER_MINUTE,
            )
        max_in_flight = int(normalized["max_concurrent_requests"])
        return LLMExecutionSnapshot(
            mode="custom",
            initial_max_in_flight=max_in_flight,
            max_in_flight=max_in_flight,
            requests_per_minute=int(normalized["requests_per_minute"]),
        )
    return LLMExecutionSnapshot(
        mode="automatic",
        initial_max_in_flight=AUTOMATIC_INITIAL_MAX_IN_FLIGHT,
        max_in_flight=AUTOMATIC_MAX_IN_FLIGHT,
        requests_per_minute=AUTOMATIC_REQUESTS_PER_MINUTE,
    )


def execution_scope_key(
    profile: Mapping[str, object] | None,
    *,
    fallback: str = "default",
) -> str:
    name = str((profile or {}).get("name") or "").strip()
    material = name if name else str(fallback or "default")
    digest = hashlib.sha256(
        f"{_PROFILE_SCOPE_SALT}\0{material}".encode("utf-8")
    ).hexdigest()
    return f"profile-{digest}"


class LLMExecutionPermit:
    def __init__(
        self,
        registry: "LLMExecutionGovernorRegistry",
        scope_key: str,
    ) -> None:
        self._registry = registry
        self.scope_key = scope_key
        self._completed = False
        self._lock = threading.Lock()

    def complete(
        self,
        *,
        error_category: LLMErrorCategory | None = None,
        retry_after_seconds: float = 0.0,
    ) -> None:
        with self._lock:
            if self._completed:
                return
            self._completed = True
        self._registry._complete(
            self.scope_key,
            error_category=error_category,
            retry_after_seconds=retry_after_seconds,
        )


class _ScopeState:
    def __init__(
        self,
        snapshot: LLMExecutionSnapshot,
        *,
        lock: threading.RLock,
    ) -> None:
        self.condition = threading.Condition(lock)
        self.snapshot = snapshot
        self.effective_max_in_flight = snapshot.initial_max_in_flight
        self.active = 0
        self.queued = 0
        self.peak_active = 0
        self.physical_request_count = 0
        self.successes_since_adjustment = 0
        self.cooldown_until = 0.0
        self.limiting_reason = "configured"


class LLMExecutionGovernorRegistry:
    """Shared in-process admission control for all calls in one model profile."""

    def __init__(self, *, clock=time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.RLock()
        self._entries: dict[str, _ScopeState] = {}

    def configure(
        self,
        scope_key: str,
        snapshot: LLMExecutionSnapshot,
    ) -> None:
        """Apply one explicit saved/start-time plan for a shared profile."""
        key = str(scope_key)
        with self._lock:
            state = self._entries.get(key)
            if state is None:
                self._entries[key] = _ScopeState(snapshot, lock=self._lock)
                return
            self._apply_snapshot(state, snapshot)

    def acquire(
        self,
        scope_key: str,
        snapshot: LLMExecutionSnapshot,
    ) -> LLMExecutionPermit:
        key = str(scope_key)
        with self._lock:
            state = self._entries.get(key)
            if state is None:
                state = _ScopeState(snapshot, lock=self._lock)
                self._entries[key] = state
            state.queued += 1
            try:
                while True:
                    now = self._clock()
                    cooldown_remaining = max(0.0, state.cooldown_until - now)
                    if (
                        cooldown_remaining <= 0
                        and state.active < state.effective_max_in_flight
                    ):
                        state.active += 1
                        state.peak_active = max(state.peak_active, state.active)
                        state.physical_request_count += 1
                        break
                    state.condition.wait(
                        timeout=(
                            min(cooldown_remaining, 0.25)
                            if cooldown_remaining > 0
                            else 0.25
                        )
                    )
            finally:
                state.queued -= 1
        return LLMExecutionPermit(self, key)

    def status(
        self,
        scope_key: str,
        snapshot: LLMExecutionSnapshot | None = None,
    ) -> dict[str, int | str]:
        key = str(scope_key)
        with self._lock:
            state = self._entries.get(key)
            if state is None:
                resolved = snapshot or execution_snapshot_from_profile({})
                state = _ScopeState(resolved, lock=self._lock)
                self._entries[key] = state
            return {
                "mode": state.snapshot.mode,
                "configured_max_in_flight": state.snapshot.max_in_flight,
                "effective_max_in_flight": state.effective_max_in_flight,
                "requests_per_minute": state.snapshot.requests_per_minute,
                "active": state.active,
                "queued": state.queued,
                "peak_active": state.peak_active,
                "physical_request_count": state.physical_request_count,
                "limiting_reason": state.limiting_reason,
            }

    def _apply_snapshot(
        self,
        state: _ScopeState,
        snapshot: LLMExecutionSnapshot,
    ) -> None:
        if state.snapshot == snapshot:
            return
        state.snapshot = snapshot
        state.effective_max_in_flight = snapshot.initial_max_in_flight
        state.successes_since_adjustment = 0
        state.cooldown_until = 0.0
        state.limiting_reason = "configured"
        state.condition.notify_all()

    def _complete(
        self,
        scope_key: str,
        *,
        error_category: LLMErrorCategory | None,
        retry_after_seconds: float,
    ) -> None:
        with self._lock:
            state = self._entries.get(str(scope_key))
            if state is None:
                return
            state.active = max(0, state.active - 1)
            if error_category in _OVERLOAD_CATEGORIES:
                state.effective_max_in_flight = max(
                    1,
                    state.effective_max_in_flight // 2,
                )
                state.successes_since_adjustment = 0
                state.limiting_reason = "provider_overload"
                delay = _finite_nonnegative(retry_after_seconds)
                if delay > 0:
                    state.cooldown_until = max(
                        state.cooldown_until,
                        self._clock() + delay,
                    )
            elif error_category is None:
                state.successes_since_adjustment += 1
                if (
                    state.effective_max_in_flight
                    < state.snapshot.max_in_flight
                    and state.successes_since_adjustment
                    >= _RECOVERY_SUCCESS_WINDOW
                ):
                    state.effective_max_in_flight += 1
                    state.successes_since_adjustment = 0
                    state.limiting_reason = (
                        "configured"
                        if state.effective_max_in_flight
                        >= state.snapshot.max_in_flight
                        else "recovering"
                    )
            state.condition.notify_all()


_DEFAULT_EXECUTION_GOVERNORS = LLMExecutionGovernorRegistry()


def get_default_execution_governors() -> LLMExecutionGovernorRegistry:
    return _DEFAULT_EXECUTION_GOVERNORS


def _strict_int(
    value: object,
    field_name: str,
    minimum: int,
    maximum: int,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise LLMExecutionSettingsError(f"{field_name} must be an integer")
    if not minimum <= value <= maximum:
        raise LLMExecutionSettingsError(
            f"{field_name} must be between {minimum} and {maximum}"
        )
    return value


def _finite_nonnegative(value: object) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(parsed) or parsed < 0:
        return 0.0
    return parsed
