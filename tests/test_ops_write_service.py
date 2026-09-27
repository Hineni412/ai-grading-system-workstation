from __future__ import annotations

import asyncio
import sqlite3
import zipfile
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.ops.plan_store import OpsPlanStore
from backend.ops.write_service import (
    OpsPreflightStale,
    OpsWriteService,
)
from backend.ops.journal import OpsOperationBusy
from backend.jobs.manager import JobManager
from backend.jobs.store import JobStore
from backend.public_data import contains_filesystem_reference
from backend.schema_migrations import ensure_schema_current
from update_tools.backup_core import preview_backup


def _create_database(
    path: Path,
    *,
    target: str | None = None,
    migrations_dir: Path | None = None,
    backup_dir: Path | None = None,
) -> None:
    if target is not None and migrations_dir is not None:
        ensure_schema_current(
            target,
            path,
            migrations_dir=migrations_dir,
            backup_dir=backup_dir,
        )
        with sqlite3.connect(path) as connection:
            connection.execute("INSERT INTO sample(value) VALUES ('kept')")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT)")
        connection.execute("INSERT INTO sample(value) VALUES ('kept')")
        required = (
            ("students", "grading_sessions", "exam_papers")
            if path.name == "grading_system.db"
            else ("papers", "questions", "question_tags")
        )
        for table in required:
            connection.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)")
        connection.commit()


def _paths(tmp_path: Path) -> SimpleNamespace:
    project_root = tmp_path / "project"
    data_root = project_root / "data"
    paths = SimpleNamespace(
        project_root=project_root,
        data_root=data_root,
        databases_dir=data_root / "databases",
        db_path=data_root / "databases" / "grading_system.db",
        qb_db_path=data_root / "databases" / "question_bank.db",
        exams_dir=data_root / "exams",
        config_dir=data_root / "config",
        templates_dir=data_root / "templates",
        annotated_dir=data_root / "annotated",
        reports_dir=data_root / "reports",
        qb_data_dir=data_root / "question_bank",
        outputs_dir=data_root / "outputs",
        snapshots_dir=data_root / "snapshots",
        backups_dir=data_root / "backups",
        logs_dir=project_root / "logs",
        ops_state_dir=tmp_path / "local" / "ops",
        migration_project_root=tmp_path,
    )
    migration_dirs = _migration_dirs(tmp_path, include_pending=False)
    _create_database(
        paths.db_path,
        target="grading",
        migrations_dir=migration_dirs["grading"],
        backup_dir=tmp_path / "fixture-backups",
    )
    _create_database(
        paths.qb_db_path,
        target="question_bank",
        migrations_dir=migration_dirs["question_bank"],
        backup_dir=tmp_path / "fixture-backups",
    )
    _migration_dirs(tmp_path, include_pending=True)
    (project_root / "config").mkdir(parents=True)
    (project_root / "config" / "app_config.yaml").write_text(
        "VERSION: test", encoding="utf-8"
    )
    return paths


def _migration_dirs(
    tmp_path: Path,
    *,
    include_pending: bool = True,
) -> dict[str, Path]:
    root = tmp_path / "migrations"
    grading = root / "grading"
    question_bank = root / "question_bank"
    grading.mkdir(parents=True, exist_ok=True)
    question_bank.mkdir(parents=True, exist_ok=True)
    (grading / "000_baseline.sql").write_text(
        "CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT);\n"
        "CREATE TABLE students (id INTEGER PRIMARY KEY);\n"
        "CREATE TABLE grading_sessions (id INTEGER PRIMARY KEY);\n"
        "CREATE TABLE exam_papers (id INTEGER PRIMARY KEY);\n",
        encoding="utf-8",
    )
    (question_bank / "000_baseline.sql").write_text(
        "CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT);\n"
        "CREATE TABLE papers (id INTEGER PRIMARY KEY);\n"
        "CREATE TABLE questions (id INTEGER PRIMARY KEY);\n"
        "CREATE TABLE question_tags (id INTEGER PRIMARY KEY);\n",
        encoding="utf-8",
    )
    if include_pending:
        (grading / "001_add_preview.sql").write_text(
            "CREATE TABLE preview_grading (id INTEGER PRIMARY KEY);",
            encoding="utf-8",
        )
        (question_bank / "001_add_preview.sql").write_text(
            "CREATE TABLE preview_question_bank (id INTEGER PRIMARY KEY);",
            encoding="utf-8",
        )
    return {"grading": grading, "question_bank": question_bank}


def _service(tmp_path: Path, paths: SimpleNamespace) -> OpsWriteService:
    return OpsWriteService(
        paths,
        plan_store=OpsPlanStore(),
        migration_dirs=_migration_dirs(tmp_path),
        upload_id_factory=lambda: "a" * 32,
    )


def _request(operation: str, **values: object) -> SimpleNamespace:
    return SimpleNamespace(operation=operation, **values)


def _write_zip(path: Path, members: dict[str, bytes]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return path


@pytest.mark.parametrize(
    ("member", "payload"),
    [
        ("user_data/databases/grading_system.db", b"not-sqlite"),
        ("user_data/databases/unexpected.db", b"not-sqlite"),
        ("user_data/databases/grading_system.db-wal", b"wal"),
        ("user_data/databases/grading_system.sqlite", b"sqlite"),
    ],
)
def test_restore_preflight_rejects_invalid_database_candidates(
    tmp_path: Path,
    member: str,
    payload: bytes,
) -> None:
    paths = _paths(tmp_path)
    archive = _write_zip(
        paths.backups_dir / "backup_20260712_120000_manual.zip",
        {member: payload},
    )

    with pytest.raises(ValueError):
        _service(tmp_path, paths).preflight(
            _request("restore", backup_filename=archive.name)
        )


def test_consumed_plan_rejects_changed_restore_resource(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    archive = _write_zip(
        paths.backups_dir / "backup_20260712_120000_manual.zip",
        {"user_data/config/settings.json": b"{}"},
    )
    service = _service(tmp_path, paths)
    response = service.preflight(_request("restore", backup_filename=archive.name))
    with zipfile.ZipFile(archive, "a") as opened:
        opened.writestr("user_data/config/changed.json", "changed")

    with pytest.raises(OpsPreflightStale):
        service.consume_plan(str(response["confirmation_token"]))
