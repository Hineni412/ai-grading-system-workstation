from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from backend.api.app import ApiError
from backend.teaching_prep.api import create_router
from backend.teaching_prep.application import TeachingPrepService
from backend.teaching_prep.domain.errors import TeachingPrepConflictError

from .test_a01_foundation import _migrated_service


class _SimulatedMaterialDeleteProcessExit(BaseException):
    pass


def _api_client(service: TeachingPrepService) -> TestClient:
    app = FastAPI()
    app.state.workspace_services = {"teaching-prep": service}
    app.include_router(create_router(), prefix="/api/teaching-prep")

    @app.exception_handler(ApiError)
    async def handle_api_error(_request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    return TestClient(app)


def _lesson_tree(
    service: TeachingPrepService,
) -> tuple[str, str, str, list[str]]:
    curriculum, _created = service.create_curriculum(
        request_token="curriculum-0001",
        title="合成七年级下册",
        grade_level=7,
        volume="second",
        publisher="合成出版社",
    )
    chapter, _created = service.create_lesson_node(
        request_token="lesson-chapter-0001",
        curriculum_id=curriculum.id,
        parent_id=None,
        node_type="chapter",
        title="第八章 二元一次方程组",
    )
    section, _created = service.create_lesson_node(
        request_token="lesson-section-0001",
        curriculum_id=curriculum.id,
        parent_id=chapter.id,
        node_type="section",
        title="消元",
    )
    lessons = []
    for index, title in enumerate(
        ("代入消元", "加减消元", "实际问题"),
        start=1,
    ):
        node, _created = service.create_lesson_node(
            request_token=f"lesson-node-000{index}",
            curriculum_id=curriculum.id,
            parent_id=section.id,
            node_type="lesson",
            title=title,
            duration_minutes=45,
        )
        lessons.append(node.id)
    return curriculum.id, chapter.id, section.id, lessons


def test_teacher_can_create_section_with_multiple_ordered_lessons(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    curriculum_id, chapter_id, section_id, lesson_ids = _lesson_tree(service)

    nodes = service.list_lesson_nodes(curriculum_id)

    assert [node.node_type for node in nodes] == [
        "chapter",
        "section",
        "lesson",
        "lesson",
        "lesson",
    ]
    assert nodes[0].id == chapter_id
    assert nodes[1].id == section_id
    assert [node.id for node in nodes[2:]] == lesson_ids


def test_reorder_and_edit_use_revision_conflict_protection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    curriculum_id, _chapter_id, section_id, lesson_ids = _lesson_tree(service)
    lessons = [
        item
        for item in service.list_lesson_nodes(curriculum_id)
        if item.parent_id == section_id
    ]

    reordered = service.reorder_lesson_nodes(
        curriculum_id=curriculum_id,
        parent_id=section_id,
        ordered_ids=list(reversed(lesson_ids)),
        expected_revisions={item.id: item.revision for item in lessons},
    )

    assert [item.id for item in reordered] == list(reversed(lesson_ids))
    with pytest.raises(TeachingPrepConflictError):
        service.reorder_lesson_nodes(
            curriculum_id=curriculum_id,
            parent_id=section_id,
            ordered_ids=lesson_ids,
            expected_revisions={item.id: item.revision for item in lessons},
        )
    current = reordered[0]
    inactive = service.update_lesson_node(
        current.id,
        expected_revision=current.revision,
        title=current.title,
        duration_minutes=40,
        is_active=False,
    )
    assert inactive.is_active is False
    assert inactive.duration_minutes == 40


def test_same_fingerprint_does_not_duplicate_material_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    material = tmp_path / "synthetic-textbook.pdf"
    material.write_bytes(b"%PDF-1.7 synthetic")

    first, first_created = service.register_material_file(
        request_token="material-0001",
        path=material,
        display_name="合成教材",
        unit_count=12,
        inspection_status="ready",
    )
    repeated, repeated_created = service.register_material_file(
        request_token="material-0002",
        path=material,
        display_name="同一文件再次选择",
        unit_count=12,
        inspection_status="ready",
    )

    assert first_created is True
    assert repeated_created is False
    assert repeated.id == first.id
    assert len(service.list_material_versions()) == 1


def test_missing_material_requires_explicit_relocation_and_preserves_versions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    original = tmp_path / "synthetic-reference.pptx"
    original.write_bytes(b"synthetic-pptx-v1")
    first, _created = service.register_material_file(
        request_token="material-1001",
        path=original,
        display_name="合成参考课件",
    )
    moved = tmp_path / "moved-reference.pptx"
    original.rename(moved)

    missing = service.refresh_material_availability(first.id)
    relocated, created = service.relocate_material(
        first.id,
        request_token="material-1002",
        path=moved,
    )

    assert missing.availability == "needs_relocation"
    assert created is False
    assert relocated.id == first.id
    assert relocated.availability == "available"

    replacement = tmp_path / "replacement-reference.pptx"
    replacement.write_bytes(b"synthetic-pptx-v2")
    newer, created = service.relocate_material(
        first.id,
        request_token="material-1003",
        path=replacement,
    )

    assert created is True
    assert newer.id != first.id
    assert newer.source_id == first.source_id
    versions = service.list_material_versions(search="合成参考")
    assert {item.id for item in versions} == {first.id, newer.id}


def test_teacher_can_import_and_relocate_a_controlled_material_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    client = _api_client(service)
    headers = {
        "content-type": "application/pdf",
        "x-upload-filename": quote("合成教材.pdf"),
        "x-display-name": quote("合成教材"),
        "x-request-token": "material-upload-0001",
        "x-file-modified-ms": "1760000000000",
    }

    imported = client.post(
        "/api/teaching-prep/materials/import-copy",
        headers=headers,
        content=b"%PDF-1.7 controlled synthetic material",
    )

    assert imported.status_code == 201
    payload = imported.json()
    assert payload["safe_filename"] == "合成教材.pdf"
    assert payload["availability"] == "available"
    assert str(tmp_path) not in json.dumps(payload, ensure_ascii=False)
    stored = service.catalog.get_material_location(payload["id"])
    assert stored.is_relative_to(service.paths["materials"])
    assert stored.read_bytes() == b"%PDF-1.7 controlled synthetic material"

    stored.unlink()
    assert service.refresh_material_availability(
        payload["id"]
    ).availability == "needs_relocation"
    relocated = client.post(
        f"/api/teaching-prep/materials/{payload['id']}/relocate-copy",
        headers={**headers, "x-request-token": "material-upload-0002"},
        content=b"%PDF-1.7 controlled synthetic material",
    )

    assert relocated.status_code == 200
    assert relocated.json()["id"] == payload["id"]
    assert relocated.json()["availability"] == "available"
    assert service.catalog.get_material_location(payload["id"]).is_file()


def test_teacher_can_permanently_delete_an_unused_controlled_material_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    client = _api_client(service)
    imported = client.post(
        "/api/teaching-prep/materials/import-copy",
        headers={
            "content-type": "application/pdf",
            "x-upload-filename": quote("待删除资料.pdf"),
            "x-display-name": quote("待删除资料"),
            "x-request-token": "material-delete-0001",
        },
        content=b"%PDF-1.7 controlled synthetic material",
    )
    payload = imported.json()
    controlled_copy = service.catalog.get_material_location(payload["id"])
    _curriculum, semester, _created = service.create_semester_workspace(
        request_token="material-delete-semester-0001",
        title="合成七年级下册",
        grade_level=7,
        volume="second",
        school_year="2026-2027",
        term="second",
        planned_new_lesson_count=60,
    )
    record, _created = service.attach_semester_material(
        semester.id,
        request_token="material-delete-record-0001",
        material_version_id=payload["id"],
        material_role="textbook",
    )
    with service.database.connect(immediate=True) as connection:
        connection.execute(
            """
            INSERT INTO teaching_prep_operations (
                operation_id, operation_type, idempotency_key,
                request_hash, status
            ) VALUES (?, 'semester_mapping', ?, ?, 'succeeded')
            """,
            ("delete-proposal-op-0001", "delete-proposal-key-0001", "a" * 64),
        )
        connection.execute(
            """
            INSERT INTO semester_mapping_proposals (
                id, semester_id, operation_id, source_state_sha256,
                status, payload_json
            ) VALUES (?, ?, ?, ?, 'proposed', ?)
            """,
            (
                "1" * 32,
                semester.id,
                "delete-proposal-op-0001",
                "b" * 64,
                json.dumps({"source_material_record_ids": [record.id]}),
            ),
        )

    preview = client.get(
        f"/api/teaching-prep/material-sources/{payload['source_id']}"
        f"/deletion-preview?expected_revision={payload['source_revision']}"
    )

    assert preview.status_code == 200, preview.text
    impact = preview.json()
    assert impact["source_id"] == payload["source_id"]
    assert impact["display_name"] == "待删除资料"
    assert impact["can_delete"] is True
    assert impact["owned_file_count"] == 1
    assert impact["impact_counts"]["material_sources"] == 1
    assert impact["impact_counts"]["material_versions"] == 1
    assert impact["impact_counts"]["semester_material_records"] == 1
    assert impact["impact_counts"]["semester_mapping_proposals"] == 1
    assert impact["generation_history_count"] == 0
    assert impact["preserved_history_note"] is None
    assert len(impact["preview_version"]) == 64
    assert str(tmp_path) not in json.dumps(impact, ensure_ascii=False)

    operation_id = "material-delete-operation-0001"
    deleted = client.request(
        "DELETE",
        f"/api/teaching-prep/material-sources/{payload['source_id']}",
        json={
            "expected_revision": payload["source_revision"],
            "operation_id": operation_id,
            "preview_version": impact["preview_version"],
            "confirmation_phrase": impact["confirmation_phrase"],
        },
    )

    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["operation_id"] == operation_id
    assert deleted.json()["status"] == "succeeded"
    assert deleted.json()["preview_version"] == impact["preview_version"]
    assert deleted.json()["deleted_source_id"] == payload["source_id"]
    assert service.list_material_versions(include_archived=True) == ()
    assert service.list_semester_materials(semester.id) == ()
    with service.database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM semester_mapping_proposals"
        ).fetchone()[0] == 0
    assert not controlled_copy.exists()

    receipt = client.get(
        f"/api/teaching-prep/material-deletions/{operation_id}"
    )
    replay = client.request(
        "DELETE",
        f"/api/teaching-prep/material-sources/{payload['source_id']}",
        json={
            "expected_revision": payload["source_revision"],
            "operation_id": operation_id,
            "preview_version": impact["preview_version"],
            "confirmation_phrase": impact["confirmation_phrase"],
        },
    )

    assert receipt.status_code == 200, receipt.text
    assert receipt.json() == deleted.json()
    assert replay.status_code == 200, replay.text
    assert replay.json() == deleted.json()


def test_material_deletion_running_receipt_keeps_the_fixed_count_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    source = tmp_path / "pending-delete.pdf"
    source.write_bytes(b"%PDF-1.7 synthetic pending delete")
    version, _created = service.register_material_file(
        request_token="material-delete-pending-0001",
        path=source,
        display_name="等待确认结果的资料",
    )
    impact = service.preview_material_deletion(
        version.source_id,
        expected_revision=version.source_revision,
    )
    operation_id = "material-delete-pending-operation-0001"
    assert service.catalog.begin_material_deletion(
        operation_id=operation_id,
        source_id=version.source_id,
        source_display_name=version.display_name,
        expected_revision=version.source_revision,
        preview_version=str(impact["preview_version"]),
        request_hash="d" * 64,
        impact=impact,
    ) is None

    receipt = service.get_material_deletion(operation_id)

    assert receipt["status"] == "running"
    assert receipt["counts"] == {
        "material_sources": 0,
        "material_versions": 0,
        "material_units": 0,
        "lesson_material_links": 0,
        "semester_material_records": 0,
        "semester_mapping_proposals": 0,
        "reference_ppt_collections": 0,
        "exercise_regions": 0,
        "exercise_candidates": 0,
    }


@pytest.mark.parametrize("interrupt_after_moves", [0, 1, 2])
def test_material_deletion_restart_restores_every_staged_file_and_converges(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    interrupt_after_moves: int,
) -> None:
    from PIL import Image

    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    source = service.paths["temp"] / "synthetic-delete-recovery.png"
    Image.new("RGB", (24, 24), "white").save(source)
    version, _created = service.import_material_copy(
        request_token="material-delete-recovery-0001",
        staged_path=source,
        original_filename=source.name,
        display_name="中断恢复合成资料",
    )
    unit = service.parse_material_version(version.id)[0]
    controlled_files = (
        service.catalog.get_material_location(version.id),
        service.material_preview_path(unit.id),
    )
    impact = service.preview_material_deletion(
        version.source_id,
        expected_revision=version.source_revision,
    )
    operation_id = "material-delete-recovery-operation-0001"
    staging = service.paths["staging"] / (
        "md-" + hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:16]
    )
    real_replace = os.replace
    moved = 0

    def process_exits_around_staging_move(
        source_path: str | os.PathLike[str],
        target_path: str | os.PathLike[str],
    ) -> None:
        nonlocal moved
        if Path(target_path).parent == staging:
            if interrupt_after_moves == 0:
                raise _SimulatedMaterialDeleteProcessExit
            real_replace(source_path, target_path)
            moved += 1
            if moved == interrupt_after_moves:
                raise _SimulatedMaterialDeleteProcessExit
            return
        real_replace(source_path, target_path)

    with monkeypatch.context() as crash:
        crash.setattr(os, "replace", process_exits_around_staging_move)
        with pytest.raises(_SimulatedMaterialDeleteProcessExit):
            service.delete_material_source(
                version.source_id,
                expected_revision=version.source_revision,
                operation_id=operation_id,
                preview_version=str(impact["preview_version"]),
                confirmation_phrase=str(impact["confirmation_phrase"]),
            )

    restarted = TeachingPrepService(service.root)
    receipt = restarted.get_material_deletion(operation_id)
    replay = restarted.delete_material_source(
        version.source_id,
        expected_revision=version.source_revision,
        operation_id=operation_id,
        preview_version=str(impact["preview_version"]),
        confirmation_phrase=str(impact["confirmation_phrase"]),
    )

    assert receipt["status"] == "interrupted"
    assert receipt["error_code"] == (
        "application_restarted_during_material_delete"
    )
    assert replay["status"] == "succeeded"
    assert replay["operation_id"] == operation_id
    assert restarted.get_material_deletion(operation_id) == replay
    assert restarted.list_material_versions(include_archived=True) == ()
    assert all(not path.exists() for path in controlled_files)
    assert not staging.exists()


def test_incomplete_material_delete_recovery_cannot_reclaim_the_operation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    source = service.paths["temp"] / "synthetic-incomplete-recovery.pdf"
    source.write_bytes(b"%PDF-1.7 synthetic incomplete recovery")
    version, _created = service.import_material_copy(
        request_token="material-delete-incomplete-recovery-0001",
        staged_path=source,
        original_filename=source.name,
        display_name="恢复不完整合成资料",
    )
    impact = service.preview_material_deletion(
        version.source_id,
        expected_revision=version.source_revision,
    )
    operation_id = "material-delete-incomplete-operation-0001"
    staging = service.paths["staging"] / (
        "md-" + hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:16]
    )
    real_replace = os.replace

    def process_exits_after_move(
        source_path: str | os.PathLike[str],
        target_path: str | os.PathLike[str],
    ) -> None:
        real_replace(source_path, target_path)
        if Path(target_path).parent == staging:
            raise _SimulatedMaterialDeleteProcessExit

    with monkeypatch.context() as crash:
        crash.setattr(os, "replace", process_exits_after_move)
        with pytest.raises(_SimulatedMaterialDeleteProcessExit):
            service.delete_material_source(
                version.source_id,
                expected_revision=version.source_revision,
                operation_id=operation_id,
                preview_version=str(impact["preview_version"]),
                confirmation_phrase=str(impact["confirmation_phrase"]),
            )

    next(staging.iterdir()).unlink()
    restarted = TeachingPrepService(service.root)
    receipt = restarted.get_material_deletion(operation_id)
    replay = restarted.delete_material_source(
        version.source_id,
        expected_revision=version.source_revision,
        operation_id=operation_id,
        preview_version=str(impact["preview_version"]),
        confirmation_phrase=str(impact["confirmation_phrase"]),
    )

    assert receipt["status"] == "interrupted"
    assert receipt["error_code"] == "material_delete_recovery_incomplete"
    assert replay == receipt
    assert restarted.get_material_version(version.id).id == version.id


def test_material_deletion_restart_cleans_staging_after_database_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    source = service.paths["temp"] / "synthetic-delete-committed.pdf"
    source.write_bytes(b"%PDF-1.7 synthetic committed delete")
    version, _created = service.import_material_copy(
        request_token="material-delete-committed-0001",
        staged_path=source,
        original_filename=source.name,
        display_name="已提交删除合成资料",
    )
    impact = service.preview_material_deletion(
        version.source_id,
        expected_revision=version.source_revision,
    )
    operation_id = "material-delete-committed-operation-0001"
    staging = service.paths["staging"] / (
        "md-" + hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:16]
    )
    real_delete = service.catalog.delete_material_source

    def process_exits_after_database_commit(*args, **kwargs):
        real_delete(*args, **kwargs)
        raise _SimulatedMaterialDeleteProcessExit

    with monkeypatch.context() as crash:
        crash.setattr(
            service.catalog,
            "delete_material_source",
            process_exits_after_database_commit,
        )
        with pytest.raises(_SimulatedMaterialDeleteProcessExit):
            service.delete_material_source(
                version.source_id,
                expected_revision=version.source_revision,
                operation_id=operation_id,
                preview_version=str(impact["preview_version"]),
                confirmation_phrase=str(impact["confirmation_phrase"]),
            )

    assert any(staging.iterdir())
    restarted = TeachingPrepService(service.root)
    receipt = restarted.get_material_deletion(operation_id)

    assert receipt["status"] == "succeeded"
    assert restarted.list_material_versions(include_archived=True) == ()
    assert not staging.exists()


def test_material_delete_waits_until_active_parsing_has_finished(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    original = tmp_path / "synthetic-active-parse.pdf"
    original.write_bytes(b"%PDF-1.7 controlled synthetic material")
    version, _created = service.register_material_file(
        request_token="material-delete-active-0001",
        path=original,
        display_name="正在解析的资料",
    )
    controlled_copy = service.catalog.get_material_location(version.id)
    impact = service.preview_material_deletion(
        version.source_id,
        expected_revision=version.source_revision,
    )
    service._active_material_parses.add(version.id)

    with pytest.raises(TeachingPrepConflictError):
        service.delete_material_source(
            version.source_id,
            expected_revision=version.source_revision,
            operation_id="material-delete-active-operation-0001",
            preview_version=str(impact["preview_version"]),
            confirmation_phrase=str(impact["confirmation_phrase"]),
        )

    assert controlled_copy.is_file()
    assert service.catalog.get_material_version(version.id).id == version.id


def test_material_import_rejects_path_metadata_and_oversized_body(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    client = _api_client(service)
    base_headers = {
        "content-type": "application/pdf",
        "x-display-name": "unsafe",
        "x-request-token": "material-upload-unsafe",
    }

    unsafe = client.post(
        "/api/teaching-prep/materials/import-copy",
        headers={
            **base_headers,
            "x-upload-filename": quote("../outside.pdf"),
        },
        content=b"%PDF-1.7 synthetic",
    )
    too_large = client.post(
        "/api/teaching-prep/materials/import-copy",
        headers={
            **base_headers,
            "x-upload-filename": "large.pdf",
            "content-length": str(256 * 1024 * 1024 + 1),
        },
        content=b"small test body",
    )

    assert unsafe.status_code == 422
    assert too_large.status_code == 413
    assert list(service.paths["temp"].glob("material-upload-*")) == []
