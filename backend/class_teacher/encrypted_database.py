from __future__ import annotations

import os
import json
import sqlite3
import zipfile
from contextlib import closing
from pathlib import Path
from typing import Any, Callable

from backend.schema_migrations import ensure_schema_current
from backend.workspaces.contracts import WorkspaceContext

from .errors import VaultError


class _SilentMigrationLogger:
    def info(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def error(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class EncryptedDatabase:
    """Owns the class-teacher SQLite file and its atomic recovery helpers."""

    def __init__(self, context: WorkspaceContext) -> None:
        self.root = context.root
        self.database_path = self.root / "student_affairs.db"
        self.backup_dir = self.root / "backups"
        project_root = Path(
            getattr(context.paths, "migration_project_root", context.paths.project_root)
        )
        self.migrations_dir = project_root / "migrations" / "student_affairs"
        self.recover_interrupted_operations()

    @property
    def exists(self) -> bool:
        return self.database_path.is_file()

    def initialize_schema(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        try:
            ensure_schema_current(
                "student_affairs",
                self.database_path,
                migrations_dir=self.migrations_dir,
                backup_dir=self.backup_dir,
                logger_override=_SilentMigrationLogger(),
                allow_existing_migrations=False,
            )
        except Exception as exc:
            raise VaultError(
                "vault_initialization_failed",
                "保险箱初始化失败，没有创建可用数据",
                status_code=500,
            ) from exc

    def connect(self) -> sqlite3.Connection:
        if not self.exists:
            raise VaultError(
                "vault_not_initialized",
                "班主任工作台尚未初始化",
                status_code=409,
            )
        connection = sqlite3.connect(self.database_path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA secure_delete = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def snapshot_bytes(self) -> bytes:
        try:
            with closing(self.connect()) as source, closing(
                sqlite3.connect(":memory:")
            ) as target:
                source.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                source.execute("PRAGMA journal_mode = DELETE")
                try:
                    source.backup(target)
                    target.row_factory = sqlite3.Row
                    if (
                        target.execute("PRAGMA integrity_check").fetchone()[0]
                        != "ok"
                    ):
                        raise ValueError("integrity")
                    return target.serialize()
                finally:
                    source.execute("PRAGMA journal_mode = WAL")
        except VaultError:
            raise
        except Exception as exc:
            raise VaultError(
                "vault_snapshot_failed",
                "无法创建一致的保险箱快照，现有数据没有改变",
                status_code=409,
            ) from exc

    def isolated_copy(
        self,
        payload: bytes,
        *,
        root: Path,
    ) -> EncryptedDatabase:
        """Build a private rewrite candidate without touching the live vault."""
        self.validate_snapshot(payload)
        root.mkdir(parents=True, exist_ok=True)
        candidate = object.__new__(EncryptedDatabase)
        candidate.root = root
        candidate.database_path = root / "student_affairs.db"
        candidate.backup_dir = root / "backups"
        candidate.migrations_dir = self.migrations_dir
        candidate.backup_dir.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(":memory:")) as source:
            source.deserialize(payload)
            with closing(sqlite3.connect(candidate.database_path)) as target:
                source.backup(target)
        return candidate

    @staticmethod
    def validate_snapshot(payload: bytes) -> None:
        try:
            with closing(sqlite3.connect(":memory:")) as connection:
                connection.deserialize(payload)
                if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("integrity")
                required = {
                    "vault_metadata",
                    "encrypted_objects",
                    "access_audit",
                    "idempotency_ledger",
                    "encrypted_backup_records",
                }
                present = {
                    str(row[0])
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                if not required.issubset(present):
                    raise ValueError("schema")
        except Exception as exc:
            raise VaultError(
                "vault_backup_invalid",
                "专用备份没有通过结构与完整性校验",
                status_code=409,
            ) from exc

    def replace_from_snapshot(
        self,
        payload: bytes,
        *,
        finalize: Callable[[], None] | None = None,
    ) -> None:
        self.validate_snapshot(payload)
        self.recover_interrupted_operations()
        rollback = self.root / ".restore-rollback.db"
        try:
            self._publish_snapshot_file(
                self.snapshot_bytes(),
                rollback,
            )
            self.replace_from_snapshot_atomically(payload)
            self.initialize_schema()
            if finalize is not None:
                finalize()
            rollback.unlink(missing_ok=True)
        except Exception as exc:
            self.recover_interrupted_operations()
            raise VaultError(
                "vault_restore_failed",
                "恢复未完成，原保险箱已经保留",
                status_code=409,
            ) from exc

    def replace_from_snapshot_atomically(self, payload: bytes) -> None:
        """Replace the live database in one filesystem operation."""
        self.validate_snapshot(payload)
        candidate = self.root / ".restore-candidate.db"
        candidate.unlink(missing_ok=True)
        self._write_snapshot_file(payload, candidate)
        try:
            if self.database_path.exists():
                with closing(self.connect()) as current:
                    current.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    current.execute("PRAGMA journal_mode = DELETE")
            for suffix in ("-wal", "-shm"):
                self.database_path.with_name(
                    self.database_path.name + suffix
                ).unlink(missing_ok=True)
            os.replace(candidate, self.database_path)
            with closing(self.connect()) as restored:
                if (
                    restored.execute("PRAGMA integrity_check").fetchone()[0]
                    != "ok"
                ):
                    raise ValueError("integrity")
        finally:
            candidate.unlink(missing_ok=True)

    def create_subject_delete_transaction(
        self,
        *,
        operation_id: str,
        database_snapshot: bytes,
        backup_paths: list[Path],
    ) -> Path:
        self.validate_snapshot(database_snapshot)
        transaction = (
            self.root
            / f".subject-delete-{operation_id}.rollback.cttxn"
        )
        temporary = transaction.with_suffix(".cttxn.tmp")
        transaction.unlink(missing_ok=True)
        temporary.unlink(missing_ok=True)
        manifest = {
            "version": 1,
            "operation_id": operation_id,
            "backup_names": [path.name for path in backup_paths],
        }
        try:
            with zipfile.ZipFile(
                temporary,
                mode="w",
                compression=zipfile.ZIP_STORED,
            ) as archive:
                archive.writestr(
                    "manifest.json",
                    json.dumps(manifest, sort_keys=True),
                )
                archive.writestr(
                    "student_affairs.snapshot",
                    database_snapshot,
                )
                for index, path in enumerate(backup_paths):
                    archive.write(path, f"backups/{index}.ctbackup")
            with temporary.open("r+b") as handle:
                handle.flush()
                os.fsync(handle.fileno())
            self._read_subject_delete_transaction(temporary)
            os.replace(temporary, transaction)
            return transaction
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    def recover_interrupted_operations(self) -> None:
        if not self.root.exists():
            return
        rollback = self.root / ".restore-rollback.db"
        rollback_temporary = self.root / ".restore-rollback.db.tmp"
        candidate = self.root / ".restore-candidate.db"
        rollback_temporary.unlink(missing_ok=True)
        if rollback.is_file():
            try:
                rollback_payload = rollback.read_bytes()
                self.validate_snapshot(rollback_payload)
                self.replace_from_snapshot_atomically(rollback_payload)
                rollback.unlink()
            except Exception as exc:
                raise VaultError(
                    "vault_interrupted_restore_recovery_failed",
                    "上次保险箱替换未完成，旧保险箱无法自动恢复",
                    status_code=409,
                ) from exc
        candidate.unlink(missing_ok=True)
        temporary_transactions = list(
            self.root.glob(".subject-delete-*.rollback.cttxn.tmp")
        )
        for temporary in temporary_transactions:
            temporary.unlink(missing_ok=True)
        transactions = list(
            self.root.glob(".subject-delete-*.rollback.cttxn")
        )
        if len(transactions) > 1:
            raise VaultError(
                "support_delete_recovery_ambiguous",
                "检测到多个未完成删除事务，无法自动判断恢复顺序",
                status_code=409,
            )
        for transaction in transactions:
            self._recover_subject_delete_transaction(transaction)

    def commit_subject_delete_transaction(self, transaction: Path) -> None:
        transaction.unlink()

    def _recover_subject_delete_transaction(
        self,
        transaction: Path,
    ) -> None:
        try:
            manifest, database_snapshot, backups = (
                self._read_subject_delete_transaction(transaction)
            )
            self.replace_from_snapshot_atomically(database_snapshot)
            self.backup_dir.mkdir(parents=True, exist_ok=True)
            for name, payload in backups.items():
                destination = self.backup_dir / name
                temporary = destination.with_suffix(
                    destination.suffix + ".restore-tmp"
                )
                temporary.write_bytes(payload)
                os.replace(temporary, destination)
            operation_id = str(manifest["operation_id"])
            for pending in self.backup_dir.glob(
                f"*.ctbackup.{operation_id}.pending-delete"
            ):
                pending.unlink(missing_ok=True)
            transaction.unlink()
        except Exception as exc:
            raise VaultError(
                "support_delete_transaction_recovery_failed",
                "上次学生删除未完成，原保险箱和专用备份无法自动恢复",
                status_code=409,
            ) from exc

    def _read_subject_delete_transaction(
        self,
        transaction: Path,
    ) -> tuple[dict[str, Any], bytes, dict[str, bytes]]:
        with zipfile.ZipFile(transaction, mode="r") as archive:
            if archive.testzip() is not None:
                raise ValueError("transaction_crc")
            manifest = json.loads(archive.read("manifest.json"))
            if (
                manifest.get("version") != 1
                or not isinstance(manifest.get("operation_id"), str)
                or not isinstance(manifest.get("backup_names"), list)
            ):
                raise ValueError("transaction_manifest")
            names = [str(value) for value in manifest["backup_names"]]
            if any(
                not name
                or Path(name).name != name
                or not name.endswith(".ctbackup")
                for name in names
            ):
                raise ValueError("transaction_backup_name")
            database_snapshot = archive.read(
                "student_affairs.snapshot"
            )
            self.validate_snapshot(database_snapshot)
            backups = {
                name: archive.read(f"backups/{index}.ctbackup")
                for index, name in enumerate(names)
            }
        return manifest, database_snapshot, backups

    @staticmethod
    def _write_snapshot_file(payload: bytes, destination: Path) -> None:
        destination.unlink(missing_ok=True)
        with closing(sqlite3.connect(":memory:")) as source:
            source.deserialize(payload)
            with closing(sqlite3.connect(destination)) as target:
                source.backup(target)
                if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("integrity")

    def _publish_snapshot_file(
        self,
        payload: bytes,
        destination: Path,
    ) -> None:
        temporary = destination.with_suffix(
            destination.suffix + ".tmp"
        )
        temporary.unlink(missing_ok=True)
        try:
            self._write_snapshot_file(payload, temporary)
            self.validate_snapshot(temporary.read_bytes())
            with temporary.open("r+b") as handle:
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)


__all__ = ["EncryptedDatabase"]
