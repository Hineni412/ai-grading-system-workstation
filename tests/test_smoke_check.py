from __future__ import annotations

import sqlite3
import hashlib
from pathlib import Path

from db_manager import DBManager
from grading_run_store import GradingRunStore
from question_bank.database.schema import initialize_database


def _write(path: Path, text: str = "VALUE = 1\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _sqlite_schema_names(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','index','trigger')"
        ).fetchall()
    return {str(row[0]) for row in rows}


def _checkpoint(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_python_file_discovery_excludes_generated_and_data_directories(tmp_path: Path) -> None:
    from tools.smoke_check import iter_python_files

    _write(tmp_path / "app.py")
    _write(tmp_path / "pkg" / "service.py")
    _write(tmp_path / "runtime" / "ignored.py")
    _write(tmp_path / ".worktrees" / "ignored.py")
    _write(tmp_path / "user_data" / "ignored.py")
    _write(tmp_path / ".git" / "hooks" / "ignored.py")
    _write(tmp_path / "__pycache__" / "ignored.py")
    _write(tmp_path / "pkg" / "__pycache__" / "ignored.py")

    discovered = {
        path.relative_to(tmp_path).as_posix()
        for path in iter_python_files(tmp_path)
    }

    assert discovered == {"app.py", "pkg/service.py"}


def test_static_compile_reports_syntax_errors(tmp_path: Path) -> None:
    from tools.smoke_check import run_static_compile

    _write(tmp_path / "ok.py")
    _write(tmp_path / "bad.py", "def broken(:\n    pass\n")

    result = run_static_compile(tmp_path)

    assert not result.ok
    assert result.return_code != 0
    assert "bad.py" in "\n".join(result.messages)


def test_database_idempotency_check_uses_copies_and_keeps_sources_unchanged(tmp_path: Path) -> None:
    from tools.smoke_check import run_database_idempotency_check

    grading_db = tmp_path / "source" / "grading_system.db"
    question_bank_db = tmp_path / "source" / "question_bank.db"
    grading_db.parent.mkdir(parents=True, exist_ok=True)

    DBManager(grading_db).initialize()
    GradingRunStore(grading_db).initialize()
    initialize_database(question_bank_db)
    _checkpoint(grading_db)
    _checkpoint(question_bank_db)
    grading_schema_before = _sqlite_schema_names(grading_db)
    question_bank_schema_before = _sqlite_schema_names(question_bank_db)
    grading_hash_before = _sha256(grading_db)
    question_bank_hash_before = _sha256(question_bank_db)

    result = run_database_idempotency_check(
        grading_db=grading_db,
        question_bank_db=question_bank_db,
        work_dir=tmp_path / "smoke-work",
    )

    assert result.ok, result.messages
    assert _sha256(grading_db) == grading_hash_before
    assert _sha256(question_bank_db) == question_bank_hash_before
    assert _sqlite_schema_names(grading_db) == grading_schema_before
    assert _sqlite_schema_names(question_bank_db) == question_bank_schema_before


def test_database_idempotency_check_skips_missing_sources(tmp_path: Path) -> None:
    from tools.smoke_check import run_database_idempotency_check

    result = run_database_idempotency_check(
        grading_db=tmp_path / "missing-grading.db",
        question_bank_db=tmp_path / "missing-question-bank.db",
        work_dir=tmp_path / "smoke-work",
    )

    assert result.ok
    assert any("跳过" in message for message in result.messages)


def test_run_smoke_skip_tests_omits_pytest_step(monkeypatch, tmp_path: Path) -> None:
    from tools import smoke_check

    calls: list[str] = []

    monkeypatch.setattr(
        smoke_check,
        "run_static_compile",
        lambda project_root: smoke_check.StepResult("静态编译", True, 0, 0.0, ["ok"]),
    )
    monkeypatch.setattr(
        smoke_check,
        "run_pytest",
        lambda project_root: calls.append("pytest")
        or smoke_check.StepResult("全量测试", True, 0, 0.0, ["ok"]),
    )
    monkeypatch.setattr(
        smoke_check,
        "run_database_idempotency_check",
        lambda *, grading_db, question_bank_db, work_dir=None: smoke_check.StepResult(
            "两库初始化幂等", True, 0, 0.0, ["ok"]
        ),
    )

    results = smoke_check.run_smoke(
        project_root=tmp_path,
        grading_db=tmp_path / "grading.db",
        question_bank_db=tmp_path / "question_bank.db",
        skip_tests=True,
    )

    assert calls == []
    assert [result.name for result in results] == ["静态编译", "全量测试", "两库初始化幂等"]
    assert results[1].skipped


def test_temp_tree_cleanup_retries_transient_windows_lock(monkeypatch, tmp_path: Path) -> None:
    from tools import smoke_check

    target = tmp_path / "locked"
    _write(target / "file.txt")
    real_rmtree = smoke_check.shutil.rmtree
    attempts = 0

    def flaky_rmtree(path: Path) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise PermissionError("temporary lock")
        real_rmtree(path)

    monkeypatch.setattr(smoke_check.shutil, "rmtree", flaky_rmtree)

    smoke_check._remove_tree_with_retries(target, delay_seconds=0)

    assert attempts == 2
    assert not target.exists()
