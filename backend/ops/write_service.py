from __future__ import annotations

import hashlib
import re
import sqlite3
import time
import uuid
from collections.abc import AsyncIterable, Callable
from pathlib import Path
from typing import Any

from backend.jobs.manager import JobManager
from backend.jobs.store import JobRecord

from data_transfer_service import (
    build_export_manifest,
    default_export_sources,
    normalize_export_scope,
)
from path_manager import PathManager
from question_bank.services.question_read_service import captured_sqlite_snapshot_path
from update_tools.backup_core import VALID_REASONS, preview_backup
from update_tools.migrate_db import preview_migrations

from .archive import OpsArchivePolicy, inspect_zip, stage_zip_upload
from .database_validation import (
    migration_directories,
    validate_archive_databases,
)
from .models import OpsInternalPlan, OpsOperation
from .journal import OpsOperationBusy, OpsOperationJournal, OpsOperationNotFound
from .lock import OpsLockBusy, OpsOperationLock
from .plan_store import OpsPlanStore


class OpsWriteError(RuntimeError):
    pass


class OpsResourceNotFound(OpsWriteError):
    pass


class OpsPreflightStale(OpsWriteError):
    pass


class OpsRequestInvalid(OpsWriteError):
    pass


_UPLOAD_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_OFFLINE_OPERATIONS = {
    OpsOperation.RESTORE,
    OpsOperation.MIGRATION,
    OpsOperation.TRANSFER_IMPORT,
}
_JOB_TYPES = {
    OpsOperation.BACKUP: "ops_backup",
    OpsOperation.RESTORE: "ops_restore_prepare",
    OpsOperation.MIGRATION: "ops_migration_prepare",
    OpsOperation.TRANSFER_IMPORT: "ops_transfer_import_prepare",
    OpsOperation.TRANSFER_EXPORT: "ops_transfer_export",
}


class OpsWriteService:
    def __init__(
        self,
        paths: PathManager,
        *,
        plan_store: OpsPlanStore,
        migration_dirs: dict[str, Path] | None = None,
        archive_policy: OpsArchivePolicy | None = None,
        upload_id_factory: Callable[[], str] | None = None,
        journal: OpsOperationJournal | None = None,
    ) -> None:
        self.paths = paths
        self.plan_store = plan_store
        self.archive_policy = archive_policy or OpsArchivePolicy()
        self.migration_dirs = migration_dirs or {
            "grading": Path(paths.project_root) / "migrations" / "grading",
            "question_bank": Path(paths.project_root) / "migrations" / "question_bank",
        }
        self._upload_id_factory = upload_id_factory or (lambda: uuid.uuid4().hex)
        state_root = Path(
            getattr(
                paths,
                "ops_state_dir",
                Path(paths.data_root).parent / "ops-state",
            )
        )
        self.journal = journal or OpsOperationJournal(state_root)

    async def stage_import_upload(
        self,
        *,
        filename: str,
        chunks: AsyncIterable[bytes],
    ) -> dict[str, object]:
        staged = await stage_zip_upload(
            filename=filename,
            chunks=chunks,
            staging_root=Path(self.paths.ops_state_dir) / "uploads",
            policy=self.archive_policy,
            upload_id_factory=self._upload_id_factory,
        )
        return {
            "upload_id": staged.upload_id,
            "filename": staged.filename,
            "size_bytes": staged.size_bytes,
            "sha256": staged.sha256,
        }

    def preflight(self, request: Any) -> dict[str, object]:
        try:
            raw_operation = request.operation
            operation = (
                raw_operation
                if isinstance(raw_operation, OpsOperation)
                else OpsOperation(str(raw_operation))
            )
        except (AttributeError, ValueError) as exc:
            raise OpsRequestInvalid("unsupported operation") from exc
        if operation is OpsOperation.BACKUP:
            plan = self._preflight_backup(request)
        elif operation is OpsOperation.RESTORE:
            plan = self._preflight_restore(request)
        elif operation is OpsOperation.MIGRATION:
            plan = self._preflight_migration(request)
        elif operation is OpsOperation.TRANSFER_IMPORT:
            plan = self._preflight_transfer_import(request)
        else:
            plan = self._preflight_transfer_export(request)
        token, expires_at = self.plan_store.issue(plan)
        return {
            "operation": operation.value,
            "confirmation_token": token,
            "expires_at": expires_at,
            "requires_restart": operation in _OFFLINE_OPERATIONS,
            "summary": plan.summary,
        }

    def consume_plan(self, confirmation_token: str) -> OpsInternalPlan:
        plan = self.plan_store.consume(confirmation_token)
        current = self._fingerprint_for_plan(plan)
        if current != plan.resource_fingerprint:
            raise OpsPreflightStale("preflight resource has changed")
        return plan

    def build_job_payload(self, plan: OpsInternalPlan) -> dict[str, object]:
        return {
            "operation_id": str(uuid.uuid4()),
            "operation": plan.operation.value,
            "parameters": dict(plan.parameters),
            "resource_fingerprint": plan.resource_fingerprint,
        }

    def resource_fingerprint(
        self,
        operation: OpsOperation,
        parameters: dict[str, object],
    ) -> str:
        return self._fingerprint_for_plan(
            OpsInternalPlan(
                operation=operation,
                parameters=dict(parameters),
                resource_fingerprint="",
                summary={},
                created_monotonic=0.0,
            )
        )

    def submit(
        self,
        confirmation_token: str,
        manager: JobManager,
    ) -> JobRecord:
        try:
            with OpsOperationLock(
                Path(self.paths.ops_state_dir), timeout_seconds=0.0
            ).acquire():
                if self.journal.pending_exists() or manager.store.has_active_job_types(
                    set(_JOB_TYPES.values())
                ):
                    raise OpsOperationBusy("another ops operation is active")
                plan = self.consume_plan(confirmation_token)
                return manager.submit(
                    _JOB_TYPES[plan.operation], self.build_job_payload(plan)
                )
        except OpsLockBusy as exc:
            raise OpsOperationBusy("another ops operation is active") from exc

    def operation_status(self, _operation_id: str) -> dict[str, object]:
        try:
            return self.journal.load_public(_operation_id)
        except OpsOperationNotFound as exc:
            raise OpsResourceNotFound("operation not found") from exc

    def cancel_operation(self, _operation_id: str) -> dict[str, object]:
        try:
            with OpsOperationLock(Path(self.paths.ops_state_dir)).acquire():
                return self.journal.cancel_pending(_operation_id)
        except OpsOperationNotFound as exc:
            raise OpsResourceNotFound("operation not found") from exc

    def migration_previews(self, target: str) -> list[dict[str, Any]]:
        if target not in {"grading", "question_bank", "all"}:
            raise OpsRequestInvalid("invalid migration target")
        targets = ("grading", "question_bank") if target == "all" else (target,)
        return [self._migration_preview(item) for item in targets]

    def migration_files_fingerprint(self, target: str) -> str:
        if target not in {"grading", "question_bank", "all"}:
            raise OpsRequestInvalid("invalid migration target")
        targets = ("grading", "question_bank") if target == "all" else (target,)
        digest = hashlib.sha256()
        for item in targets:
            digest.update(item.encode("ascii"))
            for migration in sorted(Path(self.migration_dirs[item]).glob("*.sql")):
                digest.update(migration.name.encode("utf-8"))
                digest.update(_file_sha256(migration).encode("ascii"))
        return digest.hexdigest()

    def _preflight_backup(self, request: Any) -> OpsInternalPlan:
        reason = str(getattr(request, "reason", "") or "")
        if reason not in VALID_REASONS:
            raise OpsRequestInvalid("invalid backup reason")
        allowed_scopes = ("grading",)
        requested_scopes = list(
            getattr(request, "scopes", ()) or allowed_scopes
        )
        if (
            not requested_scopes
            or len(set(requested_scopes)) != len(requested_scopes)
            or set(requested_scopes) - set(allowed_scopes)
        ):
            raise OpsRequestInvalid("invalid backup scopes")
        scopes = [scope for scope in allowed_scopes if scope in requested_scopes]
        preview = preview_backup(path_manager=self.paths, scopes=scopes)
        summary = {
            "file_count": int(preview["file_count"]),
            "total_size_bytes": int(preview["total_size"]),
            "database_count": sum(
                str(name).casefold().endswith(".db")
                for name in preview["files"]
            ),
            "skipped_count": len(preview["skipped"]),
            "sensitive_skipped_count": len(preview["skipped_sensitive"]),
            "warnings": [],
        }
        parameters = {"reason": reason, "scopes": scopes}
        return self._plan(OpsOperation.BACKUP, parameters, summary)

    def _preflight_restore(self, request: Any) -> OpsInternalPlan:
        filename = str(getattr(request, "backup_filename", "") or "")
        archive = self._controlled_backup(filename)
        inspection = inspect_zip(
            archive,
            policy=self.archive_policy,
            allowed_roots={"user_data", "config", "logs"},
        )
        validate_archive_databases(
            archive,
            inspection,
            migration_dirs=self._database_migration_dirs(),
        )
        database_count = sum(
            member.parts[:2] == ("user_data", "databases") and not member.is_dir
            for member in inspection.members
        )
        summary = {
            "file_count": inspection.file_count,
            "total_expanded_bytes": inspection.total_expanded_bytes,
            "database_count": database_count,
            "warnings": [],
        }
        return self._plan(
            OpsOperation.RESTORE,
            {"backup_filename": filename},
            summary,
            resource_fingerprint=_file_sha256(archive),
        )

    def _preflight_migration(self, request: Any) -> OpsInternalPlan:
        target = str(getattr(request, "target", "") or "")
        if target not in {"grading", "question_bank", "all"}:
            raise OpsRequestInvalid("invalid migration target")
        targets = ("grading", "question_bank") if target == "all" else (target,)
        previews = self.migration_previews(target)
        summary = {
            "target": target,
            "pending_migrations": sum(item["pending_migrations"] for item in previews),
            "applied_in_preview": sum(len(item["applied"]) for item in previews),
            "integrity": "ok",
            "warnings": [],
        }
        return self._plan(OpsOperation.MIGRATION, {"target": target}, summary)

    def _preflight_transfer_import(self, request: Any) -> OpsInternalPlan:
        upload_id = str(getattr(request, "upload_id", "") or "").lower()
        archive = self._upload_path(upload_id)
        inspection = inspect_zip(
            archive,
            policy=self.archive_policy,
            allowed_roots={"user_data", "config"},
        )
        validate_archive_databases(
            archive,
            inspection,
            migration_dirs=self._database_migration_dirs(),
        )
        summary = {
            "file_count": inspection.file_count,
            "total_expanded_bytes": inspection.total_expanded_bytes,
            "database_count": sum(
                member.parts[:2] == ("user_data", "databases") and not member.is_dir
                for member in inspection.members
            ),
            "warnings": [],
        }
        return self._plan(
            OpsOperation.TRANSFER_IMPORT,
            {"upload_id": upload_id},
            summary,
            resource_fingerprint=_file_sha256(archive),
        )

    def _preflight_transfer_export(self, request: Any) -> OpsInternalPlan:
        try:
            scope = normalize_export_scope(str(getattr(request, "scope", "") or ""))
        except ValueError as exc:
            raise OpsRequestInvalid("invalid export scope") from exc
        entries = build_export_manifest(
            default_export_sources(Path(self.paths.project_root), Path(self.paths.data_root)),
            scope=scope,
        )
        summary = {
            "scope": scope,
            "file_count": len(entries),
            "total_size_bytes": sum(entry.size_bytes for entry in entries),
            "warnings": [],
        }
        fingerprint = _entry_fingerprint(
            entries,
            sqlite_ignored_data_tables={
                Path(self.paths.db_path): frozenset({"jobs"}),
                Path(self.paths.qb_db_path): frozenset(),
            },
        )
        return self._plan(
            OpsOperation.TRANSFER_EXPORT,
            {"scope": scope},
            summary,
            resource_fingerprint=fingerprint,
        )

    def _plan(
        self,
        operation: OpsOperation,
        parameters: dict[str, object],
        summary: dict[str, object],
        *,
        resource_fingerprint: str | None = None,
    ) -> OpsInternalPlan:
        plan = OpsInternalPlan(
            operation=operation,
            parameters=parameters,
            resource_fingerprint=resource_fingerprint or "",
            summary=summary,
            created_monotonic=time.monotonic(),
        )
        if resource_fingerprint is None:
            plan = OpsInternalPlan(
                operation=operation,
                parameters=parameters,
                resource_fingerprint=self._fingerprint_for_plan(plan),
                summary=summary,
                created_monotonic=plan.created_monotonic,
            )
        return plan

    def _fingerprint_for_plan(self, plan: OpsInternalPlan) -> str:
        if plan.operation is OpsOperation.RESTORE:
            return _file_sha256(self._controlled_backup(str(plan.parameters["backup_filename"])))
        if plan.operation is OpsOperation.TRANSFER_IMPORT:
            return _file_sha256(self._upload_path(str(plan.parameters["upload_id"])))
        if plan.operation is OpsOperation.TRANSFER_EXPORT:
            entries = build_export_manifest(
                default_export_sources(Path(self.paths.project_root), Path(self.paths.data_root)),
                scope=str(plan.parameters["scope"]),
            )
            return _entry_fingerprint(
                entries,
                sqlite_ignored_data_tables={
                    Path(self.paths.db_path): frozenset({"jobs"}),
                    Path(self.paths.qb_db_path): frozenset(),
                },
            )
        if plan.operation is OpsOperation.MIGRATION:
            target = str(plan.parameters["target"])
            targets = ("grading", "question_bank") if target == "all" else (target,)
            digest = hashlib.sha256()
            for item in targets:
                source = self.paths.db_path if item == "grading" else self.paths.qb_db_path
                digest.update(_file_sha256(Path(source)).encode("ascii"))
                for migration in sorted(Path(self.migration_dirs[item]).glob("*.sql")):
                    digest.update(migration.name.encode("utf-8"))
                    digest.update(_file_sha256(migration).encode("ascii"))
            return digest.hexdigest()
        scopes = (
            list(plan.parameters.get("scopes") or [])
            if plan.operation is OpsOperation.BACKUP
            else None
        )
        preview = preview_backup(path_manager=self.paths, scopes=scopes)
        return _path_entries_fingerprint(
            [
                (str(name), self._path_for_backup_name(str(name)))
                for name in preview["files"]
            ],
            sqlite_ignored_data_tables={
                Path(self.paths.db_path): frozenset({"jobs"}),
                Path(self.paths.qb_db_path): frozenset(),
            },
        )

    def _migration_preview(self, target: str) -> dict[str, Any]:
        source = Path(self.paths.db_path if target == "grading" else self.paths.qb_db_path)
        with captured_sqlite_snapshot_path(source, required_tables=frozenset()) as candidate:
            return preview_migrations(
                target,
                db_path=candidate,
                migrations_dir=Path(self.migration_dirs[target]),
            )

    def _database_migration_dirs(self) -> dict[str, Path]:
        directories = migration_directories(self.paths)
        directories.update(
            {name: Path(path) for name, path in self.migration_dirs.items()}
        )
        return directories

    def _controlled_backup(self, filename: str) -> Path:
        if not filename or Path(filename).name != filename or not filename.startswith("backup_") or not filename.lower().endswith(".zip"):
            raise OpsResourceNotFound("backup resource not found")
        path = Path(self.paths.backups_dir) / filename
        if not path.is_file() or path.is_symlink():
            raise OpsResourceNotFound("backup resource not found")
        return path

    def _upload_path(self, upload_id: str) -> Path:
        if not _UPLOAD_ID_RE.fullmatch(upload_id):
            raise OpsResourceNotFound("upload resource not found")
        path = Path(self.paths.ops_state_dir) / "uploads" / f"{upload_id}.zip"
        if not path.is_file() or path.is_symlink():
            raise OpsResourceNotFound("upload resource not found")
        return path

    def _path_for_backup_name(self, name: str) -> Path:
        from update_tools.backup_core import taxonomy_backup_target
        if target := taxonomy_backup_target(self.paths, name):
            return target
        parts = Path(name).parts
        if parts[0] == "user_data":
            return Path(self.paths.data_root).joinpath(*parts[1:])
        if parts[0] == "config":
            return Path(self.paths.project_root).joinpath(*parts)
        if parts[0] == "logs":
            return Path(self.paths.logs_dir).joinpath(*parts[1:])
        raise OpsRequestInvalid("invalid backup manifest")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _entry_fingerprint(
    entries: list[Any],
    *,
    sqlite_ignored_data_tables: dict[Path, frozenset[str]] | None = None,
) -> str:
    return _path_entries_fingerprint(
        [
            (str(entry.arc_name), Path(entry.source_path))
            for entry in entries
        ],
        sqlite_ignored_data_tables=sqlite_ignored_data_tables,
    )


def _path_entries_fingerprint(
    entries: list[tuple[str, Path]],
    *,
    sqlite_ignored_data_tables: dict[Path, frozenset[str]] | None = None,
) -> str:
    digest = hashlib.sha256()
    sqlite_tables_by_path = {
        Path(path).resolve(strict=False): ignored_tables
        for path, ignored_tables in (sqlite_ignored_data_tables or {}).items()
    }
    for arc_name, source_path in entries:
        digest.update(arc_name.encode("utf-8"))
        ignored_tables = sqlite_tables_by_path.get(
            source_path.resolve(strict=False)
        )
        if ignored_tables is not None:
            digest.update(
                _sqlite_logical_fingerprint(
                    source_path,
                    ignored_data_tables=ignored_tables,
                ).encode("ascii")
            )
            continue
        try:
            stat_result = source_path.stat()
            digest.update(str(stat_result.st_size).encode("ascii"))
            digest.update(str(stat_result.st_mtime_ns).encode("ascii"))
        except OSError:
            digest.update(b"missing")
    return digest.hexdigest()


def _sqlite_logical_fingerprint(
    path: Path,
    *,
    ignored_data_tables: frozenset[str],
) -> str:
    digest = hashlib.sha256()
    ignored = {name.casefold() for name in ignored_data_tables}
    uri = f"{Path(path).resolve(strict=True).as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("BEGIN")
        schema_rows = connection.execute(
            "SELECT type, name, tbl_name, sql "
            "FROM sqlite_schema "
            "WHERE name NOT LIKE 'sqlite_%' "
            "ORDER BY type, name"
        ).fetchall()
        for row in schema_rows:
            _digest_sqlite_row(digest, row)

        table_rows = connection.execute(
            "SELECT name FROM sqlite_schema "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        ).fetchall()
        for (table_name_value,) in table_rows:
            table_name = str(table_name_value)
            if table_name.casefold() in ignored:
                continue
            quoted = _quote_sqlite_identifier(table_name)
            table_info = connection.execute(
                f"PRAGMA table_info({quoted})"
            ).fetchall()
            primary_key_columns = [
                str(row[1])
                for row in sorted(table_info, key=lambda row: int(row[5]))
                if int(row[5]) > 0
            ]
            digest.update(table_name.encode("utf-8"))
            if primary_key_columns:
                order_by = ", ".join(
                    _quote_sqlite_identifier(name)
                    for name in primary_key_columns
                )
                rows = connection.execute(
                    f"SELECT * FROM {quoted} ORDER BY {order_by}"
                )
            else:
                rows = connection.execute(
                    f"SELECT rowid, * FROM {quoted} ORDER BY rowid"
                )
            for row in rows:
                _digest_sqlite_row(digest, row)

        has_sequence = connection.execute(
            "SELECT 1 FROM sqlite_schema "
            "WHERE type = 'table' AND name = 'sqlite_sequence'"
        ).fetchone()
        if has_sequence is not None:
            for row in connection.execute(
                "SELECT name, seq FROM sqlite_sequence ORDER BY name"
            ):
                if str(row[0]).casefold() not in ignored:
                    _digest_sqlite_row(digest, row)
        connection.rollback()
    finally:
        connection.close()
    return digest.hexdigest()


def _quote_sqlite_identifier(value: str) -> str:
    return '"' + str(value).replace('"', '""') + '"'


def _digest_sqlite_row(
    digest: Any,
    row: Any,
) -> None:
    for value in row:
        if value is None:
            marker, encoded = b"N", b""
        elif isinstance(value, bytes):
            marker, encoded = b"B", value
        elif isinstance(value, int):
            marker, encoded = b"I", str(value).encode("ascii")
        elif isinstance(value, float):
            marker, encoded = b"F", value.hex().encode("ascii")
        else:
            marker, encoded = b"T", str(value).encode("utf-8")
        digest.update(marker)
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)


__all__ = [
    "OpsPreflightStale",
    "OpsRequestInvalid",
    "OpsResourceNotFound",
    "OpsWriteError",
    "OpsWriteService",
]
