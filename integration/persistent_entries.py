"""Per-entry durable store for derived results that outlive the process.

One small ``*.entry`` file per key under a controlled directory; the file
name is ``sha256(pickle(key))[:40]`` and the payload is
``{"version": 1, "key", "signature", "payload"}``. A stored entry is used
only when its key and signature match the caller's; every read or write
failure is silent and falls back to recomputation.
"""

from __future__ import annotations

import hashlib
import os
import pickle
import tempfile
import threading
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import Any

_SNAPSHOT_LIMIT = 512
_SAVED_LIMIT = 2048


def _file_state(path: Path) -> tuple:
    info = path.stat()
    return (str(path), info.st_dev, info.st_ino, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


class PersistentEntryStore:
    """Versioned one-file-per-key store with bounds and silent failure.

    ``signature`` in ``get`` is either the expected value or a callable that
    receives the stored signature and returns the live expected signature (or
    ``None`` to reject cheaply before the expensive signature is computed).
    """

    def __init__(self, directory: Path, *, max_entries: int, max_bytes: int) -> None:
        self._dir = Path(directory)
        self._max_entries = int(max_entries)
        self._max_bytes = int(max_bytes)
        self._lock = threading.Lock()
        # Parsed entry snapshots keyed by verified file identity, so repeated
        # reads of an unchanged file do not unpickle again.
        self._snapshots: OrderedDict[tuple, Any] = OrderedDict()
        # Verified on-disk state per entry file so a repeated put can skip
        # rewriting identical bytes without loading the file back.
        self._saved: OrderedDict[str, tuple] = OrderedDict()

    def entry_path(self, key: Any) -> Path:
        digest = hashlib.sha256(
            pickle.dumps(key, pickle.HIGHEST_PROTOCOL)
        ).hexdigest()[:40]
        return self._dir / f"{digest}.entry"

    def _snapshot(self, path: Path) -> Any:
        file_state = _file_state(path)
        with self._lock:
            snapshot = self._snapshots.get(file_state)
            if snapshot is not None:
                self._snapshots.move_to_end(file_state)
                return snapshot
        with path.open("rb") as saved:
            snapshot = pickle.load(saved)
        if _file_state(path) != file_state:
            return None  # The file changed under the read.
        with self._lock:
            self._snapshots[file_state] = snapshot
            self._snapshots.move_to_end(file_state)
            while len(self._snapshots) > _SNAPSHOT_LIMIT:
                self._snapshots.popitem(last=False)
        return snapshot

    def get(self, key: Any, signature: Any | Callable[[Any], Any]) -> Any:
        """Return the stored payload, or ``None`` on any mismatch or error."""
        try:
            path = self.entry_path(key)
            snapshot = self._snapshot(path)
            if (snapshot is None or not isinstance(snapshot, dict)
                    or snapshot.get("version") != 1
                    or snapshot.get("key") != key):
                return None
            stored_signature = snapshot["signature"]
            expected = (signature(stored_signature)
                        if callable(signature) else signature)
            if expected is None or stored_signature != expected:
                return None
            return snapshot.get("payload")
        except (OSError, pickle.PickleError, EOFError, KeyError, TypeError,
                ValueError, AttributeError, ImportError):
            return None

    def put(self, key: Any, signature: Any, payload: Any) -> None:
        path = self.entry_path(key)
        temporary: Path | None = None
        try:
            data = pickle.dumps(
                {"version": 1, "key": key, "signature": signature,
                 "payload": payload},
                pickle.HIGHEST_PROTOCOL,
            )
            digest = hashlib.sha256(data).digest()
            with self._lock:
                memo = self._saved.get(str(path))
                if memo is not None:
                    try:
                        current = _file_state(path)
                    except OSError:
                        current = None
                    if current == memo[0] and signature == memo[1] and digest == memo[2]:
                        self._saved.move_to_end(str(path))
                        return
                self._dir.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(dir=self._dir, delete=False) as saved:
                    temporary = Path(saved.name)
                    saved.write(data)
                os.replace(temporary, path)
                try:
                    file_state = _file_state(path)
                    self._saved[str(path)] = (file_state, signature, digest)
                    self._saved.move_to_end(str(path))
                    while len(self._saved) > _SAVED_LIMIT:
                        self._saved.popitem(last=False)
                except OSError:
                    self._saved.pop(str(path), None)
                self._evict(keep=path)
        except (OSError, pickle.PickleError):
            pass  # Saving a derived cache must not fail the request.
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def _evict(self, *, keep: Path) -> None:
        """Drop the oldest entry files beyond the count/size caps, never the
        file that was just written."""
        try:
            entries = [
                item for item in self._dir.iterdir()
                if item.is_file() and item.suffix == ".entry"
            ]
            size = sum(item.stat().st_size for item in entries)
            overflow = len(entries) - self._max_entries
            if overflow <= 0 and size <= self._max_bytes:
                return
            entries.sort(key=lambda item: (item.stat().st_mtime_ns, item.name))
            keep = keep.resolve(strict=False)
            for item in entries:
                if overflow <= 0 and size <= self._max_bytes:
                    break
                if item.resolve(strict=False) == keep:
                    continue
                try:
                    item_size = item.stat().st_size
                    item.unlink()
                    self._saved.pop(str(item), None)
                    overflow -= 1
                    size -= item_size
                except OSError:
                    pass
        except OSError:
            pass


_STORES: OrderedDict[tuple, PersistentEntryStore] = OrderedDict()
_STORES_LOCK = threading.Lock()
_STORES_LIMIT = 32


def persistent_entry_store(
    directory: Path,
    *,
    max_entries: int,
    max_bytes: int,
) -> PersistentEntryStore:
    """Share one store per (directory, caps) so in-process memos survive
    across the short-lived service objects that use them."""
    key = (str(Path(directory).resolve(strict=False)),
           int(max_entries), int(max_bytes))
    with _STORES_LOCK:
        store = _STORES.get(key)
        if store is None:
            store = PersistentEntryStore(
                Path(directory), max_entries=max_entries, max_bytes=max_bytes,
            )
        _STORES[key] = store
        _STORES.move_to_end(key)
        while len(_STORES) > _STORES_LIMIT:
            _STORES.popitem(last=False)
        return store
