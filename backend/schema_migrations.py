from __future__ import annotations

import hashlib
import shutil
import sqlite3
import tempfile
import threading
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.schema_contracts import schema_signature
from update_tools.migrate_db import MigrationFile, run_migrations


@dataclass(frozen=True)
class SchemaGateResult:
    target: str
    current_version: str
    applied: tuple[str, ...]
    pending: tuple[str, ...] = ()


class SchemaVersionError(RuntimeError):
    """The database cannot be safely opened by this application version."""


class SchemaMigrationRequired(SchemaVersionError):
    """An existing database needs an explicitly confirmed maintenance migration."""

    def __init__(self, target: str, pending: tuple[str, ...]) -> None:
        self.target = str(target)
        self.pending = tuple(pending)
        super().__init__(
            "database migration is pending; use protected maintenance "
            f"for target {self.target}"
        )


class _QuietMigrationLogger:
    def info(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def error(self, *_args: Any, **_kwargs: Any) -> None:
        return None


_SCHEMA_SIGNATURE_CACHE: dict[tuple[object, ...], dict[str, object]] = {}
_SCHEMA_SIGNATURE_LOCK = threading.Lock()

# inspect_schema_version is a read-only verification (integrity_check,
# foreign_key_check, schema signature comparison), but its outcome also
# depends on database CONTENT, not only on schema. The memo therefore keys on
# the db file generation (size+mtime_ns of the db file; for the -wal, its
# size+mtime_ns while it holds frames, else a single empty/missing marker) plus
# PRAGMA schema_version, the migration manifest stamp, and the recorded
# migration history: it dedupes repeated checks inside one request or across
# read-only requests, while any write that touches the files (or bumps the
# schema version) forces re-verification.
_SCHEMA_INSPECT_CACHE: dict[tuple[object, ...], SchemaGateResult] = {}
_SCHEMA_INSPECT_LOCK = threading.Lock()
_SCHEMA_INSPECT_CACHE_LIMIT = 32


def _manifest_files(migration_root: Path) -> tuple[Path, ...]:
    files = tuple(sorted(migration_root.glob("*.sql")))
    if not files:
        raise SchemaVersionError("migration manifest is empty")
    return files


def _manifest_cache_key(files: tuple[Path, ...]) -> tuple[object, ...]:
    return (
        tuple(
            (
                str(path.resolve()),
                path.name,
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
            for path in files
        ),
    )


def _expected_schema_signature(
    target: str,
    migration_root: Path,
    *,
    applied_count: int | None = None,
) -> dict[str, object]:
    files = _manifest_files(migration_root)
    selected = files if applied_count is None else files[:applied_count]
    cache_key = (target, _manifest_cache_key(selected))
    with _SCHEMA_SIGNATURE_LOCK:
        cached = _SCHEMA_SIGNATURE_CACHE.get(cache_key)
        if cached is not None:
            return cached
        with tempfile.TemporaryDirectory(
            prefix=f"{target}_schema_contract_"
        ) as raw:
            work_root = Path(raw)
            reference_db = work_root / "reference.db"
            if selected:
                selected_root = work_root / "migrations"
                selected_root.mkdir()
                for source in selected:
                    shutil.copy2(source, selected_root / source.name)
                report = run_migrations(
                    target,
                    db_path=reference_db,
                    migrations_dir=selected_root,
                    backup_dir_override=work_root / "backups",
                    logger_override=_QuietMigrationLogger(),
                )
                if report.error:
                    raise SchemaVersionError(
                        "migration manifest cannot build the recorded schema"
                    )
            else:
                with closing(sqlite3.connect(reference_db)):
                    pass
            signature = schema_signature(reference_db)
        _SCHEMA_SIGNATURE_CACHE[cache_key] = signature
        return signature


def _database_is_blank(database: Path) -> bool:
    if not database.exists():
        return True
    if not database.is_file() or database.is_symlink():
        raise SchemaVersionError("database path is invalid")
    uri = database.resolve().as_uri() + "?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            row = connection.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' LIMIT 1"
            ).fetchone()
    except (sqlite3.DatabaseError, OSError) as exc:
        raise SchemaVersionError("database cannot be inspected safely") from exc
    return row is None


def inspect_schema_version(
    target: str,
    db_path: Path,
    *,
    migrations_dir: Path | None = None,
) -> SchemaGateResult:
    """Validate one existing database without changing it.

    The recorded successful migrations must be an exact checksum-matching prefix
    of the local manifest, and the live schema must exactly match that prefix.
    """

    migration_root = (
        Path(migrations_dir)
        if migrations_dir is not None
        else Path(__file__).resolve().parents[1] / "migrations" / target
    )
    database = Path(db_path)
    if not database.is_file() or database.is_symlink():
        raise SchemaVersionError("database candidate is missing")
    memo_key = _inspect_memo_key(target, database, migration_root)
    if memo_key is not None:
        with _SCHEMA_INSPECT_LOCK:
            cached = _SCHEMA_INSPECT_CACHE.get(memo_key)
        if cached is not None:
            return cached
    files = _manifest_files(migration_root)
    migrations = tuple(MigrationFile.from_path(path) for path in files)
    result = _inspect_schema_version_uncached(
        target,
        database,
        migration_root=migration_root,
        migrations=migrations,
    )
    if memo_key is not None:
        with _SCHEMA_INSPECT_LOCK:
            _SCHEMA_INSPECT_CACHE[memo_key] = result
            while len(_SCHEMA_INSPECT_CACHE) > _SCHEMA_INSPECT_CACHE_LIMIT:
                _SCHEMA_INSPECT_CACHE.pop(next(iter(_SCHEMA_INSPECT_CACHE)))
    return result


def _inspect_memo_key(
    target: str,
    database: Path,
    migration_root: Path,
) -> tuple[object, ...] | None:
    uri = database.resolve().as_uri() + "?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            schema_version = int(
                connection.execute("PRAGMA schema_version").fetchone()[0]
            )
            table = connection.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='schema_migrations'"
            ).fetchone()
            if table is None:
                history: tuple[object, ...] = ()
            else:
                history = tuple(
                    connection.execute(
                        "SELECT migration_name, checksum, success "
                        "FROM schema_migrations ORDER BY migration_name"
                    ).fetchall()
                )
    except (sqlite3.DatabaseError, OSError, TypeError, ValueError):
        return None
    try:
        stat = database.stat()
        db_generation: tuple[object, ...] = (
            database.name, stat.st_size, stat.st_mtime_ns
        )
    except OSError:
        return None
    # A read-only open creates/touches an empty -wal, so its mtime is not a
    # write signal while it holds no frames; missing and size-0 both mean
    # "no pending WAL data" and share one marker. A non-empty WAL's
    # (size, mtime_ns) is stable across read-only opens and is keyed fully.
    wal = Path(f"{database}-wal")
    try:
        wal_stat = wal.stat()
    except FileNotFoundError:
        wal_generation: tuple[object, ...] = (wal.name, "no-wal-data")
    except OSError:
        return None
    else:
        if wal_stat.st_size:
            wal_generation = (wal.name, wal_stat.st_size, wal_stat.st_mtime_ns)
        else:
            wal_generation = (wal.name, "no-wal-data")
    try:
        manifest_stamp = tuple(
            (path.name, path.stat().st_size, path.stat().st_mtime_ns)
            for path in sorted(migration_root.glob("*.sql"))
        )
    except OSError:
        return None
    return (
        target,
        str(database.resolve()),
        str(migration_root.resolve()),
        manifest_stamp,
        schema_version,
        history,
        db_generation,
        wal_generation,
    )


def _inspect_schema_version_uncached(
    target: str,
    database: Path,
    *,
    migration_root: Path,
    migrations: tuple[MigrationFile, ...],
) -> SchemaGateResult:
    uri = database.resolve().as_uri() + "?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            quick = connection.execute("PRAGMA quick_check").fetchall()
            integrity = connection.execute("PRAGMA integrity_check").fetchall()
            if quick != [("ok",)] or integrity != [("ok",)]:
                raise SchemaVersionError("database integrity check failed")
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise SchemaVersionError("database foreign key check failed")
            table = connection.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='schema_migrations'"
            ).fetchone()
            if table is None:
                raise SchemaVersionError("database migration history is missing")
            rows = connection.execute(
                "SELECT migration_name, checksum, success "
                "FROM schema_migrations ORDER BY id"
            ).fetchall()
    except SchemaVersionError:
        raise
    except (sqlite3.DatabaseError, OSError) as exc:
        raise SchemaVersionError("database cannot be inspected safely") from exc

    try:
        has_failed_record = any(int(row[2]) != 1 for row in rows)
    except (TypeError, ValueError) as exc:
        raise SchemaVersionError(
            "database migration history contains an invalid status"
        ) from exc
    if has_failed_record:
        raise SchemaVersionError("database migration history contains a failure")
    applied = tuple(str(row[0]) for row in rows)
    expected_names = tuple(migration.name for migration in migrations[: len(rows)])
    if len(rows) > len(migrations) or applied != expected_names:
        known_names = {migration.name for migration in migrations}
        if any(name not in known_names for name in applied):
            raise SchemaVersionError(
                "database schema is newer than this application"
            )
        raise SchemaVersionError("recorded migration history has a gap")
    for row, migration in zip(rows, migrations):
        checksum = str(row[1] or "").strip()
        if not checksum or checksum != migration.checksum:
            raise SchemaVersionError(
                f"recorded migration checksum does not match: {migration.name}"
            )
    try:
        actual_signature = schema_signature(database)
    except (sqlite3.DatabaseError, OSError) as exc:
        raise SchemaVersionError("database schema cannot be inspected") from exc
    expected_signature = _expected_schema_signature(
        target,
        migration_root,
        applied_count=len(applied),
    )
    if actual_signature != expected_signature:
        raise SchemaVersionError(
            "database schema differs from the recorded migration history"
        )
    pending = tuple(migration.name for migration in migrations[len(applied) :])
    return SchemaGateResult(
        target=target,
        current_version=applied[-1] if applied else "0",
        applied=applied,
        pending=pending,
    )


def ensure_schema_current(
    target: str,
    db_path: Path,
    *,
    migrations_dir: Path | None = None,
    backup_dir: Path | None = None,
    logger_override: Any | None = None,
    allow_existing_migrations: bool = True,
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
    blank = _database_is_blank(database)
    if not blank:
        inspected = inspect_schema_version(
            target,
            database,
            migrations_dir=migration_root,
        )
        if inspected.pending and not allow_existing_migrations:
            raise SchemaMigrationRequired(target, inspected.pending)
        if not inspected.pending:
            return inspected

    report = run_migrations(
        target,
        db_path=database,
        migrations_dir=migration_root,
        backup_dir_override=effective_backup_dir,
        logger_override=logger_override,
    )
    if report.error:
        raise SchemaVersionError(report.error)
    inspected = inspect_schema_version(
        target,
        database,
        migrations_dir=migration_root,
    )
    if inspected.pending:
        raise SchemaVersionError("database migration did not reach the requested version")
    return inspected


def ensure_application_schema(paths: Any) -> dict[str, SchemaGateResult]:
    project_root = Path(
        getattr(paths, "project_root", Path(__file__).resolve().parents[1])
    )
    migration_project_root = Path(
        getattr(paths, "migration_project_root", project_root)
    )
    backup_dir = Path(
        getattr(paths, "backups_dir", Path(paths.db_path).parent / "backups")
    )
    return {
        "grading": ensure_schema_current(
            "grading",
            Path(paths.db_path),
            migrations_dir=migration_project_root / "migrations" / "grading",
            backup_dir=backup_dir,
            allow_existing_migrations=False,
        ),
        "question_bank": ensure_schema_current(
            "question_bank",
            Path(paths.qb_db_path),
            migrations_dir=(
                migration_project_root / "migrations" / "question_bank"
            ),
            backup_dir=backup_dir,
            allow_existing_migrations=False,
        ),
    }


__all__ = [
    "SchemaGateResult",
    "SchemaMigrationRequired",
    "SchemaVersionError",
    "ensure_application_schema",
    "ensure_schema_current",
    "inspect_schema_version",
]
