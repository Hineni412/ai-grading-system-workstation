from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.teaching_prep.api import create_router
from backend.teaching_prep.application import TeachingPrepService
from backend.teaching_prep.application import preparation_service
from backend.teaching_prep.application.pptx_execution import digest
from backend.teaching_prep.domain.errors import (
    TeachingPrepConflictError,
    TeachingPrepNotFoundError,
)
from backend.teaching_prep.infrastructure.fakes import FakeWpsAdapter
from backend.teaching_prep.infrastructure.wps_adapter import (
    SubprocessWpsAdapter,
)

from .test_a01_foundation import _migrated_service
from .test_a07_slide_plans import _confirmed_draft


class _BlockingWpsAdapter:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.delegate = FakeWpsAdapter()

    def execute(
        self,
        *,
        operation_id: str,
        plan: dict[str, object],
    ) -> dict[str, object]:
        self.started.set()
        if not self.release.wait(timeout=5):
            raise TimeoutError("synthetic blocking adapter was not released")
        return self.delegate.execute(operation_id=operation_id, plan=plan)


class _BudgetExpiringParser:
    def __init__(self, delegate, database) -> None:
        self.delegate = delegate
        self.database = database

    def parse(self, path: Path, *, material_type: str):
        units = self.delegate.parse(path, material_type=material_type)
        if path.name == "candidate.pptx":
            with self.database.connect(immediate=True) as connection:
                connection.execute(
                    """
                    UPDATE teaching_prep_operations
                    SET created_at = '2026-07-30T00:00:00.000Z'
                    WHERE operation_id = (
                        SELECT operation_id
                        FROM pptx_execution_runs
                        WHERE status = 'verifying'
                        ORDER BY created_at DESC
                        LIMIT 1
                    )
                    """
                )
        return units


class _BlockingCandidateParser:
    def __init__(self, delegate, marker: Path) -> None:
        self.delegate = delegate
        self.marker = marker

    def parse(self, path: Path, *, material_type: str):
        if path.name == "candidate.pptx":
            self.marker.write_text("verification-started", encoding="utf-8")
            time.sleep(5)
        return self.delegate.parse(path, material_type=material_type)


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
    performance = service.get_lesson_generation_performance(run.id)
    assert performance.budget_ms == 300_000
    assert performance.within_budget is True
    assert performance.model_call_count == 0
    assert performance.wps_execution_count == 1
    assert performance.technical_retry_count == 0
    performance_budget = adapter.calls[0]["plan"]["performance_budget"]
    assert performance_budget["timeout_milliseconds"] == 170_000
    assert performance_budget["total_budget_ms"] == 300_000
    assert 170_000 < performance_budget["remaining_budget_ms"] <= 300_000
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


def test_generation_budget_stops_before_wps_and_never_publishes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service = _migrated_service(tmp_path, monkeypatch)
    plan = _approved_plan(service, tmp_path)
    with service.database.connect(immediate=True) as connection:
        operation = connection.execute(
            """
            SELECT operation_id
            FROM lesson_draft_versions
            WHERE resource_pack_id = ?
              AND operation_id IS NOT NULL
            ORDER BY created_at
            LIMIT 1
            """,
            (plan.resource_pack_id,),
        ).fetchone()
        assert operation is not None
        connection.execute(
            """
            UPDATE teaching_prep_operations
            SET created_at = '2026-07-30T00:00:00.000Z',
                finished_at = '2026-07-30T00:05:01.000Z'
            WHERE operation_id = ?
            """,
            (str(operation["operation_id"]),),
        )
    adapter = FakeWpsAdapter()
    service.wps_adapter = adapter

    run, version, created = service.execute_slide_plan(
        plan.id,
        operation_id="a08-budget-exceeded",
        confirmed=True,
    )

    assert created is True
    assert version is None
    assert run.status == "failed"
    assert run.error_code == "generation_budget_exceeded"
    assert adapter.calls == []
    performance = service.get_lesson_generation_performance(run.id)
    assert performance.budget_status == "exceeded"
    assert performance.within_budget is False
    assert performance.wps_execution_count == 0


def test_generation_budget_is_checked_during_candidate_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service = _migrated_service(tmp_path, monkeypatch)
    plan = _approved_plan(service, tmp_path)
    service.wps_adapter = FakeWpsAdapter()
    service.material_parser = _BudgetExpiringParser(
        service.material_parser,
        service.database,
    )

    run, version, created = service.execute_slide_plan(
        plan.id,
        operation_id="a08-verification-budget",
        confirmed=True,
    )

    assert created is True
    assert version is None
    assert run.status == "failed"
    assert run.error_code == "generation_budget_exceeded"
    assert list(service.paths["outputs"].rglob("*.pptx")) == []


def test_hard_deadline_terminates_a_blocking_candidate_verifier(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service = _migrated_service(tmp_path, monkeypatch)
    plan = _approved_plan(service, tmp_path)
    marker = tmp_path / "verification-worker-started.txt"
    service.wps_adapter = FakeWpsAdapter()
    service.material_parser = _BlockingCandidateParser(
        service.material_parser,
        marker,
    )
    remaining_checks = 0

    def remaining_budget(_run_id: str) -> int:
        nonlocal remaining_checks
        remaining_checks += 1
        # The first three checks are setup/WPS gates; this fourth one is the
        # timeout handed to the isolated verification worker.
        return 10_000 if remaining_checks == 4 else 60_000

    monkeypatch.setattr(
        service,
        "_remaining_generation_budget_ms",
        remaining_budget,
    )

    started = time.monotonic()
    run, version, created = service.execute_slide_plan(
        plan.id,
        operation_id="a08-hard-verification-deadline",
        confirmed=True,
    )
    elapsed = time.monotonic() - started

    assert created is True
    assert marker.is_file(), run.error_code
    assert elapsed < 13
    assert version is None
    assert run.status == "failed"
    assert run.error_code == "generation_budget_exceeded"
    assert list(service.paths["outputs"].rglob("*.pptx")) == []


def test_budget_breach_after_file_move_reverts_the_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service = _migrated_service(tmp_path, monkeypatch)
    plan = _approved_plan(service, tmp_path)
    service.wps_adapter = FakeWpsAdapter()
    original_rename = preparation_service.os.rename

    def expire_budget_after_move(source, target) -> None:
        original_rename(source, target)
        if Path(source).name != "candidate.pptx":
            return
        with service.database.connect(immediate=True) as connection:
            connection.execute(
                """
                UPDATE teaching_prep_operations
                SET created_at = '2026-07-30T00:00:00.000Z'
                WHERE operation_id = (
                    SELECT operation_id
                    FROM pptx_execution_runs
                    WHERE status = 'publishing'
                    ORDER BY created_at DESC
                    LIMIT 1
                )
                """
            )

    monkeypatch.setattr(
        preparation_service.os,
        "rename",
        expire_budget_after_move,
    )

    run, version, created = service.execute_slide_plan(
        plan.id,
        operation_id="a08-publication-budget-gate",
        confirmed=True,
    )

    assert created is True
    assert version is None
    assert run.status == "failed"
    assert run.error_code == "generation_budget_exceeded"
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


def test_concurrent_execution_is_rejected_and_cancelled_run_never_publishes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service = _migrated_service(tmp_path, monkeypatch)
    plan = _approved_plan(service, tmp_path)
    adapter = _BlockingWpsAdapter()
    service.wps_adapter = adapter
    result: list[object] = []
    errors: list[BaseException] = []

    def execute() -> None:
        try:
            result.extend(
                service.execute_slide_plan(
                    plan.id,
                    operation_id="a08-blocking-operation",
                    confirmed=True,
                )
            )
        except BaseException as exc:  # pragma: no cover - diagnostic capture
            errors.append(exc)

    worker = threading.Thread(target=execute)
    worker.start()
    assert adapter.started.wait(timeout=5)
    running = service.pptx_executions.get_by_operation(
        "a08-blocking-operation"
    )
    assert running.status == "running"

    with pytest.raises(TeachingPrepConflictError):
        service.execute_slide_plan(
            plan.id,
            operation_id="a08-concurrent-operation",
            confirmed=True,
        )

    app = FastAPI()
    app.state.workspace_services = {"teaching-prep": service}
    app.include_router(create_router(), prefix="/api/teaching-prep")
    client = TestClient(app)
    cancelled = client.post(
        f"/api/teaching-prep/pptx-executions/{running.id}/cancel"
    )
    adapter.release.set()
    worker.join(timeout=10)

    assert not worker.is_alive()
    assert errors == []
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert len(result) == 3
    final_run, version, created = result
    assert final_run.status == "cancelled"
    assert version is None
    assert created is True
    assert list(service.paths["outputs"].rglob("*.pptx")) == []
    assert service.list_pptx_executions(plan.id)[0].status == "cancelled"


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
            "generation_deadline_at": "2999-01-01T00:00:00.000Z",
        },
    )

    restarted = TeachingPrepService(service.root)
    recovered, version = restarted.recover_pptx_execution(run.id)

    assert recovered.status == "published"
    assert version is not None
    assert version.id == reserved.id
    output, _filename = restarted.pptx_download(version.id)
    assert _sha256(output) == candidate_sha


def test_expired_interrupted_publication_is_failed_without_download(
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
        operation_id="a08-expired-publish-recovery",
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
            "generation_deadline_at": "2000-01-01T00:00:00.000Z",
        },
    )
    with pytest.raises(TimeoutError, match="generation budget"):
        service.pptx_executions.finish_publish(
            run_id=run.id,
            version_id=reserved.id,
            output_sha256=candidate_sha,
            deadline_at="2000-01-01T00:00:00.000Z",
        )
    assert service.pptx_executions.get(run.id).status == "publishing"

    restarted = TeachingPrepService(service.root)
    recovered, version = restarted.recover_pptx_execution(run.id)

    assert recovered.status == "failed"
    assert recovered.error_code == "generation_budget_exceeded"
    assert version is None
    assert list(restarted.paths["outputs"].rglob("*.pptx")) == []
    with pytest.raises(TeachingPrepNotFoundError):
        restarted.pptx_download(reserved.id)


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

    assert response.status_code == 202
    payload = response.json()
    assert payload["execution"]["status"] == "running"
    assert payload["execution"]["phase"] == "copying"
    assert payload["version"] is None
    finished = client.get(
        f"/api/teaching-prep/pptx-executions/{payload['execution']['id']}"
    )
    assert finished.status_code == 200
    assert finished.json()["status"] == "published"
    lesson_id = service.get_resource_pack(plan.resource_pack_id).lesson_node_id
    versions = client.get(
        f"/api/teaching-prep/lessons/{lesson_id}/pptx-versions"
    )
    assert versions.status_code == 200
    version = versions.json()["items"][0]
    assert version["download_url"].startswith(
        "/api/teaching-prep/pptx-versions/"
    )
    serialized = json.dumps(
        {"start": payload, "finished": finished.json(), "version": version},
        ensure_ascii=False,
    )
    assert str(tmp_path) not in serialized
    assert "source-copy.pptx" not in serialized
    download = client.get(version["download_url"])
    assert download.status_code == 200
    assert download.headers["content-type"].startswith(
        "application/vnd.openxmlformats"
    )
