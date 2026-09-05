from __future__ import annotations

import hashlib
import logging
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from backend.api.app import ApiError
from backend.class_teacher.api import create_router
from backend.class_teacher.encrypted_database import EncryptedDatabase
from backend.class_teacher.errors import VaultError
from backend.class_teacher.feature import _create_service, create_workspace_feature
from backend.class_teacher.model_approval import FakeApprovedModelGateway
from backend.class_teacher.vault_service import VaultService
from backend.schema_migrations import ensure_schema_current
from backend.workspaces.contracts import WorkspaceContext


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _context(tmp_path: Path) -> WorkspaceContext:
    root = tmp_path / "workspaces" / "class-teacher"
    paths = SimpleNamespace(
        project_root=PROJECT_ROOT,
        migration_project_root=PROJECT_ROOT,
        api_profiles_path=tmp_path / "api_profiles.json",
        legacy_api_profiles_paths=(),
        workspace_dir=lambda module_id, create=False: root,
    )
    return WorkspaceContext(module_id="class-teacher", root=root, paths=paths)


def _tree_snapshot(root: Path) -> dict[str, tuple[str, str | None]]:
    if not root.exists():
        return {}
    snapshot: dict[str, tuple[str, str | None]] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            snapshot[relative] = ("file", digest)
        else:
            snapshot[relative] = ("directory", None)
    return snapshot


def _insert_marker(
    connection: sqlite3.Connection,
    *,
    object_id: str,
    payload_nonce: bytes,
) -> None:
    connection.execute(
        """
        INSERT INTO encrypted_objects (
            object_id, object_type, format_version, cek_nonce, wrapped_cek,
            payload_nonce, payload_ciphertext, revision, created_at, updated_at
        ) VALUES (?, 'synthetic', 1, X'', X'', ?, X'7b7d', 1,
                  '2026-01-01T00:00:00+00:00',
                  '2026-01-01T00:00:00+00:00')
        """,
        (object_id, payload_nonce),
    )


def _seed_classification_case(
    context: WorkspaceContext,
    *,
    legacy: bool,
    with_wal: bool,
) -> sqlite3.Connection | None:
    service = VaultService(context)
    service.ensure_plaintext_ready()
    database_path = service.database.database_path
    if not with_wal:
        with closing(sqlite3.connect(database_path)) as connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            connection.execute("PRAGMA journal_mode = DELETE")
            _insert_marker(
                connection,
                object_id="classification-marker",
                payload_nonce=(
                    b"synthetic-legacy-nonce"
                    if legacy
                    else b"plaintext-json-v1"
                ),
            )
            connection.commit()
        Path(f"{database_path}-wal").unlink(missing_ok=True)
        Path(f"{database_path}-shm").unlink(missing_ok=True)
        return None

    keeper = sqlite3.connect(database_path)
    keeper.execute("PRAGMA journal_mode = WAL")
    keeper.execute("PRAGMA wal_autocheckpoint = 0")
    _insert_marker(
        keeper,
        object_id="classification-marker",
        payload_nonce=(
            b"synthetic-legacy-nonce" if legacy else b"plaintext-json-v1"
        ),
    )
    keeper.commit()
    assert Path(f"{database_path}-wal").is_file()
    assert Path(f"{database_path}-shm").is_file()
    return keeper


def test_production_workbench_is_open_and_persists_student_content_as_plaintext(
    tmp_path: Path,
) -> None:
    service = _create_service(_context(tmp_path))

    app = FastAPI()
    app.state.workspace_services = {"class-teacher": service}
    app.include_router(create_router(), prefix="/api/class-teacher")
    assert TestClient(app).get("/api/class-teacher/vault/status").status_code == 404

    created = service.support.create_subject(
        token="",
        operation_id="plaintext-subject-create-001",
        source_student_id="SYNTHETIC-001",
        display_name="合成学生甲",
        class_label="合成班",
    )

    assert created["display_name"] == "合成学生甲"
    assert service.student_directory.search(token="")["items"][0][
        "display_name"
    ] == "合成学生甲"
    assert service.affairs.list(token="")["items"] == []
    with sqlite3.connect(service.database.database_path) as connection:
        payload = connection.execute(
            "SELECT payload_ciphertext FROM encrypted_objects "
            "WHERE object_type = 'student_subject'"
        ).fetchone()[0]
    assert "合成学生甲" in bytes(payload).decode("utf-8")


def test_feature_service_creation_blocks_pending_ordinary_database_migration(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    migration_root = PROJECT_ROOT / "migrations" / "class_teacher_work"
    released_migrations = tmp_path / "released-class-teacher-work-migrations"
    released_migrations.mkdir()
    migrations = sorted(migration_root.glob("*.sql"))
    for migration in migrations[:-1]:
        shutil.copy2(migration, released_migrations / migration.name)
    database = context.root / "class_teacher_work.db"
    ensure_schema_current(
        "class_teacher_work",
        database,
        migrations_dir=released_migrations,
        backup_dir=tmp_path / "released-backups",
        logger_override=logging.getLogger("test.class-teacher.ordinary-migration"),
    )
    before = database.read_bytes()
    service_factory = create_workspace_feature().service_factory
    assert callable(service_factory)

    with pytest.raises(VaultError) as blocked:
        service_factory(context)

    assert blocked.value.code == "class_teacher_work_initialization_failed"
    assert database.read_bytes() == before
    with sqlite3.connect(database) as connection:
        pending_drop_ran = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name = 'daily_week_anchor'"
        ).fetchone()
    assert pending_drop_ran is not None


def test_plaintext_mode_still_requires_anonymous_preview_confirmation_before_model_send(
    tmp_path: Path,
) -> None:
    gateway = FakeApprovedModelGateway(result="合成草案")
    service = VaultService(_context(tmp_path), model_gateway=gateway)

    preview = service.model_approval.prepare(
        token="",
        purpose="student_support_note",
        source_text="张三同学本次数学 88分，希望调整作业节奏",
    )

    assert preview["exact_payload"]["task_text"] == (
        "学生A本次数学 [已移除具体数值]，希望调整作业节奏"
    )
    assert gateway.calls == []

    service.model_approval.confirm(
        token="",
        preview_id=str(preview["preview_id"]),
        fingerprint=str(preview["fingerprint"]),
        operation_id="plaintext-model-confirm-001",
    )

    assert len(gateway.calls) == 1
    assert gateway.calls[0]["payload"] == preview["exact_payload"]


@pytest.mark.parametrize("with_wal", [False, True])
def test_legacy_database_is_never_opened_or_changed_by_normal_runtime(
    tmp_path: Path,
    with_wal: bool,
) -> None:
    context = _context(tmp_path)
    keeper = _seed_classification_case(context, legacy=True, with_wal=with_wal)
    before = _tree_snapshot(context.root)
    try:
        assert EncryptedDatabase(context).has_unsupported_storage_format() is True
        feature = create_workspace_feature()
        assert feature.migration_provider(context) is None

        service = feature.service_factory(context)
        app = FastAPI()
        app.state.workspace_services = {"class-teacher": service}
        app.include_router(create_router(), prefix="/api/class-teacher")
        client = TestClient(app)
        headers = {"x-class-teacher-client": "class-teacher-browser-v1"}
        with pytest.raises(ApiError) as conversation_blocked:
            client.post(
                "/api/class-teacher/intake/conversations",
                headers=headers,
            )
        with pytest.raises(ApiError) as calendar_blocked:
            client.get("/api/class-teacher/work?as_of=2026-08-03")
        assert conversation_blocked.value.code == (
            "class_teacher_database_format_unsupported"
        )
        assert calendar_blocked.value.code == (
            "class_teacher_database_format_unsupported"
        )
        assert not service.ordinary_database.exists
        with pytest.raises(VaultError) as blocked:
            service.support.create_subject(
                token="",
                operation_id="legacy-write-must-stop-001",
                source_student_id="SYNTHETIC-001",
                display_name="合成学生甲",
                class_label="合成班",
            )
        assert blocked.value.code == "class_teacher_database_format_unsupported"
        assert _tree_snapshot(context.root) == before
    finally:
        if keeper is not None:
            keeper.close()


def test_plaintext_wal_is_classified_without_touching_live_companion_files(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    keeper = _seed_classification_case(context, legacy=False, with_wal=True)
    before = _tree_snapshot(context.root)
    try:
        database = EncryptedDatabase(context)
        assert database.has_unsupported_storage_format() is False
        assert _tree_snapshot(context.root) == before
    finally:
        assert keeper is not None
        keeper.close()


@pytest.mark.parametrize("damage", ["invalid_payload", "missing_history"])
def test_damaged_or_unknown_database_is_rejected_without_changes(
    tmp_path: Path,
    damage: str,
) -> None:
    context = _context(tmp_path)
    seeded = VaultService(context)
    seeded.ensure_plaintext_ready()
    database_path = seeded.database.database_path
    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.execute("PRAGMA journal_mode = DELETE")
        if damage == "invalid_payload":
            _insert_marker(
                connection,
                object_id="damaged-plaintext-payload",
                payload_nonce=b"plaintext-json-v1",
            )
            connection.execute(
                "UPDATE encrypted_objects SET payload_ciphertext = ? "
                "WHERE object_id = ?",
                (b"\x80", "damaged-plaintext-payload"),
            )
        else:
            connection.execute("DELETE FROM schema_migrations")
        connection.commit()

    before = _tree_snapshot(context.root)
    database = EncryptedDatabase(context)
    assert database.has_unsupported_storage_format() is True
    feature = create_workspace_feature()
    assert feature.migration_provider(context) is None
    service = feature.service_factory(context)
    with pytest.raises(VaultError) as blocked:
        service.ensure_plaintext_ready()
    assert blocked.value.code == "class_teacher_database_format_unsupported"
    assert not service.ordinary_database.exists
    assert _tree_snapshot(context.root) == before


def test_unsupported_database_skips_ordinary_initialization_and_ai_registration(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    seeded = VaultService(context)
    seeded.ordinary_database.initialize_schema()
    keeper = _seed_classification_case(context, legacy=True, with_wal=True)
    before = _tree_snapshot(context.root)
    try:
        feature = create_workspace_feature()
        service = feature.service_factory(context)

        registered: list[str] = []

        class Registrar:
            def register_adapter(self, task_kind: str, _adapter: object) -> None:
                registered.append(task_kind)

        assert feature.register_ai_tasks is not None
        feature.register_ai_tasks(Registrar(), service)

        assert registered == []
        assert _tree_snapshot(context.root) == before
    finally:
        assert keeper is not None
        keeper.close()
