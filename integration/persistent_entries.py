"""Per-entry durable store for derived results that outlive the process.

One small ``*.entry`` file per key under a controlled directory; the file
name is ``sha256(pickle(key))[:40]``. The envelope holds ``key``,
``change_stamp`` and either ``payload`` or a losslessly packed
``payload_zlib``. Compaction is opt-in; input metadata stays directly
readable for idle maintenance. A stored entry is used
only when its request key and change metadata match the caller's; every read or write
failure is silent and falls back to recomputation.
"""

from __future__ import annotations

import hashlib
import os
import pickle
import tempfile
import threading
import zlib
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import Any

_SNAPSHOT_LIMIT = 512
_SNAPSHOT_MAX_BYTES = 32 * 1024 * 1024
_SAVED_LIMIT = 2048


def _file_state(path: Path) -> tuple:
    info = path.stat()
    return (str(path), info.st_dev, info.st_ino, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


class PersistentEntryStore:
    """One-file-per-request store with bounds and silent failure.

    ``signature`` in ``get`` is either the expected value or a callable that
    receives the stored signature and returns the live expected signature (or
    ``None`` if the captured database no longer matches the live change metadata).
    """

    def __init__(self, directory: Path, *, max_entries: int, max_bytes: int,
                 compact: bool = False) -> None:
        self._dir = Path(directory)
        self._max_entries = int(max_entries)
        self._max_bytes = int(max_bytes)
        self._compact = compact
        self._lock = threading.Lock()
        # Parsed entry snapshots keyed by verified file identity, so repeated
        # reads of an unchanged file do not unpickle again.
        self._snapshots: OrderedDict[tuple, Any] = OrderedDict()
        self._snapshot_sizes: dict[tuple, int] = {}
        self._snapshot_bytes = 0
        self._snapshot_max_bytes = min(self._max_bytes, _SNAPSHOT_MAX_BYTES)
        # Verified on-disk state per entry file so a repeated put can skip
        # rewriting identical bytes without loading the file back.
        self._saved: OrderedDict[str, tuple] = OrderedDict()
        self._saved_bytes = 0

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
        size = file_state[3]
        if isinstance(snapshot, dict) and "payload_zlib" in snapshot:
            payload = zlib.decompress(snapshot.pop("payload_zlib"))
            size = max(size, len(payload))
            snapshot["payload"] = pickle.loads(payload)
        if _file_state(path) != file_state:
            return None  # The file changed under the read.
        with self._lock:
            if _file_state(path) != file_state:
                return None
            self._drop_snapshots(str(path))
            if size > self._snapshot_max_bytes:
                return snapshot
            self._snapshots[file_state] = snapshot
            self._snapshot_sizes[file_state] = size
            self._snapshot_bytes += size
            self._snapshots.move_to_end(file_state)
            while (len(self._snapshots) > _SNAPSHOT_LIMIT
                   or self._snapshot_bytes > self._snapshot_max_bytes):
                oldest, _ = self._snapshots.popitem(last=False)
                self._snapshot_bytes -= self._snapshot_sizes.pop(oldest)
        return snapshot

    def _drop_snapshots(self, path: str) -> None:
        """Caller holds the store lock; discard revisions of one file."""
        for state in tuple(self._snapshots):
            if state[0] == path:
                self._snapshots.pop(state)
                self._snapshot_bytes -= self._snapshot_sizes.pop(state)

    def get(self, key: Any, signature: Any | Callable[[Any], Any]) -> Any:
        """Return the stored payload, or ``None`` on any mismatch or error."""
        try:
            path = self.entry_path(key)
            snapshot = self._snapshot(path)
            if (snapshot is None or not isinstance(snapshot, dict)
                    or snapshot.get("key") != key):
                return None
            stored_signature = snapshot.get("change_stamp", snapshot.get("signature"))
            expected = (signature(stored_signature)
                        if callable(signature) else signature)
            if expected is None or stored_signature != expected:
                return None
            return snapshot.get("payload")
        except (OSError, pickle.PickleError, EOFError, KeyError, TypeError,
                ValueError, AttributeError, ImportError, zlib.error):
            return None

    def put(self, key: Any, signature: Any, payload: Any,
            *, _expected_state: tuple | None = None) -> None:
        path = self.entry_path(key)
        temporary: Path | None = None
        try:
            envelope = {"key": key, "change_stamp": signature, "payload": payload}
            if self._compact:
                envelope["storage_version"] = 1
            raw = pickle.dumps(envelope, pickle.HIGHEST_PROTOCOL)
            with self._lock:
                memo = self._saved.get(str(path))
                if memo is not None:
                    try:
                        current = _file_state(path)
                    except OSError:
                        current = None
                    if current == memo[0] and signature == memo[1] and raw == memo[2]:
                        self._saved.move_to_end(str(path))
                        return
            # Do this once on a changed write, outside the lock. Hot reads
            # retain the original payload bytes and never decompress again.
            data = raw
            if self._compact and len(raw) >= 4096:
                packed = pickle.dumps(payload, pickle.HIGHEST_PROTOCOL)
                candidate = pickle.dumps(
                    {"key": key, "change_stamp": signature, "storage_version": 1,
                     "payload_zlib": zlib.compress(packed, level=1)},
                    pickle.HIGHEST_PROTOCOL,
                )
                if len(candidate) * 10 <= len(raw) * 9:
                    data = candidate
            with self._lock:
                if _expected_state is not None and _file_state(path) != _expected_state:
                    return  # A request replaced this entry during maintenance.
                self._dir.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(dir=self._dir, delete=False) as saved:
                    temporary = Path(saved.name)
                    saved.write(data)
                os.replace(temporary, path)
                self._drop_snapshots(str(path))
                try:
                    file_state = _file_state(path)
                    previous = self._saved.pop(str(path), None)
                    if previous is not None:
                        self._saved_bytes -= len(previous[2])
                    if len(raw) <= self._snapshot_max_bytes:
                        self._saved[str(path)] = (file_state, signature, raw)
                        self._saved_bytes += len(raw)
                    while (len(self._saved) > _SAVED_LIMIT
                           or self._saved_bytes > self._snapshot_max_bytes):
                        self._saved_bytes -= len(self._saved.popitem(last=False)[1][2])
                except OSError:
                    removed = self._saved.pop(str(path), None)
                    if removed is not None:
                        self._saved_bytes -= len(removed[2])
                self._evict(keep=path)
        except (OSError, pickle.PickleError, zlib.error):
            pass  # Saving a derived cache must not fail the request.
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def maintain(self, is_current: Callable[[Any, Any], bool | None], *,
                 can_continue: Callable[[], bool] = lambda: True,
                 dry_run: bool = False) -> dict:
        """Reclaim obsolete derived entries, compact retained legacy files.

        None means the caller cannot establish validity; leave that entry
        alone. Inspect only the outer metadata, without decoding results.
        This runs in the idle worker, never on a foreground cache lookup.
        """
        result = {"removed_files": 0, "rewritten_files": 0,
                  "freed_bytes": 0, "completed": True}
        if not self._dir.exists():
            return result
        for path in self._dir.glob("*.entry"):
            if not can_continue():
                result["completed"] = False
                break
            try:
                state = _file_state(path)
                with path.open("rb") as saved:
                    snapshot = pickle.load(saved)
                if not isinstance(snapshot, dict) or "key" not in snapshot:
                    current = False
                else:
                    key = snapshot["key"]
                    signature = snapshot.get("change_stamp", snapshot.get("signature"))
                    current = is_current(key, signature)
                if current is False:
                    with self._lock:
                        if _file_state(path) != state:
                            continue
                        if not dry_run:
                            path.unlink()
                            self._drop_snapshots(str(path))
                            previous = self._saved.pop(str(path), None)
                            if previous is not None:
                                self._saved_bytes -= len(previous[2])
                    result["removed_files"] += 1
                    result["freed_bytes"] += state[3]
                elif (current is True and self._compact and not dry_run
                      and "payload" in snapshot and snapshot.get("storage_version") != 1):
                    if self.entry_path(key) != path:
                        continue
                    self.put(key, signature, snapshot["payload"], _expected_state=state)
                    after = path.stat().st_size
                    if after < state[3]:
                        result["rewritten_files"] += 1
                        result["freed_bytes"] += state[3] - after
            except (OSError, pickle.PickleError, EOFError, ValueError, TypeError,
                    AttributeError, ImportError):
                # An unreadable entry cannot establish its metadata. Retain
                # it for the existing size eviction instead of guessing.
                continue
        return result

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
                    self._drop_snapshots(str(item))
                    removed = self._saved.pop(str(item), None)
                    if removed is not None:
                        self._saved_bytes -= len(removed[2])
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
    compact: bool = False,
) -> PersistentEntryStore:
    """Share one store per (directory, caps) so in-process memos survive
    across the short-lived service objects that use them."""
    key = (str(Path(directory).resolve(strict=False)),
           int(max_entries), int(max_bytes), bool(compact))
    with _STORES_LOCK:
        store = _STORES.get(key)
        if store is None:
            store = PersistentEntryStore(
                Path(directory), max_entries=max_entries, max_bytes=max_bytes,
                compact=compact,
            )
        _STORES[key] = store
        _STORES.move_to_end(key)
        while len(_STORES) > _STORES_LIMIT:
            _STORES.popitem(last=False)
        return store
