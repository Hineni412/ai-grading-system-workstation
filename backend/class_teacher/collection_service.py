from __future__ import annotations

import json
import re
from contextlib import closing
from datetime import UTC, datetime
from typing import Any, Callable
from uuid import uuid4

from .encrypted_database import EncryptedDatabase
from .errors import VaultError
from .planning_service import PlanningService
from .secure_repository import EncryptedObjectRepository


_COLLECTION_STATUSES = {
    "pending_notice",
    "pending_submission",
    "submitted",
    "needs_review",
    "completed",
}


def _iso() -> str:
    return datetime.now(UTC).isoformat()


class CollectionService:
    """B04 local meeting inbox and encrypted anonymous collection boards."""

    def __init__(
        self,
        database: EncryptedDatabase,
        repository: EncryptedObjectRepository,
        key_provider: Callable[[str], bytes],
        planning: PlanningService,
    ) -> None:
        self.database = database
        self.repository = repository
        self._key_provider = key_provider
        self._planning = planning

    def import_meeting_notes(
        self,
        *,
        token: str,
        operation_id: str,
        raw_text: str,
        reference_at: str | None,
    ) -> dict[str, object]:
        vmk = self._key_provider(token)
        clean_text = str(raw_text or "").strip()
        if not clean_text or len(clean_text) > 20_000:
            raise VaultError(
                "meeting_notes_invalid",
                "会议记录不能为空且不能超过 20000 个字符",
                status_code=422,
            )
        replay = self._idempotent(operation_id, "meeting.inbox.import")
        if replay is not None:
            return self.get_meeting_inbox(
                token=token,
                inbox_id=str(replay["inbox_id"]),
            )
        segments = self._split_notes(clean_text)
        reference = self._planning._parse_reference(reference_at)
        calendar = self._planning._calendar(vmk)
        drafts = [
            self._meeting_draft(
                segment=segment,
                reference=reference,
                calendar=calendar,
            )
            for segment in segments
        ]
        inbox_id = uuid4().hex
        object_id = f"meeting-inbox-{inbox_id}"
        timestamp = _iso()
        payload: dict[str, Any] = {
            "status": "draft",
            "raw_text": clean_text,
            "source_deleted": False,
            "delete_source_after_confirm": True,
            "model_enabled": False,
            "physical_request_count": 0,
            "drafts": drafts,
            "confirmed_plan_ids": [],
            "confirmed_action_ids": [],
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        with closing(self.database.connect()) as connection:
            with connection:
                self.repository.put(
                    connection,
                    vmk=vmk,
                    object_id=object_id,
                    object_type="meeting_inbox",
                    payload=payload,
                )
                self._remember(
                    connection,
                    operation_id,
                    "meeting.inbox.import",
                    {"inbox_id": inbox_id},
                )
        return {"inbox_id": inbox_id, "revision": 1, **payload}

    def list_meeting_inboxes(self, *, token: str) -> dict[str, object]:
        vmk = self._key_provider(token)
        with closing(self.database.connect()) as connection:
            rows = connection.execute(
                """
                SELECT object_id FROM encrypted_objects
                WHERE object_type = 'meeting_inbox'
                ORDER BY updated_at DESC
                """
            ).fetchall()
            items = []
            for row in rows:
                object_id = str(row["object_id"])
                payload, revision = self.repository.get(
                    connection,
                    vmk=vmk,
                    object_id=object_id,
                )
                items.append(
                    {
                        "inbox_id": object_id.removeprefix("meeting-inbox-"),
                        "revision": revision,
                        **payload,
                    }
                )
        return {"items": items}

    def get_meeting_inbox(
        self,
        *,
        token: str,
        inbox_id: str,
    ) -> dict[str, object]:
        vmk = self._key_provider(token)
        object_id = f"meeting-inbox-{inbox_id}"
        with closing(self.database.connect()) as connection:
            payload, revision = self._meeting_payload(
                connection,
                vmk,
                object_id,
            )
        return {"inbox_id": inbox_id, "revision": revision, **payload}

    def update_meeting_inbox(
        self,
        *,
        token: str,
        inbox_id: str,
        operation_id: str,
        revision: int,
        drafts: list[dict[str, object]],
        delete_source_after_confirm: bool,
    ) -> dict[str, object]:
        vmk = self._key_provider(token)
        replay = self._idempotent(operation_id, "meeting.inbox.update")
        if replay is not None:
            return self.get_meeting_inbox(token=token, inbox_id=inbox_id)
        normalized = self._validate_meeting_drafts(drafts)
        object_id = f"meeting-inbox-{inbox_id}"
        with closing(self.database.connect()) as connection:
            with connection:
                payload, current_revision = self._meeting_payload(
                    connection,
                    vmk,
                    object_id,
                )
                if current_revision != revision:
                    self._revision_conflict("会议任务草稿")
                if payload.get("status") != "draft":
                    raise VaultError(
                        "meeting_inbox_not_editable",
                        "只有未确认的会议收件箱可以修改",
                        status_code=409,
                    )
                payload.update(
                    {
                        "drafts": normalized,
                        "delete_source_after_confirm": bool(
                            delete_source_after_confirm
                        ),
                        "updated_at": _iso(),
                    }
                )
                self.repository.put(
                    connection,
                    vmk=vmk,
                    object_id=object_id,
                    object_type="meeting_inbox",
                    payload=payload,
                    expected_revision=revision,
                )
                self._remember(
                    connection,
                    operation_id,
                    "meeting.inbox.update",
                    {"inbox_id": inbox_id},
                )
        return self.get_meeting_inbox(token=token, inbox_id=inbox_id)

    def cancel_meeting_inbox(
        self,
        *,
        token: str,
        inbox_id: str,
        operation_id: str,
        revision: int,
    ) -> dict[str, object]:
        vmk = self._key_provider(token)
        replay = self._idempotent(operation_id, "meeting.inbox.cancel")
        if replay is not None:
            return self.get_meeting_inbox(token=token, inbox_id=inbox_id)
        object_id = f"meeting-inbox-{inbox_id}"
        with closing(self.database.connect()) as connection:
            with connection:
                payload, current_revision = self._meeting_payload(
                    connection,
                    vmk,
                    object_id,
                )
                if current_revision != revision:
                    self._revision_conflict("会议任务草稿")
                if payload.get("status") == "confirmed":
                    raise VaultError(
                        "meeting_inbox_already_confirmed",
                        "会议任务已经正式入账，不能再取消",
                        status_code=409,
                    )
                payload.update(
                    {
                        "status": "cancelled",
                        "raw_text": None,
                        "source_deleted": True,
                        "updated_at": _iso(),
                    }
                )
                self.repository.put(
                    connection,
                    vmk=vmk,
                    object_id=object_id,
                    object_type="meeting_inbox",
                    payload=payload,
                    expected_revision=revision,
                )
                self._remember(
                    connection,
                    operation_id,
                    "meeting.inbox.cancel",
                    {"inbox_id": inbox_id},
                )
        return self.get_meeting_inbox(token=token, inbox_id=inbox_id)

    def confirm_meeting_inbox(
        self,
        *,
        token: str,
        inbox_id: str,
        operation_id: str,
        revision: int,
    ) -> dict[str, object]:
        vmk = self._key_provider(token)
        replay = self._idempotent(operation_id, "meeting.inbox.confirm")
        if replay is not None:
            return {
                **replay,
                "model_enabled": False,
                "physical_request_count": 0,
            }
        object_id = f"meeting-inbox-{inbox_id}"
        with closing(self.database.connect()) as connection:
            with connection:
                payload, current_revision = self._meeting_payload(
                    connection,
                    vmk,
                    object_id,
                )
                if current_revision != revision:
                    self._revision_conflict("会议任务草稿")
                if payload.get("status") != "draft":
                    raise VaultError(
                        "meeting_inbox_not_editable",
                        "只有未确认的会议任务可以正式入账",
                        status_code=409,
                    )
                drafts = self._validate_meeting_drafts(
                    list(payload.get("drafts") or []),
                    require_complete=True,
                )
                if not drafts:
                    raise VaultError(
                        "meeting_drafts_required",
                        "至少保留一项会议任务后再确认",
                        status_code=422,
                    )
                for draft in drafts:
                    if draft["sensitive_findings"]:
                        raise VaultError(
                            "meeting_requires_manual_sop",
                            "会议任务涉及敏感或高风险字段，请改用人工记录或 SOP",
                            status_code=422,
                        )
                    if not draft["final_deadline"]:
                        raise VaultError(
                            "meeting_deadline_unknown",
                            "仍有会议任务缺少最终截止时间",
                            status_code=422,
                        )
                    if any(not item["due_at"] for item in draft["actions"]):
                        raise VaultError(
                            "meeting_action_deadline_unknown",
                            "仍有会议行动缺少截止时间",
                            status_code=422,
                        )
                plan_ids: list[str] = []
                action_ids: list[str] = []
                timestamp = _iso()
                for draft in drafts:
                    plan_id = uuid4().hex
                    plan_ids.append(plan_id)
                    plan_object_id = f"plan-{plan_id}"
                    self.repository.put(
                        connection,
                        vmk=vmk,
                        object_id=plan_object_id,
                        object_type="work_plan",
                        payload={
                            "title": draft["title"],
                            "description": "由本地会议任务收件箱确认生成",
                            "final_deadline": draft["final_deadline"],
                        },
                    )
                    connection.execute(
                        """
                        INSERT INTO work_plans (
                            plan_id, payload_object_id, created_at, updated_at
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (plan_id, plan_object_id, timestamp, timestamp),
                    )
                    by_draft_id: dict[str, str] = {}
                    for action in draft["actions"]:
                        action_id = uuid4().hex
                        action_ids.append(action_id)
                        by_draft_id[str(action["draft_action_id"])] = action_id
                        action_object_id = f"action-{action_id}"
                        self.repository.put(
                            connection,
                            vmk=vmk,
                            object_id=action_object_id,
                            object_type="action_item",
                            payload={
                                "title": action["title"],
                                "details": action["details"],
                                "status": "pending",
                                "due_at": action["due_at"],
                                "waiting_for_kind": None,
                                "review_at": None,
                                "completion_result": None,
                                "completed_at": None,
                                "reopened_count": 0,
                                "transition_history": [
                                    {
                                        "from": None,
                                        "to": "pending",
                                        "at": timestamp,
                                        "reason": "confirmed_from_meeting_inbox",
                                    }
                                ],
                            },
                        )
                        connection.execute(
                            """
                            INSERT INTO actions (
                                action_id, plan_id, payload_object_id,
                                created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?)
                            """,
                            (
                                action_id,
                                plan_id,
                                action_object_id,
                                timestamp,
                                timestamp,
                            ),
                        )
                        connection.execute(
                            """
                            INSERT INTO action_audit_events (
                                event_id, action_id, event_type, created_at
                            ) VALUES (?, ?, 'created.from_meeting_inbox', ?)
                            """,
                            (uuid4().hex, action_id, timestamp),
                        )
                    for action in draft["actions"]:
                        action_id = by_draft_id[str(action["draft_action_id"])]
                        for dependency in action[
                            "depends_on_draft_action_ids"
                        ]:
                            connection.execute(
                                """
                                INSERT INTO action_dependencies (
                                    action_id, depends_on_action_id, created_at
                                ) VALUES (?, ?, ?)
                                """,
                                (
                                    action_id,
                                    by_draft_id[str(dependency)],
                                    timestamp,
                                ),
                            )
                source_deleted = bool(
                    payload.get("delete_source_after_confirm")
                )
                payload.update(
                    {
                        "status": "confirmed",
                        "raw_text": None if source_deleted else payload["raw_text"],
                        "source_deleted": source_deleted,
                        "drafts": drafts,
                        "confirmed_plan_ids": plan_ids,
                        "confirmed_action_ids": action_ids,
                        "updated_at": timestamp,
                    }
                )
                self.repository.put(
                    connection,
                    vmk=vmk,
                    object_id=object_id,
                    object_type="meeting_inbox",
                    payload=payload,
                    expected_revision=revision,
                )
                result = {
                    "inbox_id": inbox_id,
                    "plan_ids": plan_ids,
                    "action_ids": action_ids,
                    "source_deleted": source_deleted,
                }
                self._remember(
                    connection,
                    operation_id,
                    "meeting.inbox.confirm",
                    result,
                )
        return {
            **result,
            "model_enabled": False,
            "physical_request_count": 0,
        }

    def create_board(
        self,
        *,
        token: str,
        operation_id: str,
        action_id: str,
        title: str,
        participant_refs: list[str],
    ) -> dict[str, object]:
        vmk = self._key_provider(token)
        replay = self._idempotent(operation_id, "collection.board.create")
        if replay is not None:
            return self.get_board(
                token=token,
                board_id=str(replay["board_id"]),
            )
        clean_title = self._text(title, "收集板名称", 240)
        participants = [
            self._text(item, "匿名参与引用", 240)
            for item in list(dict.fromkeys(participant_refs))
        ]
        if not participants or len(participants) > 100:
            raise VaultError(
                "collection_participants_invalid",
                "收集板需要 1 至 100 个匿名参与引用",
                status_code=422,
            )
        board_id = uuid4().hex
        object_id = f"collection-board-{board_id}"
        timestamp = _iso()
        payload = {
            "action_id": action_id,
            "title": clean_title,
            "items": [
                {
                    "collection_item_id": uuid4().hex,
                    "participant_ref": participant,
                    "status": "pending_notice",
                    "updated_at": timestamp,
                }
                for participant in participants
            ],
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        with closing(self.database.connect()) as connection:
            with connection:
                if connection.execute(
                    "SELECT 1 FROM actions WHERE action_id = ?",
                    (action_id,),
                ).fetchone() is None:
                    raise VaultError(
                        "action_not_found",
                        "关联行动不存在",
                        status_code=404,
                    )
                self.repository.put(
                    connection,
                    vmk=vmk,
                    object_id=object_id,
                    object_type="collection_board",
                    payload=payload,
                )
                self._remember(
                    connection,
                    operation_id,
                    "collection.board.create",
                    {"board_id": board_id},
                )
        return self._board_response(board_id, 1, payload)

    def list_boards(self, *, token: str) -> dict[str, object]:
        vmk = self._key_provider(token)
        with closing(self.database.connect()) as connection:
            rows = connection.execute(
                """
                SELECT object_id FROM encrypted_objects
                WHERE object_type = 'collection_board'
                ORDER BY updated_at DESC
                """
            ).fetchall()
            items = []
            for row in rows:
                object_id = str(row["object_id"])
                payload, revision = self.repository.get(
                    connection,
                    vmk=vmk,
                    object_id=object_id,
                )
                items.append(
                    self._board_response(
                        object_id.removeprefix("collection-board-"),
                        revision,
                        payload,
                    )
                )
        return {"items": items}

    def get_board(self, *, token: str, board_id: str) -> dict[str, object]:
        vmk = self._key_provider(token)
        with closing(self.database.connect()) as connection:
            payload, revision = self._board_payload(
                connection,
                vmk,
                board_id,
            )
        return self._board_response(board_id, revision, payload)

    def update_collection_item(
        self,
        *,
        token: str,
        board_id: str,
        item_id: str,
        operation_id: str,
        revision: int,
        status: str,
    ) -> dict[str, object]:
        vmk = self._key_provider(token)
        replay = self._idempotent(operation_id, "collection.item.update")
        if replay is not None:
            return self.get_board(token=token, board_id=board_id)
        if status not in _COLLECTION_STATUSES:
            raise VaultError(
                "collection_status_invalid",
                "收集状态无效",
                status_code=422,
            )
        object_id = f"collection-board-{board_id}"
        with closing(self.database.connect()) as connection:
            with connection:
                payload, current_revision = self._board_payload(
                    connection,
                    vmk,
                    board_id,
                )
                if current_revision != revision:
                    self._revision_conflict("收集板")
                items = list(payload.get("items") or [])
                target = next(
                    (
                        item for item in items
                        if str(item.get("collection_item_id")) == item_id
                    ),
                    None,
                )
                if target is None:
                    raise VaultError(
                        "collection_item_not_found",
                        "收集项不存在",
                        status_code=404,
                    )
                target["status"] = status
                target["updated_at"] = _iso()
                payload.update({"items": items, "updated_at": _iso()})
                self.repository.put(
                    connection,
                    vmk=vmk,
                    object_id=object_id,
                    object_type="collection_board",
                    payload=payload,
                    expected_revision=revision,
                )
                self._remember(
                    connection,
                    operation_id,
                    "collection.item.update",
                    {"board_id": board_id},
                )
        return self.get_board(token=token, board_id=board_id)

    def individual_reminder(
        self,
        *,
        token: str,
        board_id: str,
        item_id: str,
    ) -> dict[str, object]:
        board = self.get_board(token=token, board_id=board_id)
        target = next(
            (
                item for item in board["items"]
                if str(item["collection_item_id"]) == item_id
            ),
            None,
        )
        if target is None:
            raise VaultError(
                "collection_item_not_found",
                "收集项不存在",
                status_code=404,
            )
        return {
            "board_id": board_id,
            "collection_item_id": item_id,
            "audience_ref": target["participant_ref"],
            "content": (
                f"请留意“{board['title']}”的提交要求；"
                "如有困难，请单独联系老师。此文案尚未发送。"
            ),
            "basis": "当前收集项状态与教师建立的收集板",
            "unknowns": [],
            "status": "unsent",
            "sent_at": None,
        }

    def _meeting_draft(
        self,
        *,
        segment: str,
        reference: datetime,
        calendar: dict[str, object],
    ) -> dict[str, object]:
        deadline_date, final_deadline, source, deadline_unknowns = (
            self._planning._resolve_deadline(
                raw_input=segment,
                explicit=None,
                reference=reference,
                calendar=calendar,
            )
        )
        findings = self._planning._sensitive_findings(segment)
        template_kind, _template_title, steps = self._planning._template(segment)
        actions, schedule_unknowns, is_late = (
            self._planning._schedule_actions(
                steps=steps,
                final_deadline=final_deadline,
                reference=reference,
                calendar=calendar,
            )
        )
        unknowns = list(
            dict.fromkeys([*deadline_unknowns, *schedule_unknowns])
        )
        if findings:
            unknowns.append("内容命中敏感或高风险字段")
        title = re.sub(r"^[\s\d一二三四五六七八九十、.．()（）-]+", "", segment)
        return {
            "meeting_draft_id": uuid4().hex,
            "title": (title or "会议任务")[:120],
            "objective": segment,
            "deadline_date": (
                deadline_date.isoformat() if deadline_date else None
            ),
            "final_deadline": final_deadline,
            "deadline_source": source,
            "actions": actions,
            "unknowns": unknowns,
            "sensitive_findings": findings,
            "is_late": is_late,
            "template_kind": template_kind,
        }

    def _validate_meeting_drafts(
        self,
        drafts: list[dict[str, object]],
        *,
        require_complete: bool = False,
    ) -> list[dict[str, object]]:
        if len(drafts) > 30:
            raise VaultError(
                "meeting_drafts_too_many",
                "一次会议最多保留 30 项任务",
                status_code=422,
            )
        normalized = []
        identifiers: set[str] = set()
        for draft in drafts:
            draft_id = str(
                draft.get("meeting_draft_id") or uuid4().hex
            ).strip()
            if not draft_id or draft_id in identifiers:
                raise VaultError(
                    "meeting_draft_id_invalid",
                    "会议任务草稿编号重复",
                    status_code=422,
                )
            identifiers.add(draft_id)
            title = self._text(draft.get("title"), "任务名称", 240)
            objective = self._text(
                draft.get("objective"),
                "任务说明",
                4000,
            )
            findings = self._planning._sensitive_findings(
                f"{title}\n{objective}"
            )
            final_deadline = draft.get("final_deadline")
            normalized_deadline = (
                self._planning._parse_exact_deadline(
                    str(final_deadline)
                ).astimezone(UTC).isoformat()
                if final_deadline
                else None
            )
            actions = self._normalize_meeting_actions(
                list(draft.get("actions") or []),
                require_complete=require_complete,
            )
            normalized.append(
                {
                    "meeting_draft_id": draft_id,
                    "title": title,
                    "objective": objective,
                    "deadline_date": (
                        str(draft.get("deadline_date"))
                        if draft.get("deadline_date")
                        else None
                    ),
                    "final_deadline": normalized_deadline,
                    "deadline_source": str(
                        draft.get("deadline_source") or "teacher_edited"
                    ),
                    "actions": actions,
                    "unknowns": [
                        str(item)
                        for item in list(draft.get("unknowns") or [])
                    ],
                    "sensitive_findings": findings,
                    "is_late": bool(draft.get("is_late")),
                    "template_kind": str(
                        draft.get("template_kind") or "general"
                    ),
                }
            )
        return normalized

    def _normalize_meeting_actions(
        self,
        actions: list[dict[str, object]],
        *,
        require_complete: bool,
    ) -> list[dict[str, object]]:
        if not actions or len(actions) > 20:
            raise VaultError(
                "meeting_actions_invalid",
                "每项会议任务需要保留 1 至 20 个行动",
                status_code=422,
            )
        identifiers: set[str] = set()
        normalized = []
        for item in actions:
            draft_action_id = str(
                item.get("draft_action_id") or ""
            ).strip()
            title = self._text(item.get("title"), "行动名称", 240)
            if not draft_action_id or draft_action_id in identifiers:
                raise VaultError(
                    "meeting_action_id_invalid",
                    "会议行动草稿编号重复",
                    status_code=422,
                )
            identifiers.add(draft_action_id)
            due_at = item.get("due_at")
            if require_complete and not due_at:
                raise VaultError(
                    "meeting_action_deadline_unknown",
                    "仍有会议行动缺少截止时间",
                    status_code=422,
                )
            normalized_due = (
                self._planning._parse_exact_deadline(
                    str(due_at)
                ).astimezone(UTC).isoformat()
                if due_at
                else None
            )
            normalized.append(
                {
                    "draft_action_id": draft_action_id,
                    "title": title,
                    "details": (
                        str(item.get("details") or "").strip() or None
                    ),
                    "due_at": normalized_due,
                    "depends_on_draft_action_ids": [
                        str(value)
                        for value in list(
                            item.get("depends_on_draft_action_ids") or []
                        )
                    ],
                }
            )
        for item in normalized:
            dependencies = set(item["depends_on_draft_action_ids"])
            if (
                str(item["draft_action_id"]) in dependencies
                or not dependencies.issubset(identifiers)
            ):
                raise VaultError(
                    "meeting_dependencies_invalid",
                    "会议行动依赖关系无效",
                    status_code=422,
                )
        return normalized

    @staticmethod
    def _split_notes(value: str) -> list[str]:
        segments = [
            segment.strip()
            for segment in re.split(r"[\r\n；;]+", value)
            if segment.strip()
        ]
        if not segments:
            raise VaultError(
                "meeting_notes_invalid",
                "会议记录没有可识别的任务",
                status_code=422,
            )
        if len(segments) > 30:
            raise VaultError(
                "meeting_notes_too_many",
                "一次最多拆分 30 项会议任务",
                status_code=422,
            )
        return segments

    def _meeting_payload(
        self,
        connection: Any,
        vmk: bytes,
        object_id: str,
    ) -> tuple[dict[str, Any], int]:
        row = connection.execute(
            """
            SELECT 1 FROM encrypted_objects
            WHERE object_id = ? AND object_type = 'meeting_inbox'
            """,
            (object_id,),
        ).fetchone()
        if row is None:
            raise VaultError(
                "meeting_inbox_not_found",
                "会议任务收件箱不存在",
                status_code=404,
            )
        return self.repository.get(
            connection,
            vmk=vmk,
            object_id=object_id,
        )

    def _board_payload(
        self,
        connection: Any,
        vmk: bytes,
        board_id: str,
    ) -> tuple[dict[str, Any], int]:
        object_id = f"collection-board-{board_id}"
        row = connection.execute(
            """
            SELECT 1 FROM encrypted_objects
            WHERE object_id = ? AND object_type = 'collection_board'
            """,
            (object_id,),
        ).fetchone()
        if row is None:
            raise VaultError(
                "collection_board_not_found",
                "收集板不存在",
                status_code=404,
            )
        return self.repository.get(
            connection,
            vmk=vmk,
            object_id=object_id,
        )

    @staticmethod
    def _board_response(
        board_id: str,
        revision: int,
        payload: dict[str, Any],
    ) -> dict[str, object]:
        counts = {status: 0 for status in _COLLECTION_STATUSES}
        for item in list(payload.get("items") or []):
            counts[str(item["status"])] += 1
        total = sum(counts.values())
        return {
            "board_id": board_id,
            "revision": revision,
            **payload,
            "total_count": total,
            "counts": counts,
            "group_reminder": {
                "audience": "对应班级群",
                "content": (
                    f"“{payload['title']}”当前还有 "
                    f"{counts['pending_submission'] + counts['pending_notice']} "
                    "份待提交。请按要求完成；如有困难请单独联系老师。"
                    "此文案尚未发送。"
                ),
                "basis": "收集板匿名状态计数",
                "unknowns": [],
                "status": "unsent",
                "sent_at": None,
            },
        }

    def _idempotent(
        self,
        operation_id: str,
        operation_type: str,
    ) -> dict[str, object] | None:
        with closing(self.database.connect()) as connection:
            row = connection.execute(
                """
                SELECT operation_type, result_json
                FROM idempotency_ledger WHERE operation_id = ?
                """,
                (operation_id,),
            ).fetchone()
        if row is None:
            return None
        if str(row["operation_type"]) != operation_type:
            raise VaultError(
                "vault_operation_conflict",
                "此操作编号已经用于另一项操作",
                status_code=409,
            )
        return dict(json.loads(str(row["result_json"])))

    @staticmethod
    def _remember(
        connection: Any,
        operation_id: str,
        operation_type: str,
        result: dict[str, object],
    ) -> None:
        connection.execute(
            """
            INSERT INTO idempotency_ledger (
                operation_id, operation_type, result_json, created_at
            ) VALUES (?, ?, ?, ?)
            """,
            (
                operation_id,
                operation_type,
                json.dumps(result, sort_keys=True),
                _iso(),
            ),
        )

    @staticmethod
    def _text(value: object, label: str, maximum: int) -> str:
        clean = str(value or "").strip()
        if not clean or len(clean) > maximum:
            raise VaultError(
                "collection_text_invalid",
                f"{label}不能为空且不能超过 {maximum} 个字符",
                status_code=422,
            )
        return clean

    @staticmethod
    def _revision_conflict(label: str) -> None:
        raise VaultError(
            "vault_revision_conflict",
            f"{label}已经变化，请刷新后再操作",
            status_code=409,
        )


__all__ = ["CollectionService"]
