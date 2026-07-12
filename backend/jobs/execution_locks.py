from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass
from collections.abc import Callable, Iterator, Sequence


_REGISTRY_GUARD = threading.Lock()


@dataclass
class _LockEntry:
    lock: threading.Lock
    users: int = 0


_LOCKS: dict[str, _LockEntry] = {}


@contextmanager
def keyed_execution_locks(
    keys: Sequence[str],
    *,
    cancel_check: Callable[[], None] | None = None,
    poll_interval: float = 0.05,
) -> Iterator[None]:
    normalized = sorted({str(key) for key in keys if str(key)})
    with _REGISTRY_GUARD:
        entries: list[tuple[str, _LockEntry]] = []
        for key in normalized:
            entry = _LOCKS.setdefault(key, _LockEntry(threading.Lock()))
            entry.users += 1
            entries.append((key, entry))
    acquired: list[_LockEntry] = []
    try:
        for _key, entry in entries:
            while True:
                if cancel_check is not None:
                    cancel_check()
                if entry.lock.acquire(timeout=max(0.01, float(poll_interval))):
                    acquired.append(entry)
                    break
        if cancel_check is not None:
            cancel_check()
        yield
    finally:
        for entry in reversed(acquired):
            entry.lock.release()
        with _REGISTRY_GUARD:
            for key, entry in entries:
                entry.users -= 1
                if entry.users == 0 and _LOCKS.get(key) is entry:
                    _LOCKS.pop(key, None)
