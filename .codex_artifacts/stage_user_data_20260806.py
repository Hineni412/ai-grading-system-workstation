from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
from datetime import datetime, timezone
from pathlib import Path


WORKSPACE_ROOT = Path(r"D:\AI阅卷系统_工作机版_v1.5.0")
SOURCE_ROOT = (
    WORKSPACE_ROOT
    / ".worktrees"
    / "integration-usability-candidate"
    / "user_data"
)
SWAP_ROOT = Path(
    r"D:\AI阅卷系统_工作机版_v1.5.0.user-data-swap-20260806-6df9a03d"
)
INCOMING_ROOT = SWAP_ROOT / "incoming_user_data"
MANIFEST_PATH = SWAP_ROOT / "staging_manifest.json"

EXPECTED_CONTROLLED_FILES = 1102
EXPECTED_CONTROLLED_BYTES = 196_937_933
EXPECTED_SQLITE_FILES = 73
REPARSE_POINT_ATTRIBUTE = 0x400
SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")
SQLITE_SUFFIXES = {".db", ".sqlite", ".sqlite3"}


class StageError(RuntimeError):
    pass


def _is_reparse(path: Path) -> bool:
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    return bool(attributes & REPARSE_POINT_ATTRIBUTE)


def _is_sidecar(path: Path) -> bool:
    lowered = path.name.lower()
    return lowered.endswith(SIDECAR_SUFFIXES)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sqlite_uri(path: Path) -> str:
    return f"{path.resolve().as_uri()}?mode=ro&immutable=1"


def _sqlite_snapshot(path: Path) -> dict[str, object]:
    digest = hashlib.sha256()
    with sqlite3.connect(_sqlite_uri(path), uri=True, timeout=10) as connection:
        quick = [str(row[0]) for row in connection.execute("PRAGMA quick_check")]
        integrity = [
            str(row[0]) for row in connection.execute("PRAGMA integrity_check")
        ]
        if quick != ["ok"] or integrity != ["ok"]:
            raise StageError("A SQLite integrity check did not return ok")
        for line in connection.iterdump():
            digest.update(line.encode("utf-8"))
            digest.update(b"\n")
        return {
            "logical_sha256": digest.hexdigest(),
            "page_count": int(connection.execute("PRAGMA page_count").fetchone()[0]),
            "page_size": int(connection.execute("PRAGMA page_size").fetchone()[0]),
            "user_version": int(
                connection.execute("PRAGMA user_version").fetchone()[0]
            ),
            "application_id": int(
                connection.execute("PRAGMA application_id").fetchone()[0]
            ),
        }


def _backup_sqlite(source: Path, destination: Path) -> None:
    try:
        with sqlite3.connect(
            _sqlite_uri(source), uri=True, timeout=10
        ) as source_connection:
            with sqlite3.connect(destination, timeout=10) as destination_connection:
                source_connection.backup(destination_connection)
        shutil.copystat(source, destination)
    except (OSError, sqlite3.Error) as exc:
        raise StageError("Failed to create one consistent SQLite backup") from exc


def _source_inventory() -> tuple[list[tuple[Path, Path]], int]:
    source = SOURCE_ROOT.resolve(strict=True)
    if source != SOURCE_ROOT or not source.is_dir() or _is_reparse(source):
        raise StageError("The fixed source directory is not a plain directory")

    files: list[tuple[Path, Path]] = []
    controlled_bytes = 0
    for directory, child_directories, child_files in os.walk(
        source, topdown=True, followlinks=False
    ):
        current = Path(directory)
        for name in list(child_directories):
            child = current / name
            if _is_reparse(child):
                raise StageError("A reparse point exists inside the source")
        for name in child_files:
            child = current / name
            child_stat = child.lstat()
            if _is_reparse(child) or not stat.S_ISREG(child_stat.st_mode):
                raise StageError("A non-regular file exists inside the source")
            if _is_sidecar(child):
                if child.name.lower().endswith("-wal") and child_stat.st_size != 0:
                    raise StageError("A source SQLite WAL contains pending content")
                continue
            relative = child.relative_to(source)
            files.append((relative, child))
            controlled_bytes += int(child_stat.st_size)

    files.sort(key=lambda item: item[0].as_posix())
    if len(files) != EXPECTED_CONTROLLED_FILES:
        raise StageError("The controlled source file count changed")
    if controlled_bytes != EXPECTED_CONTROLLED_BYTES:
        raise StageError("The controlled source byte count changed")
    return files, controlled_bytes


def _prepare_destination() -> None:
    if SWAP_ROOT.exists() or INCOMING_ROOT.exists() or MANIFEST_PATH.exists():
        raise StageError("The timestamped swap destination already exists")
    SWAP_ROOT.mkdir(parents=False, exist_ok=False)
    INCOMING_ROOT.mkdir(parents=False, exist_ok=False)


def _stage() -> dict[str, object]:
    files, source_bytes = _source_inventory()
    sqlite_count = sum(
        source.suffix.lower() in SQLITE_SUFFIXES for _relative, source in files
    )
    if sqlite_count != EXPECTED_SQLITE_FILES:
        raise StageError("The source SQLite file count changed")

    _prepare_destination()
    path_digest = hashlib.sha256()
    content_digest = hashlib.sha256()
    sqlite_digest = hashlib.sha256()
    destination_bytes = 0

    for relative, source in files:
        destination = INCOMING_ROOT / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            if source.suffix.lower() in SQLITE_SUFFIXES:
                source_snapshot = _sqlite_snapshot(source)
                _backup_sqlite(source, destination)
                destination_snapshot = _sqlite_snapshot(destination)
                if source_snapshot != destination_snapshot:
                    raise StageError("A staged SQLite backup differs logically")
                sqlite_digest.update(relative.as_posix().encode("utf-8"))
                sqlite_digest.update(
                    str(source_snapshot["logical_sha256"]).encode("ascii")
                )
            else:
                shutil.copy2(source, destination)
                source_hash = _hash_file(source)
                if source_hash != _hash_file(destination):
                    raise StageError("A staged ordinary file hash differs")
                content_digest.update(relative.as_posix().encode("utf-8"))
                content_digest.update(source_hash.encode("ascii"))
        except StageError:
            raise
        except OSError as exc:
            raise StageError("Failed to stage one controlled source file") from exc

        path_digest.update(relative.as_posix().encode("utf-8"))
        path_digest.update(b"\n")
        destination_bytes += destination.stat().st_size

    staged_files = [
        path
        for path in INCOMING_ROOT.rglob("*")
        if path.is_file()
    ]
    staged_sidecars = [path for path in staged_files if _is_sidecar(path)]
    staged_reparse = [
        path for path in INCOMING_ROOT.rglob("*") if _is_reparse(path)
    ]
    if len(staged_files) != EXPECTED_CONTROLLED_FILES:
        raise StageError("The staged file count differs from the source")
    if staged_sidecars:
        raise StageError("A SQLite sidecar appeared in the staged data")
    if staged_reparse:
        raise StageError("A reparse point appeared in the staged data")

    source_files_after, source_bytes_after = _source_inventory()
    if len(source_files_after) != len(files) or source_bytes_after != source_bytes:
        raise StageError("The source changed while staging")

    return {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "candidate_commit": "6df9a03d56af9e717a47ce117305ddf92d849bf6",
        "controlled_file_count": len(files),
        "controlled_source_bytes": source_bytes,
        "staged_bytes": destination_bytes,
        "sqlite_file_count": sqlite_count,
        "sidecar_file_count": 0,
        "reparse_point_count": 0,
        "relative_path_sha256": path_digest.hexdigest(),
        "ordinary_content_sha256": content_digest.hexdigest(),
        "sqlite_logical_sha256": sqlite_digest.hexdigest(),
    }


def main() -> int:
    try:
        manifest = _stage()
        temporary = MANIFEST_PATH.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(MANIFEST_PATH)
    except StageError as exc:
        print(f"STAGING_FAILED: {exc}")
        return 1
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
