from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.class_teacher.intake.ports import SharedWorkspaceAITaskPort
from backend.class_teacher.vault_service import VaultService
from backend.jobs.manager import JobManager
from backend.jobs.store import JobStore
from backend.workspaces.ai_tasks.job_adapter import register_workspace_ai_job
from backend.workspaces.ai_tasks.service import WorkspaceAITaskService
from backend.workspaces.ai_tasks.store import WorkspaceAITaskStore
from backend.workspaces.contracts import WorkspaceContext


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class SyntheticConfiguredModel:
    def __init__(self, result: dict[str, object] | Exception) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    def destination_snapshot(self) -> dict[str, object]:
        return {"destination_fingerprint": "a" * 64}

    def invoke_workspace_task(self, **kwargs) -> str:
        self.calls.append(dict(kwargs))
        if isinstance(self.result, Exception):
            raise self.result
        return json.dumps(self.result, ensure_ascii=False)


def _triage() -> dict[str, object]:
    return {
        "contract_version": "class_teacher_triage.v1",
        "assistant_message": "已整理为一项合成登记草稿。",
        "clarification_questions": [],
        "work_items": [{
            "work_item_id": "shared-work-item-001",
            "domain": "activities_culture",
            "primary_mode": "record",
            "secondary_modes": [],
            "intent": "create",
            "reason_summary": "合成公共接线验证",
            "subject_refs": [],
            "time_facts": [],
            "safety_level": "normal",
            "missing_fields": [],
            "draft": {
                "summary": "合成正式登记内容",
                "observed_at": "2026-08-05T08:00:00+08:00",
            },
        }],
    }


def _wired(tmp_path: Path, result: dict[str, object] | Exception):
    roster_db = tmp_path / "roster.db"
    with closing(sqlite3.connect(roster_db)) as connection:
        connection.execute(
            "CREATE TABLE students (id INTEGER PRIMARY KEY, student_code TEXT, name TEXT, class_name TEXT)"
        )
        connection.commit()
    context = WorkspaceContext(
        module_id="class-teacher",
        root=tmp_path / "workspaces" / "class-teacher",
        paths=SimpleNamespace(
            project_root=PROJECT_ROOT,
            migration_project_root=PROJECT_ROOT,
            db_path=roster_db,
        ),
    )
    configured = SyntheticConfiguredModel(result)
    domain = VaultService(context, protection_enabled=False, model_gateway=configured)
    job_store = JobStore(tmp_path / "common.db")
    manager = JobManager(job_store, max_workers=1, cleanup_interrupted=False)
    common = WorkspaceAITaskService(
        store=WorkspaceAITaskStore(job_store.db_path),
        manager=manager,
    )
    register_workspace_ai_job(manager, common)
    port = SharedWorkspaceAITaskPort(
        common,
        model_identity=configured,
        conversations=domain.intake.conversations,
    )
    domain.intake.bind_ai_tasks(port)
    common.register_adapter("class_teacher.intake", domain.intake.ai_task_adapter)
    return domain, common, manager, configured


def test_b_adapter_uses_shared_task_and_adoption_coordinator(tmp_path: Path) -> None:
    marker = "SYNTHETIC-BODY-NOT-IN-COMMON-STORE"
    domain, common, manager, configured = _wired(tmp_path, _triage())
    try:
        conversation = domain.intake.start_conversation()
        queued = domain.intake.append_turn(
            conversation_id=str(conversation["conversation_id"]),
            expected_revision=int(conversation["revision"]),
            message=marker,
            operation_id="shared-intake-operation-001",
        )
        task_id = str(queued["turns"][-1]["task_id"])
        started = common.get(task_id=task_id)
        assert started.job_id is not None
        manager.wait(started.job_id, timeout=5)

        finished = common.get(task_id=task_id)
        restored = domain.intake.get_conversation(str(conversation["conversation_id"]))
        assert finished.status == "proposal_ready"
        assert finished.send_attempt_count == 1
        assert finished.dispatch_evidence == "response_persisted"
        assert finished.handoff_total == 1
        assert restored["state"] == "handoff_ready"
        assert marker in configured.calls[0]["messages"][-1]["content"]
        assert marker.encode() not in common.store.db_path.read_bytes()

        handoff = domain.intake.open_handoff(str(restored["handoffs"][0]["handoff_id"]))
        receipt = domain.intake.adopt_handoff(
            token="",
            handoff_id=str(handoff["handoff_id"]),
            draft_revision=int(handoff["draft_revision"]),
            target_revision="new",
            operation_id="ignored-domain-operation",
        )
        adopted = common.get(task_id=task_id)
        assert adopted.adopted_count == 1
        assert receipt["adoption_id"] == adopted.handoffs[0].adoption_id
        with closing(domain.database.connect()) as connection:
            row = connection.execute(
                "SELECT adoption_id, target_revision FROM handoff_adoption_receipts"
            ).fetchone()
        assert row is not None
        assert row[0] == adopted.handoffs[0].adoption_id
        assert row[1] == "new"
    finally:
        manager.shutdown()


def test_shared_failure_is_synchronized_without_a_second_model_send(tmp_path: Path) -> None:
    domain, common, manager, configured = _wired(tmp_path, RuntimeError("synthetic transport loss"))
    try:
        conversation = domain.intake.start_conversation()
        queued = domain.intake.append_turn(
            conversation_id=str(conversation["conversation_id"]),
            expected_revision=int(conversation["revision"]),
            message="合成结果未知正文",
            operation_id="shared-intake-operation-unknown",
        )
        task_id = str(queued["turns"][-1]["task_id"])
        started = common.get(task_id=task_id)
        assert started.job_id is not None
        manager.wait(started.job_id, timeout=5)

        restored = domain.intake.get_conversation(str(conversation["conversation_id"]))
        failed = common.get(task_id=task_id)
        assert failed.status == "result_unknown"
        assert failed.send_attempt_count == 1
        assert restored["turns"][-1]["task_state"] == "result_unknown"
        assert len(configured.calls) == 1
        replay = common.dispatch(
            failed.operation_id,
            prepared_task_id=failed.task_id,
        )
        assert replay.send_attempt_count == 1
        assert len(configured.calls) == 1
    finally:
        manager.shutdown()


def test_receipt_recovery_converges_domain_and_common_handoffs(tmp_path: Path, monkeypatch) -> None:
    domain, common, manager, _configured = _wired(tmp_path, _triage())
    try:
        conversation = domain.intake.start_conversation()
        queued = domain.intake.append_turn(
            conversation_id=str(conversation["conversation_id"]),
            expected_revision=int(conversation["revision"]),
            message="合成采用恢复内容",
            operation_id="shared-adoption-recovery-task",
        )
        task_id = str(queued["turns"][-1]["task_id"])
        started = common.get(task_id=task_id)
        assert started.job_id is not None
        manager.wait(started.job_id, timeout=5)
        ready = domain.intake.get_conversation(str(conversation["conversation_id"]))
        handoff = domain.intake.open_handoff(str(ready["handoffs"][0]["handoff_id"]))

        original_mark = domain.intake.adoption._mark_adopted
        calls = 0

        def interrupt_once(handoff_id, receipt):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("synthetic crash after domain receipt")
            original_mark(handoff_id, receipt)

        monkeypatch.setattr(domain.intake.adoption, "_mark_adopted", interrupt_once)
        with pytest.raises(RuntimeError, match="synthetic crash"):
            domain.intake.adopt_handoff(
                token="",
                handoff_id=str(handoff["handoff_id"]),
                draft_revision=int(handoff["draft_revision"]),
                target_revision="new",
                operation_id="ignored-first-adoption",
            )
        interrupted = domain.intake.get_conversation(str(conversation["conversation_id"]))
        assert interrupted["handoffs"][0]["adoption_state"] == "adoption_started"

        receipt = domain.intake.adopt_handoff(
            token="",
            handoff_id=str(handoff["handoff_id"]),
            draft_revision=int(handoff["draft_revision"]),
            target_revision="new",
            operation_id="ignored-recovered-adoption",
        )
        recovered = domain.intake.get_conversation(str(conversation["conversation_id"]))
        assert receipt["adoption_id"] == common.get(task_id=task_id).handoffs[0].adoption_id
        assert recovered["handoffs"][0]["adoption_state"] == "adopted"
        assert recovered["state"] == "teacher_confirmed"
    finally:
        manager.shutdown()


def test_successful_draft_revision_supersedes_only_the_old_common_handoff(tmp_path: Path) -> None:
    domain, common, manager, configured = _wired(tmp_path, _triage())
    try:
        conversation = domain.intake.start_conversation()
        queued = domain.intake.append_turn(
            conversation_id=str(conversation["conversation_id"]),
            expected_revision=int(conversation["revision"]),
            message="合成草稿调整正文",
            operation_id="shared-intake-operation-revise",
        )
        first_task_id = str(queued["turns"][-1]["task_id"])
        first_started = common.get(task_id=first_task_id)
        assert first_started.job_id is not None
        manager.wait(first_started.job_id, timeout=5)
        ready = domain.intake.get_conversation(str(conversation["conversation_id"]))
        handoff = domain.intake.open_handoff(str(ready["handoffs"][0]["handoff_id"]))

        configured.result = {
            "contract_version": "class_teacher_draft_revision.v1",
            "content": {
                "summary": "合成调整后的正式登记内容",
                "observed_at": "2026-08-05T09:00:00+08:00",
            },
        }
        revision = domain.intake.request_draft_revision(
            handoff_id=str(handoff["handoff_id"]),
            expected_revision=int(handoff["draft_revision"]),
            instruction="按时间顺序整理合成内容",
            operation_id="shared-draft-revision-operation",
        )
        revised_task_id = str(revision["task_id"])
        revised_started = common.get(task_id=revised_task_id)
        assert revised_started.source_ref.kind == "handoff"
        assert revised_started.source_ref.id == handoff["handoff_id"]
        assert revised_started.job_id is not None
        manager.wait(revised_started.job_id, timeout=5)
        domain.intake.get_draft_revision(str(revision["request_id"]))

        old = common.get(task_id=first_task_id)
        current = common.get(task_id=revised_task_id)
        assert old.handoffs[0].adoption_state == "stale"
        assert current.handoffs[0].adoption_state == "pending"
        revised = domain.intake.open_handoff(str(handoff["handoff_id"]))
        receipt = domain.intake.adopt_handoff(
            token="",
            handoff_id=str(revised["handoff_id"]),
            draft_revision=int(revised["draft_revision"]),
            target_revision="new",
            operation_id="ignored-after-revision",
        )
        assert receipt["adoption_id"] == common.get(task_id=revised_task_id).handoffs[0].adoption_id
    finally:
        manager.shutdown()
