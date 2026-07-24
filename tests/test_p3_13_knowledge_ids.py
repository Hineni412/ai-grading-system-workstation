from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from backend.domain_models import GradingResult, QuestionGradingDetail
from backend.repositories import SQLiteConnectionFactory
from backend.repositories.reporting import ReportRepositoryGateway
from backend.repositories.results import ResultRepositoryGateway
from backend.schema_contracts import schema_signature
from db_manager import DBManager
from grading_service import _detail_from_row
from update_tools.migrate_db import run_migrations


PROJECT_ROOT = Path(__file__).resolve().parents[1]
GRADING_MIGRATIONS = PROJECT_ROOT / "migrations" / "grading"


def _bootstrap_through_005(tmp_path: Path) -> Path:
    database = tmp_path / "grading.db"
    migrations = tmp_path / "pre-006-migrations"
    migrations.mkdir()
    for source in sorted(GRADING_MIGRATIONS.glob("*.sql")):
        if int(source.name.split("_", 1)[0]) > 5:
            continue
        shutil.copy2(source, migrations / source.name)
    report = run_migrations(
        "grading",
        db_path=database,
        migrations_dir=migrations,
    )
    assert report.error is None, report.error
    return database


def _migrations_through_006(tmp_path: Path) -> Path:
    migrations = tmp_path / "through-006-migrations"
    migrations.mkdir(exist_ok=True)
    for source in sorted(GRADING_MIGRATIONS.glob("*.sql")):
        if int(source.name.split("_", 1)[0]) <= 6:
            shutil.copy2(source, migrations / source.name)
    return migrations


def _seed_result_details(database: Path) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO students (
                id, student_code, name
            ) VALUES (1, 'S1', 'student')
            """
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
            ) VALUES (
                3, 2, 'front.png', 'back.png',
                1, 'matched', 'graded'
            )
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
        connection.execute(
            """
            INSERT INTO session_details (
                id, result_id, question_id, score_awarded,
                knowledge_id, knowledge_ids
            ) VALUES (5, 4, 'Q1', 10, 'K_SINGLE', NULL)
            """
        )
        connection.execute(
            """
            INSERT INTO session_details (
                id, result_id, question_id, score_awarded,
                knowledge_id, knowledge_ids
            ) VALUES (6, 4, 'Q2', 20, 'K_PRIMARY', ?)
            """,
            (json.dumps(["K_PRIMARY", "K_SECONDARY"]),),
        )


def test_006_backfills_lists_and_makes_legacy_value_generated(
    tmp_path: Path,
) -> None:
    database = _bootstrap_through_005(tmp_path)
    _seed_result_details(database)

    report = run_migrations(
        "grading",
        db_path=database,
        migrations_dir=_migrations_through_006(tmp_path),
    )

    assert report.error is None, report.error
    assert [(item.name, item.status) for item in report.results] == [
        ("006_knowledge_ids_primary", "applied")
    ]
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            """
            SELECT id, knowledge_id, knowledge_ids
            FROM session_details
            ORDER BY id
            """
        ).fetchall()
        assert rows == [
            (5, "K_SINGLE", '["K_SINGLE"]'),
            (6, "K_PRIMARY", '["K_PRIMARY", "K_SECONDARY"]'),
        ]
        legacy_column = next(
            row
            for row in connection.execute(
                "PRAGMA table_xinfo(session_details)"
            )
            if row[1] == "knowledge_id"
        )
        assert legacy_column[6] == 3

        connection.execute(
            """
            INSERT INTO session_details (
                result_id, question_id, score_awarded, knowledge_ids
            ) VALUES (4, 'Q3', 30, '["K_NEW", "K_EXTRA"]')
            """
        )
        assert connection.execute(
            """
            SELECT knowledge_id, knowledge_ids
            FROM session_details
            WHERE question_id = 'Q3'
            """
        ).fetchone() == ("K_NEW", '["K_NEW", "K_EXTRA"]')


@pytest.mark.parametrize(
    "knowledge_ids",
    [
        '["K_VALID", 1]',
        '["K_VALID", " "]',
    ],
)
def test_006_rejects_invalid_items_in_new_list(
    tmp_path: Path,
    knowledge_ids: str,
) -> None:
    database = _bootstrap_through_005(tmp_path)
    _seed_result_details(database)
    report = run_migrations(
        "grading",
        db_path=database,
        migrations_dir=_migrations_through_006(tmp_path),
    )
    assert report.error is None, report.error

    with sqlite3.connect(database) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO session_details (
                    result_id, question_id, score_awarded, knowledge_ids
                ) VALUES (4, 'Q-invalid', 0, ?)
                """,
                (knowledge_ids,),
            )


def test_result_repository_writes_only_list_truth_and_derives_legacy_value(
    tmp_path: Path,
) -> None:
    database = _bootstrap_through_005(tmp_path)
    _seed_result_details(database)
    report = run_migrations(
        "grading",
        db_path=database,
        migrations_dir=_migrations_through_006(tmp_path),
    )
    assert report.error is None, report.error
    repository = ResultRepositoryGateway(SQLiteConnectionFactory(database))
    grading_result = GradingResult(
        student_name="student",
        total_score=100,
        student_score=30,
        needs_human_review=False,
        grading_details=[
            QuestionGradingDetail(
                question_id="Q-new",
                score_awarded=30,
                deduction_reason=None,
                knowledge_id="STALE_LEGACY_VALUE",
                knowledge_ids=["K_MAIN", "K_SECONDARY"],
            )
        ],
        raw_json={},
    )

    result_id = repository.save_session_result(2, 1, 3, grading_result)

    assert repository.get_result_details(result_id) == [
        {
            "detail_id": 7,
            "question_id": "Q-new",
            "score_awarded": 30.0,
            "deduction_reason": None,
            "knowledge_id": "K_MAIN",
            "knowledge_ids": ["K_MAIN", "K_SECONDARY"],
            "error_category": None,
            "error_summary": None,
            "confidence_score": None,
            "secondary_errors_json": "[]",
            "secondary_errors": [],
        }
    ]


def test_schema_signature_includes_generated_legacy_column(
    tmp_path: Path,
) -> None:
    database = _bootstrap_through_005(tmp_path)
    report = run_migrations(
        "grading",
        db_path=database,
        migrations_dir=_migrations_through_006(tmp_path),
    )
    assert report.error is None, report.error

    columns = schema_signature(database)["tables"]["session_details"]["columns"]

    assert columns["knowledge_id"] == ("text", 1, "none", 0, 3)


def test_report_snapshot_reads_list_truth_and_derives_legacy_value(
    tmp_path: Path,
) -> None:
    database = _bootstrap_through_005(tmp_path)
    _seed_result_details(database)
    report = run_migrations(
        "grading",
        db_path=database,
        migrations_dir=_migrations_through_006(tmp_path),
    )
    assert report.error is None, report.error

    snapshot = ReportRepositoryGateway(
        SQLiteConnectionFactory(database)
    ).get_session_report_snapshot(2)

    assert snapshot.details[1]["knowledge_ids"] == [
        "K_PRIMARY",
        "K_SECONDARY",
    ]
    assert snapshot.details[1]["knowledge_id"] == "K_PRIMARY"


def test_grading_detail_model_derives_legacy_value_from_list_truth() -> None:
    detail = _detail_from_row(
        {
            "question_id": "Q1",
            "score_awarded": 1,
            "knowledge_id": "STALE",
            "knowledge_ids": ["K_MAIN", "K_SECONDARY"],
        }
    )

    assert detail.knowledge_id == "K_MAIN"
    assert detail.knowledge_ids == ["K_MAIN", "K_SECONDARY"]


@pytest.mark.parametrize(
    ("legacy_value", "list_value"),
    [
        ("K1", "{bad json"),
        ("K1", "[]"),
        ("K1", '["K2"]'),
        ("", None),
    ],
)
def test_006_rejects_invalid_historical_rows_without_partial_change(
    tmp_path: Path,
    legacy_value: str,
    list_value: str | None,
) -> None:
    database = _bootstrap_through_005(tmp_path)
    _seed_result_details(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            UPDATE session_details
            SET knowledge_id = ?, knowledge_ids = ?
            WHERE id = 5
            """,
            (legacy_value, list_value),
        )

    report = run_migrations(
        "grading",
        db_path=database,
        migrations_dir=_migrations_through_006(tmp_path),
    )

    assert report.error is not None
    with sqlite3.connect(database) as connection:
        columns = {
            row[1]: row
            for row in connection.execute("PRAGMA table_xinfo(session_details)")
        }
        stored = connection.execute(
            "SELECT knowledge_id, knowledge_ids FROM session_details WHERE id = 5"
        ).fetchone()
        applied = connection.execute(
            """
            SELECT COUNT(*)
            FROM schema_migrations
            WHERE migration_name = '006_knowledge_ids_primary'
              AND success = 1
            """
        ).fetchone()[0]
    assert columns["knowledge_id"][6] == 0
    assert stored == (legacy_value, list_value)
    assert applied == 0


def test_006_preserves_sequence_indexes_and_is_idempotent(
    tmp_path: Path,
) -> None:
    database = _bootstrap_through_005(tmp_path)
    _seed_result_details(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE sqlite_sequence SET seq = 99 WHERE name = 'session_details'"
        )

    first = run_migrations(
        "grading",
        db_path=database,
        migrations_dir=_migrations_through_006(tmp_path),
    )
    second = run_migrations(
        "grading",
        db_path=database,
        migrations_dir=_migrations_through_006(tmp_path),
    )

    assert first.error is None, first.error
    assert second.error is None, second.error
    assert second.results == []
    with sqlite3.connect(database) as connection:
        sequence = connection.execute(
            "SELECT seq FROM sqlite_sequence WHERE name = 'session_details'"
        ).fetchone()[0]
        indexes = {
            row[1]
            for row in connection.execute("PRAGMA index_list(session_details)")
        }
        cursor = connection.execute(
            """
            INSERT INTO session_details (
                result_id, question_id, score_awarded, knowledge_ids
            ) VALUES (4, 'Q-sequence', 0, '["K"]')
            """
        )
    assert sequence == 99
    assert cursor.lastrowid == 100
    assert {
        "idx_session_details_question",
        "idx_session_details_result",
    }.issubset(indexes)


def test_006_backfills_missing_historical_secondary_errors_column(
    tmp_path: Path,
) -> None:
    database = _bootstrap_through_005(tmp_path)
    _seed_result_details(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "ALTER TABLE session_details DROP COLUMN secondary_errors_json"
        )

    report = run_migrations(
        "grading",
        db_path=database,
        migrations_dir=_migrations_through_006(tmp_path),
    )

    assert report.error is None, report.error
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            """
            SELECT secondary_errors_json
            FROM session_details
            ORDER BY id
            """
        ).fetchall() == [("[]",), ("[]",)]


def test_diagnosis_prefers_stored_list_when_rubric_value_conflicts(
    tmp_path: Path,
) -> None:
    manager = DBManager(tmp_path / "unused.db")
    manager._load_rubric_maps_for_session = lambda _session_id: {  # type: ignore[method-assign]
        "knowledge": {"Q1": ["K_RUBRIC"]},
        "score": {"Q1": 10},
        "label": {},
    }
    row = {
        "session_id": 1,
        "student_id": 2,
        "question_id": "Q1",
        "score_awarded": 5,
        "deduction_reason": "reason",
        "knowledge_id": "K_STORED",
        "knowledge_ids": ["K_STORED", "K_SECONDARY"],
    }

    weak_points = manager._build_weak_point_rows([dict(row)])
    enriched = manager._enrich_detail_rows(
        [dict(row)],
        knowledge_id="K_SECONDARY",
    )

    assert {item["knowledge_id"] for item in weak_points} == {
        "K_STORED",
        "K_SECONDARY",
    }
    assert len(enriched) == 1
