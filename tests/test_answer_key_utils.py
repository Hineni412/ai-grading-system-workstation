from __future__ import annotations

import pytest

from ai_grader import _answer_key_forms_for_question
from answer_key_utils import answer_forms_for_question, answer_forms_map


def test_question_prefers_non_empty_accepted_forms_then_canonical_answer() -> None:
    answer_key = {
        "questions": [
            {
                "question_id": "Q9",
                "canonical_answer": "y=48x+20",
                "accepted_forms": ["y=48x+20", "y=20+48x", "48x+20=y", ""],
                "standard_answer": "legacy answer",
            }
        ]
    }

    assert answer_forms_for_question(answer_key, "Q9") == ["y=48x+20", "y=20+48x", "48x+20=y"]


def test_part_question_resolves_part_accepted_forms_before_part_fallback_answer() -> None:
    answer_key = {
        "questions": [
            {
                "question_id": "Q10",
                "parts": [
                    {
                        "part_id": "Q10(1)",
                        "answer": "4",
                        "accepted_forms": ["4", "四"],
                    }
                ],
            }
        ]
    }

    assert answer_forms_for_question(answer_key, "Q10(1)") == ["4", "四"]


@pytest.mark.parametrize(
    ("answer_field", "answer_value", "expected_forms"),
    [
        ("standard_answer", "B", ["B"]),
        ("correct_answer", "C", ["C"]),
        ("answer", "D", ["D"]),
        ("answers", ["A", "B"], ["A", "B"]),
        ("reference_answer", "E", ["E"]),
    ],
)
def test_legacy_only_question_falls_back_to_legacy_answer_fields(
    answer_field: str,
    answer_value: str | list[str],
    expected_forms: list[str],
) -> None:
    answer_key = {"questions": [{"question_id": "Q3", answer_field: answer_value}]}

    assert answer_forms_for_question(answer_key, "Q3") == expected_forms


def test_legacy_fallback_uses_only_first_non_empty_field() -> None:
    answer_key = {
        "questions": [
            {
                "question_id": "Q3",
                "standard_answer": "B",
                "correct_answer": "C",
            }
        ]
    }

    assert answer_forms_for_question(answer_key, "Q3") == ["B"]


def test_answer_forms_map_includes_parent_and_part_question_ids() -> None:
    answer_key = {
        "questions": [
            {
                "question_id": "Q9",
                "canonical_answer": "y=48x+20",
                "accepted_forms": ["y=48x+20", "y=20+48x", "48x+20=y", ""],
            },
            {
                "question_id": "Q10",
                "parts": [{"question_id": "Q10(1)", "accepted_forms": ["4", "四"], "answer": "4"}],
            },
        ]
    }

    assert answer_forms_map(answer_key) == {
        "Q9": ["y=48x+20", "y=20+48x", "48x+20=y"],
        "Q10(1)": ["4", "四"],
    }


def test_ai_grader_lookup_delegates_to_shared_answer_forms_resolver() -> None:
    answer_key = {
        "questions": [
            {
                "question_id": "Q9",
                "canonical_answer": "y=48x+20",
                "accepted_forms": ["y=48x+20", "y=20+48x", "48x+20=y", ""],
                "standard_answer": "legacy answer",
            }
        ]
    }

    assert _answer_key_forms_for_question(answer_key, "Q9") == answer_forms_for_question(answer_key, "Q9")
