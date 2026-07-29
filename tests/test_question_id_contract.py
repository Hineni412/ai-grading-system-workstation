from copy import deepcopy

import pytest

from question_id_contract import (
    QuestionIdCatalog,
    QuestionIdContractError,
    canonical_part_id,
    canonicalize_grading_config_payload,
    canonicalize_question_document,
    resolve_known_question_id,
)


def legacy_document() -> dict:
    return {
        "questions": [
            {"question_id": "Q1", "parts": [{"part_id": "P1"}]},
            {
                "question_id": "Q12",
                "parts": [
                    {"part_id": "P1"},
                    {"part_id": "Q12(2)"},
                    {"part_id": "Q12_P3"},
                ],
            },
            {
                "question_id": "Q13",
                "parts": [{"part_id": "P1"}, {"part_id": "P2"}],
            },
        ]
    }


def test_catalog_scopes_legacy_parts_to_parent() -> None:
    catalog = QuestionIdCatalog.from_document(legacy_document())

    assert catalog.detail_ids == (
        "Q1",
        "Q12(P1)",
        "Q12(P2)",
        "Q12(P3)",
        "Q13(P1)",
        "Q13(P2)",
    )
    assert catalog.resolve("P1", parent_id="Q12") == "Q12(P1)"
    assert catalog.resolve("P1", parent_id="Q13") == "Q13(P1)"
    assert catalog.resolve("P1") is None
    assert catalog.resolve("Q12(1)") == "Q12(P1)"
    assert catalog.resolve("Q12-2") == "Q12(P2)"
    assert catalog.expand("Q12") == ("Q12(P1)", "Q12(P2)", "Q12(P3)")


def test_canonicalize_returns_copy_without_mutating_input() -> None:
    original = legacy_document()
    before = deepcopy(original)

    normalized = canonicalize_question_document(original)

    assert original == before
    assert normalized["questions"][0]["parts"][0]["part_id"] == "Q1"
    assert normalized["questions"][1]["parts"][0]["part_id"] == "Q12(P1)"


def test_duplicate_canonical_parts_are_rejected() -> None:
    payload = {
        "questions": [
            {
                "question_id": "Q12",
                "parts": [{"part_id": "P1"}, {"part_id": "Q12(1)"}],
            }
        ]
    }

    with pytest.raises(QuestionIdContractError, match=r"Q12\(P1\)"):
        QuestionIdCatalog.from_document(payload)


def test_document_normalization_rewrites_parent_and_part_identities() -> None:
    normalized = canonicalize_question_document(
        {
            "questions": [
                {
                    "question_id": "12",
                    "parts": [
                        {"part_id": "P1"},
                        {"part_id": "Q12_2"},
                    ],
                }
            ]
        }
    )

    assert normalized["questions"][0]["question_id"] == "Q12"
    assert [
        part["part_id"] for part in normalized["questions"][0]["parts"]
    ] == ["Q12(P1)", "Q12(P2)"]


def test_opaque_multi_part_identity_is_rejected_without_position_guess() -> None:
    with pytest.raises(QuestionIdContractError, match="无法解析"):
        QuestionIdCatalog.from_document(
            {
                "questions": [
                    {
                        "question_id": "Q12",
                        "parts": [
                            {"part_id": "老师-甲"},
                            {"part_id": "Q12(P2)"},
                        ],
                    }
                ]
            }
        )


def test_single_part_does_not_treat_a_different_part_number_as_the_parent() -> None:
    catalog = QuestionIdCatalog.from_document(
        {
            "questions": [
                {
                    "question_id": "Q1",
                    "parts": [{"part_id": "Q1(P1)"}],
                }
            ]
        }
    )

    assert catalog.resolve("Q1(1)") == "Q1"
    assert catalog.resolve("Q1(P2)") is None


def test_grading_config_normalization_fails_closed_on_answer_alias_collision() -> None:
    payload = {
        "rubric": {
            "questions": [
                {
                    "question_id": "Q12",
                    "parts": [
                        {"part_id": "P1"},
                        {"part_id": "P2"},
                    ],
                }
            ]
        },
        "answer_key": {
            "questions": [
                {
                    "question_id": "12",
                    "parts": [
                        {"part_id": "Q12(1)"},
                        {"part_id": "Q12_P1"},
                    ],
                }
            ]
        },
    }

    with pytest.raises(QuestionIdContractError, match=r"Q12\(P1\)"):
        canonicalize_grading_config_payload(payload)


def test_known_question_resolution_prefers_the_current_detail_over_its_parent() -> None:
    known_ids = {"Q2", "Q2(P1)", "Q2(P2)"}

    assert resolve_known_question_id("Q2-1", known_ids) == "Q2(P1)"
    assert resolve_known_question_id("q2_2", known_ids) == "Q2(P2)"
    assert resolve_known_question_id("2", known_ids) == "Q2"
    assert resolve_known_question_id("Q2(P3)", known_ids) is None


def test_known_question_resolution_accepts_only_part_one_for_a_single_detail() -> None:
    known_ids = {"Q1"}

    assert resolve_known_question_id("Q1(1)", known_ids) == "Q1"
    assert resolve_known_question_id("Q1(P2)", known_ids) is None


def test_known_question_resolution_fails_closed_when_known_aliases_collide() -> None:
    known_ids = {"Q12(P1)", "Q12(1)"}

    assert resolve_known_question_id("Q12-1", known_ids) is None


@pytest.mark.parametrize("invalid_part_number", [True, 0, -1, 1.5, "1"])
def test_canonical_part_builder_rejects_non_integer_part_numbers(
    invalid_part_number: object,
) -> None:
    with pytest.raises(QuestionIdContractError, match="正整数"):
        canonical_part_id("Q12", invalid_part_number)  # type: ignore[arg-type]


def test_catalog_rejects_zero_part_number() -> None:
    with pytest.raises(QuestionIdContractError, match="无法解析"):
        QuestionIdCatalog.from_document(
            {
                "questions": [
                    {
                        "question_id": "Q12",
                        "parts": [
                            {"part_id": "Q12(P0)"},
                            {"part_id": "Q12(P1)"},
                        ],
                    }
                ]
            }
        )
