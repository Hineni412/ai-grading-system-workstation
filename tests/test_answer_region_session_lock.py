from __future__ import annotations

from pathlib import Path

import pytest

import answer_region_session_lock as lock_module
from answer_region_session_lock import AnswerRegionSessionLock


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
