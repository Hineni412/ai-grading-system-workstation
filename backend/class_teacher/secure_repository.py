from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from .crypto import FORMAT_VERSION, open_sealed, random_key, seal
from .errors import VaultError


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _aad(object_type: str, object_id: str, field: str) -> bytes:
    return (
        f"class-teacher|{object_type}|{object_id}|{field}|v{FORMAT_VERSION}"
    ).encode("utf-8")


class EncryptedObjectRepository:
    """Compatibility repository for legacy encrypted and debug plaintext rows."""

    _PLAINTEXT_MARKER = b"plaintext-json-v1"

    def __init__(self, *, plaintext: bool = False) -> None:
        self.plaintext = bool(plaintext)

    @classmethod
    def requires_plaintext_migration(cls, connection: sqlite3.Connection) -> bool:
        """Fail closed when a debug plaintext service sees a legacy vault."""

        try:
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            if "vault_metadata" not in tables or "encrypted_objects" not in tables:
                return True
            if connection.execute("SELECT 1 FROM vault_metadata LIMIT 1").fetchone():
                return True
            row = connection.execute(
                "SELECT 1 FROM encrypted_objects WHERE payload_nonce <> ? LIMIT 1",
                (cls._PLAINTEXT_MARKER,),
            ).fetchone()
            return row is not None
        except sqlite3.DatabaseError:
            return True

    def put(
        self,
        connection: sqlite3.Connection,
        *,
        vmk: bytes,
        object_id: str,
        object_type: str,
        payload: dict[str, Any],
        expected_revision: int | None = None,
    ) -> int:
        existing = connection.execute(
            "SELECT revision FROM encrypted_objects WHERE object_id = ?",
            (object_id,),
        ).fetchone()
        if existing is None:
            if expected_revision not in (None, 0):
                raise VaultError(
                    "vault_revision_conflict",
                    "记录已经变化，请刷新后再保存",
                    status_code=409,
                )
            revision = 1
            created_at = _now()
        else:
            current = int(existing["revision"])
            if expected_revision is not None and expected_revision != current:
                raise VaultError(
                    "vault_revision_conflict",
                    "记录已经变化，请刷新后再保存",
                    status_code=409,
                )
            revision = current + 1
            created_at = connection.execute(
                "SELECT created_at FROM encrypted_objects WHERE object_id = ?",
                (object_id,),
            ).fetchone()["created_at"]

        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if self.plaintext:
            cek_nonce = b""
            wrapped_cek_bytes = b""
            payload_nonce = self._PLAINTEXT_MARKER
            payload_bytes = serialized
        else:
            cek = random_key()
            wrapped_cek = seal(vmk, cek, _aad(object_type, object_id, "cek"))
            protected = seal(
                cek,
                serialized,
                _aad(object_type, object_id, "payload"),
            )
            cek_nonce = wrapped_cek.nonce
            wrapped_cek_bytes = wrapped_cek.ciphertext
            payload_nonce = protected.nonce
            payload_bytes = protected.ciphertext
        connection.execute(
            """
            INSERT INTO encrypted_objects (
                object_id, object_type, format_version, cek_nonce, wrapped_cek,
                payload_nonce, payload_ciphertext, revision, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(object_id) DO UPDATE SET
                object_type = excluded.object_type,
                format_version = excluded.format_version,
                cek_nonce = excluded.cek_nonce,
                wrapped_cek = excluded.wrapped_cek,
                payload_nonce = excluded.payload_nonce,
                payload_ciphertext = excluded.payload_ciphertext,
                revision = excluded.revision,
                updated_at = excluded.updated_at
            """,
            (
                object_id,
                object_type,
                FORMAT_VERSION,
                cek_nonce,
                wrapped_cek_bytes,
                payload_nonce,
                payload_bytes,
                revision,
                created_at,
                _now(),
            ),
        )
        return revision

    def get(
        self,
        connection: sqlite3.Connection,
        *,
        vmk: bytes,
        object_id: str,
    ) -> tuple[dict[str, Any], int]:
        row = connection.execute(
            "SELECT * FROM encrypted_objects WHERE object_id = ?",
            (object_id,),
        ).fetchone()
        if row is None:
            raise VaultError(
                "vault_object_not_found",
                "受保护记录不存在",
                status_code=404,
            )
        object_type = str(row["object_type"])
        if bytes(row["payload_nonce"]) == self._PLAINTEXT_MARKER:
            payload = bytes(row["payload_ciphertext"])
        else:
            cek = open_sealed(
                vmk,
                bytes(row["cek_nonce"]),
                bytes(row["wrapped_cek"]),
                _aad(object_type, object_id, "cek"),
            )
            payload = open_sealed(
                cek,
                bytes(row["payload_nonce"]),
                bytes(row["payload_ciphertext"]),
                _aad(object_type, object_id, "payload"),
            )
        try:
            decoded = json.loads(payload.decode("utf-8"))
            if not isinstance(decoded, dict):
                raise TypeError("payload")
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
            raise VaultError(
                "vault_integrity_error",
                "受保护数据未通过完整性校验，现有数据没有改变",
                status_code=409,
            ) from exc
        return decoded, int(row["revision"])


__all__ = ["EncryptedObjectRepository"]
