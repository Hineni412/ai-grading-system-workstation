"""Serve the last display result while a fresh one computes in the background.

Slow read endpoints hand their final JSON bytes and source version to
``serve_with_refresh``. An unchanged version reuses the saved result. A changed
version waits briefly for the fresh bytes, then serves the previous bytes if
the single-flight compute is still running. Without saved bytes the caller waits for the
first result. Compute errors propagate on that synchronous path; once a stale
result was served a late failure is logged and dropped.

Last results live in ``reports/.training_diagnosis/last_display`` as one
gzip-compressed ``.entry`` file per (endpoint, request key) pair, capped at
128 entries / 256 MiB by the shared persistent-entry store. Write endpoints
never go through this path.
"""

from __future__ import annotations

import gzip
import json
import logging
import time
import threading
from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError, wait
from pathlib import Path
from typing import Any

from integration.persistent_entries import persistent_entry_store

_LOG = logging.getLogger(__name__)

_LAST_SIGNATURE = "last"
_MAX_ENTRIES = 128
_MAX_BYTES = 256 * 1024 * 1024

_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="display-refresh")


class _Flight:
    """One single-flight compute; ``served_last`` marks a stale serve."""

    __slots__ = ("future", "served_last", "scope")

    def __init__(self, future: Future, scope: str = '') -> None:
        self.future = future
        self.served_last = False
        self.scope = scope


_FLIGHTS: dict[tuple[str, str, str], _Flight] = {}
_FLIGHTS_LOCK = threading.RLock()


def _store(data_root: Path):
    return persistent_entry_store(
        Path(data_root) / "reports" / ".training_diagnosis" / "last_display",
        max_entries=_MAX_ENTRIES,
        max_bytes=_MAX_BYTES,
    )


def _compute_and_store(
    compute: Callable[[], bytes],
    store,
    key: tuple[str, str],
    version: object = None,
    source_version: Callable[[], object] | None = None,
) -> bytes:
    result = compute()
    current = version if source_version is not None and source_version() == version else None
    store.put(key, _LAST_SIGNATURE, (current, gzip.compress(result)))
    return result


def _complete(flight_key: tuple[str, str, str], flight: _Flight):
    def done(future: Future) -> None:
        with _FLIGHTS_LOCK:
            if _FLIGHTS.get(flight_key) is flight:
                _FLIGHTS.pop(flight_key, None)
            served_last = flight.served_last
        if served_last and not future.cancelled():
            error = future.exception()
            if error is not None:
                _LOG.warning(
                    "background refresh for %s failed: %r",
                    flight_key[0],
                    error,
                )

    return done


def busy() -> bool:
    """True while any display-refresh compute is queued or running."""

    with _FLIGHTS_LOCK:
        return any(not flight.future.done() for flight in _FLIGHTS.values())


def wait_idle(timeout: float = 30.0) -> bool:
    """Wait until no display-refresh compute is queued or running."""

    deadline = time.monotonic() + timeout
    while True:
        with _FLIGHTS_LOCK:
            pending = [
                flight.future
                for flight in _FLIGHTS.values()
                if not flight.future.done()
            ]
        if not pending:
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        wait(pending, timeout=min(0.1, remaining))


def serve_with_refresh(
    *,
    data_root: Path,
    endpoint: str,
    request_key: Mapping[str, Any],
    compute: Callable[[], bytes],
    wait_seconds: float = 1.0,
    source_version: Callable[[], object] | None = None,
) -> tuple[bytes, bool]:
    """Return ``(response_bytes, refreshing)`` for one display request.

    ``compute`` must be a standalone callable: it opens its own read
    context/services from paths and returns the exact JSON bytes the route
    returns today, because it may outlive the HTTP request.
    """

    data_root = Path(data_root)
    store = _store(data_root)
    request_json = json.dumps(
        dict(request_key), sort_keys=True, ensure_ascii=False, default=str
    )
    key = (endpoint, request_json)
    flight_key = (endpoint, request_json, str(data_root.resolve(strict=False)))
    version = source_version() if source_version is not None else None
    saved = store.get(key, _LAST_SIGNATURE)
    saved_version = None
    if isinstance(saved, tuple) and len(saved) == 2:
        saved_version, saved = saved
    if saved is not None and version is not None and saved_version == version:
        return gzip.decompress(saved), False
    scope = json.dumps({name: request_key[name] for name in
                        ('scope', 'exam_scope', 'curriculum_volume_id', 'class_ids') if name in request_key},
                       sort_keys=True, ensure_ascii=False, default=str)
    created = False
    with _FLIGHTS_LOCK:
        for other_key, other in tuple(_FLIGHTS.items()):
            if (other_key[0] == endpoint and other_key[2] == flight_key[2]
                    and other.scope != scope and not other.future.running()):
                # Cancellation invokes the existing completion callback.
                other.future.cancel()
        flight = _FLIGHTS.get(flight_key)
        if flight is None:
            flight = _Flight(
                _EXECUTOR.submit(_compute_and_store, compute, store, key, version, source_version), scope
            )
            _FLIGHTS[flight_key] = flight
            created = True
    if created:
        flight.future.add_done_callback(_complete(flight_key, flight))
    if saved is not None:
        with _FLIGHTS_LOCK:
            flight.served_last = True
        # Fast recomputations should complete this request, instead of returning
        # old bytes and waiting through the client's polling backoff.
        done, _ = wait((flight.future,), timeout=wait_seconds)
        if done and not flight.future.cancelled() and flight.future.exception() is None:
            return flight.future.result(), source_version is not None and source_version() != version
        return gzip.decompress(saved), True
    try:
        result = flight.future.result(timeout=wait_seconds)
        return result, source_version is not None and source_version() != version
    except TimeoutError:
        pass
    result = flight.future.result()
    return result, source_version is not None and source_version() != version


def json_bytes(payload: Any) -> bytes:
    """Encode a payload exactly like FastAPI's JSONResponse."""

    from fastapi.encoders import jsonable_encoder

    return json.dumps(
        jsonable_encoder(payload),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


__all__ = [
    "busy",
    "json_bytes",
    "serve_with_refresh",
    "wait_idle",
]
