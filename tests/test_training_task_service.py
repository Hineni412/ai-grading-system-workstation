from __future__ import annotations

import copy
import sqlite3
from pathlib import Path

import pytest

from question_bank.database.schema import connect, initialize_database
from question_bank.services.training_task_service import TrainingTaskService


@pytest.fixture
def service(tmp_path: Path) -> TrainingTaskService:
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO questions (
                id, question_number, question_type, question_text, answer_text, difficulty
            ) VALUES (201, '1', '解答题', '原题干', '原答案', '5')
            """
        )
        conn.execute(
            """
            INSERT INTO question_tags (question_id, tag_type, tag_value, source)
            VALUES (201, 'knowledge_point', '二次函数', 'manual')
            """
        )
    return TrainingTaskService(db_path)


@pytest.fixture
def practice_plan() -> dict:
    return {
        "scope_snapshot": {"mode": "selected", "student_ids": ["12"]},
        "exam_scope": {"mode": "current", "session_ids": [14]},
        "diagnosis_snapshot": {
            "students": [
                {
                    "student_id": "12",
                    "student_name": "张三",
                    "class_id": "九年级1班",
                    "weak_points": [],
                }
            ]
        },
        "generation_config": {"question_count": 10},
        "warnings": [],
        "variants": [
            {
                "variant_key": "student-12",
                "variant_type": "individual",
                "student_ids": ["12"],
                "grouping_reason": {"rule": "individual"},
                "diagnosis_snapshot": {"student_id": "12"},
                "shortages": [],
                "warnings": [],
                "items": [
                    {
                        "question_id": 201,
                        "question_fingerprint": "fingerprint-201",
                        "item_order": 1,
                        "stage": "direct",
                        "target_concept_id": 7,
                        "matched_concept_id": 7,
                        "relation_type": "direct",
                        "recommend_score": 0.9,
                        "warnings": [],
                    }
                ],
            }
        ],
    }


def test_save_task_persists_scope_diagnosis_variants_and_items(
    service: TrainingTaskService,
    practice_plan: dict,
) -> None:
    task = service.create_task(practice_plan, created_by="teacher")
    loaded = service.get_task(task.id)

    assert loaded["scope_snapshot"] == practice_plan["scope_snapshot"]
    assert loaded["diagnosis_snapshot"] == practice_plan["diagnosis_snapshot"]
    assert loaded["variants"][0]["items"][0]["question_id"] == 201
    assert loaded["variants"][0]["items"][0]["task_item_code"]


def test_task_item_snapshot_survives_later_question_edit(
    service: TrainingTaskService,
    practice_plan: dict,
) -> None:
    task = service.create_task(practice_plan, created_by="teacher")
    with connect(service.db_path) as conn:
        conn.execute("UPDATE questions SET question_text = '后来修改的题干' WHERE id = 201")

    loaded = service.get_task(task.id)

    assert loaded["variants"][0]["items"][0]["question_snapshot"]["question_text"] == "原题干"


def test_attempt_can_reference_stable_task_item_code(
    service: TrainingTaskService,
    practice_plan: dict,
) -> None:
    task = service.create_task(practice_plan, created_by="teacher")
    item_code = task.variants[0].items[0].task_item_code

    service.record_attempt_stub(
        task_item_code=item_code,
        student_id="12",
        grading_session_id=21,
        grading_question_id="8",
    )

    assert service.list_attempts(task.id)[0]["task_item_code"] == item_code


def test_cancel_task_keeps_saved_snapshots(
    service: TrainingTaskService,
    practice_plan: dict,
) -> None:
    task = service.create_task(practice_plan, created_by="teacher")

    service.cancel_task(task.id)
    loaded = service.get_task(task.id)

    assert loaded["status"] == "cancelled"
    assert loaded["variants"][0]["items"][0]["question_snapshot"]["question_text"] == "原题干"


def test_task_creation_is_transactional(
    service: TrainingTaskService,
    practice_plan: dict,
) -> None:
    invalid_plan = copy.deepcopy(practice_plan)
    invalid_plan["variants"].append(copy.deepcopy(invalid_plan["variants"][0]))

    with pytest.raises(sqlite3.IntegrityError):
        service.create_task(invalid_plan, created_by="teacher")

    assert service.list_tasks() == []


def test_mark_export_state_updates_task_and_variant_only(
    service: TrainingTaskService,
    practice_plan: dict,
) -> None:
    task = service.create_task(practice_plan, created_by="teacher")

    service.mark_export_state(task.id, status="exporting", variant_id=task.variants[0].id)
    loaded = service.get_task(task.id)

    assert loaded["status"] == "exporting"
    assert loaded["variants"][0]["status"] == "exporting"
    assert loaded["variants"][0]["items"][0]["question_snapshot"]["question_text"] == "原题干"


def test_read_methods_do_not_run_database_initialization(
    service: TrainingTaskService,
    practice_plan: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = service.create_task(practice_plan, created_by="teacher")

    def fail_if_initialized(_db_path):
        raise AssertionError("read-only task lookup must not initialize the database")

    monkeypatch.setattr(
        "question_bank.services.training_task_service.initialize_database",
        fail_if_initialized,
    )
    monkeypatch.setattr(
        "question_bank.services.training_task_service.connect",
        fail_if_initialized,
    )

    assert service.list_tasks()[0]["id"] == task.id
    assert service.get_task(task.id)["task_code"] == task.task_code
