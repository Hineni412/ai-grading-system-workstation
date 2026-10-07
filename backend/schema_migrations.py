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

from backend.schema_contracts import (
    CurrentSchemaContract,
    current_schema_contract_path,
    load_current_schema_contract,
    schema_signature_document,
)
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

# inspect_schema_version runs the expensive PRAGMA integrity_check and
# foreign_key_check only on the first successful inspection per process per
# database file identity (resolved path + st_dev + st_ino); a restored or
# replaced file gets a new identity and is fully checked again. Later
# inspections still verify the schema signature and migration history, and
# that verification is itself memoized by (path, file identity,
# PRAGMA schema_version, manifest stamp, history) so ordinary data writes do
# not repeat it. Operational backup/restore paths keep their own full checks.
_SCHEMA_INSPECT_CACHE: dict[tuple[object, ...], SchemaGateResult] = {}
_SCHEMA_INSPECT_LOCK = threading.Lock()
_SCHEMA_INSPECT_CACHE_LIMIT = 32
_FULL_CHECK_DONE: set[tuple[str, int, int]] = set()
_DEFAULT_MIGRATIONS_ROOT = Path(__file__).resolve().parents[1] / "migrations"


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
            signature = schema_signature_document(reference_db)
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


def _current_contract(target: str) -> CurrentSchemaContract | None:
    try:
        return load_current_schema_contract(target)
    except (OSError, ValueError, TypeError) as exc:
        raise SchemaVersionError("current schema contract cannot be read safely") from exc


def _recorded_checksums(rows: tuple[object, ...] | list[Any]) -> tuple[tuple[str, str], ...]:
    return tuple((str(row[0]), str(row[1] or "").strip()) for row in rows)


def _create_current_database(
    target: str,
    database: Path,
    contract: CurrentSchemaContract,
) -> SchemaGateResult:
    database.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            if connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' LIMIT 1"
            ).fetchone():
                raise SchemaVersionError("current schema initialization requires a blank database")
            for statement in contract.creation_statements:
                connection.execute(statement)
            if schema_signature_document(database, connection=connection) != contract.signature:
                raise SchemaVersionError("current schema creation does not match its contract")
            connection.executemany(
                "INSERT INTO schema_migrations (migration_name, checksum, success) VALUES (?, ?, 1)",
                contract.migration_checksums,
            )
            if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise SchemaVersionError("database integrity check failed")
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise SchemaVersionError("database foreign key check failed")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    return inspect_schema_version(target, database)


def inspect_schema_version(
    target: str,
    db_path: Path,
    *,
    migrations_dir: Path | None = None,
) -> SchemaGateResult:
    """Validate one existing database without changing it.

    Complete databases match the current contract. Historical databases must
    match an exact checksum-verified prefix of the conversion SQL manifest.
    """

    migration_root = (
        Path(migrations_dir)
        if migrations_dir is not None
        else _DEFAULT_MIGRATIONS_ROOT / target
    )
    database = Path(db_path)
    if not database.is_file() or database.is_symlink():
        raise SchemaVersionError("database candidate is missing")
    contract = _current_contract(target)
    memo_key = _inspect_memo_key(target, database, migration_root, contract=contract)
    if memo_key is not None:
        with _SCHEMA_INSPECT_LOCK:
            cached = _SCHEMA_INSPECT_CACHE.get(memo_key)
        if cached is not None:
            return cached
    identity = _database_file_identity(database)
    full_check = True
    if identity is not None:
        with _SCHEMA_INSPECT_LOCK:
            full_check = identity not in _FULL_CHECK_DONE
    result = _inspect_schema_version_uncached(
        target,
        database,
        migration_root=migration_root,
        contract=contract,
        full_check=full_check,
    )
    with _SCHEMA_INSPECT_LOCK:
        if identity is not None:
            _FULL_CHECK_DONE.add(identity)
        if memo_key is not None:
            _SCHEMA_INSPECT_CACHE[memo_key] = result
            while len(_SCHEMA_INSPECT_CACHE) > _SCHEMA_INSPECT_CACHE_LIMIT:
                _SCHEMA_INSPECT_CACHE.pop(next(iter(_SCHEMA_INSPECT_CACHE)))
    return result


def _database_file_identity(database: Path) -> tuple[str, int, int] | None:
    try:
        stat = database.stat()
    except OSError:
        return None
    return (str(database.resolve()), int(stat.st_dev), int(stat.st_ino))


def _inspect_memo_key(
    target: str,
    database: Path,
    migration_root: Path,
    *,
    contract: CurrentSchemaContract | None,
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
                        "FROM schema_migrations ORDER BY id"
                    ).fetchall()
                )
    except (sqlite3.DatabaseError, OSError, TypeError, ValueError):
        return None
    identity = _database_file_identity(database)
    if identity is None:
        return None
    try:
        current = contract is not None and _recorded_checksums(history) == contract.migration_checksums
        manifest_stamp = None if current else (
            str(migration_root.resolve()),
            tuple(
                (path.name, path.stat().st_size, path.stat().st_mtime_ns)
                for path in sorted(migration_root.glob("*.sql"))
            ),
        )
        contract_stamp = None
        if target in {"grading", "question_bank"}:
            contract_path = current_schema_contract_path(target)
            contract_stamp = (
                contract_path.stat().st_size,
                contract_path.stat().st_mtime_ns,
            )
    except OSError:
        return None
    return (
        target,
        str(database.resolve()),
        manifest_stamp,
        contract_stamp,
        schema_version,
        history,
        identity,
    )


def _inspect_schema_version_uncached(
    target: str,
    database: Path,
    *,
    migration_root: Path,
    contract: CurrentSchemaContract | None,
    full_check: bool,
) -> SchemaGateResult:
    uri = database.resolve().as_uri() + "?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            if full_check:
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
    if contract is not None and applied == tuple(item[0] for item in contract.migration_checksums):
        for row, (name, checksum) in zip(rows, contract.migration_checksums):
            if str(row[1] or "").strip() != checksum:
                raise SchemaVersionError(f"recorded migration checksum does not match: {name}")
        try:
            actual = schema_signature_document(database)
        except (sqlite3.DatabaseError, OSError) as exc:
            raise SchemaVersionError("database schema cannot be inspected") from exc
        if actual != contract.signature:
            raise SchemaVersionError("database schema differs from the current schema contract")
        return SchemaGateResult(target, applied[-1], applied)
    migrations = tuple(MigrationFile.from_path(path) for path in _manifest_files(migration_root))
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
        actual_signature = schema_signature_document(database)
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
        else _DEFAULT_MIGRATIONS_ROOT / target
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
    if blank and migration_root.resolve() == (_DEFAULT_MIGRATIONS_ROOT / target).resolve():
        contract = _current_contract(target)
        if contract is not None:
            return _create_current_database(target, database, contract)
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
