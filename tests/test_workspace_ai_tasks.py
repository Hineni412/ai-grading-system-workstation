from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from backend.api.app import ApiError
from backend.api.routers.jobs import (
    public_job_error,
    public_job_payload,
    public_job_result,
)
from backend.api.routers.workspace_ai_tasks import router as workspace_ai_router
from backend.jobs.manager import JobManager
from backend.jobs.store import JobStore
from backend.workspaces.ai_tasks.fakes import FakeWorkspaceAITaskAdapter
from backend.workspaces.ai_tasks.job_adapter import register_workspace_ai_job
from backend.workspaces.ai_tasks.models import (
    AdapterResult,
    HandoffDraft,
    OpaqueRef,
    OperationConflictError,
    PrepareRequest,
)
from backend.workspaces.ai_tasks.service import WorkspaceAITaskService
from backend.workspaces.ai_tasks.store import WorkspaceAITaskStore


@pytest.fixture(autouse=True)
def synthetic_task_registration(monkeypatch):
    # Exercise the generic task engine with a test-only adapter. Production has no workbench registrations.
    from backend.workspaces.ai_tasks import registry

    monkeypatch.setattr(
        registry,
        "_PRESENTATIONS",
        {
            ("class_teacher", "class_teacher.intake_triage"): registry.TaskPresentation(
                "class_teacher", "class_teacher.intake_triage", "合成工作项", "合成测试"
            ),
        },
    )
    monkeypatch.setattr(
        registry,
        "DESTINATION_KEYS",
        frozenset(
            {
                "class_teacher.home",
                "class_teacher.affair.record",
                "class_teacher.student.record",
                "class_teacher.plan.calendar",
                "class_teacher.affair.sop",
            }
        ),
    )


def _request(module: str = "class_teacher") -> PrepareRequest:
    return PrepareRequest(
        module=module,
        task_kind="class_teacher.intake_triage",
        source_ref=OpaqueRef(
            kind="conversation",
            id="synthetic-source-001",
            revision="7",
        ),
        context_refs=(OpaqueRef(kind="snapshot", id="snapshot-001", revision="3"),),
        prompt_contract_version="synthetic-v1",
        model_destination_fingerprint="a" * 64,
        return_target="class_teacher.home",
    )


def _result(module: str = "class_teacher") -> AdapterResult:
    destination = "class_teacher.affair.record"
    return AdapterResult(
        proposal_ref_id="proposal-001",
        proposal_revision="1",
        handoffs=(
            HandoffDraft(
                work_item_id="work-item-001",
                intent="create",
                handling_mode="record",
                destination_key=destination,
                subject_refs=(OpaqueRef(kind="lesson", id="lesson-001", revision="2"),),
                draft_ref=OpaqueRef(kind="draft", id="draft-001", revision="1"),
                prefill_keys=("summary",),
                return_destination_key="class_teacher.home",
            ),
            HandoffDraft(
                work_item_id="work-item-002",
                intent="plan",
                handling_mode="plan_calendar",
                destination_key=destination,
                subject_refs=(),
                draft_ref=OpaqueRef(kind="draft", id="draft-002", revision="1"),
                return_destination_key="class_teacher.home",
            ),
        ),
    )


def _service(tmp_path: Path, module: str = "class_teacher"):
    job_store = JobStore(tmp_path / "grading.db")
    manager = JobManager(job_store, max_workers=2, cleanup_interrupted=False)
    adapter = FakeWorkspaceAITaskAdapter(module=module, result=_result(module))
    service = WorkspaceAITaskService(
        store=WorkspaceAITaskStore(job_store.db_path),
        manager=manager,
        adapters=((_request(module).task_kind, adapter),),
    )
    register_workspace_ai_job(manager, service)
    return service, manager, adapter


def test_cancel_and_restart_recovery_follow_send_evidence(tmp_path: Path) -> None:
    service, manager, _adapter = _service(tmp_path)
    try:
        prepared = service.prepare("operation-cancel-001", _request())
        cancelled = service.cancel(prepared.operation_id)
        assert cancelled.status == "cancelled_before_dispatch"
        assert cancelled.send_attempt_count == 0

        before = service.prepare("operation-restart-before", _request())
        service.store.create_dispatch_job(before.task_id)
        service.recover_interrupted()
        assert service.get(task_id=before.task_id).status == "failed_before_dispatch"

        after = service.prepare("operation-restart-after", _request())
        service.store.create_dispatch_job(after.task_id)
        service.store.claim(after.task_id)
        service.store.reserve_send_attempt(after.task_id)
        service.recover_interrupted()
        recovered = service.get(task_id=after.task_id)
        assert recovered.status == "result_unknown"
        assert recovered.dispatch_evidence == "may_have_started"
        repeated = service.dispatch(
            after.operation_id,
            prepared_task_id=after.task_id,
        )
        assert repeated.job_id == recovered.job_id
        assert repeated.send_attempt_count == 1

        discarded = service.discard_result_unknown(after.operation_id)
        assert discarded.status == "discarded"
        assert discarded.dispatch_evidence == "may_have_started"
        assert discarded.send_attempt_count == 1
        assert _adapter.discarded_unknown_operations == [after.operation_id]
        assert service.discard_result_unknown(after.operation_id).status == "discarded"
        assert _adapter.discarded_unknown_operations == [after.operation_id]
    finally:
        manager.shutdown()


def test_adoption_receipt_converges_after_response_loss(tmp_path: Path) -> None:
    service, manager, adapter = _service(tmp_path)
    try:
        prepared = service.prepare("operation-adopt-001", _request())
        started = service.dispatch(
            prepared.operation_id,
            prepared_task_id=prepared.task_id,
        )
        assert started.job_id is not None
        manager.wait(started.job_id, timeout=5)
        handoff = next(
            item
            for item in service.get(task_id=prepared.task_id).handoffs
            if item.work_item_id == "work-item-001"
        )
        adapter.fail_after_receipt = True
        with pytest.raises(RuntimeError, match="response loss"):
            service.adopt(
                handoff.handoff_id,
                module="class_teacher",
                draft_revision="1",
                target_revision="9",
            )
        result = service.adopt(
            handoff.handoff_id,
            module="class_teacher",
            draft_revision="1",
            target_revision="9",
        )
        assert len(adapter.receipts) == 1
        finished = service.get(task_id=prepared.task_id)
        assert finished.adopted_count == 1
        assert finished.pending_count == 1
        assert result.object_ref.endswith("work-item-001")
        with pytest.raises(Exception):
            service.adopt(
                handoff.handoff_id,
                module="class_teacher",
                draft_revision="1",
                target_revision="10",
            )
    finally:
        manager.shutdown()


def test_safe_api_is_idempotent_and_workspace_job_projection_is_allowlisted(
    tmp_path: Path,
) -> None:
    service, manager, _adapter = _service(tmp_path)
    app = FastAPI()
    app.state.workspace_ai_task_service = service
    app.include_router(workspace_ai_router)

    @app.exception_handler(ApiError)
    async def handle_error(_request, error: ApiError):
        return JSONResponse(
            status_code=error.status_code,
            content={"error": {"code": error.code, "message": error.message}},
        )

    client = TestClient(app)
    payload = {
        "operation_id": "operation-api-001",
        "module": "class_teacher",
        "task_kind": "class_teacher.intake_triage",
        "source_ref": {"kind": "conversation", "id": "source-api-001", "revision": "1"},
        "context_refs": [
            {"kind": "semester", "id": "semester-api-001", "revision": "4"}
        ],
        "prompt_contract_version": "synthetic-v1",
        "model_destination_fingerprint": "a" * 64,
        "return_target": "class_teacher.home",
    }
    try:
        first = client.post("/api/workspace-ai-tasks/prepare", json=payload)
        replay = client.post("/api/workspace-ai-tasks/prepare", json=payload)
        assert first.status_code == replay.status_code == 201
        assert first.json()["task_id"] == replay.json()["task_id"]
        assert first.json()["context_refs"] == payload["context_refs"]
        assert [
            item.task_id for item in service.list_module_tasks("class_teacher")
        ] == [first.json()["task_id"]]
        listed = client.get(
            "/api/workspace-ai-tasks",
            params={"module": "class_teacher"},
        )
        assert listed.status_code == 200
        assert [item["task_id"] for item in listed.json()] == [first.json()["task_id"]]
        assert "prompt" not in listed.text.casefold()
        assert "prompt" not in first.text.casefold()
        assert "prefill" not in first.text.casefold()
        dispatch = client.post(
            "/api/workspace-ai-tasks/operations/operation-api-001/dispatch",
            json={"prepared_task_id": first.json()["task_id"]},
        )
        assert dispatch.status_code == 202
        job_id = dispatch.json()["job_id"]
        assert job_id is not None
        manager.wait(job_id, timeout=5)
        job = manager.get(job_id)
        assert job is not None
        assert public_job_payload(job) == {"task_id": first.json()["task_id"]}
        assert set(public_job_result(job)) <= {"task_id", "status"}

        unsafe = replace(job, error="C:\\private\\student-name.txt secret body")
        assert "student-name" not in str(public_job_error(unsafe))
    finally:
        manager.shutdown()
