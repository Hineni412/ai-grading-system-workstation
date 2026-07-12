from __future__ import annotations

import os
import sqlite3
import tempfile
import zipfile
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
from .plan_store import OpsPlanStore
from .write_service import OpsWriteService


def register_ops_job_handlers(manager: JobManager, *, paths: Any) -> None:
    manager.register(
        "ops_backup",
        lambda context: run_ops_backup_job(context=context, paths=paths),
    )
    manager.register(
        "ops_transfer_export",
        lambda context: run_ops_transfer_export_job(context=context, paths=paths),
    )


def run_ops_backup_job(*, context: JobContext, paths: Any) -> dict[str, object]:
    parameters = _validate_online_payload(context, OpsOperation.BACKUP, paths)
    reason = str(parameters.get("reason") or "")
    if not reason:
        raise ValueError("backup reason is required")
    with keyed_execution_locks(["ops-write"], cancel_check=context.raise_if_cancelled):
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
            entries = _backup_entries(paths, staging_root)
            write_export_zip(entries, archive_path)
            _validate_zip(archive_path)
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
    with keyed_execution_locks(["ops-write"], cancel_check=context.raise_if_cancelled):
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
            archive_path = Path(staging_value) / "export.zip"
            entries = build_export_manifest(
                default_export_sources(Path(paths.project_root), Path(paths.data_root)),
                scope=scope,
            )
            write_export_zip(entries, archive_path)
            _validate_zip(archive_path)
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


def _backup_entries(paths: Any, staging_root: Path) -> list[ExportEntry]:
    preview = preview_backup(path_manager=paths)
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
            entries.append(ExportEntry(snapshot, name, snapshot.stat().st_size))
            continue
        source = _backup_source_path(paths, name)
        try:
            size_bytes = source.stat().st_size
        except OSError:
            continue
        entries.append(ExportEntry(source, name, size_bytes))
    return entries


def _backup_source_path(paths: Any, arc_name: str) -> Path:
    parts = Path(arc_name).parts
    if parts[0] == "user_data":
        return Path(paths.data_root).joinpath(*parts[1:])
    if parts[0] == "config":
        return Path(paths.project_root).joinpath(*parts)
    if parts[0] == "logs":
        return Path(paths.logs_dir).joinpath(*parts[1:])
    raise ValueError("backup source is invalid")


def _sqlite_snapshot(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise FileNotFoundError("database source is missing")
    with closing(sqlite3.connect(source)) as source_connection:
        with closing(sqlite3.connect(destination)) as destination_connection:
            source_connection.backup(destination_connection)


def _validate_zip(path: Path) -> None:
    with zipfile.ZipFile(path, "r") as archive:
        if archive.testzip() is not None:
            raise ValueError("published archive failed CRC validation")


__all__ = [
    "register_ops_job_handlers",
    "run_ops_backup_job",
    "run_ops_transfer_export_job",
]
