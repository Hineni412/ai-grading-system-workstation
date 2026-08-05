from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from question_bank.database.schema import initialize_database
from question_bank.mastery.current import CurrentMastery, aggregate_current_mastery
from question_bank.relations.query_service import (
    CurrentGraphQuery,
    CurrentKnowledgeGraphQueryService,
)
from tests.current_knowledge_support import install_current_knowledge


def _profile() -> dict[str, object]:
    return {
        "scope": {"mode": "student", "student_ids": ["12"], "class_id": None},
        "exam_scope": {
            "mode": "current",
            "session_ids": [14],
            "sessions": [{"session_id": 14, "session_name": "合成考试"}],
        },
        "coverage": {"covered_items": 2, "total_items": 2, "missing_items": {}},
        "warnings": [],
        "diagnosis_identity": "question_tag",
        "_mastery_session_times": {"14": "2026-07-30T08:00:00+08:00"},
        "students": [
            {
                "student_id": 12,
                "weak_points": [
                    {
                        "knowledge_point": "一元一次方程",
                        "knowledge_key": "knowledge_point:一元一次方程",
                        "source_question_refs": [
                            {
                                "session_id": 14,
                                "question_id": "Q1",
                                "bank_question_id": 1,
                                "score_awarded": 6,
                                "full_score": 10,
                            }
                        ],
                        "deduction_count": 1,
                        "evidence_count": 1,
                        "tag_context": {},
                        "error_counts": {"primary": {}, "secondary": {}},
                    },
                    {
                        "knowledge_point": "未治理合成词",
                        "knowledge_key": "knowledge_point:未治理合成词",
                        "source_question_refs": [],
                    },
                ],
            }
        ],
    }


def _service(tmp_path: Path) -> CurrentKnowledgeGraphQueryService:
    database = tmp_path / "question-bank.db"
    initialize_database(database)
    install_current_knowledge(database)
    return CurrentKnowledgeGraphQueryService(
        database,
        clock=lambda: datetime(2026, 7, 30, 12, 0, tzinfo=UTC),
    )


def test_query_uses_current_nodes_relations_and_one_mastery(tmp_path: Path) -> None:
    payload = _service(tmp_path).query(
        _profile(),
        CurrentGraphQuery(
            knowledge_keys=("kp_alg_linear_equation",),
            prerequisite_depth=1,
        ),
    )
    assert payload["response_schema_version"] == "knowledge-graph-current"
    assert len(payload["response_version"]) == 64
    assert payload["current_standard"]["release_id"]
    direct = next(
        node for node in payload["nodes"]
        if node["stable_key"] == "kp_alg_linear_equation"
    )
    assert direct["mastery"]["status"] == "available"
    assert direct["mastery"]["exam_evidence_count"] == 1
    assert direct["mastery"]["training_evidence_count"] == 0
    assert "mastery_v1" not in direct and "mastery_v2" not in direct
    assert payload["counts"]["evidence_row_count"] == 1
    assert all(edge["basis_kind"] and edge["strength"] for edge in payload["edges"])


def test_unknown_evidence_is_excluded_and_unknown_request_is_reported(tmp_path: Path) -> None:
    payload = _service(tmp_path).query(
        _profile(),
        CurrentGraphQuery(knowledge_keys=("kp_missing_identity",)),
    )
    assert payload["nodes"] == []
    assert payload["edges"] == []
    assert payload["counts"]["evidence_row_count"] == 1
    assert payload["missing"] == [
        {
            "kind": "requested_identity_not_found",
            "stable_key": "kp_missing_identity",
            "count": 1,
        }
    ]


def test_evidence_is_current_named_and_paginated(tmp_path: Path) -> None:
    payload = _service(tmp_path).evidence(
        _profile(),
        stable_key="kp_alg_linear_equation",
        page=1,
        page_size=1,
    )
    assert payload["response_schema_version"] == "knowledge-graph-evidence-current"
    assert payload["total"] == 1
    assert payload["items"][0]["knowledge_label"] == "一元一次方程"


def test_group_mastery_uses_effective_weight_not_evidence_count() -> None:
    values = {
        ("A", "kp_alg_linear_equation"): CurrentMastery(
            stable_key="kp_alg_linear_equation",
            display_name="一元一次方程",
            status="available",
            value=0.2,
            evidence_count=10,
            effective_weight=1.0,
            parameter_version="a" * 64,
        ),
        ("B", "kp_alg_linear_equation"): CurrentMastery(
            stable_key="kp_alg_linear_equation",
            display_name="一元一次方程",
            status="available",
            value=0.8,
            evidence_count=1,
            effective_weight=3.0,
            parameter_version="a" * 64,
        ),
    }

    group = aggregate_current_mastery(values)["kp_alg_linear_equation"]

    assert group.value == 0.65
    assert group.effective_weight == 4.0


def test_training_only_mastery_is_projected_as_graph_evidence(
    tmp_path: Path,
) -> None:
    class TrainingOnlyMasteryCalculator:
        def calculate(
            self,
            *_args: object,
            **_kwargs: object,
        ) -> dict[tuple[str, str], CurrentMastery]:
            return {
                ("12", "kp_alg_linear_equation"): CurrentMastery(
                    stable_key="kp_alg_linear_equation",
                    display_name="一元一次方程",
                    status="available",
                    value=0.75,
                    evidence_count=1,
                    effective_weight=1.0,
                    parameter_version="a" * 64,
                    contributing_student_count=1,
                    exam_evidence_count=0,
                    training_evidence_count=1,
                )
            }

    database = tmp_path / "question-bank.db"
    initialize_database(database)
    install_current_knowledge(database)
    calculator = TrainingOnlyMasteryCalculator()
    service = CurrentKnowledgeGraphQueryService(
        database,
        mastery_calculator=calculator,  # type: ignore[arg-type]
    )
    profile = _profile()
    profile["students"] = []

    payload = service.query(
        profile,
        CurrentGraphQuery(knowledge_keys=("kp_alg_linear_equation",)),
    )

    node = next(
        item for item in payload["nodes"]
        if item["stable_key"] == "kp_alg_linear_equation"
    )
    assert payload["warnings"] == []
    assert node["mastery"]["status"] == "available", node["mastery"]
    assert node["mastery"]["exam_evidence_count"] == 0
    assert node["mastery"]["training_evidence_count"] == 1, payload
    assert node["evidence"]["student_count"] == 1
    assert node["evidence"]["item_count"] == 1
    assert node["missing_reasons"] == []
