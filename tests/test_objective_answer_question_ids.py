from __future__ import annotations

import pytest

import objective_answer_loader
from objective_answer_utils import get_standard_answer_for_question
from question_id_contract import QuestionIdContractError


@pytest.mark.parametrize(
    ("raw_question_id", "expected"),
    [
        ("第12题", "Q12"),
        ("Q12(2)", "Q12(P2)"),
        ("Q12-2", "Q12(P2)"),
        ("Q12_2", "Q12(P2)"),
        ("12.2", "Q12(P2)"),
    ],
)
def test_objective_answer_loader_normalizes_legacy_aliases_to_canonical_ids(
    raw_question_id: str,
    expected: str,
) -> None:
    assert objective_answer_loader._normalize_question_id(raw_question_id) == expected


def test_objective_answer_loader_rejects_alias_collision_within_one_source() -> None:
    with pytest.raises(QuestionIdContractError, match="Q12\\(P2\\)"):
        objective_answer_loader._extract_answers_from_data(
            {
                "Q12(2)": "A",
                "Q12-2": "B",
            },
            "answer_key.json",
        )


def test_objective_answer_loader_reads_legacy_key_from_cached_answer_map() -> None:
    answer = objective_answer_loader.get_standard_answer_for_question(
        {
            "answers_by_question_id": {
                "Q12(2)": {
                    "standard_answer": "B",
                    "source": "legacy.json",
                    "field": "answer",
                }
            }
        },
        "Q12(P2)",
    )

    assert answer == ("B", "legacy.json", "answer")


def test_objective_answer_loader_fails_closed_on_cached_alias_collision() -> None:
    answer = objective_answer_loader.get_standard_answer_for_question(
        {
            "answers_by_question_id": {
                "Q12(2)": {"standard_answer": "A"},
                "Q12-2": {"standard_answer": "B"},
            }
        },
        "Q12(P2)",
    )

    assert answer == ("", "", "")


def test_objective_answer_loader_folds_legacy_part_one_to_single_detail() -> None:
    answer = objective_answer_loader.get_standard_answer_for_question(
        {
            "answers_by_question_id": {
                "Q12": {
                    "standard_answer": "A",
                    "source": "single.json",
                    "field": "answer",
                }
            }
        },
        "Q12(1)",
    )

    assert answer == ("A", "single.json", "answer")


def test_objective_answer_lookup_selects_the_requested_canonical_part() -> None:
    rubric = {
        "questions": [
            {
                "question_id": "Q12",
                "parts": [
                    {"part_id": "Q12(1)", "standard_answer": "A"},
                    {"part_id": "Q12-2", "standard_answer": "B"},
                ],
            }
        ]
    }

    assert get_standard_answer_for_question(rubric, "Q12_2") == (
        "B",
        "rubric_part",
    )


def test_objective_answer_lookup_fails_closed_on_duplicate_part_aliases() -> None:
    rubric = {
        "questions": [
            {
                "question_id": "Q12",
                "parts": [
                    {"part_id": "Q12(1)", "standard_answer": "A"},
                    {"part_id": "Q12-1", "standard_answer": "B"},
                ],
            }
        ]
    }

    assert get_standard_answer_for_question(rubric, "Q12(P1)") == (
        None,
        "missing",
    )
