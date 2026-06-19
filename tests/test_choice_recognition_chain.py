from __future__ import annotations

from choice_recognition_chain import score_choice_by_program


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
