from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from backend.ops.models import OpsInternalPlan, OpsOperation
from backend.ops.plan_store import (
    OpsConfirmationExpired,
    OpsConfirmationInvalid,
    OpsConfirmationUsed,
    OpsPlanStore,
)
from path_manager import PathManager


@dataclass
class _FakeClock:
    monotonic_value: float
    utc_value: datetime

    def monotonic(self) -> float:
        return self.monotonic_value

    def utcnow(self) -> datetime:
        return self.utc_value

    def advance(self, seconds: float) -> None:
        self.monotonic_value += seconds
        self.utc_value += timedelta(seconds=seconds)


def _plan(*, created_monotonic: float = 10.0) -> OpsInternalPlan:
    return OpsInternalPlan(
        operation=OpsOperation.BACKUP,
        parameters={"reason": "manual"},
        resource_fingerprint="f" * 64,
        summary={"file_count": 2, "total_size_bytes": 128},
        created_monotonic=created_monotonic,
    )


def test_ops_state_dir_defaults_outside_data_root(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.delenv("AI_GRADING_OPS_STATE_DIR", raising=False)

    paths = PathManager()

    expected = (tmp_path / "local" / "AIGradingSystem" / "ops").resolve()
    assert paths.ops_state_dir == expected
    assert paths.ops_state_dir != paths.data_root
    assert not expected.exists()


def test_ops_state_dir_honors_explicit_test_override(monkeypatch, tmp_path: Path) -> None:
    override = tmp_path / "isolated-ops-state"
    monkeypatch.setenv("AI_GRADING_OPS_STATE_DIR", str(override))

    paths = PathManager()

    assert paths.ops_state_dir == override.resolve()
    assert not override.exists()


def test_plan_token_is_single_use_and_expires_after_300_seconds() -> None:
    clock = _FakeClock(10.0, datetime(2026, 7, 12, tzinfo=UTC))
    store = OpsPlanStore(clock=clock, token_factory=lambda: "a" * 64)

    token, expires_at = store.issue(_plan())

    assert expires_at == clock.utcnow() + timedelta(seconds=300)
    assert store.consume(token).operation is OpsOperation.BACKUP
    with pytest.raises(OpsConfirmationUsed):
        store.consume(token)


def test_plan_token_expires_at_exact_300_second_boundary() -> None:
    clock = _FakeClock(10.0, datetime(2026, 7, 12, tzinfo=UTC))
    store = OpsPlanStore(clock=clock, token_factory=lambda: "b" * 64)
    token, _expires_at = store.issue(_plan())

    clock.advance(300)

    with pytest.raises(OpsConfirmationExpired):
        store.consume(token)
    with pytest.raises(OpsConfirmationUsed):
        store.consume(token)


@pytest.mark.parametrize("token", ["", "unknown-token"])
def test_unknown_or_blank_confirmation_token_is_rejected(token: str) -> None:
    store = OpsPlanStore(
        clock=_FakeClock(10.0, datetime(2026, 7, 12, tzinfo=UTC)),
        token_factory=lambda: "c" * 64,
    )

    with pytest.raises(OpsConfirmationInvalid):
        store.consume(token)


def test_confirmation_token_has_exactly_one_concurrent_consumer() -> None:
    store = OpsPlanStore(
        clock=_FakeClock(10.0, datetime(2026, 7, 12, tzinfo=UTC)),
        token_factory=lambda: "d" * 64,
    )
    token, _expires_at = store.issue(_plan())
    barrier = threading.Barrier(3)
    outcomes: list[str] = []
    guard = threading.Lock()

    def consume() -> None:
        barrier.wait()
        try:
            store.consume(token)
            outcome = "consumed"
        except OpsConfirmationUsed:
            outcome = "used"
        with guard:
            outcomes.append(outcome)

    threads = [threading.Thread(target=consume) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=2)

    assert sorted(outcomes) == ["consumed", "used"]
    assert token not in repr(store)
