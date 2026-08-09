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
