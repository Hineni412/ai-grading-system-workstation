from __future__ import annotations

import json
from pathlib import Path

from ai_grader import AIGrader
from db_manager import DBManager
from grading_service import GradingService
from hybrid_batch_grading_service import (
    MajorQuestionSpec,
    build_hybrid_major_prompt,
    validate_hybrid_major_response,
)
from question_bank.database.schema import connect, initialize_database
from question_bank.services.source_question_link_service import SourceQuestionLinkService


class _FakeLLMClient:
    pass


def _grader(tmp_path: Path, *, tag_context: dict | None = None) -> AIGrader:
    rubric_path = tmp_path / "rubric.json"
    rubric_path.write_text(
        json.dumps(
            {
                "total_score": 5,
                "questions": [
                    {
                        "question_id": "Q1",
                        "question_type": "calculation",
                        "response_mode": "short_answer_points",
                        "max_score": 5,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return AIGrader(
        rubric_path,
        _FakeLLMClient(),
        question_tag_context=tag_context,
    )


def _payload(*, score: float, primary: str, secondary_errors: list[dict]) -> dict:
    return {
        "student_name": "学生甲",
        "total_score": 5,
        "student_score": score,
        "needs_human_review": False,
        "grading_details": [
            {
                "question_id": "Q1",
                "score_awarded": score,
                "deduction_reason": "作答证据",
                "confidence_score": 90,
                "error_category": "逻辑断裂",
                "error_summary": primary,
                "secondary_errors": secondary_errors,
                "observed_answer": "已写出完整推导过程",
                "evidence_steps": ["有效推导"],
                "answer_is_blank_or_no_valid_work": False,
            }
        ],
    }


def test_full_paper_prompt_uses_question_tags_without_requesting_knowledge_ids(tmp_path: Path) -> None:
    grader = _grader(
        tmp_path,
        tag_context={
            "Q1": {
                "knowledge_point": ["三角形全等"],
                "sub_skill": ["角平分线模型"],
                "method": ["构造辅助线"],
                "ability": ["推理能力"],
                "model": ["全等模型"],
                "error_type": ["辅助线思路缺失"],
                "prerequisite": ["角平分线性质"],
            }
        },
    )

    prompt = grader._build_system_prompt()

    assert "三角形全等" in prompt
    assert "辅助线思路缺失" in prompt
    assert "secondary_errors" in prompt
    assert "knowledge_id" not in prompt
    assert "knowledge_ids" not in prompt


def test_error_validation_keeps_one_primary_and_at_most_two_secondary_errors(tmp_path: Path) -> None:
    grader = _grader(
        tmp_path,
        tag_context={"Q1": {"error_type": ["辅助线思路缺失", "条件识别不完整"]}},
    )

    result = grader._validate_and_convert(
        _payload(
            score=2,
            primary="辅助线思路缺失",
            secondary_errors=[
                {"category": "审题错误", "summary": "条件识别不完整", "evidence": "漏读已知"},
                {"category": "计算错误", "summary": "符号抄错", "evidence": "第二行"},
                {"category": "表达不规范", "summary": "辅助线思路缺失", "evidence": "无作图"},
            ],
        ),
        expected_student_name="学生甲",
    )

    detail = result.grading_details[0]
    assert detail.error_category == "逻辑断裂"
    assert detail.error_summary == "辅助线思路缺失"
    assert [(item.category, item.summary) for item in detail.secondary_errors] == [
        ("审题错误", "条件识别不完整"),
        ("其他", "符号抄错"),
    ]
    assert detail.knowledge_id == "UNKNOWN"
    assert detail.knowledge_ids == []


def test_full_score_clears_primary_and_secondary_errors(tmp_path: Path) -> None:
    grader = _grader(tmp_path, tag_context={"Q1": {"error_type": ["辅助线思路缺失"]}})

    result = grader._validate_and_convert(
        _payload(
            score=5,
            primary="辅助线思路缺失",
            secondary_errors=[
                {"category": "逻辑断裂", "summary": "辅助线思路缺失", "evidence": ""}
            ],
        ),
        expected_student_name="学生甲",
    )

    detail = result.grading_details[0]
    assert detail.error_category is None
    assert detail.error_summary is None
    assert detail.secondary_errors == []


def test_grading_service_projects_current_question_tags(tmp_path: Path) -> None:
    grading_db = DBManager(tmp_path / "grading.db")
    grading_db.initialize()
    question_bank_db = tmp_path / "question_bank.db"
    initialize_database(question_bank_db)
    with connect(question_bank_db) as conn:
        conn.execute(
            "INSERT INTO questions (id, question_number, question_text) VALUES (101, '1', '题目')"
        )
        conn.execute(
            "INSERT INTO question_tags (question_id, tag_type, tag_value) "
            "VALUES (101, 'knowledge_point', '三角形全等')"
        )
    SourceQuestionLinkService(question_bank_db).confirm_link(
        grading_session_id=7,
        source_question_id="Q1",
        bank_question_id=101,
        link_method="paper_question_number",
    )
    service = GradingService(
        grading_db,
        _FakeLLMClient(),
        question_bank_db_path=question_bank_db,
    )

    assert service._question_tag_context(7, {"questions": [{"question_id": "Q1"}]}) == {
        "Q1": {"knowledge_point": ["三角形全等"]}
    }


def test_hybrid_prompt_uses_tag_context_and_omits_knowledge_output_fields() -> None:
    spec = MajorQuestionSpec(
        question_id="Q10",
        detail_question_ids=["10-1"],
        rubric={
            "question_id": "Q10",
            "knowledge_id": "LEGACY_KNOWLEDGE",
            "parts": [{"part_id": "10-1", "part_score": 5}],
        },
        answer_key={"question_id": "Q10"},
        max_score=5,
    )

    prompts = build_hybrid_major_prompt(
        spec,
        {"items": []},
        question_tag_context={"10-1": {"knowledge_point": ["三角形全等"]}},
    )
    prompt = "\n".join(prompts)

    assert "三角形全等" in prompt
    assert "secondary_errors" in prompt
    assert "knowledge_id" not in prompt
    assert "knowledge_ids" not in prompt
    assert "LEGACY_KNOWLEDGE" not in prompt


def test_hybrid_validation_uses_the_same_secondary_error_rules() -> None:
    spec = MajorQuestionSpec(
        question_id="Q10",
        detail_question_ids=["10-1"],
        rubric={"question_id": "Q10", "parts": [{"part_id": "10-1", "part_score": 5}]},
        answer_key={"question_id": "Q10"},
        max_score=5,
    )
    manifest = {
        "items": [
            {"paper_key": "paper-1", "student_id": 1, "student_name": "学生甲", "sub_items": []}
        ]
    }
    response = {
        "question_id": "Q10",
        "items": [
            {
                "paper_key": "paper-1",
                "student_id": 1,
                "grading_details": [
                    {
                        "question_id": "10-1",
                        "score_awarded": 2,
                        "deduction_reason": "作答证据",
                        "confidence_score": 95,
                        "error_category": "逻辑断裂",
                        "error_summary": "辅助线思路缺失",
                        "secondary_errors": [
                            {"category": "计算错误", "summary": "符号抄错", "evidence": "第二行"}
                        ],
                    }
                ],
            }
        ],
    }

    accepted, failed = validate_hybrid_major_response(
        response,
        manifest,
        spec,
        question_tag_context={"10-1": {"error_type": ["辅助线思路缺失"]}},
    )

    assert failed == []
    detail = accepted[0]["details"][0]
    assert detail.knowledge_id == "UNKNOWN"
    assert detail.knowledge_ids == []
    assert [(item.category, item.summary) for item in detail.secondary_errors] == [
        ("其他", "符号抄错")
    ]
