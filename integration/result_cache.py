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
    __slots__ = ("event", "error")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.error: BaseException | None = None


class ResultCache:
    def __init__(self, limit: int) -> None:
        self._limit = max(1, int(limit))
        self._entries: OrderedDict[Any, bytes] = OrderedDict()
        self._flights: dict[Any, _Flight] = {}
        self._lock = threading.Lock()

    def get_or_compute(self, key: Any, compute: Callable[[], Any]) -> Any:
        while True:
            with self._lock:
                cached = self._entries.get(key)
                if cached is not None:
                    self._entries.move_to_end(key)
                    return pickle.loads(cached)
                flight = self._flights.get(key)
                owner = flight is None
                if owner:
                    flight = _Flight()
                    self._flights[key] = flight
            if not owner:
                flight.event.wait()
                if flight.error is not None:
                    raise flight.error
                continue
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
                self._entries[key] = payload
                self._entries.move_to_end(key)
                while len(self._entries) > self._limit:
                    self._entries.popitem(last=False)
                if self._flights.get(key) is flight:
                    del self._flights[key]
                    flight.event.set()
            return result

    def put(self, key: Any, value: Any) -> None:
        payload = pickle.dumps(value, pickle.HIGHEST_PROTOCOL)
        with self._lock:
            self._entries[key] = payload
            self._entries.move_to_end(key)
            while len(self._entries) > self._limit:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
