"""Per-database commit generations backed by ``PRAGMA data_version``.

One long-lived read-only monitor connection is kept per resolved database
path. ``PRAGMA data_version`` changes on that connection whenever another
connection in the process commits a write, but stays unchanged across
read-only opens, ``PRAGMA wal_checkpoint`` runs, and ``-wal`` mtime flaps.
Each distinct observed value bumps a monotonic counter so cache keys can
use ``commit_generation()`` instead of file size/mtime fingerprints.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Union

_PATH_TYPE = Union[str, Path]

_LOCK = threading.Lock()
_MONITORS: dict[str, "_GenerationMonitor"] = {}
_MONITOR_LIMIT = 64


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
        self._seen_version: int | None = None
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
        if version != self._seen_version:
            self._seen_version = version
            self._counter += 1
        return self._counter


def commit_generation(db_path: _PATH_TYPE) -> int:
    """Return the monotonic commit generation for ``db_path``.

    The value increases whenever another connection commits a write to the
    database, when the file identity changes, or when the file cannot be
    opened; it is stable across read-only opens and checkpoints.
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
