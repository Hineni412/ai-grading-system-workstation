"""Repository providers for instrumented owned and borrowed connections."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from backend.performance.metrics import instrument_sqlite_connection
from backend.repositories.base import (
    ReadOnlyRepositoryError,
    RepositorySession,
    SQLiteConnectionFactory,
)


class InstrumentedSQLiteConnectionFactory(SQLiteConnectionFactory):
    """Count repository SQL in the existing request performance log."""

    def _open(self, *, read_only: bool) -> sqlite3.Connection:
        connection = super()._open(read_only=read_only)
        return instrument_sqlite_connection(connection)


class _BorrowedReadOnlyRepositorySession(RepositorySession):
    @contextmanager
    def transaction(self, *, immediate: bool = False) -> Iterator[RepositorySession]:
        self._check_available()
        if immediate or not self.read_only:
            raise ReadOnlyRepositoryError("borrowed repository sessions are read-only")
        yield self


class BorrowedReadOnlySessionProvider:
    """Expose a request snapshot without owning its transaction or lifecycle."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    @contextmanager
    def session(self, *, read_only: bool = False) -> Iterator[RepositorySession]:
        if not read_only:
            raise ReadOnlyRepositoryError("borrowed repository sessions are read-only")
        yield _BorrowedReadOnlyRepositorySession(
            self._connection,
            read_only=True,
        )
