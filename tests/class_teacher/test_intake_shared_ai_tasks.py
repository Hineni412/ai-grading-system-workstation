from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.class_teacher.errors import VaultError
from backend.class_teacher.intake.ports import PreparedTask, SharedWorkspaceAITaskPort
from backend.class_teacher.vault_service import VaultService
from backend.jobs.manager import JobManager
from backend.jobs.store import JobStore
from backend.llm.diagnostics import JsonlDiagnosticJournal
from backend.workspaces.ai_tasks.job_adapter import register_workspace_ai_job
from backend.workspaces.ai_tasks.model_gateway import WorkspaceAITaskModelGateway
from backend.workspaces.ai_tasks.models import OpaqueRef, PrepareRequest
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
                "record_kind": "fact",
                "source": "合成教师核对",
            },
        }],
    }


def _simplified_provider_triage() -> dict[str, object]:
    """Shape returned by the configured model during the 2026-08-08 repro."""
    return {
        "contract_version": "class_teacher_triage.v1",
        "domain": "student_support",
        "category": "record",
        "assistant_message": "已形成一份待教师核对的支持记录草稿。",
        "clarification_questions": ["是否需要补充已核验材料？"],
        "work_items": [{
            "subject_refs": ["synthetic-provider-student-reference"],
            "content": "合成学生支持事项摘要，后续信息仍需教师核对。",
        }],
    }


def _structured_provider_triage_with_text_time_fact() -> dict[str, object]:
    """Structured shape returned by the configured model on acceptance."""
    result = _triage()
    result["work_items"][0]["time_facts"] = [
        "合成暑假期间计划复查",
    ]
    return result


def _wired(tmp_path: Path, result: dict[str, object] | Exception):
    roster_db = tmp_path / "roster.db"
    with closing(sqlite3.connect(roster_db)) as connection:
        connection.execute(
            "CREATE TABLE students (id INTEGER PRIMARY KEY, student_code TEXT, name TEXT, class_name TEXT)"
        )
        connection.execute(
            "INSERT INTO students VALUES (1, 'SYN001', '合成共享学生', '一班')"
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
        model_gateway=WorkspaceAITaskModelGateway(
            diagnostic_sink=JsonlDiagnosticJournal(
                tmp_path / "logs" / "llm_diagnostics.jsonl"
            )
        ),
    )
    register_workspace_ai_job(manager, common)
    port = SharedWorkspaceAITaskPort(
        common,
        model_identity=configured,
        conversations=domain.intake.conversations,
        adoption=domain.intake.adoption,
    )
    domain.intake.bind_ai_tasks(port)
    for task_kind in (
        "class_teacher.intake_triage",
        "class_teacher.draft_revision",
        "class_teacher.intake",
    ):
        common.register_adapter(task_kind, domain.intake.ai_task_adapter)
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
        assert finished.task_kind == "class_teacher.intake_triage"
        assert finished.send_attempt_count == 1
        assert finished.dispatch_evidence == "response_persisted"
        assert finished.handoff_total == 1
        assert restored["state"] == "handoff_ready"
        assert marker in configured.calls[0]["messages"][-1]["content"]
        assert marker.encode() not in common.store.db_path.read_bytes()

        handoff = domain.intake.open_handoff(str(restored["handoffs"][0]["handoff_id"]))
        handoff = domain.intake.update_draft(
            handoff_id=str(handoff["handoff_id"]),
            expected_revision=int(handoff["draft_revision"]),
                content={
                    "summary": "合成正式登记内容（教师已核对）",
                    "observed_at": "2026-08-05T08:30:00+08:00",
                    "record_kind": "fact",
                    "source": "合成教师核对",
            },
        )
        rebound = common.get(task_id=task_id).handoffs[0]
        assert rebound.draft_ref.revision == str(handoff["draft_revision"])
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


def test_simplified_provider_triage_becomes_review_only_draft(
    tmp_path: Path,
) -> None:
    domain, common, manager, configured = _wired(
        tmp_path,
        _simplified_provider_triage(),
    )
    try:
        conversation = domain.intake.start_conversation()
        queued = domain.intake.append_turn(
            conversation_id=str(conversation["conversation_id"]),
            expected_revision=int(conversation["revision"]),
            message="合成学生支持信息",
            operation_id="shared-simplified-provider-result",
        )
        task_id = str(queued["turns"][-1]["task_id"])
        started = common.get(task_id=task_id)
        assert started.job_id is not None
        manager.wait(started.job_id, timeout=5)

        finished = common.get(task_id=task_id)
        restored = domain.intake.get_conversation(
            str(conversation["conversation_id"])
        )
        system_prompt = str(configured.calls[0]["messages"][0]["content"])

        assert finished.status == "proposal_ready"
        assert finished.dispatch_evidence == "response_persisted"
        assert restored["state"] == "handoff_ready"
        assert finished.handoffs[0].destination_key == (
            "class_teacher.student.record"
        )
        assert finished.handoffs[0].subject_refs == ()
        assert finished.handoffs[0].missing_fields
        assert '"work_item_id"' in system_prompt
        assert '"draft"' in system_prompt
    finally:
        manager.shutdown()


def test_structured_provider_text_time_fact_becomes_review_only_metadata(
    tmp_path: Path,
) -> None:
    domain, common, manager, _configured = _wired(
        tmp_path,
        _structured_provider_triage_with_text_time_fact(),
    )
    try:
        conversation = domain.intake.start_conversation()
        queued = domain.intake.append_turn(
            conversation_id=str(conversation["conversation_id"]),
            expected_revision=int(conversation["revision"]),
            message="合成带时间说明的班主任事项",
            operation_id="shared-structured-text-time-fact",
        )
        task_id = str(queued["turns"][-1]["task_id"])
        started = common.get(task_id=task_id)
        assert started.job_id is not None
        manager.wait(started.job_id, timeout=5)

        finished = common.get(task_id=task_id)
        restored = domain.intake.get_conversation(
            str(conversation["conversation_id"])
        )
        handoff = domain.intake.open_handoff(
            str(restored["handoffs"][0]["handoff_id"])
        )

        assert finished.status == "proposal_ready"
        assert restored["state"] == "handoff_ready"
        assert handoff["content"]["time_facts"] == [
            {"text": "合成暑假期间计划复查"},
        ]
    finally:
        manager.shutdown()


@pytest.mark.parametrize(
    ("reference_case", "expected_issue_code"),
    [
        ("stale_revision", "student_revision_mismatch"),
        ("unknown_id", "unknown_student_reference"),
    ],
)
def test_invalid_current_student_reference_keeps_review_draft_and_requires_reselection(
    tmp_path: Path,
    reference_case: str,
    expected_issue_code: str,
) -> None:
    domain, common, manager, configured = _wired(tmp_path, _triage())
    try:
        preference = domain.intake.preferences.get()
        domain.intake.preferences.set(
            homeroom_class="一班",
            expected_revision=int(preference["revision"]),
            expected_source_revision=str(preference["source_revision"]),
            operation_id="shared-stale-reference-homeroom",
        )
        candidate = domain.class_roster.ai_candidates(
            token="",
            class_label="一班",
        )[0]
        configured.result = {
            "contract_version": "class_teacher_triage.v1",
            "assistant_message": "已整理为一项待教师核对的合成学生记录。",
            "clarification_questions": [],
            "work_items": [
                {
                    "work_item_id": "shared-stale-student-reference",
                    "domain": "student_growth",
                    "primary_mode": "record",
                    "secondary_modes": [],
                    "intent": "create",
                    "reason_summary": "合成学生版本信息失配回归",
                    "subject_refs": [{
                        "kind": "student",
                        "id": (
                            str(candidate["id"])
                            if reference_case == "stale_revision"
                            else "f" * 64
                        ),
                        "revision": (
                            "7" * 79
                            if reference_case == "stale_revision"
                            else str(candidate["revision"])
                        ),
                    }],
                    "time_facts": [],
                    "safety_level": "normal",
                    "missing_fields": [],
                    "draft": {
                        "summary": "应当保留的待审合成观察",
                        "observed_at": "2026-08-09T08:00:00+08:00",
                    },
                },
                {
                    "work_item_id": "shared-unrelated-review-draft",
                    "domain": "activities_culture",
                    "primary_mode": "record",
                    "secondary_modes": [],
                    "intent": "create",
                    "reason_summary": "另一项合成待审事务",
                    "subject_refs": [],
                    "time_facts": [],
                    "safety_level": "normal",
                    "missing_fields": [],
                    "draft": {"summary": "不应被学生引用失配丢弃的另一项草稿"},
                },
            ],
        }

        conversation = domain.intake.start_conversation()
        queued = domain.intake.append_turn(
            conversation_id=str(conversation["conversation_id"]),
            expected_revision=int(conversation["revision"]),
            message="请整理合成学生观察",
            operation_id="shared-stale-student-reference-task",
        )
        task_id = str(queued["turns"][-1]["task_id"])
        started = common.get(task_id=task_id)
        assert started.job_id is not None
        manager.wait(started.job_id, timeout=5)

        finished = common.get(task_id=task_id)
        restored = domain.intake.get_conversation(
            str(conversation["conversation_id"])
        )
        assert len(restored["handoffs"]) == 2
        opened = [
            domain.intake.open_handoff(str(item["handoff_id"]))
            for item in restored["handoffs"]
        ]
        handoff = next(
            item
            for item in opened
            if item["destination_key"] == "class_teacher.student.record"
        )
        unrelated = next(
            item
            for item in opened
            if item["destination_key"] == "class_teacher.affair.record"
        )

        assert finished.status == "proposal_ready"
        assert restored["state"] == "handoff_ready"
        assert handoff["subject_refs"] == []
        assert "学生版本信息不一致，请重新选择" in handoff["missing_fields"]
        assert handoff["content"]["summary"] == "应当保留的待审合成观察"
        assert handoff["content"]["validation_issue_codes"] == [
            expected_issue_code
        ]
        assert unrelated["content"]["summary"] == (
            "不应被学生引用失配丢弃的另一项草稿"
        )
        assert len(configured.calls) == 1
        diagnostic_events = [
            json.loads(line)
            for line in (
                tmp_path / "logs" / "llm_diagnostics.jsonl"
            ).read_text(encoding="utf-8").splitlines()
        ]
        validation_events = [
            event
            for event in diagnostic_events
            if event.get("event") == "validation"
        ]
        assert len(validation_events) == 1
        validation_event = validation_events[0]
        assert validation_event == {
                "schema_version": 1,
                "event": "validation",
                "timestamp_utc": validation_event["timestamp_utc"],
                "call_id": validation_event["call_id"],
                "operation_id": "shared-stale-student-reference-task",
                "request_id": "shared-stale-student-reference-task",
                "attempt": 1,
                "request_kind": "workspace",
                "protocol": "validation",
                "model": "",
                "endpoint_host": "",
                "workspace_module": "class_teacher",
                "workspace_task_kind": "class_teacher_intake",
                "validation_issue_codes": [expected_issue_code],
            }
        validation_json = json.dumps(validation_events, ensure_ascii=False)
        assert "应当保留的待审合成观察" not in validation_json
        assert str(candidate["id"]) not in validation_json
        assert str(candidate["revision"]) not in validation_json
        assert "7" * 79 not in validation_json
        assert "f" * 64 not in validation_json
        assert "应当保留的待审合成观察".encode() not in common.store.db_path.read_bytes()
    finally:
        manager.shutdown()


def test_focused_student_revision_change_keeps_review_draft_without_rebinding(
    tmp_path: Path,
) -> None:
    domain, common, manager, configured = _wired(tmp_path, _triage())
    try:
        subject = domain.support.create_subject(
            token="",
            operation_id="focused-revision-subject-create",
            source_student_id="SYN-FOCUSED-REVISION-001",
            display_name="合成聚焦学生",
            class_label="一班",
        )
        conversation = domain.intake.start_conversation(
            token="",
            subject_id=str(subject["subject_id"]),
        )
        current = domain.support.update_subject(
            token="",
            subject_id=str(subject["subject_id"]),
            operation_id="focused-revision-subject-update",
            revision=int(subject["revision"]),
            display_name="合成聚焦学生新名",
            class_label="一班",
        )
        assert conversation["focused_subject_revision"] == str(
            subject["revision"]
        )
        assert str(current["revision"]) != str(subject["revision"])
        configured.result = {
            "contract_version": "class_teacher_triage.v1",
            "assistant_message": "已形成待教师复核的学生档案草稿。",
            "clarification_questions": [],
            "work_items": [{
                "work_item_id": "focused-revision-review-draft",
                "domain": "student_growth",
                "primary_mode": "record",
                "secondary_modes": [],
                "intent": "append",
                "reason_summary": "学生版本变化后保留待审正文",
                "subject_refs": [{
                    "kind": "student",
                    "id": str(current["subject_id"]),
                    "revision": str(current["revision"]),
                }],
                "time_facts": [],
                "safety_level": "teacher_review_required",
                "missing_fields": [],
                "draft": {
                    "summary": "版本变化时仍须保留的待审观察",
                    "profile_update": {
                        "summary": "近期观察仍待教师结合最新档案核对。",
                        "dimensions": [{
                            "key": "learning_ability",
                            "label": "学习与能力",
                            "items": ["近期更愿意说明自己的解题思路"],
                        }],
                        "open_questions": ["是否能在不同课堂任务中持续表达"],
                        "support_focus": [],
                    },
                },
            }],
        }

        queued = domain.intake.append_turn(
            conversation_id=str(conversation["conversation_id"]),
            expected_revision=int(conversation["revision"]),
            message="请把这条合成观察整理到当前学生档案。",
            operation_id="focused-revision-shared-task",
        )
        task_id = str(queued["turns"][-1]["task_id"])
        started = common.get(task_id=task_id)
        assert started.job_id is not None
        manager.wait(started.job_id, timeout=5)

        finished = common.get(task_id=task_id)
        restored = domain.intake.get_conversation(
            str(conversation["conversation_id"])
        )
        handoff = domain.intake.open_handoff(
            str(restored["handoffs"][0]["handoff_id"])
        )

        assert finished.status == "proposal_ready"
        assert finished.handoffs[0].subject_refs == ()
        assert handoff["subject_refs"] == []
        assert handoff["missing_fields"] == [
            "学生版本信息不一致，请重新选择"
        ]
        assert handoff["content"]["summary"] == (
            "版本变化时仍须保留的待审观察"
        )
        assert handoff["content"]["validation_issue_codes"] == [
            "student_revision_mismatch"
        ]
        assert len(configured.calls) == 1
    finally:
        manager.shutdown()


def test_malformed_student_reference_still_rejects_the_model_result(
    tmp_path: Path,
) -> None:
    result = _triage()
    result["work_items"][0]["subject_refs"] = [{
        "kind": "student",
        "id": "f" * 64,
        "revision": "7" * 64,
        "unexpected_identity_field": "must-not-be-accepted",
    }]
    domain, common, manager, configured = _wired(tmp_path, result)
    try:
        conversation = domain.intake.start_conversation()
        queued = domain.intake.append_turn(
            conversation_id=str(conversation["conversation_id"]),
            expected_revision=int(conversation["revision"]),
            message="合成格式损坏引用",
            operation_id="shared-malformed-student-reference-task",
        )
        task_id = str(queued["turns"][-1]["task_id"])
        started = common.get(task_id=task_id)
        assert started.job_id is not None
        manager.wait(started.job_id, timeout=5)

        finished = common.get(task_id=task_id)
        restored = domain.intake.get_conversation(
            str(conversation["conversation_id"])
        )

        assert finished.status == "invalid_result"
        assert restored["turns"][-1]["task_state"] == "invalid_result"
        assert restored["handoffs"] == []
        assert len(configured.calls) == 1
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


def test_manual_handoff_without_common_projection_can_be_edited_and_adopted(
    tmp_path: Path,
) -> None:
    domain, common, manager, configured = _wired(
        tmp_path,
        RuntimeError("synthetic transport loss"),
    )
    try:
        conversation = domain.intake.start_conversation()
        queued = domain.intake.append_turn(
            conversation_id=str(conversation["conversation_id"]),
            expected_revision=int(conversation["revision"]),
            message="合成黑板报两周后检查",
            operation_id="shared-manual-route-task",
        )
        turn = queued["turns"][-1]
        task_id = str(turn["task_id"])
        started = common.get(task_id=task_id)
        assert started.job_id is not None
        manager.wait(started.job_id, timeout=5)
        assert common.get(task_id=task_id).handoff_total == 0
        domain.intake.get_conversation(str(conversation["conversation_id"]))

        routed = domain.intake.manual_route(
            turn_id=str(turn["turn_id"]),
            mode="plan_calendar",
        )
        handoff = domain.intake.open_handoff(
            str(routed["handoffs"][0]["handoff_id"])
        )
        updated = domain.intake.update_draft(
            handoff_id=str(handoff["handoff_id"]),
            expected_revision=int(handoff["draft_revision"]),
            content={
                "summary": "合成黑板报两周后检查",
                "manual_routing": True,
                "final_deadline": "2026-08-19T16:00",
                "actions": [
                    {
                        "draft_action_id": "action-1",
                        "title": "检查黑板报初稿",
                        "details": "",
                        "due_at": "2026-08-12T16:00",
                        "depends_on_draft_action_ids": [],
                    }
                ],
            },
        )
        assert updated["draft_revision"] == 2
        assert common.get(task_id=task_id).handoff_total == 0

        receipt = domain.intake.adopt_handoff(
            token="",
            handoff_id=str(updated["handoff_id"]),
            draft_revision=int(updated["draft_revision"]),
            target_revision="new",
            operation_id="ignored-manual-domain-operation",
        )
        replay = domain.intake.adopt_handoff(
            token="",
            handoff_id=str(updated["handoff_id"]),
            draft_revision=int(updated["draft_revision"]),
            target_revision="new",
            operation_id="ignored-manual-domain-replay",
        )

        assert replay["formal_object_id"] == receipt["formal_object_id"]
        assert replay["adoption_id"] == receipt["adoption_id"]
        assert domain.intake.open_handoff(str(updated["handoff_id"]))[
            "adoption_state"
        ] == "adopted"
        calendar = domain.work.read(view="all", anchor="2026-08-12")
        assert {item["title"] for item in calendar["nodes"]} == {
            "合成黑板报两周后检查",
            "检查黑板报初稿",
        }
        assert {item["relation"] for item in calendar["edges"]} == {"contains"}
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
                "record_kind": "fact",
                "source": "合成教师核对",
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
        assert current.task_kind == "class_teacher.draft_revision"
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


def test_reading_original_conversation_does_not_stale_the_current_revision_handoff(
    tmp_path: Path,
) -> None:
    domain, common, manager, configured = _wired(tmp_path, _triage())
    try:
        conversation = domain.intake.start_conversation()
        queued = domain.intake.append_turn(
            conversation_id=str(conversation["conversation_id"]),
            expected_revision=int(conversation["revision"]),
            message="合成历史任务回看正文",
            operation_id="shared-history-rebind-original",
        )
        original_task_id = str(queued["turns"][-1]["task_id"])
        original = common.get(task_id=original_task_id)
        assert original.job_id is not None
        manager.wait(original.job_id, timeout=5)
        ready = domain.intake.get_conversation(str(conversation["conversation_id"]))
        handoff = domain.intake.open_handoff(str(ready["handoffs"][0]["handoff_id"]))

        configured.result = {
            "contract_version": "class_teacher_draft_revision.v1",
            "content": {
                "summary": "合成当前版本正文",
                "observed_at": "2026-08-05T11:00:00+08:00",
            },
        }
        revision = domain.intake.request_draft_revision(
            handoff_id=str(handoff["handoff_id"]),
            expected_revision=int(handoff["draft_revision"]),
            instruction="整理为当前版本",
            operation_id="shared-history-rebind-current",
        )
        current_task_id = str(revision["task_id"])
        current = common.get(task_id=current_task_id)
        assert current.job_id is not None
        manager.wait(current.job_id, timeout=5)
        domain.intake.get_draft_revision(str(revision["request_id"]))

        current_handoff = common.get(task_id=current_task_id).handoffs[0]
        assert current_handoff.adoption_state == "pending"
        assert (
            domain.intake.conversations.common_handoff_id(str(handoff["handoff_id"]))
            == current_handoff.handoff_id
        )

        # Conversation refresh reads the original turn task again.  It must not
        # rebind that historical revision over the current revision handoff.
        domain.intake.get_conversation(str(conversation["conversation_id"]))
        domain.intake.conversations.ai_tasks.get(task_id=original_task_id)

        assert common.get(task_id=current_task_id).handoffs[0].adoption_state == "pending"
        assert (
            domain.intake.conversations.common_handoff_id(str(handoff["handoff_id"]))
            == current_handoff.handoff_id
        )
    finally:
        manager.shutdown()


def test_draft_rebind_recovers_after_cross_store_projection_interruption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    domain, common, manager, _configured = _wired(tmp_path, _triage())
    try:
        conversation = domain.intake.start_conversation()
        queued = domain.intake.append_turn(
            conversation_id=str(conversation["conversation_id"]),
            expected_revision=int(conversation["revision"]),
            message="合成跨库恢复内容",
            operation_id="shared-rebind-interruption-task",
        )
        task_id = str(queued["turns"][-1]["task_id"])
        started = common.get(task_id=task_id)
        assert started.job_id is not None
        manager.wait(started.job_id, timeout=5)
        ready = domain.intake.get_conversation(str(conversation["conversation_id"]))
        handoff = domain.intake.open_handoff(str(ready["handoffs"][0]["handoff_id"]))

        port = domain.intake.conversations.ai_tasks
        original_rebind = port.service.rebind_handoff_draft
        interrupted = False

        def interrupt_once(*args, **kwargs):
            nonlocal interrupted
            if not interrupted:
                interrupted = True
                raise RuntimeError("synthetic rebind projection interruption")
            return original_rebind(*args, **kwargs)

        monkeypatch.setattr(port.service, "rebind_handoff_draft", interrupt_once)
        with pytest.raises(RuntimeError, match="synthetic rebind projection interruption"):
            domain.intake.update_draft(
                handoff_id=str(handoff["handoff_id"]),
                expected_revision=int(handoff["draft_revision"]),
                content={
                    "summary": "合成跨库恢复内容（已保存）",
                    "observed_at": "2026-08-05T10:00:00+08:00",
                    "record_kind": "fact",
                    "source": "合成教师核对",
                },
            )

        restored = domain.intake.open_handoff(str(handoff["handoff_id"]))
        assert common.get(task_id=task_id).handoffs[0].draft_ref.revision != str(
            restored["draft_revision"]
        )
        receipt = domain.intake.adopt_handoff(
            token="",
            handoff_id=str(restored["handoff_id"]),
            draft_revision=int(restored["draft_revision"]),
            target_revision="new",
            operation_id="ignored-rebind-recovered",
        )
        shared = common.get(task_id=task_id).handoffs[0]
        assert shared.draft_ref.revision == str(restored["draft_revision"])
        assert receipt["adoption_id"] == common.get(task_id=task_id).handoffs[0].adoption_id
    finally:
        manager.shutdown()


def test_legacy_intake_task_recovers_locally_without_a_second_model_send(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    domain, common, manager, configured = _wired(tmp_path, _triage())
    def prepare_as_legacy(*, operation_id: str, request: dict[str, object]):
        source = dict(request["source_ref"])
        contexts = [dict(item) for item in list(request["context_refs"])]
        prepared = common.store.prepare(
            operation_id=operation_id,
            module="class_teacher",
            task_kind="class_teacher.intake",
            source_ref=OpaqueRef(
                str(source["kind"]), str(source["id"]), str(source["revision"])
            ),
            context_refs=tuple(
                OpaqueRef(str(item["kind"]), str(item["id"]), str(item["revision"]))
                for item in contexts
            ),
            prompt_contract_version=str(request["prompt_contract_version"]),
            request_fingerprint="b" * 64,
            model_destination_fingerprint="a" * 64,
            return_target=str(request["return_target"]),
        )
        return PreparedTask(prepared.task_id, "")

    monkeypatch.setattr(domain.intake.conversations.ai_tasks, "prepare", prepare_as_legacy)
    conversation = domain.intake.start_conversation()
    queued = domain.intake.append_turn(
        conversation_id=str(conversation["conversation_id"]),
        expected_revision=int(conversation["revision"]),
        message="合成旧任务恢复正文",
        operation_id="shared-legacy-intake-recovery",
    )
    task_id = str(queued["turns"][-1]["task_id"])
    started = common.get(task_id=task_id)
    assert started.task_kind == "class_teacher.intake"
    assert started.status == "prepared"
    queued_task, created = common.store.create_dispatch_job(task_id)
    assert created is True
    claimed = common.store.claim(queued_task.task_id)
    sending = common.store.reserve_send_attempt(claimed.task_id)
    domain.intake.ai_task_adapter.execute(sending, model_gateway=common.model_gateway)
    assert len(configured.calls) == 1
    manager.shutdown()

    restarted_manager = JobManager(JobStore(common.store.db_path), max_workers=1, cleanup_interrupted=False)
    restarted = WorkspaceAITaskService(
        store=WorkspaceAITaskStore(common.store.db_path),
        manager=restarted_manager,
        adapters=(("class_teacher.intake", domain.intake.ai_task_adapter),),
    )
    try:
        restarted.recover_interrupted()
        recovered = restarted.get(task_id=task_id)
        assert recovered.status == "proposal_ready"
        assert recovered.task_kind == "class_teacher.intake"
        assert len(configured.calls) == 1
    finally:
        restarted_manager.shutdown()


def test_legacy_intake_kind_cannot_prepare_or_dispatch_a_new_model_send(tmp_path: Path) -> None:
    _domain, common, manager, configured = _wired(tmp_path, _triage())
    request = PrepareRequest(
        module="class_teacher",
        task_kind="class_teacher.intake",
        source_ref=OpaqueRef("conversation", "legacy-source", "1"),
        context_refs=(OpaqueRef("turn", "legacy-turn", "1"),),
        prompt_contract_version="class_teacher_triage.v1",
        model_destination_fingerprint="a" * 64,
        return_target="class_teacher.home",
    )
    try:
        with pytest.raises(ValueError, match="recovery-only"):
            common.prepare("shared-legacy-new-prepare", request)
        assert common.store.find_by_operation("shared-legacy-new-prepare") is None

        persisted = common.store.prepare(
            operation_id="shared-legacy-new-dispatch",
            module=request.module,
            task_kind=request.task_kind,
            source_ref=request.source_ref,
            context_refs=request.context_refs,
            prompt_contract_version=request.prompt_contract_version,
            request_fingerprint="c" * 64,
            model_destination_fingerprint=request.model_destination_fingerprint,
            return_target=request.return_target,
        )
        with pytest.raises(ValueError, match="recovery-only"):
            common.dispatch(
                "shared-legacy-new-dispatch",
                prepared_task_id=persisted.task_id,
            )
        assert common.get(task_id=persisted.task_id).job_id is None
        queued, created = common.store.create_dispatch_job(persisted.task_id)
        assert created is True
        assert common.run_task(queued.task_id)["status"] == "result_unknown"
        assert configured.calls == []
    finally:
        manager.shutdown()


def test_student_target_conflict_rebinds_shared_handoff_and_adopts_once(tmp_path: Path) -> None:
    domain, common, manager, configured = _wired(tmp_path, _triage())
    try:
        preference = domain.intake.preferences.get()
        domain.intake.preferences.set(
            homeroom_class="一班",
            expected_revision=int(preference["revision"]),
            expected_source_revision=str(preference["source_revision"]),
            operation_id="shared-homeroom-class",
        )
        subject = domain.class_roster.ai_candidates(token="", class_label="一班")[0]
        configured.result = {
            "contract_version": "class_teacher_triage.v1",
            "assistant_message": "已整理为合成学生登记草稿。",
            "clarification_questions": [],
            "work_items": [{
                "work_item_id": "shared-student-record-001",
                "domain": "student_growth",
                "primary_mode": "record",
                "secondary_modes": [],
                "intent": "create",
                "reason_summary": "合成学生版本冲突验证",
                "subject_refs": [{
                    "kind": "student",
                    "id": str(subject["id"]),
                    "revision": str(subject["revision"]),
                }],
                "time_facts": [],
                "safety_level": "normal",
                "missing_fields": [],
                "draft": {
                    "summary": "合成学生观察",
                    "observed_at": "2026-08-05T08:00:00+08:00",
                    "record_kind": "fact",
                    "source": "合成教师核对",
                },
            }],
        }
        conversation = domain.intake.start_conversation()
        queued = domain.intake.append_turn(
            conversation_id=str(conversation["conversation_id"]),
            expected_revision=int(conversation["revision"]),
            message="请登记合成学生观察",
            operation_id="shared-student-target-task",
        )
        task_id = str(queued["turns"][-1]["task_id"])
        started = common.get(task_id=task_id)
        assert started.job_id is not None
        manager.wait(started.job_id, timeout=5)
        ready = domain.intake.get_conversation(str(conversation["conversation_id"]))
        handoff = domain.intake.open_handoff(str(ready["handoffs"][0]["handoff_id"]))

        roster_db = domain.class_roster.source.database_path
        with closing(sqlite3.connect(roster_db)) as connection:
            connection.execute("UPDATE students SET name='合成共享学生新名' WHERE id=1")
            connection.commit()
        with pytest.raises(VaultError, match="学生资料已变化"):
            domain.intake.adopt_handoff(
                token="",
                handoff_id=str(handoff["handoff_id"]),
                draft_revision=int(handoff["draft_revision"]),
                target_revision=str(subject["revision"]),
                operation_id="ignored-shared-target-conflict",
            )
        conflicted_common = common.get(task_id=task_id).handoffs[0]
        conflicted_domain = domain.intake.open_handoff(str(handoff["handoff_id"]))
        assert conflicted_common.adoption_state == "opened"
        assert conflicted_common.target_revision is None
        assert conflicted_domain["adoption_state"] == "stale"

        current = domain.class_roster.ai_candidates(token="", class_label="一班")[0]
        rebound = domain.intake.update_draft(
            handoff_id=str(handoff["handoff_id"]),
            expected_revision=int(conflicted_domain["draft_revision"]),
            content={
                "summary": "合成学生观察已重新核对",
                "observed_at": "2026-08-05T08:30:00+08:00",
                "record_kind": "fact",
                "source": "合成教师核对",
            },
            subject_refs=[{
                "kind": "student",
                "id": str(current["id"]),
                "revision": str(current["revision"]),
            }],
        )
        rebound_common = common.get(task_id=task_id).handoffs[0]
        assert rebound_common.draft_ref.revision == str(rebound["draft_revision"])
        assert rebound_common.subject_refs == (
            OpaqueRef("student", str(current["id"]), str(current["revision"])),
        )

        def adopt_once(_index: int):
            return domain.intake.adopt_handoff(
                token="",
                handoff_id=str(rebound["handoff_id"]),
                draft_revision=int(rebound["draft_revision"]),
                target_revision=str(current["revision"]),
                operation_id="ignored-shared-rebound",
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            receipts = list(executor.map(adopt_once, range(2)))
        assert receipts[0]["adoption_id"] == receipts[1]["adoption_id"]
        assert common.get(task_id=task_id).handoffs[0].adoption_state == "adopted"
        with closing(domain.database.connect()) as connection:
            assert connection.execute("SELECT COUNT(*) FROM support_records").fetchone()[0] == 1
            assert connection.execute("SELECT COUNT(*) FROM handoff_adoption_receipts").fetchone()[0] == 1
    finally:
        manager.shutdown()


def test_plan_validation_failure_reopens_both_layers_and_can_be_corrected(tmp_path: Path) -> None:
    domain, common, manager, configured = _wired(tmp_path, _triage())
    try:
        configured.result = {
            "contract_version": "class_teacher_triage.v1",
            "assistant_message": "已整理为合成计划草稿。",
            "clarification_questions": [],
            "work_items": [{
                "work_item_id": "shared-plan-validation-001",
                "domain": "activities_culture",
                "primary_mode": "plan_calendar",
                "secondary_modes": [],
                "intent": "plan",
                "reason_summary": "合成计划补齐验证",
                "subject_refs": [],
                "time_facts": [],
                "safety_level": "normal",
                "missing_fields": ["请至少保留一个行动"],
                "draft": {
                    "summary": "合成黑板报计划",
                    "plan_title": "合成黑板报",
                    "final_deadline": "2026-08-20T16:00:00+08:00",
                    "actions": [],
                },
            }],
        }
        conversation = domain.intake.start_conversation()
        queued = domain.intake.append_turn(
            conversation_id=str(conversation["conversation_id"]),
            expected_revision=int(conversation["revision"]),
            message="安排合成黑板报检查",
            operation_id="shared-plan-validation-task",
        )
        task_id = str(queued["turns"][-1]["task_id"])
        started = common.get(task_id=task_id)
        assert started.job_id is not None
        manager.wait(started.job_id, timeout=5)
        ready = domain.intake.get_conversation(str(conversation["conversation_id"]))
        handoff = domain.intake.open_handoff(str(ready["handoffs"][0]["handoff_id"]))

        with pytest.raises(VaultError, match="至少保留一个行动"):
            domain.intake.adopt_handoff(
                token="",
                handoff_id=str(handoff["handoff_id"]),
                draft_revision=int(handoff["draft_revision"]),
                target_revision="new",
                operation_id="ignored-plan-validation",
            )
        assert common.get(task_id=task_id).handoffs[0].adoption_state == "opened"
        assert domain.intake.open_handoff(str(handoff["handoff_id"]))["adoption_state"] == "opened"

        corrected = domain.intake.update_draft(
            handoff_id=str(handoff["handoff_id"]),
            expected_revision=int(handoff["draft_revision"]),
            content={
                "summary": "合成黑板报计划",
                "plan_title": "合成黑板报",
                "final_deadline": "2026-08-20T16:00:00+08:00",
                "actions": [{
                    "draft_action_id": "shared-action-1",
                    "title": "检查合成初稿",
                    "details": "",
                    "due_at": "2026-08-15T16:00:00+08:00",
                    "depends_on_draft_action_ids": [],
                }],
            },
        )
        receipt = domain.intake.adopt_handoff(
            token="",
            handoff_id=str(corrected["handoff_id"]),
            draft_revision=int(corrected["draft_revision"]),
            target_revision="new",
            operation_id="ignored-plan-corrected",
        )
        assert str(receipt["object_ref"]).startswith("class_teacher:plan:")
        assert common.get(task_id=task_id).handoffs[0].adoption_state == "adopted"
        with closing(domain.database.connect()) as connection:
            assert connection.execute("SELECT COUNT(*) FROM work_plans").fetchone()[0] == 1
            assert connection.execute("SELECT COUNT(*) FROM handoff_adoption_receipts").fetchone()[0] == 1
    finally:
        manager.shutdown()
