import json
import sqlite3
from pathlib import Path

import pytest

from db_manager import DBManager
from question_bank.services.alignment_review_service import (
    AlignmentFocusItem,
    AlignmentReviewService,
    apply_scope_exclusions,
    filter_focus_sources,
    focus_items_from_diagnosis,
    merge_focus_sources,
)


@pytest.fixture
def review_system(tmp_path: Path):
    grading_db = tmp_path / "grading_system.db"
    question_bank_db = tmp_path / "question_bank.db"
    DBManager(grading_db).initialize()
    rubric_path = tmp_path / "review_rubric.json"
    rubric_path.write_text(
        json.dumps(
            {
                "questions": [
                    {
                        "question_id": "Q1",
                        "max_score": 5,
                        "knowledge_id": "K_UNKNOWN",
                        "knowledge_name": "陌生诊断词",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with sqlite3.connect(grading_db) as conn:
        conn.execute(
            "INSERT INTO students (id, student_code, name, class_name) "
            "VALUES (12, 'S12', '张三', '九年级1班')"
        )
        conn.execute(
            """
            INSERT INTO grading_sessions (
                id, session_name, rubric_path, answer_key_path, status, is_deleted
            ) VALUES (14, '当前考试', ?, '', 'completed', 0)
            """,
            (str(rubric_path),),
        )
        conn.execute(
            """
            INSERT INTO exam_papers (
                id, session_id, front_image, back_image, student_id,
                match_status, processing_status
            ) VALUES (1401, 14, '', '', 12, 'matched', 'graded')
            """
        )
        conn.execute(
            """
            INSERT INTO session_results (
                id, session_id, student_id, paper_id, total_score,
                student_score, needs_human_review, raw_json
            ) VALUES (14001, 14, 12, 1401, 5, 0, 0, '{}')
            """
        )
        conn.execute(
            """
            INSERT INTO session_details (
                result_id, question_id, score_awarded, deduction_reason,
                knowledge_id, knowledge_ids
            ) VALUES (14001, 'Q1', 0, '需要巩固', 'K_UNKNOWN', '["K_UNKNOWN"]')
            """
        )
    review = AlignmentReviewService(grading_db, question_bank_db)
    concept = review.alignment.create_concept("math.unknown", "待确认知识点")
    scope = {"mode": "student", "student_ids": ["12"]}
    exam_scope = {"mode": "current", "session_ids": [14]}
    return review, concept.id, scope, exam_scope


def _diagnosis() -> dict:
    return {
        "exam_scope": {"session_ids": [1]},
        "students": [
            {
                "student_id": "70",
                "weak_points": [
                    {
                        "source_term": "角平分线性质",
                        "source_display": "G7_15 · 角平分线性质",
                        "mapping_status": "suggested",
                        "evidence_count": 3,
                    }
                ],
            }
        ],
    }


def test_focus_item_preserves_namespace_and_provenance() -> None:
    items = focus_items_from_diagnosis(_diagnosis())

    assert items == [
        AlignmentFocusItem(
            source_namespace="grading_weak_point",
            source_value="角平分线性质",
            display_value="G7_15 · 角平分线性质",
            evidence_count=3,
            student_ids=("70",),
            session_ids=(1,),
        )
    ]


def test_missing_focus_source_is_injected_and_same_name_question_tag_is_not_used() -> None:
    focus = focus_items_from_diagnosis(_diagnosis())
    sources = [
        {
            "source_namespace": "question_tag",
            "source_value": "角平分线性质",
            "display_value": "角平分线性质",
            "evidence_count": 4,
        }
    ]

    merged = merge_focus_sources(sources, focus)
    filtered = filter_focus_sources(merged, focus)

    assert len(filtered) == 1
    assert filtered[0]["source_namespace"] == "grading_weak_point"
    assert filtered[0]["source_value"] == "角平分线性质"
    assert filtered[0]["evidence_count"] == 3


def test_confirm_and_rebuild_returns_confirmed_current_diagnosis(
    review_system,
) -> None:
    review, concept_id, scope, exam_scope = review_system

    refreshed = review.confirm_and_rebuild(
        scope=scope,
        exam_scope=exam_scope,
        source_value="陌生诊断词",
        concept_id=concept_id,
    )

    weak = refreshed["students"][0]["weak_points"][0]
    assert weak["mapping_status"] == "confirmed"
    assert weak["eligible_for_recommendation"] is True


def test_scope_exclusion_does_not_create_global_rejection() -> None:
    diagnosis = _diagnosis()
    updated = apply_scope_exclusions(diagnosis, {"角平分线性质"})

    weak = updated["students"][0]["weak_points"][0]
    assert weak["eligible_for_recommendation"] is False
    assert weak["review_state"] == "本次不推荐"
    assert focus_items_from_diagnosis(updated) == []
