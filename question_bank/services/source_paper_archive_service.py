from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from question_bank.database.paths import project_data_root


SUPPORTED_SUFFIXES = {".docx", ".pdf"}
_UNSAFE_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
_ARCHIVE_LOCKS_GUARD = threading.Lock()
_ARCHIVE_LOCKS: dict[tuple[Path, str], tuple[threading.RLock, int]] = {}


@dataclass(frozen=True, slots=True)
class ArchivedSourcePaper:
    physical_path: Path
    stored_path: str
    sha256: str
    reused: bool


def archive_source_paper(
    source_file: str | Path,
    *,
    data_root: str | Path | None = None,
    raw_papers_dir: str | Path | None = None,
) -> ArchivedSourcePaper:
    source = Path(source_file).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    return _archive_from_path(source, data_root=data_root, raw_papers_dir=raw_papers_dir)


def archive_source_bytes(
    *,
    filename: str,
    content: bytes,
    data_root: str | Path | None = None,
    raw_papers_dir: str | Path | None = None,
) -> ArchivedSourcePaper:
    if not content:
        raise ValueError("source paper content is empty")
    root, destination_dir = _roots(data_root, raw_papers_dir)
    source_name = Path(filename or "source-paper.docx").name
    suffix = Path(source_name).suffix.lower() or ".docx"
    _validate_suffix(suffix)
    digest = hashlib.sha256(content).hexdigest()
    with _archive_sha_lock(destination_dir, digest):
        reused = _reuse_matching(destination_dir, suffix, digest)
        if reused is not None:
            return _result(reused, root, digest, True)
        destination = _available_destination(
            destination_dir,
            Path(source_name).stem,
            suffix,
            digest,
        )
        _atomic_write_bytes(destination, content)
        if _sha256_file(destination) != digest:
            destination.unlink(missing_ok=True)
            raise OSError("archived source hash verification failed")
        return _result(destination, root, digest, False)


@contextmanager
def source_archive_sha_lock(
    *,
    content: bytes,
    data_root: str | Path | None = None,
    raw_papers_dir: str | Path | None = None,
) -> Iterator[None]:
    if not content:
        raise ValueError("source paper content is empty")
    _root, destination_dir = _roots(data_root, raw_papers_dir)
    digest = hashlib.sha256(content).hexdigest()
    with _archive_sha_lock(destination_dir, digest):
        yield


def _archive_from_path(
    source: Path,
    *,
    data_root: str | Path | None,
    raw_papers_dir: str | Path | None,
) -> ArchivedSourcePaper:
    root, destination_dir = _roots(data_root, raw_papers_dir)
    suffix = source.suffix.lower()
    _validate_suffix(suffix)
    digest = _sha256_file(source)
    with _archive_sha_lock(destination_dir, digest):
        reused = _reuse_matching(destination_dir, suffix, digest)
        if reused is not None:
            return _result(reused, root, digest, True)
        destination = _available_destination(destination_dir, source.stem, suffix, digest)
        fd, temp_name = tempfile.mkstemp(
            prefix=".source-paper-",
            suffix=".tmp",
            dir=destination_dir,
        )
        os.close(fd)
        temp_path = Path(temp_name)
        try:
            shutil.copy2(source, temp_path)
            if _sha256_file(temp_path) != digest:
                raise OSError("archived source hash verification failed")
            os.replace(temp_path, destination)
        finally:
            temp_path.unlink(missing_ok=True)
        return _result(destination, root, digest, False)


@contextmanager
def _archive_sha_lock(destination_dir: Path, digest: str) -> Iterator[None]:
    key = (Path(destination_dir).resolve(strict=False), str(digest))
    with _ARCHIVE_LOCKS_GUARD:
        existing = _ARCHIVE_LOCKS.get(key)
        if existing is None:
            lock = threading.RLock()
            references = 0
        else:
            lock, references = existing
        _ARCHIVE_LOCKS[key] = (lock, references + 1)
    lock.acquire()
    try:
        yield
    finally:
        lock.release()
        with _ARCHIVE_LOCKS_GUARD:
            current_lock, current_references = _ARCHIVE_LOCKS[key]
            if current_lock is lock and current_references == 1:
                del _ARCHIVE_LOCKS[key]
            else:
                _ARCHIVE_LOCKS[key] = (current_lock, current_references - 1)


def _roots(
    data_root: str | Path | None,
    raw_papers_dir: str | Path | None,
) -> tuple[Path, Path]:
    if data_root is not None:
        root = Path(data_root).expanduser().resolve()
    elif raw_papers_dir is not None:
        destination = Path(raw_papers_dir).expanduser().resolve()
        if destination.name != "raw_papers" or destination.parent.name != "question_bank":
            raise ValueError("raw_papers_dir must end with question_bank/raw_papers")
        root = destination.parent.parent
    else:
        root = project_data_root().resolve()

    destination = (
        Path(raw_papers_dir).expanduser().resolve()
        if raw_papers_dir is not None
        else root / "question_bank" / "raw_papers"
    )
    if destination != root and root not in destination.parents:
        raise ValueError("raw_papers_dir must stay inside data_root")
    destination.mkdir(parents=True, exist_ok=True)
    return root, destination


def _validate_suffix(suffix: str) -> None:
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError(f"unsupported source paper type: {suffix}")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_stem(value: str) -> str:
    return _UNSAFE_FILENAME.sub("_", value).strip(" ._") or "source-paper"


def _short_destination(directory: Path, stem: str, suffix: str, digest: str) -> Path:
    return directory / f"{_safe_stem(stem)}_{digest[:12]}{suffix}"


def _available_destination(directory: Path, stem: str, suffix: str, digest: str) -> Path:
    short = _short_destination(directory, stem, suffix, digest)
    if not short.exists() or _sha256_file(short) == digest:
        return short
    full = directory / f"{_safe_stem(stem)}_{digest}{suffix}"
    if full.exists() and _sha256_file(full) != digest:
        raise OSError("full SHA256 archive filename collision")
    return full


def _reuse_matching(directory: Path, suffix: str, digest: str) -> Path | None:
    resolved_directory = directory.resolve()
    for candidate in directory.glob(f"*_{digest[:12]}*{suffix}"):
        resolved = candidate.resolve()
        if resolved_directory not in resolved.parents:
            continue
        if candidate.is_file() and _sha256_file(candidate) == digest:
            return candidate
    return None


def _result(path: Path, root: Path, digest: str, reused: bool) -> ArchivedSourcePaper:
    resolved = path.resolve()
    return ArchivedSourcePaper(
        physical_path=resolved,
        stored_path=resolved.relative_to(root).as_posix(),
        sha256=digest,
        reused=reused,
    )


def _atomic_write_bytes(destination: Path, content: bytes) -> None:
    fd, temp_name = tempfile.mkstemp(
        prefix=".source-paper-",
        suffix=".tmp",
        dir=destination.parent,
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, destination)
    finally:
        Path(temp_name).unlink(missing_ok=True)


__all__ = [
    "ArchivedSourcePaper",
    "archive_source_bytes",
    "archive_source_paper",
    "source_archive_sha_lock",
]
