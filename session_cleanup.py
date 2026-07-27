from __future__ import annotations

import shutil
import threading
import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from backend.repositories.access import GradingRepositoryAccess, as_grading_repositories
from question_bank.database.schema import connect as connect_question_bank


SESSION_STORAGE_DIR_NAMES = ("templates", "exams", "annotated")
_SESSION_LIFECYCLE_LOCKS_GUARD = threading.Lock()
_SESSION_LIFECYCLE_LOCKS: dict[int, threading.RLock] = {}


class SessionStorageDeletionIncomplete(RuntimeError):
    """Some owned session storage could not be removed; the DB is retained."""

    def __init__(self, failed_paths: list[str]) -> None:
        super().__init__("Some archived exam files could not be permanently deleted.")
        self.failed_paths = tuple(failed_paths)


class SessionDerivedTrainingDataExists(RuntimeError):
    """A frozen training snapshot still contains this exam's diagnosis data."""

    def __init__(self, task_codes: list[str]) -> None:
        super().__init__("Derived training tasks still reference this exam.")
        self.task_codes = tuple(task_codes)


@contextmanager
def session_lifecycle_guard(session_id: int) -> Iterator[None]:
    """Serialize archive, restore and permanent-delete actions for one exam."""

    clean_session_id = int(session_id)
    with _SESSION_LIFECYCLE_LOCKS_GUARD:
        lock = _SESSION_LIFECYCLE_LOCKS.setdefault(
            clean_session_id,
            threading.RLock(),
        )
    with lock:
        yield


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _resolve_stored_candidate(path_value: str | None, data_root: Path) -> Path | None:
    if not path_value:
        return None

    raw = str(path_value).strip()
    if not raw:
        return None

    project_root = data_root.parent
    path = Path(raw)
    candidates: list[Path] = []

    if path.is_absolute():
        candidates.append(path)
        parts = path.parts
        lowered = [part.lower() for part in parts]
        if "user_data" in lowered:
            index = lowered.index("user_data")
            if index + 1 < len(parts):
                candidates.append(data_root.joinpath(*parts[index + 1 :]))
    else:
        candidates.append(project_root / path)
        candidates.append(data_root / path)

    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved.exists() and _is_under(resolved, data_root):
            return resolved
    return None


def _collect_session_dirs(session_id: int, data_root: Path) -> set[Path]:
    dirs: set[Path] = set()
    for dirname in SESSION_STORAGE_DIR_NAMES:
        path = data_root / dirname / f"session_{int(session_id)}"
        if path.exists() and path.is_dir() and _is_under(path, data_root):
            dirs.add(path.resolve())
    config_source_dir = (
        data_root
        / "config"
        / "uploaded"
        / "config_sources"
        / f"session-{int(session_id)}"
    )
    if (
        config_source_dir.exists()
        and config_source_dir.is_dir()
        and _is_under(config_source_dir, data_root)
    ):
        dirs.add(config_source_dir.resolve())
    return dirs


def _collect_session_file_paths(
    db: GradingRepositoryAccess,
    session_id: int,
    data_root: Path,
) -> set[Path]:
    files: set[Path] = set()
    for raw_path in db.collect_session_storage_paths(session_id):
        resolved = _resolve_stored_candidate(raw_path, data_root)
        if resolved is not None and resolved.is_file():
            files.add(resolved)
    return files


def _collect_other_session_references(
    db: GradingRepositoryAccess,
    session_id: int,
    data_root: Path,
) -> set[Path]:
    refs: set[Path] = set()
    for session in db.list_grading_sessions(include_deleted=True):
        other_id = int(session["id"])
        if other_id == int(session_id):
            continue
        refs.update(_collect_session_file_paths(db, other_id, data_root))
    return refs


def _contains_shared_reference(directory: Path, shared_refs: set[Path]) -> bool:
    for ref in shared_refs:
        try:
            ref.relative_to(directory)
            return True
        except ValueError:
            continue
    return False


def _is_inside_any(path: Path, directories: set[Path]) -> bool:
    for directory in directories:
        try:
            path.relative_to(directory)
            return True
        except ValueError:
            continue
    return False


def _count_files(directory: Path) -> int:
    return sum(1 for item in directory.rglob("*") if item.is_file())


def _delete_collected_storage(
    files: set[Path],
    directories: set[Path],
    shared_refs: set[Path],
) -> dict[str, Any]:
    protected_dirs = {directory for directory in directories if _contains_shared_reference(directory, shared_refs)}
    directories_to_delete = directories - protected_dirs
    files_to_delete = {
        path
        for path in files
        if path not in shared_refs and not _is_inside_any(path, directories_to_delete)
    }

    deleted_files = 0
    deleted_dirs = 0
    skipped_shared = len(protected_dirs) + sum(1 for path in files if path in shared_refs)
    failed_paths: list[str] = []

    for path in sorted(files_to_delete, key=lambda item: len(item.parts), reverse=True):
        try:
            path.unlink(missing_ok=True)
            deleted_files += 1
        except OSError:
            failed_paths.append(str(path))

    for directory in sorted(directories_to_delete, key=lambda item: len(item.parts), reverse=True):
        try:
            deleted_files += _count_files(directory)
            shutil.rmtree(directory)
            deleted_dirs += 1
        except OSError:
            failed_paths.append(str(directory))

    return {
        "deleted_files": deleted_files,
        "deleted_dirs": deleted_dirs,
        "skipped_shared": skipped_shared,
        "failed_paths": failed_paths,
    }


def preview_session_permanent_deletion(
    db: GradingRepositoryAccess,
    session_id: int,
    *,
    data_root: Path,
) -> dict[str, int]:
    """Count owned storage without changing the archived exam."""

    db = as_grading_repositories(db)
    data_root = Path(data_root).resolve()
    files = _collect_session_file_paths(db, int(session_id), data_root)
    directories = _collect_session_dirs(int(session_id), data_root)
    shared_refs = _collect_other_session_references(db, int(session_id), data_root)
    protected_dirs = {
        directory
        for directory in directories
        if _contains_shared_reference(directory, shared_refs)
    }


def _json_references_session(value: Any, session_id: int) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized_key = str(key).strip().casefold()
            if normalized_key == "session_id":
                try:
                    if int(child) == int(session_id):
                        return True
                except (TypeError, ValueError):
                    pass
            if normalized_key == "session_ids" and isinstance(child, list):
                for item in child:
                    try:
                        if int(item) == int(session_id):
                            return True
                    except (TypeError, ValueError):
                        continue
            if _json_references_session(child, session_id):
                return True
    elif isinstance(value, list):
        return any(_json_references_session(item, session_id) for item in value)
    return False


def _decoded_json(value: Any) -> Any:
    try:
        return json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def question_bank_session_reference_impact(
    question_bank_db_path: Path,
    session_id: int,
) -> dict[str, Any]:
    """Inspect derived references while preserving bank questions and tags."""

    session_key = str(int(session_id))
    with connect_question_bank(Path(question_bank_db_path)) as connection:
        link_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM grading_question_links "
                "WHERE grading_session_id = ?",
                (session_key,),
            ).fetchone()[0]
        )
        skill_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM assessment_item_skills "
                "WHERE grading_session_id = ?",
                (session_key,),
            ).fetchone()[0]
        )
        attempt_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM training_attempts "
                "WHERE grading_session_id = ?",
                (session_key,),
            ).fetchone()[0]
        )
        task_rows = connection.execute(
            """
            SELECT task_code, exam_scope_json, diagnosis_snapshot_json
            FROM training_tasks
            ORDER BY id
            """
        ).fetchall()

    blocking_task_codes = [
        str(row["task_code"])
        for row in task_rows
        if _json_references_session(
            _decoded_json(row["exam_scope_json"]),
            int(session_id),
        )
        or _json_references_session(
            _decoded_json(row["diagnosis_snapshot_json"]),
            int(session_id),
        )
    ]
    return {
        "question_links": max(0, link_count),
        "assessment_skill_links": max(0, skill_count),
        "training_attempts": max(0, attempt_count),
        "blocking_training_tasks": blocking_task_codes,
    }


def _delete_question_bank_session_references(
    question_bank_db_path: Path,
    session_id: int,
) -> dict[str, int]:
    session_key = str(int(session_id))
    impact = question_bank_session_reference_impact(
        question_bank_db_path,
        int(session_id),
    )
    blocking_tasks = list(impact["blocking_training_tasks"])
    if blocking_tasks:
        raise SessionDerivedTrainingDataExists(blocking_tasks)

    counts: dict[str, int] = {}
    with connect_question_bank(Path(question_bank_db_path)) as connection:
        for key, table in (
            ("training_attempts", "training_attempts"),
            ("assessment_skill_links", "assessment_item_skills"),
            ("question_links", "grading_question_links"),
        ):
            cursor = connection.execute(
                f"DELETE FROM {table} WHERE grading_session_id = ?",
                (session_key,),
            )
            counts[key] = max(0, int(cursor.rowcount))
    return counts
    deletable_dirs = directories - protected_dirs
    standalone_files = {
        path
        for path in files
        if path not in shared_refs and not _is_inside_any(path, deletable_dirs)
    }
    directory_files = sum(_count_files(directory) for directory in deletable_dirs)
    return {
        "owned_files": len(standalone_files) + directory_files,
        "owned_directories": len(deletable_dirs),
        "protected_shared_paths": len(protected_dirs)
        + sum(1 for path in files if path in shared_refs),
    }


def hard_delete_session_from_archive(
    db: GradingRepositoryAccess,
    session_id: int,
    *,
    data_root: Path,
    question_bank_db_path: Path | None = None,
    expected_revision: str | None = None,
) -> dict[str, Any]:
    db = as_grading_repositories(db)
    session = db.get_grading_session(int(session_id))
    if session is None:
        raise ValueError(f"Session {session_id} does not exist.")
    if int(session.get("is_deleted") or 0) != 1:
        raise ValueError("Only archived sessions can be permanently deleted.")

    data_root = Path(data_root).resolve()
    files = _collect_session_file_paths(db, int(session_id), data_root)
    directories = _collect_session_dirs(int(session_id), data_root)
    shared_refs = _collect_other_session_references(db, int(session_id), data_root)

    question_bank_counts: dict[str, int] = {}
    if question_bank_db_path is not None:
        question_bank_counts = _delete_question_bank_session_references(
            Path(question_bank_db_path),
            int(session_id),
        )
    # Storage is removed before the database row. If Windows keeps a file open,
    # the archived exam remains present and the same action can safely be retried.
    file_counts = _delete_collected_storage(files, directories, shared_refs)
    if file_counts["failed_paths"]:
        raise SessionStorageDeletionIncomplete(file_counts["failed_paths"])
    db_counts = db.hard_delete_grading_session(
        int(session_id),
        expected_revision=expected_revision,
    )

    return {
        "session_id": int(session_id),
        "db_counts": db_counts,
        "question_bank_counts": question_bank_counts,
        **file_counts,
    }


def hard_delete_session_from_recycle_bin(
    db: GradingRepositoryAccess,
    session_id: int,
    *,
    data_root: Path,
) -> dict[str, Any]:
    """Compatibility alias for the former recycle-bin wording."""

    return hard_delete_session_from_archive(
        db,
        session_id,
        data_root=data_root,
    )
