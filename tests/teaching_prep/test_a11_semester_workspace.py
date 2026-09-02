from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.teaching_prep.api.router import create_router
from backend.teaching_prep.domain.errors import (
    TeachingPrepConflictError,
)

from .test_a01_foundation import _migrated_service
from .test_a02_catalog import _api_client, _lesson_tree
from .test_a03_material_units import _pdf


def _semester(service):
    curriculum_id, _chapter_id, _section_id, lesson_ids = _lesson_tree(service)
    semester, created = service.create_semester(
        request_token="semester-2026-first",
        curriculum_id=curriculum_id,
        school_year="2026-2027",
        term="first",
        planned_new_lesson_count=48,
    )
    assert created is True
    return semester, lesson_ids


def test_semester_workspace_is_atomic_and_survives_a_fresh_submit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    kwargs = {
        "title": "合成八年级上册",
        "grade_level": 8,
        "volume": "first",
        "publisher": "人教版",
        "edition_label": "2026",
        "school_year": "2026-2027",
        "term": "first",
        "planned_new_lesson_count": 48,
    }

    curriculum, semester, created = service.create_semester_workspace(
        request_token="semester-workspace-first-0001",
        **kwargs,
    )
    repeated_curriculum, repeated_semester, repeated_created = (
        service.create_semester_workspace(
            request_token="semester-workspace-fresh-0002",
            **kwargs,
        )
    )

    assert created is True
    assert repeated_created is False
    assert repeated_curriculum.id == curriculum.id
    assert repeated_semester.id == semester.id
    assert len(service.list_curricula()) == 1
    assert len(service.list_semesters()) == 1


def test_semester_workspace_rolls_back_when_semester_insert_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    with service.database.connect(immediate=True) as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_semester_workspace
            BEFORE INSERT ON teaching_semesters
            BEGIN
                SELECT RAISE(ABORT, 'synthetic semester insert failure');
            END
            """
        )
    try:
        with pytest.raises(sqlite3.IntegrityError):
            service.create_semester_workspace(
                request_token="semester-workspace-rollback-0001",
                title="不应保留的教材",
                grade_level=8,
                volume="first",
                publisher=None,
                edition_label=None,
                school_year="2026-2027",
                term="first",
                planned_new_lesson_count=48,
            )
    finally:
        with service.database.connect(immediate=True) as connection:
            connection.execute("DROP TRIGGER reject_semester_workspace")

    assert service.list_curricula() == ()
    assert service.list_semesters() == ()


def test_semester_workspace_reuses_a_legacy_orphan_curriculum(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    orphan, created = service.create_curriculum(
        request_token="legacy-orphan-curriculum-0001",
        title="合成七年级上册",
        grade_level=7,
        volume="first",
        publisher="人教版",
        edition_label=None,
    )
    curriculum, semester, workspace_created = service.create_semester_workspace(
        request_token="semester-workspace-orphan-0001",
        title="合成七年级上册",
        grade_level=7,
        volume="first",
        publisher="人教版",
        edition_label=None,
        school_year="2026-2027",
        term="first",
        planned_new_lesson_count=48,
    )

    assert created is True
    assert workspace_created is True
    assert curriculum.id == orphan.id
    assert semester.curriculum_id == orphan.id
    assert len(service.list_curricula()) == 1


def test_semester_workspace_rejects_a_second_curriculum_for_same_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    service.create_semester_workspace(
        request_token="semester-workspace-scope-0001",
        title="合成八年级上册",
        grade_level=8,
        volume="first",
        publisher="人教版",
        edition_label=None,
        school_year="2026-2027",
        term="first",
        planned_new_lesson_count=48,
    )

    with pytest.raises(TeachingPrepConflictError, match="already exists"):
        service.create_semester_workspace(
            request_token="semester-workspace-scope-0002",
            title="合成八年级上册",
            grade_level=8,
            volume="first",
            publisher="北师版",
            edition_label=None,
            school_year="2026-2027",
            term="first",
            planned_new_lesson_count=48,
        )

    assert len(service.list_curricula()) == 1
    assert len(service.list_semesters()) == 1


def test_semester_summary_tracks_plan_and_lesson_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    semester, lesson_ids = _semester(service)

    preparing = service.set_semester_lesson_progress(
        semester.id,
        lesson_ids[0],
        status="preparing",
        expected_revision=None,
    )
    repeated = service.set_semester_lesson_progress(
        semester.id,
        lesson_ids[0],
        status="preparing",
        expected_revision=None,
    )
    service.set_semester_lesson_progress(
        semester.id,
        lesson_ids[1],
        status="taught",
        expected_revision=None,
    )

    assert repeated == preparing
    summary = service.list_semesters()[0]
    assert summary.planned_new_lesson_count == 48
    assert summary.active_lesson_count == 3
    assert summary.preparing_lesson_count == 1
    assert summary.taught_lesson_count == 1
    assert summary.not_started_lesson_count == 1

    with pytest.raises(TeachingPrepConflictError):
        service.set_semester_lesson_progress(
            semester.id,
            lesson_ids[0],
            status="ready",
            expected_revision=None,
        )

    ready = service.set_semester_lesson_progress(
        semester.id,
        lesson_ids[0],
        status="ready",
        expected_revision=preparing.revision,
    )
    assert ready.status == "ready"
    assert ready.revision == preparing.revision + 1


def test_semester_materials_are_incremental_and_homework_role_is_unique(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    semester, _lesson_ids = _semester(service)
    first_path = _pdf(tmp_path / "workbook-a.pdf", ["A-1", "A-2"])
    second_path = _pdf(tmp_path / "workbook-b.pdf", ["B-1", "B-2"])
    first, _created = service.register_material_file(
        request_token="semester-material-file-a",
        path=first_path,
        display_name="合成日常作业教辅",
    )
    second, _created = service.register_material_file(
        request_token="semester-material-file-b",
        path=second_path,
        display_name="合成补充教辅",
    )

    attached, created = service.attach_semester_material(
        semester.id,
        request_token="semester-material-attach-a",
        material_version_id=first.id,
        material_role="homework_workbook",
    )
    repeated, repeated_created = service.attach_semester_material(
        semester.id,
        request_token="semester-material-attach-a",
        material_version_id=first.id,
        material_role="homework_workbook",
    )

    assert created is True
    assert repeated_created is False
    assert repeated == attached
    assert attached.parse_status == "not_started"
    with pytest.raises(TeachingPrepConflictError):
        service.attach_semester_material(
            semester.id,
            request_token="semester-material-attach-b",
            material_version_id=second.id,
            material_role="homework_workbook",
        )

    supplement, _created = service.attach_semester_material(
        semester.id,
        request_token="semester-material-attach-c",
        material_version_id=second.id,
        material_role="exercise_workbook",
    )
    assert supplement.material_role == "exercise_workbook"
    assert len(service.list_semester_materials(semester.id)) == 2


def test_daily_workbook_a_and_b_are_separate_recoverable_volumes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    semester, lesson_ids = _semester(service)
    versions = []
    for volume in ("A", "B", "A-copy"):
        path = _pdf(tmp_path / f"daily-{volume}.pdf", [volume])
        version, _created = service.register_material_file(
            request_token=f"daily-file-{volume}",
            path=path,
            display_name=f"全品学练考 {volume}",
        )
        versions.append(version)

    a_record, _created = service.attach_semester_material(
        semester.id,
        request_token="daily-attach-a",
        material_version_id=versions[0].id,
        material_role="homework_workbook",
        workbook_series="全品学练考",
        workbook_volume="A",
    )
    b_record, _created = service.attach_semester_material(
        semester.id,
        request_token="daily-attach-b",
        material_version_id=versions[1].id,
        material_role="homework_workbook",
        workbook_series="全品学练考",
        workbook_volume="B",
    )

    assert (a_record.material_role, a_record.workbook_volume) == (
        "homework_workbook", "A"
    )
    assert (b_record.material_role, b_record.workbook_volume) == (
        "homework_workbook", "B"
    )
    service.parse_material_version(versions[0].id)
    link, _created = service.create_material_link(
        request_token="daily-link-a",
        lesson_node_id=lesson_ids[0],
        material_version_id=versions[0].id,
        start_unit=1,
        end_unit=1,
        crop=None,
        purpose="exercise",
        teacher_note=None,
        confirmation_status="confirmed",
    )
    pack, _created = service.freeze_resource_pack(
        request_token="daily-pack-a",
        lesson_node_id=lesson_ids[0],
        class_name=None,
        lesson_type="new_lesson",
        teacher_context=None,
        reference_ppt_intents={},
        question_ids=[],
        assessment_ids=[],
        knowledge_scope=[],
        selected_material_link_ids=[link.id],
        selected_exercise_candidate_ids=[],
    )
    assert pack.payload["materials"][0]["semester_material_role"] == (
        "homework_workbook"
    )
    with pytest.raises(TeachingPrepConflictError):
        service.attach_semester_material(
            semester.id,
            request_token="daily-attach-a-copy",
            material_version_id=versions[2].id,
            material_role="homework_workbook",
            workbook_series="全品学练考",
            workbook_volume="A",
        )

    with pytest.raises(TeachingPrepConflictError):
        service.update_material_source(
            versions[0].source_id,
            expected_revision=1,
            display_name=None,
            archived=True,
        )
    current_a = next(
        item for item in service.list_semester_materials(semester.id)
        if item.id == a_record.id
    )
    service.update_semester_material(
        current_a.id,
        expected_revision=current_a.revision,
        material_role=current_a.material_role,
        mapping_status=current_a.mapping_status,
        is_active=False,
        workbook_series=current_a.workbook_series,
        workbook_volume=current_a.workbook_volume,
    )
    archived = service.update_material_source(
        versions[0].source_id,
        expected_revision=1,
        display_name=None,
        archived=True,
    )
    assert archived.source_archived_at is not None
    inactive = next(
        item for item in service.list_semester_materials(semester.id)
        if item.id == a_record.id
    )
    with pytest.raises(TeachingPrepConflictError):
        service.update_semester_material(
            inactive.id,
            expected_revision=inactive.revision,
            material_role=inactive.material_role,
            mapping_status=inactive.mapping_status,
            is_active=True,
            workbook_series=inactive.workbook_series,
            workbook_volume=inactive.workbook_volume,
        )
    restored = service.update_material_source(
        versions[0].source_id,
        expected_revision=archived.source_revision,
        display_name="全品学练考 A 本",
        archived=False,
    )
    assert restored.source_archived_at is None
    assert restored.display_name == "全品学练考 A 本"


def test_parse_status_follows_new_versions_without_overwriting_mapping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    semester, _lesson_ids = _semester(service)
    first_path = _pdf(tmp_path / "textbook-v1.pdf", ["V1-1", "V1-2"])
    first, _created = service.register_material_file(
        request_token="semester-version-file-1",
        path=first_path,
        display_name="合成教材",
    )
    record, _created = service.attach_semester_material(
        semester.id,
        request_token="semester-version-attach",
        material_version_id=first.id,
        material_role="textbook",
    )

    service.parse_material_version(first.id)
    parsed = service.list_semester_materials(semester.id)[0]
    assert parsed.parse_status == "parsed"
    assert parsed.last_parsed_version_id == first.id
    response = _api_client(service).get(
        f"/api/teaching-prep/semesters/{semester.id}/materials"
    )
    assert response.status_code == 200
    public_record = response.json()["items"][0]
    assert public_record["safe_filename"] == "textbook-v1.pdf"
    assert "current_file_name" not in public_record
    assert str(tmp_path) not in response.text
    confirmed = service.update_semester_material(
        parsed.id,
        expected_revision=parsed.revision,
        material_role="textbook",
        mapping_status="confirmed",
        is_active=True,
    )

    second_path = _pdf(tmp_path / "textbook-v2.pdf", ["V2-1", "V2-2"])
    second, created = service.register_material_file(
        request_token="semester-version-file-2",
        path=second_path,
        source_id=first.source_id,
        display_name="合成教材",
    )
    assert created is True
    before_parse = service.list_semester_materials(semester.id)[0]
    assert before_parse.has_unparsed_update is True
    assert before_parse.mapping_status == confirmed.mapping_status

    service.parse_material_version(second.id)
    after_parse = service.list_semester_materials(semester.id)[0]
    assert after_parse.last_parsed_version_id == second.id
    assert after_parse.has_unparsed_update is False
    assert after_parse.mapping_status == "needs_review"


def test_semester_api_returns_state_without_local_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    semester, lesson_ids = _semester(service)
    client = _api_client(service)

    progress = client.put(
        (
            f"/api/teaching-prep/semesters/{semester.id}"
            f"/lesson-progress/{lesson_ids[0]}"
        ),
        json={"status": "preparing", "expected_revision": None},
    )
    listed = client.get("/api/teaching-prep/semesters")

    assert progress.status_code == 200
    assert listed.status_code == 200
    payload = listed.json()["items"][0]
    assert payload["school_year"] == "2026-2027"
    assert payload["preparing_lesson_count"] == 1
    assert str(tmp_path) not in listed.text


def test_semester_workspace_api_creates_once_and_reopens_same_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    client = _api_client(service)
    body = {
        "request_token": "semester-workspace-api-first-0001",
        "curriculum": {
            "title": "合成八年级上册",
            "grade_level": 8,
            "volume": "first",
            "publisher": "人教版",
            "edition_label": None,
        },
        "school_year": "2026-2027",
        "term": "first",
        "planned_new_lesson_count": 48,
    }

    created = client.post("/api/teaching-prep/semester-workspaces", json=body)
    reopened = client.post(
        "/api/teaching-prep/semester-workspaces",
        json={
            **body,
            "request_token": "semester-workspace-api-fresh-0002",
        },
    )

    assert created.status_code == 201
    assert reopened.status_code == 200
    assert reopened.json()["curriculum"]["id"] == created.json()["curriculum"]["id"]
    assert reopened.json()["semester"]["id"] == created.json()["semester"]["id"]
    assert str(tmp_path) not in reopened.text
