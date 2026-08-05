from __future__ import annotations

import json
from contextlib import closing
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from ..encrypted_database import EncryptedDatabase
from ..errors import VaultError
from ..planning_service import PlanningService
from ..secure_repository import EncryptedObjectRepository
from ..sop_baseline_service import SopBaselineService
from ..sop_workflow_service import SopWorkflowService
from ..support_record_service import SupportRecordService
from .conversations import ConversationStore


def _iso() -> str:
    return datetime.now(UTC).isoformat()


class HandoffAdoption:
    def __init__(
        self,
        conversations: ConversationStore,
        database: EncryptedDatabase,
        repository: EncryptedObjectRepository,
        key_provider,
        support: SupportRecordService,
        planning: PlanningService,
        sop: SopWorkflowService,
        sop_baselines: SopBaselineService,
        class_roster,
    ) -> None:
        self.conversations = conversations
        self.database = database
        self.repository = repository
        self._key_provider = key_provider
        self.support = support
        self.planning = planning
        self.sop = sop
        self.sop_baselines = sop_baselines
        self.class_roster = class_roster

    def adopt(
        self,
        *,
        token: str,
        handoff_id: str,
        draft_revision: int,
        target_revision: str,
        operation_id: str,
        adoption_id: str | None = None,
    ) -> dict[str, object]:
        handoff = self.conversations.open_handoff(handoff_id)
        if int(handoff["draft_revision"]) != int(draft_revision):
            raise VaultError("class_teacher_draft_conflict", "草稿已经变化，请刷新后再保存", status_code=409)
        if adoption_id and str(handoff["adoption_id"]) != adoption_id:
            self._bind_adoption_id(handoff_id, adoption_id)
            handoff = self.conversations.handoff_for_adapter(handoff_id)
        resolved_adoption_id = str(handoff["adoption_id"])
        receipt = self._receipt(resolved_adoption_id)
        if receipt is not None:
            self._mark_adopted(handoff_id, receipt)
            return {**receipt, "replayed": True}
        if str(handoff["adoption_state"]) in {"discarded", "stale"}:
            raise VaultError("class_teacher_handoff_not_adoptable", "这份交接已失效或已丢弃", status_code=409)

        self._mark_adoption_started(handoff_id, target_revision)

        mode = str(handoff["handling_mode"])
        if mode == "record":
            receipt = self._adopt_record(token, handoff, target_revision, operation_id)
        elif mode == "plan_calendar":
            receipt = self._adopt_plan(token, handoff, target_revision, operation_id)
        elif mode == "sop":
            receipt = self._adopt_sop(token, handoff, target_revision, operation_id)
        else:
            raise VaultError("class_teacher_handling_mode_invalid", "处理方式无效", status_code=422)
        self._mark_adopted(handoff_id, receipt)
        return {**receipt, "replayed": False}

    def find_receipt(self, adoption_id: str) -> dict[str, object] | None:
        return self._receipt(adoption_id)

    def _bind_adoption_id(self, handoff_id: str, adoption_id: str) -> None:
        with closing(self.conversations.database.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = self.conversations._handoff_row(connection, handoff_id)
                current = str(row["adoption_id"])
                if current == adoption_id:
                    connection.commit()
                    return
                if str(row["adoption_state"]) in {"adopted", "discarded", "stale"}:
                    raise VaultError("class_teacher_adoption_conflict", "交接采用编号已经固定", status_code=409)
                if self._receipt(current) is not None:
                    raise VaultError("class_teacher_adoption_conflict", "交接采用收据已经存在", status_code=409)
                connection.execute(
                    "UPDATE intake_handoffs SET adoption_id=?, updated_at=? WHERE handoff_id=?",
                    (adoption_id, _iso(), handoff_id),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def _adopt_record(
        self,
        token: str,
        handoff: dict[str, object],
        target_revision: str,
        operation_id: str,
    ) -> dict[str, object]:
        content = dict(handoff["content"])
        destination = str(handoff["destination_key"])
        if destination == "class_teacher.student.record":
            refs = list(handoff.get("subject_refs") or [])
            if len(refs) != 1 or not isinstance(refs[0], dict):
                raise VaultError("class_teacher_subject_required", "保存学生记录前请只选择一名学生", status_code=422)
            subject_id = str(refs[0].get("id") or "")
            selected_revision = str(refs[0].get("revision") or "")
            if selected_revision != str(target_revision):
                self._mark_stale(str(handoff["handoff_id"]), str(target_revision))
                raise VaultError("class_teacher_target_conflict", "学生资料已变化，请刷新后重新核对", status_code=409)
            subject_identity: dict[str, str] | None = None
            try:
                subject = self.support.get_subject(token=token, subject_id=subject_id)
            except VaultError as exc:
                if exc.code != "support_subject_not_found":
                    raise
                try:
                    source = self.class_roster.resolve_opaque_ref(
                        token=token,
                        opaque_ref=subject_id,
                        expected_revision=selected_revision,
                    )
                except VaultError:
                    self._mark_stale(str(handoff["handoff_id"]), str(target_revision))
                    raise
                subject_identity = {
                    "source_student_id": source.source_key,
                    "display_name": source.display_name,
                    "class_label": source.class_label,
                }
            else:
                if str(subject["revision"]) != str(target_revision):
                    self._mark_stale(str(handoff["handoff_id"]), str(target_revision))
                    raise VaultError("class_teacher_target_conflict", "学生资料已变化，请刷新后重新核对", status_code=409)
            hook = self._receipt_hook(handoff, target_revision, "student_record")
            record = self.support.create_record(
                token=token,
                operation_id=operation_id,
                subject_id=subject_id,
                record_kind=str(content.get("record_kind") or "fact"),
                content=str(content.get("summary") or content.get("content") or ""),
                scene=str(content.get("scene") or "班主任工作台登记"),
                source=str(content.get("source") or "教师核对的会话草稿"),
                basis=str(content.get("basis") or "").strip() or None,
                counterexample=str(content.get("counterexample") or "").strip() or None,
                category=str(content.get("category") or "日常记录"),
                observed_at=str(content.get("observed_at") or _iso()),
                review_at=str(content.get("review_at") or "").strip() or None,
                expires_at=str(content.get("expires_at") or "").strip() or None,
                subject_identity=subject_identity,
                transaction_hook=lambda connection, vmk, record_id: hook(connection, vmk, record_id),
            )
            return self._receipt(str(handoff["adoption_id"])) or {
                "formal_object_type": "student_record", "formal_object_id": str(record["record_id"])
            }

        if destination != "class_teacher.affair.record":
            raise VaultError("class_teacher_destination_invalid", "登记目标未获允许", status_code=422)
        vmk = self._key_provider(token)
        record_id = uuid4().hex
        timestamp = _iso()
        with closing(self.database.connect()) as connection:
            with connection:
                object_id = f"class-teacher-affair-record-{record_id}"
                self.repository.put(
                    connection,
                    vmk=vmk,
                    object_id=object_id,
                    object_type="class_teacher_affair_record",
                    payload={**content, "teacher_confirmed": True, "created_at": timestamp},
                )
                connection.execute(
                    "INSERT INTO class_teacher_affair_records VALUES (?, ?, ?, ?)",
                    (record_id, object_id, timestamp, timestamp),
                )
                self._write_receipt(connection, handoff, target_revision, "affair_record", record_id)
        return self._receipt(str(handoff["adoption_id"])) or {}

    def _adopt_plan(
        self,
        token: str,
        handoff: dict[str, object],
        target_revision: str,
        operation_id: str,
    ) -> dict[str, object]:
        content = dict(handoff["content"])
        actions = content.get("actions")
        if not isinstance(actions, list) or not actions:
            raise VaultError("class_teacher_plan_actions_required", "请至少保留一个行动后再加入计划", status_code=422)
        deadline = str(content.get("final_deadline") or "").strip()
        if not deadline:
            raise VaultError("class_teacher_plan_deadline_required", "加入正式日历前请确认截止时间", status_code=422)
        draft = self.planning.create_draft(
            token=token,
            operation_id=f"{operation_id}-draft",
            raw_input=str(content.get("summary") or content.get("plan_title") or "班主任计划"),
            reference_at=str(content.get("reference_at") or "").strip() or None,
            final_deadline=deadline,
        )
        hook = self._receipt_hook(handoff, target_revision, "plan")
        self.planning.confirm_draft(
            token=token,
            draft_id=str(draft["draft_id"]),
            operation_id=operation_id,
            revision=int(draft["revision"]),
            plan_title=str(content.get("plan_title") or content.get("summary") or "班主任计划"),
            actions=[dict(item) for item in actions if isinstance(item, dict)],
            transaction_hook=lambda connection, vmk, plan_id: hook(connection, vmk, plan_id),
        )
        return self._receipt(str(handoff["adoption_id"])) or {}

    def _adopt_sop(
        self,
        token: str,
        handoff: dict[str, object],
        target_revision: str,
        operation_id: str,
    ) -> dict[str, object]:
        content = dict(handoff["content"])
        baselines = self.sop_baselines.ensure_baselines(token=token)["items"]
        template_key = str(content.get("template_key") or "baseline.student_conflict")
        selected = next((item for item in baselines if item.get("template_key") == template_key), None)
        if selected is None:
            raise VaultError("class_teacher_sop_template_invalid", "请选择可用的学校流程模板", status_code=422)
        refs = [str(item.get("id")) for item in list(handoff.get("subject_refs") or []) if isinstance(item, dict) and item.get("id")]
        participant_refs = [str(item) for item in list(content.get("participant_refs") or []) if str(item).strip()]
        if not refs and not participant_refs:
            raise VaultError("class_teacher_sop_participants_required", "进入 SOP 前请确认参与对象", status_code=422)
        hook = self._receipt_hook(handoff, target_revision, "sop_affair")
        self.sop.create_affair(
            token=token,
            operation_id=operation_id,
            template_version_id=str(selected["template_version_id"]),
            title=str(content.get("title") or content.get("summary") or "待教师处理的连续事务"),
            summary=str(content.get("summary") or "").strip() or None,
            participant_refs=participant_refs,
            subject_ids=refs,
            idempotency_fingerprint=str(handoff["adoption_id"]),
            transaction_hook=lambda connection, vmk, affair_id, _occurrence_id: hook(connection, vmk, affair_id),
        )
        return self._receipt(str(handoff["adoption_id"])) or {}

    def _receipt_hook(self, handoff: dict[str, object], target_revision: str, object_type: str):
        def write(connection: Any, _vmk: bytes, object_id: str) -> None:
            self._write_receipt(connection, handoff, target_revision, object_type, object_id)
        return write

    @staticmethod
    def _write_receipt(
        connection: Any,
        handoff: dict[str, object],
        target_revision: str,
        object_type: str,
        object_id: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO handoff_adoption_receipts (
                adoption_id, handoff_id, handling_mode, formal_object_type,
                formal_object_id, draft_revision, target_revision, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(handoff["adoption_id"]), str(handoff["handoff_id"]),
                str(handoff["handling_mode"]), object_type, object_id,
                int(handoff["draft_revision"]), str(target_revision), _iso(),
            ),
        )

    def _receipt(self, adoption_id: str) -> dict[str, object] | None:
        if not self.database.exists:
            return None
        with closing(self.database.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM handoff_adoption_receipts WHERE adoption_id=?", (adoption_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def _mark_adopted(self, handoff_id: str, receipt: dict[str, object]) -> None:
        with closing(self.conversations.database.connect()) as connection:
            with connection:
                row = self.conversations._handoff_row(connection, handoff_id)
                connection.execute(
                    """
                    UPDATE intake_handoffs SET adoption_state='adopted',
                        formal_object_type=?, formal_object_id=?, updated_at=?
                    WHERE handoff_id=?
                    """,
                    (receipt["formal_object_type"], receipt["formal_object_id"], _iso(), handoff_id),
                )
                connection.execute(
                    "UPDATE intake_drafts SET state='adopted', updated_at=? WHERE draft_id=?",
                    (_iso(), str(row["draft_id"])),
                )
                remaining = connection.execute(
                    """
                    SELECT COUNT(*) FROM intake_handoffs h JOIN intake_drafts d ON d.draft_id=h.draft_id
                    WHERE d.conversation_id=? AND h.adoption_state IN ('pending','opened','adoption_started')
                    """,
                    (str(row["conversation_id"]),),
                ).fetchone()[0]
                if int(remaining) == 0:
                    connection.execute(
                        "UPDATE intake_conversations SET state='teacher_confirmed', updated_at=? WHERE conversation_id=?",
                        (_iso(), str(row["conversation_id"])),
                    )

    def _mark_adoption_started(self, handoff_id: str, target_revision: str) -> None:
        with closing(self.conversations.database.connect()) as connection:
            with connection:
                row = self.conversations._handoff_row(connection, handoff_id)
                if str(row["adoption_state"]) not in {"adopted", "discarded", "stale"}:
                    connection.execute(
                        "UPDATE intake_handoffs SET adoption_state='adoption_started', target_revision=?, updated_at=? WHERE handoff_id=?",
                        (str(target_revision), _iso(), handoff_id),
                    )

    def _mark_stale(self, handoff_id: str, target_revision: str) -> None:
        with closing(self.conversations.database.connect()) as connection:
            with connection:
                row = self.conversations._handoff_row(connection, handoff_id)
                connection.execute(
                    "UPDATE intake_handoffs SET adoption_state='stale', target_revision=?, updated_at=? WHERE handoff_id=?",
                    (str(target_revision), _iso(), handoff_id),
                )
                connection.execute(
                    "UPDATE intake_drafts SET state='stale', updated_at=? WHERE draft_id=?",
                    (_iso(), str(row["draft_id"])),
                )


__all__ = ["HandoffAdoption"]
