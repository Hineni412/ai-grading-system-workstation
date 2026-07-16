from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


_REGISTRY_GUARD = threading.Lock()
_SESSION_LOCKS: dict[tuple[Path, int], threading.Lock] = {}


def _lock_for(lock_key: tuple[Path, int]) -> threading.Lock:
    with _REGISTRY_GUARD:
        return _SESSION_LOCKS.setdefault(lock_key, threading.Lock())


@contextmanager
def session_config_lock(
    upload_config_dir: Path,
    session_id: int,
) -> Iterator[None]:
    lock_key = (
        Path(upload_config_dir).resolve(strict=False),
        int(session_id),
    )
    with _lock_for(lock_key):
        yield
