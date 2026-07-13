from __future__ import annotations

import threading
import time
from collections.abc import Callable


class RequestPacer:
    """Reserve evenly spaced request start slots without initial burst credit."""

    def __init__(
        self,
        requests_per_minute: int,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._interval = 60.0 / max(1, int(requests_per_minute))
        self._clock = clock
        self._sleeper = sleeper
        self._lock = threading.Lock()
        self._next_slot = self._clock()

    def tighten(self, requests_per_minute: int) -> None:
        stricter_interval = 60.0 / max(1, int(requests_per_minute))
        with self._lock:
            if stricter_interval <= self._interval:
                return
            self._next_slot += stricter_interval - self._interval
            self._interval = stricter_interval

    def acquire(self) -> None:
        with self._lock:
            now = self._clock()
            slot = max(now, self._next_slot)
            self._next_slot = slot + self._interval
        delay = slot - now
        if delay > 0:
            self._sleeper(delay)
