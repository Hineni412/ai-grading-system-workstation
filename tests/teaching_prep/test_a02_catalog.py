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
from backend.teaching_prep.infrastructure.fakes import FakeWpsAdapter

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


@pytest.mark.parametrize("terminal_status", ["published", "failed", "cancelled"])
def test_material_deletion_preserves_terminal_generation_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    terminal_status: str,
) -> None:
    from .test_a08_pptx_execution import _approved_plan

    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    client = _api_client(service)
    plan = _approved_plan(service, tmp_path)
    source_record = plan.payload["source_presentations"][0]
    version = service.get_material_version(
        str(source_record["material_version_id"])
    )
    if terminal_status == "published":
        service.wps_adapter = FakeWpsAdapter()
        run, published, created = service.execute_slide_plan(
            plan.id,
            operation_id="material-history-published-run",
            confirmed=True,
        )
        assert created is True
        assert published is not None
    else:
        run, created = service.pptx_executions.begin(
            operation_id=f"material-history-{terminal_status}-run",
            request_hash="c" * 64,
            slide_plan_id=plan.id,
            source_material_version_id=version.id,
            source_sha256=version.content_sha256,
            expected_slide_count=1,
        )
        assert created is True
        if terminal_status == "failed":
            service.pptx_executions.fail(run.id, "synthetic_terminal_failure")
        else:
            service.pptx_executions.cancel(run.id)
        run = service.pptx_executions.get(run.id)
    assert run.status == terminal_status

    preview = client.get(
        f"/api/teaching-prep/material-sources/{version.source_id}"
        f"/deletion-preview?expected_revision={version.source_revision}"
    )

    assert preview.status_code == 200, preview.text
    impact = preview.json()
    assert impact["can_delete"] is True
    assert impact["blocker_code"] is None
    assert impact["generation_history_count"] == 1
    assert impact["preserved_snapshot_count"] == 1
    assert impact["blocking_generation_count"] == 0
    note = str(impact["preserved_history_note"])
    assert "资料名" in note
    assert "安全文件名" in note
    assert "版本标识" in note
    assert "内容指纹" in note
    assert "不保留原文件" in note
    assert "绝对路径" in note
    assert "无法恢复" in note
    assert str(tmp_path) not in json.dumps(impact, ensure_ascii=False)

    deleted = client.request(
        "DELETE",
        f"/api/teaching-prep/material-sources/{version.source_id}",
        json={
            "expected_revision": version.source_revision,
            "operation_id": f"material-history-delete-{terminal_status}",
            "preview_version": impact["preview_version"],
            "confirmation_phrase": impact["confirmation_phrase"],
        },
    )

    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["status"] == "succeeded"
    assert service.pptx_executions.get(run.id).status == terminal_status
    assert all(
        item.source_id != version.source_id
        for item in service.list_material_versions(include_archived=True)
    )
    assert not service._execution_staging(run.id).exists()
    with service.database.connect() as connection:
        snapshot = connection.execute(
            """
            SELECT material_version_id, source_id, display_name, file_name,
                   material_type, content_sha256
            FROM pptx_execution_source_snapshots
            WHERE material_version_id = ?
            """,
            (version.id,),
        ).fetchone()
    assert snapshot is not None
    assert tuple(snapshot) == (
        version.id,
        version.source_id,
        version.display_name,
        version.file_name,
        version.material_type,
        version.content_sha256,
    )
    if terminal_status == "cancelled":
        original_path = tmp_path / "a05-reference.pptx"
        reimported, reimport_created = service.register_material_file(
            request_token="material-history-reimport-same-bytes",
            path=original_path,
            display_name="重新导入的同字节课件",
        )
        assert reimport_created is True
        assert reimported.id != version.id
        assert reimported.source_id != version.source_id
        assert reimported.content_sha256 == version.content_sha256


@pytest.mark.parametrize("crash_point", ["before_commit", "after_commit"])
def test_terminal_generation_staging_follows_durable_material_deletion_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    crash_point: str,
) -> None:
    from .test_a08_pptx_execution import _approved_plan

    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    plan = _approved_plan(service, tmp_path)
    source_record = plan.payload["source_presentations"][0]
    version = service.get_material_version(
        str(source_record["material_version_id"])
    )
    run, created = service.pptx_executions.begin(
        operation_id=f"material-staging-{crash_point}-run",
        request_hash="e" * 64,
        slide_plan_id=plan.id,
        source_material_version_id=version.id,
        source_sha256=version.content_sha256,
        expected_slide_count=1,
    )
    assert created is True
    service.pptx_executions.fail(run.id, "synthetic_terminal_failure")
    execution_staging = service._execution_staging(run.id)
    preview_dir = execution_staging / "wps-previews"
    preview_dir.mkdir(parents=True)
    source_copy = execution_staging / "source-copy.pptx"
    source_copy.write_bytes(b"synthetic retained source copy")
    (preview_dir / "slide-00001.png").write_bytes(b"synthetic preview")
    impact = service.preview_material_deletion(
        version.source_id,
        expected_revision=version.source_revision,
    )
    operation_id = f"material-staging-{crash_point}-delete"
    real_replace = os.replace
    real_delete = service.catalog.delete_material_source

    def stop_after_first_move(source_path, target_path) -> None:
        real_replace(source_path, target_path)
        if Path(target_path).parent.name.startswith("md-"):
            raise _SimulatedMaterialDeleteProcessExit

    def stop_after_database_commit(*args, **kwargs):
        result = real_delete(*args, **kwargs)
        raise _SimulatedMaterialDeleteProcessExit from None

    with monkeypatch.context() as crash:
        if crash_point == "before_commit":
            crash.setattr(os, "replace", stop_after_first_move)
        else:
            crash.setattr(
                service.catalog,
                "delete_material_source",
                stop_after_database_commit,
            )
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
    if crash_point == "before_commit":
        assert receipt["status"] == "interrupted"
        assert source_copy.read_bytes() == b"synthetic retained source copy"
        assert restarted.get_material_version(version.id).id == version.id
        receipt = restarted.delete_material_source(
            version.source_id,
            expected_revision=version.source_revision,
            operation_id=operation_id,
            preview_version=str(impact["preview_version"]),
            confirmation_phrase=str(impact["confirmation_phrase"]),
        )

    assert receipt["status"] == "succeeded"
    assert all(
        item.source_id != version.source_id
        for item in restarted.list_material_versions(include_archived=True)
    )
    retained_run = restarted.get_pptx_execution(run.id)
    assert retained_run.status == "failed"
    assert retained_run.staging_retained is False
    assert not execution_staging.exists()


def test_terminal_generation_deletion_scrubs_only_deleted_source_payload_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from .test_a08_pptx_execution import _approved_plan

    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    plan = _approved_plan(service, tmp_path)
    source_record = plan.payload["source_presentations"][0]
    version = service.get_material_version(
        str(source_record["material_version_id"])
    )
    service.wps_adapter = FakeWpsAdapter()
    run, published, created = service.execute_slide_plan(
        plan.id,
        operation_id="material-payload-scrub-published-run",
        confirmed=True,
    )
    assert created is True
    assert run.status == "published"
    assert published is not None
    baseline = service.preview_material_deletion(
        version.source_id,
        expected_revision=version.source_revision,
    )
    with service.database.connect(immediate=True) as connection:
        pack_row = connection.execute(
            "SELECT payload_json FROM resource_pack_versions WHERE id = ?",
            (plan.resource_pack_id,),
        ).fetchone()
        plan_row = connection.execute(
            "SELECT payload_json FROM slide_plan_versions WHERE id = ?",
            (plan.id,),
        ).fetchone()
        assert pack_row is not None
        assert plan_row is not None
        pack_payload = json.loads(str(pack_row["payload_json"]))
        plan_payload = json.loads(str(plan_row["payload_json"]))
        deleted_material = next(
            item
            for item in pack_payload["materials"]
            if item["material_version_id"] == version.id
        )
        retained_material = next(
            item
            for item in pack_payload["materials"]
            if item["material_version_id"] != version.id
        )
        retained_material_before = json.loads(
            json.dumps(retained_material, ensure_ascii=False)
        )
        deleted_unit = deleted_material["units"][0]
        deleted_unit_id = str(deleted_unit["unit_id"])
        deleted_unit.update(
            {
                "text": "DELETED_RESOURCE_PACK_TEXT",
                "extracted_text": "DELETED_RESOURCE_PACK_EXTRACTED_TEXT",
                "object_summary": {"secret": "DELETED_OBJECT_SUMMARY"},
                "preview_url": f"/deleted-preview/{deleted_unit_id}",
            }
        )
        deleted_slide = next(
            item
            for item in plan_payload["slides"]
            if item["material_version_id"] == version.id
        )
        deleted_slide.update(
            {
                "text_summary": "DELETED_SLIDE_TEXT_SUMMARY",
                "extracted_text": "DELETED_SLIDE_EXTRACTED_TEXT",
                "object_summary": {"secret": "DELETED_SLIDE_OBJECT"},
                "preview_url": f"/deleted-slide-preview/{deleted_unit_id}",
            }
        )
        connection.execute(
            "UPDATE resource_pack_versions SET payload_json = ? WHERE id = ?",
            (
                json.dumps(
                    pack_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                plan.resource_pack_id,
            ),
        )
        connection.execute(
            "UPDATE slide_plan_versions SET payload_json = ? WHERE id = ?",
            (
                json.dumps(
                    plan_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                plan.id,
            ),
        )

    impact = service.preview_material_deletion(
        version.source_id,
        expected_revision=version.source_revision,
    )

    assert impact["preview_version"] != baseline["preview_version"]
    deleted = service.delete_material_source(
        version.source_id,
        expected_revision=version.source_revision,
        operation_id="material-payload-scrub-delete",
        preview_version=str(impact["preview_version"]),
        confirmation_phrase=str(impact["confirmation_phrase"]),
    )
    assert deleted["status"] == "succeeded"
    assert service.get_material_deletion(
        "material-payload-scrub-delete"
    )["preview_version"] == impact["preview_version"]
    assert service.pptx_executions.get(run.id).status == "published"
    output, _filename = service.pptx_download(published.id)
    assert output.is_file()

    with service.database.connect() as connection:
        payload_rows = connection.execute(
            """
            SELECT 'resource_pack_versions' AS table_name, id, payload_json
            FROM resource_pack_versions
            UNION ALL
            SELECT 'slide_plan_versions' AS table_name, id, payload_json
            FROM slide_plan_versions
            ORDER BY table_name, id
            """
        ).fetchall()
    serialized_payloads = "\n".join(
        str(row["payload_json"]) for row in payload_rows
    )
    for forbidden in (
        "DELETED_RESOURCE_PACK_TEXT",
        "DELETED_RESOURCE_PACK_EXTRACTED_TEXT",
        "DELETED_OBJECT_SUMMARY",
        "DELETED_SLIDE_TEXT_SUMMARY",
        "DELETED_SLIDE_EXTRACTED_TEXT",
        "DELETED_SLIDE_OBJECT",
        deleted_unit_id,
    ):
        assert forbidden not in serialized_payloads
    scrubbed_pack = json.loads(
        next(
            str(row["payload_json"])
            for row in payload_rows
            if row["table_name"] == "resource_pack_versions"
            and row["id"] == plan.resource_pack_id
        )
    )
    scrubbed_plan = json.loads(
        next(
            str(row["payload_json"])
            for row in payload_rows
            if row["table_name"] == "slide_plan_versions"
            and row["id"] == plan.id
        )
    )
    retained_material_after = next(
        item
        for item in scrubbed_pack["materials"]
        if item["material_version_id"] != version.id
    )
    assert retained_material_after == retained_material_before
    scrubbed_unit = next(
        item
        for item in scrubbed_pack["materials"]
        if item["material_version_id"] == version.id
    )["units"][0]
    assert scrubbed_unit["unit_id"] is None
    assert scrubbed_unit["text"] == ""
    assert scrubbed_unit["extracted_text"] == ""
    assert scrubbed_unit["object_summary"] == {}
    assert scrubbed_unit["preview_url"] is None
    scrubbed_slide = next(
        item
        for item in scrubbed_plan["slides"]
        if item["material_version_id"] == version.id
    )
    assert scrubbed_slide["material_unit_id"] is None
    assert scrubbed_slide["text_summary"] == ""
    assert scrubbed_slide["extracted_text"] == ""
    assert scrubbed_slide["object_summary"] == {}
    assert scrubbed_slide["preview_url"] is None


@pytest.mark.parametrize(
    "blocking_status",
    ["running", "verifying", "publishing", "interrupted"],
)
def test_material_deletion_blocks_active_or_recoverable_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    blocking_status: str,
) -> None:
    from .test_a08_pptx_execution import _approved_plan

    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    client = _api_client(service)
    plan = _approved_plan(service, tmp_path)
    source_record = plan.payload["source_presentations"][0]
    version = service.get_material_version(
        str(source_record["material_version_id"])
    )
    run, created = service.pptx_executions.begin(
        operation_id=f"material-blocking-{blocking_status}-run",
        request_hash="b" * 64,
        slide_plan_id=plan.id,
        source_material_version_id=version.id,
        source_sha256=version.content_sha256,
        expected_slide_count=1,
    )
    assert created is True
    if blocking_status != "running":
        with service.database.connect(immediate=True) as connection:
            connection.execute(
                "UPDATE pptx_execution_runs SET status = ? WHERE id = ?",
                (blocking_status, run.id),
            )

    preview = client.get(
        f"/api/teaching-prep/material-sources/{version.source_id}"
        f"/deletion-preview?expected_revision={version.source_revision}"
    )

    assert preview.status_code == 200, preview.text
    impact = preview.json()
    assert impact["generation_history_count"] == 1
    assert impact["preserved_snapshot_count"] == 1
    assert impact["blocking_generation_count"] == 1
    assert impact["can_delete"] is False
    assert impact["blocker_code"] == "active_courseware_generation"

    blocked = client.request(
        "DELETE",
        f"/api/teaching-prep/material-sources/{version.source_id}",
        json={
            "expected_revision": version.source_revision,
            "operation_id": f"material-blocking-{blocking_status}-delete",
            "preview_version": impact["preview_version"],
            "confirmation_phrase": impact["confirmation_phrase"],
        },
    )

    assert blocked.status_code == 409, blocked.text
    assert service.get_material_version(version.id).id == version.id
    if blocking_status == "interrupted":
        service.pptx_executions.fail(run.id, "teacher_abandoned_recovery")
        refreshed = service.preview_material_deletion(
            version.source_id,
            expected_revision=version.source_revision,
        )
        assert refreshed["preview_version"] != impact["preview_version"]
        assert refreshed["blocking_generation_count"] == 0
        assert refreshed["can_delete"] is True


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
