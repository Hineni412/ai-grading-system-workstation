from __future__ import annotations

import threading
import subprocess
import sys
import os
from pathlib import Path

from backend.ops.lock import OpsLockBusy, OpsOperationLock


def test_second_ops_writer_cannot_enter_until_first_releases(tmp_path: Path) -> None:
    first = OpsOperationLock(tmp_path / "ops", timeout_seconds=0.2)
    second = OpsOperationLock(tmp_path / "ops", timeout_seconds=0.05)
    outcomes: list[str] = []

    with first.acquire():
        def contend() -> None:
            try:
                with second.acquire():
                    outcomes.append("entered")
            except OpsLockBusy:
                outcomes.append("busy")

        thread = threading.Thread(target=contend)
        thread.start()
        thread.join(timeout=1)

    assert outcomes == ["busy"]
    with second.acquire():
        outcomes.append("entered_after_release")
    assert outcomes[-1] == "entered_after_release"


def test_cancel_check_runs_while_waiting_for_ops_lock(tmp_path: Path) -> None:
    first = OpsOperationLock(tmp_path / "ops", timeout_seconds=0.2)
    second = OpsOperationLock(tmp_path / "ops", timeout_seconds=1)
    checks = 0

    with first.acquire():
        def cancel() -> None:
            nonlocal checks
            checks += 1
            raise RuntimeError("cancelled")

        try:
            with second.acquire(cancel_check=cancel):
                raise AssertionError("must not enter")
        except RuntimeError as exc:
            assert str(exc) == "cancelled"

    assert checks == 1


def test_ops_lock_is_exclusive_across_processes(tmp_path: Path) -> None:
    state_root = tmp_path / "ops"
    first = OpsOperationLock(state_root, timeout_seconds=0.2)
    script = (
        f"import sys; sys.path.insert(0, {str(Path.cwd())!r}); "
        "from pathlib import Path; "
        "from backend.ops.lock import OpsLockBusy, OpsOperationLock; "
        f"lock=OpsOperationLock(Path({str(state_root)!r}), timeout_seconds=0.1); "
        "code=0; "
        "\ntry:\n"
        "  with lock.acquire(): code=3\n"
        "except OpsLockBusy: code=0\n"
        "raise SystemExit(code)"
    )

    with first.acquire():
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path.cwd(),
            env={**os.environ, "PYTHONPATH": str(Path.cwd())},
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )

    assert completed.returncode == 0, completed.stderr
