"""Bounded pickle-bytes result caches with single-flight computation.

Values are pickled on store and unpickled on every hit, so callers can never
mutate a shared cached object in either direction.  While one caller computes
a key, concurrent callers wait on the flight and share the result instead of
duplicating the work (used by the overview/graph/assistant caches and the
background refresher).
"""

from __future__ import annotations

import pickle
import threading
from collections import OrderedDict
from collections.abc import Callable
from typing import Any


class _Flight:
    __slots__ = ("event", "error", "payload")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.error: BaseException | None = None
        self.payload: bytes | None = None


class ResultCache:
    def __init__(self, limit: int, *, max_bytes: int | None = None) -> None:
        self._limit = max(1, int(limit))
        self._max_bytes = None if max_bytes is None else max(0, int(max_bytes))
        self._bytes = 0
        self._entries: OrderedDict[Any, bytes] = OrderedDict()
        self._flights: dict[Any, _Flight] = {}
        self._lock = threading.Lock()

    def get_or_compute(self, key: Any, compute: Callable[[], Any]) -> Any:
        while True:
            with self._lock:
                cached = self._entries.get(key)
                if cached is not None:
                    self._entries.move_to_end(key)
                else:
                    flight = self._flights.get(key)
                    owner = flight is None
                    if owner:
                        flight = _Flight()
                        self._flights[key] = flight
            if cached is not None:
                return pickle.loads(cached)
            if not owner:
                flight.event.wait()
                if flight.error is not None:
                    raise flight.error
                # An oversized or subsequently evicted value still serves the
                # callers already waiting for this flight, each independently.
                return pickle.loads(flight.payload)
            try:
                result = compute()
                payload = pickle.dumps(result, pickle.HIGHEST_PROTOCOL)
            except BaseException as exc:
                with self._lock:
                    if self._flights.get(key) is flight:
                        del self._flights[key]
                        flight.error = exc
                        flight.event.set()
                raise
            with self._lock:
                self._store(key, payload)
                if self._flights.get(key) is flight:
                    del self._flights[key]
                    flight.payload = payload
                    flight.event.set()
            return result

    def put(self, key: Any, value: Any) -> None:
        payload = pickle.dumps(value, pickle.HIGHEST_PROTOCOL)
        with self._lock:
            self._store(key, payload)

    def _store(self, key: Any, payload: bytes) -> None:
        """Called under the lock; the budget measures retained pickle bytes."""
        previous = self._entries.pop(key, None)
        if previous is not None:
            self._bytes -= len(previous)
        if self._max_bytes is not None and len(payload) > self._max_bytes:
            return
        self._entries[key] = payload
        self._bytes += len(payload)
        while (len(self._entries) > self._limit
               or self._max_bytes is not None and self._bytes > self._max_bytes):
            self._bytes -= len(self._entries.popitem(last=False)[1])

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._bytes = 0
