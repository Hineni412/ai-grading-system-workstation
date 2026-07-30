from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.teaching_prep.api import create_router
from backend.teaching_prep.application import TeachingPrepService
from backend.teaching_prep.application.pptx_execution import digest
from backend.teaching_prep.domain.errors import TeachingPrepConflictError
from backend.teaching_prep.infrastructure.fakes import FakeWpsAdapter
from backend.teaching_prep.infrastructure.wps_adapter import (
    SubprocessWpsAdapter,
)

from .test_a01_foundation import _migrated_service
from .test_a07_slide_plans import _confirmed_draft


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _approved_plan(service: TeachingPrepService, tmp_path: Path):
    _pack, draft, _reference_link = _confirmed_draft(service, tmp_path)
    plan, _created = service.create_slide_plan(
        draft.id,
        request_token="a08-create-slide-plan",
    )
    reviews = [
        {
            "operation_id": item["operation_id"],
            "decision": (
                "rejected"
                if item["execution_mode"] == "manual_only"
                else "approved"
            ),
            "reason": item["reason"],
            "planned_minutes": item["planned_minutes"],
            "teacher_note": "合成 A08 审批",
        }
        for item in plan.payload["operations"]
    ]
    approved, _created = service.revise_slide_plan(
        plan.id,
        request_token="a08-approve-slide-plan",
        operation_reviews=reviews,
        approve_low_risk_deletions=False,
        review_note="合成执行前审批完成",
    )
    assert approved.status == "approved"
    return approved


def test_real_wps_adapter_is_explicitly_gated_without_starting_wps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AI_GRADING_TEACHING_PREP_WPS_ENABLED", "1")
    _paths, service = _migrated_service(tmp_path, monkeypatch)

    assert isinstance(service.wps_adapter, SubprocessWpsAdapter)
    assert service.status()["wps_execution_available"] is True
    assert service.status()["real_wps_enabled"] is True


def test_approved_plan_executes_on_copy_verifies_and_publishes_new_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service = _migrated_service(tmp_path, monkeypatch)
    plan = _approved_plan(service, tmp_path)
    source = tmp_path / "a05-reference.pptx"
    source_before = _sha256(source)
    adapter = FakeWpsAdapter()
    service.wps_adapter = adapter

    run, version, created = service.execute_slide_plan(
        plan.id,
        operation_id="a08-synthetic-execution",
        confirmed=True,
    )
    repeated, repeated_version, repeated_created = service.execute_slide_plan(
        plan.id,
        operation_id="a08-synthetic-execution",
        confirmed=True,
    )

    assert created is True
    assert repeated_created is False
    assert run.status == "published"
    assert repeated.id == run.id
    assert version is not None
    assert repeated_version == version
    assert version.status == "published"
    assert version.output_sha256
    assert version.verification_report["verified"] is True
    assert (
        version.verification_report["mathematical_correctness_checked"]
        is False
    )
    output, filename = service.pptx_download(version.id)
    assert output.is_file()
    assert output.name == filename == version.output_filename
    assert output != source
    assert _sha256(output) == version.output_sha256
    assert _sha256(source) == source_before
    assert len(adapter.calls) == 1
    executor_json = json.dumps(
        adapter.calls[0]["plan"],
        ensure_ascii=False,
        sort_keys=True,
    )
    assert '"reason"' not in executor_json
    assert '"citations"' not in executor_json
    assert "teacher_note" not in executor_json
    assert str(source) not in executor_json


def test_verification_failure_never_publishes_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service = _migrated_service(tmp_path, monkeypatch)
    plan = _approved_plan(service, tmp_path)
    service.wps_adapter = FakeWpsAdapter(
        result={
            "status": "completed",
            "source_lock_check": "passed",
            "applied_operation_ids": [],
            "reopened_in_wps": False,
            "rendered_all_slides": True,
            "slideshow_check_passed": True,
            "unapproved_content_preserved": True,
        }
    )

    run, version, created = service.execute_slide_plan(
        plan.id,
        operation_id="a08-invalid-verification",
        confirmed=True,
    )

    assert created is True
    assert version is None
    assert run.status == "failed"
    assert run.error_code == "verification_failed"
    assert run.staging_retained is True
    assert list(service.paths["outputs"].rglob("*.pptx")) == []


def test_timeout_and_second_operation_never_repeat_or_overwrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service = _migrated_service(tmp_path, monkeypatch)
    plan = _approved_plan(service, tmp_path)
    source = tmp_path / "a05-reference.pptx"
    source_before = _sha256(source)
    service.wps_adapter = FakeWpsAdapter(failure=TimeoutError())

    run, version, created = service.execute_slide_plan(
        plan.id,
        operation_id="a08-timeout-operation",
        confirmed=True,
    )

    assert created is True
    assert version is None
    assert run.status == "failed"
    assert run.error_code == "wps_helper_timeout"
    assert _sha256(source) == source_before
    assert list(service.paths["outputs"].rglob("*.pptx")) == []
    with pytest.raises(TeachingPrepConflictError):
        service.execute_slide_plan(
            plan.id,
            operation_id="a08-different-operation",
            confirmed=True,
        )


def test_changed_source_is_rejected_before_operation_is_created(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service = _migrated_service(tmp_path, monkeypatch)
    plan = _approved_plan(service, tmp_path)
    service.wps_adapter = FakeWpsAdapter()
    source = tmp_path / "a05-reference.pptx"
    source.write_bytes(source.read_bytes() + b"changed")

    with pytest.raises(TeachingPrepConflictError):
        service.execute_slide_plan(
            plan.id,
            operation_id="a08-source-changed",
            confirmed=True,
        )

    assert service.list_pptx_executions(plan.id) == ()


def test_restart_marks_running_execution_interrupted_and_retains_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service = _migrated_service(tmp_path, monkeypatch)
    plan = _approved_plan(service, tmp_path)
    source_record = plan.payload["source_presentations"][0]
    preview = service.slide_plan_preview(
        plan.id,
        include_proposed=False,
    )
    request_hash = digest(
        {
            "slide_plan_id": plan.id,
            "source_material_version_id": source_record[
                "material_version_id"
            ],
            "source_sha256": source_record["content_sha256"],
            "expected_slide_count": preview["after_slide_count"],
        }
    )
    run, created = service.pptx_executions.begin(
        operation_id="a08-interrupted-operation",
        request_hash=request_hash,
        slide_plan_id=plan.id,
        source_material_version_id=source_record["material_version_id"],
        source_sha256=source_record["content_sha256"],
        expected_slide_count=preview["after_slide_count"],
    )
    assert created is True
    staging = service.paths["staging"] / run.id
    staging.mkdir()

    restarted = TeachingPrepService(service.root)
    interrupted = restarted.get_pptx_execution(run.id)

    assert interrupted.status == "interrupted"
    assert interrupted.error_code == "application_restarted"
    assert interrupted.staging_retained is True
    assert interrupted.recovery_actions == ("discard_staging",)


def test_interrupted_atomic_publication_recovers_only_matching_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service = _migrated_service(tmp_path, monkeypatch)
    plan = _approved_plan(service, tmp_path)
    source_record = plan.payload["source_presentations"][0]
    preview = service.slide_plan_preview(
        plan.id,
        include_proposed=False,
    )
    request_hash = digest(
        {
            "slide_plan_id": plan.id,
            "source_material_version_id": source_record[
                "material_version_id"
            ],
            "source_sha256": source_record["content_sha256"],
            "expected_slide_count": preview["after_slide_count"],
        }
    )
    run, _created = service.pptx_executions.begin(
        operation_id="a08-publish-recovery",
        request_hash=request_hash,
        slide_plan_id=plan.id,
        source_material_version_id=source_record["material_version_id"],
        source_sha256=source_record["content_sha256"],
        expected_slide_count=preview["after_slide_count"],
    )
    staging = service.paths["staging"] / run.id
    staging.mkdir()
    candidate = staging / "candidate.pptx"
    candidate.write_bytes((tmp_path / "a05-reference.pptx").read_bytes())
    candidate_sha = _sha256(candidate)
    service.pptx_executions.set_verifying(
        run.id,
        {
            "schema_version": 1,
            "status": "completed",
            "applied_operation_ids": [],
        },
    )
    pack = service.resource_packs.get(plan.resource_pack_id)
    reserved, _relative = service.pptx_executions.begin_publish(
        run_id=run.id,
        lesson_node_id=pack.lesson_node_id,
        slide_plan_id=plan.id,
        slide_count=preview["after_slide_count"],
        verification_report={
            "verified": True,
            "candidate_sha256": candidate_sha,
            "mathematical_correctness_checked": False,
        },
    )

    restarted = TeachingPrepService(service.root)
    recovered, version = restarted.recover_pptx_execution(run.id)

    assert recovered.status == "published"
    assert version is not None
    assert version.id == reserved.id
    output, _filename = restarted.pptx_download(version.id)
    assert _sha256(output) == candidate_sha


def test_execution_api_is_path_safe_and_downloads_verified_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service = _migrated_service(tmp_path, monkeypatch)
    plan = _approved_plan(service, tmp_path)
    service.wps_adapter = FakeWpsAdapter()
    app = FastAPI()
    app.state.workspace_services = {"teaching-prep": service}
    app.include_router(create_router(), prefix="/api/teaching-prep")
    client = TestClient(app)

    response = client.post(
        f"/api/teaching-prep/slide-plans/{plan.id}/executions",
        json={
            "operation_id": "a08-api-execution",
            "confirmed": True,
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["execution"]["status"] == "published"
    assert payload["version"]["download_url"].startswith(
        "/api/teaching-prep/pptx-versions/"
    )
    serialized = json.dumps(payload, ensure_ascii=False)
    assert str(tmp_path) not in serialized
    assert "source-copy.pptx" not in serialized
    download = client.get(payload["version"]["download_url"])
    assert download.status_code == 200
    assert download.headers["content-type"].startswith(
        "application/vnd.openxmlformats"
    )
