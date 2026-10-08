from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.ops.jobs import create_safety_backup
from backend.ops.journal import OpsOperationJournal, OpsOperationManifest
from backend.ops.offline import apply_pending_operation
from backend.schema_migrations import ensure_schema_current

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


def _table_exists(path: Path, table: str) -> bool:
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
    return row is not None


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


def test_all_migration_failure_rolls_back_both_databases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    operation_root = paths.ops_state_dir / "operations" / OPERATION_ID
    staging = operation_root / "staging"
    staging.mkdir(parents=True)
    backup = create_safety_backup(
        paths=paths,
        reason="before_update",
        operation_id=OPERATION_ID,
        archive_names=(
            "user_data/databases/grading_system.db",
            "user_data/databases/question_bank.db",
        ),
    )
    from backend.ops.plan_store import OpsPlanStore
    from backend.ops.write_service import OpsWriteService

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
    assert not (paths.databases_dir / "backups").exists()
    assert journal.load_public(OPERATION_ID)["status"] == "rolled_back"


def test_startup_entry_points_stay_light() -> None:
    heavy_modules = {
        "backend.api.app",
        "openai",
        "question_bank.services.question_read_service",
        "backend.jobs.default_handlers",
    }
    repo_root = Path(__file__).resolve().parents[1]
    probe = (
        "import importlib, json, sys\n"
        "importlib.import_module(sys.argv[1])\n"
        "print(json.dumps(sorted(sys.modules)))\n"
    )
    for module in (
        "tools.ensure_frontend",
        "backend.api.launcher",
        "backend.ops.offline",
    ):
        result = subprocess.run(
            [sys.executable, "-c", probe, module],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert result.returncode == 0, (module, result.stderr[-2000:])
        loaded = set(json.loads(result.stdout.strip().splitlines()[-1]))
        leaked = sorted(loaded & heavy_modules)
        assert not leaked, f"{module} loaded heavy modules: {leaked}"


def test_automatic_standard_and_request_receipt_survive_ordinary_backup_and_offline_restore(tmp_path):
    import shutil
    from contextlib import nullcontext
    from tests.test_ops_jobs import _paths as current_paths, _Context, _payload, _service
    from tests.test_skill_candidates import _automatic_type_bank, _automatic_response
    from backend.jobs.chapter_types import run_chapter_type_job
    from backend.ops.jobs import run_ops_backup_job
    from question_bank.current_knowledge import CurrentKnowledgeResolver
    from question_bank.knowledge_graph_release.loader import load_release, load_taxonomy_catalog_for_release
    from question_bank.services.chapter_type_service import preview_chapter_type_plan
    source = tmp_path / "TEST-source"
    db, _ = _automatic_type_bank(source)
    plan = preview_chapter_type_plan(db_path=db, data_root=source, volume_id="bnu24-math-g8-lower")
    context = SimpleNamespace(job_id=904, payload={"authorization": {**plan, "confirmed": True, "request_limit": 1}},
        raise_if_cancelled=lambda: None, report=lambda *args: None)
    result = run_chapter_type_job(context=context, question_bank_db_path=db, data_root=source,
        ai_service_factory=lambda: SimpleNamespace(organize_chapter_types=_automatic_response), publication_guard=nullcontext)
    assert result["outcome"] == "published", result
    expected = CurrentKnowledgeResolver.from_active_database(db)
    paths = current_paths(tmp_path / "TEST-backup", migration_current=True)
    with sqlite3.connect(db) as original, sqlite3.connect(paths.qb_db_path) as target:
        original.backup(target)
    shutil.copytree(source / "question_bank", paths.qb_data_dir)
    backup = run_ops_backup_job(context=_Context(_payload(_service(paths), "backup", reason="manual")), paths=paths)
    with zipfile.ZipFile(paths.backups_dir / backup["filename"]) as archive:
        members = {name: archive.read(name) for name in archive.namelist() if not name.endswith("/")}
    prefix = f"user_data/question_bank/knowledge_releases/{expected.release_id}"
    assert f"{prefix}/release.json" in members and f"{prefix}/taxonomy.json" in members
    assert "user_data/question_bank/chapter_type_runs/job-904.json" in members
    assert not any("api_profiles" in name for name in members)
    restored = current_paths(tmp_path / "TEST-restored", migration_current=True)
    journal = _prepare_restore(restored, members)
    assert apply_pending_operation(paths=restored) == 0
    assert journal.load_public(OPERATION_ID)["status"] == "applied"
    actual = CurrentKnowledgeResolver.from_active_database(restored.qb_db_path)
    assert (actual.release_id, actual.content_hash) == (expected.release_id, expected.content_hash)
    local_release = load_release(restored.qb_data_dir / "knowledge_releases" / expected.release_id / "release.json")
    catalog = load_taxonomy_catalog_for_release(local_release)
    assert all(term["definition"] for term in catalog["terms"] if term["dimension"] == "ability")
    receipt = json.loads((restored.qb_data_dir / "chapter_type_runs/job-904.json").read_text(encoding="utf-8"))
    assert receipt["requests"][0]["status"] == "validated" and receipt["status"] == "published"
    def forbidden():
        pytest.fail("restored completed chapter task must never add a model request")
    replay = run_chapter_type_job(context=context, question_bank_db_path=restored.qb_db_path,
        data_root=restored.data_root, ai_service_factory=forbidden, publication_guard=nullcontext)
    assert replay == result