from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.dependencies import get_request_diagnosis_profile_service
from backend.api.routers.graph import (
    get_graph_v2_query_service,
    get_mastery_rollout_repository,
)
from question_bank.database.schema import initialize_database
from question_bank.mastery.rollout import MasteryRolloutRepository
from question_bank.relations.query_service import KnowledgeGraphV2QueryService


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
                "covered_items": 2,
                "total_items": 2,
                "missing_items": {},
            },
            "warnings": [],
            "diagnosis_identity": "question_tag",
            "_mastery_session_times": {
                "14": "2026-01-01T08:00:00+08:00",
            },
            "students": [
                {
                    "student_id": 12,
                    "student_code": "S12",
                    "student_name": "合成学生",
                    "class_id": "合成班",
                    "weak_points": [
                        {
                            "knowledge_point": "一元一次方程",
                            "knowledge_key": "knowledge_point:一元一次方程",
                            "mastery": 1.0,
                            "score_sum": 10,
                            "full_score_sum": 10,
                            "deduction_count": 0,
                            "evidence_count": 1,
                            "exam_count": 1,
                            "actionable_reasons": [],
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
                                    "score_awarded": 10,
                                    "full_score": 10,
                                    "score_rate": 1.0,
                                }
                            ],
                        }
                    ],
                }
            ],
        }


def _client(tmp_path: Path) -> TestClient:
    database = tmp_path / "question-bank.db"
    initialize_database(database)
    repository = MasteryRolloutRepository(database)
    app = create_app()
    app.dependency_overrides[
        get_request_diagnosis_profile_service
    ] = lambda: FakeDiagnosisService()
    app.dependency_overrides[
        get_mastery_rollout_repository
    ] = lambda: repository
    app.dependency_overrides[
        get_graph_v2_query_service
    ] = lambda: KnowledgeGraphV2QueryService(
        database,
        rollout_repository=repository,
        clock=lambda: _as_of_datetime(),
    )
    return TestClient(app)


def _as_of_datetime():
    from datetime import datetime

    return datetime.fromisoformat("2026-01-31T00:00:00+00:00")


def _scope() -> dict[str, object]:
    return {
        "scope": {"mode": "student", "student_ids": ["12"]},
        "exam_scope": {"mode": "current", "session_ids": [14]},
    }


def test_rollout_api_compares_reviews_enables_and_fully_rolls_back(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)

    initial = client.get("/api/graph/v2/mastery/rollout")
    comparison = client.post(
        "/api/graph/v2/mastery/compare",
        json={
            **_scope(),
            "as_of": "2026-01-31T00:00:00+00:00",
        },
    )

    assert initial.status_code == 200
    assert initial.json()["active_mode"] == "v1"
    assert comparison.status_code == 200
    payload = comparison.json()
    assert payload["required_review_count"] == 1
    assert payload["items"][0]["absolute_delta"] > 0.10
    assert payload["gate"]["passed"] is False

    blocked = client.put(
        "/api/graph/v2/mastery/rollout",
        json={
            "enabled": True,
            "expected_revision": initial.json()["revision"],
            "teacher_ref": "teacher-synthetic",
            "reason": "尚未抽检",
            "evaluation_id": payload["evaluation_id"],
        },
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "mastery_rollout_gate_blocked"

    gate = payload["gate"]
    for item in (
        item for item in payload["items"] if item["requires_review"]
    ):
        reviewed = client.post(
            "/api/graph/v2/mastery/spot-check",
            json={
                "evaluation_id": payload["evaluation_id"],
                "item_hash": item["item_hash"],
                "decision": "accepted",
                "teacher_ref": "teacher-synthetic",
                "reason": "合成证据和差异解释可接受",
                "expected_revision": gate["revision"],
            },
        )
        assert reviewed.status_code == 200
        gate = reviewed.json()
    assert gate["passed"] is True

    enabled = client.put(
        "/api/graph/v2/mastery/rollout",
        json={
            "enabled": True,
            "expected_revision": initial.json()["revision"],
            "teacher_ref": "teacher-synthetic",
            "reason": "所需差异均已核对",
            "evaluation_id": payload["evaluation_id"],
        },
    )
    graph_enabled = client.post(
        "/api/graph/v2/query",
        json={**_scope(), "knowledge_keys": [], "prerequisite_depth": 0},
    )

    assert enabled.status_code == 200
    assert enabled.json()["active_mode"] == "v2"
    assert graph_enabled.status_code == 200
    assert graph_enabled.json()["mastery_mode"] == "v2"
    node = graph_enabled.json()["nodes"][0]
    assert node["mastery_v1"]["value"] == 1.0
    assert node["mastery_v2"]["status"] == "available"
    assert node["mastery_v2"]["value"] < node["mastery_v1"]["value"]

    disabled = client.put(
        "/api/graph/v2/mastery/rollout",
        json={
            "enabled": False,
            "expected_revision": enabled.json()["revision"],
            "teacher_ref": "teacher-synthetic",
            "reason": "完整回退演练",
        },
    )
    graph_disabled = client.post(
        "/api/graph/v2/query",
        json={**_scope(), "knowledge_keys": [], "prerequisite_depth": 0},
    )

    assert disabled.status_code == 200
    assert disabled.json()["active_mode"] == "v1"
    assert disabled.json()["active_parameter_version"] is None
    assert disabled.json()["approved_evaluation_id"] is None
    assert graph_disabled.status_code == 200
    assert graph_disabled.json()["mastery_mode"] == "v1"
    assert graph_disabled.json()["mastery_parameter_version"] is None
    assert graph_disabled.json()["nodes"][0]["mastery_v2"] == {
        "status": "unavailable",
        "value": None,
        "evidence_count": 0,
        "reason": "mastery_v2_not_enabled",
    }


def test_parameter_api_creates_new_immutable_version_and_keeps_history(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    first_comparison = client.post(
        "/api/graph/v2/mastery/compare",
        json={
            **_scope(),
            "as_of": "2026-01-31T00:00:00+00:00",
        },
    ).json()
    created = client.post(
        "/api/graph/v2/mastery/parameters",
        json={"prior_mean": 0.6, "prior_strength": 2.0},
    )
    second_comparison = client.post(
        "/api/graph/v2/mastery/compare",
        json={
            **_scope(),
            "as_of": "2026-01-31T00:00:00+00:00",
            "parameter_version": created.json()["parameter_version"],
        },
    ).json()
    history = client.get("/api/graph/v2/mastery/parameters")

    assert created.status_code == 200
    assert first_comparison["parameter_version"] != created.json()[
        "parameter_version"
    ]
    assert second_comparison["parameter_version"] == created.json()[
        "parameter_version"
    ]
    assert first_comparison["items"][0]["mastery_v2"]["explanations"] != (
        second_comparison["items"][0]["mastery_v2"]["explanations"]
    )
    assert history.status_code == 200
    versions = {
        item["parameter_version"] for item in history.json()["items"]
    }
    assert versions == {
        first_comparison["parameter_version"],
        created.json()["parameter_version"],
    }


def test_comparison_rejects_naive_time_and_unknown_parameter_version(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)

    naive = client.post(
        "/api/graph/v2/mastery/compare",
        json={**_scope(), "as_of": "2026-01-31T00:00:00"},
    )
    missing = client.post(
        "/api/graph/v2/mastery/compare",
        json={
            **_scope(),
            "as_of": "2026-01-31T00:00:00+00:00",
            "parameter_version": "a" * 64,
        },
    )

    assert naive.status_code == 422
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == (
        "mastery_parameter_version_not_found"
    )
