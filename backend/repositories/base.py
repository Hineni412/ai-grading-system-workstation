"""SQLite connection ownership and transaction primitives for repositories."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Protocol, TypeVar, runtime_checkable


class RepositoryError(RuntimeError):
    """Base error for repository infrastructure failures."""


class RepositoryConnectionError(RepositoryError):
    """A repository connection could not be opened or closed safely."""


class RepositoryTransactionError(RepositoryError):
    """A repository transaction boundary could not be completed safely."""


class RepositoryClosedError(RepositoryError):
    """A caller attempted to reuse a session after its owner closed it."""


class RepositoryThreadError(RepositoryError):
    """A caller attempted to use a session from a different thread."""


class ReadOnlyRepositoryError(RepositoryError):
    """A write-only transaction mode was requested from a read-only session."""


RowT = TypeVar("RowT", covariant=True)


class RowMapper(Protocol[RowT]):
    def __call__(self, row: sqlite3.Row) -> RowT: ...


@runtime_checkable
class Repository(Protocol):
    @property
    def session(self) -> "RepositorySession": ...


class RepositoryConnection:
    """Restricted SQL surface that keeps transaction ownership with the session."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.__connection = connection

    def execute(self, statement: str, parameters: Any = ()) -> sqlite3.Cursor:
        return self.__connection.execute(statement, parameters)

    def executemany(
        self,
        statement: str,
        parameters: Iterable[Any],
    ) -> sqlite3.Cursor:
        return self.__connection.executemany(statement, parameters)

    @property
    def total_changes(self) -> int:
        return int(self.__connection.total_changes)


class RepositorySession:
    """One thread-bound connection shared by repositories for one request/unit of work."""

    def __init__(self, connection: sqlite3.Connection, *, read_only: bool) -> None:
        self._connection = connection
        self._connection_view = RepositoryConnection(connection)
        self._read_only = bool(read_only)
        self._owner_thread = threading.get_ident()
        self._closed = False
        self._transaction_depth = 0
        self._savepoint_counter = 0
        self._outer_immediate = False

    @property
    def connection(self) -> RepositoryConnection:
        self._check_available()
        return self._connection_view

    @property
    def read_only(self) -> bool:
        return self._read_only

    @contextmanager
    def transaction(self, *, immediate: bool = False) -> Iterator["RepositorySession"]:
        self._check_available()
        if immediate and self._read_only:
            raise ReadOnlyRepositoryError("read-only sessions cannot start immediate transactions")
        outermost = self._transaction_depth == 0
        if immediate and not outermost and not self._outer_immediate:
            raise RepositoryTransactionError(
                "nested immediate transaction requires an immediate outer transaction"
            )
        savepoint: str | None = None
        try:
            if outermost:
                self._connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
                self._outer_immediate = bool(immediate)
            else:
                self._savepoint_counter += 1
                savepoint = f"repository_savepoint_{self._savepoint_counter}"
                self._connection.execute(f"SAVEPOINT {savepoint}")
        except sqlite3.Error as exc:
            raise RepositoryTransactionError("repository transaction could not start") from exc
        self._transaction_depth += 1
        try:
            yield self
        except BaseException as primary_error:
            try:
                self._rollback(outermost=outermost, savepoint=savepoint)
            except RepositoryTransactionError as rollback_error:
                raise primary_error from rollback_error
            raise
        else:
            self._commit(outermost=outermost, savepoint=savepoint)
        finally:
            self._transaction_depth -= 1
            if outermost:
                self._outer_immediate = False

    def _commit(self, *, outermost: bool, savepoint: str | None) -> None:
        try:
            if outermost:
                self._connection.commit()
            else:
                self._connection.execute(f"RELEASE SAVEPOINT {savepoint}")
        except sqlite3.Error as commit_error:
            try:
                self._connection.rollback()
            except sqlite3.Error as rollback_error:
                rollback_failure = RepositoryTransactionError(
                    "repository transaction could not roll back after commit failure"
                )
                rollback_failure.__cause__ = rollback_error
                failure = RepositoryTransactionError(
                    "repository transaction could not commit and its rollback also failed"
                )
                failure.add_note(
                    f"commit also failed with {type(commit_error).__name__}"
                )
                raise failure from rollback_failure
            raise RepositoryTransactionError(
                "repository transaction could not commit"
            ) from commit_error

    def _rollback(self, *, outermost: bool, savepoint: str | None) -> None:
        try:
            if outermost:
                self._connection.rollback()
            else:
                self._connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                self._connection.execute(f"RELEASE SAVEPOINT {savepoint}")
        except sqlite3.Error as exc:
            raise RepositoryTransactionError("repository transaction could not roll back") from exc

    def _check_available(self) -> None:
        if self._closed:
            raise RepositoryClosedError("repository session is closed")
        if threading.get_ident() != self._owner_thread:
            raise RepositoryThreadError("repository session belongs to another thread")

    def _close(self) -> None:
        self._check_available()
        rollback_error: sqlite3.Error | None = None
        close_error: sqlite3.Error | None = None
        try:
            if self._connection.in_transaction:
                try:
                    self._connection.rollback()
                except sqlite3.Error as exc:
                    rollback_error = exc
            try:
                self._connection.close()
            except sqlite3.Error as exc:
                close_error = exc
        finally:
            self._closed = True
        if rollback_error is not None:
            failure = RepositoryConnectionError(
                "repository transaction could not roll back before close"
            )
            if close_error is not None:
                failure.add_note(
                    f"repository connection close also failed with {type(close_error).__name__}"
                )
            raise failure from rollback_error
        if close_error is not None:
            raise RepositoryConnectionError(
                "repository connection could not close"
            ) from close_error


class SQLiteConnectionFactory:
    """Create owned SQLite sessions without pooling or module-level connections."""

    def __init__(self, database: Path, *, busy_timeout_ms: int = 5_000) -> None:
        self.database = Path(database)
        self.busy_timeout_ms = max(0, int(busy_timeout_ms))

    @contextmanager
    def session(self, *, read_only: bool = False) -> Iterator[RepositorySession]:
        connection = self._open(read_only=read_only)
        session = RepositorySession(connection, read_only=read_only)
        primary_error: BaseException | None = None
        try:
            yield session
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            try:
                session._close()
            except RepositoryConnectionError as cleanup_error:
                if primary_error is None:
                    raise
                primary_error.add_note(str(cleanup_error))
                for note in getattr(cleanup_error, "__notes__", ()):
                    primary_error.add_note(note)

    def _open(self, *, read_only: bool) -> sqlite3.Connection:
        connection: sqlite3.Connection | None = None
        try:
            if read_only:
                uri = f"{self.database.resolve().as_uri()}?mode=ro"
                connection = sqlite3.connect(
                    uri,
                    uri=True,
                    isolation_level="DEFERRED",
                    check_same_thread=True,
                )
            else:
                connection = sqlite3.connect(
                    self.database,
                    isolation_level="DEFERRED",
                    check_same_thread=True,
                )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
            if read_only:
                connection.execute("PRAGMA query_only = ON")
            return connection
        except (OSError, sqlite3.Error) as exc:
            failure = RepositoryConnectionError("repository database is unavailable")
            if connection is not None:
                try:
                    connection.close()
                except sqlite3.Error as close_error:
                    failure.add_note(
                        "repository connection close after initialization failure also failed "
                        f"with {type(close_error).__name__}"
                    )
            raise failure from exc


def map_row(mapper: RowMapper[RowT], row: sqlite3.Row | None) -> RowT | None:
    """Apply a typed mapper while preserving the common optional-row contract."""

    return None if row is None else mapper(row)


def map_rows(mapper: RowMapper[RowT], rows: list[sqlite3.Row]) -> list[RowT]:
    """Apply a typed mapper to a materialized SQLite row list."""

    return [mapper(row) for row in rows]
