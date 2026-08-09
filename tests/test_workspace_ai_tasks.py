from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from backend.api.app import ApiError
from backend.api.routers.jobs import public_job_error, public_job_payload, public_job_result
from backend.api.routers.workspace_ai_tasks import router as workspace_ai_router
from backend.jobs.manager import JobManager
from backend.jobs.store import JobStore
from backend.llm import LLMGateway
from backend.llm.diagnostics import JsonlDiagnosticJournal
from backend.workspaces.ai_tasks.fakes import FakeWorkspaceAITaskAdapter
from backend.workspaces.ai_tasks.job_adapter import register_workspace_ai_job
from backend.workspaces.ai_tasks.model_gateway import WorkspaceAITaskModelGateway
from backend.workspaces.ai_tasks.models import (
    AdapterResult,
    HandoffDraft,
    OpaqueRef,
    OperationConflictError,
    PrepareRequest,
)
from backend.workspaces.ai_tasks.service import WorkspaceAITaskService
from backend.workspaces.ai_tasks.store import WorkspaceAITaskStore
from backend.workspaces.contracts import WorkspaceContext, WorkspaceFeature
from backend.workspaces.model_policy import WorkspaceModelGateway, WorkspaceModelRequest
from backend.workspaces.registry import WorkspaceRegistry
from backend.llm.trace import NullCallTraceSink
from backend.llm.usage import NullUsageSink


def _request(module: str = "teaching_prep") -> PrepareRequest:
    return PrepareRequest(
        module=module,
        task_kind=(
            "teaching_prep.lesson_plan"
            if module == "teaching_prep"
            else "class_teacher.intake_triage"
        ),
        source_ref=OpaqueRef(
            kind="lesson" if module == "teaching_prep" else "conversation",
            id="synthetic-source-001",
            revision="7",
        ),
        context_refs=(OpaqueRef(kind="snapshot", id="snapshot-001", revision="3"),),
        prompt_contract_version="synthetic-v1",
        model_destination_fingerprint="a" * 64,
        return_target=(
            "teaching_prep.lesson.plan"
            if module == "teaching_prep"
            else "class_teacher.home"
        ),
    )


def _result(module: str = "teaching_prep") -> AdapterResult:
    destination = (
        "teaching_prep.lesson.plan"
        if module == "teaching_prep"
        else "class_teacher.affair.record"
    )
    return AdapterResult(
        proposal_ref_id="proposal-001",
        proposal_revision="1",
        handoffs=(
            HandoffDraft(
                work_item_id="work-item-001",
                intent="review" if module == "teaching_prep" else "create",
                handling_mode="record",
                destination_key=destination,
                subject_refs=(OpaqueRef(kind="lesson", id="lesson-001", revision="2"),),
                draft_ref=OpaqueRef(kind="draft", id="draft-001", revision="1"),
                prefill_keys=("summary",),
                return_destination_key=(
                    "teaching_prep.overview"
                    if module == "teaching_prep"
                    else "class_teacher.home"
                ),
            ),
            HandoffDraft(
                work_item_id="work-item-002",
                intent="review" if module == "teaching_prep" else "plan",
                handling_mode="plan_calendar",
                destination_key=destination,
                subject_refs=(),
                draft_ref=OpaqueRef(kind="draft", id="draft-002", revision="1"),
                return_destination_key=(
                    "teaching_prep.overview"
                    if module == "teaching_prep"
                    else "class_teacher.home"
                ),
            ),
        ),
    )


def _service(tmp_path: Path, module: str = "teaching_prep"):
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


@pytest.mark.parametrize("module", ["teaching_prep", "class_teacher"])
def test_fake_adapters_share_prepare_dispatch_and_handoff_contract(
    tmp_path: Path,
    module: str,
) -> None:
    service, manager, adapter = _service(tmp_path, module)
    try:
        prepared = service.prepare(f"operation-{module}-001", _request(module))
        replayed = service.prepare(f"operation-{module}-001", _request(module))
        assert replayed.task_id == prepared.task_id
        dispatched = service.dispatch(
            prepared.operation_id,
            prepared_task_id=prepared.task_id,
        )
        assert dispatched.job_id is not None
        manager.wait(dispatched.job_id, timeout=5)
        finished = service.get(task_id=prepared.task_id)
        assert finished.status == "proposal_ready"
        assert finished.dispatch_evidence == "response_persisted"
        assert finished.send_attempt_count == 1
        assert finished.handoff_total == 2
        assert finished.pending_count == 2
        assert adapter.calls == [prepared.operation_id]
    finally:
        manager.shutdown()


def test_prepare_conflict_and_concurrent_dispatch_only_create_one_job(
    tmp_path: Path,
) -> None:
    service, manager, adapter = _service(tmp_path)
    try:
        prepared = service.prepare("operation-concurrent-001", _request())
        changed = replace(
            _request(),
            model_destination_fingerprint="b" * 64,
        )
        with pytest.raises(OperationConflictError):
            service.prepare(prepared.operation_id, changed)

        with ThreadPoolExecutor(max_workers=4) as executor:
            snapshots = list(
                executor.map(
                    lambda _index: service.dispatch(
                        prepared.operation_id,
                        prepared_task_id=prepared.task_id,
                    ),
                    range(4),
                )
            )
        job_ids = {item.job_id for item in snapshots}
        assert len(job_ids) == 1
        job_id = next(iter(job_ids))
        assert job_id is not None
        manager.wait(job_id, timeout=5)
        assert adapter.calls == [prepared.operation_id]
        with sqlite3.connect(service.store.db_path) as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM jobs WHERE job_type = 'workspace_ai.run'"
            ).fetchone()[0]
        assert count == 1
    finally:
        manager.shutdown()


def test_actionable_module_tasks_exclude_finished_history_and_closed_proposals(
    tmp_path: Path,
) -> None:
    service, manager, _adapter = _service(tmp_path)
    try:
        prepared = service.prepare("operation-actionable-prepared", _request())
        pending = service.prepare("operation-actionable-pending", _request())
        dispatched = service.dispatch(
            pending.operation_id,
            prepared_task_id=pending.task_id,
        )
        assert dispatched.job_id is not None
        manager.wait(dispatched.job_id, timeout=5)

        closed = service.prepare("operation-actionable-closed", _request())
        dispatched_closed = service.dispatch(
            closed.operation_id,
            prepared_task_id=closed.task_id,
        )
        assert dispatched_closed.job_id is not None
        manager.wait(dispatched_closed.job_id, timeout=5)
        failed = service.prepare("operation-actionable-failed", _request())
        with sqlite3.connect(service.store.db_path) as connection:
            connection.execute(
                "UPDATE workspace_ai_handoffs SET adoption_state = 'adopted' "
                "WHERE task_id = ?",
                (closed.task_id,),
            )
            connection.execute(
                "UPDATE workspace_ai_tasks SET status = 'failed', phase = 'finished' "
                "WHERE task_id = ?",
                (failed.task_id,),
            )

        actionable = service.list_actionable_module_tasks("teaching_prep")
        complete = service.list_module_tasks("teaching_prep")

        assert {task.task_id for task in actionable} == {
            prepared.task_id,
            pending.task_id,
        }
        assert {task.task_id for task in complete} == {
            prepared.task_id,
            pending.task_id,
            closed.task_id,
            failed.task_id,
        }
    finally:
        manager.shutdown()


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
                module="teaching_prep",
                draft_revision="1",
                target_revision="9",
            )
        result = service.adopt(
            handoff.handoff_id,
            module="teaching_prep",
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
                module="teaching_prep",
                draft_revision="1",
                target_revision="10",
            )
    finally:
        manager.shutdown()


def test_recovery_reuses_domain_proposal_and_never_resends(tmp_path: Path) -> None:
    service, manager, adapter = _service(tmp_path)
    try:
        prepared = service.prepare("operation-recover-proposal", _request())
        service.store.create_dispatch_job(prepared.task_id)
        task = service.store.claim(prepared.task_id)
        task = service.store.reserve_send_attempt(task.task_id)
        adapter.execute(task, model_gateway=service.model_gateway)

        service.recover_interrupted()

        recovered = service.get(task_id=prepared.task_id)
        assert recovered.status == "proposal_ready"
        assert adapter.calls == [prepared.operation_id]
    finally:
        manager.shutdown()


def test_startup_registers_domain_adapters_before_local_recovery(tmp_path: Path) -> None:
    service, manager, adapter = _service(tmp_path)
    try:
        prepared = service.prepare("operation-startup-recovery", _request())
        service.store.create_dispatch_job(prepared.task_id)
        task = service.store.claim(prepared.task_id)
        task = service.store.reserve_send_attempt(task.task_id)
        adapter.execute(task, model_gateway=service.model_gateway)

        restarted = WorkspaceAITaskService(
            store=service.store,
            manager=manager,
        )

        class Paths:
            def workspace_dir(self, module_id: str, *, create: bool = False) -> Path:
                del create
                return tmp_path / module_id

        def register_ai_tasks(registrar, _domain_service) -> None:
            registrar.register_adapter("teaching_prep.lesson_plan", adapter)

        registry = WorkspaceRegistry(
            [
                WorkspaceFeature(
                    module_id="teaching-prep",
                    api_prefix="/api/teaching-prep",
                    job_prefix="teaching_prep",
                    enabled=True,
                    register_ai_tasks=register_ai_tasks,
                )
            ],
            paths=Paths(),
        )
        registry.register_ai_tasks(restarted, {"teaching-prep": object()})
        restarted.recover_interrupted()

        assert restarted.get(task_id=prepared.task_id).status == "proposal_ready"
        assert adapter.calls == [prepared.operation_id]
    finally:
        manager.shutdown()


@pytest.mark.parametrize(
    ("module_id", "purpose", "response_text", "parse_status"),
    [
        (
            "teaching-prep",
            "lesson_plan",
            '{"synthetic":"model-return"}',
            "parsed",
        ),
        (
            "class-teacher",
            "intake_triage",
            "model-return-that-fails-business-validation",
            "not_json",
        ),
    ],
)
def test_task_gateway_records_complete_local_diagnostics_and_zero_retry(
    tmp_path: Path,
    module_id: str,
    purpose: str,
    response_text: str,
    parse_status: str,
) -> None:
    class Completions:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def create(self, **kwargs: object) -> dict[str, object]:
            self.calls.append(kwargs)
            return {
                "model": "synthetic-model",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": response_text,
                        },
                    }
                ],
            }

    class Paths:
        def workspace_dir(self, module_id: str, *, create: bool = False) -> Path:
            root = tmp_path / module_id
            if create:
                root.mkdir(parents=True, exist_ok=True)
            return root

    journal = JsonlDiagnosticJournal(tmp_path / "llm_diagnostics.jsonl")
    completions = Completions()
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    paths = Paths()
    context = WorkspaceContext(
        module_id=module_id,
        root=paths.workspace_dir(module_id),
        paths=paths,
    )
    low_level = LLMGateway()
    unsafe_gateway = WorkspaceModelGateway(
        context=context,
        gateway=low_level,
        metadata_only=False,
        claim_operations=False,
        allow_retry=True,
    )
    task_gateway = WorkspaceAITaskModelGateway(diagnostic_sink=journal)
    request = WorkspaceModelRequest(
        purpose=purpose,
        data_classification="confidential",
        operation_id=f"operation-{module_id}-diagnostics",
    )

    task_gateway.chat_completions(
        gateway=unsafe_gateway,
        request=request,
        client=client,
        model="synthetic-model",
        kwargs={"messages": [{"role": "user", "content": "synthetic body"}]},
    )

    assert len(completions.calls) == 1
    listed = journal.list_calls(limit=10, request_kind="workspace")
    assert listed["returned"] == 1
    call = journal.get_call(str(listed["items"][0]["call_id"]))
    assert call is not None
    expected_module = module_id.replace("-", "_")
    assert call["workspace_module"] == expected_module
    assert call["workspace_task_kind"] == purpose
    module_calls = journal.list_calls(
        limit=10,
        workspace_module=expected_module,
    )
    assert module_calls["returned"] == 1
    assert journal.list_calls(
        limit=10,
        workspace_module="class_teacher" if expected_module != "class_teacher" else "teaching_prep",
    )["returned"] == 0
    assert call["operation_id"] == request.operation_id
    assert call["outcome"] == "success"
    assert call["retry_limit"] == 0
    assert "synthetic body" in str(call["request"])
    assert call["raw_response"] == response_text
    assert call["parse_status"] == parse_status
    assert getattr(low_level.diagnostic_sink, "journal", None) is journal
    assert isinstance(low_level.trace_sink, NullCallTraceSink)
    assert isinstance(low_level.usage_sink, NullUsageSink)
    with pytest.raises(Exception, match="already used"):
        task_gateway.chat_completions(
            gateway=unsafe_gateway,
            request=request,
            client=object(),
            model="synthetic-model",
            kwargs={},
        )


def test_task_gateway_records_transport_failure_without_retry(
    tmp_path: Path,
) -> None:
    class FailingCompletions:
        def __init__(self) -> None:
            self.call_count = 0

        def create(self, **_kwargs: object) -> object:
            self.call_count += 1
            raise RuntimeError("synthetic upstream failure")

    class Paths:
        def workspace_dir(self, module_id: str, *, create: bool = False) -> Path:
            root = tmp_path / module_id
            if create:
                root.mkdir(parents=True, exist_ok=True)
            return root

    journal = JsonlDiagnosticJournal(tmp_path / "llm_diagnostics.jsonl")
    completions = FailingCompletions()
    context = WorkspaceContext(
        module_id="teaching-prep",
        root=tmp_path / "teaching-prep",
        paths=Paths(),
    )
    gateway = WorkspaceModelGateway(
        context=context,
        gateway=LLMGateway(),
        metadata_only=False,
        claim_operations=False,
        allow_retry=True,
    )

    with pytest.raises(RuntimeError, match="synthetic upstream failure"):
        WorkspaceAITaskModelGateway(diagnostic_sink=journal).chat_completions(
            gateway=gateway,
            request=WorkspaceModelRequest(
                purpose="semester_mapping",
                data_classification="confidential",
                operation_id="operation-teaching-prep-failure",
            ),
            client=SimpleNamespace(
                chat=SimpleNamespace(completions=completions),
            ),
            model="synthetic-model",
            kwargs={"messages": [{"role": "user", "content": "synthetic body"}]},
        )

    assert completions.call_count == 1
    listed = journal.list_calls(limit=10, request_kind="workspace")
    assert listed["returned"] == 1
    call = journal.get_call(str(listed["items"][0]["call_id"]))
    assert call is not None
    assert call["outcome"] == "failure"
    assert call["retry_limit"] == 0
    assert call["will_retry"] is False
    assert "synthetic body" in str(call["request"])
    assert call["error"]["message"] == "synthetic upstream failure"


def test_constructor_adapter_path_uses_registration_validation(tmp_path: Path) -> None:
    job_store = JobStore(tmp_path / "grading.db")
    manager = JobManager(job_store, max_workers=1, cleanup_interrupted=False)
    adapter = FakeWorkspaceAITaskAdapter(module="teaching_prep", result=_result())
    try:
        with pytest.raises(ValueError, match="not registered"):
            WorkspaceAITaskService(
                store=WorkspaceAITaskStore(job_store.db_path),
                manager=manager,
                adapters=(("teaching_prep.not_registered", adapter),),
            )
    finally:
        manager.shutdown()


def test_handoff_lifecycle_source_staleness_and_safe_metadata(tmp_path: Path) -> None:
    service, manager, adapter = _service(tmp_path)
    try:
        first = service.prepare("operation-handoff-state", _request())
        started = service.dispatch(first.operation_id, prepared_task_id=first.task_id)
        assert started.job_id is not None
        manager.wait(started.job_id, timeout=5)
        handoffs = service.get(task_id=first.task_id).handoffs
        service.mark_handoff(handoffs[0].handoff_id, "opened")
        service.mark_handoff(handoffs[0].handoff_id, "discarded")
        assert service.get(task_id=first.task_id).discarded_count == 1

        newer = replace(_request(), source_ref=replace(_request().source_ref, revision="8"))
        service.prepare("operation-handoff-newer", newer)
        assert service.get(task_id=first.task_id).stale_count == 1

        adapter.result = replace(
            _result(),
            handoffs=(replace(_result().handoffs[0], work_item_id="C:/private/student-name"),),
        )
        unsafe = service.prepare("operation-unsafe-handoff", replace(_request(), source_ref=replace(_request().source_ref, id="unsafe")))
        run = service.dispatch(unsafe.operation_id, prepared_task_id=unsafe.task_id)
        assert run.job_id is not None
        manager.wait(run.job_id, timeout=5)
        assert service.get(task_id=unsafe.task_id).status == "invalid_result"
        assert b"student-name" not in service.store.db_path.read_bytes()
    finally:
        manager.shutdown()


def test_common_database_and_job_projection_do_not_copy_domain_body(
    tmp_path: Path,
) -> None:
    service, manager, _adapter = _service(tmp_path)
    marker = "SYNTHETIC-PRIVATE-BODY-MUST-NOT-PERSIST"
    try:
        prepared = service.prepare("operation-body-scan-001", _request())
        started = service.dispatch(
            prepared.operation_id,
            prepared_task_id=prepared.task_id,
        )
        assert started.job_id is not None
        manager.wait(started.job_id, timeout=5)
        assert marker.encode() not in service.store.db_path.read_bytes()
        job = manager.get(started.job_id)
        assert job is not None
        assert job.payload == {"task_id": prepared.task_id}
        assert marker not in str(job.result)
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
        "module": "teaching_prep",
        "task_kind": "teaching_prep.lesson_plan",
        "source_ref": {"kind": "lesson", "id": "lesson-api-001", "revision": "1"},
        "context_refs": [
            {"kind": "semester", "id": "semester-api-001", "revision": "4"}
        ],
        "prompt_contract_version": "synthetic-v1",
        "model_destination_fingerprint": "a" * 64,
        "return_target": "teaching_prep.lesson.plan",
    }
    try:
        first = client.post("/api/workspace-ai-tasks/prepare", json=payload)
        replay = client.post("/api/workspace-ai-tasks/prepare", json=payload)
        assert first.status_code == replay.status_code == 201
        assert first.json()["task_id"] == replay.json()["task_id"]
        assert first.json()["context_refs"] == payload["context_refs"]
        assert [item.task_id for item in service.list_module_tasks("teaching_prep")] == [
            first.json()["task_id"]
        ]
        listed = client.get(
            "/api/workspace-ai-tasks",
            params={"module": "teaching_prep"},
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
