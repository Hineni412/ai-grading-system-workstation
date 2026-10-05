from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace

import pytest

from backend.repositories.db_manager import DBManager
from question_bank.database.schema import initialize_database
from question_bank.services.question_read_service import (
    QuestionBankSnapshotUnavailable,
    captured_sqlite_read_connection,
)


def _request_paths(tmp_path: Path) -> SimpleNamespace:
    grading_path = tmp_path / "grading.db"
    question_bank_path = tmp_path / "question-bank.db"
    DBManager(grading_path).initialize()
    initialize_database(question_bank_path)
    return SimpleNamespace(
        db_path=grading_path,
        qb_db_path=question_bank_path,
    )


def _seed_request_boundary_tables(paths: SimpleNamespace) -> None:
    for db_path, value in (
        (paths.db_path, "grading-original"),
        (paths.qb_db_path, "question-bank-original"),
    ):
        with sqlite3.connect(db_path) as connection:
            connection.execute("CREATE TABLE request_boundary(value TEXT NOT NULL)")
            connection.execute(
                "INSERT INTO request_boundary(value) VALUES (?)",
                (value,),
            )


def test_request_snapshot_stays_stable_while_later_request_sees_legacy_commit(
    tmp_path: Path,
) -> None:
    import backend.api.read_connections as read_connections

    paths = _request_paths(tmp_path)
    _seed_request_boundary_tables(paths)

    with read_connections.request_read_context(paths) as current:
        assert (
            current.grading_connection.execute(
                "SELECT value FROM request_boundary"
            ).fetchone()["value"]
            == "grading-original"
        )

        writer = DBManager(paths.db_path)._connect()
        try:
            writer.execute(
                "UPDATE request_boundary SET value = ?",
                ("grading-committed",),
            )
            writer.commit()
        finally:
            writer.close()

        assert (
            current.grading_connection.execute(
                "SELECT value FROM request_boundary"
            ).fetchone()["value"]
            == "grading-original"
        )

    with read_connections.request_read_context(paths) as later:
        assert (
            later.grading_connection.execute(
                "SELECT value FROM request_boundary"
            ).fetchone()["value"]
            == "grading-committed"
        )
