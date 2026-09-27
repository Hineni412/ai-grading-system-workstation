from __future__ import annotations

import sqlite3
import hashlib
import subprocess
from pathlib import Path

from backend.jobs.store import JobStore
from db_manager import DBManager
from grading_run_store import GradingRunStore
from question_bank.database.schema import initialize_database


def _checkpoint(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def test_pytest_preserves_shared_runner_failure_and_slow_test_details(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from tools import smoke_check

    output = "FAILED tests/test_flow.py::test_save\n1 failed in 2.00s\n2.00s call tests/test_flow.py::test_save\n"
    monkeypatch.setattr(
        smoke_check.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command,
            7,
            stdout=output,
            stderr="synthetic stderr",
        ),
    )
    result = smoke_check.run_pytest(tmp_path)
    assert not result.ok
    assert result.return_code == 7
    assert result.messages == [*output.splitlines(), "synthetic stderr"]
