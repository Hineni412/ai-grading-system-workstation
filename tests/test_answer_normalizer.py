from __future__ import annotations

import pytest

from answer_normalizer import match_fill_blank_answer


@pytest.mark.parametrize("student_answer", ["72°、54°", "54°，72°", "72度、54度"])
def test_complete_answer_set_accepts_all_values_in_any_order(student_answer: str) -> None:
    result = match_fill_blank_answer(student_answer, "72°或54°")

    assert result["matched"] is True


@pytest.mark.parametrize("student_answer", ["72°", "54°", "72°、54°、60°", "72°、60°"])
def test_complete_answer_set_rejects_missing_wrong_or_extra_values(student_answer: str) -> None:
    result = match_fill_blank_answer(student_answer, "72°或54°")

    assert result["matched"] is False
