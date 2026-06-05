from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from db_manager import DBManager


SESSION_STORAGE_DIR_NAMES = ("templates", "exams", "annotated")


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
    return dirs


def _collect_session_file_paths(db: DBManager, session_id: int, data_root: Path) -> set[Path]:
    files: set[Path] = set()
    for raw_path in db.collect_session_storage_paths(session_id):
        resolved = _resolve_stored_candidate(raw_path, data_root)
        if resolved is not None and resolved.is_file():
            files.add(resolved)
    return files


def _collect_other_session_references(db: DBManager, session_id: int, data_root: Path) -> set[Path]:
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


def hard_delete_session_from_recycle_bin(
    db: DBManager,
    session_id: int,
    *,
    data_root: Path,
) -> dict[str, Any]:
    session = db.get_grading_session(int(session_id))
    if session is None:
        raise ValueError(f"Session {session_id} does not exist.")
    if int(session.get("is_deleted") or 0) != 1:
        raise ValueError("Only sessions in recycle bin can be permanently deleted.")

    data_root = Path(data_root).resolve()
    files = _collect_session_file_paths(db, int(session_id), data_root)
    directories = _collect_session_dirs(int(session_id), data_root)
    shared_refs = _collect_other_session_references(db, int(session_id), data_root)

    db_counts = db.hard_delete_grading_session(int(session_id))
    file_counts = _delete_collected_storage(files, directories, shared_refs)

    return {
        "session_id": int(session_id),
        "db_counts": db_counts,
        **file_counts,
    }
