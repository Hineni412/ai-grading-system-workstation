from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from question_bank.database.schema import connect, initialize_database


EXPECTED_TABLES = {
    "knowledge_concepts",
    "knowledge_relations",
    "knowledge_source_mappings",
    "grading_question_links",
    "training_tasks",
    "training_variants",
    "variant_students",
    "training_task_items",
    "training_exports",
    "training_attempts",
}


def test_initialize_database_creates_knowledge_practice_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)

    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()

    assert EXPECTED_TABLES.issubset({str(row["name"]) for row in rows})


def test_source_mapping_identity_is_unique(tmp_path: Path) -> None:
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)

    with connect(db_path) as conn:
        concept_id = conn.execute(
            "INSERT INTO knowledge_concepts (canonical_key, name) VALUES (?, ?)",
            ("math.quadratic_function", "二次函数"),
        ).lastrowid
        conn.execute(
            """
            INSERT INTO knowledge_source_mappings
                (source_namespace, source_value, concept_id, status, confidence)
            VALUES (?, ?, ?, 'confirmed', 1.0)
            """,
            ("grading_weak_point", "二次函数图像", concept_id),
        )

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO knowledge_source_mappings
                    (source_namespace, source_value, concept_id, status, confidence)
                VALUES (?, ?, ?, 'confirmed', 1.0)
                """,
                ("grading_weak_point", "二次函数图像", concept_id),
            )


def test_training_attempt_requires_stable_task_item_reference(tmp_path: Path) -> None:
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)

    with connect(db_path) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO training_attempts (task_item_code, student_id)
                VALUES ('missing-item', 'student-1')
                """
            )
