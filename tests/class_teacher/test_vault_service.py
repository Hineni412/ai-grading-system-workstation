from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.class_teacher.errors import VaultError
from backend.class_teacher.vault_service import VaultService
from backend.workspaces.contracts import WorkspaceContext


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _service(tmp_path: Path) -> VaultService:
    return VaultService(
        WorkspaceContext(
            module_id="class-teacher",
            root=tmp_path / "workspaces" / "class-teacher",
            paths=SimpleNamespace(
                project_root=PROJECT_ROOT,
                migration_project_root=PROJECT_ROOT,
            ),
        )
    )


def _checkpoint(path: Path) -> None:
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def _insert_legacy_ciphertext_marker(service: VaultService) -> None:
    service.ensure_plaintext_ready()
    with closing(service.database.connect()) as connection:
        with connection:
            connection.execute(
                """
                INSERT INTO encrypted_objects (
                    object_id, object_type, format_version, cek_nonce, wrapped_cek,
                    payload_nonce, payload_ciphertext, revision, created_at, updated_at
                ) VALUES ('legacy-row', 'synthetic', 1, X'01', X'02', X'03', X'04',
                          1, '2026-01-01T00:00:00+00:00',
                          '2026-01-01T00:00:00+00:00')
                """
            )
    _checkpoint(service.database.database_path)


def test_service_construction_has_no_filesystem_side_effects(tmp_path: Path) -> None:
    service = _service(tmp_path)

    assert not service.database.root.exists()


def test_plaintext_ready_creates_only_plaintext_compatibility_rows(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    key = service.ensure_plaintext_ready()

    with closing(service.database.connect()) as connection:
        with connection:
            revision = service.repository.put(
                connection,
                vmk=key,
                object_id="synthetic-object",
                object_type="synthetic",
                payload={"text": "合成明文"},
            )
        metadata_count = connection.execute(
            "SELECT COUNT(*) FROM vault_metadata"
        ).fetchone()[0]
        row = connection.execute(
            "SELECT cek_nonce, wrapped_cek, payload_nonce, payload_ciphertext "
            "FROM encrypted_objects WHERE object_id='synthetic-object'"
        ).fetchone()

    assert revision == 1
    assert metadata_count == 0
    assert bytes(row[0]) == b""
    assert bytes(row[1]) == b""
    assert bytes(row[2]) == b"plaintext-json-v1"
    assert "合成明文" in bytes(row[3]).decode("utf-8")
    assert service.ensure_plaintext_ready() == key


def test_runtime_exposes_no_retired_protection_operations(tmp_path: Path) -> None:
    service = _service(tmp_path)

    retired = {
        "status",
        "session_key",
        "initialize",
        "initialize_pin",
        "unlock",
        "unlock_pin",
        "recover",
        "recover_pin",
        "upgrade_legacy_to_pin",
        "lock",
        "touch",
        "change_pin",
        "change_password",
        "acknowledge_recovery_key",
    }
    assert not any(hasattr(service, name) for name in retired)
    assert not hasattr(service, "pin_protection")


def test_legacy_database_is_classified_read_only_and_never_changed(
    tmp_path: Path,
) -> None:
    seeded = _service(tmp_path)
    _insert_legacy_ciphertext_marker(seeded)
    database_path = seeded.database.database_path
    before = database_path.read_bytes()

    restarted = _service(tmp_path)

    assert restarted.prepare_existing_plaintext_runtime() is False
    with pytest.raises(VaultError) as blocked:
        restarted.ensure_plaintext_ready()
    assert blocked.value.code == "vault_plaintext_migration_required"
    assert database_path.read_bytes() == before


def test_constructor_defers_plaintext_recovery_until_explicit_prepare(
    tmp_path: Path,
) -> None:
    seeded = _service(tmp_path)
    seeded.ensure_plaintext_ready()
    candidate = seeded.database.root / ".database-replacement-candidate.db"
    candidate.write_bytes(b"synthetic-interrupted-candidate")

    restarted = _service(tmp_path)

    assert candidate.read_bytes() == b"synthetic-interrupted-candidate"
    assert restarted.prepare_existing_plaintext_runtime() is True
    assert not candidate.exists()


def test_explicit_plaintext_recovery_restores_valid_rollback_snapshot(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    first = service.support.create_subject(
        token="",
        operation_id="plaintext-recovery-first-001",
        source_student_id="SYNTHETIC-001",
        display_name="合成学生甲",
        class_label="合成班",
    )
    snapshot = service.database.snapshot_bytes()
    service.support.create_subject(
        token="",
        operation_id="plaintext-recovery-second-001",
        source_student_id="SYNTHETIC-002",
        display_name="合成学生乙",
        class_label="合成班",
    )
    rollback = service.database.root / ".restore-rollback.db"
    service.database._write_snapshot_file(snapshot, rollback)

    restarted = _service(tmp_path)
    assert rollback.is_file()
    assert restarted.prepare_existing_plaintext_runtime() is True

    items = restarted.support.list_subjects(token="")["items"]
    assert [item["subject_id"] for item in items] == [first["subject_id"]]
    assert not rollback.exists()
