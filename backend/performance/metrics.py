from __future__ import annotations

import math
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import Lock
from typing import Protocol


@dataclass(frozen=True, repr=False)
class RequestPerformanceRecord:
    request_id: str
    method: str
    route_template: str
    status_code: int
    elapsed_ms: float
    db_statements_total: int
    db_select_statements: int

    def __post_init__(self) -> None:
        if not math.isfinite(self.elapsed_ms) or self.elapsed_ms < 0:
            raise ValueError("elapsed_ms must be finite and nonnegative")

    def __repr__(self) -> str:
        return (
            "RequestPerformanceRecord("
            f"method={self.method!r}, "
            f"route_template={self.route_template!r}, "
            f"status_code={self.status_code!r}, "
            f"elapsed_ms={self.elapsed_ms!r}, "
            f"db_statements_total={self.db_statements_total!r}, "
            f"db_select_statements={self.db_select_statements!r}"
            ")"
        )


class PerformanceSink(Protocol):
    def record(self, record: RequestPerformanceRecord) -> None: ...


@dataclass
class RequestPerformanceRecorder:
    request_id: str
    _db_statements_total: int = field(default=0, init=False, repr=False)
    _db_select_statements: int = field(default=0, init=False, repr=False)
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def count_statement(self, statement: str) -> None:
        stripped = statement.lstrip()
        if not stripped:
            return
        first_token = stripped.split(maxsplit=1)[0].upper()
        with self._lock:
            self._db_statements_total += 1
            if first_token in {"SELECT", "WITH"}:
                self._db_select_statements += 1

    def finish(
        self,
        *,
        method: str,
        route_template: str,
        status_code: int,
        elapsed_ms: float,
    ) -> RequestPerformanceRecord:
        with self._lock:
            statements_total = self._db_statements_total
            select_statements = self._db_select_statements
        return RequestPerformanceRecord(
            request_id=self.request_id,
            method=method,
            route_template=route_template,
            status_code=status_code,
            elapsed_ms=elapsed_ms,
            db_statements_total=statements_total,
            db_select_statements=select_statements,
        )


_ACTIVE_RECORDER: ContextVar[RequestPerformanceRecorder | None] = ContextVar(
    "api_performance_recorder",
    default=None,
)


@contextmanager
def request_performance_scope(
    request_id: str,
) -> Iterator[RequestPerformanceRecorder]:
    recorder = RequestPerformanceRecorder(request_id=request_id)
    token = _ACTIVE_RECORDER.set(recorder)
    try:
        yield recorder
    finally:
        _ACTIVE_RECORDER.reset(token)


def instrument_sqlite_connection(
    connection: sqlite3.Connection,
) -> sqlite3.Connection:
    if _ACTIVE_RECORDER.get() is None:
        return connection

    def trace(statement: str) -> None:
        recorder = _ACTIVE_RECORDER.get()
        if recorder is not None:
            recorder.count_statement(statement)

    connection.set_trace_callback(trace)
    return connection


@dataclass
class InMemoryPerformanceSink:
    _records: dict[str, RequestPerformanceRecord] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def record(self, record: RequestPerformanceRecord) -> None:
        with self._lock:
            if record.request_id in self._records:
                raise ValueError("duplicate request_id")
            self._records[record.request_id] = record

    def pop(self, request_id: str) -> RequestPerformanceRecord:
        with self._lock:
            try:
                return self._records.pop(request_id)
            except KeyError:
                raise KeyError("missing request_id") from None
