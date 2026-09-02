from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.teaching_prep.api import create_router
from backend.teaching_prep.application.slide_plans import validate_operation
from backend.teaching_prep.domain.errors import (
    TeachingPrepConflictError,
    TeachingPrepValidationError,
)

from .test_a01_foundation import _migrated_service
from .test_a05_resource_packs import (
    _evidence_fakes,
    _freeze,
    _freeze_ready_setup,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _confirmed_draft(service, tmp_path: Path, *, delete_reference=False):
    question_reader, assessment_reader = _evidence_fakes()
    service.question_evidence_reader = question_reader
    service.assessment_evidence_reader = assessment_reader
    lesson_id, reference_link, _candidate = _freeze_ready_setup(
        service,
        tmp_path,
    )
    if delete_reference:
        pack, _created = service.freeze_resource_pack(
            request_token="a07-delete-pack",
            lesson_node_id=lesson_id,
            class_name="合成七年级一班",
            lesson_type="new_lesson",
            teacher_context="匿名合成班情",
            reference_ppt_intents={
                reference_link.id: "candidate_delete"
            },
            question_ids=[101],
            assessment_ids=[7],
            knowledge_scope=["一元一次方程"],
        )
    else:
        pack, _created = _freeze(
            service,
            token="a07-resource-pack",
            lesson_id=lesson_id,
            reference_link_id=reference_link.id,
        )
    local, _created = service.generate_lesson_draft(
        pack.id,
        operation_id="a07-local-draft-operation",
        mode="local_template",
        confirmed=True,
    )
    confirmed, _created = service.revise_lesson_draft(
        local.id,
        request_token="a07-confirm-draft",
        payload=local.payload,
        confirmed=True,
    )
    return pack, confirmed, reference_link


def test_plan_is_idempotent_stable_and_does_not_touch_source_ppt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    pack, draft, _reference_link = _confirmed_draft(service, tmp_path)
    source = tmp_path / "a05-reference.pptx"
    before = _sha256(source)

    first, created = service.create_slide_plan(
        draft.id,
        request_token="a07-create-plan",
    )
    repeated, repeated_created = service.create_slide_plan(
        draft.id,
        request_token="a07-create-plan",
    )
    preview = service.slide_plan_preview(
        first.id,
        include_proposed=True,
    )

    assert created is True
    assert repeated_created is False
    assert repeated.id == first.id
    assert first.status == "in_review"
    assert first.resource_pack_id == pack.id
    assert first.payload["slides"]
    assert all(
        len(slide["stable_signature"]) == 64
        for slide in first.payload["slides"]
    )
    assert all(
        operation["target"]["match_strategy"]
        in {
            "source_fingerprint_and_slide_signature",
            "new_object",
            "manual_only",
            "question_insertion",
            "exact_wps_name_and_source_fingerprint",
        }
        for operation in first.payload["operations"]
    )
    assert all(
        operation["target"]["pre_execution_match"][
            "must_fail_closed_on_conflict"
        ]
        is True
        for operation in first.payload["operations"]
    )
    assert preview["before_slide_count"] == 2
    assert preview["after_slide_count"] == 2
    assert any(
        operation["kind"] == "insert_static_image"
        and operation["target"]["target_kind"] == "existing_slide"
        and operation["target"].get("new_slide_operation_id") is None
        for operation in first.payload["operations"]
    )
    assert before == _sha256(source)


def test_plan_requires_confirmed_draft_and_marks_unsupported_objects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    question_reader, assessment_reader = _evidence_fakes()
    service.question_evidence_reader = question_reader
    service.assessment_evidence_reader = assessment_reader
    lesson_id, reference_link, _candidate = _freeze_ready_setup(
        service,
        tmp_path,
    )
    pack, _created = _freeze(
        service,
        token="a07-unconfirmed-pack",
        lesson_id=lesson_id,
        reference_link_id=reference_link.id,
    )
    local, _created = service.generate_lesson_draft(
        pack.id,
        operation_id="a07-unconfirmed-draft",
        mode="local_template",
        confirmed=True,
    )

    with pytest.raises(TeachingPrepValidationError):
        service.create_slide_plan(
            local.id,
            request_token="a07-unconfirmed-plan",
        )

    confirmed, _created = service.revise_lesson_draft(
        local.id,
        request_token="a07-confirm-after-reject",
        payload=local.payload,
        confirmed=True,
    )
    plan, _created = service.create_slide_plan(
        confirmed.id,
        request_token="a07-protected-plan",
    )
    assert any(
        item["object_type"] == "complex_math_text"
        for item in plan.payload["unsupported_objects"]
    )


def test_batch_delete_approval_and_individual_undo_create_history_versions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    _pack, draft, _reference_link = _confirmed_draft(
        service,
        tmp_path,
        delete_reference=True,
    )
    plan, _created = service.create_slide_plan(
        draft.id,
        request_token="a07-delete-plan",
    )

    batch, created = service.revise_slide_plan(
        plan.id,
        request_token="a07-batch-delete",
        operation_reviews=[],
        approve_low_risk_deletions=True,
        review_note="批量确认低风险删除",
    )
    repeated, repeated_created = service.revise_slide_plan(
        plan.id,
        request_token="a07-batch-delete",
        operation_reviews=[],
        approve_low_risk_deletions=True,
        review_note="批量确认低风险删除",
    )

    deletions = [
        item
        for item in batch.payload["operations"]
        if item["kind"] == "delete_slide"
    ]
    assert created is True
    assert repeated_created is False
    assert repeated.id == batch.id
    assert deletions
    assert {item["decision"] for item in deletions} == {"approved"}
    assert len(batch.payload["approval_history"]) == len(deletions)
    first = deletions[0]
    undo, _created = service.revise_slide_plan(
        batch.id,
        request_token="a07-undo-delete",
        operation_reviews=[
            {
                "operation_id": first["operation_id"],
                "decision": "proposed",
                "reason": "教师撤销，保留此页继续复核。",
                "planned_minutes": first["planned_minutes"],
                "teacher_note": "撤销批量决定",
            }
        ],
        approve_low_risk_deletions=False,
        review_note="逐项撤销",
    )

    assert undo.version_number == batch.version_number + 1
    assert next(
        item
        for item in undo.payload["operations"]
        if item["operation_id"] == first["operation_id"]
    )["decision"] == "proposed"
    assert service.get_slide_plan(batch.id).payload != undo.payload


def test_all_items_decided_can_approve_plan_and_whitelist_is_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    _pack, draft, _reference_link = _confirmed_draft(service, tmp_path)
    plan, _created = service.create_slide_plan(
        draft.id,
        request_token="a07-review-plan",
    )
    reviews = []
    for item in plan.payload["operations"]:
        reviews.append(
            {
                "operation_id": item["operation_id"],
                "decision": (
                    "rejected"
                    if item["execution_mode"] == "manual_only"
                    else "approved"
                ),
                "reason": item["reason"],
                "planned_minutes": item["planned_minutes"],
                "teacher_note": "合成审批",
            }
        )
    approved, _created = service.revise_slide_plan(
        plan.id,
        request_token="a07-approve-plan",
        operation_reviews=reviews,
        approve_low_risk_deletions=False,
        review_note="逐项核对完成",
    )

    assert approved.status == "approved"
    assert all(
        item["decision"] != "proposed"
        for item in approved.payload["operations"]
    )

    illegal = deepcopy(plan.payload["operations"][0])
    illegal.update(
        {
            "kind": "modify_text_box",
            "decision": "approved",
            "execution_mode": "manual_only",
        }
    )
    with pytest.raises(TeachingPrepValidationError):
        validate_operation(illegal)


def test_source_change_invalidates_plan_and_export_is_safe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    _pack, draft, reference_link = _confirmed_draft(service, tmp_path)
    plan, _created = service.create_slide_plan(
        draft.id,
        request_token="a07-source-plan",
    )
    service.update_material_link(
        reference_link.id,
        expected_revision=reference_link.revision,
        start_unit=reference_link.start_unit,
        end_unit=reference_link.end_unit,
        crop=reference_link.crop,
        purpose=reference_link.purpose,
        teacher_note="合成来源范围已重新确认",
        confirmation_status=reference_link.confirmation_status,
        is_active=reference_link.is_active,
    )

    invalidated = service.get_slide_plan(plan.id)
    preview = service.slide_plan_preview(
        plan.id,
        include_proposed=True,
    )
    assert invalidated.status == "invalidated"
    assert preview["valid_for_execution"] is False
    operation = plan.payload["operations"][0]
    with pytest.raises(TeachingPrepConflictError):
        service.revise_slide_plan(
            plan.id,
            request_token="a07-invalidated-review",
            operation_reviews=[
                {
                    "operation_id": operation["operation_id"],
                    "decision": "approved",
                    "reason": operation["reason"],
                    "planned_minutes": operation["planned_minutes"],
                    "teacher_note": None,
                }
            ],
            approve_low_risk_deletions=False,
            review_note=None,
        )

    app = FastAPI()
    app.state.workspace_services = {"teaching-prep": service}
    app.include_router(create_router(), prefix="/api/teaching-prep")
    response = TestClient(app).get(
        f"/api/teaching-prep/slide-plans/{plan.id}/checklist"
    )

    assert response.status_code == 200
    assert response.json()["plan"]["status"] == "invalidated"
    assert "attachment" in response.headers["content-disposition"]
    assert str(tmp_path) not in response.text
    assert "local_path" not in response.text
    assert json.loads(response.text)["preview"]["source_changed"] is True
