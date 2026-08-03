from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.class_teacher.errors import VaultError
from backend.class_teacher.protection import FakeCurrentUserProtection
from backend.class_teacher.vault_service import VaultService
from backend.workspaces.contracts import WorkspaceContext


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PIN = "246810"
NEW_PIN = "135790"
BACKUP_PASSWORD = "合成专用备份密码-足够长-001"


def _service(tmp_path: Path, provider: FakeCurrentUserProtection) -> VaultService:
    return VaultService(
        WorkspaceContext(
            module_id="class-teacher",
            root=tmp_path / "workspaces" / "class-teacher",
            paths=SimpleNamespace(
                project_root=PROJECT_ROOT,
                migration_project_root=PROJECT_ROOT,
            ),
        ),
        protection_provider=provider,
    )


def _initialized(
    tmp_path: Path,
) -> tuple[VaultService, FakeCurrentUserProtection, str]:
    provider = FakeCurrentUserProtection(b"V" * 32)
    service = _service(tmp_path, provider)
    initialized = service.initialize_pin(
        pin=PIN,
        operation_id="vault-admin-init-001",
    )
    return service, provider, str(initialized["session_token"])


def test_status_exposes_server_authoritative_lease_without_revealing_secrets(
    tmp_path: Path,
) -> None:
    service, _provider, token = _initialized(tmp_path)

    status = service.status(token)

    assert status["locked"] is False
    assert 0 < int(status["session_expires_in_seconds"]) <= 300
    assert status["status_observed_at"]
    assert status["lock_reason"] is None
    assert PIN not in repr(status)


def test_pin_change_locks_session_and_keeps_vault_recoverable(
    tmp_path: Path,
) -> None:
    service, provider, token = _initialized(tmp_path)

    changed = service.change_pin(
        token=token,
        current_pin=PIN,
        new_pin=NEW_PIN,
        operation_id="vault-pin-change-001",
    )

    assert changed == {"completed": True, "locked": True}
    assert service.status(token)["locked"] is True
    restarted = _service(tmp_path, provider)
    unlocked = restarted.unlock_pin(pin=NEW_PIN)
    assert restarted.status(str(unlocked["session_token"]))["locked"] is False
    with pytest.raises(VaultError) as unchanged:
        restarted.change_pin(
            token=str(unlocked["session_token"]),
            current_pin=NEW_PIN,
            new_pin=NEW_PIN,
            operation_id="vault-pin-change-002",
        )
    assert unchanged.value.code == "vault_pin_unchanged"


def test_restore_preview_is_complete_replace_and_becomes_stale_after_write(
    tmp_path: Path,
) -> None:
    service, _provider, token = _initialized(tmp_path)
    backup = service.create_backup(
        token=token,
        backup_password=BACKUP_PASSWORD,
        operation_id="vault-backup-create-001",
    )
    preview = service.preview_restore(
        token=token,
        file_name=str(backup["file_name"]),
        secret=BACKUP_PASSWORD,
        secret_kind="password",
    )

    assert preview["mode"] == "complete_replace"
    assert preview["will_replace_current"] is True
    assert preview["will_lock_after_confirm"] is True
    assert preview["backup_scope_counts"] == preview["current_scope_counts"]
    assert preview["confirmation_phrase"] == "确认完整替换班主任工作台"

    service.support.create_subject(
        token=token,
        operation_id="restore-stale-subject-001",
        source_student_id="synthetic-restore-stale-001",
        display_name="合成恢复学生",
        class_label="合成一班",
    )
    with pytest.raises(VaultError) as stale:
        service.confirm_restore(
            token=token,
            preview_token=str(preview["preview_token"]),
            operation_id="vault-restore-confirm-001",
            confirmation_phrase=str(preview["confirmation_phrase"]),
        )
    assert stale.value.code == "vault_restore_preview_stale"
