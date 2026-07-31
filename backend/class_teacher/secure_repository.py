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
    """The only persistence boundary allowed to handle protected payloads."""

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

        cek = random_key()
        wrapped_cek = seal(vmk, cek, _aad(object_type, object_id, "cek"))
        protected = seal(
            cek,
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8"),
            _aad(object_type, object_id, "payload"),
        )
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
                wrapped_cek.nonce,
                wrapped_cek.ciphertext,
                protected.nonce,
                protected.ciphertext,
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
