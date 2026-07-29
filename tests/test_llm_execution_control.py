from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from backend.llm.execution import (
    LLMExecutionGovernorRegistry,
    execution_scope_key,
    execution_snapshot_from_profile,
)
from backend.llm.errors import LLMErrorCategory


def test_execution_modes_resolve_to_one_canonical_snapshot() -> None:
    automatic = execution_snapshot_from_profile(
        {"name": "校内模型", "request_speed_mode": "automatic"}
    )
    conservative = execution_snapshot_from_profile(
        {"name": "校内模型", "request_speed_mode": "conservative"}
    )
    custom = execution_snapshot_from_profile(
        {
            "name": "校内模型",
            "request_speed_mode": "custom",
            "max_concurrent_requests": 37,
            "requests_per_minute": 10_000,
        }
    )

    assert automatic.as_public_dict() == {
        "mode": "automatic",
        "initial_max_in_flight": 6,
        "max_in_flight": 20,
        "requests_per_minute": 1000,
    }
    assert conservative.as_public_dict() == {
        "mode": "conservative",
        "initial_max_in_flight": 1,
        "max_in_flight": 1,
        "requests_per_minute": 60,
    }
    assert custom.as_public_dict() == {
        "mode": "custom",
        "initial_max_in_flight": 37,
        "max_in_flight": 37,
        "requests_per_minute": 10_000,
    }


def test_execution_scope_is_stable_per_named_profile_without_exposing_name() -> None:
    first = execution_scope_key({"name": "校内模型"})
    second = execution_scope_key({"name": "校内模型"})
    other = execution_scope_key({"name": "备用模型"})

    assert first == second
    assert first != other
    assert "校内模型" not in first


def test_governor_caps_all_callers_sharing_one_profile_scope() -> None:
    snapshot = execution_snapshot_from_profile(
        {
            "request_speed_mode": "custom",
            "max_concurrent_requests": 3,
            "requests_per_minute": 10_000,
        }
    )
    governor = LLMExecutionGovernorRegistry()
    release = threading.Event()
    entered = threading.Event()
    lock = threading.Lock()
    active = 0
    peak = 0

    def invoke() -> None:
        nonlocal active, peak
        permit = governor.acquire("shared-profile", snapshot)
        with lock:
            active += 1
            peak = max(peak, active)
            if peak == 3:
                entered.set()
        release.wait(timeout=2)
        with lock:
            active -= 1
        permit.complete()

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(invoke) for _ in range(8)]
        assert entered.wait(timeout=2)
        status = governor.status("shared-profile")
        assert status["active"] == 3
        assert status["queued"] == 5
        release.set()
        for future in futures:
            future.result(timeout=2)

    assert peak == 3
    assert governor.status("shared-profile")["active"] == 0


def test_overload_reduces_future_admission_without_retrying_failed_call() -> None:
    snapshot = execution_snapshot_from_profile(
        {
            "request_speed_mode": "custom",
            "max_concurrent_requests": 12,
            "requests_per_minute": 1000,
        }
    )
    governor = LLMExecutionGovernorRegistry()

    permit = governor.acquire("shared-profile", snapshot)
    permit.complete(
        error_category=LLMErrorCategory.RATE_LIMIT,
        retry_after_seconds=0,
    )

    status = governor.status("shared-profile")
    assert status["effective_max_in_flight"] == 6
    assert status["limiting_reason"] == "provider_overload"
    assert status["physical_request_count"] == 1


def test_explicit_saved_configuration_is_not_reverted_by_an_older_caller() -> None:
    old_snapshot = execution_snapshot_from_profile(
        {
            "request_speed_mode": "custom",
            "max_concurrent_requests": 4,
            "requests_per_minute": 60,
        }
    )
    saved_snapshot = execution_snapshot_from_profile(
        {
            "request_speed_mode": "custom",
            "max_concurrent_requests": 12,
            "requests_per_minute": 600,
        }
    )
    governor = LLMExecutionGovernorRegistry()
    governor.configure("shared-profile", saved_snapshot)

    permit = governor.acquire("shared-profile", old_snapshot)
    permit.complete()

    status = governor.status("shared-profile")
    assert status["configured_max_in_flight"] == 12
    assert status["requests_per_minute"] == 600
