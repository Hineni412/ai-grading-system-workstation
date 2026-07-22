"""P3-03 public repository connection and transaction contracts."""

from __future__ import annotations

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


def test_row_mapping_and_repository_protocol_use_the_shared_session(tmp_path: Path) -> None:
    from backend.repositories import Repository, SQLiteConnectionFactory, map_row, map_rows

    @dataclass(frozen=True)
    class Item:
        item_id: int
        value: str

    class ItemRepository:
        def __init__(self, session: object) -> None:
            self._session = session

        @property
        def session(self) -> object:
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
