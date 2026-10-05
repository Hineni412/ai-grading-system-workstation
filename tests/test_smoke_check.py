from __future__ import annotations

import sqlite3
import hashlib
import subprocess
from pathlib import Path

from backend.jobs.store import JobStore
from backend.repositories.db_manager import DBManager
from backend.scan_grading.grading_run_store import GradingRunStore
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


def test_documentation_checks_source_links_without_local_browser_artifacts(tmp_path: Path) -> None:
    from tools.check_documentation import check_markdown_links

    (tmp_path / "README.md").write_text(
        "`frontend/test-results/`\n"
        "`frontend/playwright-report/`\n"
        "`frontend/src/missing.vue`\n"
        "[missing source](frontend/src/missing.vue)\n",
        encoding="utf-8",
    )
    issues = check_markdown_links(tmp_path)
    assert [(issue.code, issue.line) for issue in issues] == [("DOC001", 4), ("DOC002", 3)]
