from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from typing import Any, Callable
from uuid import NAMESPACE_URL, uuid4, uuid5

from ..encrypted_database import EncryptedDatabase
from ..errors import VaultError
from ..secure_repository import EncryptedObjectRepository
from ..sop_baseline_service import SopBaselineService
from ..sop_workflow_service import SopWorkflowService
from ..support_record_service import SupportRecordService
from .conversations import ConversationStore


# 计划节点编号从采用编号确定性派生：同一采用的重放得到同一批编号，
# 工作图按「操作编号+操作内容」判定重放，编号漂移会被当成不同操作拒绝。
_HANDOFF_PLAN_NAMESPACE = uuid5(NAMESPACE_URL, "class-teacher:handoff-plan")


def _stable_plan_id(*parts: str) -> str:
    return uuid5(_HANDOFF_PLAN_NAMESPACE, ":".join(parts)).hex


_CONFIRMED_RECORD_KINDS = frozenset(
    {
        "fact",
        "student_statement",
        "reported_statement",
        "teacher_observation",
        "provisional_judgment",
        "professional_conclusion",
    }
)


def _iso() -> str:
    return datetime.now(UTC).isoformat()


def validated_sop_profile_update_entry(
    raw: dict[str, object],
    *,
    subject_id: str,
    validate_profile_update: Callable[[object], dict[str, object]],
) -> dict[str, object]:
    """校验并归一化单条学生档案拟更新；建单批量与草稿逐人确认复用。"""
    record_kind = str(raw.get("record_kind") or "").strip()
    source = str(raw.get("source") or "").strip()
    record_summary = str(raw.get("record_summary") or "").strip()
    observed_at = str(raw.get("observed_at") or "").strip()
    if record_kind not in _CONFIRMED_RECORD_KINDS:
        raise VaultError(
            "class_teacher_record_kind_invalid",
            "请逐名确认学生记录性质",
            status_code=422,
        )
    if not source:
        raise VaultError(
            "class_teacher_record_source_required",
            "请逐名填写学生记录的信息来源",
            status_code=422,
        )
    if not record_summary:
        raise VaultError(
            "class_teacher_sop_profile_summary_required",
            "请逐名核对拟写入学生档案的事件摘要",
            status_code=422,
        )
    if not observed_at:
        raise VaultError(
            "class_teacher_sop_profile_observed_at_required",
            "请逐名核对冲突发生日期或时间",
            status_code=422,
        )
    if record_kind == "professional_conclusion" and not str(
        raw.get("basis") or ""
    ).strip():
        raise VaultError(
            "class_teacher_professional_basis_required",
            "专业结论需要逐名填写书面材料或专业依据",
            status_code=422,
        )
    profile_update = validate_profile_update(raw.get("profile_update"))
    base_revision = raw.get("profile_base_revision")
    if (
        isinstance(base_revision, bool)
        or not isinstance(base_revision, int)
        or base_revision < 0
    ):
        raise VaultError(
            "class_teacher_sop_profile_revision_invalid",
            "学生档案版本无效，请重新整理",
            status_code=422,
        )
    return {
        "subject_id": subject_id,
        "record_kind": record_kind,
        "source": source,
        "basis": str(raw.get("basis") or "").strip() or None,
        "counterexample": (
            str(raw.get("counterexample") or "").strip() or None
        ),
        "record_summary": record_summary,
        "scene": str(raw.get("scene") or "冲突与安全事件").strip(),
        "category": str(raw.get("category") or "同伴冲突跟进").strip(),
        "observed_at": observed_at,
        "review_at": str(raw.get("review_at") or "").strip() or None,
        "expires_at": str(raw.get("expires_at") or "").strip() or None,
        "profile_base_revision": base_revision,
        "profile_update": profile_update,
    }


class HandoffAdoption:
    def __init__(
        self,
        conversations: ConversationStore,
        database: EncryptedDatabase,
        repository: EncryptedObjectRepository,
        key_provider,
        support: SupportRecordService,
        work,
        sop: SopWorkflowService,
        sop_baselines: SopBaselineService,
        class_roster,
        student_cards,
    ) -> None:
        self.conversations = conversations
        self.database = database
        self.repository = repository
        self._key_provider = key_provider
        self.support = support
        self.work = work
        self.sop = sop
        self.sop_baselines = sop_baselines
        self.class_roster = class_roster
        self.student_cards = student_cards

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
        receipt = self.find_receipt(resolved_adoption_id)
        if receipt is not None:
            self._mark_adopted(handoff_id, receipt)
            return {**receipt, "replayed": True}
        if str(handoff["adoption_state"]) in {"discarded", "stale"}:
            raise VaultError("class_teacher_handoff_not_adoptable", "这份交接已失效或已丢弃", status_code=409)

        mode = str(handoff["handling_mode"])
        if mode == "record":
            self._validated_record_attribution(handoff)
        self._mark_adoption_started(handoff_id, target_revision)

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
        if not self.database.exists:
            return None
        receipt = self._receipt(adoption_id)
        if receipt is not None:
            if str(receipt["formal_object_type"]) == "plan":
                handoff = self.conversations.handoff_for_adapter(
                    str(receipt["handoff_id"])
                )
                self._confirm_and_project_plan(
                    token="",
                    handoff=handoff,
                    target_revision=str(receipt["target_revision"]),
                    operation_id=adoption_id,
                )
            # The formal object and receipt commit in the domain transaction.
            # A crash can still happen before the ordinary intake projection is
            # updated, so receipt lookup is also the idempotent recovery seam.
            self._mark_adopted(str(receipt["handoff_id"]), receipt)
        return receipt

    def release_uncommitted(
        self,
        *,
        handoff_id: str,
        adoption_id: str,
        target_revision: str,
    ) -> None:
        if self.find_receipt(adoption_id) is not None:
            return
        with closing(self.conversations.database.connect()) as connection:
            with connection:
                connection.execute(
                    """
                    UPDATE intake_handoffs
                    SET adoption_state='opened', target_revision=NULL, updated_at=?
                    WHERE handoff_id=? AND adoption_id=?
                      AND adoption_state='adoption_started'
                      AND target_revision=?
                    """,
                    (_iso(), handoff_id, adoption_id, str(target_revision)),
                )

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
        record_kind, record_source = self._validated_record_attribution(
            handoff
        )
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
                    source = self.class_roster.resolve_roster_ref(
                        roster_ref=subject_id,
                        expected_revision=selected_revision,
                    )
                except VaultError:
                    self._mark_stale(str(handoff["handoff_id"]), str(target_revision))
                    raise
                subject_identity = {
                    "source_student_id": source.source_key,
                    "student_code": source.student_code,
                    "display_name": source.display_name,
                    "class_label": source.class_label,
                }
            else:
                if str(subject["revision"]) != str(target_revision):
                    self._mark_stale(str(handoff["handoff_id"]), str(target_revision))
                    raise VaultError("class_teacher_target_conflict", "学生资料已变化，请刷新后重新核对", status_code=409)
            receipt_hook = self._receipt_hook(handoff, target_revision, "student_record")
            profile_update = content.get("profile_update")
            profile_base_revision = content.get("profile_base_revision")

            def hook(connection: Any, vmk: bytes, record_id: str) -> None:
                receipt_hook(connection, vmk, record_id)
                if not isinstance(profile_update, dict):
                    return
                subject_row = connection.execute(
                    "SELECT subject_id FROM support_records WHERE record_id=?",
                    (record_id,),
                ).fetchone()
                if subject_row is None:
                    raise VaultError(
                        "support_record_not_found",
                        "学生记录没有完成，档案未更新",
                        status_code=409,
                    )
                self._record_profile_snapshot(
                    connection,
                    vmk=vmk,
                    adoption_id=str(handoff["adoption_id"]),
                    subject_id=str(subject_row["subject_id"]),
                )
                self.student_cards.upsert_current_profile_in_connection(
                    connection,
                    vmk=vmk,
                    subject_id=str(subject_row["subject_id"]),
                    profile_update=profile_update,
                    expected_revision=(
                        int(profile_base_revision)
                        if isinstance(profile_base_revision, int)
                        else None
                    ),
                    operation_id=f"profile_{operation_id}",
                    model_operation_id=self.conversations.task_id_for_handoff(
                        str(handoff["handoff_id"])
                    ),
                    teacher_quote=str(content.get("teacher_quote") or content.get("summary") or ""),
                    model_draft=json.dumps(profile_update, ensure_ascii=False),
                    source_record_id=record_id,
                    latest_round_record_id=record_id,
                )
            record = self.support.create_record(
                token=token,
                operation_id=operation_id,
                subject_id=subject_id,
                record_kind=record_kind,
                content=str(content.get("summary") or content.get("content") or ""),
                scene=str(content.get("scene") or "班主任工作台登记"),
                source=record_source,
                basis=str(content.get("basis") or "").strip() or None,
                counterexample=str(content.get("counterexample") or "").strip() or None,
                category=str(content.get("category") or "日常记录"),
                observed_at=str(content.get("observed_at") or _iso()),
                review_at=str(content.get("review_at") or "").strip() or None,
                expires_at=str(content.get("expires_at") or "").strip() or None,
                subject_identity=subject_identity,
                transaction_hook=hook,
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
                    payload={
                        **{
                            key: value
                            for key, value in content.items()
                            if key != "teacher_confirmed"
                        },
                        "record_kind": record_kind,
                        "source": record_source,
                        "teacher_confirmed": True,
                        "created_at": timestamp,
                    },
                )
                connection.execute(
                    "INSERT INTO class_teacher_affair_records VALUES (?, ?, ?, ?)",
                    (record_id, object_id, timestamp, timestamp),
                )
                self._write_receipt(connection, handoff, target_revision, "affair_record", record_id)
        return self._receipt(str(handoff["adoption_id"])) or {}

    @staticmethod
    def _validated_record_attribution(
        handoff: dict[str, object],
    ) -> tuple[str, str]:
        content = handoff.get("content")
        if not isinstance(content, dict):
            raise VaultError(
                "class_teacher_record_kind_invalid",
                "请明确选择记录性质",
                status_code=422,
            )
        record_kind = str(content.get("record_kind") or "").strip()
        if record_kind not in _CONFIRMED_RECORD_KINDS:
            raise VaultError(
                "class_teacher_record_kind_invalid",
                "请明确选择记录性质",
                status_code=422,
            )
        source = str(content.get("source") or "").strip()
        if not source:
            raise VaultError(
                "class_teacher_record_source_required",
                "请填写信息来源",
                status_code=422,
            )
        if record_kind == "professional_conclusion":
            if not str(content.get("basis") or "").strip():
                raise VaultError(
                    "class_teacher_professional_basis_required",
                    "专业结论需要填写书面材料或专业依据",
                    status_code=422,
                )
            if not str(content.get("observed_at") or "").strip():
                raise VaultError(
                    "class_teacher_professional_date_required",
                    "专业结论需要核对结论日期",
                    status_code=422,
                )
            if (
                str(handoff.get("destination_key") or "")
                == "class_teacher.student.record"
                and not isinstance(content.get("profile_update"), dict)
            ):
                raise VaultError(
                    "class_teacher_professional_profile_required",
                    "请核对专业结论将如何更新学生当前档案",
                    status_code=422,
                )
            if (
                str(handoff.get("destination_key") or "")
                == "class_teacher.student.record"
                and not str(content.get("current_school_support") or "").strip()
            ):
                raise VaultError(
                    "class_teacher_professional_school_support_required",
                    "请核对学生当前在校支持；如暂无请明确记录",
                    status_code=422,
                )
        return record_kind, source

    def _adopt_plan(
        self,
        token: str,
        handoff: dict[str, object],
        target_revision: str,
        operation_id: str,
    ) -> dict[str, object]:
        self._confirm_and_project_plan(
            token=token,
            handoff=handoff,
            target_revision=target_revision,
            operation_id=operation_id,
        )
        return self._receipt(str(handoff["adoption_id"])) or {}

    def _confirm_and_project_plan(
        self,
        *,
        token: str,
        handoff: dict[str, object],
        target_revision: str,
        operation_id: str,
    ) -> None:
        content = dict(handoff["content"])
        actions = content.get("actions")
        if not isinstance(actions, list) or not actions:
            raise VaultError("class_teacher_plan_actions_required", "请至少保留一个行动后再加入计划", status_code=422)
        deadline = str(content.get("final_deadline") or "").strip()
        if not deadline:
            raise VaultError("class_teacher_plan_deadline_required", "加入正式日历前请确认截止时间", status_code=422)
        clean_actions = [dict(item) for item in actions if isinstance(item, dict)]
        for item in clean_actions:
            if not str(item.get("due_at") or "").strip():
                raise VaultError("class_teacher_plan_action_deadline_required", "行动缺少截止时间", status_code=422)
        plan_title = str(content.get("plan_title") or content.get("summary") or "班主任计划")
        # 台账双写已收敛：工作图是计划的唯一正式载体，页面（首页/日历）只读工作图。
        # 节点编号从采用编号确定性派生；收据以工作图返回的编号为准，
        # 同一 operation 的重放会返回原节点，收据与节点据此幂等收敛。
        adoption_id = str(handoff["adoption_id"])
        action_ids = [
            _stable_plan_id(adoption_id, "action", str(item.get("draft_action_id") or index))
            for index, item in enumerate(clean_actions)
        ]
        result = self.work.create_confirmed_plan(
            plan_id=_stable_plan_id(adoption_id),
            action_ids=action_ids,
            plan_title=plan_title,
            final_deadline=deadline,
            actions=clean_actions,
            operation_id=f"handoff_plan_{adoption_id}",
        )
        self._write_plan_receipt(handoff, target_revision, str(result["plan_id"]))

    def _write_plan_receipt(
        self,
        handoff: dict[str, object],
        target_revision: str,
        plan_id: str,
    ) -> None:
        # 先走密钥入口完成明文库初始化，再写收据。
        self._key_provider()
        try:
            with closing(self.database.connect()) as connection:
                with connection:
                    self._write_receipt(connection, handoff, target_revision, "plan", plan_id)
        except sqlite3.IntegrityError:
            # 同一采用编号的收据已存在（上次写库后中断）：入口的 find_receipt
            # 重放路径会按收据重新投影并标记已并入，这里不必重复写。
            pass

    def _adopt_sop(
        self,
        token: str,
        handoff: dict[str, object],
        target_revision: str,
        operation_id: str,
    ) -> dict[str, object]:
        content = dict(handoff["content"])
        baselines = self.sop_baselines.ensure_baselines(token=token)["items"]
        template_key = str(content.get("template_key") or "").strip()
        if not template_key:
            raise VaultError(
                "class_teacher_sop_template_required",
                "请先选择与实际情况相符的学校流程模板",
                status_code=422,
            )
        selected = next((item for item in baselines if item.get("template_key") == template_key), None)
        if selected is None:
            raise VaultError("class_teacher_sop_template_invalid", "请选择可用的学校流程模板", status_code=422)
        refs: list[str] = []
        subject_id_by_ref: dict[str, str] = {}
        display_name_by_subject: dict[str, str] = {}
        verified_current_subject_ids: list[str] = []
        for index, item in enumerate(list(handoff.get("subject_refs") or [])):
            if not isinstance(item, dict) or not item.get("id"):
                continue
            candidate_id = str(item.get("id") or "")
            candidate_revision = str(item.get("revision") or "")
            try:
                subject = self.support.get_subject(
                    token=token,
                    subject_id=candidate_id,
                )
            except VaultError as exc:
                if exc.code != "support_subject_not_found":
                    raise
                source = self.class_roster.resolve_roster_ref(
                    roster_ref=candidate_id,
                    expected_revision=candidate_revision,
                )
                subject = self.support.create_subject_for_roster_source(
                    token=token,
                    operation_id=(
                        f"sop-subject-{handoff['adoption_id']}-{index + 1}"
                    ),
                    source_student_id=source.source_key,
                    legacy_student_code=source.student_code,
                    display_name=source.display_name,
                    class_label=source.class_label,
                )
                verified_current_subject_ids.append(
                    str(subject["subject_id"])
                )
            resolved_subject_id = str(subject["subject_id"])
            refs.append(resolved_subject_id)
            subject_id_by_ref[candidate_id] = resolved_subject_id
            display_name_by_subject[resolved_subject_id] = str(
                subject.get("display_name") or "学生"
            )
        participant_refs = [str(item) for item in list(content.get("participant_refs") or []) if str(item).strip()]
        if not refs and not participant_refs:
            raise VaultError("class_teacher_sop_participants_required", "进入 SOP 前请确认参与对象", status_code=422)
        profile_updates = self._validated_sop_profile_updates(
            content=content,
            subject_id_by_ref=subject_id_by_ref,
        )
        step_text_overrides = {
            str(item.get("key") or ""): {
                "title": str(item.get("title") or ""),
                "details": str(item.get("details") or ""),
            }
            for item in list(content.get("steps") or [])
            if isinstance(item, dict) and str(item.get("key") or "").strip()
        }
        receipt_hook = self._receipt_hook(handoff, target_revision, "sop_affair")

        def hook(
            connection: Any,
            vmk: bytes,
            affair_id: str,
            _occurrence_id: str,
        ) -> None:
            receipt_hook(connection, vmk, affair_id)
            task_id = self.conversations.task_id_for_handoff(
                str(handoff["handoff_id"])
            )
            # 档案更新只落待确认草稿，教师逐人确认后才真正写入档案。
            self.sop._insert_profile_update_drafts_in_connection(
                connection,
                vmk=vmk,
                affair_id=affair_id,
                updates=[
                    {
                        **update,
                        "display_name": display_name_by_subject.get(
                            str(update["subject_id"]),
                            "学生",
                        ),
                    }
                    for update in profile_updates
                ],
                source_task_id=task_id,
            )
        self.sop.create_affair(
            token=token,
            operation_id=operation_id,
            template_version_id=str(selected["template_version_id"]),
            title=str(content.get("title") or content.get("summary") or "待教师处理的连续事务"),
            summary=str(content.get("summary") or "").strip() or None,
            participant_refs=participant_refs,
            subject_ids=refs,
            verified_current_subject_ids=verified_current_subject_ids,
            step_text_overrides=step_text_overrides,
            pending_verifications=[
                str(item).strip()
                for item in list(content.get("to_verify") or [])
                if str(item).strip()
            ],
            idempotency_fingerprint=str(handoff["adoption_id"]),
            transaction_hook=hook,
        )
        return self._receipt(str(handoff["adoption_id"])) or {}

    def _validated_sop_profile_updates(
        self,
        *,
        content: dict[str, object],
        subject_id_by_ref: dict[str, str],
    ) -> list[dict[str, object]]:
        raw_updates = content.get("student_profile_updates")
        if raw_updates is None:
            return []
        if not isinstance(raw_updates, list) or len(raw_updates) > 50:
            raise VaultError(
                "class_teacher_sop_profile_updates_invalid",
                "学生档案更新草稿无效，请重新核对",
                status_code=422,
            )
        updates: list[dict[str, object]] = []
        seen: set[str] = set()
        for raw in raw_updates:
            if not isinstance(raw, dict):
                raise VaultError(
                    "class_teacher_sop_profile_updates_invalid",
                    "学生档案更新草稿无效，请重新核对",
                    status_code=422,
                )
            if not bool(raw.get("include", True)):
                continue
            subject_ref = raw.get("subject_ref")
            if not isinstance(subject_ref, dict):
                raise VaultError(
                    "class_teacher_sop_profile_subject_invalid",
                    "学生档案更新没有绑定本次参与学生",
                    status_code=422,
                )
            source_ref = str(subject_ref.get("id") or "")
            subject_id = subject_id_by_ref.get(source_ref)
            if not subject_id or source_ref in seen:
                raise VaultError(
                    "class_teacher_sop_profile_subject_invalid",
                    "学生档案更新与本次参与学生不一致",
                    status_code=422,
                )
            seen.add(source_ref)
            updates.append(
                validated_sop_profile_update_entry(
                    raw,
                    subject_id=subject_id,
                    validate_profile_update=self.student_cards.validate_profile_update,
                )
            )
        return updates

    def _record_profile_snapshot(
        self,
        connection: Any,
        *,
        vmk: bytes,
        adoption_id: str,
        subject_id: str,
    ) -> None:
        """Capture the pre-merge current profile so one-click revert can restore it.

        A NULL profile_snapshot_object_id on the receipt means no profile
        existed before this merge; revert then supersedes the created entry.
        """
        row = connection.execute(
            """
            SELECT entry_id, payload_object_id, source_record_id
            FROM student_card_entries
            WHERE subject_id=? AND state='active'
            ORDER BY created_at DESC, entry_id DESC LIMIT 1
            """,
            (subject_id,),
        ).fetchone()
        if row is None:
            return
        payload, revision = self.repository.get(
            connection,
            vmk=vmk,
            object_id=str(row["payload_object_id"]),
        )
        snapshot_object_id = f"profile-revert-snapshot-{adoption_id}"
        self.repository.put(
            connection,
            vmk=vmk,
            object_id=snapshot_object_id,
            object_type="student_profile_revert_snapshot",
            payload={
                "entry_id": str(row["entry_id"]),
                "payload_object_id": str(row["payload_object_id"]),
                "payload": payload,
                "revision_before": int(revision),
                "source_record_id_before": (
                    str(row["source_record_id"]) if row["source_record_id"] else None
                ),
            },
        )
        connection.execute(
            "UPDATE handoff_adoption_receipts SET profile_snapshot_object_id=? WHERE adoption_id=?",
            (snapshot_object_id, adoption_id),
        )

    def revert_profile_adoption(self, *, token: str, handoff_id: str) -> dict[str, object]:
        handoff = self.conversations.handoff_for_adapter(handoff_id)
        if str(handoff["destination_key"]) != "class_teacher.student.record":
            raise VaultError(
                "class_teacher_revert_not_allowed",
                "只有学生个人档案更新支持一键撤回",
                status_code=422,
            )
        adoption_id = str(handoff["adoption_id"])
        receipt = self._receipt(adoption_id)
        if receipt is None or str(receipt["formal_object_type"]) != "student_record":
            raise VaultError(
                "class_teacher_revert_not_adopted",
                "这份档案更新尚未并入，无需撤回",
                status_code=409,
            )
        if receipt.get("reverted_at"):
            # Idempotent replay: the profile was already restored; make sure the
            # ordinary-database handoff state caught up, then report success.
            self._mark_reverted(handoff_id)
            return self._revert_result(receipt, replayed=True)
        if str(handoff["adoption_state"]) != "adopted":
            raise VaultError(
                "class_teacher_revert_not_adopted",
                "这份档案更新尚未并入，无需撤回",
                status_code=409,
            )
        record_id = str(receipt["formal_object_id"])
        vmk = self._key_provider(token)
        with closing(self.database.connect()) as connection:
            with connection:
                subject_row = connection.execute(
                    "SELECT subject_id FROM support_records WHERE record_id=?",
                    (record_id,),
                ).fetchone()
                if subject_row is None:
                    raise VaultError(
                        "support_record_not_found",
                        "原始记录不存在，无法撤回档案合并",
                        status_code=409,
                    )
                subject_id = str(subject_row["subject_id"])
                entry = connection.execute(
                    """
                    SELECT entry_id, payload_object_id, source_record_id
                    FROM student_card_entries
                    WHERE subject_id=? AND state='active'
                    ORDER BY created_at DESC, entry_id DESC LIMIT 1
                    """,
                    (subject_id,),
                ).fetchone()
                if entry is None or str(entry["source_record_id"] or "") != record_id:
                    raise VaultError(
                        "class_teacher_revert_superseded",
                        "档案已有更新轮次，请手动修正",
                        status_code=409,
                    )
                snapshot_object_id = str(receipt.get("profile_snapshot_object_id") or "")
                if snapshot_object_id:
                    snapshot, _snapshot_revision = self.repository.get(
                        connection,
                        vmk=vmk,
                        object_id=snapshot_object_id,
                    )
                    restored = dict(snapshot["payload"])
                    # 撤回后不再有任何"本轮更新"高亮。
                    restored.pop("latest_round", None)
                    self.repository.put(
                        connection,
                        vmk=vmk,
                        object_id=str(entry["payload_object_id"]),
                        object_type="student_card_current_profile",
                        payload=restored,
                    )
                    connection.execute(
                        "UPDATE student_card_entries SET source_record_id=? WHERE entry_id=?",
                        (snapshot.get("source_record_id_before"), str(entry["entry_id"])),
                    )
                else:
                    connection.execute(
                        "UPDATE student_card_entries SET state='superseded' WHERE entry_id=?",
                        (str(entry["entry_id"]),),
                    )
                reverted_at = _iso()
                connection.execute(
                    "UPDATE handoff_adoption_receipts SET reverted_at=? WHERE adoption_id=?",
                    (reverted_at, adoption_id),
                )
        self._mark_reverted(handoff_id)
        return self._revert_result({**receipt, "reverted_at": reverted_at}, replayed=False)

    @staticmethod
    def _revert_result(receipt: dict[str, object], *, replayed: bool) -> dict[str, object]:
        snapshot_object_id = str(receipt.get("profile_snapshot_object_id") or "")
        return {
            "adoption_id": str(receipt["adoption_id"]),
            "handoff_id": str(receipt["handoff_id"]),
            "adoption_state": "reverted",
            "profile_state": "restored" if snapshot_object_id else "not_created",
            "source_record_retained": True,
            "source_record_note": "撤回只回滚档案合并；本轮产生的原始支持记录仍保留。",
            "replayed": replayed,
        }

    def _mark_reverted(self, handoff_id: str) -> None:
        with closing(self.conversations.database.connect()) as connection:
            with connection:
                connection.execute(
                    """
                    UPDATE intake_handoffs SET adoption_state='reverted', updated_at=?
                    WHERE handoff_id=? AND adoption_state='adopted'
                    """,
                    (_iso(), handoff_id),
                )

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
                if str(row["adoption_state"]) == "reverted":
                    return
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
