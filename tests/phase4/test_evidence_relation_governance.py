from __future__ import annotations

from pathlib import Path

from question_bank.database.schema import initialize_database
from question_bank.relations.evidence_governance import (
    EvidenceRelationGovernanceService,
)
from question_bank.relations.repository import KnowledgeRelationRepository


def _hint(
    question_id: int,
    point_id: str,
    *,
    confidence: float = 0.99,
) -> dict[str, object]:
    return {
        "question_id": question_id,
        "evidence_point_id": point_id,
        "source_keys": ["kp_alg_linear_equation"],
        "target_keys": ["kp_alg_equation_properties"],
        "confidence": confidence,
        "model_name": "synthetic-combined-model",
    }


def test_high_confidence_repeated_evidence_enters_active_graph(tmp_path: Path) -> None:
    database = tmp_path / "question-bank.db"
    initialize_database(database)
    service = EvidenceRelationGovernanceService(database)

    result = service.govern(
        [_hint(101, "point-1"), _hint(102, "point-2")],
        operation_id="synthetic-tagging-operation",
    )

    assert result["auto_confirmed_count"] == 1
    assert result["exception_count"] == 0
    active = KnowledgeRelationRepository(database).list_active_relations()
    assert [(item.source_key, item.target_key) for item in active] == [
        ("kp_alg_linear_equation", "kp_alg_equation_properties")
    ]


def test_single_or_ambiguous_evidence_only_creates_exception(tmp_path: Path) -> None:
    database = tmp_path / "question-bank.db"
    initialize_database(database)
    service = EvidenceRelationGovernanceService(database)
    ambiguous = _hint(102, "point-2")
    ambiguous["target_keys"] = [
        "kp_alg_equation_properties",
        "kp_alg_real_numbers",
    ]

    result = service.govern(
        [_hint(101, "point-1"), ambiguous],
        operation_id="synthetic-tagging-operation",
    )

    assert result["auto_confirmed_count"] == 0
    assert result["exception_count"] == 2
    assert result["skipped_ambiguous_count"] == 1
    assert all(
        "ambiguous_mapping" in item["conflict_codes"]
        for item in result["outcomes"]
    )


def test_two_points_from_one_question_do_not_auto_confirm(tmp_path: Path) -> None:
    database = tmp_path / "question-bank.db"
    initialize_database(database)
    service = EvidenceRelationGovernanceService(database)

    result = service.govern(
        [_hint(101, "point-1"), _hint(101, "point-2")],
        operation_id="one-question-operation",
    )

    assert result["auto_confirmed_count"] == 0
    assert result["exception_count"] == 1
    assert result["outcomes"][0]["evidence_count"] == 1


def test_separate_tagging_operations_accumulate_cross_question_support(
    tmp_path: Path,
) -> None:
    database = tmp_path / "question-bank.db"
    initialize_database(database)
    service = EvidenceRelationGovernanceService(database)

    first = service.govern([_hint(101, "point-1")], operation_id="operation-1")
    second = service.govern([_hint(102, "point-2")], operation_id="operation-2")

    assert first["exception_count"] == 1
    assert second["auto_confirmed_count"] == 1, second["outcomes"]
    assert second["outcomes"][0]["question_ids"] == [101, 102]


def test_low_confidence_relation_is_visible_as_an_exception(tmp_path: Path) -> None:
    database = tmp_path / "question-bank.db"
    initialize_database(database)
    service = EvidenceRelationGovernanceService(database)

    result = service.govern(
        [_hint(101, "point-1", confidence=0.61)],
        operation_id="low-confidence-operation",
    )

    assert result["exception_count"] == 1
    assert result["skipped_low_confidence_count"] == 1
    assert result["outcomes"][0]["conflict_codes"] == ["low_confidence"]


def test_retry_reuses_the_confirmed_relation_without_duplicate_write(
    tmp_path: Path,
) -> None:
    database = tmp_path / "question-bank.db"
    initialize_database(database)
    service = EvidenceRelationGovernanceService(database)
    hints = [_hint(101, "point-1"), _hint(102, "point-2")]
    service.govern(hints, operation_id="synthetic-tagging-operation")

    repeated = service.govern(
        hints,
        operation_id="synthetic-tagging-operation",
    )

    assert repeated["reused_count"] == 1
    assert len(KnowledgeRelationRepository(database).list_relations()) == 1
