"""Serve the last display result while a fresh one computes in the background.

Slow read endpoints hand their final JSON bytes to ``serve_with_refresh``.
Each request starts (or joins) a single-flight compute on a small shared pool.
When the compute finishes inside ``wait_seconds`` its bytes are served and
saved as the last result for that request key; otherwise the caller receives
the previously saved bytes marked as refreshing while the compute continues
and stores the fresh result for the next request. Compute errors propagate on
the synchronous path exactly as before; once a stale result was served a late
failure is logged and dropped.

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

    __slots__ = ("future", "served_last")

    def __init__(self, future: Future) -> None:
        self.future = future
        self.served_last = False


_FLIGHTS: dict[tuple[str, str, str], _Flight] = {}
_FLIGHTS_LOCK = threading.Lock()


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
) -> bytes:
    result = compute()
    store.put(key, _LAST_SIGNATURE, gzip.compress(result))
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
    with _FLIGHTS_LOCK:
        flight = _FLIGHTS.get(flight_key)
        if flight is None:
            flight = _Flight(
                _EXECUTOR.submit(_compute_and_store, compute, store, key)
            )
            flight.future.add_done_callback(_complete(flight_key, flight))
            _FLIGHTS[flight_key] = flight
    try:
        return flight.future.result(timeout=wait_seconds), False
    except TimeoutError:
        pass
    saved = store.get(key, _LAST_SIGNATURE)
    if saved is None:
        return flight.future.result(), False
    with _FLIGHTS_LOCK:
        flight.served_last = True
    if flight.future.done():
        # Finished between the wait timeout and the flag; the done callback
        # already ran, so surface the failure here instead.
        error = flight.future.exception()
        if error is not None:
            _LOG.warning("background refresh for %s failed: %r", endpoint, error)
    return gzip.decompress(saved), True


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
