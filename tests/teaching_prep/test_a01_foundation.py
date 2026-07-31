from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_profiles import ApiProfileStore
from backend.jobs.manager import JobManager
from backend.jobs.store import JobStore
from backend.teaching_prep.api import create_router
from backend.teaching_prep.application import TeachingPrepService
from backend.teaching_prep.domain.errors import (
    TeachingPrepConflictError,
    TeachingPrepStateError,
)
from backend.teaching_prep.domain.states import LessonPreparationState
from backend.teaching_prep.feature import create_workspace_feature
from backend.workspaces.registry import WorkspaceRegistry
from path_manager import PathManager


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _paths(tmp_path: Path) -> PathManager:
    paths = PathManager()
    paths._project_root = PROJECT_ROOT
    paths._data_root = tmp_path / "user_data"
    paths._api_profiles_path = tmp_path / "config" / "api_profiles.json"
    return paths


def _enabled_registry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[PathManager, WorkspaceRegistry]:
    monkeypatch.setenv("AI_GRADING_TEACHING_PREP_ENABLED", "1")
    paths = _paths(tmp_path)
    registry = WorkspaceRegistry(
        [create_workspace_feature()],
        paths=paths,
    )
    return paths, registry


def _migrated_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[PathManager, TeachingPrepService]:
    paths, registry = _enabled_registry(tmp_path, monkeypatch)
    registry.run_migrations()
    services = registry.create_services()
    service = services["teaching-prep"]
    assert isinstance(service, TeachingPrepService)
    return paths, service


def test_disabled_feature_has_no_route_or_filesystem_side_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AI_GRADING_TEACHING_PREP_ENABLED", "0")
    paths = _paths(tmp_path)
    registry = WorkspaceRegistry(
        [create_workspace_feature()],
        paths=paths,
    )
    api = FastAPI()

    registry.include_routers(api)
    registry.run_migrations()
    services = registry.create_services()

    assert registry.enabled_features == ()
    assert services == {}
    assert not (paths.data_root / "workspaces").exists()
    assert TestClient(api).get("/api/teaching-prep/status").status_code == 404


def test_enabled_feature_creates_only_teaching_prep_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, registry = _enabled_registry(tmp_path, monkeypatch)

    registry.run_migrations()
    registry.run_migrations()
    services = registry.create_services()

    root = paths.workspace_dir("teaching-prep")
    assert set(path.name for path in (paths.data_root / "workspaces").iterdir()) == {
        "teaching-prep"
    }
    assert (root / "teaching_prep.db").is_file()
    assert isinstance(services["teaching-prep"], TeachingPrepService)
    assert services["teaching-prep"].status()["real_model_enabled"] is False
    with sqlite3.connect(root / "teaching_prep.db") as connection:
        assert connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name = 'lesson_preparations'
            """
        ).fetchone() == ("lesson_preparations",)


def test_active_model_profile_is_resolved_without_making_a_model_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, registry = _enabled_registry(tmp_path, monkeypatch)
    ApiProfileStore(paths.api_profiles_path).replace_all(
        [
            {
                "name": "合成备课模型",
                "config_api_key": "test-key-not-real",
                "config_base_url": "https://example.invalid/v1",
                "config_model": "test-model",
            }
        ]
    )
    registry.run_migrations()

    service = registry.create_services()["teaching-prep"]

    assert isinstance(service, TeachingPrepService)
    assert service.status()["real_model_enabled"] is True
    assert service.status()["semester_mapping_model_available"] is True
    assert not (
        paths.workspace_dir("teaching-prep") / ".model-operations"
    ).exists()


def test_create_is_idempotent_and_rejects_token_reuse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)

    first, first_created = service.create_preparation(
        request_token="request-0001",
        title="二元一次方程第一课时",
        class_name="合成七年级一班",
    )
    repeated, repeated_created = service.create_preparation(
        request_token="request-0001",
        title="二元一次方程第一课时",
        class_name="合成七年级一班",
    )

    assert first_created is True
    assert repeated_created is False
    assert repeated.id == first.id
    assert len(service.list_preparations()) == 1
    with pytest.raises(TeachingPrepConflictError):
        service.create_preparation(
            request_token="request-0001",
            title="不同内容",
            class_name="合成七年级一班",
        )


def test_stale_revision_cannot_overwrite_newer_preparation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    preparation, _created = service.create_preparation(
        request_token="request-0002",
        title="一次函数",
        class_name=None,
    )

    updated = service.update_preparation(
        preparation.id,
        expected_revision=preparation.revision,
        title="一次函数第一课时",
    )

    assert updated.revision == preparation.revision + 1
    with pytest.raises(TeachingPrepConflictError):
        service.update_preparation(
            preparation.id,
            expected_revision=preparation.revision,
            title="旧页面覆盖",
        )
    assert service.get_preparation(preparation.id).title == "一次函数第一课时"


def test_state_machine_rejects_skipping_teacher_gates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    preparation, _created = service.create_preparation(
        request_token="request-0003",
        title="全等三角形",
        class_name=None,
    )

    with pytest.raises(TeachingPrepStateError):
        service.update_preparation(
            preparation.id,
            expected_revision=preparation.revision,
            target_state=LessonPreparationState.EXECUTING,
        )


def test_api_root_and_prefixed_job_use_workspace_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, registry = _enabled_registry(tmp_path, monkeypatch)
    registry.run_migrations()
    services = registry.create_services()
    api = FastAPI()
    api.state.workspace_services = services
    api.include_router(create_router(), prefix="/api/teaching-prep")
    client = TestClient(api)

    assert client.get("/api/teaching-prep/status").json()["module"] == (
        "teaching-prep"
    )
    response = client.post(
        "/api/teaching-prep/preparations",
        json={
            "request_token": "request-api-0001",
            "title": "合成课时",
        },
    )
    assert response.status_code == 201
    assert response.json()["state"] == "selecting_sources"

    manager = JobManager(
        JobStore(tmp_path / "jobs.db"),
        max_workers=1,
    )
    try:
        registry.register_jobs(manager, services)
        assert "teaching_prep.reconcile" in manager._handlers
    finally:
        manager.shutdown()
