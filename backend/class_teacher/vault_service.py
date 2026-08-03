from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from contextlib import asynccontextmanager, closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.workspaces.contracts import WorkspaceContext

from .crypto import (
    FORMAT_VERSION,
    SCRYPT_N,
    SCRYPT_P,
    SCRYPT_R,
    b64,
    canonical_json,
    derive_key,
    generate_recovery_key,
    open_sealed,
    random_key,
    random_salt,
    seal,
    unb64,
    wipe,
)
from .encrypted_database import EncryptedDatabase
from .errors import VaultError, VaultIntegrityError
from .secure_repository import EncryptedObjectRepository


_SESSION_SECONDS = 5 * 60
_PREVIEW_SECONDS = 5 * 60
_PASSWORD_MINIMUM = 12
_BACKUP_NAME = re.compile(r"^[0-9a-f]{32}\.ctbackup$")
_OPERATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{7,127}$")
_AUDIT_CODE = re.compile(r"^[a-z][a-z0-9_.-]{1,63}$")
_BACKUP_MAGIC = "class-teacher-vault-backup"
_BACKUP_FORMAT = 1


def _now() -> datetime:
    return datetime.now(UTC)


def _snapshot_content_digest(payload: bytes) -> str:
    """Hash durable vault meaning, excluding append-only access audit noise."""
    digest = hashlib.sha256()
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.deserialize(payload)
        tables = connection.execute(
            """
            SELECT name, sql FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
              AND name != 'access_audit'
            ORDER BY name
            """
        ).fetchall()
        for table_name, schema in tables:
            name = str(table_name)
            digest.update(canonical_json({"table": name, "schema": str(schema or "")}))
            quoted = '"' + name.replace('"', '""') + '"'
            encoded_rows: list[bytes] = []
            for row in connection.execute(f"SELECT * FROM {quoted}").fetchall():
                values: list[object] = []
                for value in row:
                    if isinstance(value, bytes):
                        values.append({"bytes": b64(value)})
                    else:
                        values.append(value)
                encoded_rows.append(canonical_json({"row": values}))
            for encoded in sorted(encoded_rows):
                digest.update(len(encoded).to_bytes(8, "big"))
                digest.update(encoded)
    return digest.hexdigest()


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat()


def _vmk_aad(kind: str) -> bytes:
    return f"class-teacher|vault|vmk|{kind}|v{FORMAT_VERSION}".encode()


def _brk_aad(kind: str) -> bytes:
    return f"class-teacher|vault|brk|{kind}|v{FORMAT_VERSION}".encode()


def _backup_aad(backup_id: str, kind: str) -> bytes:
    return (
        f"class-teacher|backup|{backup_id}|{kind}|v{_BACKUP_FORMAT}"
    ).encode()


def _initialization_receipt_aad(operation_id: str) -> bytes:
    return f"class-teacher|initialization-receipt|{operation_id}".encode()


@dataclass(slots=True)
class _Session:
    token: str
    vmk: bytearray
    instance_id: str
    last_activity: float


@dataclass(slots=True)
class _RestorePreview:
    token: str
    payload: bytearray
    backup_id: str
    source_instance_id: str
    created_at: str
    expires_at: float
    current_digest: str
    confirmation_phrase: str


class VaultService:
    _PLAINTEXT_KEY = b"class-teacher-plaintext-debug-key"

    def __init__(
        self,
        context: WorkspaceContext,
        *,
        protection_provider=None,
        model_gateway=None,
        protection_enabled: bool = True,
    ) -> None:
        self._operation_lock = asyncio.Lock()
        self.protection_enabled = bool(protection_enabled)
        self.database = EncryptedDatabase(context)
        self.repository = EncryptedObjectRepository(
            plaintext=not self.protection_enabled,
        )
        from .ordinary_database import OrdinaryWorkDatabase
        from .model_approval import ModelApproval
        from .protection import PinProtection
        from .windows_dpapi import WindowsCurrentUserProtection
        from .work_graph import WorkGraph

        self.ordinary_database = OrdinaryWorkDatabase(context)
        self.work = WorkGraph(
            self.ordinary_database,
            model_gateway=model_gateway,
        )
        self.pin_protection = PinProtection(
            self.ordinary_database,
            protection_provider or WindowsCurrentUserProtection(),
        )
        self.model_approval = ModelApproval(
            self.ordinary_database,
            self.database,
            self.repository,
            self.session_key,
            model_gateway,
        )
        from .home_intake import HomeIntake

        self.home_intake = HomeIntake(
            self.ordinary_database,
            self.work,
            self.model_approval,
        )
        from .sensitive_work_projection import SensitiveWorkProjection

        self.projections = SensitiveWorkProjection(
            self.database,
            self.repository,
            self.session_key,
            self.work,
        )
        from .action_ledger_service import ActionLedgerService
        from .planning_service import PlanningService
        from .sop_workflow_service import SopWorkflowService
        from .collection_service import CollectionService
        from .sop_baseline_service import SopBaselineService
        from .support_record_service import SupportRecordService
        from .student_card_service import StudentCardService
        from .quick_inbox_service import QuickInboxService
        from .assessment_evidence_service import AssessmentEvidenceService
        from .attention_service import AttentionService

        self.actions = ActionLedgerService(
            self.database,
            self.repository,
            self.session_key,
        )
        self.planning = PlanningService(
            self.database,
            self.repository,
            self.session_key,
        )
        self.sop = SopWorkflowService(
            self.database,
            self.repository,
            self.session_key,
        )
        self.collections = CollectionService(
            self.database,
            self.repository,
            self.session_key,
            self.planning,
        )
        self.sop_baselines = SopBaselineService(self.sop)
        self.support = SupportRecordService(
            self.database,
            self.repository,
            self.session_key,
            self.projections,
        )
        from .class_roster_service import ClassRosterService
        from .existing_student_roster import SqliteExistingStudentRosterSource

        self.class_roster = ClassRosterService(
            self.database,
            self.repository,
            self.session_key,
            self.support,
            SqliteExistingStudentRosterSource(
                Path(getattr(context.paths, "db_path", context.root / "grading.db"))
            ),
        )
        self.student_cards = StudentCardService(
            self.database,
            self.repository,
            self.session_key,
            self.support,
            self.model_approval,
            self.work,
            self.projections,
        )
        self.quick_inbox = QuickInboxService(
            self.database,
            self.repository,
            self.session_key,
            self.support,
            self.actions,
            self.sop,
        )
        self.evidence = AssessmentEvidenceService(
            self.database,
            self.repository,
            self.session_key,
        )
        self.attention = AttentionService(
            self.database,
            self.repository,
            self.session_key,
            self.evidence,
            self.actions,
        )
        from .student_academic_analysis import StudentAcademicAnalysis

        self.academic = StudentAcademicAnalysis(
            self.database,
            self.repository,
            self.session_key,
            self.evidence,
            self.attention,
            self.projections,
        )
        self._lock = threading.RLock()
        self._sessions: dict[str, _Session] = {}
        self._previews: dict[str, _RestorePreview] = {}
        self._initialization_replays: dict[str, dict[str, object]] = {}

    @asynccontextmanager
    async def operation_scope(self):
        """Serialize protected API operations across lock and restore boundaries."""
        async with self._operation_lock:
            yield

    def status(self, token: str | None = None) -> dict[str, object]:
        with self._lock:
            if not self.protection_enabled:
                if self._plaintext_migration_required():
                    return {
                        "initialized": True,
                        "locked": True,
                        "idle_timeout_seconds": 0,
                        "retry_after_seconds": 0,
                        "format_version": FORMAT_VERSION,
                        "protection_mode": "legacy_migration_required",
                        "protection_state": None,
                        "legacy_upgrade_available": False,
                        "session_expires_in_seconds": 0,
                        "status_observed_at": _iso(),
                        "lock_reason": "legacy_migration_required",
                    }
                return {
                    "initialized": True,
                    "locked": False,
                    "idle_timeout_seconds": 0,
                    "retry_after_seconds": 0,
                    "format_version": FORMAT_VERSION,
                    "protection_mode": "plaintext_debug_v1",
                    "protection_state": None,
                    "legacy_upgrade_available": False,
                    "session_expires_in_seconds": 0,
                    "status_observed_at": _iso(),
                    "lock_reason": None,
                }
            self._prune()
            observed_at = _iso()
            if not self.database.exists:
                return {
                    "initialized": False,
                    "locked": True,
                    "idle_timeout_seconds": _SESSION_SECONDS,
                    "retry_after_seconds": 0,
                    "format_version": FORMAT_VERSION,
                    "protection_mode": "uninitialized",
                    "protection_state": self.pin_protection.state(),
                    "legacy_upgrade_available": False,
                    "session_expires_in_seconds": 0,
                    "status_observed_at": observed_at,
                    "lock_reason": "uninitialized",
                }
            metadata = self._metadata()
            retry_after = self._retry_after(metadata["blocked_until"])
            session = self._sessions.get(str(token or ""))
            unlocked = session is not None and session.instance_id == metadata["instance_id"]
            remaining = (
                max(
                    0,
                    min(
                        _SESSION_SECONDS,
                        int(_SESSION_SECONDS - (time.monotonic() - session.last_activity)),
                    ),
                )
                if unlocked and session is not None
                else 0
            )
            return {
                "initialized": True,
                "locked": not unlocked,
                "idle_timeout_seconds": _SESSION_SECONDS,
                "retry_after_seconds": retry_after,
                "format_version": int(metadata["format_version"]),
                "protection_mode": self.pin_protection.mode(vault_exists=True),
                "protection_state": self.pin_protection.state(),
                "legacy_upgrade_available": (
                    self.pin_protection.mode(vault_exists=True)
                    == "legacy_password_v1"
                ),
                "session_expires_in_seconds": remaining,
                "status_observed_at": observed_at,
                "lock_reason": None if unlocked else "locked",
            }

    def initialize_pin(self, *, pin: str, operation_id: str) -> dict[str, object]:
        """Create a v2 PIN/CurrentUser vault without changing the v1 DB format."""

        self.pin_protection.validate_pin(pin)
        self._validate_operation_id(operation_id)
        if self.database.exists:
            raise VaultError(
                "vault_already_initialized",
                "班主任工作台已经初始化",
                status_code=409,
            )
        internal_secret = secrets.token_urlsafe(32)
        self.pin_protection.stage(pin=pin, secret=internal_secret)
        try:
            result = self.initialize(
                password=internal_secret,
                operation_id=operation_id,
            )
            self.pin_protection.activate()
            return {**result, "protection_mode": "pin_dpapi_current_user_v2"}
        except Exception:
            if not self.database.exists:
                self.pin_protection.discard()
            raise

    def unlock_pin(self, *, pin: str) -> dict[str, object]:
        with self._lock:
            if self.pin_protection.mode(vault_exists=self.database.exists) != (
                "pin_dpapi_current_user_v2"
            ) and self.pin_protection.state() != "pending":
                raise VaultError(
                    "vault_legacy_password_required",
                    "这是旧版保险箱，请继续使用原模块密码",
                    status_code=409,
                )
            metadata = self._metadata()
            retry_after = self._retry_after(metadata["blocked_until"])
            if retry_after:
                raise VaultError(
                    "vault_access_delayed",
                    "解锁尝试过于频繁，请稍后再试",
                    status_code=429,
                    details={"retry_after_seconds": retry_after},
                )
            try:
                pending = self.pin_protection.pending_secret(pin=pin)
                if pending is not None:
                    try:
                        result = self.unlock(password=pending)
                    except VaultError as exc:
                        if exc.code != "vault_access_denied":
                            raise
                    else:
                        self.pin_protection.activate()
                        return {
                            **result,
                            "protection_mode": "pin_dpapi_current_user_v2",
                        }
                secret = self.pin_protection.secret(pin=pin)
            except VaultError as exc:
                if exc.code == "vault_pin_invalid":
                    self._record_failed_unlock(int(metadata["failed_attempts"]) + 1)
                raise
            result = self.unlock(password=secret)
            return {**result, "protection_mode": "pin_dpapi_current_user_v2"}

    def recover_pin(
        self,
        *,
        recovery_key: str,
        new_pin: str,
        operation_id: str,
    ) -> dict[str, object]:
        self.pin_protection.validate_pin(new_pin)
        self._validate_operation_id(operation_id)
        internal_secret = secrets.token_urlsafe(32)
        self.pin_protection.stage(pin=new_pin, secret=internal_secret)
        try:
            result = self.recover(
                recovery_key=recovery_key,
                new_password=internal_secret,
                operation_id=operation_id,
            )
            self.pin_protection.activate()
            return {**result, "protection_mode": "pin_dpapi_current_user_v2"}
        except Exception:
            self.pin_protection.discard()
            raise

    def upgrade_legacy_to_pin(
        self,
        *,
        token: str,
        current_password: str,
        new_pin: str,
        operation_id: str,
    ) -> dict[str, object]:
        """Explicitly replace a legacy password unlock with PIN protection.

        The PIN package is staged before the password wrapper changes. If the
        process stops between those steps, ``unlock_pin`` can finish activation
        from the pending package without repeating the password change.
        """

        self.pin_protection.validate_pin(new_pin)
        self._validate_operation_id(operation_id)
        with self._lock:
            mode = self.pin_protection.mode(vault_exists=self.database.exists)
            if mode != "legacy_password_v1":
                resumed = self.unlock_pin(pin=new_pin)
                self.lock(str(resumed["session_token"]))
                return {"completed": True, "locked": True}

            internal_secret = secrets.token_urlsafe(32)
            self.pin_protection.stage(pin=new_pin, secret=internal_secret)
            password_rewrapped = False
            try:
                self.change_password(
                    token=token,
                    current_password=current_password,
                    new_password=internal_secret,
                    operation_id=operation_id,
                )
                password_rewrapped = True
                self.pin_protection.activate()
            except Exception:
                if not password_rewrapped:
                    self.pin_protection.discard()
                raise
            return {"completed": True, "locked": True}

    def initialize(
        self,
        *,
        password: str,
        operation_id: str,
    ) -> dict[str, object]:
        self._validate_password(password)
        self._validate_operation_id(operation_id)
        with self._lock:
            replay = self._initialization_replays.get(operation_id)
            if replay is not None:
                return dict(replay)
            if self.database.exists:
                raise VaultError(
                    "vault_already_initialized",
                    "班主任工作台已经初始化",
                    status_code=409,
                )

            recovery_key = generate_recovery_key()
            vmk = random_key()
            brk = random_key()
            password_salt = random_salt()
            recovery_salt = random_salt()
            password_kek = derive_key(password, password_salt)
            recovery_kek = derive_key(recovery_key, recovery_salt)
            vmk_password = seal(password_kek, vmk, _vmk_aad("password"))
            vmk_recovery = seal(recovery_kek, vmk, _vmk_aad("recovery"))
            brk_vmk = seal(vmk, brk, _brk_aad("vmk"))
            brk_recovery = seal(recovery_kek, brk, _brk_aad("recovery"))
            instance_id = uuid4().hex
            created_at = _iso()

            try:
                self.database.initialize_schema()
                with closing(self.database.connect()) as connection:
                    with connection:
                        connection.execute(
                            """
                            INSERT INTO vault_metadata (
                                singleton_id, instance_id, format_version,
                                kdf_n, kdf_r, kdf_p,
                                password_salt, password_nonce, wrapped_vmk_password,
                                recovery_salt, recovery_nonce, wrapped_vmk_recovery,
                                brk_vmk_nonce, wrapped_brk_vmk,
                                brk_recovery_nonce, wrapped_brk_recovery,
                                created_at, updated_at
                            ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                instance_id,
                                FORMAT_VERSION,
                                SCRYPT_N,
                                SCRYPT_R,
                                SCRYPT_P,
                                password_salt,
                                vmk_password.nonce,
                                vmk_password.ciphertext,
                                recovery_salt,
                                vmk_recovery.nonce,
                                vmk_recovery.ciphertext,
                                brk_vmk.nonce,
                                brk_vmk.ciphertext,
                                brk_recovery.nonce,
                                brk_recovery.ciphertext,
                                created_at,
                                created_at,
                            ),
                        )
                        receipt = seal(
                            password_kek,
                            recovery_key.encode("utf-8"),
                            _initialization_receipt_aad(operation_id),
                        )
                        connection.execute(
                            """
                            INSERT INTO initialization_recovery_receipts (
                                operation_id, recovery_nonce,
                                recovery_ciphertext, created_at
                            ) VALUES (?, ?, ?, ?)
                            """,
                            (
                                operation_id,
                                receipt.nonce,
                                receipt.ciphertext,
                                created_at,
                            ),
                        )
                        self._audit(connection, "vault.initialize", "success")
                token = self._new_session(vmk, instance_id)
            except Exception:
                self._invalidate_sessions()
                for path in (
                    self.database.database_path,
                    self.database.database_path.with_suffix(".db-wal"),
                    self.database.database_path.with_suffix(".db-shm"),
                ):
                    path.unlink(missing_ok=True)
                raise

            result: dict[str, object] = {
                "session_token": token,
                "recovery_key": recovery_key,
                "recovery_key_shown_once": True,
                "idle_timeout_seconds": _SESSION_SECONDS,
            }
            self._initialization_replays[operation_id] = dict(result)
            return result

    def unlock(self, *, password: str) -> dict[str, object]:
        with self._lock:
            metadata = self._metadata()
            retry_after = self._retry_after(metadata["blocked_until"])
            if retry_after:
                raise VaultError(
                    "vault_access_delayed",
                    "解锁尝试过于频繁，请稍后再试",
                    status_code=429,
                    details={"retry_after_seconds": retry_after},
                )
            try:
                kek = derive_key(
                    password,
                    bytes(metadata["password_salt"]),
                    n=int(metadata["kdf_n"]),
                    r=int(metadata["kdf_r"]),
                    p=int(metadata["kdf_p"]),
                )
                vmk = open_sealed(
                    kek,
                    bytes(metadata["password_nonce"]),
                    bytes(metadata["wrapped_vmk_password"]),
                    _vmk_aad("password"),
                )
            except (VaultIntegrityError, ValueError):
                self._record_failed_unlock(int(metadata["failed_attempts"]) + 1)
                raise VaultError(
                    "vault_access_denied",
                    "密码或受保护数据未通过校验",
                    status_code=401,
                ) from None
            with closing(self.database.connect()) as connection:
                with connection:
                    connection.execute(
                        """
                        UPDATE vault_metadata
                        SET failed_attempts = 0, blocked_until = NULL, updated_at = ?
                        WHERE singleton_id = 1
                        """,
                        (_iso(),),
                    )
                    self._audit(connection, "vault.unlock", "success")
                    receipt = connection.execute(
                        """
                        SELECT operation_id, recovery_nonce,
                               recovery_ciphertext
                        FROM initialization_recovery_receipts
                        ORDER BY created_at DESC LIMIT 1
                        """
                    ).fetchone()
            pending_recovery_key = None
            if receipt is not None:
                try:
                    pending_recovery_key = open_sealed(
                        kek,
                        bytes(receipt["recovery_nonce"]),
                        bytes(receipt["recovery_ciphertext"]),
                        _initialization_receipt_aad(
                            str(receipt["operation_id"])
                        ),
                    ).decode("utf-8")
                except (VaultIntegrityError, UnicodeDecodeError):
                    pending_recovery_key = None
            return {
                "session_token": self._new_session(vmk, str(metadata["instance_id"])),
                "idle_timeout_seconds": _SESSION_SECONDS,
                "recovery_key": pending_recovery_key,
            }

    def acknowledge_recovery_key(self, *, token: str) -> dict[str, object]:
        with self._lock:
            self._require_session(token)
            with closing(self.database.connect()) as connection:
                with connection:
                    deleted = connection.execute(
                        "DELETE FROM initialization_recovery_receipts"
                    ).rowcount
                    self._audit(
                        connection,
                        "vault.recovery_key.acknowledge",
                        "success",
                    )
            return {
                "acknowledged": True,
                "receipts_deleted": int(deleted),
            }

    def recover(
        self,
        *,
        recovery_key: str,
        new_password: str,
        operation_id: str,
    ) -> dict[str, object]:
        self._validate_password(new_password)
        self._validate_operation_id(operation_id)
        with self._lock:
            metadata = self._metadata()
            try:
                recovery_kek = derive_key(
                    recovery_key,
                    bytes(metadata["recovery_salt"]),
                    n=int(metadata["kdf_n"]),
                    r=int(metadata["kdf_r"]),
                    p=int(metadata["kdf_p"]),
                )
                vmk = open_sealed(
                    recovery_kek,
                    bytes(metadata["recovery_nonce"]),
                    bytes(metadata["wrapped_vmk_recovery"]),
                    _vmk_aad("recovery"),
                )
            except (VaultIntegrityError, ValueError):
                raise VaultError(
                    "vault_access_denied",
                    "恢复密钥或受保护数据未通过校验",
                    status_code=401,
                ) from None
            self._rewrap_password(
                vmk,
                new_password,
                audit_action="vault.recover",
                pending_recovery_key=recovery_key,
            )
            token = self._new_session(vmk, str(metadata["instance_id"]))
            return {
                "session_token": token,
                "idle_timeout_seconds": _SESSION_SECONDS,
            }

    def lock(self, token: str | None) -> None:
        with self._lock:
            if token:
                session = self._sessions.pop(token, None)
                if session is not None:
                    try:
                        with closing(self.database.connect()) as connection:
                            with connection:
                                self._audit(connection, "vault.lock", "success")
                    finally:
                        wipe(session.vmk)
            self._discard_previews()

    def touch(self, *, token: str) -> dict[str, object]:
        with self._lock:
            session = self._require_session(token)
            self._cleanup_expired_drafts(bytes(session.vmk))
            return {
                "active": True,
                "idle_timeout_seconds": _SESSION_SECONDS,
                "session_expires_in_seconds": _SESSION_SECONDS,
                "status_observed_at": _iso(),
            }

    def change_pin(
        self,
        *,
        token: str,
        current_pin: str,
        new_pin: str,
        operation_id: str,
    ) -> dict[str, object]:
        self.pin_protection.validate_pin(current_pin)
        self.pin_protection.validate_pin(new_pin)
        self._validate_operation_id(operation_id)
        if current_pin == new_pin:
            raise VaultError(
                "vault_pin_unchanged",
                "新 PIN 不能与当前 PIN 相同",
                status_code=422,
            )
        with self._lock:
            session = self._require_session(token)
            if self.pin_protection.mode(vault_exists=self.database.exists) != (
                "pin_dpapi_current_user_v2"
            ):
                raise VaultError(
                    "vault_pin_change_unavailable",
                    "旧版保险箱请先升级为 6 位 PIN",
                    status_code=409,
                )
            internal_secret = self.pin_protection.secret(pin=current_pin)
            fingerprint = hmac.new(
                bytes(session.vmk),
                f"pin_change|{new_pin}".encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            with closing(self.ordinary_database.connect()) as connection:
                existing = connection.execute(
                    "SELECT * FROM protection_change_operations WHERE operation_id = ?",
                    (operation_id,),
                ).fetchone()
            if existing is not None:
                if str(existing["request_fingerprint"]) != fingerprint:
                    raise VaultError(
                        "vault_operation_conflict",
                        "同一操作编号不能用于不同的 PIN 修改",
                        status_code=409,
                    )
                if str(existing["state"]) == "completed":
                    return {"completed": True, "locked": True}
            timestamp = _iso()
            self.pin_protection.stage(
                pin=new_pin,
                secret=internal_secret,
                operation_id=operation_id,
            )
            with closing(self.ordinary_database.connect()) as connection:
                with connection:
                    connection.execute(
                        """
                        INSERT INTO protection_change_operations (
                            operation_id, change_kind, request_fingerprint,
                            state, result_json, created_at, updated_at
                        ) VALUES (?, 'pin_change', ?, 'staged', NULL, ?, ?)
                        ON CONFLICT(operation_id) DO UPDATE SET
                            state = 'staged', updated_at = excluded.updated_at
                        """,
                        (operation_id, fingerprint, timestamp, timestamp),
                    )
            self.pin_protection.activate()
            with closing(self.ordinary_database.connect()) as connection:
                with connection:
                    connection.execute(
                        """
                        UPDATE protection_change_operations
                        SET state = 'activated', updated_at = ?
                        WHERE operation_id = ?
                        """,
                        (_iso(), operation_id),
                    )
            self._invalidate_sessions()
            self._discard_previews()
            result = {"completed": True, "locked": True}
            with closing(self.ordinary_database.connect()) as connection:
                with connection:
                    connection.execute(
                        """
                        UPDATE protection_change_operations
                        SET state = 'completed', result_json = ?, updated_at = ?
                        WHERE operation_id = ?
                        """,
                        (json.dumps(result, sort_keys=True), _iso(), operation_id),
                    )
            return result

    def _cleanup_expired_drafts(self, vmk: bytes) -> None:
        attention_cutoff = _iso(_now() - timedelta(days=30))
        ai_cutoff = _iso(_now() - timedelta(days=7))
        with closing(self.database.connect()) as connection:
            with connection:
                expired_communication_ids: list[str] = []
                object_ids: list[str] = []
                communication_rows = connection.execute(
                    """
                    SELECT cd.draft_id, cd.payload_object_id,
                           cd.created_at,
                           a.payload_object_id AS action_object_id
                    FROM communication_drafts cd
                    JOIN actions a ON a.action_id = cd.action_id
                    """
                ).fetchall()
                for row in communication_rows:
                    action, _ = self.repository.get(
                        connection,
                        vmk=vmk,
                        object_id=str(row["action_object_id"]),
                    )
                    planned = str(
                        action.get("due_at") or row["created_at"]
                    )
                    try:
                        expires = datetime.fromisoformat(planned) + timedelta(
                            days=30
                        )
                    except ValueError:
                        expires = _now() + timedelta(days=1)
                    if expires.astimezone(UTC) < _now():
                        expired_communication_ids.append(
                            str(row["draft_id"])
                        )
                        object_ids.append(
                            str(row["payload_object_id"])
                        )
                object_ids.extend(
                    str(row[0])
                    for row in connection.execute(
                        """
                        SELECT payload_object_id FROM attention_cards
                        WHERE state = 'draft' AND created_at < ?
                        """,
                        (attention_cutoff,),
                    ).fetchall()
                )
                ai_ids = [
                    str(row[0])
                    for row in connection.execute(
                        """
                        SELECT record_id FROM support_records
                        WHERE record_kind = 'ai_draft' AND created_at < ?
                        """,
                        (ai_cutoff,),
                    ).fetchall()
                ]
                if ai_ids:
                    placeholders = ",".join("?" for _ in ai_ids)
                    object_ids.extend(
                        str(row[0])
                        for row in connection.execute(
                            f"""
                            SELECT payload_object_id FROM record_revisions
                            WHERE record_id IN ({placeholders})
                            """,
                            ai_ids,
                        ).fetchall()
                    )
                if expired_communication_ids:
                    placeholders = ",".join(
                        "?" for _ in expired_communication_ids
                    )
                    connection.execute(
                        f"""
                        DELETE FROM communication_drafts
                        WHERE draft_id IN ({placeholders})
                        """,
                        expired_communication_ids,
                    )
                connection.execute(
                    """
                    DELETE FROM attention_cards
                    WHERE state = 'draft' AND created_at < ?
                    """,
                    (attention_cutoff,),
                )
                if ai_ids:
                    connection.execute(
                        f"""
                        DELETE FROM support_records
                        WHERE record_id IN ({placeholders})
                        """,
                        ai_ids,
                    )
                connection.executemany(
                    "DELETE FROM encrypted_objects WHERE object_id = ?",
                    [(value,) for value in object_ids],
                )

    def session_key(self, token: str) -> bytes:
        with self._lock:
            if not self.protection_enabled:
                if self._plaintext_migration_required():
                    raise VaultError(
                        "vault_plaintext_migration_required",
                        "检测到旧加密班主任数据库，已停止读取和写入；请先执行授权迁移",
                        status_code=409,
                    )
                if not self.database.exists:
                    self.database.initialize_schema()
                return self._PLAINTEXT_KEY
            return bytes(self._require_session(token).vmk)

    def _plaintext_migration_required(self) -> bool:
        if not self.database.exists:
            return False
        try:
            uri = self.database.database_path.resolve().as_uri() + "?mode=ro"
            with closing(sqlite3.connect(uri, uri=True)) as connection:
                return self.repository.requires_plaintext_migration(connection)
        except (OSError, sqlite3.DatabaseError):
            return True

    def change_password(
        self,
        *,
        token: str,
        current_password: str,
        new_password: str,
        operation_id: str,
    ) -> None:
        self._validate_password(new_password)
        self._validate_operation_id(operation_id)
        with self._lock:
            session = self._require_session(token)
            metadata = self._metadata()
            try:
                current_kek = derive_key(
                    current_password,
                    bytes(metadata["password_salt"]),
                    n=int(metadata["kdf_n"]),
                    r=int(metadata["kdf_r"]),
                    p=int(metadata["kdf_p"]),
                )
                verified_vmk = open_sealed(
                    current_kek,
                    bytes(metadata["password_nonce"]),
                    bytes(metadata["wrapped_vmk_password"]),
                    _vmk_aad("password"),
                )
            except (VaultIntegrityError, ValueError):
                raise VaultError(
                    "vault_access_denied",
                    "当前密码或受保护数据未通过校验",
                    status_code=401,
                ) from None
            if not secrets.compare_digest(verified_vmk, bytes(session.vmk)):
                raise VaultError(
                    "vault_access_denied",
                    "当前密码或受保护数据未通过校验",
                    status_code=401,
                )
            self._rewrap_password(
                bytes(session.vmk),
                new_password,
                audit_action="vault.change_password",
                pending_recovery_key=self._read_pending_recovery_key(
                    current_kek
                ),
            )

    def create_backup(
        self,
        *,
        token: str,
        backup_password: str,
        operation_id: str,
    ) -> dict[str, object]:
        self._validate_password(backup_password)
        self._validate_operation_id(operation_id)
        with self._lock:
            session = self._require_session(token)
            replay = self._idempotent_result(operation_id, "vault.backup")
            if replay is not None:
                return replay
            metadata = self._metadata()
            brk = open_sealed(
                bytes(session.vmk),
                bytes(metadata["brk_vmk_nonce"]),
                bytes(metadata["wrapped_brk_vmk"]),
                _brk_aad("vmk"),
            )
            backup_id = uuid4().hex
            created_at = _iso()
            backup_key = random_key()
            backup_salt = random_salt()
            backup_kek = derive_key(backup_password, backup_salt)
            password_wrapper = seal(
                backup_kek,
                backup_key,
                _backup_aad(backup_id, "password"),
            )
            brk_wrapper = seal(brk, backup_key, _backup_aad(backup_id, "brk"))
            header = {
                "magic": _BACKUP_MAGIC,
                "format_version": _BACKUP_FORMAT,
                "backup_id": backup_id,
                "source_instance_id": str(metadata["instance_id"]),
                "created_at": created_at,
                "scope": "complete-vault-replacement",
            }
            protected = seal(
                backup_key,
                self.database.snapshot_bytes(),
                canonical_json(header),
            )
            package = {
                **header,
                "kdf": {"n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P},
                "backup_salt": b64(backup_salt),
                "backup_key_password_nonce": b64(password_wrapper.nonce),
                "wrapped_backup_key_password": b64(password_wrapper.ciphertext),
                "backup_key_brk_nonce": b64(brk_wrapper.nonce),
                "wrapped_backup_key_brk": b64(brk_wrapper.ciphertext),
                "recovery_salt": b64(bytes(metadata["recovery_salt"])),
                "brk_recovery_nonce": b64(bytes(metadata["brk_recovery_nonce"])),
                "wrapped_brk_recovery": b64(
                    bytes(metadata["wrapped_brk_recovery"])
                ),
                "payload_nonce": b64(protected.nonce),
                "payload_ciphertext": b64(protected.ciphertext),
            }
            file_name = f"{backup_id}.ctbackup"
            payload = canonical_json(package)
            self._atomic_backup_write(file_name, payload)
            result = {
                "backup_id": backup_id,
                "file_name": file_name,
                "created_at": created_at,
                "size_bytes": len(payload),
                "source_instance_id": str(metadata["instance_id"]),
            }
            try:
                with closing(self.database.connect()) as connection:
                    with connection:
                        connection.execute(
                        """
                        INSERT INTO encrypted_backup_records (
                            backup_id, file_name, source_instance_id,
                            format_version, size_bytes, status, created_at
                        ) VALUES (?, ?, ?, ?, ?, 'created', ?)
                        """,
                        (
                            backup_id,
                            file_name,
                            str(metadata["instance_id"]),
                            _BACKUP_FORMAT,
                            len(payload),
                            created_at,
                        ),
                        )
                        self._remember_idempotent(
                            connection,
                            operation_id,
                            "vault.backup",
                            result,
                        )
                        self._audit(
                            connection,
                            "vault.backup.create",
                            "success",
                        )
            except Exception:
                (self.database.backup_dir / file_name).unlink(missing_ok=True)
                raise
            return result

    def list_backups(self, *, token: str) -> dict[str, object]:
        with self._lock:
            self._require_session(token)
            with closing(self.database.connect()) as connection:
                rows = connection.execute(
                    """
                    SELECT backup_id, file_name, created_at, size_bytes, status
                    FROM encrypted_backup_records
                    ORDER BY created_at DESC
                    """
                ).fetchall()
            return {
                "items": [
                    {
                        "backup_id": str(row["backup_id"]),
                        "file_name": str(row["file_name"]),
                        "created_at": str(row["created_at"]),
                        "size_bytes": int(row["size_bytes"]),
                        "status": str(row["status"]),
                    }
                    for row in rows
                    if self._backup_path(str(row["file_name"])).is_file()
                ]
            }

    def verify_backup(
        self,
        *,
        file_name: str,
        secret: str,
        secret_kind: str,
    ) -> dict[str, object]:
        with self._lock:
            package, payload = self._open_backup(file_name, secret, secret_kind)
            EncryptedDatabase.validate_snapshot(payload)
            if self.database.exists:
                with closing(self.database.connect()) as connection:
                    with connection:
                        connection.execute(
                            """
                            UPDATE encrypted_backup_records
                            SET status = 'verified', verified_at = ?
                            WHERE backup_id = ?
                            """,
                            (_iso(), str(package["backup_id"])),
                        )
                        self._audit(
                            connection,
                            "vault.backup.verify",
                            "success",
                            object_id=str(package["backup_id"]),
                        )
            return self._backup_summary(package)

    def preview_restore(
        self,
        *,
        token: str,
        file_name: str,
        secret: str,
        secret_kind: str,
    ) -> dict[str, object]:
        with self._lock:
            session = self._require_session(token)
            package, payload = self._open_backup(file_name, secret, secret_kind)
            EncryptedDatabase.validate_snapshot(payload)
            current_snapshot = self.database.snapshot_bytes()
            current_digest = _snapshot_content_digest(current_snapshot)
            backup_counts = self._snapshot_scope_counts(payload)
            current_counts = self._snapshot_scope_counts(current_snapshot)
            backup_schema_version = self._snapshot_schema_version(payload)
            current_schema_version = self._snapshot_schema_version(current_snapshot)
            if backup_schema_version > current_schema_version:
                raise VaultError(
                    "vault_restore_schema_too_new",
                    "这份备份来自更新版本，请先升级应用后再恢复",
                    status_code=409,
                )
            preview_token = secrets.token_urlsafe(32)
            confirmation_phrase = "确认完整替换班主任工作台"
            preview = _RestorePreview(
                token=preview_token,
                payload=bytearray(payload),
                backup_id=str(package["backup_id"]),
                source_instance_id=str(package["source_instance_id"]),
                created_at=str(package["created_at"]),
                expires_at=time.monotonic() + _PREVIEW_SECONDS,
                current_digest=current_digest,
                confirmation_phrase=confirmation_phrase,
            )
            self._previews[preview_token] = preview
            with closing(self.database.connect()) as connection:
                with connection:
                    self._audit(
                        connection,
                        "vault.restore.preview",
                        "success",
                        object_id=preview.backup_id,
                    )
            return {
                **self._backup_summary(package),
                "preview_token": preview_token,
                "expires_in_seconds": _PREVIEW_SECONDS,
                "requires_complete_replacement": (
                    preview.source_instance_id != session.instance_id
                ),
                "source_relation": (
                    "same_instance"
                    if preview.source_instance_id == session.instance_id
                    else "other_instance"
                ),
                "backup_schema_version": backup_schema_version,
                "current_schema_version": current_schema_version,
                "migration_required": backup_schema_version < current_schema_version,
                "backup_scope_counts": backup_counts,
                "current_scope_counts": current_counts,
                "mode": "complete_replace",
                "will_replace_current": True,
                "will_lock_after_confirm": True,
                "confirmation_phrase": confirmation_phrase,
            }

    def confirm_restore(
        self,
        *,
        token: str,
        preview_token: str,
        operation_id: str,
        confirmation_phrase: str | None = None,
    ) -> dict[str, object]:
        self._validate_operation_id(operation_id)
        with self._lock:
            self._require_session(token)
            replay = self._idempotent_result(operation_id, "vault.restore")
            if replay is not None:
                return replay
            preview = self._previews.pop(preview_token, None)
            if preview is None or preview.expires_at <= time.monotonic():
                if preview is not None:
                    wipe(preview.payload)
                raise VaultError(
                    "vault_restore_preview_expired",
                    "恢复预览已失效，请重新验证备份",
                    status_code=409,
                )
            if confirmation_phrase != preview.confirmation_phrase:
                self._previews[preview_token] = preview
                raise VaultError(
                    "vault_restore_confirmation_required",
                    f"请输入“{preview.confirmation_phrase}”后再恢复",
                    status_code=422,
                )
            current_digest = _snapshot_content_digest(self.database.snapshot_bytes())
            if current_digest != preview.current_digest:
                wipe(preview.payload)
                raise VaultError(
                    "vault_restore_preview_stale",
                    "保险箱在预览后已经变化，请重新核对恢复影响",
                    status_code=409,
                )
            payload = bytes(preview.payload)
            wipe(preview.payload)
            result = {
                "restored": True,
                "backup_id": preview.backup_id,
                "locked": True,
            }

            def finalize() -> None:
                with closing(self.database.connect()) as connection:
                    with connection:
                        self._remember_idempotent(
                            connection,
                            operation_id,
                            "vault.restore",
                            result,
                        )
                        self._audit(
                            connection,
                            "vault.restore.confirm",
                            "success",
                            object_id=preview.backup_id,
                        )

            self.database.replace_from_snapshot(payload, finalize=finalize)
            self._invalidate_sessions()
            self._discard_previews()
            return result

    @staticmethod
    def _snapshot_schema_version(payload: bytes) -> int:
        import sqlite3

        with closing(sqlite3.connect(":memory:")) as connection:
            connection.deserialize(payload)
            row = connection.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE success = 1"
            ).fetchone()
            return int(row[0] if row is not None else 0)

    @staticmethod
    def _snapshot_scope_counts(payload: bytes) -> dict[str, int]:
        import sqlite3

        table_names = {
            "subjects": "student_subject_links",
            "support_records": "support_records",
            "student_cards": "student_card_entries",
            "academic_evidence": "evidence_versions",
            "attention_cards": "attention_cards",
            "affairs": "affairs",
        }
        counts: dict[str, int] = {}
        with closing(sqlite3.connect(":memory:")) as connection:
            connection.deserialize(payload)
            present = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            for key, table in table_names.items():
                counts[key] = (
                    int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                    if table in present
                    else 0
                )
        return counts

    def _metadata(self) -> Any:
        if not self.database.exists:
            raise VaultError(
                "vault_not_initialized",
                "班主任工作台尚未初始化",
                status_code=409,
            )
        try:
            with closing(self.database.connect()) as connection:
                row = connection.execute(
                    "SELECT * FROM vault_metadata WHERE singleton_id = 1"
                ).fetchone()
            if row is None:
                raise ValueError("metadata")
            return row
        except VaultIntegrityError as exc:
            raise VaultError(
                "vault_backup_invalid",
                "专用备份、密码或恢复密钥未通过校验",
                status_code=409,
            ) from exc
        except VaultError:
            raise
        except Exception as exc:
            raise VaultIntegrityError() from exc

    def _new_session(self, vmk: bytes, instance_id: str) -> str:
        token = secrets.token_urlsafe(32)
        self._sessions[token] = _Session(
            token=token,
            vmk=bytearray(vmk),
            instance_id=instance_id,
            last_activity=time.monotonic(),
        )
        from .support_ai_review import SupportRecordAIReview

        self.support_ai_reviews = SupportRecordAIReview(
            self.database,
            self.repository,
            self.session_key,
            self.support,
            self.model_approval,
            self.projections,
        )
        from .student_directory import StudentDirectory

        self.student_directory = StudentDirectory(
            self.database,
            self.repository,
            self.session_key,
        )
        from .affair_workspace import AffairWorkspace

        self.affairs = AffairWorkspace(
            self.database,
            self.repository,
            self.session_key,
            self.sop,
            self.projections,
        )
        from .home_intake_finalizer import HomeIntakeFinalizer

        self.home_intake_finalizer = HomeIntakeFinalizer(
            self.home_intake,
            self.sop_baselines,
            self.affairs,
            self.projections,
        )
        if hasattr(self, "projections"):
            self.projections.drain(token=token)
        return token

    def _require_session(self, token: str) -> _Session:
        self._prune()
        session = self._sessions.get(str(token or ""))
        if session is None:
            raise VaultError(
                "vault_locked",
                "班主任工作台已锁定，请重新解锁",
                status_code=401,
            )
        session.last_activity = time.monotonic()
        return session

    def _prune(self) -> None:
        threshold = time.monotonic() - _SESSION_SECONDS
        for token, session in list(self._sessions.items()):
            if session.last_activity <= threshold:
                wipe(session.vmk)
                del self._sessions[token]
        for token, preview in list(self._previews.items()):
            if preview.expires_at <= time.monotonic():
                wipe(preview.payload)
                del self._previews[token]

    def _invalidate_sessions(self) -> None:
        for session in self._sessions.values():
            wipe(session.vmk)
        self._sessions.clear()

    def _discard_previews(self) -> None:
        for preview in self._previews.values():
            wipe(preview.payload)
        self._previews.clear()

    def _rewrap_password(
        self,
        vmk: bytes,
        password: str,
        *,
        audit_action: str,
        pending_recovery_key: str | None = None,
    ) -> None:
        salt = random_salt()
        kek = derive_key(password, salt)
        wrapped = seal(kek, vmk, _vmk_aad("password"))
        with closing(self.database.connect()) as connection:
            with connection:
                connection.execute(
                    """
                    UPDATE vault_metadata
                    SET password_salt = ?, password_nonce = ?,
                        wrapped_vmk_password = ?, failed_attempts = 0,
                        blocked_until = NULL, updated_at = ?
                    WHERE singleton_id = 1
                    """,
                    (salt, wrapped.nonce, wrapped.ciphertext, _iso()),
                )
                receipt = connection.execute(
                    """
                    SELECT operation_id
                    FROM initialization_recovery_receipts
                    ORDER BY created_at DESC LIMIT 1
                    """
                ).fetchone()
                if receipt is not None and pending_recovery_key is not None:
                    resealed = seal(
                        kek,
                        pending_recovery_key.encode("utf-8"),
                        _initialization_receipt_aad(
                            str(receipt["operation_id"])
                        ),
                    )
                    connection.execute(
                        """
                        UPDATE initialization_recovery_receipts
                        SET recovery_nonce = ?, recovery_ciphertext = ?
                        WHERE operation_id = ?
                        """,
                        (
                            resealed.nonce,
                            resealed.ciphertext,
                            str(receipt["operation_id"]),
                        ),
                    )
                self._audit(connection, audit_action, "success")
        self._invalidate_sessions()
        self._discard_previews()

    def _read_pending_recovery_key(self, kek: bytes) -> str | None:
        with closing(self.database.connect()) as connection:
            receipt = connection.execute(
                """
                SELECT operation_id, recovery_nonce, recovery_ciphertext
                FROM initialization_recovery_receipts
                ORDER BY created_at DESC LIMIT 1
                """
            ).fetchone()
        if receipt is None:
            return None
        try:
            return open_sealed(
                kek,
                bytes(receipt["recovery_nonce"]),
                bytes(receipt["recovery_ciphertext"]),
                _initialization_receipt_aad(str(receipt["operation_id"])),
            ).decode("utf-8")
        except (VaultIntegrityError, UnicodeDecodeError):
            return None

    def _record_failed_unlock(self, attempts: int) -> None:
        delays = (0, 2, 5, 15, 30, 60, 300, 900)
        delay = delays[min(attempts, len(delays) - 1)]
        blocked_until = _iso(_now() + timedelta(seconds=delay)) if delay else None
        with closing(self.database.connect()) as connection:
            with connection:
                connection.execute(
                    """
                    UPDATE vault_metadata
                    SET failed_attempts = ?, blocked_until = ?, updated_at = ?
                    WHERE singleton_id = 1
                    """,
                    (attempts, blocked_until, _iso()),
                )
                self._audit(
                    connection,
                    "vault.unlock",
                    "failed",
                    error_category="access_denied",
                )

    @staticmethod
    def _retry_after(raw: object) -> int:
        if not raw:
            return 0
        try:
            remaining = (datetime.fromisoformat(str(raw)) - _now()).total_seconds()
            return max(0, int(remaining + 0.999))
        except ValueError:
            return 0

    def _atomic_backup_write(self, file_name: str, payload: bytes) -> None:
        self.database.backup_dir.mkdir(parents=True, exist_ok=True)
        final = self._backup_path(file_name)
        temporary = final.with_suffix(".ctbackup.tmp")
        temporary.unlink(missing_ok=True)
        try:
            with temporary.open("xb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, final)
        except Exception as exc:
            temporary.unlink(missing_ok=True)
            raise VaultError(
                "vault_backup_failed",
                "专用备份未能安全发布，现有数据没有改变",
                status_code=409,
            ) from exc

    def _backup_path(self, file_name: str) -> Path:
        if not _BACKUP_NAME.fullmatch(str(file_name or "")):
            raise VaultError(
                "vault_backup_not_found",
                "没有找到指定的专用备份",
                status_code=404,
            )
        candidate = self.database.backup_dir / file_name
        try:
            candidate.resolve(strict=False).relative_to(
                self.database.backup_dir.resolve(strict=False)
            )
        except ValueError as exc:
            raise VaultError(
                "vault_backup_not_found",
                "没有找到指定的专用备份",
                status_code=404,
            ) from exc
        return candidate

    def _open_backup(
        self,
        file_name: str,
        secret: str,
        secret_kind: str,
    ) -> tuple[dict[str, Any], bytes]:
        path = self._backup_path(file_name)
        try:
            raw = path.read_bytes()
            package = json.loads(raw.decode("utf-8"))
            if not isinstance(package, dict):
                raise TypeError("package")
            header = {
                "magic": package["magic"],
                "format_version": package["format_version"],
                "backup_id": package["backup_id"],
                "source_instance_id": package["source_instance_id"],
                "created_at": package["created_at"],
                "scope": package["scope"],
            }
            if (
                header["magic"] != _BACKUP_MAGIC
                or int(header["format_version"]) != _BACKUP_FORMAT
                or not re.fullmatch(r"[0-9a-f]{32}", str(header["backup_id"]))
            ):
                raise ValueError("format")
            backup_id = str(header["backup_id"])
            if secret_kind == "password":
                params = package["kdf"]
                kek = derive_key(
                    secret,
                    unb64(package["backup_salt"]),
                    n=int(params["n"]),
                    r=int(params["r"]),
                    p=int(params["p"]),
                )
                backup_key = open_sealed(
                    kek,
                    unb64(package["backup_key_password_nonce"]),
                    unb64(package["wrapped_backup_key_password"]),
                    _backup_aad(backup_id, "password"),
                )
            elif secret_kind == "recovery_key":
                recovery_kek = derive_key(
                    secret,
                    unb64(package["recovery_salt"]),
                    n=SCRYPT_N,
                    r=SCRYPT_R,
                    p=SCRYPT_P,
                )
                brk = open_sealed(
                    recovery_kek,
                    unb64(package["brk_recovery_nonce"]),
                    unb64(package["wrapped_brk_recovery"]),
                    _brk_aad("recovery"),
                )
                backup_key = open_sealed(
                    brk,
                    unb64(package["backup_key_brk_nonce"]),
                    unb64(package["wrapped_backup_key_brk"]),
                    _backup_aad(backup_id, "brk"),
                )
            else:
                raise ValueError("secret_kind")
            payload = open_sealed(
                backup_key,
                unb64(package["payload_nonce"]),
                unb64(package["payload_ciphertext"]),
                canonical_json(header),
            )
            return package, payload
        except FileNotFoundError as exc:
            raise VaultError(
                "vault_backup_not_found",
                "没有找到指定的专用备份",
                status_code=404,
            ) from exc
        except VaultError:
            raise
        except Exception as exc:
            raise VaultError(
                "vault_backup_invalid",
                "专用备份、密码或恢复密钥未通过校验",
                status_code=409,
            ) from exc

    @staticmethod
    def _backup_summary(package: dict[str, Any]) -> dict[str, object]:
        return {
            "backup_id": str(package["backup_id"]),
            "source_instance_id": str(package["source_instance_id"]),
            "created_at": str(package["created_at"]),
            "format_version": int(package["format_version"]),
            "scope": str(package["scope"]),
        }

    def _idempotent_result(
        self,
        operation_id: str,
        operation_type: str,
    ) -> dict[str, object] | None:
        with closing(self.database.connect()) as connection:
            row = connection.execute(
                """
                SELECT operation_type, result_json
                FROM idempotency_ledger
                WHERE operation_id = ?
                """,
                (operation_id,),
            ).fetchone()
        if row is None:
            return None
        if str(row["operation_type"]) != operation_type:
            raise VaultError(
                "vault_operation_conflict",
                "此操作编号已经用于另一项操作",
                status_code=409,
            )
        decoded = json.loads(str(row["result_json"]))
        return dict(decoded)

    @staticmethod
    def _remember_idempotent(
        connection: Any,
        operation_id: str,
        operation_type: str,
        result: dict[str, object],
    ) -> None:
        connection.execute(
            """
            INSERT INTO idempotency_ledger (
                operation_id, operation_type, result_json, created_at
            ) VALUES (?, ?, ?, ?)
            """,
            (
                operation_id,
                operation_type,
                json.dumps(result, ensure_ascii=False, sort_keys=True),
                _iso(),
            ),
        )

    @staticmethod
    def _audit(
        connection: Any,
        action: str,
        result: str,
        *,
        object_id: str | None = None,
        error_category: str | None = None,
    ) -> None:
        if not _AUDIT_CODE.fullmatch(action) or not _AUDIT_CODE.fullmatch(result):
            raise ValueError("audit code")
        if error_category is not None and not _AUDIT_CODE.fullmatch(error_category):
            raise ValueError("audit error category")
        connection.execute(
            """
            INSERT INTO access_audit (
                audit_id, action, object_id, result, error_category, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (uuid4().hex, action, object_id, result, error_category, _iso()),
        )

    @staticmethod
    def _validate_password(password: str) -> None:
        if len(str(password or "")) < _PASSWORD_MINIMUM:
            raise VaultError(
                "vault_password_too_short",
                "密码至少需要 12 个字符，建议使用容易记住的长句",
                status_code=422,
            )

    @staticmethod
    def _validate_operation_id(operation_id: str) -> None:
        if not _OPERATION_ID.fullmatch(str(operation_id or "")):
            raise VaultError(
                "vault_operation_id_invalid",
                "操作编号无效，请刷新页面后重试",
                status_code=422,
            )


__all__ = ["VaultService"]
