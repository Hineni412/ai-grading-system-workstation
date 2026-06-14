from __future__ import annotations

import errno
import multiprocessing
from pathlib import Path
from queue import Empty
from types import SimpleNamespace

import pytest

import answer_region_session_lock as lock_module
from answer_region_session_lock import AnswerRegionSessionLock, get_answer_region_session_lock


def _acquire_inherited_lock(lock: AnswerRegionSessionLock, events: object) -> None:
    events.put("started")
    with lock:
        events.put("acquired")


def test_failed_os_lock_acquisition_closes_opened_lock_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class TrackingFile:
        closed = False

        def close(self) -> None:
            self.closed = True

    tracking_file = TrackingFile()
    lock = AnswerRegionSessionLock(tmp_path / "session")
    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: tracking_file)
    monkeypatch.setattr(lock_module, "_ensure_lock_byte", lambda _file: None)

    def fail_acquire(_file: object) -> None:
        raise OSError("lock unavailable")

    monkeypatch.setattr(lock_module, "_acquire_file_lock", fail_acquire)

    with pytest.raises(OSError, match="lock unavailable"):
        with lock:
            pass

    assert tracking_file.closed is True


def test_process_identity_change_resets_local_state_and_reacquires_file_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class TrackingFile:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    inherited_file = TrackingFile()
    replacement_file = TrackingFile()
    acquired: list[object] = []
    released: list[object] = []
    lock = AnswerRegionSessionLock(tmp_path / "session")
    lock._depth = 1
    lock._lock_file = inherited_file
    lock._pid = 100
    monkeypatch.setattr(lock_module.os, "getpid", lambda: 200)
    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: replacement_file)
    monkeypatch.setattr(lock_module, "_ensure_lock_byte", lambda _file: None)
    monkeypatch.setattr(lock_module, "_acquire_file_lock", acquired.append)
    monkeypatch.setattr(lock_module, "_release_file_lock", released.append)

    with lock:
        assert lock._lock_file is replacement_file

    assert inherited_file.closed is True
    assert acquired == [replacement_file]
    assert released == [replacement_file]


def test_lock_registry_resets_across_process_identity_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = get_answer_region_session_lock(tmp_path / "session")
    child_pid = lock_module._REGISTRY_PID + 1
    monkeypatch.setattr(lock_module.os, "getpid", lambda: child_pid)

    second = get_answer_region_session_lock(tmp_path / "session")

    assert second is not first
    assert second._pid == child_pid
    assert lock_module._REGISTRY_PID == child_pid


@pytest.mark.skipif(
    "fork" not in multiprocessing.get_all_start_methods(),
    reason="requires os.fork",
)
def test_inherited_held_lock_blocks_child_until_parent_releases(tmp_path: Path) -> None:
    context = multiprocessing.get_context("fork")
    events = context.Queue()
    lock = AnswerRegionSessionLock(tmp_path / "session")
    process = context.Process(target=_acquire_inherited_lock, args=(lock, events))

    with lock:
        process.start()
        assert events.get(timeout=5) == "started"
        with pytest.raises(Empty):
            events.get(timeout=0.3)

    assert events.get(timeout=5) == "acquired"
    process.join(5)
    assert process.exitcode == 0


def test_windows_lock_retries_recognized_contention_then_acquires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class LockFile:
        def seek(self, _offset: int) -> None:
            pass

        def fileno(self) -> int:
            return 7

    attempts = 0

    def locking(_fd: int, _mode: int, _length: int) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise PermissionError(errno.EACCES, "lock contention")

    monkeypatch.setattr(
        lock_module,
        "msvcrt",
        SimpleNamespace(LK_NBLCK=1, locking=locking),
        raising=False,
    )
    monkeypatch.setattr(lock_module.time, "sleep", lambda _seconds: None)

    lock_module._acquire_windows_file_lock(LockFile())

    assert attempts == 2


def test_windows_permanent_lock_error_propagates_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class LockFile:
        def seek(self, _offset: int) -> None:
            pass

        def fileno(self) -> int:
            return 7

    attempts = 0

    def locking(_fd: int, _mode: int, _length: int) -> None:
        nonlocal attempts
        attempts += 1
        raise OSError(errno.EINVAL, "invalid lock request")

    monkeypatch.setattr(
        lock_module,
        "msvcrt",
        SimpleNamespace(LK_NBLCK=1, locking=locking),
        raising=False,
    )
    monkeypatch.setattr(
        lock_module.time,
        "sleep",
        lambda _seconds: pytest.fail("permanent errors must not be retried"),
    )

    with pytest.raises(OSError, match="invalid lock request"):
        lock_module._acquire_windows_file_lock(LockFile())

    assert attempts == 1


def test_windows_lock_contention_timeout_closes_opened_lock_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class TrackingFile:
        closed = False

        def seek(self, _offset: int) -> None:
            pass

        def fileno(self) -> int:
            return 7

        def close(self) -> None:
            self.closed = True

    tracking_file = TrackingFile()
    attempts = 0

    def locking(_fd: int, _mode: int, _length: int) -> None:
        nonlocal attempts
        attempts += 1
        raise PermissionError(errno.EACCES, "lock contention")

    times = iter((0.0, 0.0, 0.01))
    monkeypatch.setattr(
        lock_module,
        "msvcrt",
        SimpleNamespace(LK_NBLCK=1, locking=locking),
        raising=False,
    )
    monkeypatch.setattr(lock_module, "_WINDOWS_LOCK_TIMEOUT_SECONDS", 0.01, raising=False)
    monkeypatch.setattr(lock_module.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(lock_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: tracking_file)
    monkeypatch.setattr(lock_module, "_ensure_lock_byte", lambda _file: None)
    monkeypatch.setattr(
        lock_module,
        "_acquire_file_lock",
        lambda lock_file: lock_module._acquire_windows_file_lock(lock_file),
    )
    lock = AnswerRegionSessionLock(tmp_path / "session")

    with pytest.raises(TimeoutError, match="timed out"):
        with lock:
            pass

    assert attempts == 2
    assert tracking_file.closed is True
