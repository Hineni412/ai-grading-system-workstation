from __future__ import annotations

import json
import logging
import sqlite3
import threading
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.teaching_prep.api import create_router
from backend.teaching_prep.application.lesson_drafts import (
    build_local_template,
)
from backend.teaching_prep.domain.errors import (
    TeachingPrepConflictError,
    TeachingPrepValidationError,
)
from backend.teaching_prep.infrastructure.fakes import FakeLessonModelAdapter

from .test_a01_foundation import _migrated_service
from .test_a05_resource_packs import (
    _evidence_fakes,
    _freeze,
    _freeze_ready_setup,
)


def _ready_pack(service, tmp_path: Path):
    question_reader, assessment_reader = _evidence_fakes()
    service.question_evidence_reader = question_reader
    service.assessment_evidence_reader = assessment_reader
    lesson_id, reference_link, _candidate = _freeze_ready_setup(
        service,
        tmp_path,
    )
    pack, _created = _freeze(
        service,
        token="a06-resource-pack",
        lesson_id=lesson_id,
        reference_link_id=reference_link.id,
    )
    return pack


def test_local_template_is_traceable_editable_and_capacity_is_local(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    pack = _ready_pack(service, tmp_path)

    preflight = service.lesson_draft_preflight(
        pack.id,
        mode="local_template",
    )
    draft, created = service.generate_lesson_draft(
        pack.id,
        operation_id="a06-local-operation",
        mode="local_template",
        confirmed=True,
    )

    assert preflight["will_call_model"] is False
    assert preflight["data_scope"]["anonymous_class_aggregate_only"] is True
    assert created is True
    assert draft.source_kind == "local_template"
    assert draft.capacity["lesson_minutes"] == 45
    assert draft.capacity["planned_minutes"] == (
        draft.capacity["flow_minutes"]
        + draft.capacity["exercise_minutes"]
        + draft.capacity["buffer_minutes"]
    )
    references = {
        item["id"]
        for item in preflight["references"]
    }
    for section in (
        "knowledge_objectives",
        "anticipated_difficulties",
        "lesson_flow",
        "exercise_recommendations",
    ):
        for item in draft.payload[section]:
            assert set(item["citations"]) <= references

    edited = deepcopy(draft.payload)
    edited["knowledge_objectives"][0]["text"] = "教师修订后的合成目标"
    edited["lesson_flow"][1]["suggested_minutes"] = 40
    revised, revised_created = service.revise_lesson_draft(
        draft.id,
        request_token="a06-teacher-revision",
        payload=edited,
        confirmed=True,
    )

    assert revised_created is True
    assert revised.version_number == 2
    assert revised.status == "confirmed"
    assert revised.source_kind == "teacher"
    assert revised.capacity["within_capacity"] is False
    assert revised.capacity["overrun_minutes"] > 0
    assert revised.capacity["reduction_options"]
    assert service.get_lesson_draft(draft.id).payload != revised.payload


def test_same_operation_calls_model_once_and_returns_saved_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    pack = _ready_pack(service, tmp_path)
    adapter = FakeLessonModelAdapter(build_local_template(pack))
    service.lesson_model_adapter = adapter
    service.lesson_model_label = "合成模型"

    first, created = service.generate_lesson_draft(
        pack.id,
        operation_id="a06-model-operation",
        mode="model",
        confirmed=True,
    )
    repeated, repeated_created = service.generate_lesson_draft(
        pack.id,
        operation_id="a06-model-operation",
        mode="model",
        confirmed=True,
    )

    assert created is True
    assert repeated_created is False
    assert repeated.id == first.id
    assert len(adapter.calls) == 1
    assert first.model_label == "合成模型"


def test_unknown_citation_rejects_whole_model_result_and_consumes_operation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    pack = _ready_pack(service, tmp_path)
    invalid = build_local_template(pack)
    invalid["focus_points"][0]["citations"] = [
        "material:invented:unit:999"
    ]
    adapter = FakeLessonModelAdapter(invalid)
    service.lesson_model_adapter = adapter

    with pytest.raises(TeachingPrepValidationError):
        service.generate_lesson_draft(
            pack.id,
            operation_id="a06-invalid-citation",
            mode="model",
            confirmed=True,
        )
    assert service.list_lesson_drafts(pack.id) == ()
    with pytest.raises(TeachingPrepConflictError):
        service.generate_lesson_draft(
            pack.id,
            operation_id="a06-invalid-citation",
            mode="model",
            confirmed=True,
        )
    assert len(adapter.calls) == 1


def test_invented_page_number_is_rejected_even_with_a_valid_citation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    pack = _ready_pack(service, tmp_path)
    invalid = build_local_template(pack)
    invalid["knowledge_objectives"][0]["text"] = "使用第999页完成合成目标"
    adapter = FakeLessonModelAdapter(invalid)
    service.lesson_model_adapter = adapter

    with pytest.raises(TeachingPrepValidationError):
        service.generate_lesson_draft(
            pack.id,
            operation_id="a06-invented-page",
            mode="model",
            confirmed=True,
        )
    assert service.list_lesson_drafts(pack.id) == ()


def test_model_unavailable_or_unconfirmed_does_not_create_operation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    pack = _ready_pack(service, tmp_path)

    with pytest.raises(TeachingPrepValidationError):
        service.generate_lesson_draft(
            pack.id,
            operation_id="a06-not-confirmed",
            mode="local_template",
            confirmed=False,
        )
    with pytest.raises(TeachingPrepValidationError):
        service.generate_lesson_draft(
            pack.id,
            operation_id="a06-model-disabled",
            mode="model",
            confirmed=True,
        )
    with sqlite3.connect(service.database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM teaching_prep_operations"
        ).fetchone()[0] == 0


def test_api_exposes_safe_preflight_and_logs_no_resource_or_model_body(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    pack = _ready_pack(service, tmp_path)
    marker = "SYNTHETIC_SECRET_LESSON_BODY"
    pack.payload["materials"][0]["units"][0]["text"] = marker
    adapter = FakeLessonModelAdapter(build_local_template(pack))
    service.lesson_model_adapter = adapter
    app = FastAPI()
    app.state.workspace_services = {"teaching-prep": service}
    app.include_router(create_router(), prefix="/api/teaching-prep")
    client = TestClient(app)

    with caplog.at_level(logging.INFO):
        preflight = client.get(
            f"/api/teaching-prep/resource-packs/{pack.id}/draft-preflight",
            params={"mode": "model"},
        )
        response = client.post(
            f"/api/teaching-prep/resource-packs/{pack.id}/lesson-drafts",
            json={
                "operation_id": "a06-api-operation",
                "mode": "model",
                "confirmed": True,
            },
        )

    assert preflight.status_code == 200
    assert len(preflight.json()["model_destination_fingerprint"]) == 64
    assert response.status_code == 201
    assert response.json()["capacity"]["planned_minutes"] > 0
    assert marker not in caplog.text
    assert json.dumps(adapter.result, ensure_ascii=False) not in caplog.text


@pytest.mark.parametrize("failure", [RuntimeError("failed"), TimeoutError()])
def test_model_failure_or_timeout_consumes_operation_without_saving_draft(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    pack = _ready_pack(service, tmp_path)
    adapter = FakeLessonModelAdapter(failure=failure)
    service.lesson_model_adapter = adapter

    with pytest.raises(type(failure)):
        service.generate_lesson_draft(
            pack.id,
            operation_id="a10-model-failure",
            mode="model",
            confirmed=True,
        )

    assert service.list_lesson_drafts(pack.id) == ()
    assert len(adapter.calls) == 1
    with pytest.raises(TeachingPrepConflictError):
        service.generate_lesson_draft(
            pack.id,
            operation_id="a10-model-failure",
            mode="model",
            confirmed=True,
        )
    assert len(adapter.calls) == 1


def test_bad_model_payload_and_teacher_cancel_never_publish_draft(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    pack = _ready_pack(service, tmp_path)
    invalid_adapter = FakeLessonModelAdapter()
    invalid_adapter.result = "not-a-draft"  # type: ignore[assignment]
    service.lesson_model_adapter = invalid_adapter

    with pytest.raises(TeachingPrepValidationError):
        service.generate_lesson_draft(
            pack.id,
            operation_id="a10-bad-model-json",
            mode="model",
            confirmed=True,
        )
    assert service.list_lesson_drafts(pack.id) == ()

    started = threading.Event()
    release = threading.Event()

    class BlockingAdapter:
        def generate(self, **_kwargs):
            started.set()
            assert release.wait(timeout=5)
            return build_local_template(pack)

    service.lesson_model_adapter = BlockingAdapter()
    errors: list[Exception] = []

    def generate() -> None:
        try:
            service.generate_lesson_draft(
                pack.id,
                operation_id="a10-cancel-model-call",
                mode="model",
                confirmed=True,
            )
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=generate)
    worker.start()
    assert started.wait(timeout=5)
    app = FastAPI()
    app.state.workspace_services = {"teaching-prep": service}
    app.include_router(create_router(), prefix="/api/teaching-prep")
    client = TestClient(app)
    cancelled = client.post(
        "/api/teaching-prep/lesson-draft-generations/"
        "a10-cancel-model-call/cancel"
    )
    repeated = client.post(
        "/api/teaching-prep/lesson-draft-generations/"
        "a10-cancel-model-call/cancel"
    )
    release.set()
    worker.join(timeout=5)

    assert cancelled.status_code == repeated.status_code == 200
    assert cancelled.json() == {
        "operation_id": "a10-cancel-model-call",
        "status": "cancelled",
        "newly_cancelled": True,
    }
    assert repeated.json()["newly_cancelled"] is False
    assert len(errors) == 1
    assert isinstance(errors[0], TeachingPrepConflictError)
    assert service.list_lesson_drafts(pack.id) == ()
