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


def test_begin_execution_persists_its_path_free_source_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service = _migrated_service(tmp_path, monkeypatch)
    plan = _approved_plan(service, tmp_path)
    source_record = plan.payload["source_presentations"][0]
    source_version = service.get_material_version(
        str(source_record["material_version_id"])
    )
    preview = service.slide_plan_preview(
        plan.id,
        include_proposed=False,
    )
    request_hash = digest(
        {
            "slide_plan_id": plan.id,
            "source_material_version_id": source_version.id,
            "source_sha256": source_version.content_sha256,
            "expected_slide_count": preview["after_slide_count"],
        }
    )

    run, created = service.pptx_executions.begin(
        operation_id="a08-source-snapshot-begin",
        request_hash=request_hash,
        slide_plan_id=plan.id,
        source_material_version_id=source_version.id,
        source_sha256=source_version.content_sha256,
        expected_slide_count=preview["after_slide_count"],
    )

    assert created is True
    assert run.source_material_version_id == source_version.id
    with service.database.connect() as connection:
        snapshot = connection.execute(
            """
            SELECT material_version_id, source_id, display_name, file_name,
                   material_type, content_sha256, size_bytes, schema_version
            FROM pptx_execution_source_snapshots
            WHERE material_version_id = ?
            """,
            (source_version.id,),
        ).fetchone()
    assert snapshot is not None
    assert tuple(snapshot) == (
        source_version.id,
        source_version.source_id,
        source_version.display_name,
        source_version.file_name,
        source_version.material_type,
        source_version.content_sha256,
        source_version.size_bytes,
        1,
    )


def test_source_rename_keeps_first_snapshot_name_and_allows_later_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service = _migrated_service(tmp_path, monkeypatch)
    plan = _approved_plan(service, tmp_path)
    source_record = plan.payload["source_presentations"][0]
    source_version = service.get_material_version(
        str(source_record["material_version_id"])
    )
    preview = service.slide_plan_preview(
        plan.id,
        include_proposed=False,
    )
    first_hash = digest(
        {
            "slide_plan_id": plan.id,
            "source_material_version_id": source_version.id,
            "source_sha256": source_version.content_sha256,
            "expected_slide_count": preview["after_slide_count"],
        }
    )
    _first, first_created = service.pptx_executions.begin(
        operation_id="a08-source-snapshot-before-rename",
        request_hash=first_hash,
        slide_plan_id=plan.id,
        source_material_version_id=source_version.id,
        source_sha256=source_version.content_sha256,
        expected_slide_count=preview["after_slide_count"],
    )
    assert first_created is True
    with service.database.connect() as connection:
        alternative = connection.execute(
            """
            SELECT id
            FROM slide_plan_versions
            WHERE id != ?
            ORDER BY created_at, id
            LIMIT 1
            """,
            (plan.id,),
        ).fetchone()
    assert alternative is not None
    alternative_plan_id = str(alternative["id"])
    renamed = service.update_material_source(
        source_version.source_id,
        expected_revision=source_version.source_revision,
        display_name="合成重命名后的课件",
        archived=None,
    )
    assert renamed.display_name == "合成重命名后的课件"
    second_hash = digest(
        {
            "slide_plan_id": alternative_plan_id,
            "source_material_version_id": source_version.id,
            "source_sha256": source_version.content_sha256,
            "expected_slide_count": preview["after_slide_count"],
        }
    )

    second, second_created = service.pptx_executions.begin(
        operation_id="a08-source-snapshot-after-rename",
        request_hash=second_hash,
        slide_plan_id=alternative_plan_id,
        source_material_version_id=source_version.id,
        source_sha256=source_version.content_sha256,
        expected_slide_count=preview["after_slide_count"],
    )

    assert second_created is True
    assert second.source_material_version_id == source_version.id
    with service.database.connect() as connection:
        snapshot_name = connection.execute(
            """
            SELECT display_name
            FROM pptx_execution_source_snapshots
            WHERE material_version_id = ?
            """,
            (source_version.id,),
        ).fetchone()
    assert snapshot_name is not None
    assert str(snapshot_name["display_name"]) == source_version.display_name


def test_begin_execution_fails_closed_on_source_snapshot_identity_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service = _migrated_service(tmp_path, monkeypatch)
    plan = _approved_plan(service, tmp_path)
    source_record = plan.payload["source_presentations"][0]
    source_version = service.get_material_version(
        str(source_record["material_version_id"])
    )
    preview = service.slide_plan_preview(
        plan.id,
        include_proposed=False,
    )
    first_hash = digest(
        {
            "slide_plan_id": plan.id,
            "source_material_version_id": source_version.id,
            "source_sha256": source_version.content_sha256,
            "expected_slide_count": preview["after_slide_count"],
        }
    )
    service.pptx_executions.begin(
        operation_id="a08-source-snapshot-before-conflict",
        request_hash=first_hash,
        slide_plan_id=plan.id,
        source_material_version_id=source_version.id,
        source_sha256=source_version.content_sha256,
        expected_slide_count=preview["after_slide_count"],
    )
    with service.database.connect(immediate=True) as connection:
        alternative = connection.execute(
            """
            SELECT id
            FROM slide_plan_versions
            WHERE id != ?
            ORDER BY created_at, id
            LIMIT 1
            """,
            (plan.id,),
        ).fetchone()
        assert alternative is not None
        alternative_plan_id = str(alternative["id"])
        connection.execute(
            """
            UPDATE pptx_execution_source_snapshots
            SET file_name = 'conflicting-history.pptx'
            WHERE material_version_id = ?
            """,
            (source_version.id,),
        )
    conflicting_hash = digest(
        {
            "slide_plan_id": alternative_plan_id,
            "source_material_version_id": source_version.id,
            "source_sha256": source_version.content_sha256,
            "expected_slide_count": preview["after_slide_count"],
        }
    )

    with pytest.raises(
        TeachingPrepConflictError,
        match="source material snapshot conflicts",
    ):
        service.pptx_executions.begin(
            operation_id="a08-source-snapshot-conflict",
            request_hash=conflicting_hash,
            slide_plan_id=alternative_plan_id,
            source_material_version_id=source_version.id,
            source_sha256=source_version.content_sha256,
            expected_slide_count=preview["after_slide_count"],
        )

    with service.database.connect() as connection:
        assert connection.execute(
            """
            SELECT 1
            FROM teaching_prep_operations
            WHERE operation_id = 'a08-source-snapshot-conflict'
            """
        ).fetchone() is None
        assert connection.execute(
            """
            SELECT 1
            FROM pptx_execution_runs
            WHERE slide_plan_id = ?
            """,
            (alternative_plan_id,),
        ).fetchone() is None


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


def test_material_delete_cannot_enter_before_publish_file_tail_finishes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service = _migrated_service(tmp_path, monkeypatch)
    plan = _approved_plan(service, tmp_path)
    source_record = plan.payload["source_presentations"][0]
    source_version = service.get_material_version(
        str(source_record["material_version_id"])
    )
    service.wps_adapter = FakeWpsAdapter()
    preview_copy_ready = threading.Event()
    release_preview_copy = threading.Event()
    real_copytree = preparation_service.shutil.copytree

    def pause_published_preview_copy(source_path, target_path, *args, **kwargs):
        target = Path(target_path).resolve()
        published_preview_root = (
            service.paths["previews"] / "pptx-versions"
        ).resolve()
        if target.parent == published_preview_root:
            preview_copy_ready.set()
            if not release_preview_copy.wait(timeout=5):
                raise TimeoutError("synthetic publish tail was not released")
        return real_copytree(source_path, target_path, *args, **kwargs)

    monkeypatch.setattr(
        preparation_service.shutil,
        "copytree",
        pause_published_preview_copy,
    )
    execution_results: list[tuple[object, object, bool]] = []
    execution_errors: list[BaseException] = []

    def execute() -> None:
        try:
            execution_results.append(
                service.execute_slide_plan(
                    plan.id,
                    operation_id="a08-publish-tail-delete-race",
                    confirmed=True,
                )
            )
        except BaseException as exc:  # pragma: no cover - diagnostic capture
            execution_errors.append(exc)

    worker = threading.Thread(target=execute)
    worker.start()
    assert preview_copy_ready.wait(timeout=5)
    in_tail = service.pptx_executions.get_by_operation(
        "a08-publish-tail-delete-race"
    )
    impact = service.preview_material_deletion(
        source_version.source_id,
        expected_revision=source_version.source_revision,
    )
    first_delete_errors: list[BaseException] = []
    first_delete_results: list[dict[str, object]] = []
    try:
        first_delete_results.append(
            service.delete_material_source(
                source_version.source_id,
                expected_revision=source_version.source_revision,
                operation_id="a08-publish-tail-first-delete",
                preview_version=str(impact["preview_version"]),
                confirmation_phrase=str(impact["confirmation_phrase"]),
            )
        )
    except BaseException as exc:  # pragma: no cover - diagnostic capture
        first_delete_errors.append(exc)
    finally:
        release_preview_copy.set()
    worker.join(timeout=10)

    assert in_tail.status == "publishing"
    assert impact["can_delete"] is False
    assert impact["blocking_generation_count"] == 1
    assert first_delete_results == []
    assert len(first_delete_errors) == 1
    assert isinstance(first_delete_errors[0], TeachingPrepConflictError)
    assert not worker.is_alive()
    assert execution_errors == []
    assert len(execution_results) == 1
    final_run, published, created = execution_results[0]
    assert created is True
    assert final_run.status == "published"
    assert published is not None

    refreshed = service.preview_material_deletion(
        source_version.source_id,
        expected_revision=source_version.source_revision,
    )
    deleted = service.delete_material_source(
        source_version.source_id,
        expected_revision=source_version.source_revision,
        operation_id="a08-publish-tail-final-delete",
        preview_version=str(refreshed["preview_version"]),
        confirmation_phrase=str(refreshed["confirmation_phrase"]),
    )

    assert refreshed["can_delete"] is True
    assert deleted["status"] == "succeeded"
    assert service.get_pptx_execution(final_run.id).status == "published"
    assert not service._execution_staging(final_run.id).exists()
    output, _filename = service.pptx_download(published.id)
    assert output.is_file()


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


@pytest.mark.parametrize("entrypoint", ["start", "sync"])
def test_pptx_begin_and_material_delete_share_the_material_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    entrypoint: str,
) -> None:
    _paths, service = _migrated_service(tmp_path, monkeypatch)
    plan = _approved_plan(service, tmp_path)
    source_record = plan.payload["source_presentations"][0]
    source_version = service.get_material_version(
        str(source_record["material_version_id"])
    )
    impact = service.preview_material_deletion(
        source_version.source_id,
        expected_revision=source_version.source_revision,
    )
    adapter = _BlockingWpsAdapter()
    service.wps_adapter = adapter
    begin_persisted = threading.Event()
    release_begin = threading.Event()
    real_begin = service.pptx_executions.begin

    def pause_after_begin(**kwargs):
        result = real_begin(**kwargs)
        begin_persisted.set()
        if not release_begin.wait(timeout=5):
            raise TimeoutError("synthetic begin was not released")
        return result

    monkeypatch.setattr(service.pptx_executions, "begin", pause_after_begin)
    execution_errors: list[BaseException] = []
    deletion_errors: list[BaseException] = []

    def execute() -> None:
        try:
            if entrypoint == "start":
                service.start_pptx_execution(
                    plan.id,
                    operation_id=f"a08-locked-{entrypoint}-execution",
                    confirmed=True,
                )
            else:
                service.execute_slide_plan(
                    plan.id,
                    operation_id=f"a08-locked-{entrypoint}-execution",
                    confirmed=True,
                )
        except BaseException as exc:  # pragma: no cover - diagnostic capture
            execution_errors.append(exc)

    def delete() -> None:
        try:
            service.delete_material_source(
                source_version.source_id,
                expected_revision=source_version.source_revision,
                operation_id=f"a08-locked-{entrypoint}-delete",
                preview_version=str(impact["preview_version"]),
                confirmation_phrase=str(impact["confirmation_phrase"]),
            )
        except BaseException as exc:  # pragma: no cover - diagnostic capture
            deletion_errors.append(exc)

    execution_worker = threading.Thread(target=execute)
    deletion_worker = threading.Thread(target=delete)
    execution_worker.start()
    assert begin_persisted.wait(timeout=5)
    deletion_worker.start()
    deletion_worker.join(timeout=0.2)
    deletion_waited_for_begin = deletion_worker.is_alive()
    release_begin.set()
    if entrypoint == "sync":
        assert adapter.started.wait(timeout=5)
    deletion_worker.join(timeout=5)
    adapter.release.set()
    execution_worker.join(timeout=10)

    assert deletion_waited_for_begin is True
    assert not deletion_worker.is_alive()
    assert not execution_worker.is_alive()
    assert execution_errors == []
    assert len(deletion_errors) == 1
    assert isinstance(deletion_errors[0], TeachingPrepConflictError)
    assert service.get_material_version(source_version.id).id == source_version.id


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
    source_version = service.get_material_version(
        str(source_record["material_version_id"])
    )
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
    (staging / "source-copy.pptx").write_bytes(b"synthetic source copy")

    restarted = TeachingPrepService(service.root)
    interrupted = restarted.get_pptx_execution(run.id)

    assert interrupted.status == "interrupted"
    assert interrupted.error_code == "application_restarted"
    assert interrupted.staging_retained is True
    assert interrupted.recovery_actions == ("discard_staging",)

    blocked = restarted.preview_material_deletion(
        source_version.source_id,
        expected_revision=source_version.source_revision,
    )
    assert blocked["can_delete"] is False
    assert blocked["blocking_generation_count"] == 1

    abandoned = restarted.discard_pptx_staging(run.id)

    assert abandoned.status == "failed"
    assert abandoned.error_code == "teacher_abandoned_recovery"
    assert abandoned.staging_retained is False
    assert abandoned.recovery_actions == ()
    assert not staging.exists()
    allowed = restarted.preview_material_deletion(
        source_version.source_id,
        expected_revision=source_version.source_revision,
    )
    assert allowed["can_delete"] is True
    assert allowed["blocking_generation_count"] == 0


def test_discard_interrupted_loses_cleanly_when_recovery_wins_the_cas(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service = _migrated_service(tmp_path, monkeypatch)
    plan = _approved_plan(service, tmp_path)
    source_record = plan.payload["source_presentations"][0]
    preview = service.slide_plan_preview(plan.id, include_proposed=False)
    run, created = service.pptx_executions.begin(
        operation_id="a08-discard-recovery-race",
        request_hash=digest(
            {
                "slide_plan_id": plan.id,
                "source_material_version_id": source_record[
                    "material_version_id"
                ],
                "source_sha256": source_record["content_sha256"],
                "expected_slide_count": preview["after_slide_count"],
            }
        ),
        slide_plan_id=plan.id,
        source_material_version_id=source_record["material_version_id"],
        source_sha256=source_record["content_sha256"],
        expected_slide_count=preview["after_slide_count"],
    )
    assert created is True
    staging = service._execution_staging(run.id)
    staging.mkdir()
    retained = staging / "candidate.pptx"
    retained.write_bytes(b"candidate must survive the losing discard")
    restarted = TeachingPrepService(service.root)
    real_get = restarted.pptx_executions.get
    first_read = True

    def recovery_wins_after_interrupted_read(run_id: str):
        nonlocal first_read
        current = real_get(run_id)
        if first_read:
            first_read = False
            assert current.status == "interrupted"
            with restarted.database.connect(immediate=True) as connection:
                connection.execute(
                    """
                    UPDATE pptx_execution_runs
                    SET status = 'publishing'
                    WHERE id = ? AND status = 'interrupted'
                    """,
                    (run_id,),
                )
                connection.execute(
                    """
                    UPDATE teaching_prep_operations
                    SET status = 'running'
                    WHERE operation_id = ? AND status = 'interrupted'
                    """,
                    (current.operation_id,),
                )
        return current

    monkeypatch.setattr(
        restarted.pptx_executions,
        "get",
        recovery_wins_after_interrupted_read,
    )

    with pytest.raises(
        TeachingPrepConflictError,
        match="recovery state changed",
    ):
        restarted.discard_pptx_staging(run.id)

    actual = real_get(run.id)
    assert actual.status == "publishing"
    with restarted.database.connect() as connection:
        operation = connection.execute(
            """
            SELECT status, error_code
            FROM teaching_prep_operations
            WHERE operation_id = ?
            """,
            (actual.operation_id,),
        ).fetchone()
    assert operation is not None
    assert tuple(operation) == ("running", "application_restarted")
    assert retained.read_bytes() == b"candidate must survive the losing discard"


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


def test_preview_only_execution_waits_for_teacher_confirm_before_publish(
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
        operation_id="a08-preview-only-execution",
        confirmed=True,
        preview_only=True,
    )
    assert created is True
    service.process_pptx_execution(started.id, publish=False)
    previewed = service.get_pptx_execution(started.id)
    assert previewed.status == "verifying"
    assert previewed.published_version_id is None
    assert previewed.verification_report is not None
    preview = service.pptx_execution_preview_path(started.id, slide_number=1)
    assert preview.is_file()
    versions = service.list_lesson_pptx_versions(
        service.get_resource_pack(plan.resource_pack_id).lesson_node_id
    )
    assert versions == ()
    assert _sha256(source) == source_before

    restarted = TeachingPrepService(service.root)
    restarted.wps_adapter = FakeWpsAdapter()
    previewed_after_restart = restarted.get_pptx_execution(started.id)
    assert previewed_after_restart.status == "verifying"
    assert previewed_after_restart.published_version_id is None
    assert previewed_after_restart.verification_report is not None

    finished, version = restarted.confirm_pptx_preview(
        started.id, confirmed=True
    )
    assert finished.status == "published"
    assert version is not None
    assert version.status == "published"
    output, _filename = restarted.pptx_download(version.id)
    assert output.is_file()
    assert output != source
    assert _sha256(source) == source_before


def test_preview_only_api_waits_for_confirm_before_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service = _migrated_service(tmp_path, monkeypatch)
    plan = _approved_plan(service, tmp_path)
    source = tmp_path / "a05-reference.pptx"
    source_before = _sha256(source)
    service.wps_adapter = FakeWpsAdapter()
    app = FastAPI()
    app.state.workspace_services = {"teaching-prep": service}
    app.include_router(create_router(), prefix="/api/teaching-prep")
    client = TestClient(app)

    response = client.post(
        f"/api/teaching-prep/slide-plans/{plan.id}/executions",
        json={
            "operation_id": "a08-preview-only-api",
            "confirmed": True,
            "preview_only": True,
        },
    )
    assert response.status_code == 202
    run_id = response.json()["execution"]["id"]
    previewed = client.get(f"/api/teaching-prep/pptx-executions/{run_id}")
    assert previewed.status_code == 200
    assert previewed.json()["status"] == "verifying"
    assert previewed.json()["published_version_id"] is None
    page = client.get(
        f"/api/teaching-prep/pptx-executions/{run_id}/preview",
        params={"slide": 1},
    )
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("image/png")
    lesson_id = service.get_resource_pack(plan.resource_pack_id).lesson_node_id
    versions = client.get(
        f"/api/teaching-prep/lessons/{lesson_id}/pptx-versions"
    )
    assert versions.status_code == 200
    assert versions.json()["items"] == []

    confirmed = client.post(
        f"/api/teaching-prep/pptx-executions/{run_id}/confirm-preview",
        json={"confirmed": True},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["execution"]["status"] == "published"
    assert confirmed.json()["version"] is not None
    assert _sha256(source) == source_before
