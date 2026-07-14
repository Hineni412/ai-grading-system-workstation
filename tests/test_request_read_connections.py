from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from db_manager import DBManager
from question_bank.database.schema import connect
from question_bank.services.question_read_service import (
    captured_sqlite_read_connection,
)


def test_db_manager_borrowed_connection_reuses_identity_without_closing(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "grading.db"
    external = sqlite3.connect(db_path)
    try:
        external.execute("CREATE TEMP TABLE request_identity(value TEXT)")
        external.execute(
            "INSERT INTO request_identity(value) VALUES ('same-connection')"
        )
        manager = DBManager(db_path, external_connection=external)

        borrowed = manager._connect()
        assert borrowed.execute(
            "SELECT value FROM request_identity"
        ).fetchone()[0] == "same-connection"

        borrowed.close()
        assert external.execute("SELECT 1").fetchone()[0] == 1
    finally:
        external.close()


def test_db_manager_nested_context_does_not_commit_or_end_request_snapshot(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "grading.db"
    external = sqlite3.connect(db_path, isolation_level=None)
    try:
        external.execute("CREATE TABLE items(value TEXT)")
        external.execute("BEGIN")
        manager = DBManager(db_path, external_connection=external)

        with manager._connect() as outer:
            with manager._connect() as inner:
                assert outer.execute("SELECT 1").fetchone()[0] == 1
                assert inner.execute("SELECT 1").fetchone()[0] == 1
                inner.commit()
                inner.rollback()
                inner.close()
                assert external.in_transaction is True

        assert external.in_transaction is True
        external.rollback()
    finally:
        external.close()


def test_question_bank_connect_borrowed_connection_does_not_own_lifecycle(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "question-bank.db"
    external = sqlite3.connect(db_path)
    try:
        external.execute("CREATE TABLE items(value TEXT)")
        external.commit()
        external.execute("INSERT INTO items(value) VALUES ('normal-exit')")

        with connect(db_path, external_connection=external) as borrowed:
            assert borrowed is external

        assert external.in_transaction is True
        with pytest.raises(RuntimeError, match="borrowed failure"):
            with connect(db_path, external_connection=external) as borrowed:
                borrowed.execute(
                    "INSERT INTO items(value) VALUES ('exception-exit')"
                )
                raise RuntimeError("borrowed failure")

        assert external.in_transaction is True
        assert external.execute(
            "SELECT value FROM items ORDER BY rowid"
        ).fetchall() == [("normal-exit",), ("exception-exit",)]
        external.rollback()
    finally:
        external.close()

    with sqlite3.connect(db_path) as check:
        assert check.execute("SELECT value FROM items").fetchall() == []


def test_snapshot_connection_allows_cross_worker_teardown_but_rejects_writes(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "question-bank.db"
    with sqlite3.connect(db_path) as seed:
        seed.execute("CREATE TABLE items(value TEXT)")
        seed.execute("INSERT INTO items(value) VALUES ('snapshot')")

    context = captured_sqlite_read_connection(
        db_path,
        required_tables=frozenset({"items"}),
        check_same_thread=False,
    )
    connection = context.__enter__()
    manager = DBManager(db_path, external_connection=connection)
    assert connection.in_transaction is True
    assert manager._connect().execute(
        "SELECT value FROM items"
    ).fetchone()["value"] == "snapshot"
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        manager._connect().execute("INSERT INTO items(value) VALUES ('blocked')")

    with ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(context.__exit__, None, None, None).result()

    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")


def test_legacy_connections_keep_existing_pragmas_and_close_behavior(
    tmp_path: Path,
) -> None:
    grading_path = tmp_path / "grading.db"
    grading_connection = DBManager(grading_path)._connect()
    assert grading_connection.row_factory is sqlite3.Row
    assert grading_connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert grading_connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    assert grading_connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    grading_connection.close()
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        grading_connection.execute("SELECT 1")

    question_bank_path = tmp_path / "question-bank.db"
    with connect(question_bank_path) as question_bank_connection:
        assert question_bank_connection.row_factory is sqlite3.Row
        assert question_bank_connection.execute(
            "PRAGMA foreign_keys"
        ).fetchone()[0] == 1
        assert question_bank_connection.execute(
            "PRAGMA busy_timeout"
        ).fetchone()[0] == 5000
        assert question_bank_connection.execute(
            "PRAGMA journal_mode"
        ).fetchone()[0] == "wal"

    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        question_bank_connection.execute("SELECT 1")
