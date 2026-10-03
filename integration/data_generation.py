"""Per-database commit generations backed by SQLite's existing metadata.

One long-lived read-only monitor connection is kept per resolved database
path. ``PRAGMA data_version`` detects commits from other connections while
the WAL contains frames. Each distinct observed state bumps a monotonic
counter so caches can use ``commit_generation()`` instead of WAL mtimes.

Some read-only WAL connections repeatedly advance data_version while the
WAL is empty. In that state all committed content is in the main file; use
its existing SQLite header and WAL-index counters to distinguish real
changes from reads. Checkpoint metadata transitions may refresh a cache;
repeated reads of unchanged content keep the same generation.
"""

from __future__ import annotations

import sqlite3
import hashlib
import threading
from contextlib import closing
from pathlib import Path
from typing import Union

_PATH_TYPE = Union[str, Path]


def database_content_revision(path: Path, connection: sqlite3.Connection | None = None) -> str:
    """Hash read-only logical SQLite pages, preserving versions across checkpoints.

    This is the existing diagnosis snapshot algorithm. Journal mode and
    header bookkeeping are excluded; schema/data pages remain included.
    """
    if connection is None:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as owned:
            owned.execute("PRAGMA query_only = ON")
            return database_content_revision(path, owned)
    content = memoryview(connection.serialize())
    digest = hashlib.sha256()
    for start, end in ((0, 18), (20, 24), (28, 92), (100, len(content))):
        digest.update(content[start:end])
    return digest.hexdigest()

_LOCK = threading.Lock()
_MONITORS: dict[str, _GenerationMonitor] = {}
_MONITOR_LIMIT = 64


def _empty_wal_change_counter(path: Path) -> tuple | None:
    wal = Path(f"{path}-wal")

    def empty() -> bool:
        try:
            return wal.stat().st_size == 0
        except FileNotFoundError:
            return True

    try:
        if not empty():
            return None
        with path.open("rb") as database:
            header = database.read(32)
        # Check again so a writer starting a WAL while the header is read
        # sends us back to SQLite's live connection version.
        if len(header) < 32 or not empty():
            return None
        try:
            with Path(f"{path}-shm").open("rb") as index:
                wal_index = index.read(96)
        except FileNotFoundError:
            wal_index = b""
        if wal_index and (len(wal_index) < 96 or wal_index[:48] != wal_index[48:96]):
            return None
        # The WAL-index commit counter survives TRUNCATE checkpoints even
        # when the main-file counter and timestamp do not change. Its two
        # header copies must agree; otherwise let SQLite handle the writer.
        committed = (wal_index[8:12], wal_index[32:40]) if wal_index else None
        if not empty():
            return None
        state = path.stat()
        return (header[24:28], state.st_mtime_ns, state.st_size, committed)
    except OSError:
        return None


def _file_identity(path: Path) -> tuple[int, int] | None:
    try:
        info = path.stat()
    except OSError:
        return None
    return (int(info.st_dev), int(info.st_ino))


class _GenerationMonitor:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._connection: sqlite3.Connection | None = None
        self._identity: tuple[int, int] | None = None
        self._seen_version: tuple | None = None
        self._counter = 0

    def _close(self) -> None:
        connection, self._connection = self._connection, None
        self._identity = None
        if connection is not None:
            try:
                connection.close()
            except sqlite3.Error:
                pass

    def _open(self) -> sqlite3.Connection | None:
        try:
            uri = self._path.resolve(strict=True).as_uri() + "?mode=ro"
            connection = sqlite3.connect(uri, uri=True, check_same_thread=False)
            connection.execute("PRAGMA query_only = ON")
            connection.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
        except (OSError, sqlite3.Error):
            return None
        return connection

    def generation(self) -> int:
        identity = _file_identity(self._path)
        if identity is None or identity != self._identity:
            self._close()
            self._seen_version = None
            if identity is None:
                self._counter += 1
                return self._counter
            self._connection = self._open()
            if self._connection is None:
                self._counter += 1
                return self._counter
            self._identity = identity
        try:
            row = self._connection.execute("PRAGMA data_version").fetchone()
            version = int(row[0])
        except sqlite3.Error:
            self._close()
            self._seen_version = None
            self._counter += 1
            return self._counter
        main_counter = _empty_wal_change_counter(self._path)
        observed = ("main", main_counter) if main_counter is not None else ("sqlite", version)
        if observed != self._seen_version:
            self._seen_version = observed
            self._counter += 1
        return self._counter


def commit_generation(db_path: _PATH_TYPE) -> int:
    """Return the monotonic commit generation for ``db_path``.

    The value increases whenever another connection commits a write to the
    database, when the file identity changes, or when the file cannot be
    opened. Repeated read-only opens are stable; checkpoint metadata
    transitions may conservatively refresh a cache.
    """

    resolved = str(Path(db_path).resolve(strict=False))
    with _LOCK:
        monitor = _MONITORS.get(resolved)
        if monitor is None:
            if len(_MONITORS) >= _MONITOR_LIMIT:
                stale = list(_MONITORS.values())
                _MONITORS.clear()
                for item in stale:
                    item._close()
            monitor = _GenerationMonitor(Path(resolved))
            _MONITORS[resolved] = monitor
        return monitor.generation()


def reset_commit_generations() -> None:
    """Close all monitor connections and drop counters (test isolation)."""

    with _LOCK:
        monitors = list(_MONITORS.values())
        _MONITORS.clear()
    for monitor in monitors:
        monitor._close()
