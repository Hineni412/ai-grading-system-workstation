from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace

import pytest

from backend.api.routers.workspace_ai_tasks import router as workspace_ai_router
from backend.jobs.manager import JobManager
from backend.jobs.store import JobStore
from backend.teaching_prep.application.ai_task_adapter import TeachingPrepAITaskAdapter
from backend.workspaces.ai_tasks.model_gateway import WorkspaceAITaskModelGateway
from backend.workspaces.ai_tasks.models import (
    AdoptionResult,
    HandoffSnapshot,
    OpaqueRef,
    PrepareRequest,
    RevisionConflictError,
    StoredTask,
)
from backend.workspaces.ai_tasks.service import WorkspaceAITaskService
from backend.workspaces.ai_tasks.store import WorkspaceAITaskStore

from .test_a01_foundation import _migrated_service
from .test_a02_catalog import _api_client
from .test_a07_slide_plans import _confirmed_draft


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

        def create_slide_plan(self, draft_id: str, *, request_token: str):
            self.calls += 1
            assert (draft_id, request_token) == ("draft-r7", "operation-r7-slide")
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


def test_domain_adoption_is_revision_safe_and_concurrent_replay_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, adapter, result, handoff, target_revision = (
        _real_slide_adoption_setup(tmp_path, monkeypatch)
    )
    draft_revision = result.proposal_revision
    with pytest.raises(RevisionConflictError):
        adapter.adopt(
            replace(handoff, handoff_id="handoff-wrong-draft"),
            adoption_id="adoption-wrong-draft",
            draft_revision=str(int(draft_revision) + 1),
            target_revision=target_revision,
        )
    with pytest.raises(RevisionConflictError):
        adapter.adopt(
            replace(handoff, handoff_id="handoff-wrong-target"),
            adoption_id="adoption-wrong-target",
            draft_revision=draft_revision,
            target_revision=str(int(target_revision) + 1),
        )

    barrier = Barrier(2)

    def adopt_once(_index: int) -> AdoptionResult:
        barrier.wait(timeout=5)
        return adapter.adopt(
            handoff,
            adoption_id="adoption-r7-domain-slide",
            draft_revision=draft_revision,
            target_revision=target_revision,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        adopted = list(executor.map(adopt_once, range(2)))

    assert adopted[0] == adopted[1]
    assert adopted[0].object_ref == (
        f"teaching_prep:slide_plan:{result.proposal_ref_id}"
    )
    assert adapter.find_adoption("adoption-r7-domain-slide") == adopted[0]
    domain = service.find_workspace_ai_adoption("adoption-r7-domain-slide")
    assert domain is not None
    assert domain.object_id == result.proposal_ref_id
    assert domain.object_status == "in_review"
    with service.database.connect() as connection:
        marker = connection.execute(
            "SELECT workspace_adoption_id FROM slide_plan_versions WHERE id = ?",
            (result.proposal_ref_id,),
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
        adapter.adopt(
            handoff,
            adoption_id="adoption-r7-rollback",
            draft_revision=result.proposal_revision,
            target_revision=target_revision,
        )

    with service.database.connect() as connection:
        marker = connection.execute(
            "SELECT workspace_adoption_id FROM slide_plan_versions WHERE id = ?",
            (result.proposal_ref_id,),
        ).fetchone()[0]
        receipt_count = connection.execute(
            "SELECT COUNT(*) FROM teaching_prep_ai_adoption_receipts",
        ).fetchone()[0]
    assert marker is None
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

        client = _api_client(service)
        client.app.state.workspace_ai_task_service = coordinator
        client.app.include_router(workspace_ai_router)
        conflicted = client.post(
            f"/api/teaching-prep/ai-handoffs/{handoff.handoff_id}/adopt",
            json={
                "draft_revision": result.proposal_revision,
                "target_revision": stale_target_revision,
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
                },
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            adopted = list(executor.map(adopt_latest, range(2)))

        assert [response.status_code for response in adopted] == [200, 200]
        assert adopted[0].json() == adopted[1].json()
        adoption = adopted[0].json()
        assert adoption["object_id"] == result.proposal_ref_id
        assert adoption["target_revision"] == latest_target_revision
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
        },
    )

    assert response.status_code == 200
    assert response.json()["object_kind"] == "slide_plan"
    assert response.json()["object_id"] == result.proposal_ref_id
    assert response.json()["object_status"] == "in_review"
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
