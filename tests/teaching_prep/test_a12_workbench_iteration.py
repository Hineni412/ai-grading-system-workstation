from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from backend.teaching_prep.application.preferences import (
    DEFAULT_TEACHING_PREFERENCES,
)
from backend.teaching_prep.domain.errors import TeachingPrepConflictError
from backend.teaching_prep.infrastructure.fakes import (
    FakeExerciseSuggestionModelAdapter,
    FakeWpsAdapter,
)

from .test_a01_foundation import _migrated_service
from .test_a02_catalog import _api_client
from .test_a05_resource_packs import (
    _evidence_fakes,
    _freeze_ready_setup,
)
from .test_a08_pptx_execution import _approved_plan, _sha256
from .test_a11_semester_workspace import _semester


def _selection(preflight, link):
    return {
        "material_selections": [
            {
                "link_id": link["link_id"],
                "start_unit": link["start_unit"],
                "end_unit": link["end_unit"],
                **(
                    {"ppt_intent": "keep"}
                    if link["purpose"] == "reference_ppt"
                    else {}
                ),
            }
        ],
        "exercise_candidate_ids": [],
        "question_ids": [],
        "assessment_ids": [],
        "knowledge_scope": [],
        "preparation_preferences": deepcopy(DEFAULT_TEACHING_PREFERENCES),
        "class_name": None,
        "teacher_context": "只允许合成资料范围",
    }


def _suggestion(material, *, material_version_id: str | None = None):
    unit = material["units"][0]
    return {
        "material_version_id": material_version_id
        or material["material_version_id"],
        "question_number": "1",
        "content_label": "合成候选题",
        "difficulty": "medium",
        "classroom_use": "guided_practice",
        "estimated_minutes": 4,
        "teaching_focus": "检查方程变形",
        "reason": "与本节目标直接相关",
        "uncertainties": ["答案区域需教师核对"],
        "question_regions": [
            {
                "material_unit_id": unit["unit_id"],
                "sequence": 1,
                "crop": {"x0": 0.1, "y0": 0.1, "x1": 0.9, "y1": 0.55},
            }
        ],
        "answer_regions": [
            {
                "material_unit_id": unit["unit_id"],
                "sequence": 1,
                "crop": {"x0": 0.1, "y0": 0.58, "x1": 0.9, "y1": 0.9},
            }
        ],
    }


def test_lesson_statuses_are_projected_in_one_semester_query(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service = _migrated_service(tmp_path, monkeypatch)
    semester, lesson_ids = _semester(service)

    statuses = service.list_lesson_preparation_statuses(semester.id)

    assert [item["lesson_node_id"] for item in statuses] == lesson_ids
    assert all(item["manual_progress"] == "not_started" for item in statuses)
    assert all(item["preparation_stage"] == "select" for item in statuses)
    assert all("latest" in item and "blockers" in item for item in statuses)


def test_reference_snapshot_limits_model_input_and_review_never_verifies_answer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service = _migrated_service(tmp_path, monkeypatch)
    lesson_id, _reference_link, _candidate = _freeze_ready_setup(service, tmp_path)
    preflight = service.reference_selection_preflight(lesson_id)
    selected = preflight["catalog"]["material_links"][0]
    outside = preflight["catalog"]["material_links"][1]
    draft = service.save_reference_selection_draft(
        lesson_id,
        expected_revision=None,
        source_state_sha256=preflight["source_state_sha256"],
        selection=_selection(preflight, selected),
    )
    snapshot, created = service.freeze_reference_selection_snapshot(
        lesson_id,
        request_token="a12-reference-snapshot-0001",
        expected_draft_revision=draft.revision,
    )
    assert created is True
    assert [
        item["material_version_id"]
        for item in snapshot.payload["model_input"]["materials"]
    ] == [selected["material_version_id"]]

    adapter = FakeExerciseSuggestionModelAdapter(
        {"suggestions": [_suggestion(selected)]}
    )
    service.exercise_suggestion_model_adapter = adapter
    run, run_created = service.start_exercise_suggestion_run(
        snapshot.id,
        operation_id="a12-suggestion-operation-0001",
        confirmed=True,
    )
    repeated, repeated_created = service.start_exercise_suggestion_run(
        snapshot.id,
        operation_id="a12-suggestion-operation-0001",
        confirmed=True,
    )
    assert run_created is True
    assert repeated_created is False
    assert repeated.id == run.id

    service.process_exercise_suggestion_run(run.id)
    finished, suggestions = service.get_exercise_suggestion_run(run.id)
    assert finished.status == "succeeded"
    assert finished.model_call_count == 1
    assert len(adapter.calls) == 1
    accepted = service.review_exercise_suggestion(
        suggestions[0].id,
        expected_revision=suggestions[0].revision,
        decision="accepted",
        teacher_payload=None,
        rejection_reason=None,
    )
    candidate = next(
        item
        for item in service.list_exercise_candidates(lesson_id)
        if item.id == accepted.exercise_candidate_id
    )
    assert candidate.answer_status == "candidate"
    assert candidate.answer_status != "teacher_verified"

    second_snapshot, _created = service.freeze_reference_selection_snapshot(
        lesson_id,
        request_token="a12-reference-snapshot-0002",
        expected_draft_revision=draft.revision,
    )
    service.exercise_suggestion_model_adapter = FakeExerciseSuggestionModelAdapter(
        {
            "suggestions": [
                _suggestion(
                    selected,
                    material_version_id=outside["material_version_id"],
                )
            ]
        }
    )
    invalid, _created = service.start_exercise_suggestion_run(
        second_snapshot.id,
        operation_id="a12-suggestion-operation-outside",
        confirmed=True,
    )
    service.process_exercise_suggestion_run(invalid.id)
    invalid_result, invalid_items = service.get_exercise_suggestion_run(invalid.id)
    assert invalid_result.status == "failed"
    assert invalid_items == ()


def test_resource_subset_and_capacity_preview_have_no_hidden_side_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service = _migrated_service(tmp_path, monkeypatch)
    question_reader, assessment_reader = _evidence_fakes()
    service.question_evidence_reader = question_reader
    service.assessment_evidence_reader = assessment_reader
    lesson_id, reference_link, candidate = _freeze_ready_setup(service, tmp_path)
    all_links = service.list_material_links(lesson_id)

    preflight = service.resource_pack_preflight(
        lesson_id,
        reference_ppt_intents={reference_link.id: "keep"},
        selected_material_link_ids=[item.id for item in all_links],
        selected_exercise_candidate_ids=[candidate.id],
    )
    assert preflight["ready_to_freeze"] is True

    pack, _created = service.freeze_resource_pack(
        request_token="a12-resource-subset-0001",
        lesson_node_id=lesson_id,
        class_name=None,
        lesson_type="new_lesson",
        teacher_context=None,
        reference_ppt_intents={reference_link.id: "keep"},
        question_ids=[],
        assessment_ids=[],
        knowledge_scope=[],
        selected_material_link_ids=[item.id for item in all_links],
        selected_exercise_candidate_ids=[candidate.id],
    )
    assert pack.payload["selection"]["material_link_ids"] == [
        item.id for item in all_links
    ]
    assert pack.payload["selection"]["exercise_candidate_ids"] == [candidate.id]

    local, _created = service.generate_lesson_draft(
        pack.id,
        operation_id="a12-local-draft-operation",
        mode="local_template",
        confirmed=True,
    )
    before = service.list_lesson_drafts(pack.id)
    capacity = service.preview_lesson_draft_capacity(local.id, payload=local.payload)
    after = service.list_lesson_drafts(pack.id)
    assert capacity["lesson_minutes"] > 0
    assert [item.id for item in after] == [item.id for item in before]


def test_async_pptx_start_publishes_trusted_current_version_without_touching_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service = _migrated_service(tmp_path, monkeypatch)
    plan = _approved_plan(service, tmp_path)
    source = tmp_path / "a05-reference.pptx"
    source_before = _sha256(source)
    service.wps_adapter = FakeWpsAdapter()

    started, created = service.start_pptx_execution(
        plan.id,
        operation_id="a12-async-pptx-operation",
        confirmed=True,
    )
    assert created is True
    assert started.status == "running"
    assert started.phase == "copying"

    service.process_pptx_execution(started.id)
    finished = service.get_pptx_execution(started.id)
    versions = service.list_lesson_pptx_versions(
        service.get_resource_pack(plan.resource_pack_id).lesson_node_id
    )
    assert finished.status == "published"
    assert finished.phase == "done"
    assert len(versions) == 1
    assert versions[0]["is_current"] is True
    assert versions[0]["file_verified"] is True
    assert service.pptx_version_preview_path(
        versions[0]["version"].id
    ).is_file()
    assert _sha256(source) == source_before

    version = versions[0]["version"]
    same, revision, changed = service.activate_pptx_version(
        version.id,
        expected_revision=versions[0]["current_revision"],
    )
    assert same.id == version.id
    assert changed is False
    assert revision == versions[0]["current_revision"]
    with pytest.raises(TeachingPrepConflictError):
        service.activate_pptx_version(version.id, expected_revision=revision + 1)


def test_workbench_http_contract_runs_suggestions_as_an_observable_operation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service = _migrated_service(tmp_path, monkeypatch)
    lesson_id, _reference_link, _candidate = _freeze_ready_setup(service, tmp_path)
    preflight = service.reference_selection_preflight(lesson_id)
    selected = preflight["catalog"]["material_links"][0]
    service.exercise_suggestion_model_adapter = FakeExerciseSuggestionModelAdapter(
        {"suggestions": [_suggestion(selected)]}
    )
    client = _api_client(service)

    response = client.put(
        f"/api/teaching-prep/lessons/{lesson_id}/reference-selection-draft",
        json={
            "expected_revision": None,
            "source_state_sha256": preflight["source_state_sha256"],
            "selection": _selection(preflight, selected),
        },
    )
    assert response.status_code == 200
    draft = response.json()
    response = client.post(
        f"/api/teaching-prep/lessons/{lesson_id}/reference-selection-snapshots",
        json={
            "request_token": "a12-http-snapshot-0001",
            "expected_draft_revision": draft["revision"],
        },
    )
    assert response.status_code == 201
    snapshot = response.json()
    response = client.post(
        f"/api/teaching-prep/reference-selection-snapshots/{snapshot['id']}/exercise-suggestion-runs",
        json={
            "operation_id": "a12-http-suggestion-operation",
            "confirmed": True,
        },
    )
    assert response.status_code == 202
    run_id = response.json()["id"]
    finished = client.get(
        f"/api/teaching-prep/exercise-suggestion-runs/{run_id}"
    )
    assert finished.status_code == 200
    assert finished.json()["status"] == "succeeded"
    assert len(finished.json()["suggestions"]) == 1
