from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import re

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from backend.api.app import ApiError
from backend.teaching_prep.api import create_router
from backend.teaching_prep.application.lesson_drafts import build_local_template
from backend.teaching_prep.application.lesson_drafts import (
    _classify_slide,
    _is_homework_workbook,
    _local_slide_adaptations,
    draft_preflight,
    normalize_model_draft_payload,
)
from backend.teaching_prep.application.preferences import (
    DEFAULT_TEACHING_PREFERENCES,
    normalize_teaching_preferences,
)
from backend.teaching_prep.application.pptx_execution import (
    build_executor_request,
)
from backend.teaching_prep.application.preparation_service import (
    TeachingPrepService,
)
from backend.teaching_prep.application.slide_plans import (
    build_slide_plan_payload,
    validate_operation,
    validate_plan_payload,
)
from backend.teaching_prep.api.schemas import LessonDraftPreflightResponse
from backend.teaching_prep.domain.errors import (
    TeachingPrepConflictError,
    TeachingPrepValidationError,
)
from backend.teaching_prep.infrastructure.fakes import FakeLessonModelAdapter
from backend.teaching_prep.infrastructure.llm.lesson_model import (
    WorkspaceLessonModelAdapter,
    _model_output_contract,
)

from .test_a01_foundation import _migrated_service
from .test_a05_resource_packs import (
    _evidence_fakes,
    _freeze_ready_setup,
)


def _ready_service_and_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    paths, service = _migrated_service(tmp_path, monkeypatch)
    question_reader, assessment_reader = _evidence_fakes()
    service.question_evidence_reader = question_reader
    service.assessment_evidence_reader = assessment_reader
    lesson_id, reference_link, _candidate = _freeze_ready_setup(
        service,
        tmp_path,
    )
    return paths, service, lesson_id, reference_link


def _preferences(**changes: object) -> dict[str, object]:
    value = deepcopy(DEFAULT_TEACHING_PREFERENCES)
    value.update(changes)
    return value


def test_lesson_model_repairs_wrapped_json_and_requests_enough_output() -> None:
    class Gateway:
        def __init__(self) -> None:
            self.kwargs: dict[str, object] = {}

        def chat_completions(self, **kwargs: object) -> dict[str, object]:
            self.kwargs = kwargs
            return {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": "```json\n{\"uncertainties\": []}\n```"
                        },
                    }
                ]
            }

    gateway = Gateway()
    adapter = WorkspaceLessonModelAdapter(
        gateway=gateway,  # type: ignore[arg-type]
        client=object(),
        model="test-model",
    )

    result = adapter.generate(
        operation_id="lesson-model-json-repair",
        resource_pack={
            "lesson": {"lesson_node_id": "lesson-1"},
            "materials": [
                {
                    "link_id": "ppt-link",
                    "purpose": "reference_ppt",
                    "units": [{"unit_index": 1}, {"unit_index": 2}],
                },
                {
                    "link_id": "book-link",
                    "purpose": "textbook",
                    "units": [{"unit_index": 10}],
                },
            ],
            "exercises": [],
        },
    )

    assert result == {"uncertainties": []}
    assert gateway.kwargs["kwargs"]["max_tokens"] == 16_000  # type: ignore[index]
    user_content = gateway.kwargs["kwargs"]["messages"][1]["content"]  # type: ignore[index]
    contract = json.loads(user_content)["output_contract"]
    assert contract["slide_adaptations"]["required_count"] == 2
    assert contract["slide_adaptations"]["allowed_slide_refs"] == [
        "material:ppt-link:unit:1",
        "material:ppt-link:unit:2",
    ]
    assert contract["slide_adaptations"]["allowed_textbook_refs"] == [
        "material:book-link:unit:10"
    ]
    assert contract["allowed_exercise_recommendation_refs"] == []
    assert contract["schemas"]["lesson_flow"]["required_phases"] == [
        "introduction",
        "exploration",
        "example",
        "practice",
        "summary",
    ]
    assert contract["schemas"]["knowledge_objectives"]["item"] == {
        "text": "string",
        "citations": "non-empty array of allowed citation IDs",
    }


def test_lesson_model_rejects_truncated_output() -> None:
    class Gateway:
        def chat_completions(self, **_kwargs: object) -> dict[str, object]:
            return {
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {"content": "{\"uncertainties\":"},
                    }
                ]
            }

    adapter = WorkspaceLessonModelAdapter(
        gateway=Gateway(),  # type: ignore[arg-type]
        client=object(),
        model="test-model",
    )

    with pytest.raises(TeachingPrepValidationError, match="truncated"):
        adapter.generate(
            operation_id="lesson-model-truncated",
            resource_pack={},
        )


def test_lesson_model_contract_supports_object_edits_and_existing_slide_inserts() -> None:
    contract = _model_output_contract(
        {
            "materials": [
                {
                    "link_id": "ppt-link",
                    "purpose": "reference_ppt",
                    "units": [
                        {
                            "unit_index": 1,
                            "title": "练习 1",
                            "object_summary": {
                                "objects": [
                                    {
                                        "object_ref": "shape:7",
                                        "safe_to_delete": True,
                                    }
                                ]
                            },
                        },
                        {
                            "unit_index": 2,
                            "title": "产品介绍",
                            "text": "扫码获取更多资源",
                            "object_summary": {
                                "objects": [
                                    {
                                        "object_ref": "shape:9",
                                        "safe_to_delete": True,
                                    }
                                ]
                            },
                        },
                        {
                            "unit_index": 3,
                            "title": "3",
                            "text": "3. 已知直角三角形两边，求第三边",
                            "object_summary": {
                                "objects": [
                                    {
                                        "object_ref": "shape:11",
                                        "safe_to_delete": True,
                                    }
                                ]
                            },
                        },
                    ],
                }
            ],
            "exercises": [
                {
                    "candidate_id": "exercise-one",
                    "selection_status": "selected",
                }
            ],
        }
    )

    slide_schema = contract["schemas"]["slide_adaptations"]["item"]
    exercise_schema = contract["schemas"]["exercise_recommendations"]["item"]
    slide_ref = "material:ppt-link:unit:1"

    assert slide_schema["delete_object_refs"] == (
        "array of allowed object refs for this slide"
    )
    assert contract["slide_adaptations"]["allowed_object_refs_by_slide"] == {
        slide_ref: [f"{slide_ref}:object:shape:7"],
        "material:ppt-link:unit:2": [],
        "material:ppt-link:unit:3": [
            "material:ppt-link:unit:3:object:shape:11"
        ],
    }
    assert exercise_schema["target_slide_ref"] == (
        "one allowed slide ref when action is include"
    )


def test_model_draft_drops_unaddressable_page_level_exercise_suggestions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service, lesson_id, reference_link = _ready_service_and_sources(
        tmp_path,
        monkeypatch,
    )
    pack, _created = _freeze_with_preferences(
        service,
        lesson_id=lesson_id,
        reference_link_id=reference_link.id,
        token="a10-page-level-suggestion-pack",
        preferences=_preferences(),
    )
    pack_payload = deepcopy(pack.payload)
    pack_payload["exercises"] = []
    pack_payload["evidence"]["question"]["items"] = []
    pack = replace(pack, payload=pack_payload)
    raw = build_local_template(pack)
    raw["exercise_recommendations"] = [
        {
            "source_ref": "material:workbook-link:unit:6",
            "action": "include",
            "title": "整页中的基础题",
            "reason": "模型未提供可精确引用的候选题 ID",
            "estimated_minutes": 5,
            "citations": ["material:workbook-link:unit:6"],
        }
    ]

    normalized = normalize_model_draft_payload(raw, pack)

    assert normalized["exercise_recommendations"] == []
    assert any(
        "尚未切分为可精确引用的候选题" in item
        for item in normalized["uncertainties"]
    )


def test_model_draft_defaults_missing_focus_kind_to_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service, lesson_id, reference_link = _ready_service_and_sources(
        tmp_path,
        monkeypatch,
    )
    pack, _created = _freeze_with_preferences(
        service,
        lesson_id=lesson_id,
        reference_link_id=reference_link.id,
        token="a10-missing-focus-kind-pack",
        preferences=_preferences(),
    )
    raw = build_local_template(pack)
    raw["focus_points"][0].pop("kind")

    normalized = normalize_model_draft_payload(raw, pack)

    assert normalized["focus_points"][0]["kind"] == "key"


def _freeze_with_preferences(
    service: TeachingPrepService,
    *,
    lesson_id: str,
    reference_link_id: str,
    token: str,
    preferences: dict[str, object],
):
    return service.freeze_resource_pack(
        request_token=token,
        lesson_node_id=lesson_id,
        class_name="合成七年级一班",
        lesson_type="new_lesson",
        teacher_context="仅包含匿名班级整体情况",
        reference_ppt_intents={reference_link_id: "keep"},
        question_ids=[101],
        assessment_ids=[7],
        knowledge_scope=["一元一次方程"],
        preparation_preferences=preferences,
    )


def test_personal_preferences_persist_and_reject_stale_overwrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, service = _migrated_service(tmp_path, monkeypatch)
    original = service.get_teaching_preferences()
    changed = _preferences(
        practice_trim_level="strong",
        supplement_question_limit=1,
    )

    updated = service.update_teaching_preferences(
        expected_revision=original.revision,
        payload=changed,
    )

    assert updated.revision == original.revision + 1
    assert updated.payload["practice_trim_level"] == "strong"
    restarted = TeachingPrepService(paths.workspace_dir("teaching-prep"))
    assert restarted.get_teaching_preferences().payload == changed
    with pytest.raises(TeachingPrepConflictError):
        service.update_teaching_preferences(
            expected_revision=original.revision,
            payload=_preferences(practice_trim_level="light"),
        )


def test_preferences_api_exposes_defaults_and_revision_conflicts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service = _migrated_service(tmp_path, monkeypatch)
    app = FastAPI()
    app.state.workspace_services = {"teaching-prep": service}
    app.include_router(create_router(), prefix="/api/teaching-prep")

    @app.exception_handler(ApiError)
    async def handle_api_error(_request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    client = TestClient(app)

    current = client.get("/api/teaching-prep/preferences")
    saved = client.patch(
        "/api/teaching-prep/preferences",
        json={
            "expected_revision": current.json()["revision"],
            "payload": _preferences(
                practice_trim_level="strong",
                supplement_question_limit=1,
            ),
        },
    )
    stale = client.patch(
        "/api/teaching-prep/preferences",
        json={
            "expected_revision": current.json()["revision"],
            "payload": _preferences(practice_trim_level="light"),
        },
    )

    assert current.status_code == 200
    assert current.json()["payload"]["page_label_font_size"] == 28
    assert saved.status_code == 200
    assert saved.json()["revision"] == current.json()["revision"] + 1
    assert stale.status_code == 409


def test_preference_validation_keeps_hard_rules_local() -> None:
    with pytest.raises(TeachingPrepValidationError):
        normalize_teaching_preferences(
            _preferences(page_label_font_size=24)
        )
    with pytest.raises(TeachingPrepValidationError):
        normalize_teaching_preferences(
            _preferences(
                supplement_from_references=False,
                supplement_question_limit=2,
            )
        )


def test_resource_pack_freezes_preferences_for_preflight_and_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service, lesson_id, reference_link = _ready_service_and_sources(
        tmp_path,
        monkeypatch,
    )
    frozen_preferences = _preferences(
        practice_trim_level="strong",
        supplement_question_limit=1,
    )
    pack, _created = _freeze_with_preferences(
        service,
        lesson_id=lesson_id,
        reference_link_id=reference_link.id,
        token="a10-preference-pack",
        preferences=frozen_preferences,
    )
    current = service.get_teaching_preferences()
    service.update_teaching_preferences(
        expected_revision=current.revision,
        payload=_preferences(
            practice_trim_level="light",
            supplement_question_limit=3,
        ),
    )

    preflight = service.lesson_draft_preflight(pack.id, mode="model")
    adapter = FakeLessonModelAdapter(build_local_template(pack))
    service.lesson_model_adapter = adapter
    service.generate_lesson_draft(
        pack.id,
        operation_id="a10-preference-model",
        mode="model",
        confirmed=True,
    )

    assert pack.payload["preparation_preferences"] == frozen_preferences
    assert preflight["preparation_preferences"] == frozen_preferences
    assert adapter.calls[0]["resource_pack"]["preparation_preferences"] == (
        frozen_preferences
    )


def test_model_must_classify_every_reference_slide(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service, lesson_id, reference_link = _ready_service_and_sources(
        tmp_path,
        monkeypatch,
    )
    pack, _created = _freeze_with_preferences(
        service,
        lesson_id=lesson_id,
        reference_link_id=reference_link.id,
        token="a10-incomplete-model-pack",
        preferences=_preferences(),
    )
    incomplete = build_local_template(pack)
    incomplete["slide_adaptations"] = incomplete["slide_adaptations"][:-1]
    service.lesson_model_adapter = FakeLessonModelAdapter(incomplete)

    with pytest.raises(TeachingPrepValidationError):
        service.generate_lesson_draft(
            pack.id,
            operation_id="a10-incomplete-model-operation",
            mode="model",
            confirmed=True,
        )


def test_slide_plan_turns_reviewed_tendencies_into_safe_operations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service, lesson_id, reference_link = _ready_service_and_sources(
        tmp_path,
        monkeypatch,
    )
    pack, _created = _freeze_with_preferences(
        service,
        lesson_id=lesson_id,
        reference_link_id=reference_link.id,
        token="a10-slide-preference-pack",
        preferences=_preferences(supplement_question_limit=1),
    )
    local, _created = service.generate_lesson_draft(
        pack.id,
        operation_id="a10-slide-preference-draft",
        mode="local_template",
        confirmed=True,
    )
    edited = deepcopy(local.payload)
    textbook = next(
        item
        for item in pack.payload["materials"]
        if item["purpose"] == "textbook"
    )
    textbook_ref = (
        f"material:{textbook['link_id']}:"
        f"unit:{textbook['units'][0]['unit_index']}"
    )
    first = edited["slide_adaptations"][0]
    first["textbook_refs"] = [textbook_ref]
    first["citations"] = [first["slide_ref"], textbook_ref]
    second = edited["slide_adaptations"][1]
    second["role"] = "practice"
    second["action"] = "delete"
    second["reason"] = "课件后段练习过多，教师审核前列为候选删除。"
    revised, _created = service.revise_lesson_draft(
        local.id,
        request_token="a10-slide-preference-review",
        payload=edited,
        confirmed=True,
    )

    plan, _created = service.create_slide_plan(
        revised.id,
        request_token="a10-slide-preference-plan",
    )
    marker = next(
        item
        for item in plan.payload["operations"]
        if item["kind"] == "add_text_box"
    )
    deletion = next(
        item
        for item in plan.payload["operations"]
        if item["kind"] == "delete_slide"
    )

    printed_page = textbook["units"][0]["object_summary"][
        "printed_page_number"
    ]
    assert marker["details"]["text"] == f"教材 P{printed_page}"
    assert deletion["decision"] == "proposed"
    assert plan.payload["preparation_preferences"] == (
        pack.payload["preparation_preferences"]
    )

    missing_page_payload = deepcopy(pack.payload)
    missing_page_textbook = next(
        item
        for item in missing_page_payload["materials"]
        if item["purpose"] == "textbook"
    )
    missing_page_textbook["units"][0]["object_summary"].pop(
        "printed_page_number", None
    )
    missing_page_textbook["units"][0]["object_summary"].pop(
        "printed_page_number_source", None
    )
    missing_page_plan, _source_state = build_slide_plan_payload(
        replace(pack, payload=missing_page_payload),
        revised,
    )
    assert not any(
        item["kind"] == "add_text_box"
        for item in missing_page_plan["operations"]
    )

    offset_payload = deepcopy(pack.payload)
    offset_textbook = next(
        item
        for item in offset_payload["materials"]
        if item["purpose"] == "textbook"
    )
    offset_textbook["units"][0]["object_summary"].update(
        {
            "printed_page_number": 9,
            "printed_page_number_source": "visible_footer_or_header",
        }
    )
    offset_plan, _source_state = build_slide_plan_payload(
        replace(pack, payload=offset_payload),
        revised,
    )
    offset_marker = next(
        item
        for item in offset_plan["operations"]
        if item["kind"] == "add_text_box"
    )
    assert offset_marker["details"]["text"] == "教材 P9"
    assert offset_marker["decision"] == "proposed"
    assert offset_marker["target"]["position"]

    invalid_marker = deepcopy(offset_marker)
    invalid_marker["decision"] = "approved"
    invalid_marker["details"]["font_size"] = 24
    with pytest.raises(TeachingPrepValidationError):
        validate_operation(invalid_marker)

    executable = deepcopy(offset_plan)
    for operation in executable["operations"]:
        operation["decision"] = (
            "approved"
            if operation["operation_id"] == offset_marker["operation_id"]
            else "rejected"
        )
    request = build_executor_request(
        executable,
        source_sha256="a" * 64,
        source_copy=tmp_path / "source-copy.pptx",
        candidate=tmp_path / "candidate.pptx",
        preview_dir=tmp_path / "previews",
        resolve_asset=lambda _ref, _operation_id: tmp_path / "asset.png",
    )
    assert request["operations"][0]["details"]["font_size"] == 28
    assert request["operations"][0]["details"]["semantic_role"] == (
        "textbook_page_label"
    )


def test_slide_plan_model_proposal_uses_frozen_support_materials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service, lesson_id, reference_link = _ready_service_and_sources(
        tmp_path,
        monkeypatch,
    )
    pack, _created = _freeze_with_preferences(
        service,
        lesson_id=lesson_id,
        reference_link_id=reference_link.id,
        token="a10-model-slide-pack",
        preferences=_preferences(),
    )
    local, _created = service.generate_lesson_draft(
        pack.id,
        operation_id="a10-model-slide-local",
        mode="local_template",
        confirmed=True,
    )
    confirmed, _created = service.revise_lesson_draft(
        local.id,
        request_token="a10-model-slide-confirmed",
        payload=local.payload,
        confirmed=True,
    )
    proposed = build_local_template(pack)
    textbook = next(
        item
        for item in pack.payload["materials"]
        if item["purpose"] == "textbook"
    )
    textbook_ref = (
        f"material:{textbook['link_id']}:"
        f"unit:{textbook['units'][0]['unit_index']}"
    )
    first = proposed["slide_adaptations"][0]
    first["textbook_refs"] = [textbook_ref]
    first["citations"] = [first["slide_ref"], textbook_ref]
    first["reason"] = "依据冻结教材页标注讲授出处。"
    adapter = FakeLessonModelAdapter(proposed)
    service.lesson_model_adapter = adapter

    plan, created = service.create_slide_plan(
        confirmed.id,
        request_token="a10-model-slide-plan",
        model_proposal=True,
    )

    assert created is True
    assert adapter.calls[0]["operation_id"] == "a10-model-slide-plan"
    assert any(
        item["kind"] == "add_text_box"
        and item["citations"] == [first["slide_ref"], textbook_ref]
        for item in plan.payload["operations"]
    )


def test_slide_plan_targets_question_objects_existing_slide_and_textbook_label(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service, lesson_id, reference_link = _ready_service_and_sources(
        tmp_path,
        monkeypatch,
    )
    pack, _created = _freeze_with_preferences(
        service,
        lesson_id=lesson_id,
        reference_link_id=reference_link.id,
        token="a10-object-edit-pack",
        preferences=_preferences(supplement_question_limit=1),
    )
    draft, _created = service.generate_lesson_draft(
        pack.id,
        operation_id="a10-object-edit-draft",
        mode="local_template",
        confirmed=True,
    )
    payload = deepcopy(pack.payload)
    ppt = next(item for item in payload["materials"] if item["purpose"] == "reference_ppt")
    slide = ppt["units"][0]
    slide["object_summary"]["objects"] = [
        {
            "object_ref": "shape:7",
            "wps_object_id": "Question 3",
            "object_type": "text_box",
            "text": "3. 多余练习",
            "position": {"x": 0.62, "y": 0.52, "width": 0.32, "height": 0.25},
            "has_animation": False,
            "safe_to_delete": True,
        }
    ]
    slide_ref = f"material:{ppt['link_id']}:unit:{slide['unit_index']}"
    object_ref = f"{slide_ref}:object:shape:7"
    textbook = next(item for item in payload["materials"] if item["purpose"] == "textbook")
    textbook_unit = textbook["units"][0]
    textbook_unit["object_summary"].update(
        {
            "printed_page_number": 9,
            "printed_page_number_source": "teacher_confirmed",
        }
    )
    textbook_ref = f"material:{textbook['link_id']}:unit:{textbook_unit['unit_index']}"
    proposal = build_local_template(replace(pack, payload=payload))
    first = proposal["slide_adaptations"][0]
    first["delete_object_refs"] = [object_ref]
    first["textbook_refs"] = [textbook_ref]
    first["citations"] = [slide_ref, textbook_ref]
    included = next(
        item for item in proposal["exercise_recommendations"] if item["action"] == "include"
    )
    included["target_slide_ref"] = slide_ref

    plan, _source_state = build_slide_plan_payload(
        replace(pack, payload=payload),
        draft,
        proposal_payload=proposal,
    )
    deletion = next(item for item in plan["operations"] if item["kind"] == "delete_shape")
    insertion = next(
        item for item in plan["operations"] if item["kind"] == "insert_static_image"
    )
    marker = next(item for item in plan["operations"] if item["kind"] == "add_text_box")

    assert deletion["target"]["object_locator"] == {
        "match_confidence": "exact",
        "wps_object_id": "Question 3",
        "stable_signature": "shape:7",
    }
    assert deletion["risk"] == "high"
    for exact_top_level_type in ("grpSp", "graphicFrame"):
        exact_top_level_deletion = deepcopy(deletion)
        exact_top_level_deletion["decision"] = "approved"
        exact_top_level_deletion["target"]["object_type"] = (
            exact_top_level_type
        )
        assert validate_operation(exact_top_level_deletion)["decision"] == (
            "approved"
        )
    protected_picture_deletion = deepcopy(deletion)
    protected_picture_deletion["decision"] = "approved"
    protected_picture_deletion["target"]["object_type"] = "static_image"
    with pytest.raises(TeachingPrepValidationError):
        validate_operation(protected_picture_deletion)
    assert insertion["target"]["target_kind"] == "existing_slide"
    assert insertion["target"]["slide_signature"] == plan["slides"][0]["stable_signature"]
    assert "new_slide_operation_id" not in insertion["target"]
    assert insertion["target"]["position"] == {
        "x": 0.04,
        "y": 0.12,
        "width": 0.52,
        "height": 0.68,
    }
    assert marker["details"]["text"] == "教材 P9"

    executable = deepcopy(plan)
    approved_ids = {
        deletion["operation_id"],
        insertion["operation_id"],
        marker["operation_id"],
    }
    for operation in executable["operations"]:
        operation["decision"] = (
            "approved"
            if operation["operation_id"] in approved_ids
            else "rejected"
        )
    request = build_executor_request(
        executable,
        source_sha256="b" * 64,
        source_copy=tmp_path / "source-copy.pptx",
        candidate=tmp_path / "candidate.pptx",
        preview_dir=tmp_path / "previews",
        resolve_asset=lambda _ref, _operation_id: tmp_path / "question.png",
    )
    request_insertion = next(
        item for item in request["operations"] if item["kind"] == "insert_static_image"
    )
    assert request_insertion["target"]["target_kind"] == "existing_slide"
    assert request_insertion["target"]["generated_page_number"] == 1


def test_teacher_can_move_insert_and_correct_textbook_label(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service, lesson_id, reference_link = _ready_service_and_sources(
        tmp_path,
        monkeypatch,
    )
    pack, _created = _freeze_with_preferences(
        service,
        lesson_id=lesson_id,
        reference_link_id=reference_link.id,
        token="a10-review-placement-pack",
        preferences=_preferences(supplement_question_limit=1),
    )
    local, _created = service.generate_lesson_draft(
        pack.id,
        operation_id="a10-review-placement-local",
        mode="local_template",
        confirmed=True,
    )
    confirmed, _created = service.revise_lesson_draft(
        local.id,
        request_token="a10-review-placement-confirm",
        payload=local.payload,
        confirmed=True,
    )
    proposal = build_local_template(pack)
    textbook = next(
        item for item in pack.payload["materials"] if item["purpose"] == "textbook"
    )
    textbook_ref = (
        f"material:{textbook['link_id']}:unit:{textbook['units'][0]['unit_index']}"
    )
    first = proposal["slide_adaptations"][0]
    first["textbook_refs"] = [textbook_ref]
    first["citations"] = [first["slide_ref"], textbook_ref]
    service.lesson_model_adapter = FakeLessonModelAdapter(proposal)
    plan, _created = service.create_slide_plan(
        confirmed.id,
        request_token="a10-review-placement-plan",
        model_proposal=True,
    )
    marker = next(item for item in plan.payload["operations"] if item["kind"] == "add_text_box")
    insertion = next(
        item for item in plan.payload["operations"] if item["kind"] == "insert_static_image"
    )
    reviews = []
    for operation in plan.payload["operations"]:
        review = {
            "operation_id": operation["operation_id"],
            "decision": (
                "approved"
                if operation["operation_id"] in {
                    marker["operation_id"],
                    insertion["operation_id"],
                }
                else "rejected"
            ),
            "reason": operation["reason"],
            "planned_minutes": operation["planned_minutes"],
            "teacher_note": None,
        }
        if operation["operation_id"] == marker["operation_id"]:
            review["text"] = "教材 P88"
            review["position"] = {
                "x": 0.70,
                "y": 0.90,
                "width": 0.24,
                "height": 0.06,
            }
        if operation["operation_id"] == insertion["operation_id"]:
            review["target_slide_number"] = 2
            review["position"] = {
                "x": 0.46,
                "y": 0.16,
                "width": 0.48,
                "height": 0.62,
            }
        reviews.append(review)

    revised, _created = service.revise_slide_plan(
        plan.id,
        request_token="a10-review-placement-save",
        operation_reviews=reviews,
        approve_low_risk_deletions=False,
        review_note="人工校正位置与教材页码",
    )
    revised_marker = next(
        item for item in revised.payload["operations"] if item["operation_id"] == marker["operation_id"]
    )
    revised_insert = next(
        item for item in revised.payload["operations"] if item["operation_id"] == insertion["operation_id"]
    )

    assert revised_marker["details"]["text"] == "教材 P88"
    assert revised_marker["target"]["position"]["x"] == 0.70
    assert revised_insert["target"]["generated_page_number"] == 2
    assert revised_insert["target"]["slide_signature"] == revised.payload["slides"][1]["stable_signature"]
    assert revised_insert["target"]["position"]["x"] == 0.46


def test_legacy_resource_pack_uses_read_time_defaults_without_rewrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service, lesson_id, reference_link = _ready_service_and_sources(
        tmp_path,
        monkeypatch,
    )
    pack, _created = _freeze_with_preferences(
        service,
        lesson_id=lesson_id,
        reference_link_id=reference_link.id,
        token="a10-legacy-source-pack",
        preferences=_preferences(),
    )
    draft, _created = service.generate_lesson_draft(
        pack.id,
        operation_id="a10-legacy-draft",
        mode="local_template",
        confirmed=True,
    )
    legacy_payload = deepcopy(pack.payload)
    legacy_payload.pop("preparation_preferences")
    legacy_pack = replace(pack, payload=legacy_payload)

    preflight = draft_preflight(
        legacy_pack,
        mode="local_template",
        model_available=False,
        model_label=None,
    )
    preflight["model_destination_fingerprint"] = "f" * 64
    validated = LessonDraftPreflightResponse.model_validate(preflight)
    plan, _source_state = build_slide_plan_payload(legacy_pack, draft)

    assert validated.preparation_preferences.model_dump() == _preferences()
    assert plan["preparation_preferences"] == _preferences()
    assert any(
        item["kind"] == "insert_static_image"
        for item in plan["operations"]
    )
    assert "preparation_preferences" not in legacy_pack.payload


def test_local_supplement_selection_uses_homework_and_duplicate_preferences(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service, lesson_id, reference_link = _ready_service_and_sources(
        tmp_path,
        monkeypatch,
    )
    pack, _created = _freeze_with_preferences(
        service,
        lesson_id=lesson_id,
        reference_link_id=reference_link.id,
        token="a10-source-priority-pack",
        preferences=_preferences(supplement_question_limit=3),
    )
    payload = deepcopy(pack.payload)
    ordinary = payload["exercises"][0]
    homework = deepcopy(ordinary)
    homework["candidate_id"] = "full-workbook"
    homework["content_label"] = "学期指定作业教辅重点题"
    for region in homework["question_regions"]:
        region["material_name"] = "不含品牌名的练习册"
        region["semester_material_role"] = "homework_workbook"
    duplicate = deepcopy(ordinary)
    duplicate["candidate_id"] = "ppt-duplicate"
    duplicate["content_label"] = "Synthetic example x = 2"
    payload["exercises"] = [homework, duplicate, ordinary]

    draft = build_local_template(replace(pack, payload=payload))
    recommendations = {
        item["source_ref"]: item
        for item in draft["exercise_recommendations"]
    }

    assert recommendations["exercise:full-workbook"]["action"] == "backup"
    assert "不直接照搬" in recommendations["exercise:full-workbook"]["reason"]
    assert recommendations["exercise:ppt-duplicate"]["action"] == "backup"
    assert "重复" in recommendations["exercise:ppt-duplicate"]["reason"]
    assert recommendations[
        f"exercise:{ordinary['candidate_id']}"
    ]["action"] == "include"


def test_homework_workbook_role_never_comes_from_filename() -> None:
    branded_without_role = {
        "question_regions": [
            {
                "material_name": "全品学练考",
                "semester_material_role": "exercise_workbook",
            }
        ]
    }
    explicitly_selected = {
        "question_regions": [
            {
                "material_name": "普通练习册",
                "semester_material_role": "homework_workbook",
            }
        ]
    }

    assert _is_homework_workbook(branded_without_role) is False
    assert _is_homework_workbook(explicitly_selected) is True


def test_local_practice_classification_and_trim_levels_remain_distinct() -> None:
    assert _classify_slide("比例练习", "") == "practice"
    assert _classify_slide("例如后练习", "") == "practice"
    assert _classify_slide("例 2：求证", "") == "example"
    assert _classify_slide("练习后的讲解", "典型例题") == "example"
    materials = [
        {
            "purpose": "reference_ppt",
            "link_id": "reference",
            "teacher_intent": "keep",
            "units": [
                {
                    "unit_index": index,
                    "title": f"练习 {index}",
                    "text": "练习",
                }
                for index in range(1, 11)
            ],
        }
    ]
    delete_counts = []
    for level in ("light", "moderate", "strong"):
        payload = {
            "materials": materials,
            "preparation_preferences": _preferences(
                practice_trim_level=level,
                prefer_short_practice=False,
            ),
        }
        adaptations = _local_slide_adaptations(payload)
        delete_counts.append(
            sum(item["action"] == "delete" for item in adaptations)
        )

    assert delete_counts == [2, 3, 4]
    short_preserved = _local_slide_adaptations(
        {
            "materials": materials,
            "preparation_preferences": _preferences(
                practice_trim_level="strong",
                prefer_short_practice=True,
            ),
        }
    )
    assert not any(item["action"] == "delete" for item in short_preserved)
    materials[0]["units"][8] = {
        "unit_index": 9,
        "title": "典型例题",
        "text": "典型例题\n练习后的完整讲解" + "推导" * 120,
    }
    typical_example = _local_slide_adaptations(
        {
            "materials": materials,
            "preparation_preferences": _preferences(
                practice_trim_level="strong",
                preserve_teaching_examples=True,
            ),
        }
    )[8]
    assert typical_example["role"] == "example"
    assert typical_example["action"] == "keep"


def test_legacy_slide_plan_validation_adds_read_only_default_preferences() -> None:
    legacy_payload = {
        "schema_version": 1,
        "source_presentations": [],
        "slides": [],
        "operations": [],
        "unsupported_objects": [],
        "approval_history": [],
    }

    validated = validate_plan_payload(legacy_payload)

    assert validated["preparation_preferences"] == _preferences()
    assert "preparation_preferences" not in legacy_payload
    invalid_payload = deepcopy(legacy_payload)
    invalid_payload["preparation_preferences"] = None
    with pytest.raises(TeachingPrepValidationError):
        validate_plan_payload(invalid_payload)


def test_multi_region_supplement_keeps_every_frozen_crop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service, lesson_id, reference_link = _ready_service_and_sources(
        tmp_path,
        monkeypatch,
    )
    pack, _created = _freeze_with_preferences(
        service,
        lesson_id=lesson_id,
        reference_link_id=reference_link.id,
        token="a10-multi-region-pack",
        preferences=_preferences(supplement_question_limit=1),
    )
    draft, _created = service.generate_lesson_draft(
        pack.id,
        operation_id="a10-multi-region-draft",
        mode="local_template",
        confirmed=True,
    )
    payload = deepcopy(pack.payload)
    second_region = deepcopy(payload["exercises"][0]["question_regions"][0])
    second_region["region_id"] = "second-region"
    second_region["material_unit_id"] = "second-material-unit"
    second_region["preview_url"] = "/api/teaching-prep/exercise-regions/second/preview"
    payload["exercises"][0]["question_regions"].append(second_region)

    plan, _source_state = build_slide_plan_payload(
        replace(pack, payload=payload),
        draft,
    )
    images = [
        item
        for item in plan["operations"]
        if item["kind"] == "insert_static_image"
    ]

    assert len(images) == 2
    assert {
        item["target"]["material_unit_id"]
        for item in images
    } == {
        payload["exercises"][0]["question_regions"][0]["material_unit_id"],
        "second-material-unit",
    }
    assert images[0]["target"]["position"] != images[1]["target"]["position"]


def test_multiple_exercises_on_one_existing_slide_do_not_overlap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service, lesson_id, reference_link = _ready_service_and_sources(
        tmp_path,
        monkeypatch,
    )
    pack, _created = _freeze_with_preferences(
        service,
        lesson_id=lesson_id,
        reference_link_id=reference_link.id,
        token="a10-shared-slide-exercises-pack",
        preferences=_preferences(supplement_question_limit=2),
    )
    draft, _created = service.generate_lesson_draft(
        pack.id,
        operation_id="a10-shared-slide-exercises-draft",
        mode="local_template",
        confirmed=True,
    )
    pack_payload = deepcopy(pack.payload)
    second_exercise = deepcopy(pack_payload["exercises"][0])
    second_exercise["candidate_id"] = "second-exercise"
    second_exercise["question_regions"][0]["region_id"] = "second-region"
    second_exercise["question_regions"][0]["material_unit_id"] = (
        "second-material-unit"
    )
    pack_payload["exercises"].append(second_exercise)
    draft_payload = deepcopy(draft.payload)
    first_recommendation = draft_payload["exercise_recommendations"][0]
    second_recommendation = deepcopy(first_recommendation)
    second_recommendation["source_ref"] = "exercise:second-exercise"
    second_recommendation["citations"] = ["exercise:second-exercise"]
    draft_payload["exercise_recommendations"].append(second_recommendation)

    plan, _source_state = build_slide_plan_payload(
        replace(pack, payload=pack_payload),
        replace(draft, payload=draft_payload),
    )
    images = [
        item
        for item in plan["operations"]
        if item["kind"] == "insert_static_image"
    ]

    assert len(images) == 2
    assert images[0]["target"]["position"] != images[1]["target"]["position"]


def test_migration_default_matches_application_default() -> None:
    sql = (
        Path("migrations/teaching_prep/009_teacher_preferences.sql")
        .read_text(encoding="utf-8")
    )
    match = re.search(r"'(\{[^']+\})'", sql)

    assert match is not None
    assert json.loads(match.group(1)) == DEFAULT_TEACHING_PREFERENCES
