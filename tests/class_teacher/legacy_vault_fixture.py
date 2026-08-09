from __future__ import annotations

import base64
import json
import secrets
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from backend.class_teacher.errors import VaultError


FORMAT_VERSION = 1
SCRYPT_N = 32768
SCRYPT_R = 8
SCRYPT_P = 1
_PIN_AAD = b"class-teacher|sensitive-vault-secret|pin-dpapi-current-user|v2"
_DPAPI_ENTROPY = b"class-teacher|current-user|v2"
_PLAINTEXT_MARKER = b"plaintext-json-v1"


@dataclass(frozen=True, slots=True)
class _Wrapped:
    nonce: bytes
    ciphertext: bytes


class SyntheticCurrentUserProtection:
    def __init__(self, key: bytes) -> None:
        self.key = bytes(key)

    def protect_current_user(self, plaintext: bytes, entropy: bytes) -> bytes:
        nonce = secrets.token_bytes(12)
        return nonce + AESGCM(self.key).encrypt(nonce, plaintext, entropy)

    def unprotect_current_user(self, protected: bytes, entropy: bytes) -> bytes:
        try:
            return AESGCM(self.key).decrypt(
                protected[:12],
                protected[12:],
                entropy,
            )
        except (InvalidTag, ValueError) as exc:
            raise VaultError(
                "vault_windows_binding_unavailable",
                "synthetic current user mismatch",
                status_code=409,
            ) from exc


@dataclass(frozen=True, slots=True)
class SyntheticLegacyVault:
    source: Path
    protection_database: Path | None
    password: str
    pin: str
    recovery_key: str
    provider: SyntheticCurrentUserProtection
    payloads: dict[str, dict[str, object]]


def create_legacy_vault(
    root: Path,
    *,
    pin_state: Literal["active", "pending", "both"] | None = None,
    mixed_plaintext: bool = False,
    format_version: int = FORMAT_VERSION,
    kdf_n: int = SCRYPT_N,
) -> SyntheticLegacyVault:
    root.mkdir(parents=True, exist_ok=True)
    source = root / "student_affairs.db"
    protection_database = (
        root / "class_teacher_work.db" if pin_state is not None else None
    )
    password = "合成旧模块密码-足够长-001"
    internal_password = "synthetic-internal-pin-secret-001"
    pin = "482615"
    recovery_key = "CTRK-SYNTHETIC-RECOVERY-KEY-001"
    provider = SyntheticCurrentUserProtection(b"P" * 32)
    vmk = b"V" * 32
    password_secret = internal_password if pin_state is not None else password

    payloads: dict[str, dict[str, object]] = {
        "student-001": {
            "display_name": "合成学生甲",
            "class_label": "合成一班",
        },
        "note-001": {
            "body": "仅用于旧库转换测试",
            "tags": ["合成", "离线"],
        },
    }
    with sqlite3.connect(source) as connection:
        connection.executescript(_VAULT_SCHEMA)
        password_salt = b"S" * 16
        recovery_salt = b"R" * 16
        wrapped_password = _seal(
            _derive(password_secret, password_salt, n=kdf_n),
            vmk,
            _vmk_aad("password", format_version),
        )
        wrapped_recovery = _seal(
            _derive(recovery_key, recovery_salt, n=kdf_n),
            vmk,
            _vmk_aad("recovery", format_version),
        )
        connection.execute(
            """
            INSERT INTO vault_metadata (
                singleton_id, instance_id, format_version,
                kdf_n, kdf_r, kdf_p,
                password_salt, password_nonce, wrapped_vmk_password,
                recovery_salt, recovery_nonce, wrapped_vmk_recovery,
                brk_vmk_nonce, wrapped_brk_vmk,
                brk_recovery_nonce, wrapped_brk_recovery,
                failed_attempts, blocked_until, created_at, updated_at
            ) VALUES (
                1, 'synthetic-instance', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, 0, NULL, ?, ?
            )
            """,
            (
                format_version,
                kdf_n,
                SCRYPT_R,
                SCRYPT_P,
                password_salt,
                wrapped_password.nonce,
                wrapped_password.ciphertext,
                recovery_salt,
                wrapped_recovery.nonce,
                wrapped_recovery.ciphertext,
                b"B" * 12,
                b"wrapped-brk-vmk",
                b"C" * 12,
                b"wrapped-brk-recovery",
                "2026-08-01T00:00:00+00:00",
                "2026-08-02T00:00:00+00:00",
            ),
        )
        for index, (object_id, payload) in enumerate(payloads.items(), start=1):
            object_type = (
                "student_subject"
                if object_id.startswith("student")
                else "synthetic_note"
            )
            if mixed_plaintext and index == len(payloads):
                cek_nonce = b""
                wrapped_cek = b""
                payload_nonce = _PLAINTEXT_MARKER
                payload_ciphertext = _canonical(payload)
            else:
                cek = bytes([index]) * 32
                wrapped = _seal(
                    vmk,
                    cek,
                    _object_aad(object_type, object_id, "cek", format_version),
                )
                protected = _seal(
                    cek,
                    _canonical(payload),
                    _object_aad(object_type, object_id, "payload", format_version),
                )
                cek_nonce = wrapped.nonce
                wrapped_cek = wrapped.ciphertext
                payload_nonce = protected.nonce
                payload_ciphertext = protected.ciphertext
            connection.execute(
                """
                INSERT INTO encrypted_objects (
                    object_id, object_type, format_version,
                    cek_nonce, wrapped_cek, payload_nonce, payload_ciphertext,
                    revision, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    object_id,
                    object_type,
                    format_version,
                    cek_nonce,
                    wrapped_cek,
                    payload_nonce,
                    payload_ciphertext,
                    index + 2,
                    f"2026-08-0{index}T01:00:00+00:00",
                    f"2026-08-0{index}T02:00:00+00:00",
                ),
            )
            connection.execute(
                "INSERT INTO synthetic_links (link_id, payload_object_id) VALUES (?, ?)",
                (f"link-{index}", object_id),
            )
        connection.execute(
            """
            INSERT INTO initialization_recovery_receipts (
                operation_id, recovery_nonce, recovery_ciphertext, created_at
            ) VALUES ('synthetic-init', ?, ?, '2026-08-01T00:00:00+00:00')
            """,
            (b"N" * 12, b"encrypted-recovery-receipt"),
        )
        connection.commit()

    if protection_database is not None:
        with sqlite3.connect(protection_database) as connection:
            connection.executescript(_PROTECTION_SCHEMA)
            if pin_state in {"active", "both"}:
                _insert_pin_package(
                    connection,
                    table="sensitive_protection",
                    pin=pin,
                    internal_secret=internal_password,
                    provider=provider,
                )
            if pin_state in {"pending", "both"}:
                _insert_pin_package(
                    connection,
                    table="sensitive_protection_pending",
                    pin=pin,
                    internal_secret=internal_password,
                    provider=provider,
                )
            connection.commit()

    return SyntheticLegacyVault(
        source=source,
        protection_database=protection_database,
        password=password,
        pin=pin,
        recovery_key=recovery_key,
        provider=provider,
        payloads=payloads,
    )


def _insert_pin_package(
    connection: sqlite3.Connection,
    *,
    table: str,
    pin: str,
    internal_secret: str,
    provider: SyntheticCurrentUserProtection,
) -> None:
    salt = b"I" * 16
    wrapped = _seal(
        _derive(pin, salt),
        internal_secret.encode("utf-8"),
        _PIN_AAD,
    )
    package = json.dumps(
        {
            "nonce": base64.b64encode(wrapped.nonce).decode("ascii"),
            "ciphertext": base64.b64encode(wrapped.ciphertext).decode("ascii"),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    protected = provider.protect_current_user(package, _DPAPI_ENTROPY)
    columns = "singleton_id, mode, pin_salt, protected_secret, created_at, updated_at"
    values = "1, 'pin_dpapi_current_user_v2', ?, ?, '2026-08-01', '2026-08-01'"
    if table == "sensitive_protection":
        columns += ", state"
        values += ", 'active'"
    connection.execute(
        f"INSERT INTO {table} ({columns}) VALUES ({values})",
        (salt, protected),
    )


def _derive(secret: str, salt: bytes, *, n: int = SCRYPT_N) -> bytes:
    return Scrypt(
        salt=salt,
        length=32,
        n=n,
        r=SCRYPT_R,
        p=SCRYPT_P,
    ).derive(secret.encode("utf-8"))


def _seal(key: bytes, plaintext: bytes, aad: bytes) -> _Wrapped:
    nonce = secrets.token_bytes(12)
    return _Wrapped(
        nonce=nonce,
        ciphertext=AESGCM(key).encrypt(nonce, plaintext, aad),
    )


def _canonical(payload: dict[str, object]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _vmk_aad(kind: str, version: int) -> bytes:
    return f"class-teacher|vault|vmk|{kind}|v{version}".encode("utf-8")


def _object_aad(
    object_type: str,
    object_id: str,
    field: str,
    version: int,
) -> bytes:
    return f"class-teacher|{object_type}|{object_id}|{field}|v{version}".encode(
        "utf-8"
    )


_VAULT_SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE vault_metadata (
    singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
    instance_id TEXT NOT NULL UNIQUE,
    format_version INTEGER NOT NULL,
    kdf_n INTEGER NOT NULL,
    kdf_r INTEGER NOT NULL,
    kdf_p INTEGER NOT NULL,
    password_salt BLOB NOT NULL,
    password_nonce BLOB NOT NULL,
    wrapped_vmk_password BLOB NOT NULL,
    recovery_salt BLOB NOT NULL,
    recovery_nonce BLOB NOT NULL,
    wrapped_vmk_recovery BLOB NOT NULL,
    brk_vmk_nonce BLOB NOT NULL,
    wrapped_brk_vmk BLOB NOT NULL,
    brk_recovery_nonce BLOB NOT NULL,
    wrapped_brk_recovery BLOB NOT NULL,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    blocked_until TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE encrypted_objects (
    object_id TEXT PRIMARY KEY,
    object_type TEXT NOT NULL,
    format_version INTEGER NOT NULL,
    cek_nonce BLOB NOT NULL,
    wrapped_cek BLOB NOT NULL,
    payload_nonce BLOB NOT NULL,
    payload_ciphertext BLOB NOT NULL,
    revision INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE initialization_recovery_receipts (
    operation_id TEXT PRIMARY KEY,
    recovery_nonce BLOB NOT NULL,
    recovery_ciphertext BLOB NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE synthetic_links (
    link_id TEXT PRIMARY KEY,
    payload_object_id TEXT NOT NULL REFERENCES encrypted_objects(object_id)
);
"""


_PROTECTION_SCHEMA = """
CREATE TABLE sensitive_protection (
    singleton_id INTEGER PRIMARY KEY,
    mode TEXT NOT NULL,
    state TEXT NOT NULL,
    pin_salt BLOB NOT NULL,
    protected_secret BLOB NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE sensitive_protection_pending (
    singleton_id INTEGER PRIMARY KEY,
    mode TEXT NOT NULL,
    pin_salt BLOB NOT NULL,
    protected_secret BLOB NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


__all__ = [
    "SyntheticCurrentUserProtection",
    "SyntheticLegacyVault",
    "create_legacy_vault",
]
