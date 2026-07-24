from __future__ import annotations

import ast
import json
import re
import shutil
import sqlite3
from pathlib import Path

import pytest

from update_tools.migrate_db import run_migrations


PROJECT_ROOT = Path(__file__).resolve().parents[1]
GRADING_MIGRATIONS = PROJECT_ROOT / "migrations" / "grading"


def _migrations_through(tmp_path: Path, maximum_order: int) -> Path:
    selected = tmp_path / f"migrations-through-{maximum_order:03d}"
    selected.mkdir()
    for source in sorted(GRADING_MIGRATIONS.glob("*.sql")):
        if int(source.name.split("_", 1)[0]) <= maximum_order:
            shutil.copy2(source, selected / source.name)
    return selected


def _bootstrap_through_006(tmp_path: Path) -> Path:
    database = tmp_path / "grading.db"
    report = run_migrations(
        "grading",
        db_path=database,
        migrations_dir=_migrations_through(tmp_path, 6),
    )
    assert report.error is None, report.error
    return database


def _seed_details(database: Path) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO students (id, student_code, name) VALUES (1, 'S1', 'student')"
        )
        connection.execute(
            """
            INSERT INTO grading_sessions (
                id, session_name, rubric_path, answer_key_path, status
            ) VALUES (2, 'session', 'rubric', 'answer', 'completed')
            """
        )
        connection.execute(
            """
            INSERT INTO exam_papers (
                id, session_id, front_image, back_image,
                student_id, match_status, processing_status
            ) VALUES (3, 2, 'front.png', 'back.png', 1, 'matched', 'graded')
            """
        )
        connection.execute(
            """
            INSERT INTO session_results (
                id, session_id, student_id, paper_id,
                total_score, student_score, needs_human_review, raw_json
            ) VALUES (4, 2, 1, 3, 100, 80, 0, '{}')
            """
        )
        connection.executemany(
            """
            INSERT INTO session_details (
                id, result_id, question_id, score_awarded, knowledge_ids
            ) VALUES (?, 4, ?, ?, ?)
            """,
            [
                (5, "Q1", 10, json.dumps(["K_SINGLE"])),
                (6, "Q2", 20, json.dumps(["K_PRIMARY", "K_SECONDARY"])),
            ],
        )
        connection.execute(
            "UPDATE sqlite_sequence SET seq = 99 WHERE name = 'session_details'"
        )


def test_007_drops_legacy_column_and_preserves_detail_contract(
    tmp_path: Path,
) -> None:
    database = _bootstrap_through_006(tmp_path)
    _seed_details(database)

    report = run_migrations(
        "grading",
        db_path=database,
        migrations_dir=GRADING_MIGRATIONS,
    )

    assert report.error is None, report.error
    assert [(item.name, item.status) for item in report.results] == [
        ("007_drop_legacy_knowledge_id", "applied")
    ]
    with sqlite3.connect(database) as connection:
        columns = {
            row[1]: row
            for row in connection.execute("PRAGMA table_xinfo(session_details)")
        }
        assert "knowledge_id" not in columns
        assert columns["knowledge_ids"][3] == 1
        assert connection.execute(
            """
            SELECT id, result_id, question_id, score_awarded, knowledge_ids
            FROM session_details
            ORDER BY id
            """
        ).fetchall() == [
            (5, 4, "Q1", 10.0, '["K_SINGLE"]'),
            (6, 4, "Q2", 20.0, '["K_PRIMARY", "K_SECONDARY"]'),
        ]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        indexes = {
            row[1]
            for row in connection.execute("PRAGMA index_list(session_details)")
        }
        assert {
            "idx_session_details_question",
            "idx_session_details_result",
        }.issubset(indexes)
        triggers = {
            row[0]
            for row in connection.execute(
                """
                SELECT name
                FROM sqlite_schema
                WHERE type = 'trigger' AND tbl_name = 'session_details'
                """
            )
        }
        assert triggers == {
            "session_details_knowledge_ids_valid_insert",
            "session_details_knowledge_ids_valid_update",
        }
        assert connection.execute(
            "SELECT seq FROM sqlite_sequence WHERE name = 'session_details'"
        ).fetchone() == (99,)
        cursor = connection.execute(
            """
            INSERT INTO session_details (
                result_id, question_id, score_awarded, knowledge_ids
            ) VALUES (4, 'Q3', 30, '["K_NEW"]')
            """
        )
        assert cursor.lastrowid == 100
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO session_details (
                    result_id, question_id, score_awarded, knowledge_ids
                ) VALUES (4, 'Q-invalid', 0, '["K_VALID", 1]')
                """
            )


def test_007_rejects_invalid_list_without_partial_rebuild(
    tmp_path: Path,
) -> None:
    database = _bootstrap_through_006(tmp_path)
    _seed_details(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "DROP TRIGGER session_details_knowledge_ids_valid_insert"
        )
        connection.execute(
            "DROP TRIGGER session_details_knowledge_ids_valid_update"
        )
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            """
            UPDATE session_details
            SET knowledge_ids = '["K_PRIMARY", 1]'
            WHERE id = 6
            """
        )

    report = run_migrations(
        "grading",
        db_path=database,
        migrations_dir=GRADING_MIGRATIONS,
    )

    assert report.error is not None
    with sqlite3.connect(database) as connection:
        columns = {
            row[1]: row
            for row in connection.execute("PRAGMA table_xinfo(session_details)")
        }
        assert columns["knowledge_id"][6] == 3
        assert connection.execute(
            "SELECT knowledge_ids FROM session_details WHERE id = 6"
        ).fetchone() == ('["K_PRIMARY", 1]',)
        assert connection.execute(
            """
            SELECT COUNT(*)
            FROM schema_migrations
            WHERE migration_name = '007_drop_legacy_knowledge_id'
              AND success = 1
            """
        ).fetchone() == (0,)
        assert connection.execute(
            """
            SELECT COUNT(*)
            FROM sqlite_schema
            WHERE name = 'session_details_p3_14_new'
            """
        ).fetchone() == (0,)


def test_007_rejects_unexpected_schema_column_without_data_loss(
    tmp_path: Path,
) -> None:
    database = _bootstrap_through_006(tmp_path)
    _seed_details(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "ALTER TABLE session_details ADD COLUMN unexpected_payload TEXT"
        )
        connection.execute(
            """
            UPDATE session_details
            SET unexpected_payload = 'valuable'
            WHERE id = 6
            """
        )

    report = run_migrations(
        "grading",
        db_path=database,
        migrations_dir=GRADING_MIGRATIONS,
    )

    assert report.error is not None
    with sqlite3.connect(database) as connection:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_xinfo(session_details)")
        }
        assert "knowledge_id" in columns
        assert "unexpected_payload" in columns
        assert connection.execute(
            """
            SELECT knowledge_ids, unexpected_payload
            FROM session_details
            WHERE id = 6
            """
        ).fetchone() == ('["K_PRIMARY", "K_SECONDARY"]', "valuable")
        assert connection.execute(
            """
            SELECT COUNT(*)
            FROM schema_migrations
            WHERE migration_name = '007_drop_legacy_knowledge_id'
              AND success = 1
            """
        ).fetchone() == (0,)


def test_007_is_idempotent_and_keeps_sync_state_columns(
    tmp_path: Path,
) -> None:
    database = _bootstrap_through_006(tmp_path)
    first = run_migrations(
        "grading",
        db_path=database,
        migrations_dir=GRADING_MIGRATIONS,
    )
    second = run_migrations(
        "grading",
        db_path=database,
        migrations_dir=GRADING_MIGRATIONS,
    )

    assert first.error is None, first.error
    assert second.error is None, second.error
    assert second.results == []
    with sqlite3.connect(database) as connection:
        session_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(grading_sessions)")
        }
    assert {
        "question_bank_sync_state",
        "question_bank_sync_details_json",
        "question_bank_sync_error",
        "question_bank_sync_updated_at",
    }.issubset(session_columns)


def test_runtime_session_detail_sql_does_not_reference_legacy_column() -> None:
    runtime_paths = [
        PROJECT_ROOT / "backend",
        PROJECT_ROOT / "integration",
        PROJECT_ROOT / "question_bank",
        PROJECT_ROOT / "tools",
    ]
    python_files = [
        path
        for root in runtime_paths
        for path in root.rglob("*.py")
    ] + [
        PROJECT_ROOT / "db_manager.py",
        PROJECT_ROOT / "grading_service.py",
    ]
    violations: list[str] = []
    legacy_column = re.compile(r"\bknowledge_id\b")
    for path in python_files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
            ):
                continue
            sql = node.value
            if "session_details" in sql and legacy_column.search(sql):
                violations.append(
                    f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}"
                )

    assert violations == []
