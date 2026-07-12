from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path


class OpsLockBusy(RuntimeError):
    pass


_REGISTRY_GUARD = threading.Lock()
_PROCESS_LOCKS: dict[str, threading.Lock] = {}


class OpsOperationLock:
    def __init__(
        self,
        state_root: Path,
        *,
        timeout_seconds: float = 30.0,
        retry_seconds: float = 0.05,
    ) -> None:
        self.state_root = Path(state_root)
        self.lock_path = self.state_root / "ops-write.lock"
        self.timeout_seconds = float(timeout_seconds)
        self.retry_seconds = float(retry_seconds)
        key = str(self.state_root.resolve(strict=False)).casefold()
        with _REGISTRY_GUARD:
            self._process_lock = _PROCESS_LOCKS.setdefault(key, threading.Lock())

    @contextmanager
    def acquire(
        self,
        *,
        cancel_check: Callable[[], None] | None = None,
    ) -> Iterator[None]:
        deadline = time.monotonic() + self.timeout_seconds
        self._acquire_process(deadline, cancel_check)
        try:
            self.state_root.mkdir(parents=True, exist_ok=True)
            with self.lock_path.open("a+b") as handle:
                _ensure_lock_byte(handle)
                self._acquire_file(handle, deadline, cancel_check)
                try:
                    yield
                finally:
                    _release_file(handle)
        finally:
            self._process_lock.release()

    def _acquire_process(
        self,
        deadline: float,
        cancel_check: Callable[[], None] | None,
    ) -> None:
        while not self._process_lock.acquire(blocking=False):
            if cancel_check is not None:
                cancel_check()
            if time.monotonic() >= deadline:
                raise OpsLockBusy("protected operation lock is busy")
            time.sleep(self.retry_seconds)

    def _acquire_file(
        self,
        handle: object,
        deadline: float,
        cancel_check: Callable[[], None] | None,
    ) -> None:
        while True:
            try:
                _lock_file_nonblocking(handle)
                return
            except OSError as exc:
                if cancel_check is not None:
                    cancel_check()
                if time.monotonic() >= deadline:
                    raise OpsLockBusy("protected operation lock is busy") from exc
                time.sleep(self.retry_seconds)


def _ensure_lock_byte(handle: object) -> None:
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
        os.fsync(handle.fileno())
    handle.seek(0)


def _lock_file_nonblocking(handle: object) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _release_file(handle: object) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


__all__ = ["OpsLockBusy", "OpsOperationLock"]
