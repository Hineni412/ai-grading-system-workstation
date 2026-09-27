from __future__ import annotations


from choice_recognition_chain import (
    score_choice_by_program,
)


def test_score_choice_by_program_matches_any_accepted_form() -> None:
    result = score_choice_by_program(
        selected="C",
        standard_answer=["B", "C"],
        max_score=8,
        confidence=0.95,
    )

    assert result["is_correct"] is True
    assert result["score"] == 8
    assert result["auto_scored"] is True
    assert result["need_review"] is False


def test_explicit_empty_choice_is_blank_but_missing_or_unclear_answer_needs_review() -> (
    None
):
    for answer in ("", "blank", "   "):
        result = score_choice_by_program(answer, "A", 5, 1.0)
        assert result["score"] == 0
        assert result["need_review"] is False
    for answer in (None, "unclear", "multiple"):
        assert score_choice_by_program(answer, "A", 5, 1.0)["need_review"] is True
