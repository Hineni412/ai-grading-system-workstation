from __future__ import annotations

from backend.scan_grading.grading_completeness import (
    audit_grading_details,
)


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


def _detail(question_id: str, score_awarded: object) -> dict[str, object]:
    return {
        "question_id": question_id,
        "score_awarded": score_awarded,
        "deduction_reason": "",
        "knowledge_id": "K",
        "knowledge_ids": ["K"],
    }


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


def test_incomplete_multipart_results_are_flagged_missing() -> None:
    audit = audit_grading_details(
        RUBRIC,
        [
            _detail("Q1", 5),
            _detail("Q10(1)", 4),
        ],
    )

    assert audit["status"] == "incomplete"
    assert audit["missing_question_ids"] == ["Q10(P2)"]
    assert audit["affected_major_question_ids"] == ["Q10"]
