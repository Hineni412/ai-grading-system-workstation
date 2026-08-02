from __future__ import annotations

import json
import re
from contextlib import closing
from datetime import UTC, datetime
from typing import Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .crypto import b64, derive_key, open_sealed, random_key, random_salt, seal, unb64
from .errors import VaultError, VaultIntegrityError
from .ordinary_database import OrdinaryWorkDatabase


PIN_DPAPI_V2 = "pin_dpapi_current_user_v2"
LEGACY_PASSWORD_V1 = "legacy_password_v1"
_PIN = re.compile(r"[0-9]{6}")
_PIN_AAD = b"class-teacher|sensitive-vault-secret|pin-dpapi-current-user|v2"
_DPAPI_ENTROPY = b"class-teacher|current-user|v2"


def _now() -> str:
    return datetime.now(UTC).isoformat()


class ProtectionProvider(Protocol):
    def protect_current_user(self, plaintext: bytes, entropy: bytes) -> bytes: ...

    def unprotect_current_user(self, protected: bytes, entropy: bytes) -> bytes: ...


class FakeCurrentUserProtection:
    """In-memory-key adapter for deterministic synthetic tests only."""

    def __init__(self, key: bytes | None = None) -> None:
        self._key = key or random_key()

    def protect_current_user(self, plaintext: bytes, entropy: bytes) -> bytes:
        nonce = random_salt()[:12]
        return nonce + AESGCM(self._key).encrypt(nonce, plaintext, entropy)

    def unprotect_current_user(self, protected: bytes, entropy: bytes) -> bytes:
        try:
            return AESGCM(self._key).decrypt(
                protected[:12],
                protected[12:],
                entropy,
            )
        except (InvalidTag, ValueError) as exc:
            raise VaultError(
                "vault_windows_binding_unavailable",
                "当前 Windows 用户无法打开敏感保险箱，请使用恢复密钥重新绑定",
                status_code=409,
            ) from exc


class PinProtection:
    """Persist and recover the internal high-entropy v1 vault secret.

    Existing student_affairs.db files remain untouched.  New v2 vaults use a
    random internal v1 password which is protected by both a six-digit PIN and
    the current Windows-user adapter.
    """

    def __init__(
        self,
        database: OrdinaryWorkDatabase,
        provider: ProtectionProvider,
    ) -> None:
        self.database = database
        self.provider = provider

    def mode(self, *, vault_exists: bool) -> str:
        if not vault_exists:
            return "uninitialized"
        row = self._row()
        if row is None and self._pending_row() is None:
            return LEGACY_PASSWORD_V1
        return PIN_DPAPI_V2

    def state(self) -> str | None:
        row = self._row()
        if row is not None:
            return "active"
        return "pending" if self._pending_row() is not None else None

    def stage(
        self,
        *,
        pin: str,
        secret: str,
        operation_id: str | None = None,
    ) -> None:
        self.validate_pin(pin)
        salt = random_salt()
        wrapped = seal(derive_key(pin, salt), secret.encode("utf-8"), _PIN_AAD)
        package = json.dumps(
            {"nonce": b64(wrapped.nonce), "ciphertext": b64(wrapped.ciphertext)},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        protected = self.provider.protect_current_user(package, _DPAPI_ENTROPY)
        self.database.initialize_schema() if not self.database.exists else None
        timestamp = _now()
        with closing(self.database.connect()) as connection:
            with connection:
                connection.execute(
                    """
                    INSERT INTO sensitive_protection_pending (
                        singleton_id, mode, pin_salt, protected_secret,
                        created_at, updated_at, operation_id
                    ) VALUES (1, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(singleton_id) DO UPDATE SET
                        mode = excluded.mode,
                        pin_salt = excluded.pin_salt,
                        protected_secret = excluded.protected_secret,
                        operation_id = excluded.operation_id,
                        updated_at = excluded.updated_at
                    """,
                    (
                        PIN_DPAPI_V2,
                        salt,
                        protected,
                        timestamp,
                        timestamp,
                        operation_id,
                    ),
                )

    def activate(self) -> None:
        if not self.database.exists:
            raise VaultError(
                "vault_pin_setup_missing",
                "PIN 保护尚未准备完成",
                status_code=409,
            )
        with closing(self.database.connect()) as connection:
            with connection:
                pending = connection.execute(
                    """
                    SELECT * FROM sensitive_protection_pending
                    WHERE singleton_id = 1
                    """
                ).fetchone()
                if pending is None:
                    changed = 0
                else:
                    connection.execute(
                        """
                        INSERT INTO sensitive_protection (
                            singleton_id, mode, state, pin_salt,
                            protected_secret, created_at, updated_at
                        ) VALUES (1, ?, 'active', ?, ?, ?, ?)
                        ON CONFLICT(singleton_id) DO UPDATE SET
                            mode = excluded.mode,
                            state = 'active',
                            pin_salt = excluded.pin_salt,
                            protected_secret = excluded.protected_secret,
                            updated_at = excluded.updated_at
                        """,
                        (
                            PIN_DPAPI_V2,
                            bytes(pending["pin_salt"]),
                            bytes(pending["protected_secret"]),
                            str(pending["created_at"]),
                            _now(),
                        ),
                    )
                    changed = connection.execute(
                        "DELETE FROM sensitive_protection_pending WHERE singleton_id = 1"
                    ).rowcount
        if changed != 1:
            raise VaultError(
                "vault_pin_setup_missing",
                "PIN 保护尚未准备完成",
                status_code=409,
            )

    def secret(self, *, pin: str, allow_pending: bool = False) -> str:
        self.validate_pin(pin)
        row = self._row()
        if row is None and allow_pending:
            row = self._pending_row()
        if row is None:
            raise VaultError(
                "vault_pin_setup_missing",
                "PIN 保护尚未准备完成",
                status_code=409,
            )
        try:
            package_raw = self.provider.unprotect_current_user(
                bytes(row["protected_secret"]),
                _DPAPI_ENTROPY,
            )
            package = json.loads(package_raw.decode("utf-8"))
            plaintext = open_sealed(
                derive_key(pin, bytes(row["pin_salt"])),
                unb64(package["nonce"]),
                unb64(package["ciphertext"]),
                _PIN_AAD,
            )
            return plaintext.decode("utf-8")
        except VaultIntegrityError as exc:
            raise VaultError(
                "vault_pin_invalid",
                "PIN 不正确，敏感数据没有改变",
                status_code=401,
            ) from exc
        except VaultError:
            raise
        except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise VaultError(
                "vault_pin_protection_invalid",
                "PIN 保护状态损坏，请使用恢复密钥",
                status_code=409,
            ) from exc

    def pending_secret(self, *, pin: str) -> str | None:
        self.validate_pin(pin)
        row = self._pending_row()
        if row is None:
            return None
        active = self._row()
        try:
            package_raw = self.provider.unprotect_current_user(
                bytes(row["protected_secret"]),
                _DPAPI_ENTROPY,
            )
            package = json.loads(package_raw.decode("utf-8"))
            return open_sealed(
                derive_key(pin, bytes(row["pin_salt"])),
                unb64(package["nonce"]),
                unb64(package["ciphertext"]),
                _PIN_AAD,
            ).decode("utf-8")
        except VaultIntegrityError:
            if active is not None:
                return None
            raise VaultError(
                "vault_pin_invalid",
                "PIN 不正确，敏感数据没有改变",
                status_code=401,
            ) from None
        except VaultError:
            raise
        except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise VaultError(
                "vault_pin_protection_invalid",
                "PIN 保护状态损坏，请使用恢复密钥",
                status_code=409,
            ) from exc

    def discard(self) -> None:
        if not self.database.exists:
            return
        with closing(self.database.connect()) as connection:
            with connection:
                connection.execute("DELETE FROM sensitive_protection_pending")

    def _row(self):
        if not self.database.exists:
            return None
        try:
            with closing(self.database.connect()) as connection:
                return connection.execute(
                    "SELECT * FROM sensitive_protection WHERE singleton_id = 1"
                ).fetchone()
        except Exception:
            # An old ordinary database predating v2 is treated as legacy until
            # the explicit upgrade command creates the protection table.
            return None

    def _pending_row(self):
        if not self.database.exists:
            return None
        try:
            with closing(self.database.connect()) as connection:
                return connection.execute(
                    """
                    SELECT * FROM sensitive_protection_pending
                    WHERE singleton_id = 1
                    """
                ).fetchone()
        except Exception:
            return None

    @staticmethod
    def validate_pin(pin: str) -> None:
        if _PIN.fullmatch(str(pin or "")) is None:
            raise VaultError(
                "vault_pin_invalid_format",
                "PIN 必须是 6 位数字",
                status_code=422,
            )


__all__ = [
    "FakeCurrentUserProtection",
    "LEGACY_PASSWORD_V1",
    "PIN_DPAPI_V2",
    "PinProtection",
    "ProtectionProvider",
]
