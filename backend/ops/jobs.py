from __future__ import annotations

import os
import sqlite3
import tempfile
from collections.abc import Iterable
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.jobs.execution_locks import keyed_execution_locks
from backend.jobs.manager import JobContext, JobManager
from data_transfer_service import (
    ExportEntry,
    build_export_manifest,
    default_export_sources,
    write_export_zip,
)
from update_tools.backup_core import preview_backup

from .models import OpsOperation
from .archive import OpsArchivePolicy, extract_validated_zip, inspect_zip
from .database_validation import (
    DATABASE_MEMBERS,
    migration_directories,
    validate_archive_databases,
    validate_staged_databases,
)
from .journal import OpsOperationJournal, OpsOperationManifest
from .lock import OpsOperationLock
from .plan_store import OpsPlanStore
from .write_service import OpsWriteService


class OpsPreBackupFailed(RuntimeError):
    pass


def register_ops_job_handlers(manager: JobManager, *, paths: Any) -> None:
    manager.register(
        "ops_backup",
        lambda context: run_ops_backup_job(context=context, paths=paths),
    )
    manager.register(
        "ops_transfer_export",
        lambda context: run_ops_transfer_export_job(context=context, paths=paths),
    )
    manager.register(
        "ops_restore_prepare",
        lambda context: run_ops_restore_prepare_job(context=context, paths=paths),
    )
    manager.register(
        "ops_migration_prepare",
        lambda context: run_ops_migration_prepare_job(context=context, paths=paths),
    )
    manager.register(
        "ops_transfer_import_prepare",
        lambda context: run_ops_transfer_import_prepare_job(context=context, paths=paths),
    )


def run_ops_backup_job(*, context: JobContext, paths: Any) -> dict[str, object]:
    parameters = _validate_online_payload(context, OpsOperation.BACKUP, paths)
    reason = str(parameters.get("reason") or "")
    if not reason:
        raise ValueError("backup reason is required")
    with (
        OpsOperationLock(Path(paths.ops_state_dir)).acquire(
            cancel_check=context.raise_if_cancelled
        ),
        keyed_execution_locks(["ops-write"], cancel_check=context.raise_if_cancelled),
    ):
        context.report(0.05, "ops_backup", "preparing")
        output_root = Path(paths.backups_dir)
        output_root.mkdir(parents=True, exist_ok=True)
        filename = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{reason}.zip"
        destination = output_root / filename
        if destination.exists():
            raise FileExistsError("backup destination already exists")
        with tempfile.TemporaryDirectory(
            dir=output_root,
            prefix=f".job-{context.job_id}-",
        ) as staging_value:
            staging_root = Path(staging_value)
            archive_path = staging_root / "backup.zip"
            scopes = list(parameters.get("scopes") or [])
            entries = _backup_entries(paths, staging_root, scopes=scopes)
            write_export_zip(entries, archive_path)
            _validate_zip(archive_path, paths=paths)
            context.report(0.9, "ops_backup", "ready_to_publish")
            context.raise_if_cancelled()
            os.replace(archive_path, destination)
        context.report(1.0, "ops_backup", "published")
        return {
            "operation_id": str(context.payload["operation_id"]),
            "operation": OpsOperation.BACKUP.value,
            "outcome": "published",
            "filename": filename,
            "file_path": str(destination),
            "file_count": len(entries),
        }


def run_ops_transfer_export_job(
    *,
    context: JobContext,
    paths: Any,
) -> dict[str, object]:
    parameters = _validate_online_payload(
        context,
        OpsOperation.TRANSFER_EXPORT,
        paths,
    )
    scope = str(parameters.get("scope") or "")
    if scope not in {"lean", "full"}:
        raise ValueError("export scope is invalid")
    with (
        OpsOperationLock(Path(paths.ops_state_dir)).acquire(
            cancel_check=context.raise_if_cancelled
        ),
        keyed_execution_locks(["ops-write"], cancel_check=context.raise_if_cancelled),
    ):
        context.report(0.05, "ops_transfer_export", "preparing")
        output_root = Path(paths.outputs_dir) / "ops"
        output_root.mkdir(parents=True, exist_ok=True)
        filename = (
            f"data_export_{scope}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_"
            f"{context.job_id}.zip"
        )
        destination = output_root / filename
        with tempfile.TemporaryDirectory(
            dir=output_root,
            prefix=f".job-{context.job_id}-",
        ) as staging_value:
            staging_root = Path(staging_value)
            archive_path = staging_root / "export.zip"
            entries = _transfer_export_entries(
                paths,
                staging_root,
                scope=scope,
            )
            write_export_zip(entries, archive_path)
            _validate_zip(archive_path, paths=paths)
            context.report(0.9, "ops_transfer_export", "ready_to_publish")
            context.raise_if_cancelled()
            os.replace(archive_path, destination)
        context.report(1.0, "ops_transfer_export", "published")
        return {
            "operation_id": str(context.payload["operation_id"]),
            "operation": OpsOperation.TRANSFER_EXPORT.value,
            "outcome": "published",
            "filename": filename,
            "file_path": str(destination),
            "file_count": len(entries),
        }


def create_safety_backup(
    *,
    paths: Any,
    reason: str,
    operation_id: str,
    archive_names: Iterable[str],
) -> Path:
    output_root = Path(paths.backups_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    filename = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{reason}.zip"
    destination = output_root / filename
    if destination.exists():
        filename = (
            f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{reason}_"
            f"{operation_id[:8]}.zip"
        )
        destination = output_root / filename
    try:
        with tempfile.TemporaryDirectory(
            dir=output_root,
            prefix=f".safety-{operation_id}-",
        ) as staging_value:
            staging_root = Path(staging_value)
            archive_path = staging_root / "safety.zip"
            entries = _safety_backup_entries(
                paths,
                staging_root,
                archive_names=archive_names,
            )
            write_export_zip(entries, archive_path)
            _validate_zip(
                archive_path,
                paths=paths,
                required_members={str(entry.arc_name) for entry in entries},
            )
            os.replace(archive_path, destination)
    except Exception as exc:
        destination.unlink(missing_ok=True)
        raise OpsPreBackupFailed("preparation safety backup failed") from exc
    return destination


def run_ops_restore_prepare_job(
    *,
    context: JobContext,
    paths: Any,
) -> dict[str, object]:
    return _run_offline_prepare(
        context=context,
        paths=paths,
        operation=OpsOperation.RESTORE,
        backup_reason="before_restore",
    )


def run_ops_transfer_import_prepare_job(
    *,
    context: JobContext,
    paths: Any,
) -> dict[str, object]:
    return _run_offline_prepare(
        context=context,
        paths=paths,
        operation=OpsOperation.TRANSFER_IMPORT,
        backup_reason="before_import",
    )


def run_ops_migration_prepare_job(
    *,
    context: JobContext,
    paths: Any,
) -> dict[str, object]:
    return _run_offline_prepare(
        context=context,
        paths=paths,
        operation=OpsOperation.MIGRATION,
        backup_reason="before_update",
    )


def _run_offline_prepare(
    *,
    context: JobContext,
    paths: Any,
    operation: OpsOperation,
    backup_reason: str,
) -> dict[str, object]:
    parameters = _validate_online_payload(context, operation, paths)
    operation_id = str(context.payload.get("operation_id") or "")
    journal = OpsOperationJournal(Path(paths.ops_state_dir))
    with (
        OpsOperationLock(Path(paths.ops_state_dir)).acquire(
            cancel_check=context.raise_if_cancelled
        ),
        keyed_execution_locks(["ops-write"], cancel_check=context.raise_if_cancelled),
    ):
        if journal.pending_exists():
            from .journal import OpsOperationBusy

            raise OpsOperationBusy("another ops operation is pending")
        operation_root = Path(paths.ops_state_dir) / "operations" / operation_id
        staging_root = operation_root / "staging"
        try:
            if operation is OpsOperation.RESTORE:
                source = Path(paths.backups_dir) / str(parameters["backup_filename"])
                extract_validated_zip(
                    source,
                    staging_root,
                    policy=OpsWriteService(paths, plan_store=OpsPlanStore()).archive_policy,
                    allowed_roots={"user_data", "config", "logs"},
                )
                validate_staged_databases(
                    staging_root,
                    migration_dirs=migration_directories(paths),
                )
            elif operation is OpsOperation.TRANSFER_IMPORT:
                source = (
                    Path(paths.ops_state_dir)
                    / "uploads"
                    / f"{parameters['upload_id']}.zip"
                )
                extract_validated_zip(
                    source,
                    staging_root,
                    policy=OpsWriteService(paths, plan_store=OpsPlanStore()).archive_policy,
                    allowed_roots={"user_data", "config"},
                )
                validate_staged_databases(
                    staging_root,
                    migration_dirs=migration_directories(paths),
                )
            else:
                staging_root.mkdir(parents=True, exist_ok=True)
                OpsWriteService(paths, plan_store=OpsPlanStore()).migration_previews(
                    str(parameters["target"])
                )
            context.raise_if_cancelled()
            context.report(
                0.1,
                f"ops_{operation.value}_prepare",
                "creating_safety_backup",
            )
            replacement_names = _prepared_replacement_names(
                operation=operation,
                parameters=parameters,
                staging_root=staging_root,
            )
            preparation_backup = create_safety_backup(
                paths=paths,
                reason=backup_reason,
                operation_id=operation_id,
                archive_names=replacement_names,
            )
            context.raise_if_cancelled()
            created_at = datetime.now().astimezone().isoformat(timespec="seconds")
            manifest = OpsOperationManifest(
                operation_id=operation_id,
                operation=operation.value,
                parameters=parameters,
                resource_fingerprint=(
                    OpsWriteService(
                        paths,
                        plan_store=OpsPlanStore(),
                    ).migration_files_fingerprint(str(parameters["target"]))
                    if operation is OpsOperation.MIGRATION
                    else str(context.payload["resource_fingerprint"])
                ),
                staging_root=str(staging_root),
                preparation_backup=str(preparation_backup),
                created_at=created_at,
            )
            journal.prepare(manifest)
        except Exception:
            if operation_root.exists():
                import shutil

                shutil.rmtree(operation_root, ignore_errors=True)
            raise
        context.report(1.0, f"ops_{operation.value}_prepare", "restart_required")
        result = {
            "operation_id": operation_id,
            "operation": operation.value,
            "outcome": "prepared_restart_required",
            "backup_filename": preparation_backup.name,
        }
        if operation is OpsOperation.MIGRATION:
            result["target"] = str(parameters["target"])
        return result


def _validate_online_payload(
    context: JobContext,
    operation: OpsOperation,
    paths: Any,
) -> dict[str, object]:
    if str(context.payload.get("operation") or "") != operation.value:
        raise ValueError("ops job operation is invalid")
    parameters = context.payload.get("parameters")
    if not isinstance(parameters, dict):
        raise ValueError("ops job parameters are invalid")
    expected = str(context.payload.get("resource_fingerprint") or "")
    service = OpsWriteService(paths, plan_store=OpsPlanStore())
    current = service.resource_fingerprint(operation, parameters)
    if not expected or current != expected:
        raise ValueError("preflight resource changed")
    return dict(parameters)


def _backup_entries(
    paths: Any,
    staging_root: Path,
    *,
    scopes: list[object],
) -> list[ExportEntry]:
    preview = preview_backup(path_manager=paths, scopes=[str(item) for item in scopes])
    entries: list[ExportEntry] = []
    snapshots = {
        "user_data/databases/grading_system.db": (
            Path(paths.db_path),
            staging_root / "grading_system.db",
        ),
        "user_data/databases/question_bank.db": (
            Path(paths.qb_db_path),
            staging_root / "question_bank.db",
        ),
    }
    for arc_name in preview["files"]:
        name = str(arc_name)
        if name.endswith((".db-wal", ".db-shm")):
            continue
        if name in snapshots:
            source, snapshot = snapshots[name]
            _sqlite_snapshot(source, snapshot)
            entries.append(
                ExportEntry(snapshot, name, snapshot.stat().st_size, staging_root)
            )
            continue
        source = _backup_source_path(paths, name)
        try:
            size_bytes = source.stat().st_size
        except OSError:
            continue
        entries.append(
            ExportEntry(source, name, size_bytes, _backup_source_root(paths, name))
        )
    return entries


def _safety_backup_entries(
    paths: Any,
    staging_root: Path,
    *,
    archive_names: Iterable[str],
) -> list[ExportEntry]:
    entries: list[ExportEntry] = []
    seen: set[str] = set()
    for index, raw_name in enumerate(sorted(str(item) for item in archive_names)):
        name = Path(raw_name.replace("\\", "/")).as_posix()
        pure = Path(name)
        if (
            not pure.parts
            or pure.is_absolute()
            or ".." in pure.parts
            or pure.parts[0] not in {"user_data", "config", "logs"}
            or name in seen
        ):
            raise ValueError("safety backup member is invalid")
        seen.add(name)
        source = _backup_source_path(paths, name)
        if not source.exists() and not source.is_symlink():
            continue
        if not source.is_file() or source.is_symlink():
            raise ValueError("safety backup source is not a regular file")
        if name in DATABASE_MEMBERS:
            snapshot = staging_root / "database-snapshots" / f"{index}.db"
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            _sqlite_snapshot(source, snapshot)
            entries.append(
                ExportEntry(snapshot, name, snapshot.stat().st_size, staging_root)
            )
            continue
        entries.append(
            ExportEntry(
                source,
                name,
                source.stat().st_size,
                _backup_source_root(paths, name),
            )
        )
    return entries


def _prepared_replacement_names(
    *,
    operation: OpsOperation,
    parameters: dict[str, object],
    staging_root: Path,
) -> tuple[str, ...]:
    if operation is OpsOperation.MIGRATION:
        requested = str(parameters["target"])
        targets = (
            ("grading", "question_bank")
            if requested == "all"
            else (requested,)
        )
        by_target = {target: member for member, target in DATABASE_MEMBERS.items()}
        return tuple(by_target[target] for target in targets)
    return tuple(
        path.relative_to(staging_root).as_posix()
        for path in sorted(staging_root.rglob("*"))
        if path.is_file()
    )


def _transfer_export_entries(
    paths: Any,
    staging_root: Path,
    *,
    scope: str,
) -> list[ExportEntry]:
    entries = build_export_manifest(
        default_export_sources(Path(paths.project_root), Path(paths.data_root)),
        scope=scope,
    )
    snapshot_root = staging_root / "database-snapshots"
    snapshots = {
        Path(paths.db_path).resolve(strict=False): snapshot_root
        / "grading_system.db",
        Path(paths.qb_db_path).resolve(strict=False): snapshot_root
        / "question_bank.db",
    }
    prepared: list[ExportEntry] = []
    for entry in entries:
        snapshot = snapshots.get(Path(entry.source_path).resolve(strict=False))
        if snapshot is None:
            prepared.append(entry)
            continue
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        _sqlite_snapshot(Path(entry.source_path), snapshot)
        prepared.append(
            ExportEntry(
                snapshot,
                str(entry.arc_name),
                snapshot.stat().st_size,
                staging_root,
            )
        )
    return prepared


def _backup_source_path(paths: Any, arc_name: str) -> Path:
    from update_tools.backup_core import taxonomy_backup_target
    if target := taxonomy_backup_target(paths, arc_name):
        return target
    parts = Path(arc_name).parts
    if parts[0] == "user_data":
        return Path(paths.data_root).joinpath(*parts[1:])
    if parts[0] == "config":
        return Path(paths.project_root).joinpath(*parts)
    if parts[0] == "logs":
        return Path(paths.logs_dir).joinpath(*parts[1:])
    raise ValueError("backup source is invalid")


def _backup_source_root(paths: Any, arc_name: str) -> Path:
    from update_tools.backup_core import taxonomy_backup_target
    if target := taxonomy_backup_target(paths, arc_name):
        return target.parent
    parts = Path(arc_name).parts
    if parts[0] == "user_data":
        return Path(paths.data_root)
    if parts[0] == "config":
        return Path(paths.project_root) / "config"
    if parts[0] == "logs":
        return Path(paths.logs_dir)
    raise ValueError("backup source is invalid")


def _sqlite_snapshot(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise FileNotFoundError("database source is missing")
    with closing(sqlite3.connect(source)) as source_connection:
        with closing(sqlite3.connect(destination)) as destination_connection:
            source_connection.backup(destination_connection)


def _validate_zip(
    path: Path,
    *,
    paths: Any,
    required_members: Iterable[str] = (),
) -> None:
    inspection = inspect_zip(
        path,
        policy=OpsArchivePolicy(),
        allowed_roots={"user_data", "config", "logs"},
    )
    validate_archive_databases(
        path,
        inspection,
        required_members=required_members,
        migration_dirs=migration_directories(paths),
    )


__all__ = [
    "OpsPreBackupFailed",
    "create_safety_backup",
    "register_ops_job_handlers",
    "run_ops_backup_job",
    "run_ops_migration_prepare_job",
    "run_ops_restore_prepare_job",
    "run_ops_transfer_import_prepare_job",
    "run_ops_transfer_export_job",
]
