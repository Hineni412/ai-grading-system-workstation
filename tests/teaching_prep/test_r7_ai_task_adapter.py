from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace

import pytest

from backend.api.routers.workspace_ai_tasks import router as workspace_ai_router
from backend.jobs.manager import JobManager
from backend.jobs.store import JobStore
from backend.teaching_prep.domain.errors import (
    TeachingPrepNotFoundError,
    TeachingPrepRetryAvailableError,
)
from backend.teaching_prep.infrastructure.fakes import (
    FakeExerciseSuggestionModelAdapter,
)
from backend.teaching_prep.application.ai_task_adapter import (
    TeachingPrepAITaskAdapter,
    bind_adoption_command,
)
from backend.workspaces.ai_tasks.model_gateway import WorkspaceAITaskModelGateway
from backend.workspaces.ai_tasks.models import (
    AdoptionResult,
    AdapterResult,
    HandoffSnapshot,
    KnownAdapterFailure,
    OpaqueRef,
    PrepareRequest,
    RevisionConflictError,
    StoredTask,
)
from backend.workspaces.ai_tasks.service import WorkspaceAITaskService
from backend.workspaces.ai_tasks.store import WorkspaceAITaskStore

from .test_a01_foundation import _migrated_service
from .test_a02_catalog import _api_client
from .test_a03_material_units import _pdf
from .test_a05_resource_packs import _freeze_ready_setup
from .test_a07_slide_plans import _confirmed_draft
from .test_a11_semester_workspace import (
    _FakeSemesterMappingModel,
    _accept_all_mappings,
    _semester,
)
from .test_a12_workbench_iteration import _selection, _suggestion


def _task() -> StoredTask:
    return StoredTask(
        task_id="task-r7-slide",
        operation_id="operation-r7-slide",
        module="teaching_prep",
        task_kind="teaching_prep.slide_change_proposal",
        source_ref=OpaqueRef("lesson", "l" * 32, "3"),
        context_refs=(OpaqueRef("lesson_draft", "draft-r7", "3"),),
        prompt_contract_version="slide-proposal-v1",
        request_fingerprint="a" * 64,
        model_destination_fingerprint="b" * 64,
        return_target="teaching_prep.lesson.slides",
        status="running",
        phase="send_attempt_reserved",
        progress=.15,
        send_attempt_count=1,
        dispatch_evidence="may_have_started",
        cancel_requested=False,
        proposal_ref_id=None,
        proposal_revision=None,
        job_id=1,
        error_code=None,
        revision=2,
        created_at="2026-08-05T00:00:00Z",
        updated_at="2026-08-05T00:00:00Z",
        finished_at=None,
    )


def _persist_proposal_handoff(
    adapter: TeachingPrepAITaskAdapter,
    task: StoredTask,
    *,
    proposal_id: str,
    proposal_revision: str,
    target_revision: str,
    adoption_id: str,
) -> tuple[AdapterResult, HandoffSnapshot]:
    result = adapter._result(task, proposal_id, proposal_revision)
    adapter._save_result(task, result)
    draft = result.handoffs[0]
    handoff = HandoffSnapshot(
        contract_version="teacher_workspace_handoff.v1",
        handoff_id=f"handoff-{adoption_id}",
        work_item_id=draft.work_item_id,
        module="teaching_prep",
        intent=draft.intent,  # type: ignore[arg-type]
        handling_mode=draft.handling_mode,  # type: ignore[arg-type]
        destination_key=draft.destination_key,
        subject_refs=draft.subject_refs,
        draft_ref=draft.draft_ref,
        adoption_state="adoption_started",
        prefill_keys=(),
        missing_fields=(),
        source_task_id=task.task_id,
        source_turn_id=None,
        return_destination_key=draft.return_destination_key,
        return_focus_ref=draft.return_focus_ref,
        expires_on_source_change=True,
        adoption_id=adoption_id,
        target_revision=target_revision,
        revision=2,
    )
    return result, handoff


def test_slide_adapter_persists_metadata_for_local_recovery(tmp_path: Path) -> None:
    database_path = tmp_path / "teaching_prep.db"
    migration = Path("migrations/teaching_prep/015_workspace_ai_adapter.sql").read_text("utf-8")
    with sqlite3.connect(database_path) as connection:
        connection.executescript(migration)
        connection.execute(
            "CREATE TABLE lesson_nodes (id TEXT PRIMARY KEY, revision INTEGER, is_active INTEGER)"
        )
        connection.execute(
            "INSERT INTO lesson_nodes VALUES (?, 3, 1)", ("l" * 32,)
        )
        connection.execute(
            "CREATE TABLE slide_plan_versions (id TEXT PRIMARY KEY, request_token TEXT, version_number INTEGER)"
        )
        connection.execute(
            "CREATE TABLE lesson_draft_versions (id TEXT PRIMARY KEY, version_number INTEGER, resource_pack_id TEXT)"
        )
        connection.execute(
            "INSERT INTO lesson_draft_versions VALUES ('draft-r7', 3, 'pack-r7')"
        )

    class FakeService:
        def __init__(self) -> None:
            self.database_path = database_path
            self.calls = 0
            self.resource_packs = SimpleNamespace(
                source_status=lambda _pack_id: {"sources_changed": False}
            )

        def create_slide_plan(
            self,
            draft_id: str,
            *,
            request_token: str,
            model_proposal: bool,
            task_model_gateway: object,
        ):
            self.calls += 1
            assert (draft_id, request_token) == ("draft-r7", "operation-r7-slide")
            assert model_proposal is True
            assert isinstance(task_model_gateway, WorkspaceAITaskModelGateway)
            with sqlite3.connect(database_path) as connection:
                connection.execute(
                    "INSERT OR IGNORE INTO slide_plan_versions VALUES ('plan-r7', ?, 4)",
                    (request_token,),
                )
            return SimpleNamespace(id="plan-r7", version_number=4), True

    service = FakeService()
    adapter = TeachingPrepAITaskAdapter(service)  # type: ignore[arg-type]
    result = adapter.execute(_task(), model_gateway=WorkspaceAITaskModelGateway())
    recovered = adapter.recover(_task())

    assert result == recovered
    assert service.calls == 1


def _real_slide_adoption_setup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _paths, service = _migrated_service(tmp_path, monkeypatch)
    pack, draft, _reference_link = _confirmed_draft(service, tmp_path)
    with service.database.connect() as connection:
        lesson_revision = str(connection.execute(
            "SELECT revision FROM lesson_nodes WHERE id = ?",
            (pack.lesson_node_id,),
        ).fetchone()[0])
    task = replace(
        _task(),
        task_id="task-r7-domain-slide",
        operation_id="operation-r7-domain-slide",
        source_ref=OpaqueRef("lesson", pack.lesson_node_id, lesson_revision),
        context_refs=(
            OpaqueRef("lesson_draft", draft.id, str(draft.version_number)),
        ),
    )
    adapter = TeachingPrepAITaskAdapter(service)
    result = adapter.execute(task, model_gateway=WorkspaceAITaskModelGateway())
    draft_handoff = result.handoffs[0]
    handoff = HandoffSnapshot(
        contract_version="teacher_workspace_handoff.v1",
        handoff_id="handoff-r7-domain-slide",
        work_item_id=draft_handoff.work_item_id,
        module="teaching_prep",
        intent=draft_handoff.intent,  # type: ignore[arg-type]
        handling_mode=draft_handoff.handling_mode,  # type: ignore[arg-type]
        destination_key=draft_handoff.destination_key,
        subject_refs=draft_handoff.subject_refs,
        draft_ref=draft_handoff.draft_ref,
        adoption_state="adoption_started",
        prefill_keys=(),
        missing_fields=(),
        source_task_id=task.task_id,
        source_turn_id=None,
        return_destination_key=draft_handoff.return_destination_key,
        return_focus_ref=draft_handoff.return_focus_ref,
        expires_on_source_change=True,
        adoption_id="adoption-r7-domain-slide",
        target_revision=lesson_revision,
        revision=2,
    )
    return service, adapter, result, handoff, lesson_revision


def _slide_review_command(service, proposal_id: str) -> dict[str, object]:
    plan = service.get_slide_plan(proposal_id)
    return {
        "kind": "review_slide_plan",
        "operation_reviews": [
            {
                "operation_id": item["operation_id"],
                "decision": "rejected",
                "reason": item["reason"],
                "planned_minutes": item["planned_minutes"],
                "teacher_note": "教师已逐项核对",
            }
            for item in plan.payload["operations"]
        ],
        "approve_low_risk_deletions": False,
        "review_note": "教师已在课件工作面审核",
    }


def test_domain_adoption_is_revision_safe_and_concurrent_replay_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, adapter, result, handoff, target_revision = (
        _real_slide_adoption_setup(tmp_path, monkeypatch)
    )
    draft_revision = result.proposal_revision
    command = _slide_review_command(service, result.proposal_ref_id)
    with pytest.raises(RevisionConflictError):
        with bind_adoption_command(command):
            adapter.adopt(
                replace(handoff, handoff_id="handoff-wrong-draft"),
                adoption_id="adoption-wrong-draft",
                draft_revision=str(int(draft_revision) + 1),
                target_revision=target_revision,
            )
    with pytest.raises(RevisionConflictError):
        with bind_adoption_command(command):
            adapter.adopt(
                replace(handoff, handoff_id="handoff-wrong-target"),
                adoption_id="adoption-wrong-target",
                draft_revision=draft_revision,
                target_revision=str(int(target_revision) + 1),
            )

    barrier = Barrier(2)

    def adopt_once(_index: int) -> AdoptionResult:
        barrier.wait(timeout=5)
        with bind_adoption_command(command):
            return adapter.adopt(
                handoff,
                adoption_id="adoption-r7-domain-slide",
                draft_revision=draft_revision,
                target_revision=target_revision,
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        adopted = list(executor.map(adopt_once, range(2)))

    assert adopted[0] == adopted[1]
    assert adopted[0].object_ref.startswith("teaching_prep:slide_plan:")
    assert adapter.find_adoption("adoption-r7-domain-slide") == adopted[0]
    domain = service.find_workspace_ai_adoption("adoption-r7-domain-slide")
    assert domain is not None
    assert domain.object_id != result.proposal_ref_id
    assert domain.object_status == "approved"
    with service.database.connect() as connection:
        marker = connection.execute(
            "SELECT workspace_adoption_id FROM slide_plan_versions WHERE id = ?",
            (domain.object_id,),
        ).fetchone()[0]
        receipt_count = connection.execute(
            "SELECT COUNT(*) FROM teaching_prep_ai_adoption_receipts",
        ).fetchone()[0]
        shadow_count = connection.execute(
            "SELECT COUNT(*) FROM teaching_prep_ai_adopted_items",
        ).fetchone()[0]
    assert marker == "adoption-r7-domain-slide"
    assert receipt_count == 1
    assert shadow_count == 0


def test_receipt_failure_rolls_back_domain_object_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, adapter, result, handoff, target_revision = (
        _real_slide_adoption_setup(tmp_path, monkeypatch)
    )
    with service.database.connect(immediate=True) as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_r7_adoption_receipt
            BEFORE INSERT ON teaching_prep_ai_adoption_receipts
            BEGIN
                SELECT RAISE(ABORT, 'synthetic receipt failure');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="synthetic receipt failure"):
        with bind_adoption_command(
            _slide_review_command(service, result.proposal_ref_id)
        ):
            adapter.adopt(
                handoff,
                adoption_id="adoption-r7-rollback",
                draft_revision=result.proposal_revision,
                target_revision=target_revision,
            )

    with service.database.connect() as connection:
        reviewed = connection.execute(
            """
            SELECT id, workspace_adoption_id FROM slide_plan_versions
            WHERE based_on_plan_id = ? AND request_token = ?
            """,
            (result.proposal_ref_id, "ai-adopt-adoption-r7-rollback"),
        ).fetchall()
        receipt_count = connection.execute(
            "SELECT COUNT(*) FROM teaching_prep_ai_adoption_receipts",
        ).fetchone()[0]
    assert len(reviewed) == 1
    assert reviewed[0]["workspace_adoption_id"] is None
    assert receipt_count == 0


def test_public_target_conflict_reopens_handoff_and_latest_retry_converges(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service = _migrated_service(tmp_path, monkeypatch)
    pack, draft, _reference_link = _confirmed_draft(service, tmp_path)
    with service.database.connect() as connection:
        lesson = connection.execute(
            "SELECT revision FROM lesson_nodes WHERE id = ?",
            (pack.lesson_node_id,),
        ).fetchone()
    assert lesson is not None
    latest_target_revision = str(lesson["revision"])
    stale_target_revision = str(int(latest_target_revision) + 1)

    adapter = TeachingPrepAITaskAdapter(service)
    job_store = JobStore(tmp_path / "workspace_ai.db")
    manager = JobManager(job_store, max_workers=2, cleanup_interrupted=False)
    coordinator = WorkspaceAITaskService(
        store=WorkspaceAITaskStore(job_store.db_path),
        manager=manager,
        adapters=(("teaching_prep.slide_change_proposal", adapter),),
    )
    try:
        prepared = coordinator.prepare(
            "operation-r7-target-retry",
            PrepareRequest(
                module="teaching_prep",
                task_kind="teaching_prep.slide_change_proposal",
                source_ref=OpaqueRef(
                    "lesson",
                    pack.lesson_node_id,
                    latest_target_revision,
                ),
                context_refs=(
                    OpaqueRef("lesson_draft", draft.id, str(draft.version_number)),
                ),
                prompt_contract_version="slide-proposal-v1",
                model_destination_fingerprint="b" * 64,
                return_target="teaching_prep.lesson.slides",
            ),
        )
        stored = coordinator.store.require_task(prepared.task_id)
        result = adapter.execute(
            stored,
            model_gateway=WorkspaceAITaskModelGateway(),
        )
        coordinator.store.complete(
            stored.task_id,
            proposal_ref_id=result.proposal_ref_id,
            proposal_revision=result.proposal_revision,
            handoffs=result.handoffs,
            needs_input=result.needs_input,
        )
        handoff = coordinator.get(task_id=stored.task_id).handoffs[0]
        command = _slide_review_command(service, result.proposal_ref_id)

        client = _api_client(service)
        client.app.state.workspace_ai_task_service = coordinator
        client.app.include_router(workspace_ai_router)

        source_status = (
            service.workspace_ai_adoptions._resource_source_status
        )

        def missing_source(_pack_id: str) -> dict[str, object]:
            raise TeachingPrepNotFoundError("resource pack is unavailable")

        service.workspace_ai_adoptions._resource_source_status = missing_source
        invalid_context = client.post(
            f"/api/teaching-prep/ai-handoffs/{handoff.handoff_id}/adopt",
            json={
                "draft_revision": result.proposal_revision,
                "target_revision": latest_target_revision,
                "command": command,
            },
        )
        service.workspace_ai_adoptions._resource_source_status = source_status

        assert invalid_context.status_code == 409
        assert (
            invalid_context.json()["error"]["code"]
            == "workspace_ai_revision_conflict"
        )
        assert coordinator.get(
            task_id=stored.task_id
        ).handoffs[0].adoption_state == "opened"

        conflicted = client.post(
            f"/api/teaching-prep/ai-handoffs/{handoff.handoff_id}/adopt",
            json={
                "draft_revision": result.proposal_revision,
                "target_revision": stale_target_revision,
                "command": command,
            },
        )

        assert conflicted.status_code == 409
        assert conflicted.json()["error"]["code"] == "workspace_ai_revision_conflict"
        refreshed = client.get(
            f"/api/workspace-ai-tasks/{stored.task_id}",
        ).json()
        refreshed_handoff = refreshed["handoffs"][0]
        assert refreshed["status"] == "proposal_ready"
        assert refreshed["pending_count"] == 1
        assert refreshed_handoff["adoption_state"] == "opened"
        assert refreshed_handoff["target_revision"] is None
        with service.database.connect() as connection:
            marker = connection.execute(
                """
                SELECT workspace_adoption_id
                FROM slide_plan_versions
                WHERE id = ?
                """,
                (result.proposal_ref_id,),
            ).fetchone()[0]
            receipt_count = connection.execute(
                "SELECT COUNT(*) FROM teaching_prep_ai_adoption_receipts",
            ).fetchone()[0]
        assert marker is None
        assert receipt_count == 0

        barrier = Barrier(2)

        def adopt_latest(_index: int):
            thread_client = _api_client(service)
            thread_client.app.state.workspace_ai_task_service = coordinator
            barrier.wait(timeout=5)
            return thread_client.post(
                f"/api/teaching-prep/ai-handoffs/{handoff.handoff_id}/adopt",
                json={
                    "draft_revision": result.proposal_revision,
                    "target_revision": latest_target_revision,
                    "command": command,
                },
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            adopted = list(executor.map(adopt_latest, range(2)))

        assert [response.status_code for response in adopted] == [200, 200]
        assert adopted[0].json() == adopted[1].json()
        adoption = adopted[0].json()
        assert adoption["object_id"] != result.proposal_ref_id
        assert adoption["target_revision"] == latest_target_revision
        with service.database.connect() as connection:
            marker = connection.execute(
                """
                SELECT workspace_adoption_id
                FROM slide_plan_versions
                WHERE id = ?
                """,
                (adoption["object_id"],),
            ).fetchone()[0]
            receipt_count = connection.execute(
                "SELECT COUNT(*) FROM teaching_prep_ai_adoption_receipts",
            ).fetchone()[0]
        assert marker == adoption["adoption_id"]
        assert receipt_count == 1
        finished = client.get(
            f"/api/workspace-ai-tasks/{stored.task_id}",
        ).json()
        assert finished["adopted_count"] == 1
        assert finished["pending_count"] == 0
        read = client.get(
            f"/api/teaching-prep/ai-adoptions/{adoption['adoption_id']}",
        )
        assert read.status_code == 200
        assert read.json() == adoption
    finally:
        manager.shutdown()


def test_teaching_prep_adopt_endpoint_delegates_and_returns_domain_object(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, adapter, result, handoff, target_revision = (
        _real_slide_adoption_setup(tmp_path, monkeypatch)
    )

    class Coordinator:
        def __init__(self) -> None:
            self.calls: list[dict[str, str]] = []

        def adopt(self, handoff_id: str, **kwargs) -> AdoptionResult:
            self.calls.append({"handoff_id": handoff_id, **kwargs})
            return adapter.adopt(
                handoff,
                adoption_id="adoption-r7-route",
                draft_revision=str(kwargs["draft_revision"]),
                target_revision=str(kwargs["target_revision"]),
            )

    coordinator = Coordinator()
    client = _api_client(service)
    client.app.state.workspace_ai_task_service = coordinator
    response = client.post(
        f"/api/teaching-prep/ai-handoffs/{handoff.handoff_id}/adopt",
        json={
            "draft_revision": result.proposal_revision,
            "target_revision": target_revision,
            "command": _slide_review_command(service, result.proposal_ref_id),
        },
    )

    assert response.status_code == 200
    assert response.json()["object_kind"] == "slide_plan"
    assert response.json()["object_id"] != result.proposal_ref_id
    assert response.json()["object_status"] == "approved"
    assert coordinator.calls == [{
        "handoff_id": handoff.handoff_id,
        "module": "teaching_prep",
        "draft_revision": result.proposal_revision,
        "target_revision": target_revision,
    }]
    read = client.get(
        f"/api/teaching-prep/ai-adoptions/{response.json()['adoption_id']}"
    )
    assert read.status_code == 200
    assert read.json() == response.json()


def test_slide_review_adoption_persists_review_and_receipt_before_common_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, adapter, seed_result, seed_handoff, target_revision = (
        _real_slide_adoption_setup(tmp_path, monkeypatch)
    )
    seed_plan = service.get_slide_plan(seed_result.proposal_ref_id)
    source_draft = service.get_lesson_draft(seed_plan.lesson_draft_id)
    job_store = JobStore(tmp_path / "workspace_ai_review.db")
    manager = JobManager(job_store, max_workers=1, cleanup_interrupted=False)
    coordinator = WorkspaceAITaskService(
        store=WorkspaceAITaskStore(job_store.db_path),
        manager=manager,
        adapters=(("teaching_prep.slide_change_proposal", adapter),),
    )
    try:
        prepared = coordinator.prepare(
            "operation-r7-slide-review",
            PrepareRequest(
                module="teaching_prep",
                task_kind="teaching_prep.slide_change_proposal",
                source_ref=OpaqueRef(
                    "lesson",
                    seed_handoff.subject_refs[0].id,
                    target_revision,
                ),
                context_refs=(
                    OpaqueRef(
                        "lesson_draft",
                        source_draft.id,
                        str(source_draft.version_number),
                    ),
                ),
                prompt_contract_version="slide-proposal-v1",
                model_destination_fingerprint="b" * 64,
                return_target="teaching_prep.lesson.slides",
            ),
        )
        stored = coordinator.store.require_task(prepared.task_id)
        result = adapter.execute(
            stored,
            model_gateway=WorkspaceAITaskModelGateway(),
        )
        coordinator.store.complete(
            stored.task_id,
            proposal_ref_id=result.proposal_ref_id,
            proposal_revision=result.proposal_revision,
            handoffs=result.handoffs,
            needs_input=result.needs_input,
        )
        handoff = coordinator.get(task_id=stored.task_id).handoffs[0]
        command = _slide_review_command(service, result.proposal_ref_id)
        plan = service.get_slide_plan(result.proposal_ref_id)
        reviews = [
            {
                "operation_id": item["operation_id"],
                "decision": "rejected",
                "reason": item["reason"],
                "planned_minutes": item["planned_minutes"],
                "teacher_note": "教师已逐项核对",
            }
            for item in plan.payload["operations"]
        ]
        client = _api_client(service)
        client.app.state.workspace_ai_task_service = coordinator
        client.app.include_router(workspace_ai_router)

        adopted = client.post(
            f"/api/teaching-prep/ai-handoffs/{handoff.handoff_id}/adopt",
            json={
                "draft_revision": result.proposal_revision,
                "target_revision": target_revision,
                "command": {
                    "kind": "review_slide_plan",
                    "operation_reviews": reviews,
                    "approve_low_risk_deletions": False,
                    "review_note": "教师已在课件工作面审核",
                },
            },
        )

        assert adopted.status_code == 200
        body = adopted.json()
        assert body["object_status"] == "approved"
        assert body["object_id"] != result.proposal_ref_id
        reviewed = service.get_slide_plan(body["object_id"])
        assert reviewed.based_on_plan_id == result.proposal_ref_id
        assert all(
            item["decision"] == "rejected"
            for item in reviewed.payload["operations"]
        )
        task = coordinator.get(task_id=stored.task_id)
        assert task.handoffs[0].adoption_state == "adopted"
        assert task.handoffs[0].adoption_id == body["adoption_id"]
    finally:
        manager.shutdown()


def test_staged_slide_review_recovers_after_formal_action_before_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, adapter, result, handoff, target_revision = (
        _real_slide_adoption_setup(tmp_path, monkeypatch)
    )
    command = _slide_review_command(service, result.proposal_ref_id)
    with service.database.connect(immediate=True) as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_staged_review_receipt
            BEFORE INSERT ON teaching_prep_ai_adoption_receipts
            BEGIN
                SELECT RAISE(ABORT, 'synthetic staged receipt failure');
            END
            """
        )

    with pytest.raises(
        sqlite3.IntegrityError,
        match="synthetic staged receipt failure",
    ):
        with bind_adoption_command(command):
            adapter.adopt(
                handoff,
                adoption_id="adoption-r7-staged-recovery",
                draft_revision=result.proposal_revision,
                target_revision=target_revision,
            )

    with service.database.connect() as connection:
        reviewed_before = connection.execute(
            """
            SELECT id FROM slide_plan_versions
            WHERE based_on_plan_id = ?
              AND request_token = ?
            """,
            (result.proposal_ref_id, "ai-adopt-adoption-r7-staged-recovery"),
        ).fetchall()
        receipt_before = connection.execute(
            "SELECT COUNT(*) FROM teaching_prep_ai_adoption_receipts"
        ).fetchone()[0]
    assert len(reviewed_before) == 1
    assert receipt_before == 0

    with service.database.connect(immediate=True) as connection:
        connection.execute("DROP TRIGGER reject_staged_review_receipt")

    # Simulate a new process/request context: no page command is supplied.
    recovered = TeachingPrepAITaskAdapter(service).adopt(
        handoff,
        adoption_id="adoption-r7-staged-recovery",
        draft_revision=result.proposal_revision,
        target_revision=target_revision,
    )

    with service.database.connect() as connection:
        reviewed_after = connection.execute(
            """
            SELECT id FROM slide_plan_versions
            WHERE based_on_plan_id = ?
              AND request_token = ?
            """,
            (result.proposal_ref_id, "ai-adopt-adoption-r7-staged-recovery"),
        ).fetchall()
        receipt_after = connection.execute(
            "SELECT COUNT(*) FROM teaching_prep_ai_adoption_receipts"
        ).fetchone()[0]
    assert len(reviewed_after) == 1
    assert recovered.object_ref.endswith(str(reviewed_after[0]["id"]))
    assert receipt_after == 1


def test_rejected_slide_command_can_be_corrected_on_same_handoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, adapter, result, handoff, target_revision = (
        _real_slide_adoption_setup(tmp_path, monkeypatch)
    )
    with pytest.raises(RevisionConflictError):
        with bind_adoption_command({
            "kind": "review_slide_plan",
            "operation_reviews": [],
            "approve_low_risk_deletions": False,
            "review_note": "无有效决定",
        }):
            adapter.adopt(
                handoff,
                adoption_id="adoption-r7-corrected-command",
                draft_revision=result.proposal_revision,
                target_revision=target_revision,
            )

    with bind_adoption_command(
        _slide_review_command(service, result.proposal_ref_id)
    ):
        adopted = adapter.adopt(
            handoff,
            adoption_id="adoption-r7-corrected-command",
            draft_revision=result.proposal_revision,
            target_revision=target_revision,
        )

    assert adopted.object_ref.startswith("teaching_prep:slide_plan:")
    assert service.find_workspace_ai_adoption(
        "adoption-r7-corrected-command"
    ) is not None


def test_mapping_adoption_applies_reviewed_proposal_before_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service = _migrated_service(tmp_path, monkeypatch)
    semester, lesson_ids = _semester(service)
    version, _created = service.register_material_file(
        request_token="r7-mapping-file",
        path=_pdf(tmp_path / "r7-mapping.pdf", ["L1", "L2"]),
        display_name="R7 合成教辅",
    )
    record, _created = service.attach_semester_material(
        semester.id,
        request_token="r7-mapping-attach",
        material_version_id=version.id,
        material_role="exercise_workbook",
    )
    service.parse_material_version(version.id)
    _snapshot, target_revision = service.semester_mapping.snapshot(
        semester.id,
        [record.id],
    )
    service.semester_mapping_model_adapter = _FakeSemesterMappingModel({
        "tree": [],
        "mappings": [{
            "material_record_id": record.id,
            "lesson_ref": lesson_ids[0],
            "start_unit": 1,
            "end_unit": 2,
        }],
        "uncertainties": [],
    })
    proposal, _created = service.generate_semester_mapping_proposal(
        semester.id,
        operation_id="r7-mapping-proposal",
        material_record_ids=[record.id],
        expected_source_state_sha256=target_revision,
    )
    task = replace(
        _task(),
        task_id="task-r7-mapping",
        operation_id="r7-mapping-proposal",
        task_kind="teaching_prep.semester_mapping",
        source_ref=OpaqueRef("semester", semester.id, target_revision),
        context_refs=(OpaqueRef("material", record.id, str(record.revision)),),
        return_target="teaching_prep.library",
    )
    adapter = TeachingPrepAITaskAdapter(service)
    result, handoff = _persist_proposal_handoff(
        adapter,
        task,
        proposal_id=proposal.id,
        proposal_revision=str(proposal.revision),
        target_revision=target_revision,
        adoption_id="adoption-r7-mapping",
    )
    reviewed = _accept_all_mappings(service, proposal)

    with bind_adoption_command({
        "kind": "apply_semester_mapping",
        "proposal_revision": reviewed.revision,
    }):
        adopted = adapter.adopt(
            handoff,
            adoption_id="adoption-r7-mapping",
            draft_revision=result.proposal_revision,
            target_revision=target_revision,
        )

    assert service.list_semester_mapping_proposals(semester.id)[0].status == "applied"
    assert adopted.object_ref.endswith(reviewed.id)
    assert service.list_semester_materials(semester.id)[0].mapping_status == "confirmed"


def test_lesson_adoption_creates_one_confirmed_teacher_version_on_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service = _migrated_service(tmp_path, monkeypatch)
    pack, confirmed, _reference_link = _confirmed_draft(service, tmp_path)
    proposal, _created = service.revise_lesson_draft(
        confirmed.id,
        request_token="r7-lesson-ai-proposal",
        payload=deepcopy(confirmed.payload),
        confirmed=False,
    )
    with service.database.connect() as connection:
        target_revision = str(connection.execute(
            "SELECT revision FROM lesson_nodes WHERE id = ?",
            (pack.lesson_node_id,),
        ).fetchone()[0])
    task = replace(
        _task(),
        task_id="task-r7-lesson",
        operation_id="r7-lesson-ai-proposal",
        task_kind="teaching_prep.lesson_plan",
        source_ref=OpaqueRef("lesson", pack.lesson_node_id, target_revision),
        context_refs=(OpaqueRef("resource_pack", pack.id, pack.pack_sha256),),
        return_target="teaching_prep.lesson.plan",
    )
    adapter = TeachingPrepAITaskAdapter(service)
    result, handoff = _persist_proposal_handoff(
        adapter,
        task,
        proposal_id=proposal.id,
        proposal_revision=str(proposal.version_number),
        target_revision=target_revision,
        adoption_id="adoption-r7-lesson",
    )
    command = {"kind": "confirm_lesson_draft", "payload": proposal.payload}

    with bind_adoption_command(command):
        first = adapter.adopt(
            handoff,
            adoption_id="adoption-r7-lesson",
            draft_revision=result.proposal_revision,
            target_revision=target_revision,
        )
    with bind_adoption_command(command):
        replay = adapter.adopt(
            handoff,
            adoption_id="adoption-r7-lesson",
            draft_revision=result.proposal_revision,
            target_revision=target_revision,
        )

    assert replay == first
    formal_id = first.object_ref.rsplit(":", 1)[-1]
    formal = service.get_lesson_draft(formal_id)
    assert formal.status == "confirmed"
    assert formal.source_kind == "teacher"
    assert formal.based_on_draft_id == proposal.id
    with service.database.connect() as connection:
        versions = connection.execute(
            "SELECT id FROM lesson_draft_versions WHERE request_token = ?",
            ("ai-adopt-adoption-r7-lesson",),
        ).fetchall()
    assert [str(item["id"]) for item in versions] == [formal_id]


def test_exercise_adoption_waits_for_every_teacher_decision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service = _migrated_service(tmp_path, monkeypatch)
    lesson_id, _reference_link, _candidate = _freeze_ready_setup(
        service,
        tmp_path,
    )
    preflight = service.reference_selection_preflight(lesson_id)
    selected = preflight["catalog"]["material_links"][0]
    draft = service.save_reference_selection_draft(
        lesson_id,
        expected_revision=None,
        source_state_sha256=preflight["source_state_sha256"],
        selection=_selection(preflight, selected),
    )
    snapshot, _created = service.freeze_reference_selection_snapshot(
        lesson_id,
        request_token="r7-exercise-snapshot",
        expected_draft_revision=draft.revision,
    )
    service.exercise_suggestion_model_adapter = FakeExerciseSuggestionModelAdapter({
        "suggestions": [_suggestion(selected)],
    })
    run, _created = service.start_exercise_suggestion_run(
        snapshot.id,
        operation_id="r7-exercise-run",
        confirmed=True,
    )
    service.process_exercise_suggestion_run(run.id)
    run, suggestions = service.get_exercise_suggestion_run(run.id)
    with service.database.connect() as connection:
        target_revision = str(connection.execute(
            "SELECT revision FROM lesson_nodes WHERE id = ?",
            (lesson_id,),
        ).fetchone()[0])
    task = replace(
        _task(),
        task_id="task-r7-exercise",
        operation_id="r7-exercise-run",
        task_kind="teaching_prep.exercise_suggestions",
        source_ref=OpaqueRef("lesson", lesson_id, target_revision),
        context_refs=(OpaqueRef(
            "reference_snapshot",
            snapshot.id,
            snapshot.source_state_sha256,
        ),),
        return_target="teaching_prep.lesson.exercises",
    )
    adapter = TeachingPrepAITaskAdapter(service)
    result, handoff = _persist_proposal_handoff(
        adapter,
        task,
        proposal_id=run.id,
        proposal_revision="1",
        target_revision=target_revision,
        adoption_id="adoption-r7-exercise",
    )
    command = {"kind": "finalize_exercise_suggestions"}

    with pytest.raises(RevisionConflictError):
        with bind_adoption_command(command):
            adapter.adopt(
                handoff,
                adoption_id="adoption-r7-exercise",
                draft_revision=result.proposal_revision,
                target_revision=target_revision,
            )
    assert service.find_workspace_ai_adoption("adoption-r7-exercise") is None

    service.review_exercise_suggestion(
        suggestions[0].id,
        expected_revision=suggestions[0].revision,
        decision="accepted",
        teacher_payload=None,
        rejection_reason=None,
    )
    with bind_adoption_command(command):
        adopted = adapter.adopt(
            handoff,
            adoption_id="adoption-r7-exercise",
            draft_revision=result.proposal_revision,
            target_revision=target_revision,
        )

    assert adopted.object_ref.endswith(run.id)
    assert service.find_workspace_ai_adoption("adoption-r7-exercise") is not None


@pytest.mark.parametrize(
    ("kind", "operation_id", "context", "proposal_id", "revision"),
    [
        ("teaching_prep.semester_mapping", "op-semester", (), "semester-proposal", "2"),
        ("teaching_prep.lesson_plan", "op-lesson", (OpaqueRef("resource_pack", "pack-r7", "a" * 64),), "lesson-proposal", "3"),
        ("teaching_prep.exercise_suggestions", "op-exercise", (OpaqueRef("reference_snapshot", "snapshot-r7", "b" * 64),), "exercise-proposal", "1"),
        ("teaching_prep.slide_change_proposal", "op-slide", (OpaqueRef("lesson_draft", "draft-r7", "3"),), "slide-proposal", "4"),
    ],
)
def test_recover_rebuilds_each_handoff_from_domain_proposal_without_execute(
    tmp_path: Path,
    kind: str,
    operation_id: str,
    context: tuple[OpaqueRef, ...],
    proposal_id: str,
    revision: str,
) -> None:
    database_path = tmp_path / "teaching_prep.db"
    migration = Path("migrations/teaching_prep/015_workspace_ai_adapter.sql").read_text("utf-8")
    with sqlite3.connect(database_path) as connection:
        connection.executescript(migration)
        connection.executescript(
            """
            CREATE TABLE semester_mapping_proposals (id TEXT, operation_id TEXT, revision INTEGER);
            CREATE TABLE lesson_draft_versions (id TEXT, operation_id TEXT, version_number INTEGER);
            CREATE TABLE exercise_suggestion_runs (id TEXT, operation_id TEXT, status TEXT);
            CREATE TABLE slide_plan_versions (id TEXT, request_token TEXT, version_number INTEGER);
            """
        )
        connection.execute(
            "INSERT INTO semester_mapping_proposals VALUES ('semester-proposal', 'op-semester', 2)"
        )
        connection.execute(
            "INSERT INTO lesson_draft_versions VALUES ('lesson-proposal', 'op-lesson', 3)"
        )
        connection.execute(
            "INSERT INTO exercise_suggestion_runs VALUES ('exercise-proposal', 'op-exercise', 'succeeded')"
        )
        connection.execute(
            "INSERT INTO slide_plan_versions VALUES ('slide-proposal', 'op-slide', 4)"
        )

    service = SimpleNamespace(database_path=database_path)
    adapter = TeachingPrepAITaskAdapter(service)  # type: ignore[arg-type]
    task = replace(
        _task(),
        task_id=f"task-{operation_id}",
        operation_id=operation_id,
        task_kind=kind,
        context_refs=context,
        return_target={
            "teaching_prep.semester_mapping": "teaching_prep.library",
            "teaching_prep.lesson_plan": "teaching_prep.lesson.plan",
            "teaching_prep.exercise_suggestions": "teaching_prep.lesson.exercises",
            "teaching_prep.slide_change_proposal": "teaching_prep.lesson.slides",
        }[kind],
    )

    recovered = adapter.recover(task)

    assert recovered is not None
    assert (recovered.proposal_ref_id, recovered.proposal_revision) == (proposal_id, revision)
    assert adapter.recover(task) == recovered


def test_all_four_task_kinds_use_the_shared_gateway_and_create_domain_handoffs(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "teaching_prep.db"
    migration = Path("migrations/teaching_prep/015_workspace_ai_adapter.sql").read_text("utf-8")
    with sqlite3.connect(database_path) as connection:
        connection.executescript(migration)

    gateway = WorkspaceAITaskModelGateway()

    class FakeService:
        def __init__(self) -> None:
            self.database_path = database_path
            self.gateways: list[object] = []

        def generate_semester_mapping_proposal(self, *_args, **kwargs):
            self.gateways.append(kwargs["task_model_gateway"])
            return SimpleNamespace(id="semester-result", revision=2), True

        def generate_lesson_draft(self, *_args, **kwargs):
            self.gateways.append(kwargs["task_model_gateway"])
            return SimpleNamespace(id="lesson-result", version_number=3), True

        def start_exercise_suggestion_run(self, *_args, **_kwargs):
            return SimpleNamespace(id="exercise-result")

        def process_exercise_suggestion_run(self, *_args, **kwargs):
            self.gateways.append(kwargs["task_model_gateway"])

        def get_exercise_suggestion_run(self, *_args):
            return SimpleNamespace(id="exercise-result", status="succeeded"), ()

        def create_slide_plan(self, *_args, **_kwargs):
            return SimpleNamespace(id="slide-result", version_number=4), True

    service = FakeService()
    adapter = TeachingPrepAITaskAdapter(service)  # type: ignore[arg-type]
    tasks = [
        replace(
            _task(), task_id="task-semester", operation_id="operation-semester",
            task_kind="teaching_prep.semester_mapping",
            source_ref=OpaqueRef("semester", "s" * 32, "a" * 64),
            context_refs=(OpaqueRef("material", "m" * 32, "1"),),
            return_target="teaching_prep.library",
        ),
        replace(
            _task(), task_id="task-lesson", operation_id="operation-lesson",
            task_kind="teaching_prep.lesson_plan",
            context_refs=(OpaqueRef("resource_pack", "p" * 32, "b" * 64),),
            return_target="teaching_prep.lesson.plan",
        ),
        replace(
            _task(), task_id="task-exercise", operation_id="operation-exercise",
            task_kind="teaching_prep.exercise_suggestions",
            context_refs=(OpaqueRef("reference_snapshot", "r" * 32, "c" * 64),),
            return_target="teaching_prep.lesson.exercises",
        ),
        replace(
            _task(), task_id="task-slide", operation_id="operation-slide",
            task_kind="teaching_prep.slide_change_proposal",
            context_refs=(OpaqueRef("lesson_draft", "d" * 32, "3"),),
            return_target="teaching_prep.lesson.slides",
        ),
    ]

    results = [adapter.execute(task, model_gateway=gateway) for task in tasks]

    assert [result.proposal_ref_id for result in results] == [
        "semester-result", "lesson-result", "exercise-result", "slide-result",
    ]
    assert service.gateways == [gateway, gateway, gateway]
    assert [result.handoffs[0].destination_key for result in results] == [
        "teaching_prep.library", "teaching_prep.lesson.plan",
        "teaching_prep.lesson.exercises", "teaching_prep.lesson.slides",
    ]


def test_semester_mapping_retryable_failure_stays_safe_to_retry(tmp_path: Path) -> None:
    class RetryableService:
        database_path = tmp_path / "teaching_prep.db"

        def generate_semester_mapping_proposal(self, *_args, **_kwargs):
            raise TeachingPrepRetryAvailableError("model response could not be validated")

    task = replace(
        _task(),
        task_kind="teaching_prep.semester_mapping",
        source_ref=OpaqueRef("semester", "s" * 32, "a" * 64),
        context_refs=(OpaqueRef("material", "m" * 32, "1"),),
        return_target="teaching_prep.library",
    )
    adapter = TeachingPrepAITaskAdapter(RetryableService())  # type: ignore[arg-type]

    with pytest.raises(KnownAdapterFailure) as exc_info:
        adapter.execute(task, model_gateway=WorkspaceAITaskModelGateway())

    assert exc_info.value.code == "semester_mapping_retry_available"
