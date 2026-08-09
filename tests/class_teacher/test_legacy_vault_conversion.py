from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

from backend.class_teacher.legacy_vault_conversion import (
    LegacyVaultConversionError,
    convert_legacy_vault,
)
from backend.class_teacher.secure_repository import EncryptedObjectRepository


sys.path.insert(0, str(Path(__file__).resolve().parent))
from legacy_vault_fixture import (  # noqa: E402
    SyntheticCurrentUserProtection,
    create_legacy_vault,
)


_MARKER = b"plaintext-json-v1"


def _output_path(tmp_path: Path, name: str = "converted.db") -> Path:
    staging = tmp_path / "conversion-output"
    staging.mkdir(exist_ok=True)
    return staging / name


def _tree_digest(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _source_identities(path: Path) -> list[tuple[object, ...]]:
    with sqlite3.connect(path) as connection:
        return connection.execute(
            """
            SELECT object_id, object_type, format_version, revision,
                   created_at, updated_at
            FROM encrypted_objects ORDER BY object_id
            """
        ).fetchall()


def _assert_plaintext_output(path: Path, expected: dict[str, dict[str, object]]) -> None:
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert not EncryptedObjectRepository.requires_plaintext_migration(connection)
        assert connection.execute("SELECT COUNT(*) FROM vault_metadata").fetchone()[0] == 0
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM initialization_recovery_receipts"
            ).fetchone()[0]
            == 0
        )
        rows = connection.execute(
            "SELECT * FROM encrypted_objects ORDER BY object_id"
        ).fetchall()
    assert len(rows) == len(expected)
    for row in rows:
        assert bytes(row["cek_nonce"]) == b""
        assert bytes(row["wrapped_cek"]) == b""
        assert bytes(row["payload_nonce"]) == _MARKER
        assert json.loads(bytes(row["payload_ciphertext"]).decode("utf-8")) == expected[
            str(row["object_id"])
        ]


def test_password_conversion_creates_new_verified_copy_and_preserves_source_tree(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "legacy-workspace"
    legacy = create_legacy_vault(workspace)
    before_tree = _tree_digest(workspace)
    before_identities = _source_identities(legacy.source)
    output = _output_path(tmp_path)

    result = convert_legacy_vault(
        legacy.source,
        output,
        credential_kind="password",
        credential=legacy.password,
    )

    assert result.output_path == output.resolve()
    assert result.converted_objects == len(legacy.payloads)
    assert result.output_sha256 == hashlib.sha256(output.read_bytes()).hexdigest()
    assert _tree_digest(workspace) == before_tree
    assert _source_identities(output) == before_identities
    _assert_plaintext_output(output, legacy.payloads)
    with sqlite3.connect(output) as connection:
        connection.row_factory = sqlite3.Row
        payload, revision = EncryptedObjectRepository().get(
            connection,
            vmk=b"",
            object_id="student-001",
        )
    assert payload == legacy.payloads["student-001"]
    assert revision == 3
    assert [path.name for path in output.parent.iterdir()] == [output.name]


@pytest.mark.parametrize("pin_state", ["active", "pending", "both"])
def test_pin_conversion_reads_active_and_pending_packages_without_changing_either_source(
    tmp_path: Path,
    pin_state: str,
) -> None:
    workspace = tmp_path / f"legacy-pin-{pin_state}"
    legacy = create_legacy_vault(workspace, pin_state=pin_state)  # type: ignore[arg-type]
    before_tree = _tree_digest(workspace)
    output = _output_path(tmp_path, f"pin-{pin_state}.db")

    result = convert_legacy_vault(
        legacy.source,
        output,
        credential_kind="pin",
        credential=legacy.pin,
        protection_database=legacy.protection_database,
        current_user_unprotector=legacy.provider,
    )

    assert result.converted_objects == 2
    assert _tree_digest(workspace) == before_tree
    _assert_plaintext_output(output, legacy.payloads)


def test_pin_conversion_falls_back_from_invalid_pending_package_to_active(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "legacy-pin-fallback"
    legacy = create_legacy_vault(workspace, pin_state="both")
    assert legacy.protection_database is not None
    with sqlite3.connect(legacy.protection_database) as connection:
        connection.execute(
            "UPDATE sensitive_protection_pending SET protected_secret = ?",
            (b"invalid-pending-package",),
        )
        connection.commit()
    before_tree = _tree_digest(workspace)
    output = _output_path(tmp_path, "pin-active-fallback.db")

    convert_legacy_vault(
        legacy.source,
        output,
        credential_kind="pin",
        credential=legacy.pin,
        protection_database=legacy.protection_database,
        current_user_unprotector=legacy.provider,
    )

    assert _tree_digest(workspace) == before_tree
    _assert_plaintext_output(output, legacy.payloads)


def test_recovery_key_conversion_needs_no_pin_database(tmp_path: Path) -> None:
    workspace = tmp_path / "legacy-recovery"
    legacy = create_legacy_vault(workspace, pin_state="active")
    before_tree = _tree_digest(workspace)
    output = _output_path(tmp_path, "recovered.db")

    convert_legacy_vault(
        legacy.source,
        output,
        credential_kind="recovery_key",
        credential=legacy.recovery_key,
    )

    assert _tree_digest(workspace) == before_tree
    _assert_plaintext_output(output, legacy.payloads)


def test_read_only_bundle_copy_includes_committed_wal_without_touching_sidecars(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "legacy-wal"
    legacy = create_legacy_vault(workspace)
    output = _output_path(tmp_path, "wal.db")

    with sqlite3.connect(legacy.source) as live_connection:
        assert live_connection.execute("PRAGMA journal_mode = WAL").fetchone()[0] == "wal"
        live_connection.execute("CREATE TABLE wal_only_marker (value TEXT NOT NULL)")
        live_connection.execute(
            "INSERT INTO wal_only_marker (value) VALUES ('committed-in-wal')"
        )
        live_connection.commit()
        before_tree = _tree_digest(workspace)

        convert_legacy_vault(
            legacy.source,
            output,
            credential_kind="password",
            credential=legacy.password,
        )

        assert _tree_digest(workspace) == before_tree
        with sqlite3.connect(output) as converted:
            assert (
                converted.execute("SELECT value FROM wal_only_marker").fetchone()[0]
                == "committed-in-wal"
            )
    _assert_plaintext_output(output, legacy.payloads)


@pytest.mark.parametrize(
    ("kind", "credential"),
    [
        ("password", "错误旧密码"),
        ("recovery_key", "CTRK-WRONG-RECOVERY-KEY"),
    ],
)
def test_wrong_password_or_recovery_key_fails_without_output_or_source_change(
    tmp_path: Path,
    kind: str,
    credential: str,
) -> None:
    workspace = tmp_path / f"wrong-{kind}"
    legacy = create_legacy_vault(workspace)
    before_tree = _tree_digest(workspace)
    output = _output_path(tmp_path, f"wrong-{kind}.db")

    with pytest.raises(LegacyVaultConversionError) as error:
        convert_legacy_vault(
            legacy.source,
            output,
            credential_kind=kind,  # type: ignore[arg-type]
            credential=credential,
        )

    assert error.value.code == "legacy_conversion_credential_invalid"
    assert not output.exists()
    assert _tree_digest(workspace) == before_tree
    assert list(output.parent.iterdir()) == []


def test_wrong_pin_and_different_windows_user_fail_without_writes(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "wrong-pin"
    legacy = create_legacy_vault(workspace, pin_state="both")
    before_tree = _tree_digest(workspace)

    wrong_pin_output = _output_path(tmp_path, "wrong-pin.db")
    with pytest.raises(LegacyVaultConversionError) as wrong_pin:
        convert_legacy_vault(
            legacy.source,
            wrong_pin_output,
            credential_kind="pin",
            credential="111111",
            protection_database=legacy.protection_database,
            current_user_unprotector=legacy.provider,
        )
    assert wrong_pin.value.code == "legacy_conversion_credential_invalid"

    wrong_user_output = _output_path(tmp_path, "wrong-user.db")
    with pytest.raises(LegacyVaultConversionError) as wrong_user:
        convert_legacy_vault(
            legacy.source,
            wrong_user_output,
            credential_kind="pin",
            credential=legacy.pin,
            protection_database=legacy.protection_database,
            current_user_unprotector=SyntheticCurrentUserProtection(b"X" * 32),
        )
    assert wrong_user.value.code == "legacy_conversion_windows_binding_unavailable"
    assert not wrong_pin_output.exists()
    assert not wrong_user_output.exists()
    assert _tree_digest(workspace) == before_tree


def test_mixed_plaintext_and_encrypted_rows_fail_closed(tmp_path: Path) -> None:
    workspace = tmp_path / "mixed"
    legacy = create_legacy_vault(workspace, mixed_plaintext=True)
    before_tree = _tree_digest(workspace)
    output = _output_path(tmp_path)

    with pytest.raises(LegacyVaultConversionError) as error:
        convert_legacy_vault(
            legacy.source,
            output,
            credential_kind="password",
            credential=legacy.password,
        )

    assert error.value.code == "legacy_conversion_mixed_format"
    assert not output.exists()
    assert _tree_digest(workspace) == before_tree


@pytest.mark.parametrize(
    ("fixture_kwargs", "expected_code"),
    [
        ({"format_version": 2}, "legacy_conversion_format_unsupported"),
        ({"kdf_n": 16384}, "legacy_conversion_kdf_unsupported"),
    ],
)
def test_unknown_format_or_kdf_is_rejected_before_decryption(
    tmp_path: Path,
    fixture_kwargs: dict[str, int],
    expected_code: str,
) -> None:
    workspace = tmp_path / expected_code
    legacy = create_legacy_vault(workspace, **fixture_kwargs)
    output = _output_path(tmp_path, f"{expected_code}.db")

    with pytest.raises(LegacyVaultConversionError) as error:
        convert_legacy_vault(
            legacy.source,
            output,
            credential_kind="password",
            credential=legacy.password,
        )

    assert error.value.code == expected_code
    assert not output.exists()


def test_corrupt_encrypted_object_rolls_back_candidate(tmp_path: Path) -> None:
    workspace = tmp_path / "corrupt-object"
    legacy = create_legacy_vault(workspace)
    with sqlite3.connect(legacy.source) as connection:
        connection.execute(
            "UPDATE encrypted_objects SET payload_ciphertext = ? WHERE object_id = 'note-001'",
            (b"corrupt",),
        )
        connection.commit()
    before_tree = _tree_digest(workspace)
    output = _output_path(tmp_path)

    with pytest.raises(LegacyVaultConversionError) as error:
        convert_legacy_vault(
            legacy.source,
            output,
            credential_kind="password",
            credential=legacy.password,
        )

    assert error.value.code == "legacy_conversion_object_invalid"
    assert not output.exists()
    assert _tree_digest(workspace) == before_tree
    assert list(output.parent.iterdir()) == []


def test_existing_same_or_workspace_output_is_never_replaced(tmp_path: Path) -> None:
    workspace = tmp_path / "path-guards"
    legacy = create_legacy_vault(workspace)
    before_tree = _tree_digest(workspace)

    with pytest.raises(LegacyVaultConversionError) as same:
        convert_legacy_vault(
            legacy.source,
            legacy.source,
            credential_kind="password",
            credential=legacy.password,
        )
    assert same.value.code == "legacy_conversion_same_path"

    existing = _output_path(tmp_path, "existing.db")
    existing.write_bytes(b"keep-existing-output")
    with pytest.raises(LegacyVaultConversionError) as exists:
        convert_legacy_vault(
            legacy.source,
            existing,
            credential_kind="password",
            credential=legacy.password,
        )
    assert exists.value.code == "legacy_conversion_output_exists"
    assert existing.read_bytes() == b"keep-existing-output"

    inside = workspace / "converted.db"
    with pytest.raises(LegacyVaultConversionError) as inside_error:
        convert_legacy_vault(
            legacy.source,
            inside,
            credential_kind="password",
            credential=legacy.password,
        )
    assert inside_error.value.code == "legacy_conversion_output_inside_workspace"
    assert _tree_digest(workspace) == before_tree


def test_interruption_before_publish_leaves_no_output_or_temporary_plaintext(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.class_teacher import legacy_vault_conversion as conversion

    workspace = tmp_path / "interrupted"
    legacy = create_legacy_vault(workspace)
    before_tree = _tree_digest(workspace)
    output = _output_path(tmp_path)

    def interrupted(_candidate: Path, _output: Path) -> None:
        raise RuntimeError("synthetic interruption before publication")

    monkeypatch.setattr(conversion, "_publish_without_overwrite", interrupted)
    with pytest.raises(RuntimeError, match="synthetic interruption"):
        convert_legacy_vault(
            legacy.source,
            output,
            credential_kind="password",
            credential=legacy.password,
        )

    assert not output.exists()
    assert list(output.parent.iterdir()) == []
    assert _tree_digest(workspace) == before_tree


def test_publish_race_never_replaces_competing_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.class_teacher import legacy_vault_conversion as conversion

    workspace = tmp_path / "publish-race"
    legacy = create_legacy_vault(workspace)
    before_tree = _tree_digest(workspace)
    output = _output_path(tmp_path, "race.db")
    competing_content = b"created-by-competing-process"
    real_link = conversion.os.link

    def competing_link(candidate: Path, destination: Path) -> None:
        destination.write_bytes(competing_content)
        real_link(candidate, destination)

    monkeypatch.setattr(conversion.os, "link", competing_link)
    with pytest.raises(LegacyVaultConversionError) as error:
        convert_legacy_vault(
            legacy.source,
            output,
            credential_kind="password",
            credential=legacy.password,
        )

    assert error.value.code == "legacy_conversion_output_exists"
    assert output.read_bytes() == competing_content
    assert _tree_digest(workspace) == before_tree
    assert [path.name for path in output.parent.iterdir()] == [output.name]


def test_post_publish_path_replacement_is_preserved_on_validation_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.class_teacher import legacy_vault_conversion as conversion

    workspace = tmp_path / "post-publish-race"
    legacy = create_legacy_vault(workspace)
    before_tree = _tree_digest(workspace)
    output = _output_path(tmp_path, "post-link-race.db")
    competing_content = b"replacement-owned-by-competing-process"
    real_link = conversion.os.link

    def replacing_link(candidate: Path, destination: Path) -> None:
        real_link(candidate, destination)
        destination.unlink()
        destination.write_bytes(competing_content)

    monkeypatch.setattr(conversion.os, "link", replacing_link)
    with pytest.raises(LegacyVaultConversionError) as error:
        convert_legacy_vault(
            legacy.source,
            output,
            credential_kind="password",
            credential=legacy.password,
        )

    assert error.value.code == "legacy_conversion_publish_failed"
    assert output.read_bytes() == competing_content
    assert _tree_digest(workspace) == before_tree
    assert [path.name for path in output.parent.iterdir()] == [output.name]


@pytest.mark.parametrize("owner_bytes", [b"", b"class-teacher", None])
def test_next_run_removes_only_owned_deterministic_temporary_root(
    tmp_path: Path,
    owner_bytes: bytes | None,
) -> None:
    from backend.class_teacher import legacy_vault_conversion as conversion

    workspace = tmp_path / "stale-owned-temporary"
    legacy = create_legacy_vault(workspace)
    before_tree = _tree_digest(workspace)
    output = _output_path(tmp_path, "stale.db")
    temporary_root = conversion._temporary_root_for(output)
    temporary_root.mkdir()
    (temporary_root / conversion._TEMPORARY_OWNER_NAME).write_bytes(
        conversion._TEMPORARY_OWNER_CONTENT
        if owner_bytes is None
        else owner_bytes
    )
    (temporary_root / "converted.db").write_bytes(b"stale-plaintext-candidate")

    convert_legacy_vault(
        legacy.source,
        output,
        credential_kind="password",
        credential=legacy.password,
    )

    assert not temporary_root.exists()
    assert _tree_digest(workspace) == before_tree
    _assert_plaintext_output(output, legacy.payloads)


def test_unowned_deterministic_temporary_root_is_preserved_and_rejected(
    tmp_path: Path,
) -> None:
    from backend.class_teacher import legacy_vault_conversion as conversion

    workspace = tmp_path / "stale-unowned-temporary"
    legacy = create_legacy_vault(workspace)
    before_tree = _tree_digest(workspace)
    output = _output_path(tmp_path, "unowned.db")
    temporary_root = conversion._temporary_root_for(output)
    temporary_root.mkdir()
    unknown = temporary_root / "not-created-by-converter.txt"
    unknown.write_bytes(b"must-not-delete")

    with pytest.raises(LegacyVaultConversionError) as error:
        convert_legacy_vault(
            legacy.source,
            output,
            credential_kind="password",
            credential=legacy.password,
        )

    assert error.value.code == "legacy_conversion_temporary_unsafe"
    assert unknown.read_bytes() == b"must-not-delete"
    assert not output.exists()
    assert _tree_digest(workspace) == before_tree


def test_cleanup_failure_rolls_back_published_output_and_next_run_recovers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.class_teacher import legacy_vault_conversion as conversion

    workspace = tmp_path / "cleanup-failure"
    legacy = create_legacy_vault(workspace)
    before_tree = _tree_digest(workspace)
    output = _output_path(tmp_path, "cleanup-failure.db")
    real_remove = conversion._remove_owned_temporary_root
    calls = 0

    def fail_final_cleanup(temporary_root: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            real_remove(temporary_root)
            return
        raise LegacyVaultConversionError(
            "legacy_conversion_temporary_cleanup_failed",
            "synthetic safe cleanup failure",
        )

    monkeypatch.setattr(
        conversion,
        "_remove_owned_temporary_root",
        fail_final_cleanup,
    )
    with pytest.raises(LegacyVaultConversionError) as error:
        convert_legacy_vault(
            legacy.source,
            output,
            credential_kind="password",
            credential=legacy.password,
        )

    assert error.value.code == "legacy_conversion_temporary_cleanup_failed"
    assert not output.exists()
    assert _tree_digest(workspace) == before_tree

    monkeypatch.setattr(
        conversion,
        "_remove_owned_temporary_root",
        real_remove,
    )
    convert_legacy_vault(
        legacy.source,
        output,
        credential_kind="password",
        credential=legacy.password,
    )
    assert _tree_digest(workspace) == before_tree
    _assert_plaintext_output(output, legacy.payloads)


def test_successful_output_cannot_be_repeated_or_changed(tmp_path: Path) -> None:
    from backend.class_teacher import legacy_vault_conversion as conversion

    workspace = tmp_path / "repeat"
    legacy = create_legacy_vault(workspace)
    output = _output_path(tmp_path)
    convert_legacy_vault(
        legacy.source,
        output,
        credential_kind="password",
        credential=legacy.password,
    )
    before = hashlib.sha256(output.read_bytes()).hexdigest()
    temporary_root = conversion._temporary_root_for(output)
    temporary_root.mkdir()
    (temporary_root / conversion._TEMPORARY_OWNER_NAME).write_bytes(
        conversion._TEMPORARY_OWNER_CONTENT
    )
    leftover = temporary_root / "source.db"
    leftover.write_bytes(b"owned-but-not-part-of-this-repeated-run")
    temporary_before = _tree_digest(temporary_root)

    with pytest.raises(LegacyVaultConversionError) as repeated:
        convert_legacy_vault(
            legacy.source,
            output,
            credential_kind="password",
            credential=legacy.password,
        )

    assert repeated.value.code == "legacy_conversion_output_exists"
    assert hashlib.sha256(output.read_bytes()).hexdigest() == before
    assert _tree_digest(temporary_root) == temporary_before


def test_cli_reads_secret_with_hidden_prompt_and_never_prints_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from tools import convert_legacy_class_teacher_db as cli

    workspace = tmp_path / "cli"
    legacy = create_legacy_vault(workspace)
    output = _output_path(tmp_path, "cli.db")
    prompts: list[str] = []

    def hidden(prompt: str) -> str:
        prompts.append(prompt)
        return legacy.password

    monkeypatch.setattr(cli.getpass, "getpass", hidden)
    exit_code = cli.main(
        [
            "--source",
            str(legacy.source),
            "--output",
            str(output),
            "--credential-kind",
            "password",
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert prompts == ["请输入旧模块密码："]
    assert legacy.password not in captured.out
    assert legacy.password not in captured.err
    assert "合成学生甲" not in captured.out
    assert "仅用于旧库转换测试" not in captured.out
    assert json.loads(captured.out)["completed"] is True
    _assert_plaintext_output(output, legacy.payloads)


def test_cli_failure_does_not_print_secret_path_or_object_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from tools import convert_legacy_class_teacher_db as cli

    workspace = tmp_path / "cli-failure"
    legacy = create_legacy_vault(workspace)
    output = _output_path(tmp_path, "cli-failure.db")
    wrong_secret = "wrong-secret-that-must-never-be-printed"
    monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt: wrong_secret)

    exit_code = cli.main(
        [
            "--source",
            str(legacy.source),
            "--output",
            str(output),
            "--credential-kind",
            "password",
        ]
    )
    captured = capsys.readouterr()
    combined = f"{captured.out}\n{captured.err}"

    assert exit_code == 1
    assert wrong_secret not in combined
    assert str(legacy.source) not in combined
    assert "合成学生甲" not in combined
    assert "仅用于旧库转换测试" not in combined
    assert json.loads(captured.err)["code"] == "legacy_conversion_credential_invalid"
    assert not output.exists()
