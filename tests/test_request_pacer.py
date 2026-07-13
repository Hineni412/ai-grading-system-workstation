from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock

from backend.llm.pacing import LLMPacerRegistry
from backend.llm.policy import LLMRequestKind
from request_pacer import RequestPacer


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0
        self.sleep_calls: list[float] = []

    def now(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.sleep_calls.append(seconds)
        self.value += seconds


class FakePacer:
    def acquire(self) -> None:
        pass


def test_first_request_is_immediate_and_following_requests_are_evenly_spaced() -> None:
    clock = FakeClock()
    pacer = RequestPacer(60, clock=clock.now, sleeper=clock.sleep)

    pacer.acquire()
    pacer.acquire()
    pacer.acquire()

    assert clock.sleep_calls == [1.0, 1.0]
    assert clock.now() == 2.0


def test_concurrent_callers_reserve_distinct_future_slots() -> None:
    sleep_calls: list[float] = []
    sleep_lock = Lock()

    def record_sleep(seconds: float) -> None:
        with sleep_lock:
            sleep_calls.append(seconds)

    pacer = RequestPacer(60, clock=lambda: 0.0, sleeper=record_sleep)
    pacer.acquire()

    with ThreadPoolExecutor(max_workers=3) as executor:
        list(executor.map(lambda _: pacer.acquire(), range(3)))

    assert sorted(sleep_calls) == [1.0, 2.0, 3.0]


def test_registry_reuses_pacer_for_same_config_and_kind() -> None:
    created: list[int] = []
    registry = LLMPacerRegistry(
        factory=lambda rpm: created.append(rpm) or FakePacer()
    )

    registry.acquire("profile-a", LLMRequestKind.GRADING, 60)
    registry.acquire("profile-a", LLMRequestKind.GRADING, 60)

    assert created == [60]


def test_policy_change_replaces_keyed_pacer() -> None:
    created: list[int] = []
    registry = LLMPacerRegistry(
        factory=lambda rpm: created.append(rpm) or FakePacer()
    )

    registry.acquire("profile-a", LLMRequestKind.GRADING, 60)
    registry.acquire("profile-a", LLMRequestKind.GRADING, 120)

    assert created == [60, 120]


def test_registry_creates_one_pacer_for_simultaneous_first_access() -> None:
    created: list[int] = []
    created_lock = Lock()
    ready = Barrier(8)

    def factory(rpm: int) -> FakePacer:
        with created_lock:
            created.append(rpm)
        return FakePacer()

    registry = LLMPacerRegistry(factory=factory)

    def acquire() -> None:
        ready.wait()
        registry.acquire("profile-a", LLMRequestKind.GRADING, 60)

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda _: acquire(), range(8)))

    assert created == [60]
