from __future__ import annotations

import subprocess
import sys
import os
from pathlib import Path

from backend.ops.lock import OpsOperationLock


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
