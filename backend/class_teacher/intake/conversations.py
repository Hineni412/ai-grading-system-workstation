from __future__ import annotations

import json
import re
from contextlib import closing
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from ..errors import VaultError
from ..ordinary_database import OrdinaryWorkDatabase
from .ports import WorkspaceAITaskPort
from .triage_contract import TriageResult, parse_triage


_OPAQUE_ID = re.compile(r"[A-Za-z0-9_-]{8,128}")


def _iso() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class ConversationStore:
    def __init__(self, database: OrdinaryWorkDatabase, ai_tasks: WorkspaceAITaskPort) -> None:
        self.database = database
        self.ai_tasks = ai_tasks

    def start(self, *, homeroom_class: str | None = None) -> dict[str, object]:
        conversation_id = uuid4().hex
        timestamp = _iso()
        with closing(self.database.connect(create=True)) as connection:
            with connection:
                connection.execute(
                    """
                    INSERT INTO intake_conversations (
                        conversation_id, revision, state, homeroom_class,
                        created_at, updated_at
                    ) VALUES (?, 1, 'collecting', ?, ?, ?)
                    """,
                    (conversation_id, str(homeroom_class or "").strip() or None, timestamp, timestamp),
                )
        return self.get(conversation_id)

    def list_recent(self, *, limit: int = 12) -> dict[str, object]:
        if not self.database.exists:
            return {"items": []}
        size = max(1, min(int(limit), 50))
        with closing(self.database.connect()) as connection:
            rows = connection.execute(
                """
                SELECT c.conversation_id, c.revision, c.state, c.homeroom_class,
                       c.created_at, c.updated_at,
                       (SELECT teacher_message FROM intake_turns t
                        WHERE t.conversation_id=c.conversation_id
                        ORDER BY t.sequence LIMIT 1) AS first_message,
                       (SELECT COUNT(*) FROM intake_handoffs h
                        JOIN intake_drafts d ON d.draft_id=h.draft_id
                        WHERE d.conversation_id=c.conversation_id
                          AND h.adoption_state IN ('pending','opened','adoption_started')) AS pending_count
                FROM intake_conversations c
                WHERE c.state != 'abandoned'
                ORDER BY c.updated_at DESC LIMIT ?
                """,
                (size,),
            ).fetchall()
        return {"items": [dict(row) for row in rows]}

    def append_turn(
        self,
        *,
        conversation_id: str,
        expected_revision: int,
        message: str,
        operation_id: str,
    ) -> dict[str, object]:
        self._id(operation_id, "操作编号")
        clean_message = str(message or "").strip()
        if not clean_message or len(clean_message) > 4000:
            raise VaultError("class_teacher_turn_message_invalid", "请填写 1 至 4000 字的内容", status_code=422)
        timestamp = _iso()
        with closing(self.database.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                replay = connection.execute(
                    "SELECT conversation_id, source_revision, teacher_message FROM intake_turns WHERE operation_id=?",
                    (operation_id,),
                ).fetchone()
                if replay is not None:
                    if (
                        str(replay["conversation_id"]) != conversation_id
                        or int(replay["source_revision"]) != int(expected_revision)
                        or str(replay["teacher_message"]) != clean_message
                    ):
                        raise VaultError("class_teacher_operation_conflict", "同一操作编号已用于不同输入", status_code=409)
                    connection.rollback()
                    return self.get(conversation_id)
                row = connection.execute(
                    "SELECT revision, state FROM intake_conversations WHERE conversation_id=?",
                    (conversation_id,),
                ).fetchone()
                if row is None:
                    raise VaultError("class_teacher_conversation_not_found", "会话不存在", status_code=404)
                if int(row["revision"]) != int(expected_revision):
                    raise VaultError("class_teacher_conversation_conflict", "会话已在其他页面更新，请刷新后继续", status_code=409)
                sequence = int(connection.execute(
                    "SELECT COUNT(*) FROM intake_turns WHERE conversation_id=?", (conversation_id,)
                ).fetchone()[0]) + 1
                turn_id = uuid4().hex
                connection.execute(
                    """
                    INSERT INTO intake_turns (
                        turn_id, conversation_id, sequence, operation_id,
                        source_revision, teacher_message, assistant_message,
                        clarification_questions_json, task_id, task_state,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, NULL, '[]', NULL, 'preparing', ?, ?)
                    """,
                    (turn_id, conversation_id, sequence, operation_id, expected_revision, clean_message, timestamp, timestamp),
                )
                connection.execute(
                    "UPDATE intake_conversations SET revision=revision+1, state='ai_running', updated_at=? WHERE conversation_id=?",
                    (timestamp, conversation_id),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

        request = {
            "module": "class_teacher",
            "task_kind": "class_teacher.intake_triage",
            "source_ref": {"kind": "conversation", "id": conversation_id, "revision": str(expected_revision + 1)},
            "context_refs": [{"kind": "turn", "id": turn_id, "revision": "1"}],
            "prompt_contract_version": "class_teacher_triage.v1",
            "model_destination_fingerprint": "configured-workspace-model",
            "return_target": "class_teacher.home",
        }
        try:
            prepared = self.ai_tasks.prepare(operation_id=operation_id, request=request)
            snapshot = self.ai_tasks.dispatch(
                operation_id=operation_id,
                prepared_task_id=prepared.task_id,
                request_fingerprint=prepared.request_fingerprint,
            )
            with closing(self.database.connect()) as connection:
                with connection:
                    connection.execute(
                        "UPDATE intake_turns SET task_id=?, task_state=?, updated_at=? WHERE turn_id=?",
                        (snapshot.task_id, snapshot.state, _iso(), turn_id),
                    )
        except Exception:
            with closing(self.database.connect()) as connection:
                with connection:
                    connection.execute(
                        "UPDATE intake_turns SET task_state='failed_before_dispatch', updated_at=? WHERE turn_id=?",
                        (_iso(), turn_id),
                    )
                    connection.execute(
                        "UPDATE intake_conversations SET state='failed', updated_at=? WHERE conversation_id=?",
                        (_iso(), conversation_id),
                    )
        return self.get(conversation_id)

    def apply_triage_result(
        self,
        *,
        turn_id: str,
        task_id: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        result = parse_triage(payload)
        timestamp = _iso()
        with closing(self.database.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                turn = connection.execute(
                    "SELECT conversation_id, task_id, task_state FROM intake_turns WHERE turn_id=?",
                    (turn_id,),
                ).fetchone()
                if turn is None:
                    raise VaultError("class_teacher_turn_not_found", "会话轮次不存在", status_code=404)
                bound_task = str(turn["task_id"] or "")
                if bound_task and bound_task != task_id:
                    raise VaultError("class_teacher_task_conflict", "任务与会话轮次不匹配", status_code=409)
                conversation_id = str(turn["conversation_id"])
                if str(turn["task_state"]) == "response_persisted":
                    connection.rollback()
                    return self.get(conversation_id)
                existing = connection.execute(
                    "SELECT COUNT(*) FROM intake_drafts WHERE turn_id=?", (turn_id,)
                ).fetchone()[0]
                if int(existing):
                    connection.rollback()
                    return self.get(conversation_id)
                connection.execute(
                    """
                    UPDATE intake_turns SET assistant_message=?,
                        clarification_questions_json=?, task_id=?, task_state='response_persisted',
                        updated_at=? WHERE turn_id=?
                    """,
                    (result.assistant_message, _json(result.clarification_questions), task_id, timestamp, turn_id),
                )
                self._insert_work_items(connection, conversation_id, turn_id, result, timestamp)
                state = "needs_input" if result.clarification_questions and not result.work_items else "handoff_ready"
                connection.execute(
                    "UPDATE intake_conversations SET revision=revision+1, state=?, updated_at=? WHERE conversation_id=?",
                    (state, timestamp, conversation_id),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self.get(conversation_id)

    def mark_task_outcome(
        self,
        *,
        turn_id: str,
        task_id: str,
        task_state: str,
    ) -> dict[str, object]:
        if task_state not in {
            "failed_before_dispatch",
            "result_unknown",
            "invalid_result",
            "cancelled_before_dispatch",
            "cancel_requested",
        }:
            raise VaultError("class_teacher_task_state_invalid", "AI 任务状态无效", status_code=422)
        timestamp = _iso()
        with closing(self.database.connect()) as connection:
            with connection:
                turn = connection.execute(
                    "SELECT conversation_id, task_id, task_state FROM intake_turns WHERE turn_id=?",
                    (turn_id,),
                ).fetchone()
                if turn is None:
                    raise VaultError("class_teacher_turn_not_found", "会话轮次不存在", status_code=404)
                bound_task = str(turn["task_id"] or "")
                if bound_task and task_id and bound_task != task_id:
                    raise VaultError("class_teacher_task_conflict", "任务与会话轮次不匹配", status_code=409)
                if str(turn["task_state"]) == "response_persisted":
                    return self.get(str(turn["conversation_id"]))
                connection.execute(
                    "UPDATE intake_turns SET task_id=COALESCE(task_id, ?), task_state=?, updated_at=? WHERE turn_id=?",
                    (task_id or None, task_state, timestamp, turn_id),
                )
                conversation_state = "abandoned" if task_state == "cancelled_before_dispatch" else "failed"
                connection.execute(
                    "UPDATE intake_conversations SET state=?, updated_at=? WHERE conversation_id=?",
                    (conversation_state, timestamp, str(turn["conversation_id"])),
                )
        return self.get(str(turn["conversation_id"]))

    def manual_route(self, *, turn_id: str, mode: str) -> dict[str, object]:
        if mode not in {"record", "plan_calendar", "sop"}:
            raise VaultError("class_teacher_handling_mode_invalid", "请选择登记、计划或 SOP", status_code=422)
        with closing(self.database.connect()) as connection:
            turn = connection.execute(
                "SELECT conversation_id, teacher_message, task_id, task_state FROM intake_turns WHERE turn_id=?", (turn_id,)
            ).fetchone()
            if turn is None:
                raise VaultError("class_teacher_turn_not_found", "会话轮次不存在", status_code=404)
            if str(turn["task_state"]) not in {
                "failed_before_dispatch", "failed", "invalid_result", "result_unknown",
                "cancelled_before_dispatch", "cancel_requested",
            }:
                raise VaultError("class_teacher_manual_route_unavailable", "当前任务仍在处理或已有可用结果", status_code=409)
        domain = "conflict_safety" if mode == "sop" else "class_operations"
        payload = {
            "contract_version": "class_teacher_triage.v1",
            "assistant_message": "AI 未参与这次整理。已按教师选择保留原文，请在目标页面核对并补全。",
            "clarification_questions": [],
            "work_items": [{
                "work_item_id": f"manual-{uuid4().hex}",
                "domain": domain,
                "primary_mode": mode,
                "secondary_modes": [],
                "intent": "create" if mode != "plan_calendar" else "plan",
                "reason_summary": "教师在模型不可用后手动选择处理方式",
                "subject_refs": [],
                "time_facts": [],
                "safety_level": "teacher_review_required" if mode == "sop" else "normal",
                "missing_fields": ["请核对对象、时间和事实"] if mode == "record" else ["请补全正式保存所需字段"],
                "draft": {"summary": str(turn["teacher_message"]), "manual_routing": True, "steps": []},
            }],
        }
        conversation = self.apply_triage_result(
            turn_id=turn_id,
            task_id=str(turn["task_id"] or f"manual-{turn_id}"),
            payload=payload,
        )
        with closing(self.database.connect()) as connection:
            with connection:
                connection.execute(
                    "UPDATE intake_conversations SET state='manual_routing', updated_at=? WHERE conversation_id=?",
                    (_iso(), str(turn["conversation_id"])),
                )
        return self.get(str(conversation["conversation_id"]))

    def get(self, conversation_id: str) -> dict[str, object]:
        if not self.database.exists:
            raise VaultError("class_teacher_conversation_not_found", "会话不存在", status_code=404)
        with closing(self.database.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM intake_conversations WHERE conversation_id=?", (conversation_id,)
            ).fetchone()
            if row is None:
                raise VaultError("class_teacher_conversation_not_found", "会话不存在", status_code=404)
            turns = connection.execute(
                "SELECT * FROM intake_turns WHERE conversation_id=? ORDER BY sequence", (conversation_id,)
            ).fetchall()
            handoffs = self._handoffs(connection, conversation_id)
        return {
            **dict(row),
            "turns": [self._turn(dict(item)) for item in turns],
            "handoffs": handoffs,
        }

    def open_handoff(self, handoff_id: str) -> dict[str, object]:
        with closing(self.database.connect()) as connection:
            with connection:
                row = self._handoff_row(connection, handoff_id)
                if str(row["adoption_state"]) == "pending":
                    connection.execute(
                        "UPDATE intake_handoffs SET adoption_state='opened', updated_at=? WHERE handoff_id=?",
                        (_iso(), handoff_id),
                    )
                    connection.execute(
                        "UPDATE intake_conversations SET state='draft_opened', updated_at=? WHERE conversation_id=?",
                        (_iso(), str(row["conversation_id"])),
                    )
                return self._draft_view(row)

    def update_draft(
        self,
        *,
        handoff_id: str,
        expected_revision: int,
        content: dict[str, object],
        subject_refs: list[dict[str, str]] | None = None,
    ) -> dict[str, object]:
        encoded_content = _json(content)
        if len(encoded_content.encode("utf-8")) > 64 * 1024:
            raise VaultError("class_teacher_draft_too_large", "草稿内容过长，请缩短后再保存", status_code=422)
        with closing(self.database.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._handoff_row(connection, handoff_id)
                if int(row["revision"]) != int(expected_revision):
                    raise VaultError("class_teacher_draft_conflict", "草稿已在其他页面更新，请刷新后继续", status_code=409)
                stale_rebind = (
                    str(row["state"]) == "stale"
                    and str(row["destination_key"]) == "class_teacher.student.record"
                    and subject_refs is not None
                )
                if (
                    not stale_rebind
                    and (str(row["state"]) != "open" or str(row["adoption_state"]) in {"adopted", "discarded", "stale"})
                ):
                    raise VaultError("class_teacher_draft_not_editable", "这份草稿当前不能修改", status_code=409)
                refs = self._subject_refs(
                    subject_refs if subject_refs is not None else json.loads(str(row["subject_refs_json"]))
                )
                connection.execute(
                    """
                    UPDATE intake_drafts SET revision=revision+1, content_json=?,
                        subject_refs_json=?, state='open', updated_at=? WHERE draft_id=?
                    """,
                    (encoded_content, _json(refs), _iso(), str(row["draft_id"])),
                )
                if stale_rebind:
                    connection.execute(
                        "UPDATE intake_handoffs SET adoption_state='opened', target_revision=NULL, updated_at=? WHERE handoff_id=?",
                        (_iso(), handoff_id),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self.open_handoff(handoff_id)

    def request_draft_revision(
        self,
        *,
        handoff_id: str,
        expected_revision: int,
        instruction: str,
        operation_id: str,
    ) -> dict[str, object]:
        self._id(operation_id, "操作编号")
        clean_instruction = str(instruction or "").strip()
        if not clean_instruction or len(clean_instruction) > 2000:
            raise VaultError("class_teacher_revision_instruction_invalid", "请填写 1 至 2000 字的调整要求", status_code=422)
        timestamp = _iso()
        with closing(self.database.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                replay = connection.execute(
                    "SELECT * FROM intake_draft_revision_requests WHERE operation_id=?",
                    (operation_id,),
                ).fetchone()
                if replay is not None:
                    if (
                        str(replay["handoff_id"]) != handoff_id
                        or int(replay["source_draft_revision"]) != int(expected_revision)
                        or str(replay["instruction"]) != clean_instruction
                    ):
                        raise VaultError("class_teacher_operation_conflict", "同一操作编号已用于不同的草稿调整", status_code=409)
                    request_id = str(replay["request_id"])
                    connection.rollback()
                    return self.get_draft_revision(request_id)
                handoff = self._handoff_row(connection, handoff_id)
                if int(handoff["revision"]) != int(expected_revision):
                    raise VaultError("class_teacher_draft_conflict", "草稿已经变化，请刷新后再调整", status_code=409)
                if str(handoff["state"]) != "open" or str(handoff["adoption_state"]) in {"adopted", "discarded", "stale"}:
                    raise VaultError("class_teacher_draft_not_editable", "这份草稿当前不能由 AI 调整", status_code=409)
                request_id = uuid4().hex
                conversation = connection.execute(
                    "SELECT revision FROM intake_conversations WHERE conversation_id=?",
                    (str(handoff["conversation_id"]),),
                ).fetchone()
                if conversation is None:
                    raise VaultError("class_teacher_conversation_not_found", "会话不存在", status_code=404)
                connection.execute(
                    """
                    INSERT INTO intake_draft_revision_requests (
                        request_id, operation_id, handoff_id, source_draft_revision,
                        instruction, task_id, task_state, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, NULL, 'preparing', ?, ?)
                    """,
                    (request_id, operation_id, handoff_id, expected_revision, clean_instruction, timestamp, timestamp),
                )
                source_revision = int(conversation["revision"])
                turn_id = str(handoff["turn_id"])
                destination_key = str(handoff["destination_key"])
                conversation_id = str(handoff["conversation_id"])
                connection.commit()
            except Exception:
                connection.rollback()
                raise

        request = {
            "module": "class_teacher",
            "task_kind": "class_teacher.draft_revision",
            "source_ref": {"kind": "conversation", "id": conversation_id, "revision": str(source_revision)},
            "context_refs": [
                {"kind": "turn", "id": turn_id, "revision": "1"},
                {"kind": "handoff", "id": handoff_id, "revision": str(expected_revision)},
                {"kind": "draft_revision_request", "id": request_id, "revision": "1"},
            ],
            "prompt_contract_version": "class_teacher_draft_revision.v1",
            "model_destination_fingerprint": "configured-workspace-model",
            "return_target": destination_key,
        }
        try:
            prepared = self.ai_tasks.prepare(operation_id=operation_id, request=request)
            snapshot = self.ai_tasks.dispatch(
                operation_id=operation_id,
                prepared_task_id=prepared.task_id,
                request_fingerprint=prepared.request_fingerprint,
            )
            with closing(self.database.connect()) as connection:
                with connection:
                    connection.execute(
                        "UPDATE intake_draft_revision_requests SET task_id=?, task_state=?, updated_at=? WHERE request_id=?",
                        (snapshot.task_id, snapshot.state, _iso(), request_id),
                    )
        except Exception:
            with closing(self.database.connect()) as connection:
                with connection:
                    connection.execute(
                        "UPDATE intake_draft_revision_requests SET task_state='failed_before_dispatch', updated_at=? WHERE request_id=?",
                        (_iso(), request_id),
                    )
        return self.get_draft_revision(request_id)

    def get_draft_revision(self, request_id: str) -> dict[str, object]:
        self._id(request_id, "草稿调整编号")
        with closing(self.database.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM intake_draft_revision_requests WHERE request_id=?",
                (request_id,),
            ).fetchone()
        if row is None:
            raise VaultError("class_teacher_draft_revision_not_found", "草稿调整任务不存在", status_code=404)
        return {
            "request_id": str(row["request_id"]),
            "handoff_id": str(row["handoff_id"]),
            "source_draft_revision": int(row["source_draft_revision"]),
            "task_id": str(row["task_id"]) if row["task_id"] else None,
            "task_state": str(row["task_state"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }

    def draft_revision_material(self, request_id: str) -> dict[str, object]:
        self._id(request_id, "草稿调整编号")
        with closing(self.database.connect()) as connection:
            request = connection.execute(
                "SELECT * FROM intake_draft_revision_requests WHERE request_id=?",
                (request_id,),
            ).fetchone()
            if request is None:
                raise VaultError("class_teacher_draft_revision_not_found", "草稿调整任务不存在", status_code=404)
            handoff = self._handoff_row(connection, str(request["handoff_id"]))
        return {"request": dict(request), "handoff": self._draft_view(handoff)}

    def apply_draft_revision_result(
        self,
        *,
        request_id: str,
        task_id: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        if set(payload) != {"contract_version", "content"} or payload.get("contract_version") != "class_teacher_draft_revision.v1":
            raise VaultError("class_teacher_draft_revision_invalid_result", "AI 返回的草稿调整无效", status_code=422)
        content = payload.get("content")
        if not isinstance(content, dict):
            raise VaultError("class_teacher_draft_revision_invalid_result", "AI 返回的草稿调整无效", status_code=422)
        encoded_content = _json(content)
        if len(encoded_content.encode("utf-8")) > 64 * 1024:
            raise VaultError("class_teacher_draft_revision_invalid_result", "AI 返回的草稿调整过长", status_code=422)
        with closing(self.database.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                request = connection.execute(
                    "SELECT * FROM intake_draft_revision_requests WHERE request_id=?",
                    (request_id,),
                ).fetchone()
                if request is None:
                    raise VaultError("class_teacher_draft_revision_not_found", "草稿调整任务不存在", status_code=404)
                bound_task = str(request["task_id"] or "")
                if bound_task and bound_task != task_id:
                    raise VaultError("class_teacher_task_conflict", "任务与草稿调整不匹配", status_code=409)
                handoff = self._handoff_row(connection, str(request["handoff_id"]))
                if str(request["task_state"]) == "response_persisted":
                    connection.rollback()
                    return self.open_handoff(str(request["handoff_id"]))
                if int(handoff["revision"]) != int(request["source_draft_revision"]):
                    raise VaultError("class_teacher_draft_conflict", "草稿已在 AI 调整期间变化，请从最新版本重新整理", status_code=409)
                if str(handoff["state"]) != "open" or str(handoff["adoption_state"]) in {"adopted", "discarded", "stale"}:
                    raise VaultError("class_teacher_draft_not_editable", "这份草稿当前不能由 AI 调整", status_code=409)
                timestamp = _iso()
                connection.execute(
                    "UPDATE intake_drafts SET revision=revision+1, content_json=?, updated_at=? WHERE draft_id=?",
                    (encoded_content, timestamp, str(handoff["draft_id"])),
                )
                connection.execute(
                    "UPDATE intake_draft_revision_requests SET task_id=?, task_state='response_persisted', updated_at=? WHERE request_id=?",
                    (task_id, timestamp, request_id),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self.open_handoff(str(request["handoff_id"]))

    def mark_draft_revision_outcome(self, *, request_id: str, task_id: str, task_state: str) -> dict[str, object]:
        if task_state not in {"failed_before_dispatch", "failed", "result_unknown", "invalid_result", "cancelled_before_dispatch", "cancel_requested"}:
            raise VaultError("class_teacher_task_state_invalid", "AI 任务状态无效", status_code=422)
        with closing(self.database.connect()) as connection:
            with connection:
                row = connection.execute(
                    "SELECT task_id, task_state FROM intake_draft_revision_requests WHERE request_id=?",
                    (request_id,),
                ).fetchone()
                if row is None:
                    raise VaultError("class_teacher_draft_revision_not_found", "草稿调整任务不存在", status_code=404)
                if row["task_id"] and task_id and str(row["task_id"]) != task_id:
                    raise VaultError("class_teacher_task_conflict", "任务与草稿调整不匹配", status_code=409)
                if str(row["task_state"]) == "response_persisted":
                    return self.get_draft_revision(request_id)
                connection.execute(
                    "UPDATE intake_draft_revision_requests SET task_id=COALESCE(task_id, ?), task_state=?, updated_at=? WHERE request_id=?",
                    (task_id or None, task_state, _iso(), request_id),
                )
        return self.get_draft_revision(request_id)

    def discard_handoff(self, handoff_id: str) -> dict[str, object]:
        with closing(self.database.connect()) as connection:
            with connection:
                row = self._handoff_row(connection, handoff_id)
                if str(row["adoption_state"]) == "adopted":
                    raise VaultError("class_teacher_handoff_already_adopted", "正式内容已经保存，不能再丢弃交接", status_code=409)
                connection.execute(
                    "UPDATE intake_handoffs SET adoption_state='discarded', updated_at=? WHERE handoff_id=?",
                    (_iso(), handoff_id),
                )
                connection.execute(
                    "UPDATE intake_drafts SET state='discarded', updated_at=? WHERE draft_id=?",
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
        return {"handoff_id": handoff_id, "adoption_state": "discarded"}

    @staticmethod
    def _turn(item: dict[str, Any]) -> dict[str, object]:
        item["clarification_questions"] = json.loads(item.pop("clarification_questions_json"))
        return item

    @staticmethod
    def _insert_work_items(connection: Any, conversation_id: str, turn_id: str, result: TriageResult, timestamp: str) -> None:
        for item in result.work_items:
            draft_id = uuid4().hex
            handoff_id = uuid4().hex
            adoption_id = uuid4().hex
            content = {
                **item.content,
                "reason_summary": item.reason_summary,
                "time_facts": list(item.time_facts),
                "safety_level": item.safety_level,
                "ai_disclaimer": "AI 建议，待教师判断",
            }
            connection.execute(
                """
                INSERT INTO intake_drafts (
                    draft_id, work_item_id, conversation_id, turn_id, domain,
                    handling_mode, intent, destination_key, revision, content_json,
                    subject_refs_json, missing_fields_json, state, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, 'open', ?, ?)
                """,
                (
                    draft_id, item.work_item_id, conversation_id, turn_id, item.domain,
                    item.primary_mode, item.intent, item.destination_key, _json(content),
                    _json(item.subject_refs), _json(item.missing_fields), timestamp, timestamp,
                ),
            )
            connection.execute(
                """
                INSERT INTO intake_handoffs (
                    handoff_id, draft_id, adoption_id, adoption_state,
                    created_at, updated_at
                ) VALUES (?, ?, ?, 'pending', ?, ?)
                """,
                (handoff_id, draft_id, adoption_id, timestamp, timestamp),
            )

    @staticmethod
    def _handoffs(connection: Any, conversation_id: str) -> list[dict[str, object]]:
        rows = connection.execute(
            """
            SELECT h.*, d.work_item_id, d.turn_id, d.domain, d.handling_mode,
                   d.intent, d.destination_key, d.revision AS draft_revision,
                   d.missing_fields_json, d.subject_refs_json,
                   d.state AS draft_state
            FROM intake_handoffs h JOIN intake_drafts d ON d.draft_id=h.draft_id
            WHERE d.conversation_id=? ORDER BY d.created_at, d.work_item_id
            """,
            (conversation_id,),
        ).fetchall()
        results: list[dict[str, object]] = []
        for row in rows:
            missing_fields = json.loads(str(row["missing_fields_json"]))
            subject_refs = json.loads(str(row["subject_refs_json"]))
            destination = str(row["destination_key"])
            results.append({
                **dict(row),
                "missing_fields": missing_fields,
                "subject_ref_count": len(subject_refs),
                "auto_open_allowed": not missing_fields
                and (destination != "class_teacher.student.record" or len(subject_refs) == 1)
                and destination != "class_teacher.affair.sop",
            })
        return results

    @staticmethod
    def _handoff_row(connection: Any, handoff_id: str):
        row = connection.execute(
            """
            SELECT h.*, d.* FROM intake_handoffs h
            JOIN intake_drafts d ON d.draft_id=h.draft_id
            WHERE h.handoff_id=?
            """,
            (handoff_id,),
        ).fetchone()
        if row is None:
            raise VaultError("class_teacher_handoff_not_found", "交接草稿不存在", status_code=404)
        return row

    @staticmethod
    def _draft_view(row: Any) -> dict[str, object]:
        return {
            "contract_version": "teacher_workspace_handoff.v1",
            "handoff_id": str(row["handoff_id"]),
            "work_item_id": str(row["work_item_id"]),
            "conversation_id": str(row["conversation_id"]),
            "turn_id": str(row["turn_id"]),
            "draft_id": str(row["draft_id"]),
            "draft_revision": int(row["revision"]),
            "domain": str(row["domain"]),
            "handling_mode": str(row["handling_mode"]),
            "intent": str(row["intent"]),
            "destination_key": str(row["destination_key"]),
            "adoption_id": str(row["adoption_id"]),
            "adoption_state": str(row["adoption_state"]),
            "content": json.loads(str(row["content_json"])),
            "subject_refs": json.loads(str(row["subject_refs_json"])),
            "missing_fields": json.loads(str(row["missing_fields_json"])),
            "return_context": {"destination_key": "class_teacher.home", "focus_ref": str(row["work_item_id"])},
        }

    @staticmethod
    def _id(value: str, label: str) -> None:
        if _OPAQUE_ID.fullmatch(str(value or "")) is None:
            raise VaultError("class_teacher_identifier_invalid", f"{label}无效", status_code=422)

    @classmethod
    def _subject_refs(cls, values: object) -> list[dict[str, str]]:
        if not isinstance(values, list) or len(values) > 50:
            raise VaultError("class_teacher_subject_refs_invalid", "学生引用无效", status_code=422)
        normalized: list[dict[str, str]] = []
        seen: set[str] = set()
        for value in values:
            if not isinstance(value, dict) or set(value) != {"kind", "id", "revision"} or value.get("kind") != "student":
                raise VaultError("class_teacher_subject_refs_invalid", "学生引用无效", status_code=422)
            subject_id = str(value.get("id") or "")
            revision = str(value.get("revision") or "")
            cls._id(subject_id, "学生引用")
            if not revision or len(revision) > 128 or subject_id in seen:
                raise VaultError("class_teacher_subject_refs_invalid", "学生引用无效", status_code=422)
            seen.add(subject_id)
            normalized.append({"kind": "student", "id": subject_id, "revision": revision})
        return normalized


__all__ = ["ConversationStore"]
