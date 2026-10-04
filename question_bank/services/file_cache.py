"""Process-level file caches keyed by file identity (path, size, mtime).

Repeated teacher flows re-read the same question-bank assets (rich-content
sidecars, extracted images) once per candidate. These caches skip the disk
read/parse/decode when the file is unchanged; a changed size or mtime produces
a different key, so edits invalidate naturally.
"""

from __future__ import annotations

import os
import stat as stat_module
import threading
from collections import OrderedDict
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any

_BYTE_BUDGET = 256 * 1024 * 1024
_MAX_CACHED_FILE_BYTES = 16 * 1024 * 1024
_MAX_ENTRIES = 4096

_lock = threading.Lock()
_bytes_cache: OrderedDict[tuple[str, int, int], bytes] = OrderedDict()
_bytes_used = 0
_parsed_cache: OrderedDict[tuple[str, int, int], Any] = OrderedDict()
_digest_cache: OrderedDict[tuple[Any, ...], str] = OrderedDict()
_resolve_cache: OrderedDict[tuple[Any, ...], Path] = OrderedDict()
_resolved_path_cache: OrderedDict[str, Path] = OrderedDict()
_ROOT_MARKER_CACHE: OrderedDict[str, tuple[str, str]] = OrderedDict()
_RESOLVED_PATH_LIMIT = 128


def _root_marker(root: Path) -> tuple[str, str]:
    """Normcased root text plus a child-matching prefix, memoized per root."""
    raw = os.fspath(root)
    with _lock:
        marker = _ROOT_MARKER_CACHE.get(raw)
        if marker is not None:
            _ROOT_MARKER_CACHE.move_to_end(raw)
            return marker
    base = os.path.normcase(raw)
    marker = (base, base if base.endswith(os.sep) else base + os.sep)
    with _lock:
        _ROOT_MARKER_CACHE[raw] = marker
        _ROOT_MARKER_CACHE.move_to_end(raw)
        _trim(_ROOT_MARKER_CACHE, _RESOLVED_PATH_LIMIT)
    return marker


def is_within(path: Path, root: Path) -> bool:
    """Fast ``path == root or path.is_relative_to(root)`` for absolute paths.

    ``is_relative_to`` re-splits and normcases both operands on every call;
    comparing the normcased strings once per root keeps identical results —
    the root must match the path either fully or as a separator-terminated
    prefix, which is exactly what part-wise containment means.
    """
    base, prefix = _root_marker(root)
    text = os.path.normcase(os.fspath(path))
    return text == base or text.startswith(prefix)


def _file_read_identity(path: Path) -> tuple | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if stat_module.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        return ("reparse",)
    return (info.st_mode, info.st_dev, info.st_ino, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


class _FileReadTrace:
    def __init__(self) -> None:
        self.paths: dict[str, tuple | None] = {}
        self.lock = threading.Lock()
        self.usable = True

    def record(self, path: Path) -> None:
        absolute = Path(os.path.abspath(path))
        with self.lock:
            key = str(absolute)
            if key not in self.paths:
                try:
                    self.paths[key] = _file_read_identity(absolute)
                except OSError:
                    self.usable = False


_FILE_READ_TRACE: ContextVar[_FileReadTrace | None] = ContextVar("question_bank_file_read_trace", default=None)


@contextmanager
def capture_file_reads():
    """Capture positive and negative lookups for a derived local snapshot."""
    trace = _FileReadTrace()
    token = _FILE_READ_TRACE.set(trace)
    try:
        yield trace
    finally:
        _FILE_READ_TRACE.reset(token)


class _BatchFileReads:
    """Fresh directory metadata shared only by one input-loading batch."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.lock = threading.Lock()
        self.directories: dict[Path, tuple[Path, dict[str, os.stat_result]] | None] = {}

    def lookup(self, path: Path) -> tuple[Path, os.stat_result | None] | None:
        if not path.is_absolute() or not is_within(path, self.root):
            return None
        parent = path.parent
        with self.lock:
            if parent not in self.directories:
                try:
                    resolved_parent = parent.resolve()
                    with os.scandir(parent) as entries:
                        files = {os.path.normcase(entry.name): entry.stat(follow_symlinks=False) for entry in entries}
                    self.directories[parent] = (resolved_parent, files)
                except OSError:
                    self.directories[parent] = None
            directory = self.directories[parent]
        if directory is None:
            return None
        resolved_parent, files = directory
        info = files.get(os.path.normcase(path.name))
        # Reparse points (including symbolic links) still use the original
        # per-file resolution and stat, followed by the caller's root check.
        if info is not None and (
            stat_module.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & 0x400
        ):
            return None
        return resolved_parent / path.name, info


_BATCH_FILE_READS: ContextVar[_BatchFileReads | None] = ContextVar(
    "question_bank_batch_file_reads", default=None,
)


@contextmanager
def batch_file_reads(root: Path):
    """Reuse fresh scan metadata within a batch; never retain it across calls."""
    token = _BATCH_FILE_READS.set(_BatchFileReads(memoized_resolve(root)))
    try:
        yield
    finally:
        _BATCH_FILE_READS.reset(token)


def _batch_lookup(path: Path):
    trace = _FILE_READ_TRACE.get()
    if trace is not None:
        trace.record(path)
    batch = _BATCH_FILE_READS.get()
    return batch.lookup(path) if batch is not None else None


def file_is_file(path: Path) -> bool:
    found = _batch_lookup(path)
    if found is not None:
        return found[1] is not None and stat_module.S_ISREG(found[1].st_mode)
    return path.is_file()


def resolve_existing_file(path: Path) -> Path | None:
    found = _batch_lookup(path)
    if found is not None:
        resolved, info = found
        return resolved if info is not None and stat_module.S_ISREG(info.st_mode) else None
    try:
        return path.resolve() if path.is_file() else None
    except OSError:
        return None


def memoized_resolve(path: Path) -> Path:
    """Return ``path.expanduser().resolve()`` cached by the raw path string.

    Resolution walks the filesystem (``GetFinalPathNameByHandle`` on
    Windows) once per distinct input instead of once per lookup. The
    resolved value is keyed on the unresolved string, so callers keep full
    control over *when* resolution happens — it just is not repeated.
    """
    raw = os.fspath(path)
    with _lock:
        cached = _resolved_path_cache.get(raw)
        if cached is not None:
            _resolved_path_cache.move_to_end(raw)
            return cached
    resolved = Path(raw).expanduser().resolve()
    with _lock:
        _resolved_path_cache[raw] = resolved
        _resolved_path_cache.move_to_end(raw)
        _trim(_resolved_path_cache, _RESOLVED_PATH_LIMIT)
    return resolved


def _file_key(path: Path) -> tuple[str, int, int] | None:
    found = _batch_lookup(path)
    if found is not None:
        info = found[1]
        if info is None:
            return None
    else:
        try:
            info = path.stat()
        except OSError:
            return None
    if not stat_module.S_ISREG(info.st_mode):
        return None
    return (str(path), int(info.st_size), int(info.st_mtime_ns))


def _trim(mapping: OrderedDict, limit: int = _MAX_ENTRIES) -> None:
    while len(mapping) > limit:
        mapping.popitem(last=False)


def cached_file_bytes(path: Path) -> bytes:
    """Return file bytes, reusing a cached copy while the stat is unchanged."""
    key = _file_key(path)
    if key is None:
        return path.read_bytes()
    global _bytes_used
    with _lock:
        cached = _bytes_cache.get(key)
        if cached is not None:
            _bytes_cache.move_to_end(key)
            return cached
    data = path.read_bytes()
    if len(data) > _MAX_CACHED_FILE_BYTES:
        return data
    with _lock:
        _bytes_cache[key] = data
        _bytes_cache.move_to_end(key)
        _bytes_used += len(data)
        while _bytes_used > _BYTE_BUDGET:
            _evicted_key, evicted = _bytes_cache.popitem(last=False)
            _bytes_used -= len(evicted)
    return data


def cached_parsed_file(path: Path, parse: Callable[[Path], Any]) -> Any:
    """Return a parsed representation cached by file identity.

    The stored object is shared between callers; ``parse`` results must be
    treated as read-only by callers.
    """
    key = _file_key(path)
    if key is None:
        return parse(path)
    with _lock:
        if key in _parsed_cache:
            value = _parsed_cache[key]
            _parsed_cache.move_to_end(key)
            return value
    value = parse(path)
    with _lock:
        _parsed_cache[key] = value
        _parsed_cache.move_to_end(key)
        _trim(_parsed_cache)
    return value


def cached_processed_image_digest(
    path: Path,
    *,
    variant: str,
    compute: Callable[[Path], str],
) -> str:
    """Cache a derived image digest by file identity plus a variant token.

    ``variant`` must capture every input besides the file itself (e.g. crop
    polygon, comparison mode). ``compute`` receives the resolved file path on
    a miss.
    """
    key = _file_key(path)
    if key is None:
        return compute(path)
    full_key = (*key, variant)
    with _lock:
        cached = _digest_cache.get(full_key)
        if cached is not None:
            _digest_cache.move_to_end(full_key)
            return cached
    digest = compute(path)
    with _lock:
        _digest_cache[full_key] = digest
        _digest_cache.move_to_end(full_key)
        _trim(_digest_cache)
    return digest


def _dir_stamp(root: Path) -> tuple[Any, ...] | None:
    """Fingerprint a search directory and its direct children.

    Adding or removing a file inside the directory (or one nested level)
    changes the stamp, so cached resolutions refresh on import.
    """
    try:
        info = root.stat()
        if not stat_module.S_ISDIR(info.st_mode):
            return None
        children: list[tuple[str, int]] = []
        try:
            with os.scandir(root) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir():
                            # DirEntry.stat() can return a stale mtime on
                            # Windows (NTFS caches dir metadata in the scan
                            # results); stat the path for the live value.
                            children.append(
                                (
                                    entry.name,
                                    int(os.stat(entry.path).st_mtime_ns),
                                )
                            )
                    except OSError:
                        continue
        except OSError:
            pass
        return (int(info.st_mtime_ns), tuple(sorted(children)))
    except OSError:
        return None


def resolve_stamp(data_root: Path, search_subdirs: tuple[str, ...]) -> tuple[Any, ...]:
    return tuple(_dir_stamp(data_root / subdir) for subdir in search_subdirs)


def _hit_valid(
    path: Path,
    mode: str,
    candidates: tuple[Path, ...],
    stamp: tuple[Any, ...] | None,
    root: Path,
    subdirs: tuple[str, ...],
    check_candidates: Callable[[tuple[Path, ...]], list[Path]],
) -> bool:
    try:
        existing = check_candidates(candidates)
    except OSError:
        return False
    if mode == "direct":
        # Resolution was decided by candidate existence alone; re-checking
        # the same few paths detects deletion or a newly ambiguous sibling.
        return existing == [path]
    # Fallback mode: a direct candidate appearing meanwhile would have won
    # the resolution, so the hit only stands while none exist.
    if existing:
        return False
    try:
        if not file_is_file(path):
            return False
    except OSError:
        return False
    return resolve_stamp(root, subdirs) == stamp


def cached_asset_resolution(
    *,
    saved_path: str,
    data_root: Path,
    search_subdirs: tuple[str, ...],
    resolve: Callable[[], tuple[Path, str, tuple[Path, ...]]],
    check_candidates: Callable[[tuple[Path, ...]], list[Path]],
) -> Path:
    """Memoize asset-path resolution, invalidated by filesystem changes.

    ``resolve`` performs the full (security-checked) resolution on a miss and
    returns ``(path, mode, candidates)``. ``mode`` is ``"direct"`` when the
    result was decided purely by direct-candidate existence or ``"fallback"``
    when a directory scan contributed; ``candidates`` is the direct-candidate
    list checked first. Direct hits are revalidated by re-checking those few
    paths; fallback hits additionally compare a search-directory stamp so
    added or removed files refresh the result. Unresolved results are not
    cached.
    """
    root = memoized_resolve(data_root)
    subdirs = tuple(search_subdirs)
    key = (saved_path, str(root), subdirs)
    with _lock:
        entry = _resolve_cache.get(key)
        if entry is not None:
            _resolve_cache.move_to_end(key)
    # Filesystem validation can be slow on Windows. Do it outside the global
    # cache lock so independent question assets can be checked concurrently.
    if entry is not None:
        path, mode, candidates, stamp = entry
        if _hit_valid(
            path, mode, candidates, stamp, root, subdirs, check_candidates
        ):
            return path
    resolved, mode, candidates = resolve()
    try:
        if not file_is_file(resolved):
            return resolved
    except OSError:
        return resolved
    stamp = resolve_stamp(root, subdirs) if mode == "fallback" else None
    with _lock:
        _resolve_cache[key] = (resolved, mode, candidates, stamp)
        _resolve_cache.move_to_end(key)
        _trim(_resolve_cache)
    return resolved


def clear_file_caches() -> None:
    """Drop every cached entry (test isolation and explicit resets)."""
    global _bytes_used
    with _lock:
        _bytes_cache.clear()
        _parsed_cache.clear()
        _digest_cache.clear()
        _resolve_cache.clear()
        _resolved_path_cache.clear()
        _ROOT_MARKER_CACHE.clear()
        _bytes_used = 0


__all__ = [
    "cached_asset_resolution",
    "cached_file_bytes",
    "cached_parsed_file",
    "cached_processed_image_digest",
    "clear_file_caches",
    "is_within",
    "memoized_resolve",
]
