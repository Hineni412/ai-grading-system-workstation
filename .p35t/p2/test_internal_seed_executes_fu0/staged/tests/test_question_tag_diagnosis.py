from __future__ import annotations

import json
from pathlib import Path

import pytest

from db_manager import DBManager
from integration.diagnosis_profile_service import DiagnosisProfileService
from question_bank.database.schema import connect, initialize_database
from question_bank.services.source_question_link_service import SourceQuestionLinkService


def _seed_system(tmp_path: Path) -> tuple[DiagnosisProfileService, Path]:
    grading_db_path = tmp_path / "grading.db"
    question_bank_db_path = tmp_path / "question_bank.db"
    db = DBManager(grading_db_path)
    db.initialize()
    initialize_database(question_bank_db_path)
    rubric_path = tmp_path / "rubric.json"
    rubric_path.write_text(
        json.dumps(
            {
                "questions": [
                    {"question_id": "Q1", "max_score": 10},
                    {"question_id": "Q2", "max_score": 5},
                    {"question_id": "Q3", "max_score": 5},
                    {"question_id": "Q4", "max_score": 5},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with db._connect() as conn:
        conn.execute(
            "INSERT INTO students (id, student_code, name, class_name) "
            "VALUES (12, 'S12', '学生甲', '八年级1班')"
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
                id, session_id, student_id, paper_id, total_score, student_score,
                needs_human_review, raw_json
            ) VALUES (14001, 14, 12, 1401, 25, 11, 0, '{}')
            """
        )
        conn.executemany(
            """
            INSERT INTO session_details (
                result_id, question_id, score_awarded, deduction_reason,
                knowledge_ids, error_category, error_summary,
                secondary_errors_json
            ) VALUES (14001, ?, ?, ?, '["UNKNOWN"]', ?, ?, ?)
            """,
            [
                (
                    "Q1",
                    6,
                    "缺少辅助线",
                    "逻辑断裂",
                    "辅助线思路缺失",
                    json.dumps(
                        [
                            {
                                "category": "审题错误",
                                "summary": "条件识别不完整",
                                "evidence": "漏读已知",
                            }
                        ],
                        ensure_ascii=False,
                    ),
                ),
                ("Q2", 5, "", None, None, "[]"),
                ("Q3", 0, "未作答", "未作答", "未作答", "[]"),
            ],
        )

    with connect(question_bank_db_path) as conn:
        conn.executemany(
            "INSERT INTO questions (id, question_number, question_text) VALUES (?, ?, ?)",
            [(101, "1", "题目1"), (102, "2", "题目2"), (103, "3", "题目3")],
        )
        conn.executemany(
            "INSERT INTO question_tags (question_id, tag_type, tag_value) VALUES (?, ?, ?)",
            [
                (101, "knowledge_point", "三角形全等"),
                (101, "method", "构造辅助线"),
                (101, "error_type", "辅助线思路缺失"),
                (102, "knowledge_point", "三角形全等"),
                (102, "sub_skill", "角平分线模型"),
                (103, "ability", "运算能力"),
            ],
        )
    links = SourceQuestionLinkService(question_bank_db_path)
    for source_id, bank_id in (("Q1", 101), ("Q2", 102), ("Q3", 103)):
        links.confirm_link(
            grading_session_id=14,
            source_question_id=source_id,
            bank_question_id=bank_id,
            link_method="paper_question_number",
        )
    return DiagnosisProfileService(grading_db_path, question_bank_db_path), question_bank_db_path


def test_tag_profile_aggregates_exact_current_tags_and_reports_partial_coverage(tmp_path: Path) -> None:
    service, _question_bank_db = _seed_system(tmp_path)

    profile = service.build_profiles(
        scope={"mode": "student", "student_ids": ["12"]},
        exam_scope={"mode": "current", "session_ids": [14]},
    )

    assert profile["diagnosis_identity"] == "question_tag"
    weak = profile["students"][0]["weak_points"][0]
    assert weak["knowledge_key"] == "knowledge_point:三角形全等"
    assert weak["mastery"] == pytest.approx(11 / 15, abs=0.0001)
    assert weak["evidence_count"] == 2
    assert weak["tag_context"]["method"] == ["构造辅助线"]
    assert weak["tag_context"]["sub_skill"] == ["角平分线模型"]
    assert weak["error_counts"]["primary"] == {"辅助线思路缺失": 1}
    assert weak["error_counts"]["secondary"] == {"条件识别不完整": 1}
    assert profile["coverage"] == {
        "covered_items": 2,
        "total_items": 4,
        "missing_items": {"Q3": "missing_knowledge_point", "Q4": "missing_link"},
    }


def test_editing_bank_tag_moves_historical_evidence_without_regrading(tmp_path: Path) -> None:
    service, question_bank_db = _seed_system(tmp_path)
    scope = {"mode": "student", "student_ids": ["12"]}
    exam_scope = {"mode": "current", "session_ids": [14]}

    first = service.build_tag_profiles(scope=scope, exam_scope=exam_scope)
    with connect(question_bank_db) as conn:
        conn.execute(
            "UPDATE question_tags SET tag_value = '轴对称' "
            "WHERE question_id = 102 AND tag_type = 'knowledge_point'"
        )
    second = service.build_tag_profiles(scope=scope, exam_scope=exam_scope)

    assert {item["knowledge_point"] for item in first["students"][0]["weak_points"]} == {
        "三角形全等"
    }
    assert {item["knowledge_point"] for item in second["students"][0]["weak_points"]} == {
        "三角形全等",
        "轴对称",
    }
    rows = service.tag_evidence(
        knowledge_point="轴对称",
        student_ids=["12"],
        session_ids=[14],
    )
    assert [(row["session_id"], row["question_id"]) for row in rows] == [(14, "Q2")]
