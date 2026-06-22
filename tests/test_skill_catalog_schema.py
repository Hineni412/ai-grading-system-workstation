from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from question_bank.database.schema import connect, initialize_database


EXPECTED_SKILL_TABLES = {
    "skill_topics",
    "skills",
    "assessment_item_skills",
    "question_skill_links",
    "skill_resolution_conflicts",
    "skill_neighbors",
    "skill_system_settings",
    "skill_migration_runs",
}


def _seed_skill(conn: sqlite3.Connection) -> int:
    topic_id = conn.execute(
        "INSERT INTO skill_topics (stable_key, name, subject) VALUES (?, ?, ?)",
        ("math.geometry.triangle", "三角形", "math"),
    ).lastrowid
    return int(
        conn.execute(
            """
            INSERT INTO skills (stable_key, topic_id, name, origin)
            VALUES (?, ?, ?, 'builtin')
            """,
            ("math.geometry.triangle.angle_bisector", topic_id, "角平分线性质"),
        ).lastrowid
    )


def test_initialize_database_creates_unified_skill_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)

    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()

    assert EXPECTED_SKILL_TABLES.issubset({str(row["name"]) for row in rows})


def test_skill_read_mode_defaults_to_legacy(tmp_path: Path) -> None:
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)

    with connect(db_path) as conn:
        value = conn.execute(
            "SELECT value FROM skill_system_settings WHERE key = 'recommendation_read_mode'"
        ).fetchone()

    assert value is not None
    assert value["value"] == "legacy"


def test_skill_redirect_cannot_point_to_itself(tmp_path: Path) -> None:
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)

    with connect(db_path) as conn:
        skill_id = _seed_skill(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE skills SET status = 'merged', redirect_skill_id = ? WHERE id = ?",
                (skill_id, skill_id),
            )


def test_assessment_link_identity_is_unique(tmp_path: Path) -> None:
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)

    with connect(db_path) as conn:
        skill_id = _seed_skill(conn)
        values = ("12", "Q1", skill_id, "measured", "resolved")
        conn.execute(
            """
            INSERT INTO assessment_item_skills (
                grading_session_id, source_question_id, skill_id, role, status
            ) VALUES (?, ?, ?, ?, ?)
            """,
            values,
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO assessment_item_skills (
                    grading_session_id, source_question_id, skill_id, role, status
                ) VALUES (?, ?, ?, ?, ?)
                """,
                values,
            )


def test_question_link_rejects_unknown_role(tmp_path: Path) -> None:
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)

    with connect(db_path) as conn:
        skill_id = _seed_skill(conn)
        question_id = int(
            conn.execute(
                "INSERT INTO questions (question_number, question_text) VALUES ('1', '测试题')"
            ).lastrowid
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO question_skill_links (
                    question_id, skill_id, role, status
                ) VALUES (?, ?, 'topic', 'resolved')
                """,
                (question_id, skill_id),
            )


def test_neighbor_identity_is_unique(tmp_path: Path) -> None:
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)

    with connect(db_path) as conn:
        source_id = _seed_skill(conn)
        topic_id = int(conn.execute("SELECT topic_id FROM skills WHERE id = ?", (source_id,)).fetchone()[0])
        target_id = int(
            conn.execute(
                """
                INSERT INTO skills (stable_key, topic_id, name, origin)
                VALUES (?, ?, ?, 'builtin')
                """,
                ("math.geometry.triangle.angle_bisector_construction", topic_id, "角平分线作图"),
            ).lastrowid
        )
        values = (source_id, target_id, "same_topic", 0.8, "builtin")
        conn.execute(
            """
            INSERT INTO skill_neighbors (
                source_skill_id, target_skill_id, kind, weight, source
            ) VALUES (?, ?, ?, ?, ?)
            """,
            values,
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO skill_neighbors (
                    source_skill_id, target_skill_id, kind, weight, source
                ) VALUES (?, ?, ?, ?, ?)
                """,
                values,
            )
