from __future__ import annotations

from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.class_teacher.errors import VaultError
from backend.class_teacher.protection import FakeCurrentUserProtection
from backend.class_teacher.vault_service import VaultService
from backend.workspaces.contracts import WorkspaceContext


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LEGACY_PASSWORD = "合成旧版保险箱密码-足够长-001"
PROVIDER_KEY = b"K" * 32


def _service(
    tmp_path: Path,
    *,
    provider_key: bytes = PROVIDER_KEY,
) -> VaultService:
    context = WorkspaceContext(
        module_id="class-teacher",
        root=tmp_path / "workspaces" / "class-teacher",
        paths=SimpleNamespace(
            project_root=PROJECT_ROOT,
            migration_project_root=PROJECT_ROOT,
        ),
    )
    return VaultService(
        context,
        protection_provider=FakeCurrentUserProtection(provider_key),
    )


def test_new_vault_uses_six_digit_pin_and_current_user_binding(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    initialized = service.initialize_pin(
        pin="482615",
        operation_id="pin-initialize-001",
    )
    recovery_key = str(initialized["recovery_key"])
    service.lock(str(initialized["session_token"]))

    restarted = _service(tmp_path)
    status = restarted.status()
    unlocked = restarted.unlock_pin(pin="482615")

    assert status["protection_mode"] == "pin_dpapi_current_user_v2"
    assert status["protection_state"] == "active"
    assert unlocked["session_token"]
    assert unlocked["recovery_key"] == recovery_key
    assert b"482615" not in restarted.ordinary_database.database_path.read_bytes()
    assert b"482615" not in restarted.database.database_path.read_bytes()


def test_pin_format_is_rejected_before_any_workspace_file_is_created(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    with pytest.raises(VaultError) as error:
        service.initialize_pin(pin="12345a", operation_id="pin-invalid-format")

    assert error.value.code == "vault_pin_invalid_format"
    assert not service.database.root.exists()


def test_wrong_pin_and_different_current_user_never_rewrite_vault(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    initialized = service.initialize_pin(
        pin="482615",
        operation_id="pin-initialize-binding",
    )
    service.lock(str(initialized["session_token"]))
    protected_fields = (
        "password_salt",
        "password_nonce",
        "wrapped_vmk_password",
        "recovery_salt",
        "recovery_nonce",
        "wrapped_vmk_recovery",
    )
    with closing(service.database.connect()) as connection:
        metadata = connection.execute(
            "SELECT * FROM vault_metadata WHERE singleton_id = 1"
        ).fetchone()
    before = tuple(bytes(metadata[field]) for field in protected_fields)

    with pytest.raises(VaultError) as different_user:
        _service(tmp_path, provider_key=b"D" * 32).unlock_pin(
            pin="482615"
        )
    with pytest.raises(VaultError) as wrong_pin:
        _service(tmp_path).unlock_pin(pin="111111")
    status = _service(tmp_path).status()
    with pytest.raises(VaultError) as delayed:
        _service(tmp_path).unlock_pin(pin="111111")

    assert wrong_pin.value.code == "vault_pin_invalid"
    assert different_user.value.code == "vault_windows_binding_unavailable"
    assert int(status["retry_after_seconds"]) > 0
    assert delayed.value.code == "vault_access_delayed"
    with closing(service.database.connect()) as connection:
        metadata = connection.execute(
            "SELECT * FROM vault_metadata WHERE singleton_id = 1"
        ).fetchone()
    after = tuple(bytes(metadata[field]) for field in protected_fields)
    assert after == before


def test_recovery_key_rebinds_to_new_pin_without_real_data(tmp_path: Path) -> None:
    service = _service(tmp_path)
    initialized = service.initialize_pin(
        pin="482615",
        operation_id="pin-initialize-recovery",
    )
    recovery_key = str(initialized["recovery_key"])
    service.lock(str(initialized["session_token"]))

    recovered = service.recover_pin(
        recovery_key=recovery_key,
        new_pin="739204",
        operation_id="pin-recover-001",
    )
    service.lock(str(recovered["session_token"]))

    restarted = _service(tmp_path)
    new_session = restarted.unlock_pin(pin="739204")["session_token"]
    restarted.lock(str(new_session))
    with pytest.raises(VaultError):
        _service(tmp_path).unlock_pin(pin="482615")


def test_existing_long_password_vault_stays_legacy_without_auto_upgrade(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    initialized = service.initialize(
        password=LEGACY_PASSWORD,
        operation_id="legacy-initialize-001",
    )
    service.lock(str(initialized["session_token"]))

    restarted = _service(tmp_path)
    status = restarted.status()

    assert status["protection_mode"] == "legacy_password_v1"
    assert status["legacy_upgrade_available"] is True
    assert restarted.unlock(password=LEGACY_PASSWORD)["session_token"]
    assert not restarted.ordinary_database.exists
    with pytest.raises(VaultError) as pin_error:
        restarted.unlock_pin(pin="482615")
    assert pin_error.value.code == "vault_legacy_password_required"


def test_legacy_password_vault_explicitly_upgrades_to_pin(tmp_path: Path) -> None:
    service = _service(tmp_path)
    initialized = service.initialize(
        password=LEGACY_PASSWORD,
        operation_id="legacy-upgrade-initialize",
    )
    old_token = str(initialized["session_token"])

    result = service.upgrade_legacy_to_pin(
        token=old_token,
        current_password=LEGACY_PASSWORD,
        new_pin="482615",
        operation_id="legacy-upgrade-to-pin",
    )

    assert result == {"completed": True, "locked": True}
    with pytest.raises(VaultError) as expired:
        service.session_key(old_token)
    assert expired.value.code == "vault_locked"
    restarted = _service(tmp_path)
    assert restarted.status()["protection_mode"] == "pin_dpapi_current_user_v2"
    new_token = str(restarted.unlock_pin(pin="482615")["session_token"])
    restarted.lock(new_token)
    with pytest.raises(VaultError):
        _service(tmp_path).unlock(password=LEGACY_PASSWORD)


def test_wrong_legacy_password_leaves_existing_unlock_unchanged(tmp_path: Path) -> None:
    service = _service(tmp_path)
    initialized = service.initialize(
        password=LEGACY_PASSWORD,
        operation_id="legacy-upgrade-wrong-initialize",
    )

    with pytest.raises(VaultError) as error:
        service.upgrade_legacy_to_pin(
            token=str(initialized["session_token"]),
            current_password="错误的旧密码",
            new_pin="482615",
            operation_id="legacy-upgrade-wrong-password",
        )

    assert error.value.code == "vault_access_denied"
    restarted = _service(tmp_path)
    assert restarted.status()["protection_mode"] == "legacy_password_v1"
    assert restarted.status()["protection_state"] is None
    assert restarted.unlock(password=LEGACY_PASSWORD)["session_token"]


def test_interrupted_legacy_upgrade_resumes_with_new_pin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    initialized = service.initialize(
        password=LEGACY_PASSWORD,
        operation_id="legacy-upgrade-resume-initialize",
    )
    original_activate = service.pin_protection.activate

    def interrupted_activate() -> None:
        raise RuntimeError("synthetic interruption after password rewrap")

    monkeypatch.setattr(service.pin_protection, "activate", interrupted_activate)
    with pytest.raises(RuntimeError):
        service.upgrade_legacy_to_pin(
            token=str(initialized["session_token"]),
            current_password=LEGACY_PASSWORD,
            new_pin="482615",
            operation_id="legacy-upgrade-interrupted",
        )

    monkeypatch.setattr(service.pin_protection, "activate", original_activate)
    assert service.status()["protection_state"] == "pending"
    resumed = service.upgrade_legacy_to_pin(
        token=str(initialized["session_token"]),
        current_password=LEGACY_PASSWORD,
        new_pin="482615",
        operation_id="legacy-upgrade-interrupted",
    )

    assert resumed == {"completed": True, "locked": True}
    assert _service(tmp_path).status()["protection_state"] == "active"
    assert _service(tmp_path).unlock_pin(pin="482615")["session_token"]
