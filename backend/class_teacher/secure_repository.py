from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from .crypto import FORMAT_VERSION
from .errors import VaultError, unsupported_database_format_error


def _now() -> str:
    return datetime.now(UTC).isoformat()


class EncryptedObjectRepository:
    """Plaintext compatibility store for the class-teacher runtime."""

    _PLAINTEXT_MARKER = b"plaintext-json-v1"

    @classmethod
    def has_unsupported_storage_format(
        cls,
        connection: sqlite3.Connection,
    ) -> bool:
        """Return whether the database cannot be read by the current runtime."""

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
            rows = connection.execute(
                """
                SELECT format_version, cek_nonce, wrapped_cek,
                       payload_nonce, payload_ciphertext
                FROM encrypted_objects
                """
            ).fetchall()
            for row in rows:
                if (
                    int(row[0]) != FORMAT_VERSION
                    or bytes(row[1]) != b""
                    or bytes(row[2]) != b""
                    or bytes(row[3]) != cls._PLAINTEXT_MARKER
                ):
                    return True
                decoded = json.loads(bytes(row[4]).decode("utf-8"))
                if not isinstance(decoded, dict):
                    return True
            return False
        except (
            json.JSONDecodeError,
            sqlite3.DatabaseError,
            TypeError,
            UnicodeDecodeError,
            ValueError,
        ):
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
        cek_nonce = b""
        wrapped_cek_bytes = b""
        payload_nonce = self._PLAINTEXT_MARKER
        payload_bytes = serialized
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
        if bytes(row["payload_nonce"]) != self._PLAINTEXT_MARKER:
            raise unsupported_database_format_error()
        payload = bytes(row["payload_ciphertext"])
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
