"""P3-03 public repository connection and transaction contracts."""

from __future__ import annotations

import ast
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pytest


def _create_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")


def test_write_session_commits_explicit_transaction_and_closes_owned_connection(
    tmp_path: Path,
) -> None:
    from backend.repositories import RepositoryClosedError, SQLiteConnectionFactory

    database = tmp_path / "repository.db"
    _create_database(database)
    factory = SQLiteConnectionFactory(database)

    with factory.session() as session:
        with session.transaction():
            session.connection.execute("INSERT INTO items(value) VALUES (?)", ("committed",))

    with sqlite3.connect(database) as verification:
        assert verification.execute("SELECT value FROM items").fetchall() == [("committed",)]
    with pytest.raises(RepositoryClosedError):
        _ = session.connection


def test_outer_transaction_rolls_back_on_base_exception(tmp_path: Path) -> None:
    from backend.repositories import SQLiteConnectionFactory

    class Cancelled(BaseException):
        pass

    database = tmp_path / "repository.db"
    _create_database(database)
    factory = SQLiteConnectionFactory(database)

    with pytest.raises(Cancelled):
        with factory.session() as session:
            with session.transaction():
                session.connection.execute("INSERT INTO items(value) VALUES ('partial')")
                raise Cancelled()

    with sqlite3.connect(database) as verification:
        assert verification.execute("SELECT value FROM items").fetchall() == []


def test_nested_transaction_rolls_back_savepoint_and_outer_transaction_continues(
    tmp_path: Path,
) -> None:
    from backend.repositories import SQLiteConnectionFactory

    database = tmp_path / "repository.db"
    _create_database(database)
    factory = SQLiteConnectionFactory(database)

    with factory.session() as session:
        with session.transaction():
            session.connection.execute("INSERT INTO items(value) VALUES ('outer-before')")
            with pytest.raises(ValueError):
                with session.transaction():
                    session.connection.execute("INSERT INTO items(value) VALUES ('inner')")
                    raise ValueError("reject nested work")
            session.connection.execute("INSERT INTO items(value) VALUES ('outer-after')")

    with sqlite3.connect(database) as verification:
        assert verification.execute("SELECT value FROM items ORDER BY id").fetchall() == [
            ("outer-before",),
            ("outer-after",),
        ]


def test_read_only_session_rejects_writes_and_missing_database_is_not_created(
    tmp_path: Path,
) -> None:
    from backend.repositories import RepositoryConnectionError, SQLiteConnectionFactory

    database = tmp_path / "repository.db"
    _create_database(database)
    factory = SQLiteConnectionFactory(database)

    with factory.session(read_only=True) as session:
        assert session.connection.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0
        with pytest.raises(sqlite3.OperationalError):
            with session.transaction():
                session.connection.execute("INSERT INTO items(value) VALUES ('blocked')")

    missing = tmp_path / "missing.db"
    with pytest.raises(RepositoryConnectionError):
        with SQLiteConnectionFactory(missing).session(read_only=True):
            pass
    assert not missing.exists()


def test_write_outside_explicit_transaction_is_rolled_back_when_session_closes(
    tmp_path: Path,
) -> None:
    from backend.repositories import SQLiteConnectionFactory

    database = tmp_path / "repository.db"
    _create_database(database)
    factory = SQLiteConnectionFactory(database)

    with factory.session() as session:
        session.connection.execute("INSERT INTO items(value) VALUES ('uncommitted')")

    with sqlite3.connect(database) as verification:
        assert verification.execute("SELECT value FROM items").fetchall() == []


def test_repository_connection_does_not_expose_transaction_lifecycle_methods(
    tmp_path: Path,
) -> None:
    from backend.repositories import SQLiteConnectionFactory

    database = tmp_path / "repository.db"
    _create_database(database)

    with SQLiteConnectionFactory(database).session() as session:
        session.connection.execute("INSERT INTO items(value) VALUES ('uncommitted')")
        for method_name in ("commit", "rollback", "close", "executescript"):
            assert not hasattr(session.connection, method_name)

    with sqlite3.connect(database) as verification:
        assert verification.execute("SELECT value FROM items").fetchall() == []


def test_repository_sql_and_cursor_cannot_bypass_session_transaction(
    tmp_path: Path,
) -> None:
    from backend.repositories import RepositoryTransactionError, SQLiteConnectionFactory

    database = tmp_path / "repository.db"
    _create_database(database)

    with pytest.raises(ValueError, match="abort outer transaction"):
        with SQLiteConnectionFactory(database).session() as session:
            with session.transaction():
                session.connection.execute("INSERT INTO items(value) VALUES ('partial')")
                for statement in (
                    "COMMIT",
                    "\ufeffCOMMIT",
                    "  END",
                    "-- repository comment\nROLLBACK",
                    "/* repository comment */ SAVEPOINT caller_owned",
                ):
                    with pytest.raises(RepositoryTransactionError, match="transaction control"):
                        session.connection.execute(statement)
                cursor = session.connection.execute("SELECT COUNT(*) FROM items")
                assert cursor.fetchone()[0] == 1
                assert not hasattr(cursor, "connection")
                raise ValueError("abort outer transaction")

    with sqlite3.connect(database) as verification:
        assert verification.execute("SELECT value FROM items").fetchall() == []


def test_sessions_are_thread_bound_and_factory_creates_distinct_thread_connections(
    tmp_path: Path,
) -> None:
    from backend.repositories import RepositoryThreadError, SQLiteConnectionFactory

    database = tmp_path / "repository.db"
    _create_database(database)
    factory = SQLiteConnectionFactory(database)
    errors: list[type[BaseException]] = []
    with factory.session(read_only=True) as main_session:
        thread = threading.Thread(
            target=lambda: errors.append(_connection_error_type(main_session)),
        )
        thread.start()
        thread.join()
    assert errors == [RepositoryThreadError]

    barrier = threading.Barrier(2)

    def connection_identity() -> int:
        with factory.session(read_only=True) as session:
            barrier.wait(timeout=5)
            session.connection.execute("SELECT COUNT(*) FROM items").fetchone()
            return id(session.connection)

    with ThreadPoolExecutor(max_workers=2) as executor:
        identities = list(executor.map(lambda _: connection_identity(), range(2)))
    assert len(set(identities)) == 2


def test_locked_immediate_transaction_fails_without_automatic_retry(tmp_path: Path) -> None:
    from backend.repositories import RepositoryTransactionError, SQLiteConnectionFactory

    database = tmp_path / "repository.db"
    _create_database(database)
    first_factory = SQLiteConnectionFactory(database, busy_timeout_ms=1)
    second_factory = SQLiteConnectionFactory(database, busy_timeout_ms=1)

    with first_factory.session() as first, second_factory.session() as second:
        with first.transaction(immediate=True):
            first.connection.execute("INSERT INTO items(value) VALUES ('winner')")
            with pytest.raises(RepositoryTransactionError):
                with second.transaction(immediate=True):
                    pass

    with sqlite3.connect(database) as verification:
        assert verification.execute("SELECT value FROM items").fetchall() == [("winner",)]


def test_nested_immediate_requires_an_immediate_outer_transaction(tmp_path: Path) -> None:
    from backend.repositories import RepositoryTransactionError, SQLiteConnectionFactory

    database = tmp_path / "repository.db"
    _create_database(database)
    factory = SQLiteConnectionFactory(database)

    with factory.session() as session:
        with session.transaction():
            with pytest.raises(RepositoryTransactionError, match="immediate"):
                with session.transaction(immediate=True):
                    pass
            session.connection.execute("INSERT INTO items(value) VALUES ('outer')")

    with factory.session() as session:
        with session.transaction(immediate=True):
            with session.transaction(immediate=True):
                session.connection.execute("INSERT INTO items(value) VALUES ('nested')")

    with sqlite3.connect(database) as verification:
        assert verification.execute("SELECT value FROM items ORDER BY id").fetchall() == [
            ("outer",),
            ("nested",),
        ]


def test_row_mapping_and_repository_protocol_use_the_shared_session(tmp_path: Path) -> None:
    from backend.repositories import (
        Repository,
        RepositorySession,
        SQLiteConnectionFactory,
        map_row,
        map_rows,
    )

    @dataclass(frozen=True)
    class Item:
        item_id: int
        value: str

    class ItemRepository:
        def __init__(self, session: RepositorySession) -> None:
            self._session = session

        @property
        def session(self) -> RepositorySession:
            return self._session

    def mapper(row: sqlite3.Row) -> Item:
        return Item(int(row["id"]), str(row["value"]))

    database = tmp_path / "repository.db"
    _create_database(database)
    with sqlite3.connect(database) as seed:
        seed.executemany("INSERT INTO items(value) VALUES (?)", [("one",), ("two",)])
    factory = SQLiteConnectionFactory(database)

    with factory.session(read_only=True) as session:
        repository = ItemRepository(session)
        rows = session.connection.execute("SELECT id, value FROM items ORDER BY id").fetchall()
        assert isinstance(repository, Repository)
        assert map_row(mapper, rows[0]) == Item(1, "one")
        assert map_row(mapper, None) is None
        assert map_rows(mapper, rows) == [Item(1, "one"), Item(2, "two")]


def _connection_error_type(session: object) -> type[BaseException]:
    try:
        _ = session.connection  # type: ignore[attr-defined]
    except BaseException as exc:
        return type(exc)
    raise AssertionError("cross-thread session access unexpectedly succeeded")


def test_rollback_failure_preserves_the_original_cancellation() -> None:
    from backend.repositories import RepositorySession, RepositoryTransactionError

    class Cancelled(BaseException):
        pass

    class BrokenRollbackConnection:
        def execute(self, _statement: str) -> None:
            return None

        def rollback(self) -> None:
            raise sqlite3.OperationalError("rollback failed")

    session = RepositorySession(BrokenRollbackConnection(), read_only=False)  # type: ignore[arg-type]

    with pytest.raises(Cancelled) as raised:
        with session.transaction():
            raise Cancelled()
    assert isinstance(raised.value.__cause__, RepositoryTransactionError)


def test_close_failure_does_not_mask_the_original_session_error(tmp_path: Path) -> None:
    from backend.repositories import SQLiteConnectionFactory

    class Cancelled(BaseException):
        pass

    class BrokenCloseConnection:
        in_transaction = False

        def close(self) -> None:
            raise sqlite3.OperationalError("close failed")

    class BrokenCloseFactory(SQLiteConnectionFactory):
        def _open(self, *, read_only: bool) -> BrokenCloseConnection:
            return BrokenCloseConnection()

    factory = BrokenCloseFactory(tmp_path / "unused.db")
    with pytest.raises(Cancelled) as raised:
        with factory.session():
            raise Cancelled()
    assert any("could not close" in note for note in raised.value.__notes__)


def test_cleanup_attempts_close_after_rollback_failure_and_reports_both_failures(
    tmp_path: Path,
) -> None:
    from backend.repositories import RepositoryConnectionError, SQLiteConnectionFactory

    class BrokenCleanupConnection:
        in_transaction = True

        def __init__(self) -> None:
            self.close_attempted = False

        def rollback(self) -> None:
            raise sqlite3.OperationalError("rollback failed")

        def close(self) -> None:
            self.close_attempted = True
            raise sqlite3.OperationalError("close failed")

    connection = BrokenCleanupConnection()

    class BrokenCleanupFactory(SQLiteConnectionFactory):
        def _open(self, *, read_only: bool) -> BrokenCleanupConnection:
            return connection

    with pytest.raises(RepositoryConnectionError) as raised:
        with BrokenCleanupFactory(tmp_path / "unused.db").session():
            pass
    assert connection.close_attempted is True
    assert isinstance(raised.value.__cause__, sqlite3.OperationalError)
    assert any("close also failed" in note for note in raised.value.__notes__)


def test_cleanup_failure_is_attached_without_masking_original_error(tmp_path: Path) -> None:
    from backend.repositories import SQLiteConnectionFactory

    class Cancelled(BaseException):
        pass

    class BrokenCleanupConnection:
        in_transaction = True

        def rollback(self) -> None:
            raise sqlite3.OperationalError("rollback failed")

        def close(self) -> None:
            raise sqlite3.OperationalError("close failed")

    class BrokenCleanupFactory(SQLiteConnectionFactory):
        def _open(self, *, read_only: bool) -> BrokenCleanupConnection:
            return BrokenCleanupConnection()

    with pytest.raises(Cancelled) as raised:
        with BrokenCleanupFactory(tmp_path / "unused.db").session():
            raise Cancelled()
    assert any("roll back before close" in note for note in raised.value.__notes__)
    assert any("close also failed" in note for note in raised.value.__notes__)


def test_commit_and_rollback_failures_are_both_preserved() -> None:
    from backend.repositories import RepositorySession, RepositoryTransactionError

    class BrokenCommitConnection:
        def __init__(self) -> None:
            self.commit_error = sqlite3.OperationalError("commit failed: disk full")
            self.rollback_error = sqlite3.OperationalError("rollback failed: I/O error")

        def execute(self, _statement: str) -> None:
            return None

        def commit(self) -> None:
            raise self.commit_error

        def rollback(self) -> None:
            raise self.rollback_error

    connection = BrokenCommitConnection()
    session = RepositorySession(connection, read_only=False)  # type: ignore[arg-type]
    with pytest.raises(RepositoryTransactionError) as raised:
        with session.transaction():
            pass
    assert isinstance(raised.value.__cause__, ExceptionGroup)
    assert raised.value.__cause__.exceptions == (
        connection.commit_error,
        connection.rollback_error,
    )


def test_connection_initialization_failure_closes_the_opened_connection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.repositories import RepositoryConnectionError, SQLiteConnectionFactory
    from backend.repositories import base as base_module

    class BrokenPragmaConnection:
        row_factory = None

        def __init__(self) -> None:
            self.closed = False

        def execute(self, _statement: str) -> None:
            raise sqlite3.OperationalError("pragma failed")

        def close(self) -> None:
            self.closed = True

    connection = BrokenPragmaConnection()
    monkeypatch.setattr(base_module.sqlite3, "connect", lambda *_args, **_kwargs: connection)

    with pytest.raises(RepositoryConnectionError):
        SQLiteConnectionFactory(tmp_path / "repository.db")._open(read_only=False)
    assert connection.closed is True


def test_repository_module_has_no_module_level_sqlite_connection() -> None:
    from backend.repositories import base as base_module

    module = ast.parse(Path(base_module.__file__).read_text(encoding="utf-8"))
    top_level_calls = [
        node
        for statement in module.body
        for node in ast.walk(statement)
        if not isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and isinstance(node, ast.Call)
    ]
    assert not any(
        isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "sqlite3"
        and call.func.attr == "connect"
        for call in top_level_calls
    )
