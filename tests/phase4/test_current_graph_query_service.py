from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from question_bank.database.schema import initialize_database
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
