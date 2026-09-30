from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from question_bank.database.schema import connect
from question_bank.services.asset_path_service import resolve_question_bank_asset_path
from question_bank.services.source_paper_archive_service import archive_source_paper

SOURCE_COLUMNS = (
    ("papers", "source_file"),
    ("questions", "source_file"),
    ("question_previews", "source_file"),
)


@dataclass(frozen=True, slots=True)
class MigrationReport:
    scanned_sources: int
    migrated_sources: int
    reused_archives: int
    missing_sources: int
    failed_sources: int
    updated_rows: int
    backup_path: Path | None
    messages: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _InvariantSnapshot:
    question_ids: tuple[int, ...]
    question_digest: str
    tag_count: int
    tag_digest: str


def plan_source_paper_migration(
    *,
    db_path: str | Path,
    data_root: str | Path,
) -> MigrationReport:
    database_path = Path(db_path).resolve()
    root = Path(data_root).resolve()
    source_values = _source_values(database_path)
    missing = 0
    messages: list[str] = []
    for stored_value in source_values:
        if _is_portable_stored_path(stored_value):
            messages.append(f"already portable: {stored_value}")
            continue
        resolved = resolve_question_bank_asset_path(
            stored_value,
            data_root=root,
            search_subdirs=("question_bank/raw_papers",),
        )
        if not resolved.is_file():
            missing += 1
            messages.append(f"missing: {stored_value}")
        else:
            messages.append(f"would archive: {stored_value}")
    return MigrationReport(
        scanned_sources=len(source_values),
        migrated_sources=0,
        reused_archives=0,
        missing_sources=missing,
        failed_sources=0,
        updated_rows=0,
        backup_path=None,
        messages=tuple(messages),
    )


def apply_source_paper_migration(
    *,
    db_path: str | Path,
    data_root: str | Path,
) -> MigrationReport:
    database_path = Path(db_path).resolve()
    root = Path(data_root).resolve()
    source_values = _source_values(database_path)
    mapping: dict[str, str] = {}
    missing = 0
    failed = 0
    reused = 0
    messages: list[str] = []

    for stored_value in source_values:
        if _is_portable_stored_path(stored_value):
            messages.append(f"already portable: {stored_value}")
            continue
        resolved = resolve_question_bank_asset_path(
            stored_value,
            data_root=root,
            search_subdirs=("question_bank/raw_papers",),
        )
        if not resolved.is_file():
            missing += 1
            messages.append(f"missing: {stored_value}")
            continue
        try:
            archived = archive_source_paper(resolved, data_root=root)
        except (OSError, ValueError) as exc:
            failed += 1
            messages.append(f"failed: {stored_value}: {exc}")
            continue
        mapping[stored_value] = archived.stored_path
        reused += int(archived.reused)
        messages.append(f"archived: {stored_value} -> {archived.stored_path}")

    if not mapping:
        return MigrationReport(
            scanned_sources=len(source_values),
            migrated_sources=0,
            reused_archives=reused,
            missing_sources=missing,
            failed_sources=failed,
            updated_rows=0,
            backup_path=None,
            messages=tuple(messages),
        )

    backup_path = _backup_database(database_path, root / "backups")
    updated_rows = 0
    with connect(database_path) as conn:
        before = _snapshot_invariants(conn)
        conn.execute("BEGIN IMMEDIATE")
        for old_value, new_value in mapping.items():
            for table, column in SOURCE_COLUMNS:
                cursor = conn.execute(
                    f'UPDATE "{table}" SET "{column}" = ? WHERE "{column}" = ?',
                    (new_value, old_value),
                )
                updated_rows += max(0, int(cursor.rowcount))
        after = _snapshot_invariants(conn)
        if not _invariants_match(before, after):
            raise RuntimeError("migration invariant check failed")

    for stored_path in mapping.values():
        resolved = resolve_question_bank_asset_path(
            stored_path,
            data_root=root,
            search_subdirs=("question_bank/raw_papers",),
        )
        if not resolved.is_file():
            raise RuntimeError(f"migrated source does not resolve: {stored_path}")

    return MigrationReport(
        scanned_sources=len(source_values),
        migrated_sources=len(mapping),
        reused_archives=reused,
        missing_sources=missing,
        failed_sources=failed,
        updated_rows=updated_rows,
        backup_path=backup_path,
        messages=tuple(messages),
    )


def _source_values(db_path: Path) -> list[str]:
    values: set[str] = set()
    with connect(db_path) as conn:
        for table, column in SOURCE_COLUMNS:
            rows = conn.execute(
                f'SELECT DISTINCT "{column}" FROM "{table}" '
                f'WHERE "{column}" IS NOT NULL AND TRIM("{column}") <> \'\''
            ).fetchall()
            values.update(str(row[0]).strip() for row in rows if str(row[0]).strip())
    return sorted(values)


def _is_portable_stored_path(value: str) -> bool:
    normalized = value.replace("\\", "/").lstrip("./")
    return normalized.startswith("question_bank/raw_papers/")


def _backup_database(db_path: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_path = backup_dir / f"question_bank_before_source_archive_{timestamp}.db"
    source = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    destination = sqlite3.connect(backup_path)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    if not backup_path.is_file() or backup_path.stat().st_size <= 0:
        raise OSError("question-bank backup was not created")
    return backup_path


def _snapshot_invariants(conn: sqlite3.Connection) -> _InvariantSnapshot:
    question_ids = tuple(
        int(row[0]) for row in conn.execute("SELECT id FROM questions ORDER BY id")
    )
    question_columns = [
        str(row[1])
        for row in conn.execute("PRAGMA table_info('questions')")
        if str(row[1]) != "source_file"
    ]
    quoted_columns = ", ".join(f'"{column}"' for column in question_columns)
    question_rows = [
        tuple(row)
        for row in conn.execute(
            f"SELECT {quoted_columns} FROM questions ORDER BY id"
        )
    ]
    question_encoded = json.dumps(
        question_rows,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    tag_rows = [
        tuple(row)
        for row in conn.execute(
            "SELECT * FROM question_tags ORDER BY id"
        )
    ]
    encoded = json.dumps(tag_rows, ensure_ascii=False, separators=(",", ":"), default=str)
    return _InvariantSnapshot(
        question_ids=question_ids,
        question_digest=hashlib.sha256(question_encoded.encode("utf-8")).hexdigest(),
        tag_count=len(tag_rows),
        tag_digest=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
    )


def _invariants_match(
    before: _InvariantSnapshot,
    after: _InvariantSnapshot,
) -> bool:
    return before == after


__all__ = [
    "MigrationReport",
    "apply_source_paper_migration",
    "plan_source_paper_migration",
]
