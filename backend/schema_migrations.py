from __future__ import annotations

import hashlib
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.schema_contracts import schema_signature
from update_tools.migrate_db import get_migration_status, run_migrations


@dataclass(frozen=True)
class SchemaGateResult:
    target: str
    current_version: str
    applied: tuple[str, ...]


class SchemaVersionError(RuntimeError):
    """The database cannot be safely opened by this application version."""


class _QuietMigrationLogger:
    def info(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def error(self, *_args: Any, **_kwargs: Any) -> None:
        return None


_SCHEMA_SIGNATURE_CACHE: dict[tuple[object, ...], dict[str, object]] = {}
_SCHEMA_SIGNATURE_LOCK = threading.Lock()


def _manifest_cache_key(migration_root: Path) -> tuple[object, ...]:
    files = sorted(migration_root.glob("*.sql"))
    if not files:
        raise SchemaVersionError("migration manifest is empty")
    return (
        str(migration_root.resolve()),
        tuple(
            (
                path.name,
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
            for path in files
        ),
    )


def _expected_schema_signature(
    target: str,
    migration_root: Path,
) -> dict[str, object]:
    cache_key = _manifest_cache_key(migration_root)
    with _SCHEMA_SIGNATURE_LOCK:
        cached = _SCHEMA_SIGNATURE_CACHE.get(cache_key)
        if cached is not None:
            return cached
        with tempfile.TemporaryDirectory(
            prefix=f"{target}_schema_contract_"
        ) as raw:
            work_root = Path(raw)
            reference_db = work_root / "reference.db"
            report = run_migrations(
                target,
                db_path=reference_db,
                migrations_dir=migration_root,
                backup_dir_override=work_root / "backups",
                logger_override=_QuietMigrationLogger(),
            )
            if report.error:
                raise SchemaVersionError(
                    "migration manifest cannot build the current schema"
                )
            signature = schema_signature(reference_db)
        _SCHEMA_SIGNATURE_CACHE[cache_key] = signature
        return signature


def ensure_schema_current(
    target: str,
    db_path: Path,
    *,
    migrations_dir: Path | None = None,
    backup_dir: Path | None = None,
) -> SchemaGateResult:
    migration_root = (
        Path(migrations_dir)
        if migrations_dir is not None
        else Path(__file__).resolve().parents[1] / "migrations" / target
    )
    database = Path(db_path)
    effective_backup_dir = (
        Path(backup_dir)
        if backup_dir is not None
        else (
            database.parent.parent / "backups"
            if database.parent.name.casefold() == "databases"
            else database.parent / "backups"
        )
    )
    report = run_migrations(
        target,
        db_path=database,
        migrations_dir=migration_root,
        backup_dir_override=effective_backup_dir,
    )
    if report.error:
        raise SchemaVersionError(report.error)
    if schema_signature(database) != _expected_schema_signature(
        target,
        migration_root,
    ):
        raise SchemaVersionError(
            "database schema differs from the current migration manifest"
        )
    status = get_migration_status(
        target,
        db_path_override=database,
        migrations_dir_override=migration_root,
    )
    applied = tuple(str(item) for item in status.get("applied") or ())
    return SchemaGateResult(
        target=target,
        current_version=applied[-1] if applied else "0",
        applied=applied,
    )


def ensure_application_schema(paths: Any) -> dict[str, SchemaGateResult]:
    project_root = Path(
        getattr(paths, "project_root", Path(__file__).resolve().parents[1])
    )
    backup_dir = Path(
        getattr(paths, "backups_dir", Path(paths.db_path).parent / "backups")
    )
    return {
        "grading": ensure_schema_current(
            "grading",
            Path(paths.db_path),
            migrations_dir=project_root / "migrations" / "grading",
            backup_dir=backup_dir,
        ),
        "question_bank": ensure_schema_current(
            "question_bank",
            Path(paths.qb_db_path),
            migrations_dir=project_root / "migrations" / "question_bank",
            backup_dir=backup_dir,
        ),
    }


__all__ = [
    "SchemaGateResult",
    "SchemaVersionError",
    "ensure_application_schema",
    "ensure_schema_current",
]
