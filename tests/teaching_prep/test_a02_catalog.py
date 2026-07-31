from __future__ import annotations

import json
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
