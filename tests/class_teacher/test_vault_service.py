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
PASSWORD = "合成保险箱密码-足够长-001"
NEW_PASSWORD = "合成保险箱新密码-足够长-002"
BACKUP_PASSWORD = "合成备份独立密码-足够长-003"


def _service(tmp_path: Path) -> VaultService:
    paths = SimpleNamespace(
        project_root=PROJECT_ROOT,
        migration_project_root=PROJECT_ROOT,
    )
    return VaultService(
        WorkspaceContext(
            module_id="class-teacher",
            root=tmp_path / "workspaces" / "class-teacher",
            paths=paths,
        )
    )


def _vmk(service: VaultService, token: str) -> bytes:
    return bytes(service._require_session(token).vmk)


def test_service_has_no_filesystem_side_effect_before_explicit_initialization(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    assert service.status()["initialized"] is False
    assert not service.database.root.exists()


def test_initialize_encrypts_keys_and_requires_short_lived_session(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    result = service.initialize(password=PASSWORD, operation_id="initialize-001")

    assert result["recovery_key_shown_once"] is True
    assert service.status(str(result["session_token"]))["locked"] is False
    raw = service.database.database_path.read_bytes()
    assert PASSWORD.encode("utf-8") not in raw
    assert str(result["recovery_key"]).encode("utf-8") not in raw

    service.lock(str(result["session_token"]))
    assert service.status(str(result["session_token"]))["locked"] is True


def test_unacknowledged_recovery_key_survives_restart_until_teacher_acknowledges(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    initialized = service.initialize(
        password=PASSWORD,
        operation_id="initialize-recovery-receipt",
    )
    recovery_key = str(initialized["recovery_key"])
    service.lock(str(initialized["session_token"]))

    restarted = _service(tmp_path)
    unlocked = restarted.unlock(password=PASSWORD)
    assert unlocked["recovery_key"] == recovery_key
    restarted.acknowledge_recovery_key(
        token=str(unlocked["session_token"])
    )
    restarted.lock(str(unlocked["session_token"]))

    after_acknowledgement = _service(tmp_path).unlock(password=PASSWORD)
    assert after_acknowledgement["recovery_key"] is None


def test_password_change_rewraps_unacknowledged_recovery_receipt(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    initialized = service.initialize(
        password=PASSWORD,
        operation_id="initialize-recovery-rewrap",
    )
    recovery_key = str(initialized["recovery_key"])
    service.change_password(
        token=str(initialized["session_token"]),
        current_password=PASSWORD,
        new_password=NEW_PASSWORD,
        operation_id="change-password-before-recovery-ack",
    )

    unlocked = _service(tmp_path).unlock(password=NEW_PASSWORD)
    assert unlocked["recovery_key"] == recovery_key


def test_wrong_password_delays_retry_without_deleting_data(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.initialize(password=PASSWORD, operation_id="initialize-002")

    with pytest.raises(VaultError, match="密码或受保护数据"):
        service.unlock(password="错误密码也写得很长-000")
    with pytest.raises(VaultError) as blocked:
        service.unlock(password=PASSWORD)

    assert blocked.value.code == "vault_access_delayed"
    assert service.database.database_path.exists()


def test_sensitive_repository_never_writes_plaintext_payload(tmp_path: Path) -> None:
    service = _service(tmp_path)
    initialized = service.initialize(
        password=PASSWORD,
        operation_id="initialize-003",
    )
    token = str(initialized["session_token"])
    synthetic = "合成学生甲-仅用于B01密文检查"

    with closing(service.database.connect()) as connection:
        with connection:
            revision = service.repository.put(
                connection,
                vmk=_vmk(service, token),
                object_id="syn-object-001",
                object_type="synthetic_note",
                payload={"body": synthetic},
            )
        payload, loaded_revision = service.repository.get(
            connection,
            vmk=_vmk(service, token),
            object_id="syn-object-001",
        )

    assert revision == loaded_revision == 1
    assert payload == {"body": synthetic}
    assert synthetic.encode("utf-8") not in service.database.database_path.read_bytes()


def test_backup_is_authenticated_and_recovery_key_can_open_old_backup(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    initialized = service.initialize(
        password=PASSWORD,
        operation_id="initialize-004",
    )
    token = str(initialized["session_token"])
    recovery_key = str(initialized["recovery_key"])
    backup = service.create_backup(
        token=token,
        backup_password=BACKUP_PASSWORD,
        operation_id="backup-create-001",
    )

    by_password = service.verify_backup(
        file_name=str(backup["file_name"]),
        secret=BACKUP_PASSWORD,
        secret_kind="password",
    )
    by_recovery = service.verify_backup(
        file_name=str(backup["file_name"]),
        secret=recovery_key,
        secret_kind="recovery_key",
    )

    assert by_password["backup_id"] == by_recovery["backup_id"]
    assert service.create_backup(
        token=token,
        backup_password=BACKUP_PASSWORD,
        operation_id="backup-create-001",
    ) == backup


def test_restore_is_previewed_then_replaces_and_locks(tmp_path: Path) -> None:
    service = _service(tmp_path)
    initialized = service.initialize(
        password=PASSWORD,
        operation_id="initialize-005",
    )
    token = str(initialized["session_token"])
    with closing(service.database.connect()) as connection:
        with connection:
            service.repository.put(
                connection,
                vmk=_vmk(service, token),
                object_id="syn-object-restore",
                object_type="synthetic_note",
                payload={"body": "备份时版本"},
            )
    backup = service.create_backup(
        token=token,
        backup_password=BACKUP_PASSWORD,
        operation_id="backup-create-restore",
    )
    with closing(service.database.connect()) as connection:
        with connection:
            service.repository.put(
                connection,
                vmk=_vmk(service, token),
                object_id="syn-object-restore",
                object_type="synthetic_note",
                payload={"body": "恢复前已修改版本"},
                expected_revision=1,
            )

    preview = service.preview_restore(
        token=token,
        file_name=str(backup["file_name"]),
        secret=BACKUP_PASSWORD,
        secret_kind="password",
    )
    result = service.confirm_restore(
        token=token,
        preview_token=str(preview["preview_token"]),
        operation_id="restore-confirm-001",
    )

    assert result["locked"] is True
    unlocked = service.unlock(password=PASSWORD)
    with closing(service.database.connect()) as connection:
        payload, revision = service.repository.get(
            connection,
            vmk=_vmk(service, str(unlocked["session_token"])),
            object_id="syn-object-restore",
        )
    assert payload == {"body": "备份时版本"}
    assert revision == 1
    assert not (
        service.database.root / ".restore-rollback.db"
    ).exists()
    assert not (
        service.database.root / ".restore-rollback.db.tmp"
    ).exists()


@pytest.mark.parametrize("temporary_is_complete", [False, True])
def test_startup_discards_unpublished_rollback_temporary_file(
    tmp_path: Path,
    temporary_is_complete: bool,
) -> None:
    service = _service(tmp_path)
    initialized = service.initialize(
        password=PASSWORD,
        operation_id=(
            "initialize-unpublished-rollback-"
            + ("complete" if temporary_is_complete else "partial")
        ),
    )
    token = str(initialized["session_token"])
    with closing(service.database.connect()) as connection:
        with connection:
            service.repository.put(
                connection,
                vmk=_vmk(service, token),
                object_id="syn-object-unpublished-rollback",
                object_type="synthetic_note",
                payload={"body": "原在线库保持可用"},
            )
    temporary = (
        service.database.root / ".restore-rollback.db.tmp"
    )
    temporary.write_bytes(
        service.database.snapshot_bytes()
        if temporary_is_complete
        else b"incomplete rollback"
    )

    restarted = _service(tmp_path)
    unlocked = restarted.unlock(password=PASSWORD)
    with closing(restarted.database.connect()) as connection:
        payload, revision = restarted.repository.get(
            connection,
            vmk=_vmk(restarted, str(unlocked["session_token"])),
            object_id="syn-object-unpublished-rollback",
        )

    assert payload == {"body": "原在线库保持可用"}
    assert revision == 1
    assert not temporary.exists()
    assert not (
        restarted.database.root / ".restore-rollback.db"
    ).exists()


def test_startup_rolls_back_snapshot_left_after_atomic_replace(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    initialized = service.initialize(
        password=PASSWORD,
        operation_id="initialize-stale-rollback",
    )
    token = str(initialized["session_token"])
    with closing(service.database.connect()) as connection:
        with connection:
            service.repository.put(
                connection,
                vmk=_vmk(service, token),
                object_id="syn-object-stale-rollback",
                object_type="synthetic_note",
                payload={"body": "替换前版本"},
            )
    old_snapshot = service.database.snapshot_bytes()
    with closing(service.database.connect()) as connection:
        with connection:
            service.repository.put(
                connection,
                vmk=_vmk(service, token),
                object_id="syn-object-stale-rollback",
                object_type="synthetic_note",
                payload={"body": "尚未提交的替换版本"},
                expected_revision=1,
            )
    rollback = service.database.root / ".restore-rollback.db"
    service.database._write_snapshot_file(old_snapshot, rollback)

    restarted = _service(tmp_path)
    unlocked = restarted.unlock(password=PASSWORD)
    with closing(restarted.database.connect()) as connection:
        payload, revision = restarted.repository.get(
            connection,
            vmk=_vmk(restarted, str(unlocked["session_token"])),
            object_id="syn-object-stale-rollback",
        )

    assert payload == {"body": "替换前版本"}
    assert revision == 1
    assert not rollback.exists()


def test_startup_recovers_when_live_database_is_missing_mid_replace(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    initialized = service.initialize(
        password=PASSWORD,
        operation_id="initialize-missing-live-recovery",
    )
    token = str(initialized["session_token"])
    with closing(service.database.connect()) as connection:
        with connection:
            service.repository.put(
                connection,
                vmk=_vmk(service, token),
                object_id="syn-object-missing-live",
                object_type="synthetic_note",
                payload={"body": "必须恢复的版本"},
            )
    old_snapshot = service.database.snapshot_bytes()
    rollback = service.database.root / ".restore-rollback.db"
    candidate = service.database.root / ".restore-candidate.db"
    service.database._write_snapshot_file(old_snapshot, rollback)
    service.database._write_snapshot_file(old_snapshot, candidate)
    service.database.database_path.unlink()

    restarted = _service(tmp_path)
    unlocked = restarted.unlock(password=PASSWORD)
    with closing(restarted.database.connect()) as connection:
        payload, revision = restarted.repository.get(
            connection,
            vmk=_vmk(restarted, str(unlocked["session_token"])),
            object_id="syn-object-missing-live",
        )

    assert payload == {"body": "必须恢复的版本"}
    assert revision == 1
    assert not rollback.exists()
    assert not candidate.exists()


def test_change_password_invalidates_old_sessions(tmp_path: Path) -> None:
    service = _service(tmp_path)
    initialized = service.initialize(
        password=PASSWORD,
        operation_id="initialize-006",
    )
    token = str(initialized["session_token"])

    service.change_password(
        token=token,
        current_password=PASSWORD,
        new_password=NEW_PASSWORD,
        operation_id="change-password-001",
    )

    assert service.status(token)["locked"] is True
    with pytest.raises(VaultError):
        service.unlock(password=PASSWORD)
    with closing(sqlite3.connect(service.database.database_path)) as connection:
        with connection:
            connection.execute(
                """
                UPDATE vault_metadata
                SET failed_attempts = 0, blocked_until = NULL
                WHERE singleton_id = 1
                """
            )
    assert service.unlock(password=NEW_PASSWORD)["session_token"]
