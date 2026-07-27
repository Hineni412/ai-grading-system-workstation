from __future__ import annotations

import os
import shutil
import threading
import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from backend.repositories.access import GradingRepositoryAccess, as_grading_repositories
from question_bank.database.schema import connect as connect_question_bank


SESSION_STORAGE_DIR_NAMES = ("templates", "exams", "annotated")
SESSION_DELETE_STAGING_DIR_NAME = ".session-delete-staging"
SESSION_DELETE_MANIFEST_NAME = "manifest.json"
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


class SessionPermanentDeletionRecoveryFailed(RuntimeError):
    """An interrupted permanent delete needs another recovery attempt."""

    def __init__(self, phase: str, *, deletion_committed: bool) -> None:
        super().__init__("Permanent deletion recovery could not be completed.")
        self.phase = str(phase)
        self.deletion_committed = bool(deletion_committed)


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


def _storage_deletion_plan(
    files: set[Path],
    directories: set[Path],
    shared_refs: set[Path],
) -> tuple[list[Path], list[Path], dict[str, int]]:
    protected_dirs = {
        directory
        for directory in directories
        if _contains_shared_reference(directory, shared_refs)
    }
    directories_to_delete = directories - protected_dirs
    files_to_delete = {
        path
        for path in files
        if path not in shared_refs and not _is_inside_any(path, directories_to_delete)
    }
    skipped_shared = len(protected_dirs) + sum(1 for path in files if path in shared_refs)
    directory_files = sum(_count_files(directory) for directory in directories_to_delete)
    return (
        sorted(files_to_delete, key=lambda item: str(item).casefold()),
        sorted(directories_to_delete, key=lambda item: str(item).casefold()),
        {
            "deleted_files": len(files_to_delete) + directory_files,
            "deleted_dirs": len(directories_to_delete),
            "skipped_shared": skipped_shared,
        },
    )


def _session_delete_staging_dir(data_root: Path, session_id: int) -> Path:
    return (
        Path(data_root).resolve()
        / SESSION_DELETE_STAGING_DIR_NAME
        / f"session_{int(session_id)}"
    )


def _manifest_entry_path(
    value: object,
    *,
    root: Path,
    field_name: str,
) -> Path:
    raw = str(value or "").strip().replace("\\", "/")
    candidate = Path(raw)
    if not raw or candidate.is_absolute() or ".." in candidate.parts:
        raise SessionPermanentDeletionRecoveryFailed(
            f"invalid_{field_name}",
            deletion_committed=False,
        )
    resolved = (root / candidate).resolve()
    if not _is_under(resolved, root):
        raise SessionPermanentDeletionRecoveryFailed(
            f"invalid_{field_name}",
            deletion_committed=False,
        )
    return resolved


def _write_staging_manifest(staging_dir: Path, payload: dict[str, Any]) -> None:
    staging_dir.mkdir(parents=True, exist_ok=True)
    destination = staging_dir / SESSION_DELETE_MANIFEST_NAME
    temporary = staging_dir / f"{SESSION_DELETE_MANIFEST_NAME}.tmp"
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, destination)


def _read_staging_manifest(
    data_root: Path,
    session_id: int,
) -> tuple[Path, dict[str, Any]] | None:
    staging_dir = _session_delete_staging_dir(data_root, int(session_id))
    if not staging_dir.exists():
        return None
    manifest_path = staging_dir / SESSION_DELETE_MANIFEST_NAME
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SessionPermanentDeletionRecoveryFailed(
            "manifest_read",
            deletion_committed=False,
        ) from exc
    try:
        manifest_version = int(payload.get("version") or 0)
        manifest_session_id = int(payload.get("session_id") or 0)
    except (AttributeError, TypeError, ValueError) as exc:
        raise SessionPermanentDeletionRecoveryFailed(
            "manifest_validation",
            deletion_committed=False,
        ) from exc
    if (
        not isinstance(payload, dict)
        or manifest_version != 1
        or manifest_session_id != int(session_id)
        or not isinstance(payload.get("entries"), list)
        or not isinstance(payload.get("storage_counts"), dict)
    ):
        raise SessionPermanentDeletionRecoveryFailed(
            "manifest_validation",
            deletion_committed=False,
        )
    _storage_counts_from_manifest(payload)
    return staging_dir, payload


def _manifest_paths(
    data_root: Path,
    staging_dir: Path,
    payload: dict[str, Any],
) -> list[tuple[Path, Path]]:
    entries: list[tuple[Path, Path]] = []
    for raw_entry in payload["entries"]:
        if not isinstance(raw_entry, dict):
            raise SessionPermanentDeletionRecoveryFailed(
                "manifest_validation",
                deletion_committed=False,
            )
        original = _manifest_entry_path(
            raw_entry.get("original"),
            root=data_root,
            field_name="original_path",
        )
        staged = _manifest_entry_path(
            raw_entry.get("staged"),
            root=staging_dir,
            field_name="staged_path",
        )
        entries.append((original, staged))
    return entries


def _remove_empty_staging_parent(staging_dir: Path) -> None:
    try:
        staging_dir.parent.rmdir()
    except OSError:
        pass


def _restore_staged_storage(
    data_root: Path,
    session_id: int,
) -> None:
    loaded = _read_staging_manifest(data_root, int(session_id))
    if loaded is None:
        return
    staging_dir, payload = loaded
    entries = _manifest_paths(data_root, staging_dir, payload)
    failed = False
    for original, staged in reversed(entries):
        if staged.exists() and original.exists():
            failed = True
            continue
        if staged.exists():
            try:
                original.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staged, original)
            except OSError:
                failed = True
        elif not original.exists():
            failed = True
    if failed:
        raise SessionPermanentDeletionRecoveryFailed(
            "storage_restore",
            deletion_committed=False,
        )
    try:
        shutil.rmtree(staging_dir)
    except OSError as exc:
        raise SessionPermanentDeletionRecoveryFailed(
            "storage_restore_cleanup",
            deletion_committed=False,
        ) from exc
    _remove_empty_staging_parent(staging_dir)


def _stage_collected_storage(
    *,
    data_root: Path,
    session_id: int,
    files: set[Path],
    directories: set[Path],
    shared_refs: set[Path],
) -> tuple[Path, dict[str, Any]]:
    standalone_files, directories_to_delete, storage_counts = _storage_deletion_plan(
        files,
        directories,
        shared_refs,
    )
    staging_dir = _session_delete_staging_dir(data_root, int(session_id))
    if staging_dir.exists():
        _restore_staged_storage(data_root, int(session_id))
    planned_paths = [
        *(("file", path) for path in standalone_files),
        *(("directory", path) for path in directories_to_delete),
    ]
    entries = [
        {
            "kind": kind,
            "original": path.resolve().relative_to(data_root.resolve()).as_posix(),
            "staged": f"items/{index:04d}",
        }
        for index, (kind, path) in enumerate(planned_paths, start=1)
    ]
    payload: dict[str, Any] = {
        "version": 1,
        "session_id": int(session_id),
        "state": "prepared",
        "storage_counts": storage_counts,
        "entries": entries,
    }
    _write_staging_manifest(staging_dir, payload)
    manifest_paths = _manifest_paths(data_root, staging_dir, payload)
    try:
        for original, staged in manifest_paths:
            staged.parent.mkdir(parents=True, exist_ok=True)
            os.replace(original, staged)
    except OSError as exc:
        try:
            _restore_staged_storage(data_root, int(session_id))
        except SessionPermanentDeletionRecoveryFailed as recovery_exc:
            raise recovery_exc from exc
        raise SessionStorageDeletionIncomplete([str(original)]) from exc
    return staging_dir, payload


def _purge_staged_storage(
    data_root: Path,
    session_id: int,
) -> bool:
    staging_dir = _session_delete_staging_dir(data_root, int(session_id))
    if not staging_dir.exists():
        return True
    try:
        shutil.rmtree(staging_dir)
    except OSError:
        return False
    _remove_empty_staging_parent(staging_dir)
    return True


def preview_session_permanent_deletion(
    db: GradingRepositoryAccess,
    session_id: int,
    *,
    data_root: Path,
) -> dict[str, int]:
    """Count owned storage without changing the archived exam."""

    db = as_grading_repositories(db)
    data_root = Path(data_root).resolve()
    if (
        _read_staging_manifest(data_root, int(session_id)) is not None
        and db.get_grading_session(int(session_id)) is not None
    ):
        _restore_staged_storage(data_root, int(session_id))
    files = _collect_session_file_paths(db, int(session_id), data_root)
    directories = _collect_session_dirs(int(session_id), data_root)
    shared_refs = _collect_other_session_references(db, int(session_id), data_root)
    _standalone_files, _deletable_dirs, counts = _storage_deletion_plan(
        files,
        directories,
        shared_refs,
    )
    return {
        "owned_files": int(counts["deleted_files"]),
        "owned_directories": int(counts["deleted_dirs"]),
        "protected_shared_paths": int(counts["skipped_shared"]),
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


def _question_bank_session_reference_impact(
    connection: Any,
    session_id: int,
) -> dict[str, Any]:
    session_key = str(int(session_id))
    available_tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    link_count = int(
        connection.execute(
            "SELECT COUNT(*) FROM grading_question_links "
            "WHERE grading_session_id = ?",
            (session_key,),
        ).fetchone()[0]
    )
    skill_count = (
        int(
            connection.execute(
                "SELECT COUNT(*) FROM assessment_item_skills "
                "WHERE grading_session_id = ?",
                (session_key,),
            ).fetchone()[0]
        )
        if "assessment_item_skills" in available_tables
        else 0
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


def question_bank_session_reference_impact(
    question_bank_db_path: Path,
    session_id: int,
) -> dict[str, Any]:
    """Inspect derived references while preserving bank questions and tags."""

    with connect_question_bank(Path(question_bank_db_path)) as connection:
        return _question_bank_session_reference_impact(
            connection,
            int(session_id),
        )


def _delete_question_bank_session_references(
    connection: Any,
    session_id: int,
) -> dict[str, int]:
    session_key = str(int(session_id))
    impact = _question_bank_session_reference_impact(
        connection,
        int(session_id),
    )
    blocking_tasks = list(impact["blocking_training_tasks"])
    if blocking_tasks:
        raise SessionDerivedTrainingDataExists(blocking_tasks)

    counts: dict[str, int] = {}
    available_tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    for key, table in (
        ("training_attempts", "training_attempts"),
        ("assessment_skill_links", "assessment_item_skills"),
        ("question_links", "grading_question_links"),
    ):
        if table not in available_tables:
            counts[key] = 0
            continue
        cursor = connection.execute(
            f"DELETE FROM {table} WHERE grading_session_id = ?",
            (session_key,),
        )
        counts[key] = max(0, int(cursor.rowcount))
    return counts


def _delete_question_bank_session_references_committed(
    question_bank_db_path: Path,
    session_id: int,
) -> dict[str, int]:
    with connect_question_bank(Path(question_bank_db_path)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        return _delete_question_bank_session_references(
            connection,
            int(session_id),
        )


def _storage_counts_from_manifest(payload: dict[str, Any]) -> dict[str, int]:
    raw = payload.get("storage_counts")
    if not isinstance(raw, dict):
        raise SessionPermanentDeletionRecoveryFailed(
            "manifest_storage_counts",
            deletion_committed=False,
        )
    try:
        return {
            "deleted_files": max(0, int(raw.get("deleted_files") or 0)),
            "deleted_dirs": max(0, int(raw.get("deleted_dirs") or 0)),
            "skipped_shared": max(0, int(raw.get("skipped_shared") or 0)),
        }
    except (TypeError, ValueError) as exc:
        raise SessionPermanentDeletionRecoveryFailed(
            "manifest_storage_counts",
            deletion_committed=False,
        ) from exc


def recover_interrupted_session_permanent_deletion(
    db: GradingRepositoryAccess,
    session_id: int,
    *,
    data_root: Path,
    question_bank_db_path: Path | None,
) -> dict[str, Any] | None:
    """Restore a prepared delete, or finish one whose grading row is gone."""

    db = as_grading_repositories(db)
    data_root = Path(data_root).resolve()
    loaded = _read_staging_manifest(data_root, int(session_id))
    if loaded is None:
        return None
    _staging_dir, payload = loaded
    if db.get_grading_session(int(session_id)) is not None:
        _restore_staged_storage(data_root, int(session_id))
        return None

    question_bank_counts: dict[str, int] = {}
    if question_bank_db_path is not None:
        try:
            question_bank_counts = (
                _delete_question_bank_session_references_committed(
                    Path(question_bank_db_path),
                    int(session_id),
                )
            )
        except Exception as exc:
            raise SessionPermanentDeletionRecoveryFailed(
                "question_bank_cleanup",
                deletion_committed=True,
            ) from exc
    storage_counts = _storage_counts_from_manifest(payload)
    cleanup_pending = not _purge_staged_storage(data_root, int(session_id))
    return {
        "session_id": int(session_id),
        "db_counts": {},
        "question_bank_counts": question_bank_counts,
        **storage_counts,
        "storage_cleanup_pending": cleanup_pending,
        "recovered_interrupted_delete": True,
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
    data_root = Path(data_root).resolve()
    recovered = recover_interrupted_session_permanent_deletion(
        db,
        int(session_id),
        data_root=data_root,
        question_bank_db_path=question_bank_db_path,
    )
    if recovered is not None:
        return recovered
    session = db.get_grading_session(int(session_id))
    if session is None:
        raise ValueError(f"Session {session_id} does not exist.")
    if int(session.get("is_deleted") or 0) != 1:
        raise ValueError("Only archived sessions can be permanently deleted.")

    files = _collect_session_file_paths(db, int(session_id), data_root)
    directories = _collect_session_dirs(int(session_id), data_root)
    shared_refs = _collect_other_session_references(db, int(session_id), data_root)
    _staging_dir, manifest = _stage_collected_storage(
        data_root=data_root,
        session_id=int(session_id),
        files=files,
        directories=directories,
        shared_refs=shared_refs,
    )

    question_bank_counts: dict[str, int] = {}
    db_counts: dict[str, int] = {}
    deletion_committed = False
    try:
        if question_bank_db_path is None:
            db_counts = db.hard_delete_grading_session(
                int(session_id),
                expected_revision=expected_revision,
            )
            deletion_committed = True
        else:
            with connect_question_bank(Path(question_bank_db_path)) as connection:
                connection.execute("BEGIN IMMEDIATE")
                question_bank_counts = _delete_question_bank_session_references(
                    connection,
                    int(session_id),
                )
                # This IMMEDIATE grading transaction is the final revision and
                # active-work check. Question-bank changes are still uncommitted,
                # and owned files are only staged for reversible deletion.
                db_counts = db.hard_delete_grading_session(
                    int(session_id),
                    expected_revision=expected_revision,
                )
                deletion_committed = True
    except Exception as exc:
        if not deletion_committed:
            try:
                _restore_staged_storage(data_root, int(session_id))
            except SessionPermanentDeletionRecoveryFailed as recovery_exc:
                raise recovery_exc from exc
            raise
        try:
            if question_bank_db_path is not None:
                question_bank_counts = (
                    _delete_question_bank_session_references_committed(
                        Path(question_bank_db_path),
                        int(session_id),
                    )
                )
        except Exception as cleanup_exc:
            raise SessionPermanentDeletionRecoveryFailed(
                "question_bank_commit_recovery",
                deletion_committed=True,
            ) from cleanup_exc

    file_counts = _storage_counts_from_manifest(manifest)
    cleanup_pending = not _purge_staged_storage(data_root, int(session_id))
    return {
        "session_id": int(session_id),
        "db_counts": db_counts,
        "question_bank_counts": question_bank_counts,
        **file_counts,
        "storage_cleanup_pending": cleanup_pending,
        "recovered_interrupted_delete": False,
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
