from __future__ import annotations

import json
import re
from contextlib import closing
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Callable
from uuid import uuid4
from zoneinfo import ZoneInfo

from .encrypted_database import EncryptedDatabase
from .errors import VaultError
from .secure_repository import EncryptedObjectRepository


_LOCAL_ZONE = ZoneInfo("Asia/Shanghai")
_WEEKDAY = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
_PROHIBITED_TERMS = (
    "诊断",
    "抑郁",
    "焦虑",
    "欺凌",
    "伤害",
    "自杀",
    "自伤",
    "家庭暴力",
    "性侵",
    "惩戒",
)
_NON_NAME_PERSON_PREFIXES = (
    "全体", "所有", "部分", "每位", "相关", "本班", "班级", "学校", "在校", "返校",
    "安排", "组织", "提醒", "帮助", "引导", "要求", "参与", "值日", "值周", "新生",
    "全班", "各位", "通知",
)


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat()


class PlanningService:
    """Local-only B03 planning drafts. This service has no model gateway."""

    model_enabled = False

    def __init__(
        self,
        database: EncryptedDatabase,
        repository: EncryptedObjectRepository,
        key_provider: Callable[[str], bytes],
    ) -> None:
        self.database = database
        self.repository = repository
        self._key_provider = key_provider

    def create_draft(
        self,
        *,
        token: str,
        operation_id: str,
        raw_input: str,
        reference_at: str | None,
        final_deadline: str | None,
    ) -> dict[str, object]:
        vmk = self._key_provider(token)
        clean_input = str(raw_input or "").strip()
        if not clean_input:
            raise VaultError(
                "planning_input_required",
                "请先写下需要安排的事情",
                status_code=422,
            )
        if len(clean_input) > 4000:
            raise VaultError(
                "planning_input_too_long",
                "任务说明过长，请缩短后再试",
                status_code=422,
            )
        replay = self._idempotent(operation_id, "planning.draft.create")
        if replay is not None:
            return self.get_draft(
                token=token,
                draft_id=str(replay["draft_id"]),
            )

        reference = self._parse_reference(reference_at)
        calendar = self._calendar(vmk)
        deadline_date, normalized_deadline, deadline_source, deadline_unknowns = (
            self._resolve_deadline(
                raw_input=clean_input,
                explicit=final_deadline,
                reference=reference,
                calendar=calendar,
            )
        )
        findings = self._sensitive_findings(clean_input)
        template_kind, plan_title, steps = self._template(clean_input)
        actions, schedule_unknowns, is_late = self._schedule_actions(
            steps=steps,
            final_deadline=normalized_deadline,
            reference=reference,
            calendar=calendar,
        )
        unknowns = list(dict.fromkeys([*deadline_unknowns, *schedule_unknowns]))
        if findings:
            unknowns.append("内容命中敏感或高风险字段，普通规划不能直接确认")

        draft_id = uuid4().hex
        object_id = f"planning-draft-{draft_id}"
        timestamp = _iso()
        payload: dict[str, Any] = {
            "status": "draft",
            "raw_input": clean_input,
            "reference_at": reference.astimezone(UTC).isoformat(),
            "template_kind": template_kind,
            "plan_title": plan_title,
            "deadline_date": deadline_date.isoformat() if deadline_date else None,
            "final_deadline": normalized_deadline,
            "deadline_source": deadline_source,
            "actions": actions,
            "communication_drafts": [
                {
                    "audience": "由教师选择",
                    "content": self._communication_text(
                        template_kind,
                        deadline_date,
                        normalized_deadline,
                    ),
                    "basis": "本地固定模板与教师输入的截止要求",
                    "unknowns": unknowns,
                    "status": "unsent",
                    "sent_at": None,
                }
            ],
            "sensitive_findings": findings,
            "unknowns": unknowns,
            "is_late": is_late,
            "send_preview": {
                "model_enabled": False,
                "ready_to_send": False,
                "sent": False,
                "physical_request_count": 0,
                "summary": "外部模型已关闭；没有向任何外部服务发送内容",
                "excluded_field_kinds": findings,
            },
            "confirmed_plan_id": None,
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
                    object_type="planning_draft",
                    payload=payload,
                )
                self._remember(
                    connection,
                    operation_id,
                    "planning.draft.create",
                    {"draft_id": draft_id},
                )
        return {"draft_id": draft_id, "revision": 1, **payload}

    def get_draft(self, *, token: str, draft_id: str) -> dict[str, object]:
        vmk = self._key_provider(token)
        object_id = f"planning-draft-{draft_id}"
        with closing(self.database.connect()) as connection:
            row = connection.execute(
                """
                SELECT created_at, updated_at
                FROM encrypted_objects
                WHERE object_id = ? AND object_type = 'planning_draft'
                """,
                (object_id,),
            ).fetchone()
            if row is None:
                raise VaultError(
                    "planning_draft_not_found",
                    "规划草稿不存在",
                    status_code=404,
                )
            payload, revision = self.repository.get(
                connection,
                vmk=vmk,
                object_id=object_id,
            )
        return {"draft_id": draft_id, "revision": revision, **payload}

    def list_drafts(self, *, token: str) -> dict[str, object]:
        vmk = self._key_provider(token)
        with closing(self.database.connect()) as connection:
            rows = connection.execute(
                """
                SELECT object_id
                FROM encrypted_objects
                WHERE object_type = 'planning_draft'
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
                        "draft_id": object_id.removeprefix("planning-draft-"),
                        "revision": revision,
                        **payload,
                    }
                )
        return {"items": items}

    def cancel_draft(
        self,
        *,
        token: str,
        draft_id: str,
        operation_id: str,
        revision: int,
    ) -> dict[str, object]:
        vmk = self._key_provider(token)
        replay = self._idempotent(operation_id, "planning.draft.cancel")
        if replay is not None:
            return self.get_draft(token=token, draft_id=draft_id)
        object_id = f"planning-draft-{draft_id}"
        with closing(self.database.connect()) as connection:
            with connection:
                payload, current_revision = self.repository.get(
                    connection,
                    vmk=vmk,
                    object_id=object_id,
                )
                if current_revision != revision:
                    self._revision_conflict()
                if payload.get("status") == "confirmed":
                    raise VaultError(
                        "planning_draft_already_confirmed",
                        "规划草稿已经正式入账，不能再取消",
                        status_code=409,
                    )
                payload["status"] = "cancelled"
                payload["updated_at"] = _iso()
                self.repository.put(
                    connection,
                    vmk=vmk,
                    object_id=object_id,
                    object_type="planning_draft",
                    payload=payload,
                    expected_revision=revision,
                )
                self._remember(
                    connection,
                    operation_id,
                    "planning.draft.cancel",
                    {"draft_id": draft_id},
                )
        return self.get_draft(token=token, draft_id=draft_id)

    def confirm_draft(
        self,
        *,
        token: str,
        draft_id: str,
        operation_id: str,
        revision: int,
        plan_title: str,
        actions: list[dict[str, object]],
        transaction_hook: Callable[[Any, bytes, str], None] | None = None,
    ) -> dict[str, object]:
        vmk = self._key_provider(token)
        replay = self._idempotent(operation_id, "planning.draft.confirm")
        if replay is not None:
            return {
                **replay,
                "physical_request_count": 0,
                "model_enabled": False,
            }
        clean_title = str(plan_title or "").strip()
        if not clean_title or len(clean_title) > 240:
            raise VaultError(
                "planning_title_invalid",
                "工作目标名称不能为空且不能超过 240 个字符",
                status_code=422,
            )
        if not actions or len(actions) > 20:
            raise VaultError(
                "planning_actions_invalid",
                "请保留 1 至 20 个行动后再确认",
                status_code=422,
            )

        object_id = f"planning-draft-{draft_id}"
        with closing(self.database.connect()) as connection:
            with connection:
                payload, current_revision = self.repository.get(
                    connection,
                    vmk=vmk,
                    object_id=object_id,
                )
                if current_revision != revision:
                    self._revision_conflict()
                if payload.get("status") != "draft":
                    raise VaultError(
                        "planning_draft_not_editable",
                        "只有未确认草稿可以正式入账",
                        status_code=409,
                    )
                if payload.get("sensitive_findings"):
                    raise VaultError(
                        "planning_requires_manual_sop",
                        "内容涉及敏感或高风险字段，请改用人工记录或后续 SOP 流程",
                        status_code=422,
                    )
                final_deadline = payload.get("final_deadline")
                if not final_deadline:
                    raise VaultError(
                        "planning_deadline_unknown",
                        "截止时间仍不完整，请先填写精确日期和时间",
                        status_code=422,
                    )

                normalized_actions = self._validate_confirmed_actions(actions)
                plan_id = uuid4().hex
                plan_object_id = f"plan-{plan_id}"
                timestamp = _iso()
                self.repository.put(
                    connection,
                    vmk=vmk,
                    object_id=plan_object_id,
                    object_type="work_plan",
                    payload={
                        "title": clean_title,
                        "description": "由本地规划草稿确认生成",
                        "final_deadline": final_deadline,
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

                action_ids: list[str] = []
                action_id_by_draft: dict[str, str] = {}
                for item in normalized_actions:
                    action_id = uuid4().hex
                    action_ids.append(action_id)
                    action_id_by_draft[str(item["draft_action_id"])] = action_id
                    action_object_id = f"action-{action_id}"
                    action_payload = {
                        "title": item["title"],
                        "details": item["details"],
                        "status": "pending",
                        "due_at": item["due_at"],
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
                                "reason": "confirmed_from_local_planning_draft",
                            }
                        ],
                    }
                    self.repository.put(
                        connection,
                        vmk=vmk,
                        object_id=action_object_id,
                        object_type="action_item",
                        payload=action_payload,
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
                        ) VALUES (?, ?, 'created.from_local_draft', ?)
                        """,
                        (uuid4().hex, action_id, timestamp),
                    )

                for item in normalized_actions:
                    action_id = action_id_by_draft[str(item["draft_action_id"])]
                    for dependency in item["depends_on_draft_action_ids"]:
                        connection.execute(
                            """
                            INSERT INTO action_dependencies (
                                action_id, depends_on_action_id, created_at
                            ) VALUES (?, ?, ?)
                            """,
                            (
                                action_id,
                                action_id_by_draft[str(dependency)],
                                timestamp,
                            ),
                        )

                communication_ids: list[str] = []
                communication_action_id = action_ids[0]
                for draft in list(payload.get("communication_drafts") or []):
                    communication_id = uuid4().hex
                    communication_ids.append(communication_id)
                    communication_object_id = f"communication-{communication_id}"
                    communication_payload = {
                        **dict(draft),
                        "status": "unsent",
                        "sent_at": None,
                        "source_planning_draft_id": draft_id,
                    }
                    self.repository.put(
                        connection,
                        vmk=vmk,
                        object_id=communication_object_id,
                        object_type="communication_draft",
                        payload=communication_payload,
                    )
                    connection.execute(
                        """
                        INSERT INTO communication_drafts (
                            draft_id, action_id, payload_object_id,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            communication_id,
                            communication_action_id,
                            communication_object_id,
                            timestamp,
                            timestamp,
                        ),
                    )

                payload.update(
                    {
                        "status": "confirmed",
                        "plan_title": clean_title,
                        "actions": normalized_actions,
                        "confirmed_plan_id": plan_id,
                        "confirmed_action_ids": action_ids,
                        "updated_at": timestamp,
                    }
                )
                self.repository.put(
                    connection,
                    vmk=vmk,
                    object_id=object_id,
                    object_type="planning_draft",
                    payload=payload,
                    expected_revision=revision,
                )
                result = {
                    "draft_id": draft_id,
                    "plan_id": plan_id,
                    "action_ids": action_ids,
                    "communication_draft_ids": communication_ids,
                }
                self._remember(
                    connection,
                    operation_id,
                    "planning.draft.confirm",
                    result,
                )
                if transaction_hook is not None:
                    transaction_hook(connection, vmk, plan_id)
        return {
            **result,
            "physical_request_count": 0,
            "model_enabled": False,
        }

    def _calendar(self, vmk: bytes) -> dict[str, object]:
        with closing(self.database.connect()) as connection:
            try:
                payload, _revision = self.repository.get(
                    connection,
                    vmk=vmk,
                    object_id="system-school-calendar",
                )
                return payload
            except VaultError as exc:
                if exc.code != "vault_object_not_found":
                    raise
        return {
            "school_day_end": None,
            "locked_dates": [],
            "working_weekdays": None,
        }

    @staticmethod
    def _parse_reference(value: str | None) -> datetime:
        if not value:
            return _now().astimezone(_LOCAL_ZONE)
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise VaultError(
                "planning_reference_invalid",
                "参考时间无效",
                status_code=422,
            ) from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_LOCAL_ZONE)
        return parsed.astimezone(_LOCAL_ZONE)

    def _resolve_deadline(
        self,
        *,
        raw_input: str,
        explicit: str | None,
        reference: datetime,
        calendar: dict[str, object],
    ) -> tuple[date | None, str | None, str, list[str]]:
        if explicit:
            parsed = self._parse_exact_deadline(explicit)
            return (
                parsed.astimezone(_LOCAL_ZONE).date(),
                parsed.astimezone(UTC).isoformat(),
                "teacher_exact_datetime",
                [],
            )

        deadline_date: date | None = None
        source = "unknown"
        iso_match = re.search(r"(?<!\d)(20\d{2})-(\d{1,2})-(\d{1,2})(?!\d)", raw_input)
        chinese_match = re.search(
            r"(?<!\d)(20\d{2})年(\d{1,2})月(\d{1,2})日",
            raw_input,
        )
        relative_match = re.search(r"(下周|本周|这周)([一二三四五六日天])", raw_input)
        try:
            if iso_match:
                deadline_date = date(*map(int, iso_match.groups()))
                source = "local_explicit_date"
            elif chinese_match:
                deadline_date = date(*map(int, chinese_match.groups()))
                source = "local_explicit_date"
            elif relative_match:
                monday = reference.date() - timedelta(days=reference.weekday())
                week_offset = 7 if relative_match.group(1) == "下周" else 0
                deadline_date = (
                    monday
                    + timedelta(days=week_offset + _WEEKDAY[relative_match.group(2)])
                )
                source = "local_relative_date"
        except ValueError as exc:
            raise VaultError(
                "planning_deadline_invalid",
                "任务中的截止日期无效",
                status_code=422,
            ) from exc

        if deadline_date is None:
            return None, None, source, ["未识别到明确截止日期"]
        school_day_end = calendar.get("school_day_end")
        if not school_day_end:
            return (
                deadline_date,
                None,
                source,
                ["已识别截止日期，但学校放学时间尚未配置"],
            )
        deadline_time = time.fromisoformat(str(school_day_end))
        deadline = datetime.combine(
            deadline_date,
            deadline_time,
            tzinfo=_LOCAL_ZONE,
        )
        return deadline_date, deadline.astimezone(UTC).isoformat(), source, []

    @staticmethod
    def _parse_exact_deadline(value: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError as exc:
            raise VaultError(
                "planning_deadline_invalid",
                "精确截止时间无效",
                status_code=422,
            ) from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_LOCAL_ZONE)
        return parsed

    @staticmethod
    def _sensitive_findings(value: str) -> list[str]:
        findings = [
            f"高风险词：{term}"
            for term in _PROHIBITED_TERMS
            if term in value
        ]
        if re.search(r"(?<!\d)1[3-9]\d{9}(?!\d)", value):
            findings.append("手机号")
        if re.search(r"(?<!\d)\d{17}[\dXx](?!\d)", value):
            findings.append("身份证号")
        possible_people = re.finditer(
            r"([\u4e00-\u9fff]{2,4})(同学|家长)",
            value,
        )
        if any(
            not any(match.group(1).endswith(prefix) for prefix in _NON_NAME_PERSON_PREFIXES)
            for match in possible_people
        ):
            findings.append("可能包含姓名")
        return list(dict.fromkeys(findings))

    @staticmethod
    def _template(
        value: str,
    ) -> tuple[str, str, list[tuple[str, str, int]]]:
        if "回执" in value:
            return (
                "receipt",
                "回执收集与上报",
                [
                    ("确认回执要求", "核对提交格式、对象和最终上报要求", 5),
                    ("发出回执通知", "文案仍需教师检查并手工发送", 4),
                    ("中途核对回收情况", "仅记录匿名总数，不在群内公开未交姓名", 2),
                    ("完成最终收齐与核对", "确认数量和材料是否一致", 1),
                    ("完成上报并记录结果", "保存人工确认的上报结果", 0),
                ],
            )
        if "会议" in value or "开会" in value:
            return (
                "meeting",
                "会议准备与跟进",
                [
                    ("确认会议目标和对象", "核对会议范围和需要形成的结果", 3),
                    ("准备会议材料", "只整理本次会议需要的材料", 2),
                    ("完成会前确认", "确认时间、地点和参与对象", 1),
                    ("记录会议结果与后续行动", "会议结束后由教师确认结果", 0),
                ],
            )
        return (
            "general",
            "普通任务安排",
            [
                ("确认任务要求", "明确对象、交付物和完成标准", 3),
                ("推进主要工作", "按已确认要求执行", 1),
                ("检查并记录完成结果", "由教师确认后完成", 0),
            ],
        )

    def _schedule_actions(
        self,
        *,
        steps: list[tuple[str, str, int]],
        final_deadline: str | None,
        reference: datetime,
        calendar: dict[str, object],
    ) -> tuple[list[dict[str, object]], list[str], bool]:
        weekdays = calendar.get("working_weekdays")
        school_day_end = calendar.get("school_day_end")
        if not final_deadline or not weekdays or not school_day_end:
            unknowns = []
            if not weekdays:
                unknowns.append("学校工作日尚未配置，未倒排行动时间")
            if not school_day_end:
                unknowns.append("学校放学时间尚未配置，未倒排行动时间")
            return (
                [
                    {
                        "draft_action_id": f"step-{index + 1}",
                        "title": title,
                        "details": details,
                        "due_at": None,
                        "depends_on_draft_action_ids": (
                            [] if index == 0 else [f"step-{index}"]
                        ),
                    }
                    for index, (title, details, _offset) in enumerate(steps)
                ],
                unknowns,
                False,
            )

        locked = {
            date.fromisoformat(str(item))
            for item in list(calendar.get("locked_dates") or [])
        }
        working = {int(item) - 1 for item in list(weekdays)}
        deadline = datetime.fromisoformat(final_deadline).astimezone(_LOCAL_ZONE)
        end_time = time.fromisoformat(str(school_day_end))
        actions: list[dict[str, object]] = []
        late = False
        for index, (title, details, offset) in enumerate(steps):
            due_date = self._subtract_workdays(
                deadline.date(),
                offset,
                working,
                locked,
            )
            due = datetime.combine(due_date, end_time, tzinfo=_LOCAL_ZONE)
            late = late or due < reference
            actions.append(
                {
                    "draft_action_id": f"step-{index + 1}",
                    "title": title,
                    "details": details,
                    "due_at": due.astimezone(UTC).isoformat(),
                    "depends_on_draft_action_ids": (
                        [] if index == 0 else [f"step-{index}"]
                    ),
                }
            )
        return actions, [], late

    @staticmethod
    def _subtract_workdays(
        start: date,
        count: int,
        working: set[int],
        locked: set[date],
    ) -> date:
        cursor = start
        remaining = count
        while remaining > 0:
            cursor -= timedelta(days=1)
            if cursor.weekday() in working and cursor not in locked:
                remaining -= 1
        return cursor

    @staticmethod
    def _communication_text(
        template_kind: str,
        deadline_date: date | None,
        final_deadline: str | None,
    ) -> str:
        deadline = deadline_date.isoformat() if deadline_date else "待教师补充"
        if template_kind == "receipt":
            return (
                f"请按学校要求准备并提交本次回执，截止日期：{deadline}。"
                "如有困难，请单独联系老师。此文案尚未发送。"
            )
        if template_kind == "meeting":
            return (
                f"请留意本次会议安排，目标日期：{deadline}。"
                "具体时间、地点和对象仍需教师补充；此文案尚未发送。"
            )
        suffix = "" if final_deadline else "精确时间仍需教师补充；"
        return f"请按要求完成本次任务，目标日期：{deadline}。{suffix}此文案尚未发送。"

    def _validate_confirmed_actions(
        self,
        actions: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        normalized: list[dict[str, object]] = []
        identifiers: set[str] = set()
        for item in actions:
            draft_action_id = str(item.get("draft_action_id") or "").strip()
            title = str(item.get("title") or "").strip()
            details = str(item.get("details") or "").strip() or None
            due_at = item.get("due_at")
            dependencies = [
                str(value)
                for value in list(item.get("depends_on_draft_action_ids") or [])
            ]
            if (
                not draft_action_id
                or draft_action_id in identifiers
                or not title
                or len(title) > 240
            ):
                raise VaultError(
                    "planning_actions_invalid",
                    "行动名称或草稿编号无效",
                    status_code=422,
                )
            if not due_at:
                raise VaultError(
                    "planning_action_deadline_unknown",
                    "仍有行动缺少截止时间，请补充后再确认",
                    status_code=422,
                )
            normalized_due = self._parse_exact_deadline(str(due_at)).astimezone(
                UTC
            ).isoformat()
            identifiers.add(draft_action_id)
            normalized.append(
                {
                    "draft_action_id": draft_action_id,
                    "title": title,
                    "details": details,
                    "due_at": normalized_due,
                    "depends_on_draft_action_ids": dependencies,
                }
            )
        for item in normalized:
            dependencies = set(item["depends_on_draft_action_ids"])
            if (
                str(item["draft_action_id"]) in dependencies
                or not dependencies.issubset(identifiers)
            ):
                raise VaultError(
                    "planning_dependencies_invalid",
                    "行动依赖关系无效",
                    status_code=422,
                )
        return normalized

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
    def _revision_conflict() -> None:
        raise VaultError(
            "vault_revision_conflict",
            "规划草稿已经变化，请刷新后再操作",
            status_code=409,
        )


__all__ = ["PlanningService"]
