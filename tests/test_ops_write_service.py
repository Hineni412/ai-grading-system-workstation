from __future__ import annotations

import asyncio
import json
import sqlite3
import zipfile
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.ops.plan_store import OpsPlanStore
from backend.ops.models import OpsOperation
from backend.ops.write_service import (
    OpsPreflightStale,
    OpsResourceNotFound,
    OpsWriteService,
)
from backend.ops.journal import OpsOperationBusy
from backend.jobs.manager import JobManager
from backend.jobs.store import JobStore
from backend.public_data import contains_filesystem_reference
from update_tools.backup_core import preview_backup
from update_tools.migrate_db import preview_migrations


def _create_database(path: Path) -> None:
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
    )
    _create_database(paths.db_path)
    _create_database(paths.qb_db_path)
    (project_root / "config").mkdir(parents=True)
    (project_root / "config" / "app_config.yaml").write_text("VERSION: test", encoding="utf-8")
    return paths


def _migration_dirs(tmp_path: Path) -> dict[str, Path]:
    root = tmp_path / "migrations"
    grading = root / "grading"
    question_bank = root / "question_bank"
    grading.mkdir(parents=True)
    question_bank.mkdir(parents=True)
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


def test_backup_preflight_does_not_create_backup_or_log_directories(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    assert not paths.backups_dir.exists()
    assert not paths.logs_dir.exists()

    response = _service(tmp_path, paths).preflight(
        _request("backup", reason="manual")
    )

    assert response["operation"] == "backup"
    assert response["requires_restart"] is False
    assert response["summary"]["database_count"] == 2
    assert not paths.backups_dir.exists()
    assert not paths.logs_dir.exists()
    assert not contains_filesystem_reference(response["summary"])


def test_preflight_accepts_shared_operation_enum(tmp_path: Path) -> None:
    paths = _paths(tmp_path)

    response = _service(tmp_path, paths).preflight(
        _request(OpsOperation.BACKUP, reason="manual")
    )

    assert response["operation"] == "backup"


def test_preview_backup_excludes_api_profiles_without_creating_output(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.config_dir.mkdir(parents=True)
    (paths.config_dir / "api_profiles.json").write_text('{"api_key":"secret"}', encoding="utf-8")
    (paths.config_dir / "safe.json").write_text('{"ok":true}', encoding="utf-8")
    workspace_file = (
        paths.data_root / "workspaces" / "teaching-prep" / "private.db"
    )
    workspace_file.parent.mkdir(parents=True)
    workspace_file.write_text("private", encoding="utf-8")

    preview = preview_backup(path_manager=paths)

    assert "user_data/config/safe.json" in preview["files"]
    assert "user_data/config/api_profiles.json" not in preview["files"]
    assert not any("workspaces/" in name for name in preview["files"])
    assert preview["skipped_sensitive"] == ["user_data/config/api_profiles.json"]
    assert not paths.backups_dir.exists()
    assert not paths.logs_dir.exists()


def test_preview_backup_excludes_case_variant_api_profiles(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.config_dir.mkdir(parents=True)
    (paths.config_dir / "API_PROFILES.JSON").write_text("secret", encoding="utf-8")

    preview = preview_backup(path_manager=paths)

    assert "user_data/config/API_PROFILES.JSON" not in preview["files"]
    assert preview["skipped_sensitive"] == ["user_data/config/API_PROFILES.JSON"]


def test_preview_backup_includes_class_teacher_workspace_in_debug_mode(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    database = paths.data_root / "workspaces" / "class-teacher" / "student_affairs.db"
    database.parent.mkdir(parents=True)
    database.write_bytes(b"synthetic-plaintext-workspace")

    preview = preview_backup(path_manager=paths)

    assert "user_data/workspaces/class-teacher/student_affairs.db" in preview["files"]


def test_preview_backup_can_select_the_three_teacher_facing_data_groups(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    teaching_file = paths.data_root / "workspaces" / "teaching-prep" / "notes.json"
    class_file = paths.data_root / "workspaces" / "class-teacher" / "notes.json"
    teaching_file.parent.mkdir(parents=True)
    class_file.parent.mkdir(parents=True)
    teaching_file.write_text("teaching", encoding="utf-8")
    class_file.write_text("class", encoding="utf-8")

    teaching_only = preview_backup(
        path_manager=paths,
        scopes=["teaching_prep"],
    )
    assert teaching_only["files"] == [
        "user_data/workspaces/teaching-prep/notes.json"
    ]

    class_only = preview_backup(
        path_manager=paths,
        scopes=["class_teacher"],
    )
    assert class_only["files"] == [
        "user_data/workspaces/class-teacher/notes.json"
    ]

    grading_only = preview_backup(path_manager=paths, scopes=["grading"])
    assert "user_data/databases/grading_system.db" in grading_only["files"]
    assert "user_data/databases/question_bank.db" in grading_only["files"]
    assert not any("workspaces/" in name for name in grading_only["files"])


def test_restore_preflight_accepts_only_controlled_backup_name(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    archive = _write_zip(
        paths.backups_dir / "backup_20260712_120000_manual.zip",
        {"user_data/config/settings.json": b"{}"},
    )
    service = _service(tmp_path, paths)

    response = service.preflight(
        _request("restore", backup_filename=archive.name)
    )

    assert response["requires_restart"] is True
    assert response["summary"] == {
        "file_count": 1,
        "total_expanded_bytes": 2,
        "database_count": 0,
        "warnings": [],
    }
    with pytest.raises(OpsResourceNotFound):
        service.preflight(
            _request("restore", backup_filename="../outside.zip")
        )


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


@pytest.mark.parametrize(
    "tables",
    [
        ("students", "grading_sessions"),
        ("papers", "questions", "question_tags"),
    ],
)
def test_restore_preflight_rejects_incompatible_grading_schema(
    tmp_path: Path,
    tables: tuple[str, ...],
) -> None:
    paths = _paths(tmp_path)
    candidate = tmp_path / "candidate.db"
    with sqlite3.connect(candidate) as connection:
        for table in tables:
            connection.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)")
    archive = _write_zip(
        paths.backups_dir / "backup_20260712_120000_manual.zip",
        {"user_data/databases/grading_system.db": candidate.read_bytes()},
    )

    with pytest.raises(ValueError):
        _service(tmp_path, paths).preflight(
            _request("restore", backup_filename=archive.name)
        )


def test_restore_preflight_rejects_valid_main_with_wal_member(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    archive = _write_zip(
        paths.backups_dir / "backup_20260712_120000_manual.zip",
        {
            "user_data/databases/grading_system.db": paths.db_path.read_bytes(),
            "user_data/databases/grading_system.db-wal": b"unchecked-wal",
        },
    )

    with pytest.raises(ValueError):
        _service(tmp_path, paths).preflight(
            _request("restore", backup_filename=archive.name)
        )


def test_migration_preflight_executes_only_on_candidate(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    before = paths.db_path.read_bytes()

    response = _service(tmp_path, paths).preflight(
        _request("migration", target="grading")
    )

    assert response["requires_restart"] is True
    assert response["summary"]["integrity"] == "ok"
    assert response["summary"]["pending_migrations"] == 1
    assert response["summary"]["applied_in_preview"] == 1
    assert paths.db_path.read_bytes() == before
    assert not paths.logs_dir.exists()


def test_preview_migrations_applies_sql_only_to_temporary_copy(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    _create_database(source)
    migrations = _migration_dirs(tmp_path)["grading"]
    before = source.read_bytes()

    preview = preview_migrations(
        "grading",
        db_path=source,
        migrations_dir=migrations,
    )

    assert preview["integrity"] == "ok"
    assert preview["applied"] == ["001_add_preview"]
    assert source.read_bytes() == before


def test_transfer_export_preflight_uses_existing_scope_rules(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.config_dir.mkdir(parents=True)
    (paths.config_dir / "safe.json").write_text("{}", encoding="utf-8")

    response = _service(tmp_path, paths).preflight(
        _request("transfer_export", scope="lean")
    )

    assert response["requires_restart"] is False
    assert response["summary"]["scope"] == "lean"
    assert response["summary"]["file_count"] >= 3
    assert not contains_filesystem_reference(response["summary"])


def test_transfer_import_preflight_uses_server_upload_id(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    service = _service(tmp_path, paths)
    payload_path = _write_zip(
        tmp_path / "payload.zip",
        {"user_data/config/imported.json": b"{}"},
    )
    payload = payload_path.read_bytes()

    async def chunks():
        yield payload

    upload = asyncio.run(
        service.stage_import_upload(filename="payload.zip", chunks=chunks())
    )
    response = service.preflight(
        _request("transfer_import", upload_id=upload["upload_id"])
    )

    assert response["requires_restart"] is True
    assert response["summary"]["file_count"] == 1
    assert set(upload) == {"upload_id", "filename", "size_bytes", "sha256"}
    assert not contains_filesystem_reference(upload)


def test_transfer_import_preflight_rejects_corrupt_database(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    service = _service(tmp_path, paths)
    payload = _write_zip(
        tmp_path / "payload.zip",
        {"user_data/databases/question_bank.db": b"not-sqlite"},
    ).read_bytes()

    async def chunks():
        yield payload

    upload = asyncio.run(service.stage_import_upload(filename="payload.zip", chunks=chunks()))

    with pytest.raises(ValueError):
        service.preflight(
            _request("transfer_import", upload_id=upload["upload_id"])
        )


def test_consumed_plan_rejects_changed_restore_resource(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    archive = _write_zip(
        paths.backups_dir / "backup_20260712_120000_manual.zip",
        {"user_data/config/settings.json": b"{}"},
    )
    service = _service(tmp_path, paths)
    response = service.preflight(
        _request("restore", backup_filename=archive.name)
    )
    with zipfile.ZipFile(archive, "a") as opened:
        opened.writestr("user_data/config/changed.json", "changed")

    with pytest.raises(OpsPreflightStale):
        service.consume_plan(str(response["confirmation_token"]))


def test_concurrent_ops_submissions_accept_exactly_one_job(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    service = _service(tmp_path, paths)
    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)
    release = threading.Event()
    manager.register("ops_backup", lambda _context: release.wait(timeout=5) or {})
    tokens = [
        str(
            service.preflight(_request("backup", reason="manual"))[
                "confirmation_token"
            ]
        )
        for _ in range(2)
    ]
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def submit(token: str) -> None:
        barrier.wait()
        try:
            service.submit(token, manager)
            outcomes.append("accepted")
        except OpsOperationBusy:
            outcomes.append("busy")

    threads = [threading.Thread(target=submit, args=(token,)) for token in tokens]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    release.set()
    manager.shutdown()

    assert sorted(outcomes) == ["accepted", "busy"]


def test_pending_restart_operation_blocks_new_job_submission(tmp_path: Path) -> None:
    from backend.ops.journal import OpsOperationManifest

    paths = _paths(tmp_path)
    service = _service(tmp_path, paths)
    operation_id = "11111111-1111-4111-8111-111111111111"
    staging = paths.ops_state_dir / "operations" / operation_id / "staging"
    staging.mkdir(parents=True)
    service.journal.prepare(
        OpsOperationManifest(
            operation_id=operation_id,
            operation="migration",
            parameters={"target": "grading"},
            resource_fingerprint="a" * 64,
            staging_root=str(staging),
            preparation_backup=str(paths.backups_dir / "backup_safe.zip"),
            created_at="2026-07-12T12:00:00+08:00",
        )
    )
    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)
    manager.register("ops_backup", lambda _context: {})
    token = str(
        service.preflight(_request("backup", reason="manual"))["confirmation_token"]
    )

    with pytest.raises(OpsOperationBusy):
        service.submit(token, manager)
    manager.shutdown()


def test_backup_preview_rejects_detected_reparse_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    paths.config_dir.mkdir(parents=True)
    suspect = paths.config_dir / "suspect.json"
    suspect.write_text("{}", encoding="utf-8")
    from data_transfer_service import _is_reparse_point

    monkeypatch.setattr(
        "data_transfer_service._is_reparse_point",
        lambda path: Path(path) == suspect or _is_reparse_point(Path(path)),
    )

    with pytest.raises(ValueError):
        preview_backup(path_manager=paths)


def test_preflight_summary_can_be_json_encoded_without_internal_paths(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    response = _service(tmp_path, paths).preflight(
        _request("backup", reason="manual")
    )

    serialized = json.dumps(response["summary"], sort_keys=True)

    assert str(tmp_path) not in serialized
    assert "api_key" not in serialized.casefold()
