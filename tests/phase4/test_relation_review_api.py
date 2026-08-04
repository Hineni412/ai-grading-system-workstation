from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.routers.graph import get_relation_review_service
from question_bank.database.schema import initialize_database
from question_bank.relations.contracts import KnowledgeRelation, RelationType
from question_bank.relations.repository import KnowledgeRelationRepository
from question_bank.relations.review_service import RelationReviewService
from tests.current_knowledge_support import install_current_knowledge


def _client(
    tmp_path: Path,
) -> tuple[TestClient, KnowledgeRelationRepository]:
    database = tmp_path / "question-bank.db"
    initialize_database(database)
    install_current_knowledge(database)
    repository = KnowledgeRelationRepository(database)
    service = RelationReviewService(database)
    app = create_app()
    app.dependency_overrides[get_relation_review_service] = lambda: service
    return TestClient(app), repository


def _suggest(
    repository: KnowledgeRelationRepository,
    source_key: str,
    target_key: str,
):
    return repository.create_suggestion(
        KnowledgeRelation(source_key, target_key, RelationType.PARENT),
        source_kind="model",
        rationale="合成 API 建议",
        model_name="fake-relation-model",
        model_version="fake-v1",
        prompt_version="relation-prompt-v1",
        confidence=0.95,
    )


def test_relation_review_api_exposes_queue_preview_decision_and_timeline(
    tmp_path: Path,
) -> None:
    client, repository = _client(tmp_path)
    suggested = _suggest(
        repository,
        "kp_alg_real_numbers",
        "kp_alg_equation_properties",
    )

    queue = client.get("/api/graph/relations/review-queue")
    impact = client.post(
        f"/api/graph/relations/{suggested.relation_id}/impact",
        json={"action": "confirm"},
    )
    reviewed = client.post(
        f"/api/graph/relations/{suggested.relation_id}/review",
        json={
            "action": "confirm",
            "expected_revision": suggested.revision,
            "teacher_ref": "teacher-api",
            "reason": "API 合成确认",
        },
    )
    timeline = client.get(
        f"/api/graph/relations/{suggested.relation_id}/timeline"
    )

    assert queue.status_code == 200
    assert queue.json()["items"][0]["source_kind"] == "model"
    assert queue.json()["items"][0]["rationale"] == "合成 API 建议"
    assert impact.status_code == 200
    assert impact.json()["can_apply"] is True
    assert impact.json()["activity_effect"] == "include_in_next_standard_candidate"
    assert reviewed.status_code == 200
    assert reviewed.json()["relation"]["status"] == "confirmed"
    assert reviewed.json()["relation"]["decision_by"] == "teacher-api"
    assert timeline.status_code == 200
    assert [event["event_type"] for event in timeline.json()["timeline"]] == [
        "suggested",
        "confirmed",
    ]


def test_relation_review_api_reports_stale_revision_without_overwrite(
    tmp_path: Path,
) -> None:
    client, repository = _client(tmp_path)
    suggested = _suggest(
        repository,
        "kp_alg_real_numbers",
        "kp_alg_equation_properties",
    )
    first = client.post(
        f"/api/graph/relations/{suggested.relation_id}/review",
        json={
            "action": "confirm",
            "expected_revision": 1,
            "teacher_ref": "teacher-new",
            "reason": "先确认",
        },
    )
    stale = client.post(
        f"/api/graph/relations/{suggested.relation_id}/review",
        json={
            "action": "retire",
            "expected_revision": 1,
            "teacher_ref": "teacher-stale",
            "reason": "过期操作",
        },
    )

    assert first.status_code == 200
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "relation_revision_conflict"
    assert stale.json()["error"]["details"]["current_revision"] == 2
    assert suggested.relation_id in {
        relation.relation_id for relation in repository.list_active_relations()
    }


def test_relation_batch_api_returns_partial_result_and_keeps_each_revision(
    tmp_path: Path,
) -> None:
    client, repository = _client(tmp_path)
    stale = _suggest(
        repository,
        "kp_alg_real_numbers",
        "kp_alg_equation_properties",
    )
    current = _suggest(
        repository,
        "kp_alg_equation_properties",
        "kp_alg_linear_equation",
    )
    client.post(
        f"/api/graph/relations/{stale.relation_id}/review",
        json={
            "action": "reject",
            "expected_revision": 1,
            "teacher_ref": "teacher-first",
            "reason": "先拒绝",
        },
    )

    response = client.post(
        "/api/graph/relations/review-batch",
        json={
            "teacher_ref": "teacher-batch",
            "commands": [
                {
                    "relation_id": stale.relation_id,
                    "expected_revision": 1,
                    "action": "reject",
                    "reason": "过期批量项",
                },
                {
                    "relation_id": current.relation_id,
                    "expected_revision": 1,
                    "action": "reject",
                    "reason": "有效批量项",
                },
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "partial"
    assert response.json()["applied_count"] == 1
    assert response.json()["failed_count"] == 1
    assert response.json()["results"][0]["category"] == "revision_conflict"
    assert repository.get_relation(stale.relation_id).decision_by == "teacher-first"
    assert repository.get_relation(current.relation_id).decision_by == "teacher-batch"


def test_relation_amend_api_requires_scoped_shape(
    tmp_path: Path,
) -> None:
    client, repository = _client(tmp_path)
    suggested = _suggest(
        repository,
        "kp_alg_real_numbers",
        "kp_alg_equation_properties",
    )

    missing = client.post(
        f"/api/graph/relations/{suggested.relation_id}/review",
        json={
            "action": "amend",
            "expected_revision": 1,
            "teacher_ref": "teacher-api",
            "reason": "缺少改后关系",
        },
    )
    changed = client.post(
        f"/api/graph/relations/{suggested.relation_id}/review",
        json={
            "action": "amend",
            "expected_revision": 1,
            "teacher_ref": "teacher-api",
            "reason": "改为先修",
            "amended_relation": {
                "source_key": "kp_alg_linear_equation",
                "target_key": "kp_alg_equation_properties",
                "relation_type": "prerequisite",
            },
        },
    )

    assert missing.status_code == 422
    assert changed.status_code == 200
    assert changed.json()["relation"]["revision"] == 2
    assert changed.json()["relation"]["relation_type"] == "prerequisite"
    assert changed.json()["timeline"][1]["event_type"] == "amended"
