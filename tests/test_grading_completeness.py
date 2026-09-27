from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from ai_grader import AIGrader, QuestionGradingDetail
from grading_completeness import (
    audit_grading_details,
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
    answer_key_path.write_text(
        json.dumps({"questions": []}, ensure_ascii=False), encoding="utf-8"
    )
    return AIGrader(rubric_path, _FakeLLMClient(), answer_key_path=answer_key_path)


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


def test_full_paper_validation_rejects_incomplete_multipart_results(
    tmp_path: Path,
) -> None:
    grader = _grader(tmp_path)

    with pytest.raises(ValueError, match=r"Q10\(P2\)"):
        grader._validate_and_convert(
            _full_result_payload(
                _detail("Q1", 5),
                _detail("Q10(1)", 4),
            ),
            expected_student_name="student",
        )
