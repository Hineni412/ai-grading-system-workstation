from __future__ import annotations

import threading

from request_pacer import RequestPacer

from .policy import LLMRequestKind


class LLMPacerRegistry:
    def __init__(self, *, factory=RequestPacer) -> None:
        self._factory = factory
        self._entries = {}
        self._lock = threading.Lock()

    def acquire(
        self,
        config_key: str,
        kind: LLMRequestKind,
        requests_per_minute: int,
    ) -> None:
        key = (str(config_key), LLMRequestKind(kind))
        rpm = int(requests_per_minute)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                entry = (rpm, self._factory(rpm))
                self._entries[key] = entry
            elif rpm < entry[0]:
                entry[1].tighten(rpm)
                entry = (rpm, entry[1])
                self._entries[key] = entry
            pacer = entry[1]
        pacer.acquire()
