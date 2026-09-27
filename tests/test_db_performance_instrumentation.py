from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest

from backend.performance.metrics import request_performance_scope
from db_manager import DBManager
from question_bank.database.schema import connect
from question_bank.services.question_read_service import (
    captured_sqlite_read_connection,
)
from update_tools import migrate_db


def measured_counts(action: Callable[[], None]) -> tuple[int, int]:
    with request_performance_scope("db-boundary") as recorder:
        action()
        record = recorder.finish(
            method="GET",
            route_template="/api/test",
            status_code=200,
            elapsed_ms=1.0,
        )
    return record.db_statements_total, record.db_select_statements


def test_db_manager_connect_counts_pragmas_and_select(tmp_path: Path) -> None:
    manager = DBManager(tmp_path / "grading.db")

    def action() -> None:
        conn = manager._connect()
        try:
            row = conn.execute("SELECT 1 AS value").fetchone()
            assert conn.row_factory is sqlite3.Row
            assert row["value"] == 1
        finally:
            conn.close()

    assert measured_counts(action) == (4, 1)


def test_question_bank_connect_counts_and_preserves_transactions(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "question-bank.db"
    with sqlite3.connect(db_path) as seed:
        seed.execute("CREATE TABLE items(value TEXT)")

    def commit_action() -> None:
        with connect(db_path) as conn:
            conn.execute("INSERT INTO items(value) VALUES ('committed')")
            row = conn.execute("SELECT value FROM items").fetchone()
            assert conn.row_factory is sqlite3.Row
            assert row["value"] == "committed"

    assert measured_counts(commit_action) == (7, 1)
    with sqlite3.connect(db_path) as check:
        assert check.execute("SELECT value FROM items").fetchall() == [("committed",)]

    def rollback_action() -> None:
        with pytest.raises(RuntimeError, match="force rollback"):
            with connect(db_path) as conn:
                conn.execute("INSERT INTO items(value) VALUES ('rolled-back')")
                conn.execute("SELECT value FROM items").fetchall()
                raise RuntimeError("force rollback")

    assert measured_counts(rollback_action) == (7, 1)
    with sqlite3.connect(db_path) as check:
        assert check.execute("SELECT value FROM items ORDER BY rowid").fetchall() == [
            ("committed",)
        ]


def test_captured_snapshot_counts_validation_and_service_select_without_source_writes(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "question-bank.db"
    writer = sqlite3.connect(db_path)
    try:
        assert writer.execute("PRAGMA journal_mode = WAL").fetchone()[0] == "wal"
        writer.execute("PRAGMA wal_autocheckpoint = 0")
        writer.execute("CREATE TABLE items(value TEXT)")
        writer.execute("INSERT INTO items(value) VALUES ('from-wal')")
        writer.commit()
        wal_path = Path(f"{db_path}-wal")
        assert wal_path.exists()
        source_before = (db_path.read_bytes(), wal_path.read_bytes())

        def action() -> None:
            with captured_sqlite_read_connection(
                db_path,
                required_tables=frozenset({"items"}),
            ) as conn:
                row = conn.execute("SELECT value FROM items").fetchone()
                assert conn.row_factory is sqlite3.Row
                assert row["value"] == "from-wal"

        assert measured_counts(action) == (7, 2)
        assert (db_path.read_bytes(), wal_path.read_bytes()) == source_before
    finally:
        writer.close()


def test_direct_read_connection_counts_select_and_rejects_writes(
    tmp_path: Path,
) -> None:
    from backend.api.read_connections import _direct_question_bank_read

    db_path = tmp_path / "question-bank.db"
    with sqlite3.connect(db_path) as seed:
        seed.execute("CREATE TABLE items(id INTEGER PRIMARY KEY)")
        seed.execute("INSERT INTO items(id) VALUES (1)")

    def action() -> None:
        with _direct_question_bank_read(
            db_path,
            required_tables=frozenset({"items"}),
        ) as conn:
            row = conn.execute("SELECT id FROM items").fetchone()
            assert conn.row_factory is sqlite3.Row
            assert row["id"] == 1

    assert measured_counts(action) == (5, 2)
    with _direct_question_bank_read(
        db_path,
        required_tables=frozenset({"items"}),
    ) as conn:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO items(id) VALUES (2)")


def test_migration_status_counts_schema_read_without_retaining_candidate_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "candidate.db"
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    migrations_dir.joinpath("001_demo.sql").write_text("SELECT 1;", encoding="utf-8")
    with sqlite3.connect(db_path) as seed:
        seed.execute(
            "CREATE TABLE schema_migrations ("
            "id INTEGER PRIMARY KEY, migration_name TEXT, applied_at TEXT, success INTEGER)"
        )
        seed.execute(
            "INSERT INTO schema_migrations "
            "(migration_name, applied_at, success) "
            "VALUES ('001_demo', '2026-07-13T00:00:00', 1)"
        )
    monkeypatch.setattr(
        migrate_db,
        "_get_targets",
        lambda: {
            "grading": {
                "db_path": tmp_path / "unused.db",
                "migrations_dir": migrations_dir,
            }
        },
    )

    with request_performance_scope("migration-status") as recorder:
        status = migrate_db.get_migration_status(
            "grading",
            db_path_override=db_path,
            migrations_dir_override=migrations_dir,
        )
        record = recorder.finish(
            method="GET",
            route_template="/api/test",
            status_code=200,
            elapsed_ms=1.0,
        )

    assert status["applied"] == ["001_demo"]
    assert (record.db_statements_total, record.db_select_statements) == (1, 1)
    assert str(db_path) not in repr(record)
