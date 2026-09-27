from __future__ import annotations

"""Hybrid batch validation tolerance and retry regressions."""

from hybrid_batch_grading_service import (
    MajorQuestionSpec,
    grade_major_question_batch,
    validate_hybrid_major_response,
)


def test_symbolic_and_expanded_proofs_preserve_the_model_score_without_literal_answer_matching() -> (
    None
):
    spec = MajorQuestionSpec(
        question_id="Q1",
        detail_question_ids=["Q1(P1)"],
        rubric={
            "question_id": "Q1",
            "question_type": "proof",
            "max_score": 6,
            "parts": [
                {
                    "part_id": "Q1(P1)",
                    "part_score": 6,
                    "response_mode": "process_required",
                    "allow_alternative_methods": False,
                    "answer_only_max_score": 0,
                    "steps": [
                        {
                            "step_id": "S1",
                            "step_score": 3,
                            "core_goal": "核验三边平方关系",
                        },
                        {
                            "step_id": "S2",
                            "step_score": 3,
                            "core_goal": "据逆定理得到直角结论",
                        },
                    ],
                }
            ],
        },
        answer_key={"canonical_answer": "5²+12²=13²，三角形为直角三角形"},
        max_score=6,
    )
    manifest = {
        "items": [
            {
                "paper_key": "synthetic_proof",
                "student_id": 1,
                "target_detail_question_ids": ["Q1(P1)"],
            }
        ]
    }
    # The model owns mathematical evaluation; this validates score/evidence
    # transport, including a rejected answer, without asserting LLM accuracy.
    for observed, score in (
        ("AB=5、AC=12、BC=13；5²+12²=13²，故△ABC是直角三角形。", 6),
        ("AB=5、AC=12、BC=13；AB²+AC²=BC²，故△ABC是直角三角形。", 6),
        ("AB=5、AC=12、BC=13；BC²=AC²+AB²，故∠A=90°。", 6),
        ("AB=5、AC=12、BC=14；AB²+AC²=BC²，故△ABC是直角三角形。", 0),
    ):
        detail = {
            "question_id": "Q1(P1)",
            "score_awarded": score,
            "observed_answer": observed,
            "evidence_steps": [observed],
            "confidence_score": 95,
            "needs_human_review": False,
            "answer_is_blank_or_no_valid_work": False,
            "step_assessments": [
                {
                    "step_id": step_id,
                    "achievement": "full" if score else "none",
                    "score_awarded": 3 if score else 0,
                    "student_evidence": observed,
                    "missing_or_error": "" if score else "给定三边不满足平方关系",
                    "reason": "平方关系与结论成立"
                    if score
                    else "关系不成立，不能支持结论",
                }
                for step_id in ("S1", "S2")
            ],
        }
        response = {
            "question_id": "Q1",
            "items": [
                {
                    "paper_key": "synthetic_proof",
                    "student_id": 1,
                    "grading_details": [detail],
                }
            ],
        }
        accepted, failed = validate_hybrid_major_response(response, manifest, spec)
        assert failed == []
        assert accepted[0]["details"][0].score_awarded == score
        assert accepted[0]["metadata"][0]["observed_answer"] == observed


_PROCESS_SPEC = MajorQuestionSpec(
    question_id="Q1",
    detail_question_ids=["Q1(P1)"],
    rubric={
        "question_id": "Q1",
        "question_type": "proof",
        "max_score": 6,
        "parts": [
            {
                "part_id": "Q1(P1)",
                "part_score": 6,
                "response_mode": "process_required",
                "steps": [
                    {"step_id": "S1", "step_score": 3, "core_goal": "核验条件"},
                    {"step_id": "S2", "step_score": 3, "core_goal": "得到结论"},
                ],
            }
        ],
    },
    answer_key={"canonical_answer": "结论成立"},
    max_score=6,
)


SPEC = MajorQuestionSpec(
    question_id="Q12",
    detail_question_ids=["Q12(P1)", "Q12(P2)", "Q12(P3)"],
    rubric={"question_id": "Q12", "max_score": 18},
    answer_key={},
    max_score=18,
)

MANIFEST = {
    "items": [
        {
            "paper_key": "paper_a",
            "student_id": 1,
            "target_detail_question_ids": ["Q12(P1)", "Q12(P2)", "Q12(P3)"],
        },
        {
            "paper_key": "paper_b",
            "student_id": 2,
            "target_detail_question_ids": ["Q12(P1)", "Q12(P2)", "Q12(P3)"],
        },
    ]
}


def _detail(qid: str, score: float, **extra) -> dict:
    detail = {
        "question_id": qid,
        "score_awarded": score,
        "confidence_score": 95,
        "answer_discarded_by_smudge": False,
        "answer_is_blank_or_no_valid_work": False,
        "needs_human_review": False,
        "deduction_reason": "",
    }
    detail.update(extra)
    return detail


def _item(paper_key: str, student_id: int, details: list[dict]) -> dict:
    return {
        "paper_key": paper_key,
        "student_id": student_id,
        "grading_details": details,
    }


def test_single_invalid_detail_no_longer_voids_siblings() -> None:
    response = {
        "question_id": "Q12",
        "items": [
            _item(
                "paper_a",
                1,
                [
                    _detail("Q12(P1)", 5),
                    {"question_id": "Q12(P2)", "score_awarded": None},
                    _detail("Q12(P3)", 6),
                ],
            ),
        ],
    }

    accepted, failed = validate_hybrid_major_response(response, MANIFEST, SPEC)

    assert len(accepted) == 1
    assert {d.question_id for d in accepted[0]["details"]} == {"Q12(P1)", "Q12(P3)"}
    partial = [item for item in failed if item["paper_key"] == "paper_a"]
    assert len(partial) == 1
    assert partial[0]["target_detail_question_ids"] == ["Q12(P2)"]
