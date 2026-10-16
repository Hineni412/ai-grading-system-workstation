from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import AsyncIterable, Callable
from pathlib import Path
from typing import Any

from backend.jobs.manager import JobManager
from backend.jobs.store import JobRecord
from backend.files.data_transfer_service import (
    build_export_manifest,
    default_export_sources,
    normalize_export_scope,
)
from path_manager import PathManager
from update_tools.backup_core import VALID_REASONS, preview_backup
from update_tools.migrate_db import preview_migrations

from .archive import OpsArchivePolicy, inspect_zip, stage_zip_upload
from .database_validation import (
    migration_directories,
    validate_archive_databases,
)
from .journal import OpsOperationBusy, OpsOperationJournal, OpsOperationNotFound
from .lock import OpsLockBusy, OpsOperationLock
from .models import OpsInternalPlan, OpsOperation
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
# Operations whose preview binds to a fingerprinted resource that is rechecked
# at consume time. BACKUP and TRANSFER_EXPORT are read-only and carry no
# resource fingerprint.
_RESOURCE_BOUND_OPERATIONS = frozenset(
    {
        OpsOperation.RESTORE,
        OpsOperation.MIGRATION,
        OpsOperation.TRANSFER_IMPORT,
    }
)


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
        if plan.operation in _RESOURCE_BOUND_OPERATIONS:
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
        return json.dumps([
            (item, migration.name, _file_revision(migration))
            for item in targets
            for migration in sorted(Path(self.migration_dirs[item]).glob("*.sql"))
        ], separators=(",", ":"))

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
            resource_fingerprint=_file_revision(archive),
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
            resource_fingerprint=_file_revision(archive),
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
        return self._plan(
            OpsOperation.TRANSFER_EXPORT,
            {"scope": scope},
            summary,
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
        if resource_fingerprint is None and operation in _RESOURCE_BOUND_OPERATIONS:
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
            return _file_revision(self._controlled_backup(str(plan.parameters["backup_filename"])))
        if plan.operation is OpsOperation.TRANSFER_IMPORT:
            return _file_revision(self._upload_path(str(plan.parameters["upload_id"])))
        if plan.operation is OpsOperation.MIGRATION:
            return self.migration_files_fingerprint(str(plan.parameters["target"]))
        return ""

    def _migration_preview(self, target: str) -> dict[str, Any]:
        from question_bank.services.question_read_service import (
            captured_sqlite_snapshot_path,
        )

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

def _file_revision(path: Path) -> str:
    info = Path(path).stat()
    return f"file:{info.st_mtime_ns}:{info.st_size}"


__all__ = [
    "OpsPreflightStale",
    "OpsRequestInvalid",
    "OpsResourceNotFound",
    "OpsWriteError",
    "OpsWriteService",
]
