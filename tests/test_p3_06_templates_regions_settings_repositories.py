"""P3-06 template, region, and setting repositories on temporary SQLite."""

from __future__ import annotations

import inspect
import re
import sqlite3
from pathlib import Path

import pytest

from db_manager import DBManager


def _initialized_database(tmp_path: Path) -> Path:
    database = tmp_path / "grading.db"
    DBManager(database).initialize()
    return database


def _seed_session(database: Path, *, name: str = "Session") -> int:
    with sqlite3.connect(database) as connection:
        return int(
            connection.execute(
                """
                INSERT INTO grading_sessions (
                    session_name, rubric_path, answer_key_path, status
                ) VALUES (?, '', '', 'created')
                """,
                (name,),
            ).lastrowid
        )


def _region(*, region_uuid: str = "region-1") -> dict[str, object]:
    return {
        "region_uuid": region_uuid,
        "page": "front",
        "region_order": 0,
        "x": 10,
        "y": 20,
        "w": 100,
        "h": 80,
        "detected_question_id": "Q1",
        "mapped_question_id": "Q1",
        "confidence": 0.95,
        "is_confirmed": True,
        "mapping_status": "manual",
        "multi_region_confirmed": False,
    }


def test_settings_repository_preserves_string_upsert_and_default(
    tmp_path: Path,
) -> None:
    from backend.repositories import SQLiteConnectionFactory
    from backend.repositories.settings import SettingsRepositoryGateway

    database = _initialized_database(tmp_path)
    repository = SettingsRepositoryGateway(SQLiteConnectionFactory(database))

    assert repository.get_app_setting("missing", "fallback") == "fallback"
    repository.set_app_setting("selected", "1")
    repository.set_app_setting("selected", "2")
    assert repository.get_app_setting("selected") == "2"


def test_template_region_repository_replaces_regions_and_marks_snapshot(
    tmp_path: Path,
) -> None:
    from backend.repositories import SQLiteConnectionFactory
    from backend.repositories.templates import TemplateRegionRepositoryGateway

    database = _initialized_database(tmp_path)
    session_id = _seed_session(database)
    repository = TemplateRegionRepositoryGateway(
        SQLiteConnectionFactory(database)
    )
    template_id = repository.upsert_session_template(
        session_id,
        "front.png",
        "back.png",
    )

    token = repository.replace_answer_regions_atomic(
        session_id,
        template_id,
        [_region()],
        confirmed=True,
    )

    template = repository.get_session_template(session_id)
    assert template is not None
    assert template["is_confirmed"] == 1
    assert template["regions_snapshot_pending"] == 1
    assert template["regions_snapshot_token"] == token
    assert repository.list_answer_regions(session_id)[0]["region_uuid"] == (
        "region-1"
    )
    assert repository.is_template_ready(session_id)
    assert repository.mark_region_snapshot_complete(
        session_id,
        expected_token=token,
    )
    assert not repository.mark_region_snapshot_complete(
        session_id,
        expected_token=token,
    )


def test_template_region_repository_rejects_cross_session_template_without_write(
    tmp_path: Path,
) -> None:
    from backend.repositories import SQLiteConnectionFactory
    from backend.repositories.templates import TemplateRegionRepositoryGateway

    database = _initialized_database(tmp_path)
    first_session_id = _seed_session(database, name="First")
    second_session_id = _seed_session(database, name="Second")
    repository = TemplateRegionRepositoryGateway(
        SQLiteConnectionFactory(database)
    )
    template_id = repository.upsert_session_template(
        first_session_id,
        "front.png",
        "back.png",
    )

    with pytest.raises(sqlite3.IntegrityError):
        repository.replace_answer_regions_atomic(
            second_session_id,
            template_id,
            [_region()],
            confirmed=True,
        )

    assert repository.list_answer_regions(first_session_id) == []
    assert repository.list_answer_regions(second_session_id) == []


def test_unconfirmed_atomic_replace_preserves_per_region_confirmation(
    tmp_path: Path,
) -> None:
    from backend.repositories import SQLiteConnectionFactory
    from backend.repositories.templates import TemplateRegionRepositoryGateway

    database = _initialized_database(tmp_path)
    session_id = _seed_session(database)
    repository = TemplateRegionRepositoryGateway(
        SQLiteConnectionFactory(database)
    )
    template_id = repository.upsert_session_template(
        session_id,
        "front.png",
        "back.png",
    )

    repository.replace_answer_regions_atomic(
        session_id,
        template_id,
        [_region()],
        confirmed=False,
    )

    assert repository.get_session_template(session_id)["is_confirmed"] == 0
    assert repository.list_answer_regions(session_id)[0]["is_confirmed"] == 1


def test_hard_delete_rolls_back_p3_05_and_p3_06_data_together(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.repositories.templates import TemplateRepository

    database = _initialized_database(tmp_path)
    session_id = _seed_session(database)
    manager = DBManager(database)
    with sqlite3.connect(database) as connection:
        student_id = int(
            connection.execute(
                """
                INSERT INTO students (student_code, name, class_name)
                VALUES ('001', 'Alpha', 'Class-A')
                """
            ).lastrowid
        )
        paper_id = int(
            connection.execute(
                """
                INSERT INTO exam_papers (
                    session_id, front_image, back_image, student_id,
                    match_status, processing_status
                ) VALUES (?, 'front.png', 'back.png', ?, 'matched', 'graded')
                """,
                (session_id, student_id),
            ).lastrowid
        )
        result_id = int(
            connection.execute(
                """
                INSERT INTO session_results (
                    session_id, student_id, paper_id, total_score,
                    student_score, needs_human_review, raw_json
                ) VALUES (?, ?, ?, 10, 8, 0, '{}')
                """,
                (session_id, student_id, paper_id),
            ).lastrowid
        )
        connection.execute(
            """
            INSERT INTO session_details (
                result_id, question_id, score_awarded,
                deduction_reason, knowledge_ids
            ) VALUES (?, 'Q1', 8, '', '["K1"]')
            """,
            (result_id,),
        )
        connection.execute(
            """
            UPDATE grading_sessions
            SET is_deleted = 1
            WHERE id = ?
            """,
            (session_id,),
        )
    template_id = manager.template_repository.upsert_session_template(
        session_id,
        "template-front.png",
        "template-back.png",
    )
    manager.template_repository.save_answer_regions(
        session_id,
        template_id,
        [_region()],
    )

    def fail_after_region_delete(
        _repository: TemplateRepository,
        _session_id: int,
    ) -> int:
        raise RuntimeError("injected template cleanup failure")

    monkeypatch.setattr(
        TemplateRepository,
        "delete_session_templates",
        fail_after_region_delete,
    )

    with pytest.raises(RuntimeError, match="injected template cleanup failure"):
        manager.hard_delete_grading_session(session_id)

    with sqlite3.connect(database) as connection:
        counts = [
            connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "grading_sessions",
                "exam_papers",
                "session_results",
                "session_details",
                "session_templates",
                "answer_regions",
            )
        ]
    assert counts == [1, 1, 1, 1, 1, 1]


def test_db_manager_exposes_template_and_settings_repositories(
    tmp_path: Path,
) -> None:
    manager = DBManager(_initialized_database(tmp_path))

    assert manager.template_repository is manager._template_repository
    assert manager.settings_repository is manager._settings_repository


def test_answer_region_commit_service_uses_template_repository(
    tmp_path: Path,
) -> None:
    from answer_region_commit_service import AnswerRegionCommitService
    from answer_region_draft_service import AnswerRegionDraftService

    manager = DBManager(_initialized_database(tmp_path))
    session_dir = tmp_path / "session"
    service = AnswerRegionCommitService(
        manager,
        session_dir,
        AnswerRegionDraftService(session_dir),
    )

    assert service._templates is manager.template_repository
    source = inspect.getsource(AnswerRegionCommitService)
    assert "self._db.replace_answer_regions_atomic" not in source
    assert "self._db.list_answer_regions" not in source
    assert "self._db.mark_region_snapshot_complete" not in source


def test_db_manager_template_region_setting_facades_contain_no_sql() -> None:
    method_names = (
        "set_app_setting",
        "get_app_setting",
        "upsert_session_template",
        "activate_session_template",
        "get_session_template",
        "update_session_template_analysis",
        "save_answer_regions",
        "list_answer_regions",
        "bulk_update_answer_region_mapping",
        "add_answer_region",
        "replace_answer_regions_atomic",
        "mark_region_snapshot_complete",
        "delete_answer_region",
        "update_answer_region_bbox",
        "mark_template_confirmed",
        "is_template_ready",
    )

    for method_name in method_names:
        source = inspect.getsource(getattr(DBManager, method_name)).upper()
        assert re.search(
            r"\b(?:SELECT|INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM)\b",
            source,
        ) is None, method_name
