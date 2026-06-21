from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Lock

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
