from __future__ import annotations

import errno
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


_WINDOWS_LOCK_TIMEOUT_SECONDS = 10.0
_WINDOWS_LOCK_RETRY_SECONDS = 0.01
_WINDOWS_LOCK_CONTENTION_ERRNOS = {errno.EACCES, errno.EAGAIN, errno.EDEADLK}
_WINDOWS_LOCK_CONTENTION_WINERRORS = {32, 33}
_REGISTRY_PID = os.getpid()
_LOCKS_GUARD = threading.Lock()
_SESSION_LOCKS: WeakValueDictionary[Path, AnswerRegionSessionLock] = WeakValueDictionary()


class AnswerRegionSessionLock:
    def __init__(self, session_dir: Path) -> None:
        self.session_dir = session_dir.resolve(strict=False)
        self.lock_path = self.session_dir / ".answer_region_session.lock"
        self._thread_lock = threading.RLock()
        self._depth = 0
        self._lock_file: object | None = None
        self._owner_thread_id: int | None = None
        self._pid = os.getpid()

    def __enter__(self) -> AnswerRegionSessionLock:
        self._reset_if_process_changed()
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
            self._owner_thread_id = threading.get_ident()
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
        if self._pid != os.getpid():
            self._reset_if_process_changed()
            return
        # A child may unwind a parent-entered context after its registry reset.
        if self._depth <= 0:
            return
        try:
            self._depth -= 1
            if self._depth == 0:
                self._owner_thread_id = None
                lock_file = self._lock_file
                self._lock_file = None
                if lock_file is not None:
                    try:
                        _release_file_lock(lock_file)
                    finally:
                        lock_file.close()
        finally:
            self._thread_lock.release()

    def assert_held_by_current_thread(self) -> None:
        self._reset_if_process_changed()
        if self._depth <= 0 or self._owner_thread_id != threading.get_ident():
            raise AssertionError("answer region session lock must be held by current thread")

    def _reset_if_process_changed(self) -> None:
        current_pid = os.getpid()
        if self._pid == current_pid:
            return
        inherited_file = self._lock_file
        self._thread_lock = threading.RLock()
        self._depth = 0
        self._lock_file = None
        self._owner_thread_id = None
        self._pid = current_pid
        if inherited_file is not None:
            try:
                inherited_file.close()
            except OSError:
                pass


def get_answer_region_session_lock(session_dir: Path) -> AnswerRegionSessionLock:
    _reset_registry_if_process_changed()
    resolved_dir = Path(session_dir).resolve(strict=False)
    with _LOCKS_GUARD:
        lock = _SESSION_LOCKS.get(resolved_dir)
        if lock is None:
            lock = AnswerRegionSessionLock(resolved_dir)
            _SESSION_LOCKS[resolved_dir] = lock
        return lock


def _reset_registry_if_process_changed() -> None:
    global _LOCKS_GUARD, _REGISTRY_PID, _SESSION_LOCKS
    current_pid = os.getpid()
    if _REGISTRY_PID == current_pid:
        return
    for lock in list(_SESSION_LOCKS.values()):
        lock._reset_if_process_changed()
    _LOCKS_GUARD = threading.Lock()
    _SESSION_LOCKS = WeakValueDictionary()
    _REGISTRY_PID = current_pid


def _ensure_lock_byte(lock_file: object) -> None:
    lock_file.seek(0, os.SEEK_END)
    if lock_file.tell() == 0:
        lock_file.write(b"\0")
        lock_file.flush()
        os.fsync(lock_file.fileno())
    lock_file.seek(0)


def _acquire_file_lock(lock_file: object) -> None:
    if os.name == "nt":
        _acquire_windows_file_lock(lock_file)
    else:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)


def _acquire_windows_file_lock(lock_file: object) -> None:
    deadline = time.monotonic() + _WINDOWS_LOCK_TIMEOUT_SECONDS
    while True:
        lock_file.seek(0)
        try:
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            return
        except OSError as exc:
            if not _is_windows_lock_contention(exc):
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("timed out acquiring answer region session lock") from exc
            time.sleep(min(_WINDOWS_LOCK_RETRY_SECONDS, remaining))


def _is_windows_lock_contention(exc: OSError) -> bool:
    return (
        exc.errno in _WINDOWS_LOCK_CONTENTION_ERRNOS
        or getattr(exc, "winerror", None) in _WINDOWS_LOCK_CONTENTION_WINERRORS
    )


def _release_file_lock(lock_file: object) -> None:
    lock_file.seek(0)
    if os.name == "nt":
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
