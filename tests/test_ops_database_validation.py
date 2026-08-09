from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from backend.ops.database_validation import (
    OpsDatabaseInvalid,
    validate_database_file,
)
from backend.schema_migrations import ensure_schema_current


PROJECT_ROOT = Path(__file__).resolve().parents[1]
GRADING_MIGRATIONS = PROJECT_ROOT / "migrations" / "grading"


@pytest.mark.parametrize("history_damage", ["future", "checksum", "gap"])
def test_restore_database_validation_rejects_invalid_migration_history(
    tmp_path: Path,
    history_damage: str,
) -> None:
    database = tmp_path / "grading.db"
    ensure_schema_current(
        "grading",
        database,
        migrations_dir=GRADING_MIGRATIONS,
    )
    with sqlite3.connect(database) as connection:
        if history_damage == "future":
            connection.execute(
                "INSERT INTO schema_migrations "
                "(migration_name, checksum, success) "
                "VALUES ('999_future', 'future', 1)"
            )
        elif history_damage == "checksum":
            connection.execute(
                "UPDATE schema_migrations SET checksum='changed' "
                "WHERE migration_name='002_add_grading_run_ledger'"
            )
        else:
            connection.execute(
                "DELETE FROM schema_migrations "
                "WHERE migration_name='002_add_grading_run_ledger'"
            )

    with pytest.raises(OpsDatabaseInvalid):
        validate_database_file(
            database,
            "grading",
            migrations_dir=GRADING_MIGRATIONS,
        )


def test_restore_database_validation_requires_exact_schema(tmp_path: Path) -> None:
    database = tmp_path / "grading.db"
    ensure_schema_current(
        "grading",
        database,
        migrations_dir=GRADING_MIGRATIONS,
    )
    with sqlite3.connect(database) as connection:
        connection.execute("ALTER TABLE students ADD COLUMN unexpected TEXT")

    with pytest.raises(OpsDatabaseInvalid):
        validate_database_file(
            database,
            "grading",
            migrations_dir=GRADING_MIGRATIONS,
        )


def test_restore_database_validation_runs_foreign_key_check(tmp_path: Path) -> None:
    database = tmp_path / "grading.db"
    ensure_schema_current(
        "grading",
        database,
        migrations_dir=GRADING_MIGRATIONS,
    )
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute(
            "INSERT INTO teacher_score_locks ("
            "session_id, scan_batch_id, student_id, question_id, "
            "score_awarded, max_score, source_target_type, source_target_id"
            ") VALUES (999, 'batch', 999, 'Q1', 1, 1, 'synthetic', 1)"
        )

    with pytest.raises(OpsDatabaseInvalid):
        validate_database_file(
            database,
            "grading",
            migrations_dir=GRADING_MIGRATIONS,
        )
