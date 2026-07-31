from __future__ import annotations

import json
from contextlib import closing
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Callable
from uuid import uuid4
from zoneinfo import ZoneInfo

from .encrypted_database import EncryptedDatabase
from .errors import VaultError
from .secure_repository import EncryptedObjectRepository


_LOCAL_ZONE = ZoneInfo("Asia/Shanghai")
_ACTIVE_STATUSES = {
    "pending",
    "in_progress",
    "waiting",
    "partially_completed",
}
_STATUSES = _ACTIVE_STATUSES | {"completed", "cancelled", "superseded"}


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat()


def _normalize_datetime(value: str | None, field: str) -> str | None:
    if value is None or not str(value).strip():
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_LOCAL_ZONE)
        return parsed.astimezone(UTC).isoformat()
    except ValueError as exc:
        raise VaultError(
            "action_datetime_invalid",
            f"{field}不是有效日期时间",
            status_code=422,
        ) from exc


class ActionLedgerService:
    def __init__(
        self,
        database: EncryptedDatabase,
        repository: EncryptedObjectRepository,
        key_provider: Callable[[str], bytes],
    ) -> None:
        self.database = database
        self.repository = repository
        self._key_provider = key_provider

    def create_plan(
        self,
        *,
        token: str,
        operation_id: str,
        title: str,
        description: str | None,
        final_deadline: str | None,
    ) -> dict[str, object]:
        vmk = self._key_provider(token)
        clean_title = self._required_text(title, "目标名称")
        deadline = _normalize_datetime(final_deadline, "最终截止时间")
        replay = self._idempotent(operation_id, "action.plan.create")
        if replay is not None:
            return self.get_plan(token=token, plan_id=str(replay["plan_id"]))
        plan_id = uuid4().hex
        object_id = f"plan-{plan_id}"
        timestamp = _iso()
        payload = {
            "title": clean_title,
            "description": str(description or "").strip() or None,
            "final_deadline": deadline,
        }
        with closing(self.database.connect()) as connection:
            with connection:
                self.repository.put(
                    connection,
                    vmk=vmk,
                    object_id=object_id,
                    object_type="work_plan",
                    payload=payload,
                )
                connection.execute(
                    """
                    INSERT INTO work_plans (
                        plan_id, payload_object_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (plan_id, object_id, timestamp, timestamp),
                )
                result = {
                    "plan_id": plan_id,
                    "revision": 1,
                    **payload,
                    "created_at": timestamp,
                    "updated_at": timestamp,
                }
                self._remember(
                    connection,
                    operation_id,
                    "action.plan.create",
                    {"plan_id": plan_id},
                )
        return result

    def get_plan(self, *, token: str, plan_id: str) -> dict[str, object]:
        vmk = self._key_provider(token)
        with closing(self.database.connect()) as connection:
            row = connection.execute(
                """
                SELECT plan_id, payload_object_id, created_at, updated_at
                FROM work_plans WHERE plan_id = ?
                """,
                (plan_id,),
            ).fetchone()
            if row is None:
                raise VaultError(
                    "work_plan_not_found",
                    "工作目标不存在",
                    status_code=404,
                )
            return self._plan_from_row(connection, vmk, row)

    def update_plan(
        self,
        *,
        token: str,
        plan_id: str,
        operation_id: str,
        revision: int,
        title: str,
        description: str | None,
        final_deadline: str | None,
    ) -> dict[str, object]:
        vmk = self._key_provider(token)
        clean_title = self._required_text(title, "目标名称")
        normalized_deadline = _normalize_datetime(
            final_deadline,
            "最终截止时间",
        )
        replay = self._idempotent(operation_id, "action.plan.update")
        if replay is not None:
            return self.get_plan(token=token, plan_id=plan_id)
        with closing(self.database.connect()) as connection:
            with connection:
                row = connection.execute(
                    """
                    SELECT plan_id, payload_object_id
                    FROM work_plans WHERE plan_id = ?
                    """,
                    (plan_id,),
                ).fetchone()
                if row is None:
                    raise VaultError(
                        "work_plan_not_found",
                        "工作目标不存在",
                        status_code=404,
                    )
                payload, current_revision = self.repository.get(
                    connection,
                    vmk=vmk,
                    object_id=str(row["payload_object_id"]),
                )
                if current_revision != revision:
                    raise VaultError(
                        "vault_revision_conflict",
                        "工作目标已经变化，请刷新后再修改",
                        status_code=409,
                    )
                previous_deadline = payload.get("final_deadline")
                payload.update(
                    {
                        "title": clean_title,
                        "description": str(description or "").strip() or None,
                        "final_deadline": normalized_deadline,
                    }
                )
                next_revision = self.repository.put(
                    connection,
                    vmk=vmk,
                    object_id=str(row["payload_object_id"]),
                    object_type="work_plan",
                    payload=payload,
                    expected_revision=revision,
                )
                if previous_deadline and normalized_deadline:
                    shift = (
                        datetime.fromisoformat(normalized_deadline)
                        - datetime.fromisoformat(str(previous_deadline))
                    )
                    if shift:
                        self._shift_active_actions(
                            connection,
                            vmk,
                            plan_id,
                            shift,
                        )
                connection.execute(
                    "UPDATE work_plans SET updated_at = ? WHERE plan_id = ?",
                    (_iso(), plan_id),
                )
                self._remember(
                    connection,
                    operation_id,
                    "action.plan.update",
                    {"plan_id": plan_id, "revision": next_revision},
                )
        return self.get_plan(token=token, plan_id=plan_id)

    def list_plans(self, *, token: str) -> dict[str, object]:
        vmk = self._key_provider(token)
        with closing(self.database.connect()) as connection:
            rows = connection.execute(
                """
                SELECT plan_id, payload_object_id, created_at, updated_at
                FROM work_plans
                ORDER BY created_at DESC
                """
            ).fetchall()
            items = [
                self._plan_from_row(connection, vmk, row)
                for row in rows
            ]
        return {"items": items}

    def create_action(
        self,
        *,
        token: str,
        operation_id: str,
        plan_id: str,
        title: str,
        details: str | None,
        due_at: str | None,
        depends_on_action_ids: list[str],
    ) -> dict[str, object]:
        vmk = self._key_provider(token)
        clean_title = self._required_text(title, "行动名称")
        normalized_due = _normalize_datetime(due_at, "截止时间")
        replay = self._idempotent(operation_id, "action.create")
        if replay is not None:
            return self.get_action(token=token, action_id=str(replay["action_id"]))
        action_id = uuid4().hex
        object_id = f"action-{action_id}"
        timestamp = _iso()
        payload = {
            "title": clean_title,
            "details": str(details or "").strip() or None,
            "status": "pending",
            "due_at": normalized_due,
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
                    "reason": None,
                }
            ],
        }
        dependencies = list(dict.fromkeys(depends_on_action_ids))
        if action_id in dependencies:
            raise VaultError(
                "action_dependency_invalid",
                "行动不能依赖自身",
                status_code=422,
            )
        with closing(self.database.connect()) as connection:
            with connection:
                if connection.execute(
                    "SELECT 1 FROM work_plans WHERE plan_id = ?",
                    (plan_id,),
                ).fetchone() is None:
                    raise VaultError(
                        "work_plan_not_found",
                        "工作目标不存在",
                        status_code=404,
                    )
                if dependencies:
                    found = {
                        str(row[0])
                        for row in connection.execute(
                            f"""
                            SELECT action_id FROM actions
                            WHERE action_id IN ({','.join('?' for _ in dependencies)})
                            """,
                            dependencies,
                        )
                    }
                    if found != set(dependencies):
                        raise VaultError(
                            "action_dependency_invalid",
                            "依赖的行动不存在",
                            status_code=422,
                        )
                self.repository.put(
                    connection,
                    vmk=vmk,
                    object_id=object_id,
                    object_type="action_item",
                    payload=payload,
                )
                connection.execute(
                    """
                    INSERT INTO actions (
                        action_id, plan_id, payload_object_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (action_id, plan_id, object_id, timestamp, timestamp),
                )
                connection.executemany(
                    """
                    INSERT INTO action_dependencies (
                        action_id, depends_on_action_id, created_at
                    ) VALUES (?, ?, ?)
                    """,
                    [
                        (action_id, dependency, timestamp)
                        for dependency in dependencies
                    ],
                )
                self._event(connection, action_id, "created")
                self._remember(
                    connection,
                    operation_id,
                    "action.create",
                    {"action_id": action_id},
                )
        return self.get_action(token=token, action_id=action_id)

    def get_action(self, *, token: str, action_id: str) -> dict[str, object]:
        vmk = self._key_provider(token)
        with closing(self.database.connect()) as connection:
            row = connection.execute(
                """
                SELECT action_id, plan_id, payload_object_id, created_at, updated_at
                FROM actions WHERE action_id = ?
                """,
                (action_id,),
            ).fetchone()
            if row is None:
                raise VaultError(
                    "action_not_found",
                    "行动不存在",
                    status_code=404,
                )
            return self._action_from_row(connection, vmk, row)

    def list_actions(
        self,
        *,
        token: str,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> dict[str, object]:
        vmk = self._key_provider(token)
        start = _normalize_datetime(date_from, "开始时间")
        end = _normalize_datetime(date_to, "结束时间")
        with closing(self.database.connect()) as connection:
            rows = connection.execute(
                """
                SELECT action_id, plan_id, payload_object_id, created_at, updated_at
                FROM actions ORDER BY created_at DESC
                """
            ).fetchall()
            items = [
                self._action_from_row(connection, vmk, row)
                for row in rows
            ]
        if start:
            items = [
                item for item in items
                if item["due_at"] is not None and str(item["due_at"]) >= start
            ]
        if end:
            items = [
                item for item in items
                if item["due_at"] is not None and str(item["due_at"]) <= end
            ]
        return {"items": items}

    def update_action(
        self,
        *,
        token: str,
        action_id: str,
        operation_id: str,
        revision: int,
        status: str,
        due_at: str | None,
        waiting_for_kind: str | None,
        review_at: str | None,
        completion_result: str | None,
        reason: str | None,
    ) -> dict[str, object]:
        vmk = self._key_provider(token)
        if status not in _STATUSES:
            raise VaultError(
                "action_status_invalid",
                "行动状态无效",
                status_code=422,
            )
        replay = self._idempotent(operation_id, "action.update")
        if replay is not None:
            return self.get_action(token=token, action_id=action_id)
        with closing(self.database.connect()) as connection:
            with connection:
                row = connection.execute(
                    """
                    SELECT action_id, payload_object_id
                    FROM actions WHERE action_id = ?
                    """,
                    (action_id,),
                ).fetchone()
                if row is None:
                    raise VaultError(
                        "action_not_found",
                        "行动不存在",
                        status_code=404,
                    )
                payload, current_revision = self.repository.get(
                    connection,
                    vmk=vmk,
                    object_id=str(row["payload_object_id"]),
                )
                if current_revision != revision:
                    raise VaultError(
                        "vault_revision_conflict",
                        "行动已经变化，请刷新后再修改",
                        status_code=409,
                    )
                previous_status = str(payload["status"])
                if previous_status in {"completed", "cancelled", "superseded"}:
                    if status != "pending":
                        raise VaultError(
                            "action_reopen_required",
                            "已结束行动需要先重开",
                            status_code=409,
                        )
                    if not str(reason or "").strip():
                        raise VaultError(
                            "action_reason_required",
                            "重开行动需要填写原因",
                            status_code=422,
                        )
                    payload["reopened_count"] = int(
                        payload.get("reopened_count") or 0
                    ) + 1
                normalized_due = _normalize_datetime(due_at, "截止时间")
                normalized_review = _normalize_datetime(review_at, "复查时间")
                clean_waiting = str(waiting_for_kind or "").strip() or None
                clean_result = str(completion_result or "").strip() or None
                if status == "waiting" and (
                    clean_waiting is None or normalized_review is None
                ):
                    raise VaultError(
                        "action_waiting_details_required",
                        "等待状态必须填写等待对象和复查时间",
                        status_code=422,
                    )
                if status in {"completed", "partially_completed"} and clean_result is None:
                    raise VaultError(
                        "action_result_required",
                        "完成或部分完成时需要填写处理结果",
                        status_code=422,
                    )
                payload.update(
                    {
                        "status": status,
                        "due_at": normalized_due,
                        "waiting_for_kind": clean_waiting if status == "waiting" else None,
                        "review_at": normalized_review if status == "waiting" else None,
                        "completion_result": (
                            clean_result
                            if status in {"completed", "partially_completed"}
                            else None
                        ),
                        "completed_at": _iso() if status == "completed" else None,
                    }
                )
                history = list(payload.get("transition_history") or [])
                history.append(
                    {
                        "from": previous_status,
                        "to": status,
                        "at": _iso(),
                        "reason": str(reason or "").strip() or None,
                    }
                )
                payload["transition_history"] = history
                next_revision = self.repository.put(
                    connection,
                    vmk=vmk,
                    object_id=str(row["payload_object_id"]),
                    object_type="action_item",
                    payload=payload,
                    expected_revision=revision,
                )
                connection.execute(
                    "UPDATE actions SET updated_at = ? WHERE action_id = ?",
                    (_iso(), action_id),
                )
                event = "reopened" if (
                    previous_status in {"completed", "cancelled", "superseded"}
                    and status == "pending"
                ) else f"status.{status}"
                self._event(connection, action_id, event)
                self._remember(
                    connection,
                    operation_id,
                    "action.update",
                    {"action_id": action_id, "revision": next_revision},
                )
        return self.get_action(token=token, action_id=action_id)

    def dashboard(self, *, token: str, as_of: str | None = None) -> dict[str, object]:
        point = (
            self._parse_as_of(as_of)
            if as_of
            else datetime.now(_LOCAL_ZONE)
        )
        if point.tzinfo is None:
            point = point.replace(tzinfo=_LOCAL_ZONE)
        point_utc = point.astimezone(UTC)
        local_day = point.astimezone(_LOCAL_ZONE).date()
        next_week = local_day + timedelta(days=7)
        actions = list(self.list_actions(token=token)["items"])
        active = [item for item in actions if item["status"] in _ACTIVE_STATUSES]

        def due(item: dict[str, object]) -> datetime | None:
            raw = item.get("due_at")
            return datetime.fromisoformat(str(raw)) if raw else None

        return {
            "as_of": point_utc.isoformat(),
            "today": [
                item for item in active
                if due(item) is not None
                and due(item).astimezone(_LOCAL_ZONE).date() == local_day
            ],
            "overdue": [
                item for item in active
                if due(item) is not None and due(item) < point_utc
            ],
            "upcoming": [
                item for item in active
                if due(item) is not None
                and local_day < due(item).astimezone(_LOCAL_ZONE).date() <= next_week
            ],
            "waiting": [item for item in active if item["status"] == "waiting"],
            "unscheduled": [item for item in active if item["due_at"] is None],
        }

    def get_calendar(self, *, token: str) -> dict[str, object]:
        vmk = self._key_provider(token)
        with closing(self.database.connect()) as connection:
            try:
                payload, revision = self.repository.get(
                    connection,
                    vmk=vmk,
                    object_id="system-school-calendar",
                )
                return {**payload, "revision": revision, "configured": True}
            except VaultError as exc:
                if exc.code != "vault_object_not_found":
                    raise
        return {
            "configured": False,
            "revision": 0,
            "school_day_end": None,
            "locked_dates": [],
            "working_weekdays": None,
        }

    def save_calendar(
        self,
        *,
        token: str,
        operation_id: str,
        revision: int,
        school_day_end: str | None,
        locked_dates: list[str],
        working_weekdays: list[int] | None,
    ) -> dict[str, object]:
        vmk = self._key_provider(token)
        replay = self._idempotent(operation_id, "calendar.save")
        if replay is not None:
            return self.get_calendar(token=token)
        if school_day_end:
            try:
                time.fromisoformat(school_day_end)
            except ValueError as exc:
                raise VaultError(
                    "calendar_time_invalid",
                    "放学时间无效",
                    status_code=422,
                ) from exc
        normalized_dates: list[str] = []
        for item in locked_dates:
            try:
                normalized_dates.append(date.fromisoformat(item).isoformat())
            except ValueError as exc:
                raise VaultError(
                    "calendar_date_invalid",
                    "锁定日期无效",
                    status_code=422,
                ) from exc
        if working_weekdays is not None and (
            not working_weekdays
            or any(value < 1 or value > 7 for value in working_weekdays)
        ):
            raise VaultError(
                "calendar_weekdays_invalid",
                "工作日设置无效",
                status_code=422,
            )
        payload = {
            "school_day_end": school_day_end or None,
            "locked_dates": sorted(set(normalized_dates)),
            "working_weekdays": (
                sorted(set(working_weekdays)) if working_weekdays is not None else None
            ),
        }
        with closing(self.database.connect()) as connection:
            with connection:
                next_revision = self.repository.put(
                    connection,
                    vmk=vmk,
                    object_id="system-school-calendar",
                    object_type="school_calendar",
                    payload=payload,
                    expected_revision=revision,
                )
                self._remember(
                    connection,
                    operation_id,
                    "calendar.save",
                    {"revision": next_revision},
                )
        return {**payload, "revision": next_revision, "configured": True}

    def _shift_active_actions(
        self,
        connection: Any,
        vmk: bytes,
        plan_id: str,
        shift: timedelta,
    ) -> None:
        rows = connection.execute(
            """
            SELECT action_id, payload_object_id
            FROM actions WHERE plan_id = ?
            """,
            (plan_id,),
        ).fetchall()
        for row in rows:
            payload, revision = self.repository.get(
                connection,
                vmk=vmk,
                object_id=str(row["payload_object_id"]),
            )
            if payload.get("status") not in _ACTIVE_STATUSES or not payload.get(
                "due_at"
            ):
                continue
            payload["due_at"] = (
                datetime.fromisoformat(str(payload["due_at"])) + shift
            ).isoformat()
            history = list(payload.get("transition_history") or [])
            history.append(
                {
                    "from": payload.get("status"),
                    "to": payload.get("status"),
                    "at": _iso(),
                    "reason": "final_deadline_shifted",
                }
            )
            payload["transition_history"] = history
            self.repository.put(
                connection,
                vmk=vmk,
                object_id=str(row["payload_object_id"]),
                object_type="action_item",
                payload=payload,
                expected_revision=revision,
            )
            connection.execute(
                "UPDATE actions SET updated_at = ? WHERE action_id = ?",
                (_iso(), str(row["action_id"])),
            )
            self._event(
                connection,
                str(row["action_id"]),
                "deadline_shifted",
            )

    def _plan_from_row(
        self,
        connection: Any,
        vmk: bytes,
        row: Any,
    ) -> dict[str, object]:
        payload, revision = self.repository.get(
            connection,
            vmk=vmk,
            object_id=str(row["payload_object_id"]),
        )
        return {
            "plan_id": str(row["plan_id"]),
            "revision": revision,
            **payload,
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }

    def _action_from_row(
        self,
        connection: Any,
        vmk: bytes,
        row: Any,
    ) -> dict[str, object]:
        payload, revision = self.repository.get(
            connection,
            vmk=vmk,
            object_id=str(row["payload_object_id"]),
        )
        dependencies = [
            str(item[0])
            for item in connection.execute(
                """
                SELECT depends_on_action_id
                FROM action_dependencies
                WHERE action_id = ?
                ORDER BY created_at
                """,
                (str(row["action_id"]),),
            )
        ]
        return {
            "action_id": str(row["action_id"]),
            "plan_id": str(row["plan_id"]),
            "revision": revision,
            **payload,
            "depends_on_action_ids": dependencies,
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
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
    def _event(connection: Any, action_id: str, event_type: str) -> None:
        connection.execute(
            """
            INSERT INTO action_audit_events (
                event_id, action_id, event_type, created_at
            ) VALUES (?, ?, ?, ?)
            """,
            (uuid4().hex, action_id, event_type, _iso()),
        )

    @staticmethod
    def _required_text(value: str, label: str) -> str:
        clean = str(value or "").strip()
        if not clean:
            raise VaultError(
                "action_text_required",
                f"{label}不能为空",
                status_code=422,
            )
        if len(clean) > 240:
            raise VaultError(
                "action_text_too_long",
                f"{label}过长",
                status_code=422,
            )
        return clean

    @staticmethod
    def _parse_as_of(value: str) -> datetime:
        try:
            return datetime.fromisoformat(value)
        except ValueError as exc:
            raise VaultError(
                "action_datetime_invalid",
                "查询时间无效",
                status_code=422,
            ) from exc


__all__ = ["ActionLedgerService"]
