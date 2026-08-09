from __future__ import annotations

import shutil
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
from backend.teaching_prep.infrastructure.database import TeachingPrepDatabase
from backend.teaching_prep.infrastructure.repositories.pptx_execution import (
    PptxExecutionRepository,
)
from backend.workspaces.registry import WorkspaceRegistry
from path_manager import PathManager
from update_tools.migrate_db import run_migrations


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


def _legacy_pptx_execution_database(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    migration_root = PROJECT_ROOT / "migrations" / "teaching_prep"
    legacy_migrations = tmp_path / "teaching-prep-migrations-through-018"
    legacy_migrations.mkdir()
    for migration in sorted(migration_root.glob("*.sql")):
        if int(migration.stem.split("_", 1)[0]) <= 18:
            shutil.copy2(migration, legacy_migrations / migration.name)
    database_path = tmp_path / "legacy-teaching-prep.db"
    report = run_migrations(
        "teaching_prep",
        db_path=database_path,
        migrations_dir=legacy_migrations,
        backup_dir_override=tmp_path / "legacy-backups",
    )
    assert report.error is None
    ids = {
        "source": "a" * 32,
        "material_version": "b" * 32,
        "curriculum": "c" * 32,
        "lesson": "d" * 32,
        "resource_pack": "e" * 32,
        "lesson_draft": "f" * 32,
        "slide_plan": "1" * 32,
        "run": "2" * 32,
        "pptx_version": "3" * 32,
    }
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(
            f"""
            INSERT INTO material_sources (id, display_name, material_type)
            VALUES ('{ids["source"]}', '合成历史课件', 'pptx');

            INSERT INTO material_versions (
                id, source_id, request_token, request_hash, content_sha256,
                file_name, size_bytes, inspection_status
            )
            VALUES (
                '{ids["material_version"]}', '{ids["source"]}',
                'legacy-material-import', '{"3" * 64}', '{"4" * 64}',
                'synthetic-history.pptx', 4096, 'ready'
            );

            INSERT INTO curriculum_editions (
                id, request_token, request_hash, title, grade_level, volume
            )
            VALUES (
                '{ids["curriculum"]}', 'legacy-curriculum', '{"5" * 64}',
                '合成教材', 7, 'first'
            );

            INSERT INTO lesson_nodes (
                id, curriculum_id, request_token, request_hash, node_type,
                title, sort_order
            )
            VALUES (
                '{ids["lesson"]}', '{ids["curriculum"]}', 'legacy-lesson',
                '{"6" * 64}', 'lesson', '合成课时', 1
            );

            INSERT INTO resource_pack_versions (
                id, request_token, request_hash, lesson_node_id,
                version_number, source_state_sha256, pack_sha256,
                payload_json
            )
            VALUES (
                '{ids["resource_pack"]}', 'legacy-resource-pack',
                '{"7" * 64}', '{ids["lesson"]}', 1, '{"8" * 64}',
                '{"9" * 64}', '{{}}'
            );

            INSERT INTO lesson_draft_versions (
                id, request_token, request_hash, resource_pack_id,
                version_number, source_kind, status, payload_json,
                capacity_json
            )
            VALUES (
                '{ids["lesson_draft"]}', 'legacy-lesson-draft',
                '{"a" * 64}', '{ids["resource_pack"]}', 1,
                'local_template', 'confirmed', '{{}}', '{{}}'
            );

            INSERT INTO slide_plan_versions (
                id, request_token, request_hash, lesson_draft_id,
                resource_pack_id, version_number, source_ppt_state_sha256,
                status, payload_json
            )
            VALUES (
                '{ids["slide_plan"]}', 'legacy-slide-plan', '{"b" * 64}',
                '{ids["lesson_draft"]}', '{ids["resource_pack"]}', 1,
                '{"c" * 64}', 'approved', '{{}}'
            );

            INSERT INTO teaching_prep_operations (
                operation_id, operation_type, idempotency_key, request_hash,
                target_kind, target_id, status
            )
            VALUES (
                'legacy-pptx-operation', 'pptx_copy_execution',
                'legacy-pptx-operation', '{"d" * 64}', 'slide_plan',
                '{ids["slide_plan"]}', 'succeeded'
            );

            INSERT INTO pptx_execution_runs (
                id, operation_id, request_hash, slide_plan_id,
                source_material_version_id, source_sha256, staging_name,
                expected_slide_count, status, published_version_id,
                wps_invocation_count, phase
            )
            VALUES (
                '{ids["run"]}', 'legacy-pptx-operation', '{"d" * 64}',
                '{ids["slide_plan"]}', '{ids["material_version"]}',
                '{"4" * 64}', '{ids["run"]}', 2, 'published',
                '{ids["pptx_version"]}', 1, 'done'
            );

            INSERT INTO pptx_versions (
                id, slide_plan_id, lesson_node_id, execution_run_id,
                version_number, status, output_relpath, output_filename,
                output_sha256, slide_count, verification_report_json
            )
            VALUES (
                '{ids["pptx_version"]}', '{ids["slide_plan"]}',
                '{ids["lesson"]}', '{ids["run"]}', 1, 'published',
                'outputs/synthetic-history.pptx', 'synthetic-history.pptx',
                '{"e" * 64}', 2, '{{"verified":true}}'
            );
            """
        )
    return database_path, ids


def test_existing_pptx_run_migrates_to_path_free_source_snapshot(
    tmp_path: Path,
) -> None:
    database_path, ids = _legacy_pptx_execution_database(tmp_path)

    report = run_migrations(
        "teaching_prep",
        db_path=database_path,
        migrations_dir=PROJECT_ROOT / "migrations" / "teaching_prep",
        backup_dir_override=tmp_path / "upgrade-backups",
    )

    assert report.error is None
    assert report.results[-1].name == "019_pptx_execution_source_snapshots"
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(pptx_execution_source_snapshots)"
            )
        }
        assert columns == {
            "material_version_id",
            "source_id",
            "display_name",
            "file_name",
            "material_type",
            "content_sha256",
            "size_bytes",
            "schema_version",
            "created_at",
        }
        snapshot = connection.execute(
            """
            SELECT material_version_id, source_id, display_name, file_name,
                   material_type, content_sha256, size_bytes, schema_version
            FROM pptx_execution_source_snapshots
            WHERE material_version_id = ?
            """,
            (ids["material_version"],),
        ).fetchone()
        assert snapshot == (
            ids["material_version"],
            ids["source"],
            "合成历史课件",
            "synthetic-history.pptx",
            "pptx",
            "4" * 64,
            4096,
            1,
        )
        source_column = next(
            row
            for row in connection.execute(
                "PRAGMA table_info(pptx_execution_runs)"
            )
            if row[1] == "source_material_version_id"
        )
        assert source_column[3] == 1
        source_foreign_key = next(
            row
            for row in connection.execute(
                "PRAGMA foreign_key_list(pptx_execution_runs)"
            )
            if row[3] == "source_material_version_id"
        )
        assert source_foreign_key[2:7] == (
            "pptx_execution_source_snapshots",
            "source_material_version_id",
            "material_version_id",
            "NO ACTION",
            "RESTRICT",
        )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

        connection.execute(
            "DELETE FROM material_versions WHERE id = ?",
            (ids["material_version"],),
        )
        connection.execute(
            "DELETE FROM material_sources WHERE id = ?",
            (ids["source"],),
        )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

    restored = PptxExecutionRepository(
        TeachingPrepDatabase(database_path)
    ).get(ids["run"])
    assert restored.source_material_version_id == ids["material_version"]
    assert restored.source_sha256 == "4" * 64


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
