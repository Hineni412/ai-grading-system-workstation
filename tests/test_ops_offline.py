from __future__ import annotations
from pathlib import Path
from backend.files.data_transfer_service import build_export_manifest, default_export_sources
import sqlite3
import zipfile

from types import SimpleNamespace
import pytest
from backend.ops.jobs import create_safety_backup
from backend.ops.journal import OpsOperationJournal, OpsOperationManifest
from backend.ops.offline import apply_pending_operation
from backend.schema_migrations import ensure_schema_current


def data_transfer_service__write(path: Path, content: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def data_transfer_service__arc_names(paths) -> set[str]:
    return {entry.arc_name for entry in paths}


def test_lean_export_skips_regenerable_and_historical_heavy_data(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    data_root = project_root / "user_data"

    data_transfer_service__write(project_root / "config" / "app.json")
    data_transfer_service__write(data_root / "databases" / "grading_system.db")
    data_transfer_service__write(data_root / "config" / "api_profiles.json")
    data_transfer_service__write(data_root / "templates" / "session_1" / "front_template.png")
    data_transfer_service__write(data_root / "templates" / "session_1" / "template_source_full_class.pdf")
    data_transfer_service__write(data_root / "question_bank" / "raw_papers" / "paper.docx")
    data_transfer_service__write(data_root / "question_bank" / "rich_content" / "question.json")
    data_transfer_service__write(data_root / "question_bank" / "extracted_images" / "page.png")
    data_transfer_service__write(data_root / "annotated" / "session_1" / "marked.jpg")
    data_transfer_service__write(data_root / "reports" / "report.pdf")
    data_transfer_service__write(data_root / "backups" / "backup.zip")
    data_transfer_service__write(data_root / "outputs" / "comparison" / "image.jpg")

    entries = build_export_manifest(
        default_export_sources(project_root, data_root), scope="lean"
    )
    arc_names = data_transfer_service__arc_names(entries)

    assert "config/app.json" in arc_names
    assert "user_data/databases/grading_system.db" in arc_names
    assert "user_data/config/api_profiles.json" not in arc_names
    assert "user_data/templates/session_1/front_template.png" in arc_names
    assert "user_data/question_bank/raw_papers/paper.docx" in arc_names
    assert "user_data/question_bank/rich_content/question.json" in arc_names
    assert (
        "user_data/templates/session_1/template_source_full_class.pdf" not in arc_names
    )
    assert "user_data/question_bank/extracted_images/page.png" not in arc_names
    assert "user_data/annotated/session_1/marked.jpg" not in arc_names
    assert "user_data/reports/report.pdf" not in arc_names
    assert "user_data/backups/backup.zip" not in arc_names
    assert "user_data/outputs/comparison/image.jpg" not in arc_names


OPERATION_ID = "11111111-1111-4111-8111-111111111111"


def _create_database(
    path: Path,
    value: str,
    *,
    target: str,
    migrations_dir: Path,
) -> None:
    ensure_schema_current(target, path, migrations_dir=migrations_dir)
    with sqlite3.connect(path) as connection:
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
        migration_project_root=project_root,
    )
    baseline_sql = {
        "grading": (
            "CREATE TABLE sample (value TEXT);\n"
            "CREATE TABLE students (id INTEGER PRIMARY KEY);\n"
            "CREATE TABLE grading_sessions (id INTEGER PRIMARY KEY);\n"
            "CREATE TABLE exam_papers (id INTEGER PRIMARY KEY);\n"
        ),
        "question_bank": (
            "CREATE TABLE sample (value TEXT);\n"
            "CREATE TABLE papers (id INTEGER PRIMARY KEY);\n"
            "CREATE TABLE questions (id INTEGER PRIMARY KEY);\n"
            "CREATE TABLE question_tags (id INTEGER PRIMARY KEY);\n"
        ),
        "student_affairs": (
            "CREATE TABLE sample (value TEXT);\n"
            "CREATE TABLE vault_metadata (id INTEGER PRIMARY KEY);\n"
            "CREATE TABLE encrypted_objects (id INTEGER PRIMARY KEY);\n"
            "CREATE TABLE access_audit (id INTEGER PRIMARY KEY);\n"
        ),
        "class_teacher_work": (
            "CREATE TABLE sample (value TEXT);\n"
            "CREATE TABLE work_nodes (id INTEGER PRIMARY KEY);\n"
            "CREATE TABLE work_edges (id INTEGER PRIMARY KEY);\n"
            "CREATE TABLE work_operations (id INTEGER PRIMARY KEY);\n"
        ),
    }
    for target, sql in baseline_sql.items():
        directory = project_root / "migrations" / target
        directory.mkdir(parents=True)
        (directory / "000_baseline.sql").write_text(sql, encoding="utf-8")
    _create_database(
        paths.db_path,
        "grading",
        target="grading",
        migrations_dir=project_root / "migrations" / "grading",
    )
    _create_database(
        paths.qb_db_path,
        "question-bank",
        target="question_bank",
        migrations_dir=project_root / "migrations" / "question_bank",
    )
    paths.config_dir.mkdir(parents=True)
    paths.logs_dir.mkdir(parents=True)
    (paths.config_dir / "keep.txt").write_text("keep", encoding="utf-8")
    for target in ("grading", "question_bank"):
        directory = project_root / "migrations" / target
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


def _prepare_restore(
    paths: SimpleNamespace, members: dict[str, bytes]
) -> OpsOperationJournal:
    source = _write_zip(paths.backups_dir / "backup_source_manual.zip", members)
    operation_root = paths.ops_state_dir / "operations" / OPERATION_ID
    staging = operation_root / "staging"
    staging.mkdir(parents=True)
    preparation_backup = create_safety_backup(
        paths=paths,
        reason="before_restore",
        operation_id=OPERATION_ID,
        archive_names=tuple(members),
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


def test_restore_rechecks_source_overlays_files_and_backs_up_latest_state(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    paths.taxonomy_state_path = tmp_path / "account-config" / "taxonomy_state_v2.json"
    paths.taxonomy_state_path.parent.mkdir()
    paths.taxonomy_state_path.write_text('{"decision":"old"}', encoding="utf-8")
    (paths.taxonomy_state_path.parent / "api_profiles.json").write_text('secret', encoding="utf-8")
    from backend.ops.jobs import _backup_entries
    from update_tools.backup_core import preview_backup
    preview = preview_backup(path_manager=paths)
    assert "config/taxonomy-governance/state.json" in preview["files"]
    assert not any("api_profiles" in name for name in preview["files"])
    staging = tmp_path / "backup-staging"
    staging.mkdir()
    entries = _backup_entries(paths, staging, scopes=["grading"])
    assert any(entry.arc_name == "config/taxonomy-governance/state.json" for entry in entries)
    older_id = "22222222-2222-4222-8222-222222222222"
    older_root = paths.ops_state_dir / "operations" / older_id
    older_staging = older_root / "staging"
    (older_staging / "migration-backups" / "question_bank").mkdir(parents=True)
    (older_staging / "migration-backups" / "question_bank" / "x.db").write_bytes(b"x")
    older_journal = OpsOperationJournal(paths.ops_state_dir)
    older_journal.prepare(
        OpsOperationManifest(
            operation_id=older_id,
            operation="migration",
            parameters={"target": "grading"},
            resource_fingerprint="0" * 64,
            staging_root=str(older_staging),
            preparation_backup=str(
                paths.backups_dir / "backup_20200101_000000_before_update.zip"
            ),
            created_at="2026-01-01T00:00:00+08:00",
        )
    )
    older_journal.mark_applied(older_id)
    for name in (
        "backup_20200101_000000_before_update.zip",
        "backup_20200102_000000_before_apply.zip",
        "backup_20200103_000000_before_import.zip",
        "backup_20200101_000000_manual.zip",
    ):
        _write_zip(paths.backups_dir / name, {"x.txt": b"x"})
    journal = _prepare_restore(
        paths,
        {
            "user_data/config/keep.txt": b"restored",
            "user_data/config/new.txt": b"new",
            "config/taxonomy-governance/state.json": b'{"decision":"restored"}',
        },
    )
    (paths.config_dir / "keep.txt").write_text("latest", encoding="utf-8")
    (paths.config_dir / "unlisted.txt").write_text("preserved", encoding="utf-8")

    assert apply_pending_operation(paths=paths) == 0

    backups = paths.backups_dir
    assert not (backups / "backup_20200101_000000_before_update.zip").exists()
    assert not (backups / "backup_20200102_000000_before_apply.zip").exists()
    assert (backups / "backup_20200103_000000_before_import.zip").exists()
    assert (backups / "backup_20200101_000000_manual.zip").exists()
    assert (backups / "backup_source_manual.zip").exists()
    from backend.ops.jobs import _AUTOMATIC_BACKUP_REASONS, _BACKUP_NAME
    automatic = [
        path
        for path in backups.glob("backup_*.zip")
        if (match := _BACKUP_NAME.fullmatch(path.name))
        and match.group(2) in _AUTOMATIC_BACKUP_REASONS
    ]
    assert len(automatic) == 3
    assert not older_staging.exists()
    assert (older_root / "status.json").is_file()
    assert (paths.ops_state_dir / "operations" / OPERATION_ID / "status.json").is_file()

    assert (paths.config_dir / "keep.txt").read_text(encoding="utf-8") == "restored"
    assert (paths.config_dir / "new.txt").read_text(encoding="utf-8") == "new"
    assert paths.taxonomy_state_path.read_text(encoding="utf-8") == '{"decision":"restored"}'
    assert (paths.taxonomy_state_path.parent / "api_profiles.json").read_text() == "secret"
    assert (paths.config_dir / "unlisted.txt").read_text(
        encoding="utf-8"
    ) == "preserved"
    public = journal.load_public(OPERATION_ID)
    assert public["status"] == "applied"
    assert str(public["recovery"]["backup_filename"]).endswith(".zip")
    assert "apply" in str(public["recovery"]["backup_filename"])


def test_restore_rejects_corrupt_database_before_replacing_target(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    before = paths.db_path.read_bytes()
    journal = _prepare_restore(
        paths,
        {"user_data/databases/grading_system.db": b"not-sqlite"},
    )

    assert apply_pending_operation(paths=paths) == 0
    assert paths.db_path.read_bytes() == before
    assert journal.load_public(OPERATION_ID)["status"] == "rolled_back"


@pytest.mark.parametrize("fail_apply", [False, True])
def test_restore_clears_preexisting_database_companions_and_rolls_back_safely(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fail_apply: bool,
) -> None:
    paths = _paths(tmp_path)
    before_value = "grading"
    journal = _prepare_restore(
        paths,
        {"user_data/databases/grading_system.db": paths.db_path.read_bytes()},
    )
    from backend.ops import offline

    real_backup = offline.create_safety_backup

    def backup_then_add_stale_companions(**kwargs):
        backup = real_backup(**kwargs)
        if kwargs["reason"] == "before_apply":
            Path(f"{paths.db_path}-wal").write_bytes(b"stale-wal")
            Path(f"{paths.db_path}-shm").write_bytes(b"stale-shm")
        return backup

    monkeypatch.setattr(
        offline, "create_safety_backup", backup_then_add_stale_companions
    )
    if fail_apply:
        monkeypatch.setattr(
            offline,
            "_replace_database_file",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("apply failed")),
        )

    assert apply_pending_operation(paths=paths) == 0
    assert not Path(f"{paths.db_path}-wal").exists()
    assert not Path(f"{paths.db_path}-shm").exists()
    with sqlite3.connect(paths.db_path) as connection:
        assert (
            connection.execute("SELECT value FROM sample").fetchone()[0] == before_value
        )
    assert journal.load_public(OPERATION_ID)["status"] == (
        "rolled_back" if fail_apply else "applied"
    )


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
    backup = create_safety_backup(
        paths=paths,
        reason="before_apply",
        operation_id=OPERATION_ID,
        archive_names=("user_data/config/keep.txt",),
    )
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
