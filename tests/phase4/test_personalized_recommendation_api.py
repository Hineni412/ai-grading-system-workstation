from __future__ import annotations

import warnings

import pytest
from fastapi.testclient import TestClient

from question_bank.recommendation.personalized import (
    RecommendationRevisionConflict,
    RecommendationSourceChanged,
)


warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
)


class FakeDiagnosis:
    def build_profiles(self, *, scope, exam_scope):
        return {
            "students": [
                {
                    "student_id": scope["student_ids"][0],
                    "student_code": "S01",
                    "student_name": "合成学生",
                    "class_id": "SYN-C01",
                    "weak_points": [
                        {
                            "knowledge_key": "kp_alg_linear_equation",
                            "knowledge_point": "一元一次方程",
                            "mastery": 0.4,
                        }
                    ],
                }
            ],
            "exam_scope": exam_scope,
        }


class FakeRecommendation:
    def __init__(self) -> None:
        self.created = None
        self.edited = None
        self.edit_error: Exception | None = None

    def create(self, **kwargs):
        self.created = kwargs
        return _draft()

    def resolve_target_names(self, target_names):
        assert target_names == ["一元一次方程"]
        return ("kp_alg_linear_equation",)

    def get(self, draft_id: str):
        return {**_draft(), "draft_id": draft_id}

    def edit(self, draft_id, command):
        if self.edit_error is not None:
            raise self.edit_error
        self.edited = (draft_id, command)
        return {**_draft(), "draft_id": draft_id, "revision": 2}


@pytest.fixture()
def personalized_client() -> tuple[TestClient, FakeRecommendation]:
    from backend.api.app import create_app
    from backend.api.dependencies import (
        get_diagnosis_profile_service,
        get_personalized_recommendation_module,
    )

    module = FakeRecommendation()
    app = create_app()
    app.dependency_overrides[get_diagnosis_profile_service] = FakeDiagnosis
    app.dependency_overrides[
        get_personalized_recommendation_module
    ] = lambda: module
    return TestClient(app), module


def test_personalized_draft_create_and_get_have_public_contract(
    personalized_client: tuple[TestClient, FakeRecommendation],
) -> None:
    client, module = personalized_client
    response = client.post(
        "/api/training/personalized-drafts",
        json=_create_body(),
    )

    assert response.status_code == 200
    assert response.json()["draft_id"] == "d" * 64
    response_item = response.json()["students"][0]["items"][0]
    assert response_item["question_text"] == "解方程 3x + 1 = 7。"
    assert module.created["actor_ref"] == "local_teacher"
    assert module.created["config"].expected_minutes == 50
    assert module.created["config"].difficulty_min == 3
    assert module.created["config"].target_keys == (
        "kp_alg_linear_equation",
    )
    assert module.created["config"].scope_keys == ("kp_chapter_scope",)

    loaded = client.get(
        f"/api/training/personalized-drafts/{'e' * 64}"
    )
    assert loaded.status_code == 200
    assert loaded.json()["draft_id"] == "e" * 64


def test_personalized_edit_uses_revision_and_safe_errors(
    personalized_client: tuple[TestClient, FakeRecommendation],
) -> None:
    client, module = personalized_client
    response = client.post(
        f"/api/training/personalized-drafts/{'d' * 64}/edits",
        json={
            "request_token": "2" * 32,
            "expected_revision": 1,
            "action": "replace",
            "student_id": "SYN-S01",
            "item_id": "item-1",
            "reason": "更换题目",
            "replacement_question_id": 12,
        },
    )
    assert response.status_code == 200
    assert module.edited[1].replacement_question_id == 12
    assert module.edited[1].actor_ref == "local_teacher"

    module.edit_error = RecommendationRevisionConflict(1, 2)
    conflict = client.post(
        f"/api/training/personalized-drafts/{'d' * 64}/edits",
        json={
            "request_token": "3" * 32,
            "expected_revision": 1,
            "action": "lock",
            "student_id": "SYN-S01",
            "item_id": "item-1",
            "reason": "锁定",
        },
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == (
        "personalized_recommendation_revision_conflict"
    )
    assert conflict.json()["error"]["details"]["current_revision"] == 2

    module.edit_error = RecommendationSourceChanged("changed")
    changed = client.post(
        f"/api/training/personalized-drafts/{'d' * 64}/edits",
        json={
            "request_token": "4" * 32,
            "expected_revision": 2,
            "action": "lock",
            "student_id": "SYN-S01",
            "item_id": "item-1",
            "reason": "锁定",
        },
    )
    assert changed.status_code == 409
    assert changed.json()["error"]["code"] == (
        "personalized_recommendation_source_changed"
    )
    assert "source_version" not in changed.text


def test_personalized_draft_accepts_curriculum_volume_bound(
    personalized_client: tuple[TestClient, FakeRecommendation],
) -> None:
    client, module = personalized_client
    response = client.post(
        "/api/training/personalized-drafts",
        json={
            **_create_body(),
            "curriculum_volume_id": "bnu24-math-g7-lower",
        },
    )
    assert response.status_code == 200
    assert module.created["config"].curriculum_volume_id == (
        "bnu24-math-g7-lower"
    )

    unknown = client.post(
        "/api/training/personalized-drafts",
        json={
            **_create_body(),
            "request_token": "9" * 32,
            "curriculum_volume_id": "bnu24-math-g6-lower",
        },
    )
    assert unknown.status_code == 422
    assert unknown.json()["error"]["code"] == (
        "personalized_recommendation_invalid"
    )


def _create_body() -> dict[str, object]:
    return {
        "request_token": "1" * 32,
        "scope": {
            "mode": "student",
            "student_ids": ["SYN-S01"],
        },
        "exam_scope": {
            "mode": "current",
            "session_ids": [1],
        },
        "question_count": 8,
        "expected_minutes": 50,
        "difficulty_min": 3,
        "difficulty_max": 8,
        "stage_ratios": {
            "direct": 0.5,
            "prerequisite": 0.25,
            "transfer": 0.25,
        },
        "target_names": ["一元一次方程"],
        "scope_keys": ["kp_chapter_scope"],
        "exclude_current_exam_originals": True,
    }


def _draft() -> dict[str, object]:
    return {
        "draft_id": "d" * 64,
        "status": "draft",
        "revision": 1,
        "result_version": "a" * 64,
        "engine_version": "personalized-recommendation-v1",
        "source_version": "b" * 64,
        "config": {},
        "students": [
            {
                "student_id": "SYN-S01",
                "student_code": "S01",
                "student_name": "合成学生",
                "class_id": "SYN-C01",
                "selection_mode": "mastery_targeted",
                "targets": [],
                "items": [
                    {
                        "item_id": "item-1",
                        "item_order": 1,
                        "slot": 1,
                        "question_id": 31,
                        "question_number": "3",
                        "question_text": "解方程 3x + 1 = 7。",
                        "stage": "direct",
                        "target": {
                            "stable_key": "kp_alg_linear_equation",
                        },
                        "matched_key": "kp_alg_linear_equation",
                        "matched_name": "一元一次方程",
                        "relation": None,
                        "criterion_version_id": "c" * 64,
                        "criterion_point_count": 1,
                        "difficulty": 5,
                        "estimated_minutes": 6,
                        "source_paper": "合成题源",
                        "reason": "直接巩固一元一次方程。",
                        "locked": False,
                        "replacement_history": [],
                    }
                ],
                "shortages": [],
                "warnings": [],
                "estimated_minutes": 6,
            }
        ],
        "warnings": [],
        "history": [],
    }
