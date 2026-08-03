from __future__ import annotations

import sqlite3
from pathlib import Path
from threading import Event, Thread

import pytest

from backend.teaching_prep.domain.errors import (
    TeachingPrepConflictError,
    TeachingPrepRetryAvailableError,
    TeachingPrepStateError,
    TeachingPrepValidationError,
)
from backend.teaching_prep.application import TeachingPrepService

from .test_a01_foundation import _migrated_service
from .test_a02_catalog import _api_client, _lesson_tree
from .test_a03_material_units import _pdf


class _FakeSemesterMappingModel:
    def __init__(
        self,
        payload: dict[str, object],
        *,
        failure: Exception | None = None,
    ) -> None:
        self.payload = payload
        self.failure = failure
        self.calls: list[dict[str, object]] = []

    def generate(
        self,
        *,
        operation_id: str,
        semester_snapshot: dict[str, object],
    ) -> dict[str, object]:
        self.calls.append(
            {
                "operation_id": operation_id,
                "snapshot": semester_snapshot,
            }
        )
        if self.failure is not None:
            raise self.failure
        return self.payload


def _accept_all_mappings(service, proposal):
    current = proposal
    for item in tuple(current.payload["mappings"]):
        current = service.review_semester_mapping_row(
            current.id,
            str(item["mapping_id"]),
            expected_revision=current.revision,
            decision={
                "decision": "accepted",
                "lesson_ref": item["lesson_ref"],
                "start_unit": item["start_unit"],
                "end_unit": item["end_unit"],
                "reason": "合成测试逐条接受",
            },
        )
    return current


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


def test_mapping_model_proposes_existing_lesson_ranges_once_then_teacher_applies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, base_service = _migrated_service(tmp_path, monkeypatch)
    semester, lesson_ids = _semester(base_service)
    version, _created = base_service.register_material_file(
        request_token="mapping-existing-file",
        path=_pdf(tmp_path / "existing-workbook.pdf", ["L1", "L2", "L3"]),
        display_name="合成新增教辅",
    )
    record, _created = base_service.attach_semester_material(
        semester.id,
        request_token="mapping-existing-attach",
        material_version_id=version.id,
        material_role="exercise_workbook",
    )
    base_service.parse_material_version(version.id)
    fake = _FakeSemesterMappingModel(
        {
            "tree": [],
            "mappings": [
                {
                    "material_record_id": record.id,
                    "lesson_ref": lesson_ids[0],
                    "start_unit": 1,
                    "end_unit": 2,
                }
            ],
            "uncertainties": [],
        }
    )
    service = TeachingPrepService(
        paths.workspace_dir("teaching-prep"),
        semester_mapping_model_adapter=fake,
        semester_mapping_model_label="fake-semester-model",
    )

    proposal, created = service.generate_semester_mapping_proposal(
        semester.id,
        operation_id="semester-mapping-existing-0001",
        material_record_ids=[record.id],
    )
    repeated, repeated_created = service.generate_semester_mapping_proposal(
        semester.id,
        operation_id="semester-mapping-existing-0001",
        material_record_ids=[record.id],
    )
    refreshed, refreshed_created = service.generate_semester_mapping_proposal(
        semester.id,
        operation_id="semester-mapping-existing-refresh-0002",
        material_record_ids=[record.id],
    )

    assert created is True
    assert repeated_created is False
    assert repeated.id == proposal.id
    assert refreshed_created is False
    assert refreshed.id == proposal.id
    assert len(fake.calls) == 1
    reviewed = _accept_all_mappings(service, proposal)
    applied = service.apply_semester_mapping_proposal(
        reviewed.id,
        expected_revision=reviewed.revision,
    )
    assert applied.status == "applied"
    links = service.list_material_links(lesson_ids[0])
    assert [(item.start_unit, item.end_unit, item.purpose) for item in links] == [
        (1, 2, "exercise")
    ]
    updated_record = service.list_semester_materials(semester.id)[0]
    assert updated_record.mapping_status == "confirmed"


def test_failed_semester_mapping_allows_an_explicit_new_operation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, base_service = _migrated_service(tmp_path, monkeypatch)
    semester, lesson_ids = _semester(base_service)
    version, _created = base_service.register_material_file(
        request_token="mapping-retry-file",
        path=_pdf(tmp_path / "retry-workbook.pdf", ["L1", "L2"]),
        display_name="合成教辅",
    )
    record, _created = base_service.attach_semester_material(
        semester.id,
        request_token="mapping-retry-attach",
        material_version_id=version.id,
        material_role="exercise_workbook",
    )
    base_service.parse_material_version(version.id)
    fake = _FakeSemesterMappingModel(
        {
            "tree": [],
            "mappings": [
                {
                    "material_record_id": record.id,
                    "lesson_ref": lesson_ids[0],
                    "start_unit": 1,
                    "end_unit": 1,
                }
            ],
            "uncertainties": [],
        },
        failure=RuntimeError("synthetic model failure"),
    )
    service = TeachingPrepService(
        paths.workspace_dir("teaching-prep"),
        semester_mapping_model_adapter=fake,
    )

    with pytest.raises(TeachingPrepRetryAvailableError):
        service.generate_semester_mapping_proposal(
            semester.id,
            operation_id="semester-mapping-retry-old-0001",
            material_record_ids=[record.id],
        )
    with pytest.raises(TeachingPrepRetryAvailableError):
        service.generate_semester_mapping_proposal(
            semester.id,
            operation_id="semester-mapping-retry-old-0001",
            material_record_ids=[record.id],
        )

    fake.failure = None
    proposal, created = service.generate_semester_mapping_proposal(
        semester.id,
        operation_id="semester-mapping-retry-new-0002",
        material_record_ids=[record.id],
    )

    assert created is True
    assert proposal.operation_id == "semester-mapping-retry-new-0002"
    assert [item["operation_id"] for item in fake.calls] == [
        "semester-mapping-retry-old-0001",
        "semester-mapping-retry-new-0002",
    ]


def test_mapping_semantic_identity_blocks_a_parallel_fresh_operation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, base_service = _migrated_service(tmp_path, monkeypatch)
    semester, lesson_ids = _semester(base_service)
    version, _created = base_service.register_material_file(
        request_token="mapping-parallel-file",
        path=_pdf(tmp_path / "parallel-workbook.pdf", ["L1", "L2"]),
        display_name="合成教辅",
    )
    record, _created = base_service.attach_semester_material(
        semester.id,
        request_token="mapping-parallel-attach",
        material_version_id=version.id,
        material_role="exercise_workbook",
    )
    base_service.parse_material_version(version.id)
    started = Event()
    release = Event()

    class _BlockingModel(_FakeSemesterMappingModel):
        def generate(
            self,
            *,
            operation_id: str,
            semester_snapshot: dict[str, object],
        ) -> dict[str, object]:
            self.calls.append(
                {
                    "operation_id": operation_id,
                    "snapshot": semester_snapshot,
                }
            )
            started.set()
            assert release.wait(timeout=5)
            return self.payload

    fake = _BlockingModel(
        {
            "tree": [],
            "mappings": [
                {
                    "material_record_id": record.id,
                    "lesson_ref": lesson_ids[0],
                    "start_unit": 1,
                    "end_unit": 1,
                }
            ],
            "uncertainties": [],
        }
    )
    service = TeachingPrepService(
        paths.workspace_dir("teaching-prep"),
        semester_mapping_model_adapter=fake,
    )
    outcome: list[object] = []
    errors: list[BaseException] = []

    def generate_first() -> None:
        try:
            outcome.append(
                service.generate_semester_mapping_proposal(
                    semester.id,
                    operation_id="semester-mapping-parallel-first-0001",
                    material_record_ids=[record.id],
                )
            )
        except BaseException as exc:  # pragma: no cover - assertion below
            errors.append(exc)

    worker = Thread(target=generate_first)
    worker.start()
    assert started.wait(timeout=5)
    try:
        with pytest.raises(TeachingPrepStateError, match="still running"):
            service.generate_semester_mapping_proposal(
                semester.id,
                operation_id="semester-mapping-parallel-fresh-0002",
                material_record_ids=[record.id],
            )
    finally:
        release.set()
        worker.join(timeout=5)

    assert worker.is_alive() is False
    assert errors == []
    assert len(outcome) == 1
    assert len(fake.calls) == 1


def test_initial_mapping_proposal_creates_three_level_tree_atomically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, base_service = _migrated_service(tmp_path, monkeypatch)
    curriculum, _created = base_service.create_curriculum(
        request_token="mapping-empty-curriculum",
        title="合成八年级上册",
        grade_level=8,
        volume="first",
    )
    semester, _created = base_service.create_semester(
        request_token="mapping-empty-semester",
        curriculum_id=curriculum.id,
        school_year="2026-2027",
        term="first",
        planned_new_lesson_count=40,
    )
    version, _created = base_service.register_material_file(
        request_token="mapping-empty-file",
        path=_pdf(tmp_path / "empty-textbook.pdf", ["目录", "勾股定理"]),
        display_name="合成教材",
    )
    record, _created = base_service.attach_semester_material(
        semester.id,
        request_token="mapping-empty-attach",
        material_version_id=version.id,
        material_role="textbook",
    )
    base_service.parse_material_version(version.id)
    fake = _FakeSemesterMappingModel(
        {
            "tree": [
                {
                    "key": "chapter-1",
                    "title": "第一章",
                    "sections": [
                        {
                            "key": "section-1",
                            "title": "勾股定理",
                            "lessons": [
                                {
                                    "key": "lesson-1",
                                    "title": "认识勾股定理",
                                    "duration_minutes": 45,
                                }
                            ],
                        }
                    ],
                }
            ],
            "mappings": [
                {
                    "material_record_id": record.id,
                    "lesson_ref": "proposal:lesson-1",
                    "start_unit": 2,
                    "end_unit": 2,
                }
            ],
            "uncertainties": [],
        }
    )
    service = TeachingPrepService(
        paths.workspace_dir("teaching-prep"),
        semester_mapping_model_adapter=fake,
    )
    proposal, _created = service.generate_semester_mapping_proposal(
        semester.id,
        operation_id="semester-mapping-empty-0001",
        material_record_ids=[record.id],
    )

    reviewed = _accept_all_mappings(service, proposal)
    service.apply_semester_mapping_proposal(
        reviewed.id,
        expected_revision=reviewed.revision,
    )

    nodes = service.list_lesson_nodes(curriculum.id)
    assert [item.node_type for item in nodes] == [
        "chapter",
        "section",
        "lesson",
    ]
    assert [item.source_kind for item in nodes] == [
        "assistant_draft",
        "assistant_draft",
        "assistant_draft",
    ]
    assert service.list_material_links(nodes[-1].id)[0].start_unit == 2


def test_initial_tree_rejects_answer_book_before_model_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, base_service = _migrated_service(tmp_path, monkeypatch)
    curriculum, _created = base_service.create_curriculum(
        request_token="mapping-answer-curriculum",
        title="合成八年级上册",
        grade_level=8,
        volume="first",
    )
    semester, _created = base_service.create_semester(
        request_token="mapping-answer-semester",
        curriculum_id=curriculum.id,
        school_year="2026-2027",
        term="first",
        planned_new_lesson_count=40,
    )
    version, _created = base_service.register_material_file(
        request_token="mapping-answer-file",
        path=_pdf(tmp_path / "answer-book.pdf", ["答案", "解析"]),
        display_name="合成答案册",
    )
    record, _created = base_service.attach_semester_material(
        semester.id,
        request_token="mapping-answer-attach",
        material_version_id=version.id,
        material_role="answer_book",
    )
    base_service.parse_material_version(version.id)
    fake = _FakeSemesterMappingModel(
        {"tree": [], "mappings": [], "uncertainties": []}
    )
    service = TeachingPrepService(
        paths.workspace_dir("teaching-prep"),
        semester_mapping_model_adapter=fake,
        semester_mapping_model_label="fake-semester-model",
    )

    with pytest.raises(
        TeachingPrepValidationError,
        match="initial lesson tree",
    ):
        service.semester_mapping_preflight(
            semester.id,
            material_record_ids=[record.id],
        )
    with pytest.raises(
        TeachingPrepValidationError,
        match="initial lesson tree",
    ):
        service.generate_semester_mapping_proposal(
            semester.id,
            operation_id="semester-mapping-answer-0001",
            material_record_ids=[record.id],
        )

    assert fake.calls == []


def test_failed_new_version_parse_is_visible_and_keeps_confirmed_mapping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    semester, _lesson_ids = _semester(service)
    first, _created = service.register_material_file(
        request_token="parse-failure-file-v1",
        path=_pdf(tmp_path / "parse-failure-v1.pdf", ["教材第一页"]),
        display_name="合成教材",
    )
    attached, _created = service.attach_semester_material(
        semester.id,
        request_token="parse-failure-attach",
        material_version_id=first.id,
        material_role="textbook",
    )
    service.parse_material_version(first.id)
    parsed = service.list_semester_materials(semester.id)[0]
    confirmed = service.update_semester_material(
        attached.id,
        expected_revision=parsed.revision,
        material_role="textbook",
        mapping_status="confirmed",
        is_active=True,
    )
    invalid_path = tmp_path / "parse-failure-v2.pdf"
    invalid_path.write_bytes(b"not a valid PDF")
    second, _created = service.register_material_file(
        request_token="parse-failure-file-v2",
        path=invalid_path,
        source_id=first.source_id,
        display_name="合成教材",
    )

    with pytest.raises(
        TeachingPrepValidationError,
        match="PDF could not be opened",
    ):
        service.parse_material_version(second.id)

    failed = service.list_semester_materials(semester.id)[0]
    assert failed.parse_status == "failed"
    assert failed.last_parsed_version_id == first.id
    assert failed.mapping_status == confirmed.mapping_status == "confirmed"
    assert failed.has_unparsed_update is True
