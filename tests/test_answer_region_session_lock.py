from __future__ import annotations

import errno
import multiprocessing
import os
from pathlib import Path
from queue import Empty
import select
import signal
import time
from types import SimpleNamespace

import pytest

import answer_region_session_lock as lock_module
from answer_region_session_lock import AnswerRegionSessionLock, get_answer_region_session_lock


def _acquire_inherited_lock(lock: AnswerRegionSessionLock, events: object) -> None:
    events.put("started")
    with lock:
        events.put("acquired")


def _read_pipe_byte(fd: int, timeout: float) -> bytes:
    readable, _, _ = select.select([fd], [], [], timeout)
    if not readable:
        raise TimeoutError("timed out waiting for child process")
    value = os.read(fd, 1)
    if not value:
        raise EOFError("child process closed status pipe")
    return value


def _wait_for_child(pid: int, timeout: float) -> int | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        waited_pid, status = os.waitpid(pid, os.WNOHANG)
        if waited_pid == pid:
            return status
        time.sleep(0.01)
    return None


def _terminate_child(pid: int) -> None:
    status = _wait_for_child(pid, 0.1)
    if status is not None:
        return
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    os.waitpid(pid, 0)


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
    class TrackingFile:
        closed = False

        def close(self) -> None:
            self.closed = True

    first = get_answer_region_session_lock(tmp_path / "session")
    inherited_file = TrackingFile()
    first._thread_lock.acquire()
    first._depth = 1
    first._lock_file = inherited_file
    first._owner_thread_id = 123
    child_pid = lock_module._REGISTRY_PID + 1
    monkeypatch.setattr(lock_module.os, "getpid", lambda: child_pid)

    second = get_answer_region_session_lock(tmp_path / "session")

    assert second is not first
    assert inherited_file.closed is True
    assert first._depth == 0
    assert first._lock_file is None
    assert first._owner_thread_id is None
    first.__exit__(None, None, None)
    assert first._depth == 0
    with first:
        first.assert_held_by_current_thread()
    assert second._pid == child_pid
    assert lock_module._REGISTRY_PID == child_pid


@pytest.mark.skipif(
    os.name == "nt" or not hasattr(os, "fork"),
    reason="requires POSIX os.fork",
)
def test_fresh_registry_lock_after_fork_does_not_keep_its_inherited_lock(
    tmp_path: Path,
) -> None:
    inherited_lock = get_answer_region_session_lock(tmp_path / "session")
    status_read, status_write = os.pipe()
    child_pid: int | None = None

    try:
        with inherited_lock:
            child_pid = os.fork()
            if child_pid == 0:
                os.close(status_read)
                exit_code = 0
                try:
                    fresh_lock = get_answer_region_session_lock(tmp_path / "session")
                    assert fresh_lock is not inherited_lock
                    assert inherited_lock._depth == 0
                    assert inherited_lock._lock_file is None
                    os.write(status_write, b"S")
                    with fresh_lock:
                        os.write(status_write, b"A")
                    with inherited_lock:
                        os.write(status_write, b"R")
                except BaseException:
                    exit_code = 1
                finally:
                    os.close(status_write)
                    os._exit(exit_code)

            os.close(status_write)
            status_write = -1
            assert _read_pipe_byte(status_read, 5.0) == b"S"
            with pytest.raises(TimeoutError):
                _read_pipe_byte(status_read, 0.3)

        assert _read_pipe_byte(status_read, 5.0) == b"A"
        assert _read_pipe_byte(status_read, 5.0) == b"R"
        status = _wait_for_child(child_pid, 5.0)
        assert status is not None
        child_pid = None
        assert os.waitstatus_to_exitcode(status) == 0
    finally:
        if status_write >= 0:
            os.close(status_write)
        os.close(status_read)
        if child_pid is not None:
            _terminate_child(child_pid)


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
