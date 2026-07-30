from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.dependencies import get_request_diagnosis_profile_service
from backend.api.routers.graph import get_graph_v2_query_service
from question_bank.database.schema import initialize_database
from question_bank.relations.contracts import (
    KnowledgeRelation,
    RelationStatus,
    RelationType,
)
from question_bank.relations.query_service import KnowledgeGraphV2QueryService
from question_bank.relations.repository import KnowledgeRelationRepository


class FakeDiagnosisService:
    def build_tag_profiles(self, **_kwargs):
        return {
            "scope": {
                "mode": "student",
                "student_ids": ["12"],
                "class_id": None,
            },
            "exam_scope": {
                "mode": "current",
                "session_ids": [14],
                "sessions": [
                    {"session_id": 14, "session_name": "合成考试"}
                ],
            },
            "coverage": {
                "covered_items": 1,
                "total_items": 1,
                "missing_items": {},
            },
            "warnings": [],
            "diagnosis_identity": "question_tag",
            "students": [
                {
                    "student_id": 12,
                    "student_code": "S12",
                    "student_name": "合成学生",
                    "class_id": "八年级1班",
                    "weak_points": [
                        {
                            "knowledge_point": "一元一次方程",
                            "knowledge_key": "knowledge_point:一元一次方程",
                            "mastery": 0.6,
                            "deduction_count": 1,
                            "evidence_count": 1,
                            "actionable_reasons": ["合成原因"],
                            "tag_context": {},
                            "error_counts": {
                                "primary": {},
                                "secondary": {},
                            },
                            "source_question_refs": [
                                {
                                    "session_id": 14,
                                    "session_name": "合成考试",
                                    "question_id": "Q1",
                                    "bank_question_id": 1,
                                    "score_awarded": 6,
                                    "full_score": 10,
                                    "score_rate": 0.6,
                                }
                            ],
                        }
                    ],
                }
            ],
        }


def _client(
    tmp_path: Path,
) -> tuple[TestClient, KnowledgeRelationRepository]:
    database = tmp_path / "question-bank.db"
    initialize_database(database)
    repository = KnowledgeRelationRepository(database)
    suggested = repository.create_suggestion(
        KnowledgeRelation(
            "kp_alg_linear_equation",
            "kp_alg_equation_properties",
            RelationType.PREREQUISITE,
        ),
        source_kind="teacher",
        source_reference="teacher-synthetic",
        rationale="合成 API 先修关系",
    )
    repository.transition(
        suggested.relation_id,
        expected_revision=1,
        to_status=RelationStatus.CONFIRMED,
        actor_ref="teacher-synthetic",
        reason="合成确认",
    )
    app = create_app()
    app.dependency_overrides[
        get_request_diagnosis_profile_service
    ] = lambda: FakeDiagnosisService()
    app.dependency_overrides[
        get_graph_v2_query_service
    ] = lambda: KnowledgeGraphV2QueryService(database)
    return TestClient(app), repository


def _query_body() -> dict[str, object]:
    return {
        "scope": {"mode": "student", "student_ids": ["12"]},
        "exam_scope": {"mode": "current", "session_ids": [14]},
        "knowledge_keys": ["kp_alg_linear_equation"],
        "prerequisite_depth": 1,
    }


def test_graph_v2_api_returns_versioned_confirmed_graph_and_dual_mastery(
    tmp_path: Path,
) -> None:
    client, _repository = _client(tmp_path)

    response = client.post("/api/graph/v2/query", json=_query_body())

    assert response.status_code == 200
    payload = response.json()
    assert payload["response_schema_version"] == "knowledge-graph-v2"
    assert len(payload["response_version"]) == 64
    assert {node["stable_key"] for node in payload["nodes"]} == {
        "kp_alg_linear_equation",
        "kp_alg_equation_properties",
    }
    direct = next(
        node
        for node in payload["nodes"]
        if node["stable_key"] == "kp_alg_linear_equation"
    )
    assert direct["mastery_v1"] == {
        "status": "available",
        "value": 0.6,
        "evidence_count": 1,
        "reason": None,
    }
    assert direct["mastery_v2"]["status"] == "unavailable"
    assert payload["edges"][0]["relation_type"] == "prerequisite"
    assert payload["counts"] == {
        "node_count": 2,
        "edge_count": 1,
        "evidence_row_count": 1,
        "missing_count": 0,
    }


def test_graph_v2_evidence_is_stable_identity_paginated(
    tmp_path: Path,
) -> None:
    client, _repository = _client(tmp_path)
    body = {
        "scope": {"mode": "student", "student_ids": ["12"]},
        "exam_scope": {"mode": "current", "session_ids": [14]},
        "stable_key": "kp_alg_linear_equation",
        "page": 1,
        "page_size": 1,
    }

    response = client.post("/api/graph/v2/evidence", json=body)

    assert response.status_code == 200
    payload = response.json()
    assert payload["response_schema_version"] == "knowledge-graph-evidence-v2"
    assert payload["stable_key"] == "kp_alg_linear_equation"
    assert payload["display_name"] == "一元一次方程"
    assert payload["total"] == 1
    assert payload["items"][0]["question_id"] == "Q1"


def test_graph_v2_api_rejects_legacy_identity_and_reports_missing_stable_key(
    tmp_path: Path,
) -> None:
    client, _repository = _client(tmp_path)
    invalid = {
        **_query_body(),
        "knowledge_keys": ["knowledge_point:一元一次方程"],
    }
    missing_evidence = {
        "scope": {"mode": "student", "student_ids": ["12"]},
        "exam_scope": {"mode": "current", "session_ids": [14]},
        "stable_key": "kp_missing_identity",
    }

    invalid_response = client.post("/api/graph/v2/query", json=invalid)
    missing_response = client.post(
        "/api/graph/v2/evidence",
        json=missing_evidence,
    )

    assert invalid_response.status_code == 422
    assert missing_response.status_code == 404
    assert (
        missing_response.json()["error"]["code"]
        == "knowledge_identity_not_found"
    )


def test_graph_v2_openapi_contracts_are_explicit() -> None:
    schema = create_app().openapi()

    assert schema["paths"]["/api/graph/v2/query"]["post"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/GraphV2Response"
    }
    assert schema["paths"]["/api/graph/v2/evidence"]["post"]["responses"][
        "200"
    ]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/GraphV2EvidenceResponse"
    }
