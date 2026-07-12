from __future__ import annotations

import threading
from contextlib import contextmanager
from collections.abc import Iterator, Sequence


_REGISTRY_GUARD = threading.Lock()
_LOCKS: dict[str, threading.Lock] = {}


@contextmanager
def keyed_execution_locks(keys: Sequence[str]) -> Iterator[None]:
    normalized = sorted({str(key) for key in keys if str(key)})
    with _REGISTRY_GUARD:
        locks = [_LOCKS.setdefault(key, threading.Lock()) for key in normalized]
    for lock in locks:
        lock.acquire()
    try:
        yield
    finally:
        for lock in reversed(locks):
            lock.release()
