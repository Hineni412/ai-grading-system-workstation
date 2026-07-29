from __future__ import annotations

import threading

from request_pacer import RequestPacer

from .policy import LLMRequestKind


class LLMPacerRegistry:
    def __init__(self, *, factory=RequestPacer) -> None:
        self._factory = factory
        self._entries = {}
        self._configured_keys: set[str] = set()
        self._lock = threading.Lock()

    def configure(self, config_key: str, requests_per_minute: int) -> None:
        key = str(config_key)
        rpm = int(requests_per_minute)
        with self._lock:
            self._apply_rpm(key, rpm)
            self._configured_keys.add(key)

    def acquire(
        self,
        config_key: str,
        kind: LLMRequestKind,
        requests_per_minute: int,
    ) -> None:
        # All request kinds using one configured profile share one RPM budget.
        # ``kind`` remains in the compatibility interface for existing callers.
        LLMRequestKind(kind)
        key = str(config_key)
        rpm = int(requests_per_minute)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self._apply_rpm(key, rpm)
            elif key not in self._configured_keys and rpm != entry[0]:
                self._apply_rpm(key, rpm)
            pacer = self._entries[key][1]
        pacer.acquire()

    def _apply_rpm(self, key: str, rpm: int) -> None:
        entry = self._entries.get(key)
        if entry is None:
            self._entries[key] = (rpm, self._factory(rpm))
        elif rpm < entry[0]:
            entry[1].tighten(rpm)
            self._entries[key] = (rpm, entry[1])
        elif rpm > entry[0]:
            self._entries[key] = (rpm, self._factory(rpm))
