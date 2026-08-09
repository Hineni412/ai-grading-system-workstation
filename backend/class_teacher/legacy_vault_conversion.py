from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from .crypto import (
    FORMAT_VERSION,
    SCRYPT_N,
    SCRYPT_P,
    SCRYPT_R,
    canonical_json,
    derive_key,
    open_sealed,
    unb64,
    wipe,
)
from .errors import VaultError, VaultIntegrityError
from .windows_dpapi import WindowsCurrentUserProtection


CredentialKind = Literal["password", "pin", "recovery_key"]

_PLAINTEXT_MARKER = b"plaintext-json-v1"
_PIN_MODE = "pin_dpapi_current_user_v2"
_PIN = re.compile(r"[0-9]{6}")
_PIN_AAD = b"class-teacher|sensitive-vault-secret|pin-dpapi-current-user|v2"
_DPAPI_ENTROPY = b"class-teacher|current-user|v2"
_DATABASE_COMPANIONS = ("", "-wal", "-shm", "-journal")
_COPIED_COMPANIONS = ("", "-wal", "-journal")
_TEMPORARY_OWNER_NAME = ".legacy-vault-conversion-owner"
_TEMPORARY_OWNER_CONTENT = b"class-teacher-legacy-vault-conversion-v1\n"
_TEMPORARY_DATABASE_NAMES = ("source.db", "protection.db", "converted.db")


class CurrentUserUnprotector(Protocol):
    def unprotect_current_user(self, protected: bytes, entropy: bytes) -> bytes: ...


class LegacyVaultConversionError(RuntimeError):
    """A safe conversion failure that never includes secrets or row content."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)


@dataclass(frozen=True, slots=True)
class LegacyVaultConversionResult:
    output_path: Path
    converted_objects: int
    source_sha256: str
    output_sha256: str


@dataclass(frozen=True, slots=True)
class _BundleEntry:
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class _LegacyObjectIdentity:
    object_id: str
    object_type: str
    format_version: int
    revision: int
    created_at: str
    updated_at: str


def convert_legacy_vault(
    source_database: Path,
    output_database: Path,
    *,
    credential_kind: CredentialKind,
    credential: str,
    protection_database: Path | None = None,
    current_user_unprotector: CurrentUserUnprotector | None = None,
) -> LegacyVaultConversionResult:
    """Convert one legacy class-teacher database into a new plaintext copy.

    The source database and, for PIN conversion, its ordinary protection
    database are copied as raw, stable bundles before SQLite opens anything.
    Only the private copies are recovered or queried.  The final output must be
    outside the source workspace and must not already exist.
    """

    source, output, protection = _resolve_paths(
        source_database,
        output_database,
        credential_kind=credential_kind,
        protection_database=protection_database,
    )
    source_before = _bundle_state(source)
    protection_before = _bundle_state(protection) if protection is not None else None
    source_digest = _bundle_digest(source_before)

    temporary_root = _create_temporary_root(output)
    identities: tuple[_LegacyObjectIdentity, ...] = ()
    expected_output_digest = ""
    actual_output_digest = ""
    published = False
    try:
        staged_source = temporary_root / "source.db"
        _copy_stable_bundle(source, staged_source, source_before)

        staged_protection = None
        if protection is not None and protection_before is not None:
            staged_protection = temporary_root / "protection.db"
            _copy_stable_bundle(
                protection,
                staged_protection,
                protection_before,
            )

        candidate = temporary_root / "converted.db"
        vmk_buffer: bytearray | None = None
        try:
            with closing(_connect(staged_source)) as source_connection:
                _validate_integrity(source_connection, source=True)
                metadata, rows, identities = _inspect_legacy_source(
                    source_connection
                )
                vmk_buffer = bytearray(
                    _unlock_vmk(
                        metadata,
                        credential_kind=credential_kind,
                        credential=str(credential),
                        staged_protection=staged_protection,
                        current_user_unprotector=current_user_unprotector,
                    )
                )
                with closing(_connect(candidate)) as candidate_connection:
                    source_connection.backup(candidate_connection)
                    _rewrite_candidate(
                        candidate_connection,
                        rows=rows,
                        identities=identities,
                        vmk=bytes(vmk_buffer),
                    )
            _fsync_file(candidate)
            expected_output_digest = _sha256_file(candidate)
            _assert_input_bundles_unchanged(
                source,
                source_before,
                protection,
                protection_before,
            )
            _publish_without_overwrite(candidate, output)
            published = True
            actual_output_digest = _sha256_file(output)
            if actual_output_digest != expected_output_digest:
                _fail(
                    "legacy_conversion_publish_failed",
                    "输出路径在发布后发生变化；为避免误删其他文件，已停止转换",
                )
            _assert_input_bundles_unchanged(
                source,
                source_before,
                protection,
                protection_before,
            )
        except Exception:
            if published:
                try:
                    if (
                        output.is_file()
                        and _sha256_file(output) == expected_output_digest
                    ):
                        output.unlink()
                except (OSError, LegacyVaultConversionError):
                    pass
            raise
        finally:
            wipe(vmk_buffer)
    finally:
        try:
            _remove_owned_temporary_root(temporary_root)
        except Exception:
            if published:
                try:
                    if (
                        output.is_file()
                        and _sha256_file(output) == expected_output_digest
                    ):
                        output.unlink()
                except (OSError, LegacyVaultConversionError):
                    pass
            raise

    return LegacyVaultConversionResult(
        output_path=output,
        converted_objects=len(identities),
        source_sha256=source_digest,
        output_sha256=actual_output_digest,
    )


def _assert_input_bundles_unchanged(
    source: Path,
    expected_source: dict[str, _BundleEntry],
    protection: Path | None,
    expected_protection: dict[str, _BundleEntry] | None,
) -> None:
    if _bundle_state(source) != expected_source:
        _fail(
            "legacy_conversion_source_changed",
            "转换期间源数据库发生变化，输出结果不可使用",
        )
    if (
        protection is not None
        and expected_protection is not None
        and _bundle_state(protection) != expected_protection
    ):
        _fail(
            "legacy_conversion_protection_changed",
            "转换期间 PIN 保护数据库发生变化，输出结果不可使用",
        )


def _resolve_paths(
    source_database: Path,
    output_database: Path,
    *,
    credential_kind: CredentialKind,
    protection_database: Path | None,
) -> tuple[Path, Path, Path | None]:
    if credential_kind not in {"password", "pin", "recovery_key"}:
        _fail(
            "legacy_conversion_credential_kind_invalid",
            "请选择旧密码、旧 PIN 或恢复密钥中的一种转换方式",
        )
    try:
        source = Path(source_database).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise LegacyVaultConversionError(
            "legacy_conversion_source_missing",
            "没有找到指定的旧数据库",
        ) from exc
    if not source.is_file():
        _fail(
            "legacy_conversion_source_missing",
            "没有找到指定的旧数据库",
        )

    raw_output = Path(output_database)
    try:
        output_parent = raw_output.parent.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise LegacyVaultConversionError(
            "legacy_conversion_output_parent_missing",
            "输出目录不存在，请先选择一个工作区外的现有目录",
        ) from exc
    if not output_parent.is_dir():
        _fail(
            "legacy_conversion_output_parent_missing",
            "输出目录不存在，请先选择一个工作区外的现有目录",
        )
    output = output_parent / raw_output.name
    if output == source:
        _fail(
            "legacy_conversion_same_path",
            "输出文件不能覆盖旧数据库",
        )
    if _is_relative_to(output, source.parent):
        _fail(
            "legacy_conversion_output_inside_workspace",
            "输出文件必须放在旧数据库工作区之外",
        )

    protection = None
    if credential_kind == "pin":
        if protection_database is None:
            _fail(
                "legacy_conversion_protection_missing",
                "使用旧 PIN 转换时必须明确选择 PIN 保护数据库",
            )
        try:
            protection = Path(protection_database).resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise LegacyVaultConversionError(
                "legacy_conversion_protection_missing",
                "没有找到指定的 PIN 保护数据库",
            ) from exc
        if not protection.is_file():
            _fail(
                "legacy_conversion_protection_missing",
                "没有找到指定的 PIN 保护数据库",
            )
        try:
            same_protection_file = protection == source or protection.samefile(source)
        except OSError:
            same_protection_file = protection == source
        if same_protection_file:
            _fail(
                "legacy_conversion_protection_invalid",
                "旧加密库和 PIN 保护库必须是两个不同文件",
            )
        if _is_relative_to(output, protection.parent):
            _fail(
                "legacy_conversion_output_inside_workspace",
                "输出文件必须放在旧数据库工作区之外",
            )
    elif protection_database is not None:
        _fail(
            "legacy_conversion_protection_unexpected",
            "只有旧 PIN 转换需要 PIN 保护数据库",
        )
    if output.exists():
        try:
            if output.samefile(source):
                _fail(
                    "legacy_conversion_same_path",
                    "输出文件不能覆盖旧数据库",
                )
        except OSError:
            pass
        _fail(
            "legacy_conversion_output_exists",
            "输出文件已经存在；转换不会覆盖任何文件",
        )
    _cleanup_stale_temporary_root(output)
    return source, output, protection


def _temporary_root_for(output: Path) -> Path:
    return output.parent / f".{output.name}.legacy-vault-conversion.tmp"


def _is_reparse_point(path: Path, path_stat: os.stat_result) -> bool:
    if path.is_symlink():
        return True
    attributes = int(getattr(path_stat, "st_file_attributes", 0))
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
    return bool(reparse_flag and attributes & reparse_flag)


def _allowed_temporary_names() -> set[str]:
    names = {_TEMPORARY_OWNER_NAME}
    for database_name in _TEMPORARY_DATABASE_NAMES:
        names.update(f"{database_name}{suffix}" for suffix in _DATABASE_COMPANIONS)
    return names


def _remove_owned_temporary_root(temporary_root: Path) -> None:
    """Remove only the converter's exact, marked, flat temporary directory."""

    try:
        root_stat = temporary_root.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise LegacyVaultConversionError(
            "legacy_conversion_temporary_cleanup_failed",
            "无法检查上次转换的临时文件；没有生成输出文件",
        ) from exc
    if not stat.S_ISDIR(root_stat.st_mode) or _is_reparse_point(
        temporary_root, root_stat
    ):
        _fail(
            "legacy_conversion_temporary_unsafe",
            "转换专用临时路径已被其他文件占用；为避免误删，已停止转换",
        )

    owner = temporary_root / _TEMPORARY_OWNER_NAME
    try:
        owner_stat = owner.lstat()
    except FileNotFoundError:
        try:
            temporary_root.rmdir()
        except OSError as exc:
            raise LegacyVaultConversionError(
                "legacy_conversion_temporary_unsafe",
                "转换专用临时目录没有所有权标记；为避免误删，已停止转换",
            ) from exc
        return
    except OSError as exc:
        raise LegacyVaultConversionError(
            "legacy_conversion_temporary_cleanup_failed",
            "无法检查上次转换的临时文件；没有生成输出文件",
        ) from exc
    if (
        not stat.S_ISREG(owner_stat.st_mode)
        or _is_reparse_point(owner, owner_stat)
    ):
        _fail(
            "legacy_conversion_temporary_unsafe",
            "转换专用临时目录的所有权标记无效；为避免误删，已停止转换",
        )
    try:
        owner_content = owner.read_bytes()
    except OSError as exc:
        raise LegacyVaultConversionError(
            "legacy_conversion_temporary_cleanup_failed",
            "无法读取上次转换的临时文件；没有生成输出文件",
        ) from exc
    # A power loss can leave the exclusively-created marker empty or with a
    # prefix of its fixed bytes.  Such a prefix is still attributable to this
    # exact converter-owned path; arbitrary or extended content is not.
    if not _TEMPORARY_OWNER_CONTENT.startswith(owner_content):
        _fail(
            "legacy_conversion_temporary_unsafe",
            "转换专用临时目录的所有权标记无效；为避免误删，已停止转换",
        )

    allowed_names = _allowed_temporary_names()
    try:
        children = list(temporary_root.iterdir())
        for child in children:
            child_stat = child.lstat()
            if (
                child.name not in allowed_names
                or not stat.S_ISREG(child_stat.st_mode)
                or _is_reparse_point(child, child_stat)
            ):
                _fail(
                    "legacy_conversion_temporary_unsafe",
                    "转换专用临时目录包含未知内容；为避免误删，已停止转换",
                )
        for child in children:
            if child != owner:
                child.unlink()
        owner.unlink()
        temporary_root.rmdir()
    except LegacyVaultConversionError:
        raise
    except OSError as exc:
        raise LegacyVaultConversionError(
            "legacy_conversion_temporary_cleanup_failed",
            "无法清理上次转换的临时文件；没有生成输出文件",
        ) from exc


def _cleanup_stale_temporary_root(output: Path) -> None:
    _remove_owned_temporary_root(_temporary_root_for(output))


def _create_temporary_root(output: Path) -> Path:
    temporary_root = _temporary_root_for(output)
    try:
        temporary_root.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise LegacyVaultConversionError(
            "legacy_conversion_temporary_unsafe",
            "转换专用临时路径已被占用；为避免误删，已停止转换",
        ) from exc
    except OSError as exc:
        raise LegacyVaultConversionError(
            "legacy_conversion_temporary_create_failed",
            "无法创建转换专用临时目录；旧数据库没有改变",
        ) from exc

    owner = temporary_root / _TEMPORARY_OWNER_NAME
    try:
        with owner.open("xb") as handle:
            handle.write(_TEMPORARY_OWNER_CONTENT)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        try:
            owner.unlink(missing_ok=True)
            temporary_root.rmdir()
        except OSError:
            pass
        raise LegacyVaultConversionError(
            "legacy_conversion_temporary_create_failed",
            "无法标记转换专用临时目录；旧数据库没有改变",
        ) from exc
    return temporary_root


def _bundle_state(path: Path | None) -> dict[str, _BundleEntry]:
    if path is None:
        return {}
    state: dict[str, _BundleEntry] = {}
    for suffix in _DATABASE_COMPANIONS:
        candidate = Path(f"{path}{suffix}")
        if not candidate.exists():
            continue
        if candidate.is_symlink() or not candidate.is_file():
            _fail(
                "legacy_conversion_source_unsafe",
                "旧数据库包含不受支持的链接或文件类型",
            )
        state[suffix] = _BundleEntry(
            size_bytes=int(candidate.stat().st_size),
            sha256=_sha256_file(candidate),
        )
    if "" not in state:
        _fail(
            "legacy_conversion_source_missing",
            "没有找到指定的旧数据库",
        )
    return state


def _copy_stable_bundle(
    source: Path,
    destination: Path,
    expected: dict[str, _BundleEntry],
) -> None:
    for suffix in _COPIED_COMPANIONS:
        if suffix not in expected:
            continue
        source_file = Path(f"{source}{suffix}")
        destination_file = Path(f"{destination}{suffix}")
        try:
            with source_file.open("rb") as input_handle, destination_file.open(
                "xb"
            ) as output_handle:
                shutil.copyfileobj(
                    input_handle,
                    output_handle,
                    length=1024 * 1024,
                )
                output_handle.flush()
                os.fsync(output_handle.fileno())
        except OSError as exc:
            raise LegacyVaultConversionError(
                "legacy_conversion_source_copy_failed",
                "无法创建旧数据库的只读转换副本",
            ) from exc
    if _bundle_state(source) != expected:
        _fail(
            "legacy_conversion_source_changed",
            "复制期间源数据库发生变化，请关闭应用后重新转换",
        )


def _connect(path: Path) -> sqlite3.Connection:
    try:
        connection = sqlite3.connect(path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection
    except sqlite3.DatabaseError as exc:
        raise LegacyVaultConversionError(
            "legacy_conversion_database_invalid",
            "旧数据库无法作为 SQLite 数据库打开",
        ) from exc


def _validate_integrity(
    connection: sqlite3.Connection,
    *,
    source: bool,
) -> None:
    try:
        row = connection.execute("PRAGMA integrity_check").fetchone()
        if row is None or str(row[0]).casefold() != "ok":
            raise ValueError("integrity")
    except (sqlite3.DatabaseError, ValueError) as exc:
        raise LegacyVaultConversionError(
            "legacy_conversion_source_invalid" if source else "legacy_conversion_output_invalid",
            "旧数据库未通过完整性校验"
            if source
            else "转换结果未通过完整性校验",
        ) from exc


def _inspect_legacy_source(
    connection: sqlite3.Connection,
) -> tuple[sqlite3.Row, list[sqlite3.Row], tuple[_LegacyObjectIdentity, ...]]:
    tables = _table_names(connection)
    if not {"vault_metadata", "encrypted_objects"}.issubset(tables):
        _fail(
            "legacy_conversion_schema_unsupported",
            "这不是受支持的旧班主任数据库",
        )
    _require_columns(
        connection,
        "vault_metadata",
        {
            "format_version",
            "kdf_n",
            "kdf_r",
            "kdf_p",
            "password_salt",
            "password_nonce",
            "wrapped_vmk_password",
            "recovery_salt",
            "recovery_nonce",
            "wrapped_vmk_recovery",
        },
    )
    _require_columns(
        connection,
        "encrypted_objects",
        {
            "object_id",
            "object_type",
            "format_version",
            "cek_nonce",
            "wrapped_cek",
            "payload_nonce",
            "payload_ciphertext",
            "revision",
            "created_at",
            "updated_at",
        },
    )
    metadata_rows = connection.execute("SELECT * FROM vault_metadata").fetchall()
    rows = connection.execute(
        "SELECT * FROM encrypted_objects ORDER BY object_id"
    ).fetchall()
    plaintext_count = sum(
        bytes(row["payload_nonce"]) == _PLAINTEXT_MARKER for row in rows
    )
    if not metadata_rows:
        if plaintext_count == len(rows):
            _fail(
                "legacy_conversion_already_plaintext",
                "所选数据库已经是明文格式，不需要再次转换",
            )
        _fail(
            "legacy_conversion_metadata_missing",
            "旧数据库缺少解密所需的元数据",
        )
    if len(metadata_rows) != 1:
        _fail(
            "legacy_conversion_metadata_invalid",
            "旧数据库的加密元数据不完整",
        )
    if plaintext_count:
        _fail(
            "legacy_conversion_mixed_format",
            "旧数据库混合了密文和明文，已停止转换",
        )
    metadata = metadata_rows[0]
    if int(metadata["format_version"]) != FORMAT_VERSION:
        _fail(
            "legacy_conversion_format_unsupported",
            "旧数据库使用了当前转换器不支持的加密版本",
        )
    if (
        int(metadata["kdf_n"]),
        int(metadata["kdf_r"]),
        int(metadata["kdf_p"]),
    ) != (SCRYPT_N, SCRYPT_R, SCRYPT_P):
        _fail(
            "legacy_conversion_kdf_unsupported",
            "旧数据库使用了当前转换器不支持的密钥参数",
        )

    identities: list[_LegacyObjectIdentity] = []
    for row in rows:
        if int(row["format_version"]) != FORMAT_VERSION:
            _fail(
                "legacy_conversion_format_unsupported",
                "旧数据库包含当前转换器不支持的对象版本",
            )
        if len(bytes(row["cek_nonce"])) != 12 or len(bytes(row["payload_nonce"])) != 12:
            _fail(
                "legacy_conversion_object_format_invalid",
                "旧数据库包含无法识别的加密对象",
            )
        identities.append(_identity(row))
    return metadata, rows, tuple(identities)


def _unlock_vmk(
    metadata: sqlite3.Row,
    *,
    credential_kind: CredentialKind,
    credential: str,
    staged_protection: Path | None,
    current_user_unprotector: CurrentUserUnprotector | None,
) -> bytes:
    if credential_kind == "password":
        return _unwrap_vmk(metadata, secret=credential, kind="password")
    if credential_kind == "recovery_key":
        return _unwrap_vmk(metadata, secret=credential, kind="recovery")
    if _PIN.fullmatch(credential) is None:
        _fail(
            "legacy_conversion_pin_format_invalid",
            "旧 PIN 必须是 6 位数字",
        )
    if staged_protection is None:
        _fail(
            "legacy_conversion_protection_missing",
            "使用旧 PIN 转换时必须明确选择 PIN 保护数据库",
        )
    provider = current_user_unprotector or WindowsCurrentUserProtection()
    return _unlock_vmk_from_pin(
        metadata,
        staged_protection,
        pin=credential,
        provider=provider,
    )


def _unwrap_vmk(
    metadata: sqlite3.Row,
    *,
    secret: str,
    kind: Literal["password", "recovery"],
) -> bytes:
    prefix = "password" if kind == "password" else "recovery"
    try:
        kek = derive_key(
            secret,
            bytes(metadata[f"{prefix}_salt"]),
            n=int(metadata["kdf_n"]),
            r=int(metadata["kdf_r"]),
            p=int(metadata["kdf_p"]),
        )
        return open_sealed(
            kek,
            bytes(metadata[f"{prefix}_nonce"]),
            bytes(metadata[f"wrapped_vmk_{prefix}"]),
            _vmk_aad(kind),
        )
    except (VaultIntegrityError, ValueError, TypeError) as exc:
        raise LegacyVaultConversionError(
            "legacy_conversion_credential_invalid",
            "转换凭据不正确，或者旧数据库的保护信息已损坏",
        ) from exc


def _unlock_vmk_from_pin(
    metadata: sqlite3.Row,
    protection_path: Path,
    *,
    pin: str,
    provider: CurrentUserUnprotector,
) -> bytes:
    try:
        with closing(_connect(protection_path)) as connection:
            _validate_integrity(connection, source=True)
            tables = _table_names(connection)
            candidates: list[sqlite3.Row] = []
            for table in ("sensitive_protection_pending", "sensitive_protection"):
                if table not in tables:
                    continue
                _require_columns(
                    connection,
                    table,
                    {"mode", "pin_salt", "protected_secret"},
                )
                row = connection.execute(
                    f"SELECT mode, pin_salt, protected_secret FROM {table} "
                    "WHERE singleton_id = 1"
                ).fetchone()
                if row is not None:
                    candidates.append(row)
    except LegacyVaultConversionError:
        raise
    except sqlite3.DatabaseError as exc:
        raise LegacyVaultConversionError(
            "legacy_conversion_protection_invalid",
            "PIN 保护数据库无法读取",
        ) from exc
    if not candidates:
        _fail(
            "legacy_conversion_protection_missing",
            "PIN 保护数据库中没有可用的旧 PIN 信息；可改用恢复密钥",
        )

    binding_failed = False
    for row in candidates:
        if str(row["mode"]) != _PIN_MODE:
            continue
        try:
            package_raw = provider.unprotect_current_user(
                bytes(row["protected_secret"]),
                _DPAPI_ENTROPY,
            )
        except VaultError as exc:
            if exc.code in {
                "vault_windows_binding_unavailable",
                "vault_windows_protection_unavailable",
            }:
                binding_failed = True
                continue
            raise LegacyVaultConversionError(
                "legacy_conversion_windows_binding_unavailable",
                "当前 Windows 用户无法读取旧 PIN；可改用恢复密钥",
            ) from exc
        except Exception:
            binding_failed = True
            continue
        try:
            package = json.loads(package_raw.decode("utf-8"))
            internal_secret = open_sealed(
                derive_key(pin, bytes(row["pin_salt"])),
                unb64(package["nonce"]),
                unb64(package["ciphertext"]),
                _PIN_AAD,
            ).decode("utf-8")
            return _unwrap_vmk(
                metadata,
                secret=internal_secret,
                kind="password",
            )
        except (
            KeyError,
            TypeError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            VaultIntegrityError,
            LegacyVaultConversionError,
        ):
            continue
    if binding_failed:
        _fail(
            "legacy_conversion_windows_binding_unavailable",
            "当前 Windows 用户无法读取旧 PIN；可改用恢复密钥",
        )
    _fail(
        "legacy_conversion_credential_invalid",
        "旧 PIN 不正确，或者 PIN 保护信息已损坏",
    )


def _rewrite_candidate(
    connection: sqlite3.Connection,
    *,
    rows: list[sqlite3.Row],
    identities: tuple[_LegacyObjectIdentity, ...],
    vmk: bytes,
) -> None:
    try:
        connection.execute("PRAGMA journal_mode = DELETE")
        connection.execute("PRAGMA secure_delete = ON")
        connection.execute("BEGIN IMMEDIATE")
        for row in rows:
            object_id = str(row["object_id"])
            object_type = str(row["object_type"])
            cek = open_sealed(
                vmk,
                bytes(row["cek_nonce"]),
                bytes(row["wrapped_cek"]),
                _object_aad(object_type, object_id, "cek"),
            )
            payload_raw = open_sealed(
                cek,
                bytes(row["payload_nonce"]),
                bytes(row["payload_ciphertext"]),
                _object_aad(object_type, object_id, "payload"),
            )
            payload = json.loads(payload_raw.decode("utf-8"))
            if not isinstance(payload, dict):
                raise TypeError("payload")
            changed = connection.execute(
                """
                UPDATE encrypted_objects
                SET cek_nonce = ?, wrapped_cek = ?, payload_nonce = ?,
                    payload_ciphertext = ?
                WHERE object_id = ?
                """,
                (
                    b"",
                    b"",
                    _PLAINTEXT_MARKER,
                    canonical_json(payload),
                    object_id,
                ),
            ).rowcount
            if changed != 1:
                raise ValueError("object_update")
        connection.execute("DELETE FROM vault_metadata")
        if "initialization_recovery_receipts" in _table_names(connection):
            connection.execute("DELETE FROM initialization_recovery_receipts")
        connection.commit()
    except Exception as exc:
        connection.rollback()
        if isinstance(exc, LegacyVaultConversionError):
            raise
        raise LegacyVaultConversionError(
            "legacy_conversion_object_invalid",
            "旧数据库中的加密对象未通过校验，未生成输出文件",
        ) from exc
    _validate_plaintext_candidate(connection, identities)


def _validate_plaintext_candidate(
    connection: sqlite3.Connection,
    identities: tuple[_LegacyObjectIdentity, ...],
) -> None:
    _validate_integrity(connection, source=False)
    foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
    if foreign_key_errors:
        _fail(
            "legacy_conversion_output_invalid",
            "转换结果未通过关联完整性校验",
        )
    if connection.execute("SELECT COUNT(*) FROM vault_metadata").fetchone()[0] != 0:
        _fail(
            "legacy_conversion_output_invalid",
            "转换结果仍包含旧加密元数据",
        )
    if "initialization_recovery_receipts" in _table_names(connection):
        if (
            connection.execute(
                "SELECT COUNT(*) FROM initialization_recovery_receipts"
            ).fetchone()[0]
            != 0
        ):
            _fail(
                "legacy_conversion_output_invalid",
                "转换结果仍包含旧恢复凭据",
            )
    rows = connection.execute(
        "SELECT * FROM encrypted_objects ORDER BY object_id"
    ).fetchall()
    if tuple(_identity(row) for row in rows) != identities:
        _fail(
            "legacy_conversion_output_invalid",
            "转换改变了业务对象的身份或版本信息",
        )
    for row in rows:
        if (
            bytes(row["cek_nonce"]) != b""
            or bytes(row["wrapped_cek"]) != b""
            or bytes(row["payload_nonce"]) != _PLAINTEXT_MARKER
        ):
            _fail(
                "legacy_conversion_output_invalid",
                "转换结果仍包含旧加密对象",
            )
        try:
            payload = json.loads(bytes(row["payload_ciphertext"]).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LegacyVaultConversionError(
                "legacy_conversion_output_invalid",
                "转换结果中的明文对象无法读取",
            ) from exc
        if not isinstance(payload, dict):
            _fail(
                "legacy_conversion_output_invalid",
                "转换结果中的明文对象格式无效",
            )


def _table_names(connection: sqlite3.Connection) -> set[str]:
    try:
        return {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    except sqlite3.DatabaseError as exc:
        raise LegacyVaultConversionError(
            "legacy_conversion_schema_unsupported",
            "无法读取旧数据库结构",
        ) from exc


def _require_columns(
    connection: sqlite3.Connection,
    table: str,
    required: set[str],
) -> None:
    try:
        columns = {
            str(row[1])
            for row in connection.execute(f"PRAGMA table_info({table})")
        }
    except sqlite3.DatabaseError as exc:
        raise LegacyVaultConversionError(
            "legacy_conversion_schema_unsupported",
            "旧数据库结构无法读取",
        ) from exc
    if not required.issubset(columns):
        _fail(
            "legacy_conversion_schema_unsupported",
            "旧数据库结构不完整，无法安全转换",
        )


def _identity(row: sqlite3.Row) -> _LegacyObjectIdentity:
    return _LegacyObjectIdentity(
        object_id=str(row["object_id"]),
        object_type=str(row["object_type"]),
        format_version=int(row["format_version"]),
        revision=int(row["revision"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _vmk_aad(kind: str) -> bytes:
    return f"class-teacher|vault|vmk|{kind}|v{FORMAT_VERSION}".encode()


def _object_aad(object_type: str, object_id: str, field: str) -> bytes:
    return (
        f"class-teacher|{object_type}|{object_id}|{field}|v{FORMAT_VERSION}"
    ).encode("utf-8")


def _publish_without_overwrite(candidate: Path, output: Path) -> None:
    if output.exists():
        _fail(
            "legacy_conversion_output_exists",
            "输出文件已经存在；转换不会覆盖任何文件",
        )
    try:
        # candidate and output are deliberately on the same filesystem.  A
        # hard link publishes the already-fsynced inode atomically and, unlike
        # rename on POSIX, can never replace a destination created by a race.
        os.link(candidate, output)
    except FileExistsError as exc:
        raise LegacyVaultConversionError(
            "legacy_conversion_output_exists",
            "输出文件已经存在；转换不会覆盖任何文件",
        ) from exc
    except OSError as exc:
        raise LegacyVaultConversionError(
            "legacy_conversion_publish_failed",
            "无法安全发布转换结果，旧数据库没有改变",
        ) from exc


def _fsync_file(path: Path) -> None:
    try:
        # Windows rejects FlushFileBuffers for a read-only handle.  This is the
        # converter-owned candidate, so opening it without truncation for
        # read/write is safe and leaves its bytes unchanged.
        with path.open("r+b") as handle:
            os.fsync(handle.fileno())
    except OSError as exc:
        raise LegacyVaultConversionError(
            "legacy_conversion_publish_failed",
            "转换结果无法安全写入磁盘，未发布输出文件",
        ) from exc


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise LegacyVaultConversionError(
            "legacy_conversion_file_unreadable",
            "数据库文件无法读取",
        ) from exc
    return digest.hexdigest()


def _bundle_digest(state: dict[str, _BundleEntry]) -> str:
    digest = hashlib.sha256()
    for suffix, entry in sorted(state.items()):
        digest.update(suffix.encode("utf-8"))
        digest.update(str(entry.size_bytes).encode("ascii"))
        digest.update(entry.sha256.encode("ascii"))
    return digest.hexdigest()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _fail(code: str, message: str) -> None:
    raise LegacyVaultConversionError(code, message)


__all__ = [
    "CredentialKind",
    "CurrentUserUnprotector",
    "LegacyVaultConversionError",
    "LegacyVaultConversionResult",
    "convert_legacy_vault",
]
