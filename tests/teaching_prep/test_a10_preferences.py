from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from backend.api.app import ApiError
from backend.teaching_prep.api import create_router
from backend.teaching_prep.application.lesson_drafts import build_local_template
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
from backend.teaching_prep.application.slide_plans import validate_operation
from backend.teaching_prep.domain.errors import (
    TeachingPrepConflictError,
    TeachingPrepValidationError,
)
from backend.teaching_prep.infrastructure.fakes import FakeLessonModelAdapter

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

    assert marker["details"] == {
        "text": f"教材 P{textbook['units'][0]['unit_index']}",
        "font_size": 28,
        "semantic_role": "textbook_page_label",
    }
    assert marker["decision"] == "proposed"
    assert marker["target"]["position"]
    assert deletion["decision"] == "proposed"
    assert plan.payload["preparation_preferences"] == (
        pack.payload["preparation_preferences"]
    )
    invalid_marker = deepcopy(marker)
    invalid_marker["decision"] = "approved"
    invalid_marker["details"]["font_size"] = 24
    with pytest.raises(TeachingPrepValidationError):
        validate_operation(invalid_marker)

    executable = deepcopy(plan.payload)
    for operation in executable["operations"]:
        operation["decision"] = (
            "approved"
            if operation["operation_id"] == marker["operation_id"]
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
