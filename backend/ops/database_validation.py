from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from pathlib import Path

from .archive import OpsArchiveInspection, OpsArchiveInvalid


DATABASE_MEMBERS = {
    "user_data/databases/grading_system.db": "grading",
    "user_data/databases/question_bank.db": "question_bank",
    "user_data/workspaces/class-teacher/student_affairs.db": "student_affairs",
    "user_data/workspaces/class-teacher/class_teacher_work.db": "class_teacher_work",
    "user_data/workspaces/teaching-prep/teaching_prep.db": "teaching_prep",
}

REQUIRED_SAFETY_DATABASE_MEMBERS = frozenset(
    {
        "user_data/databases/grading_system.db",
        "user_data/databases/question_bank.db",
    }
)

REQUIRED_TABLES = {
    "grading": frozenset({"students", "grading_sessions", "exam_papers"}),
    "question_bank": frozenset({"papers", "questions", "question_tags"}),
    "student_affairs": frozenset(
        {"schema_migrations", "vault_metadata", "encrypted_objects", "access_audit"}
    ),
    "class_teacher_work": frozenset(
        {"schema_migrations", "work_nodes", "work_edges", "work_operations"}
    ),
    "teaching_prep": frozenset(
        {"schema_migrations", "teaching_prep_operations", "lesson_preparations"}
    ),
}


class OpsDatabaseInvalid(ValueError):
    pass


def validate_database_file(path: Path, target: str) -> None:
    required = REQUIRED_TABLES.get(str(target))
    if required is None:
        raise OpsDatabaseInvalid("unknown database target")
    candidate = Path(path)
    if not candidate.is_file() or candidate.is_symlink():
        raise OpsDatabaseInvalid("database candidate is missing")
    uri = candidate.resolve().as_uri() + "?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            quick = connection.execute("PRAGMA quick_check").fetchone()
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            if str(quick[0] if quick else "").casefold() != "ok":
                raise OpsDatabaseInvalid("database quick check failed")
            if str(integrity[0] if integrity else "").casefold() != "ok":
                raise OpsDatabaseInvalid("database integrity check failed")
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
    except (sqlite3.DatabaseError, OSError) as exc:
        raise OpsDatabaseInvalid("database candidate is invalid") from exc
    if not required <= tables:
        raise OpsDatabaseInvalid("database candidate schema is incompatible")


def validate_archive_databases(
    archive_path: Path,
    inspection: OpsArchiveInspection,
    *,
    require_all: bool = False,
) -> None:
    found: set[str] = set()
    selected: list[tuple[str, str]] = []
    for member in inspection.members:
        if member.is_dir:
            continue
        normalized = "/".join(member.parts)
        if not _looks_like_database_candidate(normalized):
            continue
        target = DATABASE_MEMBERS.get(normalized)
        if target is None:
            raise OpsArchiveInvalid("unexpected_database_candidate")
        found.add(normalized)
        selected.append((member.archive_name, target))
    if require_all and not REQUIRED_SAFETY_DATABASE_MEMBERS <= found:
        raise OpsArchiveInvalid("safety_backup_databases_missing")
    if not selected:
        return
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            with tempfile.TemporaryDirectory(prefix="ops-db-validation-") as temp_value:
                temp_root = Path(temp_value)
                for index, (archive_name, target) in enumerate(selected):
                    candidate = temp_root / f"{index}.db"
                    with archive.open(archive_name, "r") as source, candidate.open("xb") as output:
                        shutil.copyfileobj(source, output, length=1024 * 1024)
                        output.flush()
                        os.fsync(output.fileno())
                    validate_database_file(candidate, target)
    except OpsDatabaseInvalid as exc:
        raise OpsArchiveInvalid("invalid_database_candidate") from exc
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise OpsArchiveInvalid("invalid_database_candidate") from exc


def validate_staged_databases(staging_root: Path) -> None:
    root = Path(staging_root)
    for candidate in root.rglob("*"):
        if not candidate.is_file():
            continue
        normalized = candidate.relative_to(root).as_posix()
        if not _looks_like_database_candidate(normalized):
            continue
        target = DATABASE_MEMBERS.get(normalized)
        if target is None:
            raise OpsArchiveInvalid("unexpected_database_candidate")
        try:
            validate_database_file(candidate, target)
        except OpsDatabaseInvalid as exc:
            raise OpsArchiveInvalid("invalid_database_candidate") from exc


def _looks_like_database_candidate(normalized: str) -> bool:
    clean = str(normalized).replace("\\", "/")
    if clean.startswith("user_data/databases/"):
        return True
    workspace_prefixes = (
        "user_data/workspaces/class-teacher/",
        "user_data/workspaces/teaching-prep/",
    )
    if not clean.startswith(workspace_prefixes):
        return False
    name = clean.rsplit("/", 1)[-1].casefold()
    return (
        name.endswith((".db", ".sqlite", ".sqlite3"))
        or ".db-" in name
        or ".sqlite-" in name
        or ".sqlite3-" in name
    )


def validate_live_databases(
    paths: object,
    *,
    require_no_companions: bool = False,
) -> None:
    targets = (
        (Path(paths.db_path), "grading"),
        (Path(paths.qb_db_path), "question_bank"),
    )
    if require_no_companions:
        _reject_database_companions(targets)
    for path, target in targets:
        validate_database_file(path, target)
    if require_no_companions:
        _reject_database_companions(targets)


def _reject_database_companions(targets: tuple[tuple[Path, str], ...]) -> None:
    for path, _target in targets:
        for suffix in ("-wal", "-shm", "-journal"):
            if Path(f"{path}{suffix}").exists():
                raise OpsDatabaseInvalid("database companion file is present")


__all__ = [
    "DATABASE_MEMBERS",
    "OpsDatabaseInvalid",
    "validate_archive_databases",
    "validate_database_file",
    "validate_live_databases",
    "validate_staged_databases",
]
