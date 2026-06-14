from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from types import TracebackType
from weakref import WeakValueDictionary

if os.name == "nt":
    import msvcrt
else:
    import fcntl


_LOCKS_GUARD = threading.Lock()
_SESSION_LOCKS: WeakValueDictionary[Path, AnswerRegionSessionLock] = WeakValueDictionary()


class AnswerRegionSessionLock:
    def __init__(self, session_dir: Path) -> None:
        self.session_dir = session_dir.resolve(strict=False)
        self.lock_path = self.session_dir / ".answer_region_session.lock"
        self._thread_lock = threading.RLock()
        self._depth = 0
        self._lock_file: object | None = None

    def __enter__(self) -> AnswerRegionSessionLock:
        self._thread_lock.acquire()
        try:
            if self._depth == 0:
                self.session_dir.mkdir(parents=True, exist_ok=True)
                lock_file = self.lock_path.open("a+b")
                try:
                    _ensure_lock_byte(lock_file)
                    _acquire_file_lock(lock_file)
                except BaseException:
                    lock_file.close()
                    raise
                self._lock_file = lock_file
            self._depth += 1
            return self
        except BaseException:
            self._thread_lock.release()
            raise

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            self._depth -= 1
            if self._depth == 0:
                lock_file = self._lock_file
                self._lock_file = None
                if lock_file is not None:
                    try:
                        _release_file_lock(lock_file)
                    finally:
                        lock_file.close()
        finally:
            self._thread_lock.release()


def get_answer_region_session_lock(session_dir: Path) -> AnswerRegionSessionLock:
    resolved_dir = Path(session_dir).resolve(strict=False)
    with _LOCKS_GUARD:
        lock = _SESSION_LOCKS.get(resolved_dir)
        if lock is None:
            lock = AnswerRegionSessionLock(resolved_dir)
            _SESSION_LOCKS[resolved_dir] = lock
        return lock


def _ensure_lock_byte(lock_file: object) -> None:
    lock_file.seek(0, os.SEEK_END)
    if lock_file.tell() == 0:
        lock_file.write(b"\0")
        lock_file.flush()
        os.fsync(lock_file.fileno())
    lock_file.seek(0)


def _acquire_file_lock(lock_file: object) -> None:
    if os.name == "nt":
        while True:
            lock_file.seek(0)
            try:
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                time.sleep(0.01)
    else:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)


def _release_file_lock(lock_file: object) -> None:
    lock_file.seek(0)
    if os.name == "nt":
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
