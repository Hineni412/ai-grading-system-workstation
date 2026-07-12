from __future__ import annotations

import sqlite3
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.ops.jobs import create_safety_backup
from backend.ops.journal import OpsOperationJournal, OpsOperationManifest
from backend.ops.offline import apply_pending_operation


OPERATION_ID = "11111111-1111-4111-8111-111111111111"


def _create_database(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE sample (value TEXT)")
        connection.execute("INSERT INTO sample(value) VALUES (?)", (value,))


def _paths(tmp_path: Path) -> SimpleNamespace:
    project_root = tmp_path / "project"
    data_root = project_root / "user_data"
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
    _create_database(paths.db_path, "grading")
    _create_database(paths.qb_db_path, "question-bank")
    paths.config_dir.mkdir(parents=True)
    paths.logs_dir.mkdir(parents=True)
    (paths.config_dir / "keep.txt").write_text("keep", encoding="utf-8")
    for target in ("grading", "question_bank"):
        directory = project_root / "migrations" / target
        directory.mkdir(parents=True)
        (directory / "001_add_table.sql").write_text(
            f"CREATE TABLE {target}_added (id INTEGER PRIMARY KEY);",
            encoding="utf-8",
        )
    return paths


def _write_zip(path: Path, members: dict[str, bytes]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return path


def _prepare_restore(paths: SimpleNamespace, members: dict[str, bytes]) -> OpsOperationJournal:
    source = _write_zip(paths.backups_dir / "backup_source_manual.zip", members)
    operation_root = paths.ops_state_dir / "operations" / OPERATION_ID
    staging = operation_root / "staging"
    staging.mkdir(parents=True)
    preparation_backup = create_safety_backup(
        paths=paths,
        reason="before_restore",
        operation_id=OPERATION_ID,
    )
    import hashlib

    manifest = OpsOperationManifest(
        operation_id=OPERATION_ID,
        operation="restore",
        parameters={"backup_filename": source.name},
        resource_fingerprint=hashlib.sha256(source.read_bytes()).hexdigest(),
        staging_root=str(staging),
        preparation_backup=str(preparation_backup),
        created_at="2026-07-12T12:00:00+08:00",
    )
    journal = OpsOperationJournal(paths.ops_state_dir)
    journal.prepare(manifest)
    return journal


def _table_exists(path: Path, table: str) -> bool:
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
    return row is not None


def test_no_pending_operation_is_a_side_effect_free_noop(tmp_path: Path) -> None:
    paths = _paths(tmp_path)

    exit_code = apply_pending_operation(paths=paths)
    assert exit_code == 0, journal.load_public(OPERATION_ID)
    assert not paths.ops_state_dir.exists()


def test_multiple_active_pending_manifests_stop_startup(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _prepare_restore(paths, {"user_data/config/keep.txt": b"changed"})
    other_root = tmp_path / "other-ops"
    other_id = "22222222-2222-4222-8222-222222222222"
    other_staging = other_root / "operations" / other_id / "staging"
    other_staging.mkdir(parents=True)
    other = OpsOperationJournal(other_root)
    other.prepare(
        OpsOperationManifest(
            operation_id=other_id,
            operation="migration",
            parameters={"target": "grading"},
            resource_fingerprint="a" * 64,
            staging_root=str(other_staging),
            preparation_backup=str(paths.backups_dir / "backup_other.zip"),
            created_at="2026-07-12T12:01:00+08:00",
        )
    )
    import shutil

    shutil.copytree(
        other_root / "operations" / other_id,
        paths.ops_state_dir / "operations" / other_id,
    )

    assert apply_pending_operation(paths=paths) == 2


def test_restore_rechecks_source_overlays_files_and_backs_up_latest_state(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    journal = _prepare_restore(
        paths,
        {
            "user_data/config/keep.txt": b"restored",
            "user_data/config/new.txt": b"new",
        },
    )
    (paths.config_dir / "keep.txt").write_text("latest", encoding="utf-8")
    (paths.config_dir / "unlisted.txt").write_text("preserved", encoding="utf-8")

    assert apply_pending_operation(paths=paths) == 0

    assert (paths.config_dir / "keep.txt").read_text(encoding="utf-8") == "restored"
    assert (paths.config_dir / "new.txt").read_text(encoding="utf-8") == "new"
    assert (paths.config_dir / "unlisted.txt").read_text(encoding="utf-8") == "preserved"
    public = journal.load_public(OPERATION_ID)
    assert public["status"] == "applied"
    assert str(public["recovery"]["backup_filename"]).endswith(".zip")
    assert "apply" in str(public["recovery"]["backup_filename"])


def test_apply_failure_rolls_back_overwrite_and_new_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    journal = _prepare_restore(
        paths,
        {
            "user_data/config/keep.txt": b"changed",
            "user_data/config/new.txt": b"new",
        },
    )
    from backend.ops import offline

    original = offline._replace_staged_file
    calls = 0

    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected apply failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(offline, "_replace_staged_file", fail_second)

    assert apply_pending_operation(paths=paths) == 0
    assert (paths.config_dir / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert not (paths.config_dir / "new.txt").exists()
    assert journal.load_public(OPERATION_ID)["status"] == "rolled_back"


def test_rollback_failure_stops_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    journal = _prepare_restore(
        paths,
        {"user_data/config/keep.txt": b"changed"},
    )
    monkeypatch.setattr(
        "backend.ops.offline._replace_staged_file",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("apply failed")),
    )
    monkeypatch.setattr(
        "backend.ops.offline._rollback",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("rollback failed")),
    )

    assert apply_pending_operation(paths=paths) == 2
    public = journal.load_public(OPERATION_ID)
    assert public["status"] == "failed"
    assert public["result_code"] == "rollback_failed"


def test_crash_recovery_rolls_back_before_attempting_apply(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    journal = _prepare_restore(paths, {"user_data/config/keep.txt": b"changed"})
    journal.claim_pending()
    backup = create_safety_backup(paths=paths, reason="before_apply", operation_id=OPERATION_ID)
    journal.start_apply(OPERATION_ID, backup)
    journal.record_replacement(
        OPERATION_ID,
        target=paths.config_dir / "keep.txt",
        archive_name="user_data/config/keep.txt",
        existed=True,
    )
    (paths.config_dir / "keep.txt").write_text("partially-applied", encoding="utf-8")

    assert apply_pending_operation(paths=paths) == 0
    assert (paths.config_dir / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert journal.load_public(OPERATION_ID)["status"] == "rolled_back"


def test_all_migration_failure_rolls_back_both_databases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    operation_root = paths.ops_state_dir / "operations" / OPERATION_ID
    staging = operation_root / "staging"
    staging.mkdir(parents=True)
    backup = create_safety_backup(paths=paths, reason="before_update", operation_id=OPERATION_ID)
    from backend.ops.write_service import OpsWriteService
    from backend.ops.plan_store import OpsPlanStore

    service = OpsWriteService(paths, plan_store=OpsPlanStore())
    journal = OpsOperationJournal(paths.ops_state_dir)
    journal.prepare(
        OpsOperationManifest(
            operation_id=OPERATION_ID,
            operation="migration",
            parameters={"target": "all"},
            resource_fingerprint=service.migration_files_fingerprint("all"),
            staging_root=str(staging),
            preparation_backup=str(backup),
            created_at="2026-07-12T12:00:00+08:00",
        )
    )
    from backend.ops import offline

    real_run = offline.run_migrations
    calls = 0

    def fail_second(target, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("second migration failed")
        return real_run(target, **kwargs)

    monkeypatch.setattr(offline, "run_migrations", fail_second)

    exit_code = apply_pending_operation(paths=paths)
    assert exit_code == 0, journal.load_public(OPERATION_ID)
    assert not _table_exists(paths.db_path, "grading_added")
    assert not _table_exists(paths.qb_db_path, "question_bank_added")
    assert journal.load_public(OPERATION_ID)["status"] == "rolled_back"
