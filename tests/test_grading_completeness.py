from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from ai_grader import AIGrader, QuestionGradingDetail
from grading_completeness import (
    audit_grading_details,
    major_question_id,
    major_question_ids_for_issues,
)
from hybrid_batch_grading_service import _build_result


class _FakeLLMClient:
    pass


@dataclass
class _DetailLike:
    question_id: str
    score_awarded: object


RUBRIC = {
    "total_score": 15,
    "questions": [
        {
            "question_id": "Q1",
            "question_type": "calculation",
            "max_score": 5,
            "knowledge_id": "K1",
        },
        {
            "question_id": "Q10",
            "question_type": "calculation",
            "max_score": 10,
            "knowledge_id": "K10",
            "parts": [
                {"part_id": "Q10(P1)", "part_score": 4, "knowledge_id": "K10-1"},
                {"part_id": "Q10(P2)", "part_score": 6, "knowledge_id": "K10-2"},
            ],
        },
    ],
}


def _full_result_payload(*details: dict[str, object]) -> dict[str, object]:
    return {
        "student_name": "student",
        "total_score": 15,
        "student_score": 15,
        "needs_human_review": False,
        "grading_details": list(details),
    }


def _detail(question_id: str, score_awarded: object) -> dict[str, object]:
    return {
        "question_id": question_id,
        "score_awarded": score_awarded,
        "deduction_reason": "",
        "knowledge_id": "K",
        "knowledge_ids": ["K"],
    }


def _grader(tmp_path: Path) -> AIGrader:
    rubric_path = tmp_path / "rubric.json"
    answer_key_path = tmp_path / "answer_key.json"
    rubric_path.write_text(json.dumps(RUBRIC, ensure_ascii=False), encoding="utf-8")
    answer_key_path.write_text(json.dumps({"questions": []}, ensure_ascii=False), encoding="utf-8")
    return AIGrader(rubric_path, _FakeLLMClient(), answer_key_path=answer_key_path)


def test_major_question_id_normalizes_supported_sub_question_spellings() -> None:
    assert major_question_id(RUBRIC, "Q10(1)") == "Q10"
    assert major_question_id(RUBRIC, "10-2") == "Q10"
    assert major_question_id(RUBRIC, "Q1") == "Q1"
    assert major_question_id(RUBRIC, "Q99") is None


def test_audit_detects_missing_part_with_equivalent_sub_question_spelling() -> None:
    audit = audit_grading_details(
        RUBRIC,
        [
            _detail("Q1", 5),
            _detail("Q10(1)", 4),
        ],
    )

    assert audit["status"] == "incomplete"
    assert audit["missing_question_ids"] == ["Q10(P2)"]
    assert audit["duplicate_question_ids"] == []
    assert audit["unexpected_question_ids"] == []
    assert audit["score_out_of_range"] == []
    assert audit["affected_major_question_ids"] == ["Q10"]
    assert major_question_ids_for_issues(audit) == ["Q10"]


def test_audit_uses_the_shared_case_insensitive_legacy_parser() -> None:
    audit = audit_grading_details(
        RUBRIC,
        [
            _detail("Q1", 5),
            _detail("q10(1)", 4),
            _detail("10-2", 6),
        ],
    )

    assert audit["status"] == "complete"
    assert audit["missing_question_ids"] == []
    assert audit["unexpected_question_ids"] == []
    assert audit["affected_major_question_ids"] == []


def test_audit_marks_parent_substitution_unexpected_and_invalid() -> None:
    audit = audit_grading_details(
        RUBRIC,
        [
            _detail("Q1", 5),
            _detail("Q10", 10),
        ],
    )

    assert audit["status"] == "invalid"
    assert audit["missing_question_ids"] == ["Q10(P1)", "Q10(P2)"]
    assert audit["duplicate_question_ids"] == []
    assert audit["unexpected_question_ids"] == ["Q10"]
    assert audit["affected_major_question_ids"] == ["Q10"]


def test_audit_detects_duplicates_and_scores_above_exact_part_maximum() -> None:
    audit = audit_grading_details(
        RUBRIC,
        [
            _detail("Q1", 5),
            _detail("10-1", 5),
            _detail("Q10(1)", 4),
            _detail("10-2", 6),
        ],
    )

    assert audit["status"] == "invalid"
    assert audit["missing_question_ids"] == []
    assert audit["duplicate_question_ids"] == ["Q10(P1)"]
    assert audit["unexpected_question_ids"] == []
    assert audit["score_out_of_range"] == [
        {
            "question_id": "Q10(P1)",
            "score_awarded": 5.0,
            "max_score": 4.0,
            "reason": "above_max",
        }
    ]
    assert audit["affected_major_question_ids"] == ["Q10"]


def test_audit_accepts_dataclass_like_details_and_returns_complete() -> None:
    details = [
        QuestionGradingDetail("Q1", 5, "", "K1", knowledge_ids=["K1"]),
        _DetailLike("Q10(1)", 4),
        {"question_id": "10-2", "score_awarded": 6},
    ]

    audit = audit_grading_details(RUBRIC, details)

    assert audit == {
        "status": "complete",
        "missing_question_ids": [],
        "duplicate_question_ids": [],
        "unexpected_question_ids": [],
        "score_out_of_range": [],
        "affected_major_question_ids": [],
    }


def test_audit_marks_non_numeric_scores_invalid() -> None:
    audit = audit_grading_details(
        RUBRIC,
        [
            _detail("Q1", "not-a-number"),
            _detail("10-1", 4),
            _detail("10-2", 6),
        ],
    )

    assert audit["status"] == "invalid"
    assert audit["score_out_of_range"] == [
        {
            "question_id": "Q1",
            "score_awarded": "not-a-number",
            "max_score": 5.0,
            "reason": "non_numeric",
        }
    ]
    assert audit["affected_major_question_ids"] == ["Q1"]


def test_audit_marks_negative_scores_invalid() -> None:
    audit = audit_grading_details(
        RUBRIC,
        [
            _detail("Q1", 5),
            _detail("10-1", -1),
            _detail("10-2", 6),
        ],
    )

    assert audit["status"] == "invalid"
    assert audit["score_out_of_range"] == [
        {
            "question_id": "Q10(P1)",
            "score_awarded": -1.0,
            "max_score": 4.0,
            "reason": "negative",
        }
    ]
    assert audit["affected_major_question_ids"] == ["Q10"]


def test_full_paper_validation_rejects_incomplete_multipart_results(tmp_path: Path) -> None:
    grader = _grader(tmp_path)

    with pytest.raises(ValueError, match=r"Q10\(P2\)"):
        grader._validate_and_convert(
            _full_result_payload(
                _detail("Q1", 5),
                _detail("Q10(1)", 4),
            ),
            expected_student_name="student",
        )


def test_full_paper_validation_stores_rubric_exact_ids_for_equivalent_parts(tmp_path: Path) -> None:
    grader = _grader(tmp_path)

    result = grader._validate_and_convert(
        _full_result_payload(
            _detail("Q1", 5),
            _detail("Q10(1)", 4),
            _detail("Q10(2)", 6),
        ),
        expected_student_name="student",
    )

    assert [detail.question_id for detail in result.grading_details] == [
        "Q1",
        "Q10(P1)",
        "Q10(P2)",
    ]
    assert result.raw_json["grading_completeness"]["status"] == "complete"


def test_hybrid_build_result_records_completeness_and_forces_review_for_non_complete_results() -> None:
    fallback_items = [
        {
            "paper_key": "paper-1",
            "question_id": "Q10",
            "reason": "missing_detail_question_ids",
        }
    ]
    result = _build_result(
        student_name="student",
        rubric=RUBRIC,
        total_score=15,
        details=[
            QuestionGradingDetail("Q1", 5, "", "K1", confidence_score=95, knowledge_ids=["K1"]),
            QuestionGradingDetail("10-1", 4, "", "K10-1", confidence_score=95, knowledge_ids=["K10-1"]),
        ],
        metadata=[
            {"question_id": "Q1", "candidate_scores": []},
            {"question_id": "10-1", "candidate_scores": []},
        ],
        paper_key="paper-1",
        fallback_items=fallback_items,
    )

    assert result.needs_human_review is True
    assert result.raw_json["grading_completeness"] == {
        "status": "incomplete",
        "missing_question_ids": ["Q10(P2)"],
        "duplicate_question_ids": [],
        "unexpected_question_ids": [],
        "score_out_of_range": [],
        "affected_major_question_ids": ["Q10"],
    }
    assert result.raw_json["hybrid_batch_fallback"] == {
        "mode": "partial_failure",
        "items": fallback_items,
    }
