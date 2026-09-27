from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from backend.llm.execution import (
    LLMExecutionGovernorRegistry,
    execution_snapshot_from_profile,
)
from backend.llm.errors import LLMErrorCategory


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
