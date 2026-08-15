from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import os
import zipfile

import pytest

from backend.teaching_prep.application.preferences import (
    DEFAULT_TEACHING_PREFERENCES,
)
from backend.teaching_prep.application.workbench_iteration import (
    normalize_exercise_suggestion_payload,
)
from backend.teaching_prep.domain.errors import TeachingPrepConflictError
from backend.teaching_prep.infrastructure.fakes import (
    FakeExerciseSuggestionModelAdapter,
    FakeWpsAdapter,
)
from backend.teaching_prep.infrastructure.llm.exercise_suggestions import (
    WorkspaceExerciseSuggestionModelAdapter,
)
from backend.teaching_prep.infrastructure.llm.lesson_model import (
    WorkspaceLessonModelAdapter,
)

from .test_a01_foundation import _migrated_service
from .test_a02_catalog import _api_client
from .test_a05_resource_packs import (
    _evidence_fakes,
    _freeze,
    _freeze_ready_setup,
)
from .test_a03_material_units import _pptx
from .test_a08_pptx_execution import _approved_plan, _sha256
from .test_a11_semester_workspace import _pdf, _semester


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


def test_lesson_material_readiness_requires_each_parsed_reference_range(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service = _migrated_service(tmp_path, monkeypatch)
    semester, lesson_ids = _semester(service)
    lesson_id = lesson_ids[0]

    reference, _created = service.register_material_file(
        request_token="a12-readiness-reference",
        path=_pptx(tmp_path / "a12-readiness-reference.pptx"),
        display_name="合成主课件",
    )
    service.parse_material_version(reference.id)
    service.create_material_link(
        request_token="a12-readiness-reference-link",
        lesson_node_id=lesson_id,
        material_version_id=reference.id,
        start_unit=1,
        end_unit=2,
        crop=None,
        purpose="reference_ppt",
        teacher_note=None,
        confirmation_status="confirmed",
    )

    support_versions = []
    for suffix, role in (
        ("textbook", "textbook"),
        ("workbook-a", "exercise_workbook"),
        ("workbook-b", "exercise_workbook"),
    ):
        version, _created = service.register_material_file(
            request_token=f"a12-readiness-{suffix}",
            path=_pdf(tmp_path / f"a12-readiness-{suffix}.pdf", [f"{suffix}-1"]),
            display_name=f"合成{suffix}",
        )
        service.attach_semester_material(
            semester.id,
            request_token=f"a12-readiness-attach-{suffix}",
            material_version_id=version.id,
            material_role=role,
        )
        service.parse_material_version(version.id)
        support_versions.append(version)

    missing = next(
        item for item in service.list_lesson_preparation_statuses(semester.id)
        if item["lesson_node_id"] == lesson_id
    )
    assert missing["cells"]["materials"] == {
        "status": "needs_teacher",
        "summary": (
            "待补教材（合成textbook）、"
            "参考教辅（合成workbook-a）、"
            "参考教辅（合成workbook-b）页段"
        ),
        "target_panel": "sources",
    }
    for index, (version, purpose) in enumerate(
        zip(support_versions, ("textbook", "exercise", "exercise"), strict=True),
        start=1,
    ):
        service.create_material_link(
            request_token=f"a12-readiness-support-link-{index}",
            lesson_node_id=lesson_id,
            material_version_id=version.id,
            start_unit=1,
            end_unit=1,
            crop=None,
            purpose=purpose,
            teacher_note=None,
            confirmation_status="confirmed",
        )

    ready = next(
        item for item in service.list_lesson_preparation_statuses(semester.id)
        if item["lesson_node_id"] == lesson_id
    )
    assert ready["cells"]["materials"]["status"] == "ready"
    assert ready["cells"]["materials"]["summary"] == "4 份已确认"


def test_exercise_suggestion_normalizes_known_chinese_enum_aliases() -> None:
    material = {
        "purpose": "exercise",
        "material_version_id": "a" * 32,
        "units": [{"unit_id": "b" * 32, "unit_index": 6}],
    }
    raw = _suggestion(material)
    raw["difficulty"] = "中等"
    raw["classroom_use"] = "课堂检测"
    raw["uncertainties"] = ""
    raw["question_regions"][0]["crop"] = [0.1, 0.1, 0.9, 0.55]
    raw["answer_regions"][0]["crop"] = {
        "x0": 0.0,
        "y0": 0.0,
        "x1": 0.0,
        "y1": 0.0,
    }

    normalized = normalize_exercise_suggestion_payload(
        {"suggestions": [raw]}, snapshot={"materials": [material]}
    )

    assert normalized[0]["difficulty"] == "medium"
    assert normalized[0]["classroom_use"] == "diagnostic"
    assert normalized[0]["uncertainties"] == []
    assert normalized[0]["answer_regions"] == []
    assert normalized[0]["question_regions"][0]["crop"] == {
        "x0": 0.1,
        "y0": 0.1,
        "x1": 0.9,
        "y1": 0.55,
    }


def test_exercise_suggestion_accepts_textbook_source_pages() -> None:
    material = {
        "purpose": "textbook",
        "material_version_id": "a" * 32,
        "units": [{"unit_id": "b" * 32, "unit_index": 9}],
    }
    raw = _suggestion(material)
    normalized = normalize_exercise_suggestion_payload(
        {"suggestions": [raw]}, snapshot={"materials": [material]}
    )
    assert normalized[0]["material_version_id"] == "a" * 32


def test_exercise_model_receives_selected_workbook_page_images() -> None:
    class Gateway:
        def __init__(self) -> None:
            self.kwargs: dict[str, object] = {}

        def chat_completions(self, **kwargs: object) -> dict[str, object]:
            self.kwargs = kwargs
            return {"choices": [{"message": {"content": '{"suggestions":[]}'}}]}

    gateway = Gateway()
    adapter = WorkspaceExerciseSuggestionModelAdapter(
        gateway=gateway,  # type: ignore[arg-type]
        client=object(),
        model="test-model",
    )

    assert adapter.generate(
        operation_id="a12-image-request",
        reference_snapshot={
            "materials": [{"purpose": "exercise", "material_version_id": "v1"}],
            "reference_images": [
                {
                    "purpose": "exercise",
                    "material_version_id": "v1",
                    "material_unit_id": "u1",
                    "unit_index": 4,
                    "mime_type": "image/png",
                    "content": b"\x89PNG",
                }
            ],
        },
    ) == {"suggestions": []}
    user_content = gateway.kwargs["kwargs"]["messages"][1]["content"]  # type: ignore[index]
    assert isinstance(user_content, list)
    assert "reference_images" not in user_content[0]["text"]
    assert user_content[1]["text"].startswith("教辅原页；")
    assert user_content[1]["text"].endswith("material_unit_id=u1;unit_index=4")
    assert user_content[2]["image_url"]["url"].startswith(
        "data:image/png;base64,"
    )


def test_lesson_model_receives_selected_page_images() -> None:
    class Gateway:
        def __init__(self) -> None:
            self.kwargs: dict[str, object] = {}

        def chat_completions(self, **kwargs: object) -> dict[str, object]:
            self.kwargs = kwargs
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"knowledge_objectives":[],"focus_points":[],'
                                '"anticipated_difficulties":[],"lesson_flow":[],'
                                '"exercise_recommendations":[],'
                                '"slide_adaptations":[],"uncertainties":[]}'
                            )
                        }
                    }
                ]
            }

    gateway = Gateway()
    adapter = WorkspaceLessonModelAdapter(
        gateway=gateway,  # type: ignore[arg-type]
        client=object(),
        model="test-model",
    )
    payload = adapter.generate(
        operation_id="a12-lesson-image-request",
        resource_pack={
            "materials": [],
            "reference_images": [
                {
                    "purpose": "reference_ppt",
                    "material_version_id": "v1",
                    "material_unit_id": "u1",
                    "unit_index": 1,
                    "mime_type": "image/png",
                    "content": b"\x89PNG",
                }
            ],
        },
    )
    assert payload["slide_adaptations"] == []
    user_content = gateway.kwargs["kwargs"]["messages"][1]["content"]  # type: ignore[index]
    assert isinstance(user_content, list)
    assert "reference_images" not in user_content[0]["text"]
    assert user_content[1]["text"].startswith("主课件原页；")
    assert user_content[2]["image_url"]["url"].startswith(
        "data:image/png;base64,"
    )
    assert gateway.kwargs["timeout_override_seconds"] == 180


def test_select_model_page_images_round_robins_and_caps_total() -> None:
    from backend.teaching_prep.application.preparation_service import (
        _select_model_page_images,
    )

    buckets = {
        "exercise": [
            {"purpose": "exercise", "material_unit_id": f"e{index}", "content": b"e"}
            for index in range(3)
        ],
        "textbook": [
            {"purpose": "textbook", "material_unit_id": f"t{index}", "content": b"t"}
            for index in range(8)
        ],
        "reference_ppt": [
            {
                "purpose": "reference_ppt",
                "material_unit_id": f"p{index}",
                "content": b"p",
            }
            for index in range(9)
        ],
    }
    selected = _select_model_page_images(
        buckets,
        purposes=("exercise", "textbook", "reference_ppt"),
        max_images=12,
        max_bytes=12_000_000,
    )
    purposes = [str(item["purpose"]) for item in selected]
    assert len(selected) == 12
    assert purposes.count("exercise") == 3
    assert purposes.count("textbook") == 5
    assert purposes.count("reference_ppt") == 4
    assert purposes[:3] == ["exercise", "textbook", "reference_ppt"]


def test_preview_payload_for_model_compresses_large_png(tmp_path: Path) -> None:
    from backend.teaching_prep.application.preparation_service import (
        _preview_payload_for_model,
    )
    from PIL import Image

    preview = tmp_path / "large-slide.png"
    Image.frombytes("RGB", (1600, 900), os.urandom(1600 * 900 * 3)).save(preview)
    mime_type, content = _preview_payload_for_model(preview)
    assert mime_type == "image/jpeg"
    assert content.startswith(b"\xff\xd8")
    assert len(content) < preview.stat().st_size


def _add_exercise_link(service, lesson_id: str, *, token: str):
    preflight = service.reference_selection_preflight(lesson_id)
    source = next(
        item
        for item in preflight["catalog"]["material_links"]
        if item["purpose"] == "textbook"
    )
    service.create_material_link(
        request_token=token,
        lesson_node_id=lesson_id,
        material_version_id=source["material_version_id"],
        start_unit=source["start_unit"],
        end_unit=source["end_unit"],
        crop=None,
        purpose="exercise",
        teacher_note="合成普通教辅范围",
        confirmation_status="confirmed",
    )


def test_latest_material_source_version_marks_dependent_home_cells_stale(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service = _migrated_service(tmp_path, monkeypatch)
    question_reader, assessment_reader = _evidence_fakes()
    service.question_evidence_reader = question_reader
    service.assessment_evidence_reader = assessment_reader
    lesson_id, reference_link, _candidate = _freeze_ready_setup(service, tmp_path)
    curriculum = service.list_curricula()[0]
    semester, _created = service.create_semester(
        request_token="a12-stale-semester",
        curriculum_id=curriculum.id,
        school_year="2026-2027",
        term="first",
        planned_new_lesson_count=48,
    )
    pack, _created = _freeze(
        service,
        token="a12-stale-pack",
        lesson_id=lesson_id,
        reference_link_id=reference_link.id,
    )
    service.generate_lesson_draft(
        pack.id,
        operation_id="a12-stale-local-draft",
        mode="local_template",
        confirmed=True,
    )
    before = next(
        item for item in service.list_lesson_preparation_statuses(semester.id)
        if item["lesson_node_id"] == lesson_id
    )
    old_version = service.get_material_version(reference_link.material_version_id)
    replacement_path = _pptx(tmp_path / "a12-reference-v2.pptx")
    with zipfile.ZipFile(replacement_path, "a") as archive:
        archive.writestr("docProps/a12-version.txt", "synthetic-v2")
    replacement, created = service.register_material_file(
        request_token="a12-reference-v2",
        path=replacement_path,
        display_name=old_version.display_name,
        source_id=old_version.source_id,
    )

    assert created is True
    assert replacement.id != old_version.id
    assert service.resource_pack_status(lesson_id)["local_sources_changed"] is True
    after = next(
        item for item in service.list_lesson_preparation_statuses(semester.id)
        if item["lesson_node_id"] == lesson_id
    )
    assert after["cells"]["materials"]["status"] == "stale"
    assert after["cells"]["plan"]["status"] == "stale"
    assert after["cells"]["exercises"]["status"] == "stale"
    assert after["next_action"] == "重新核对资料"
    assert after["summary_revision"] != before["summary_revision"]


def test_lesson_statuses_include_only_actionable_public_ai_tasks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service = _migrated_service(tmp_path, monkeypatch)
    semester, lesson_ids = _semester(service)
    lesson_id = lesson_ids[0]

    def task(task_id: str, status: str, adoption_state: str | None = None):
        handoffs = () if adoption_state is None else (
            SimpleNamespace(
                adoption_state=adoption_state,
                subject_refs=(SimpleNamespace(kind="lesson", id=lesson_id),),
            ),
        )
        return SimpleNamespace(
            task_id=task_id,
            module="teaching_prep",
            task_kind="teaching_prep.lesson_plan",
            status=status,
            source_ref=SimpleNamespace(kind="lesson", id=lesson_id),
            proposal_ref_id=(None if status in {"queued", "running"} else f"proposal-{task_id}"),
            proposal_revision=(None if status in {"queued", "running"} else "1"),
            pending_count=(1 if adoption_state in {"pending", "opened", "adoption_started"} else 0),
            handoffs=handoffs,
        )

    statuses = service.list_lesson_preparation_statuses(
        semester.id,
        ai_tasks=(
            task("queued-task", "queued"),
            task("pending-proposal", "proposal_ready", "pending"),
            task("adopted-proposal", "proposal_ready", "adopted"),
            task("discarded-proposal", "proposal_ready", "discarded"),
        ),
    )
    lesson = next(item for item in statuses if item["lesson_node_id"] == lesson_id)

    assert [item["task_id"] for item in lesson["ai_tasks"]] == [
        "queued-task",
        "pending-proposal",
    ]
    assert lesson["ai_tasks"][0]["proposal_ref_id"] is None


def test_reference_snapshot_limits_model_input_and_review_never_verifies_answer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service = _migrated_service(tmp_path, monkeypatch)
    lesson_id, _reference_link, _candidate = _freeze_ready_setup(service, tmp_path)
    _add_exercise_link(service, lesson_id, token="a12-exercise-link")
    preflight = service.reference_selection_preflight(lesson_id)
    selected = next(
        item for item in preflight["catalog"]["material_links"]
        if item["purpose"] == "exercise"
    )
    outside = next(
        item for item in preflight["catalog"]["material_links"]
        if item["material_version_id"] != selected["material_version_id"]
    )
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
    reference_images = adapter.calls[0]["reference_snapshot"]["reference_images"]
    assert [item["material_unit_id"] for item in reference_images] == [
        unit["unit_id"] for unit in selected["units"]
    ]
    assert all(item["content"].startswith(b"\x89PNG") for item in reference_images)
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
    assert finished.status == "published", (
        finished.error_code,
        finished.execution_report,
        finished.verification_report,
    )
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
    _add_exercise_link(service, lesson_id, token="a12-http-exercise-link")
    preflight = service.reference_selection_preflight(lesson_id)
    selected = next(
        item for item in preflight["catalog"]["material_links"]
        if item["purpose"] == "exercise"
    )
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

    missing = client.get(
        f"/api/teaching-prep/lessons/{'0' * 32}/latest-exercise-suggestion-run"
    )
    assert missing.status_code == 404
    latest = client.get(
        f"/api/teaching-prep/lessons/{lesson_id}/latest-exercise-suggestion-run"
    )
    assert latest.status_code == 200
    assert latest.json()["id"] == run_id
    assert latest.json()["status"] == "succeeded"
    assert len(latest.json()["suggestions"]) == 1
