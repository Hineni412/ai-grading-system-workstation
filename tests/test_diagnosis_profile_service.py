from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from db_manager import DBManager
from integration.diagnosis_profile_service import DiagnosisProfileService
from question_bank.services.concept_alignment_service import ConceptAlignmentService


@pytest.fixture
def service(tmp_path: Path) -> DiagnosisProfileService:
    grading_db_path = tmp_path / "grading_system.db"
    question_bank_db_path = tmp_path / "question_bank.db"
    DBManager(grading_db_path).initialize()

    rubric_12 = _write_rubric(
        tmp_path / "rubric_12.json",
        [
            ("Q1", 10, "K_QUADRATIC", "二次函数"),
            ("Q2", 5, "K_UNKNOWN", "陌生诊断词"),
        ],
    )
    rubric_14 = _write_rubric(
        tmp_path / "rubric_14.json",
        [
            ("Q1", 10, "K_QUADRATIC", "二次函数"),
            ("Q2", 5, "K_UNKNOWN", "陌生诊断词"),
        ],
    )
    with sqlite3.connect(grading_db_path) as conn:
        conn.executemany(
            "INSERT INTO students (id, student_code, name, class_name) VALUES (?, ?, ?, ?)",
            [(12, "S12", "张三", "九年级1班"), (15, "S15", "李四", "九年级1班")],
        )
        conn.executemany(
            """
            INSERT INTO grading_sessions (
                id, session_name, rubric_path, answer_key_path, status, is_deleted
            ) VALUES (?, ?, ?, '', 'completed', 0)
            """,
            [(12, "第一次考试", str(rubric_12)), (14, "当前考试", str(rubric_14))],
        )
        _insert_result(
            conn,
            session_id=12,
            student_id=12,
            paper_id=1201,
            result_id=12001,
            total_score=15,
            student_score=10,
            details=[("Q1", 8, "K_QUADRATIC"), ("Q2", 2, "K_UNKNOWN")],
        )
        _insert_result(
            conn,
            session_id=12,
            student_id=15,
            paper_id=1202,
            result_id=12002,
            total_score=15,
            student_score=9,
            details=[("Q1", 7, "K_QUADRATIC"), ("Q2", 2, "K_UNKNOWN")],
        )
        _insert_result(
            conn,
            session_id=14,
            student_id=12,
            paper_id=1401,
            result_id=14001,
            total_score=15,
            student_score=7,
            details=[("Q1", 6, "K_QUADRATIC"), ("Q2", 1, "K_UNKNOWN")],
        )
        conn.execute(
            """
            UPDATE session_details
            SET deduction_reason = '未作答'
            WHERE result_id = 14001 AND question_id = 'Q2'
            """
        )

    alignment = ConceptAlignmentService(question_bank_db_path)
    concept = alignment.create_concept("math.quadratic", "二次函数")
    alignment.confirm_mapping("grading_weak_point", "二次函数", concept.id, reviewed_by="teacher")
    return DiagnosisProfileService(grading_db_path, question_bank_db_path)


def test_current_exam_single_student_profile_uses_full_score_weighting(
    service: DiagnosisProfileService,
) -> None:
    profile = service.build_profiles(
        scope={"mode": "student", "student_ids": ["12"]},
        exam_scope={"mode": "current", "session_ids": [14]},
    )

    weak = next(item for item in profile["students"][0]["weak_points"] if item["source_term"] == "二次函数")
    assert weak["mastery"] == pytest.approx(6 / 10)
    assert weak["source_question_refs"][0]["session_id"] == 14
    assert weak["eligible_for_recommendation"] is True


def test_selected_students_and_manual_sessions_are_respected(
    service: DiagnosisProfileService,
) -> None:
    profile = service.build_profiles(
        scope={"mode": "selected", "student_ids": ["12", "15"]},
        exam_scope={"mode": "manual", "session_ids": [12, 14]},
    )

    assert {item["student_id"] for item in profile["students"]} == {"12", "15"}
    assert profile["exam_scope"]["session_ids"] == [12, 14]


def test_unmapped_terms_are_visible_and_not_eligible(
    service: DiagnosisProfileService,
) -> None:
    profile = service.build_profiles(
        scope={"mode": "student", "student_ids": ["12"]},
        exam_scope={"mode": "current", "session_ids": [14]},
    )

    weak = next(item for item in profile["students"][0]["weak_points"] if item["source_term"] == "陌生诊断词")
    assert profile["unmapped_terms"] == ["陌生诊断词"]
    assert weak["eligible_for_recommendation"] is False
    assert weak["actionable_reasons"] == []


def test_class_scope_selects_students_from_requested_class(
    service: DiagnosisProfileService,
) -> None:
    profile = service.build_profiles(
        scope={"mode": "class", "class_id": "九年级1班"},
        exam_scope={"mode": "cross_exam"},
    )

    assert {item["student_id"] for item in profile["students"]} == {"12", "15"}
    assert profile["exam_scope"]["session_ids"] == [12, 14]


def _write_rubric(path: Path, questions: list[tuple[str, float, str, str]]) -> Path:
    path.write_text(
        json.dumps(
            {
                "questions": [
                    {
                        "question_id": question_id,
                        "max_score": max_score,
                        "knowledge_id": knowledge_id,
                        "knowledge_name": knowledge_name,
                    }
                    for question_id, max_score, knowledge_id, knowledge_name in questions
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _insert_result(
    conn: sqlite3.Connection,
    *,
    session_id: int,
    student_id: int,
    paper_id: int,
    result_id: int,
    total_score: float,
    student_score: float,
    details: list[tuple[str, float, str]],
) -> None:
    conn.execute(
        """
        INSERT INTO exam_papers (
            id, session_id, front_image, back_image, student_id, match_status, processing_status
        ) VALUES (?, ?, '', '', ?, 'matched', 'completed')
        """,
        (paper_id, session_id, student_id),
    )
    conn.execute(
        """
        INSERT INTO session_results (
            id, session_id, student_id, paper_id, total_score, student_score,
            needs_human_review, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, 0, '{}')
        """,
        (result_id, session_id, student_id, paper_id, total_score, student_score),
    )
    conn.executemany(
        """
        INSERT INTO session_details (
            result_id, question_id, score_awarded, deduction_reason, knowledge_id, knowledge_ids
        ) VALUES (?, ?, ?, '需要巩固', ?, ?)
        """,
        [
            (result_id, question_id, score, knowledge_id, json.dumps([knowledge_id]))
            for question_id, score, knowledge_id in details
        ],
    )
