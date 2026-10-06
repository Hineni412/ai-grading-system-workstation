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
            connection.execute("PRAGMA journal_mode=WAL")
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


@pytest.mark.parametrize("changed_database", ["db_path", "qb_db_path"])
def test_commit_during_capture_retries_and_binds_the_new_snapshot(
    tmp_path: Path, monkeypatch, changed_database: str,
) -> None:
    import backend.api.read_connections as reads

    paths = _request_paths(tmp_path)
    _seed_request_boundary_tables(paths)
    original = reads._direct_question_bank_read
    captured = []

    @contextmanager
    def change_once(*args, **kwargs):
        with original(*args, **kwargs) as connection:
            captured.append(connection)
            if len(captured) == 1:
                writer = sqlite3.connect(getattr(paths, changed_database))
                try:
                    writer.execute("UPDATE request_boundary SET value='committed-during-capture'")
                    writer.commit()
                finally:
                    writer.close()
            yield connection

    monkeypatch.setattr(reads, "_direct_question_bank_read", change_once)
    with reads.request_read_context(paths) as current:
        connection = (current.grading_connection if changed_database == "db_path"
                      else current.question_bank_connection)
        assert connection.execute("SELECT value FROM request_boundary").fetchone()[0] == "committed-during-capture"
        identity = current.diagnosis_service.cache_identity
    assert len(captured) == 2
    with pytest.raises(sqlite3.ProgrammingError):
        captured[0].execute("SELECT 1")
    with reads.request_read_context(paths) as later:
        assert later.diagnosis_service.cache_identity == identity


def test_repeated_commits_during_capture_are_bounded_and_close_connections(
    tmp_path: Path, monkeypatch,
) -> None:
    import backend.api.read_connections as reads
    from question_bank.services.question_read_service import QuestionBankSnapshotBusy

    paths = _request_paths(tmp_path)
    _seed_request_boundary_tables(paths)
    original = reads._direct_question_bank_read
    captured = []

    @contextmanager
    def always_change(*args, **kwargs):
        with original(*args, **kwargs) as connection:
            captured.append(connection)
            writer = sqlite3.connect(paths.db_path)
            try:
                writer.execute("UPDATE request_boundary SET value=?", (str(len(captured)),))
                writer.commit()
            finally:
                writer.close()
            yield connection

    monkeypatch.setattr(reads, "_direct_question_bank_read", always_change)
    with pytest.raises(QuestionBankSnapshotBusy):
        with reads.request_read_context(paths):
            pytest.fail("An unstable source must not publish a request context")
    assert len(captured) == 3
    for connection in captured:
        with pytest.raises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")


def test_commit_after_capture_keeps_the_captured_identity(tmp_path: Path, monkeypatch) -> None:
    import backend.api.read_connections as reads

    paths = _request_paths(tmp_path)
    _seed_request_boundary_tables(paths)
    original = reads.open_grading_repositories
    changed = False

    def change_after_check(*args, **kwargs):
        nonlocal changed
        if not changed:
            changed = True
            writer = sqlite3.connect(paths.db_path)
            try:
                writer.execute("UPDATE request_boundary SET value='committed-after-capture'")
                writer.commit()
            finally:
                writer.close()
        return original(*args, **kwargs)

    monkeypatch.setattr(reads, "open_grading_repositories", change_after_check)
    with reads.request_read_context(paths) as current:
        assert current.grading_connection.execute("SELECT value FROM request_boundary").fetchone()[0] == "grading-original"
        old_identity = current.diagnosis_service.cache_identity
    with reads.request_read_context(paths) as later:
        assert later.grading_connection.execute("SELECT value FROM request_boundary").fetchone()[0] == "committed-after-capture"
        assert later.diagnosis_service.cache_identity != old_identity
