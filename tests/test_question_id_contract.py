from copy import deepcopy

import pytest

from question_id_contract import (
    QuestionIdCatalog,
    QuestionIdContractError,
    canonicalize_question_document,
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
