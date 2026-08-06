from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_profiles import ApiProfileStore
from backend.api.dependencies import get_model_profile_service
from backend.api.routers.model_profiles import router
from backend.model_profiles import ModelProfileService


def test_task_bindings_route_persists_multiple_api_sites_without_exposing_keys(
    tmp_path,
) -> None:
    profile_path = tmp_path / "api_profiles.json"
    service = ModelProfileService(ApiProfileStore(profile_path))
    service.upsert(
        "校内站点",
        {"base_url": "https://school.example/v1", "api_key": "school-secret"},
    )
    service.upsert(
        "备用站点",
        {"base_url": "https://backup.example/v1", "api_key": "backup-secret"},
    )

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_model_profile_service] = lambda: service
    client = TestClient(app)
    bindings = {
        "content_generation": {
            "profile_name": "校内站点",
            "model": "content-model",
        },
        "grading": {"profile_name": "备用站点", "model": "grading-model"},
        "teaching_prep": {"profile_name": "校内站点", "model": "prep-model"},
        "class_teacher": {
            "profile_name": "备用站点",
            "model": "class-teacher-model",
        },
    }

    response = client.put("/api/model-profiles/routing/task-bindings", json=bindings)

    assert response.status_code == 200
    assert response.json()["task_bindings"] == bindings
    assert "school-secret" not in response.text
    assert "backup-secret" not in response.text

    reloaded = ModelProfileService(ApiProfileStore(profile_path))
    assert reloaded.list_state()["task_bindings"] == bindings
    assert reloaded.store.profile_for_task("grading")["base_url"] == (
        "https://backup.example/v1"
    )
    assert reloaded.store.profile_for_task("grading")["grading_model"] == (
        "grading-model"
    )
