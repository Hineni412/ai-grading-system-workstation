from __future__ import annotations

import logging
import shutil
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from backend.class_teacher.api import create_router
from backend.class_teacher.feature import _create_service, create_workspace_feature
from backend.class_teacher.model_approval import FakeApprovedModelGateway
from backend.class_teacher.vault_service import VaultService
from backend.class_teacher.errors import VaultError
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


def test_production_workbench_is_open_and_persists_student_content_as_plaintext(
    tmp_path: Path,
) -> None:
    service = _create_service(_context(tmp_path))

    status = service.status()
    assert status["initialized"] is True
    assert status["locked"] is False
    assert status["idle_timeout_seconds"] == 0
    assert status["protection_mode"] == "plaintext_debug_v1"

    app = FastAPI()
    app.state.workspace_services = {"class-teacher": service}
    app.include_router(create_router(), prefix="/api/class-teacher")
    response = TestClient(app).get("/api/class-teacher/vault/status")
    assert response.status_code == 200
    assert response.json()["protection_mode"] == "plaintext_debug_v1"

    created = service.support.create_subject(
        token="",
        operation_id="plaintext-subject-create-001",
        source_student_id="SYNTHETIC-001",
        display_name="合成学生甲",
        class_label="合成班",
    )

    assert created["display_name"] == "合成学生甲"
    assert service.student_directory.search(token="")["items"][0]["display_name"] == "合成学生甲"
    assert service.affairs.list(token="")["items"] == []
    assert service.home_intake_finalizer is not None
    assert service.support_ai_reviews is not None
    with sqlite3.connect(service.database.database_path) as connection:
        payload = connection.execute(
            "SELECT payload_ciphertext FROM encrypted_objects WHERE object_type = 'student_subject'"
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
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(intake_conversations)")
        }
    assert "focused_subject_id" not in columns


def test_plaintext_mode_still_requires_anonymous_preview_confirmation_before_model_send(
    tmp_path: Path,
) -> None:
    gateway = FakeApprovedModelGateway(result="合成草案")
    service = VaultService(
        _context(tmp_path),
        model_gateway=gateway,
        protection_enabled=False,
    )

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


def test_plaintext_mode_stops_before_reading_or_writing_a_legacy_encrypted_vault(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    legacy = VaultService(context, model_gateway=FakeApprovedModelGateway())
    legacy.initialize(
        password="synthetic-password-123",
        operation_id="legacy-vault-initialize-001",
    )
    before = legacy.database.database_path.read_bytes()

    plaintext = VaultService(
        context,
        model_gateway=FakeApprovedModelGateway(),
        protection_enabled=False,
    )

    assert plaintext.status()["protection_mode"] == "legacy_migration_required"
    with pytest.raises(VaultError, match="授权迁移"):
        plaintext.support.create_subject(
            token="",
            operation_id="legacy-write-must-stop-001",
            source_student_id="SYNTHETIC-001",
            display_name="合成学生甲",
            class_label="合成班",
        )
    assert plaintext.database.database_path.read_bytes() == before
