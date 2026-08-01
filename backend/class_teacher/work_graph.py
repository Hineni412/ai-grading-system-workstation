from __future__ import annotations

import json
import re
from contextlib import closing
from datetime import UTC, date, datetime
from typing import Any
from uuid import uuid4

from .content_policy import SensitiveContentPolicy
from .errors import VaultError
from .ordinary_database import OrdinaryWorkDatabase
from .work_planning import WorkPlanning, WorkPlanningGateway


_OPERATION_ID = re.compile(r"[A-Za-z0-9_-]{8,128}")
_NATURAL_DATE_PATTERNS = (
    re.compile(
        r"(?<!\d)(?:(?P<year>\d{4})\s*年\s*)?"
        r"(?P<month>\d{1,2})\s*月\s*"
        r"(?P<day>\d{1,2})\s*(?:日|号)(?!\d)"
    ),
    re.compile(
        r"(?<!\d)(?:(?P<year>\d{4})[./-])?"
        r"(?P<month>\d{1,2})[./-](?P<day>\d{1,2})(?!\d)"
    ),
)
_NODE_KINDS = {
    "goal",
    "task",
    "waiting",
    "decision",
    "collection",
    "communication",
    "sop",
}
_STATUSES = {
    "pending",
    "in_progress",
    "waiting",
    "completed",
    "cancelled",
}
_PROJECTION_TITLE = "学生事项待跟进"
_SOP_PROJECTION_TITLE = "敏感 SOP 待复查"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _iso_date(value: str | None, *, label: str = "日期") -> str | None:
    if value is None or not str(value).strip():
        return None
    try:
        parsed = date.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise VaultError(
            "class_teacher_work_date_invalid",
            f"{label}必须是明确的年月日",
            status_code=422,
        ) from exc
    return parsed.isoformat()


def _natural_language_date(
    text: str,
    *,
    selected_date: str | None,
    today: date | None = None,
) -> str | None:
    """Resolve one explicit Chinese calendar date without inventing a time.

    A yearless date uses the selected calendar day's year when its month/day
    match.  Otherwise it resolves to the next non-past occurrence.  Multiple
    different dates and conflicts with the selected calendar day fail closed
    so a teacher can correct the draft before anything is persisted.
    """

    matches = sorted(
        (
            match
            for pattern in _NATURAL_DATE_PATTERNS
            for match in pattern.finditer(text)
        ),
        key=lambda match: match.start(),
    )
    if not matches:
        return selected_date

    selected = date.fromisoformat(selected_date) if selected_date else None
    reference = today or date.today()
    resolved: list[date] = []
    for match in matches:
        month = int(match.group("month"))
        day = int(match.group("day"))
        year_text = match.group("year")
        if year_text:
            year = int(year_text)
        elif selected is not None and (selected.month, selected.day) == (month, day):
            year = selected.year
        else:
            year = reference.year
        try:
            candidate = date(year, month, day)
        except ValueError as exc:
            raise VaultError(
                "class_teacher_work_date_invalid",
                "任务文字中的日期不是有效年月日",
                status_code=422,
            ) from exc
        if not year_text and selected is None and candidate < reference:
            try:
                candidate = date(year + 1, month, day)
            except ValueError as exc:
                raise VaultError(
                    "class_teacher_work_date_invalid",
                    "任务文字中的日期不是有效年月日",
                    status_code=422,
                ) from exc
        resolved.append(candidate)

    unique = {item.isoformat() for item in resolved}
    if len(unique) != 1:
        raise VaultError(
            "class_teacher_work_date_ambiguous",
            "一句话中出现了多个不同日期，请保留一个最终日期",
            status_code=422,
        )
    natural = resolved[0]
    if selected is not None and natural != selected:
        raise VaultError(
            "class_teacher_work_date_conflict",
            "任务文字中的日期与日历所选日期不一致，请确认最终日期",
            status_code=422,
        )
    return natural.isoformat()


class WorkGraph:
    """The ordinary-work interface used by HTTP callers and tests.

    It hides schema creation, date-only scheduling, idempotency, optimistic
    concurrency and restricted projection recovery behind four commands.
    """

    def __init__(
        self,
        database: OrdinaryWorkDatabase,
        *,
        model_gateway: WorkPlanningGateway | None = None,
    ) -> None:
        self.database = database
        self.planning = WorkPlanning(database, model_gateway)

    def query(
        self,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        as_of: str | None = None,
    ) -> dict[str, object]:
        today = _iso_date(as_of, label="查看日期") or date.today().isoformat()
        start = _iso_date(start_date, label="开始日期")
        end = _iso_date(end_date, label="结束日期")
        if start is not None and end is not None and start > end:
            raise VaultError(
                "class_teacher_work_range_invalid",
                "开始日期不能晚于结束日期",
                status_code=422,
            )
        if not self.database.exists:
            return self._snapshot([], [], today=today, start=start, end=end)

        self.drain_projection_outbox()
        with closing(self.database.connect()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM work_nodes
                ORDER BY due_date IS NULL, due_date, created_at, node_id
                """
            ).fetchall()
            edge_rows = connection.execute(
                """
                SELECT source_node_id, target_node_id, relation
                FROM work_edges
                ORDER BY created_at, source_node_id, target_node_id
                """
            ).fetchall()
        node_ids = self._connected_range_node_ids(
            rows,
            edge_rows,
            start=start,
            end=end,
        )
        nodes = [self._node(row) for row in rows if str(row["node_id"]) in node_ids]
        edges = [
            {
                "source_node_id": str(row["source_node_id"]),
                "target_node_id": str(row["target_node_id"]),
                "relation": str(row["relation"]),
            }
            for row in edge_rows
            if str(row["source_node_id"]) in node_ids
            and str(row["target_node_id"]) in node_ids
        ]
        return self._snapshot(nodes, edges, today=today, start=start, end=end)

    @staticmethod
    def _connected_range_node_ids(
        rows: list[Any],
        edge_rows: list[Any],
        *,
        start: str | None,
        end: str | None,
    ) -> set[str]:
        all_ids = {str(row["node_id"]) for row in rows}
        if start is None and end is None:
            return all_ids
        visible: set[str] = set()
        for row in rows:
            due_date = None if row["due_date"] is None else str(row["due_date"])
            if due_date is None or (
                (start is None or due_date >= start)
                and (end is None or due_date <= end)
            ):
                visible.add(str(row["node_id"]))
        adjacency: dict[str, set[str]] = {node_id: set() for node_id in all_ids}
        for edge in edge_rows:
            source = str(edge["source_node_id"])
            target = str(edge["target_node_id"])
            if source in all_ids and target in all_ids:
                adjacency[source].add(target)
                adjacency[target].add(source)
        pending = list(visible)
        while pending:
            current = pending.pop()
            for neighbor in adjacency[current] - visible:
                visible.add(neighbor)
                pending.append(neighbor)
        return visible

    def prepare_plan(
        self,
        *,
        text: str,
        due_date: str | None,
        parent_node_id: str | None = None,
        parent_revision: int | None = None,
    ) -> dict[str, object]:
        title = self._ordinary_title(text)
        selected_deadline = _iso_date(due_date, label="截止日期")
        deadline = _natural_language_date(title, selected_date=selected_deadline)
        parent_context = None
        local_context: dict[str, object] = {"mode": "new_work"}
        mode = "new_work"
        if parent_node_id is not None:
            if parent_revision is None:
                raise VaultError(
                    "class_teacher_work_revision_required",
                    "缺少当前任务版本，请刷新后重试",
                    status_code=422,
                )
            parent = self._require_node(parent_node_id)
            if int(parent["revision"]) != int(parent_revision):
                raise VaultError(
                    "class_teacher_work_revision_conflict",
                    "任务已经变化，请刷新后再生成 AI 分支",
                    status_code=409,
                )
            mode = "progress_update"
            parent_context = {
                "kind": str(parent["kind"]),
                "title": str(parent["title"]),
                "details": None if parent["details"] is None else str(parent["details"]),
                "status": str(parent["status"]),
                "due_date": None if parent["due_date"] is None else str(parent["due_date"]),
            }
            local_context = {
                "mode": mode,
                "parent_node_id": parent_node_id,
                "parent_revision": int(parent_revision),
            }
        return self.planning.prepare(
            source_text=title,
            final_due_date=deadline,
            mode=mode,
            parent_context=parent_context,
            local_context=local_context,
        )

    def invoke_plan(
        self,
        *,
        preview_id: str,
        fingerprint: str,
        operation_id: str,
    ) -> dict[str, object]:
        return self.planning.invoke(
            preview_id=preview_id,
            fingerprint=fingerprint,
            operation_id=operation_id,
        )

    def plan_status(self, *, operation_id: str) -> dict[str, object]:
        return self.planning.status(operation_id=operation_id)

    def confirm_plan(
        self,
        *,
        model_operation_id: str,
        plan_fingerprint: str,
        operation_id: str,
    ) -> dict[str, object]:
        self._validate_operation_id(operation_id)
        proposal = self.planning.confirmable(
            operation_id=model_operation_id,
            plan_fingerprint=plan_fingerprint,
        )
        plan = proposal.get("plan")
        if not isinstance(plan, dict):
            raise VaultError(
                "class_teacher_work_plan_not_confirmable",
                "AI 方案尚未成功，不能写入工作图",
                status_code=409,
            )
        raw_nodes = plan.get("nodes")
        raw_edges = plan.get("edges")
        if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
            raise VaultError(
                "class_teacher_work_plan_integrity_error",
                "AI 方案未通过完整性校验",
                status_code=409,
            )
        local_context = proposal.get("local_context")
        if not isinstance(local_context, dict):
            local_context = {}
        mode = str(local_context.get("mode") or "")

        self.database.initialize_schema() if not self.database.exists else None
        with closing(self.database.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                replay = self._replay(connection, operation_id, "work.plan.confirm")
                if replay is not None:
                    if str(replay.get("model_operation_id") or "") != model_operation_id:
                        raise VaultError(
                            "class_teacher_operation_conflict",
                            "同一操作编号不能用于不同的 AI 方案",
                            status_code=409,
                        )
                    connection.commit()
                    return replay
                prior = self._confirmed_model_plan(connection, model_operation_id)
                if prior is not None:
                    raise VaultError(
                        "class_teacher_work_plan_already_confirmed",
                        "这份 AI 方案已经写入工作图",
                        status_code=409,
                    )

                parent_node_id = None
                if mode == "progress_update":
                    parent_node_id = str(local_context.get("parent_node_id") or "")
                    parent_revision = int(local_context.get("parent_revision") or 0)
                    parent = connection.execute(
                        "SELECT revision FROM work_nodes WHERE node_id = ?",
                        (parent_node_id,),
                    ).fetchone()
                    if parent is None:
                        raise VaultError(
                            "class_teacher_work_node_not_found",
                            "普通工作项不存在",
                            status_code=404,
                        )
                    if int(parent["revision"]) != parent_revision:
                        raise VaultError(
                            "class_teacher_work_revision_conflict",
                            "任务已经变化，请重新生成 AI 分支",
                            status_code=409,
                        )
                elif mode != "new_work":
                    raise VaultError(
                        "class_teacher_work_plan_integrity_error",
                        "AI 方案缺少有效来源",
                        status_code=409,
                    )

                timestamp = _now()
                ids: dict[str, str] = {}
                persisted: list[dict[str, object]] = []
                for raw in raw_nodes:
                    if not isinstance(raw, dict):
                        raise VaultError(
                            "class_teacher_work_plan_integrity_error",
                            "AI 方案节点未通过完整性校验",
                            status_code=409,
                        )
                    draft_key = str(raw["draft_key"])
                    node_id = uuid4().hex
                    ids[draft_key] = node_id
                    connection.execute(
                        """
                        INSERT INTO work_nodes (
                            node_id, kind, classification, title, details,
                            status, due_date, revision, created_at, updated_at
                        ) VALUES (?, ?, 'ordinary', ?, ?, ?, ?, 1, ?, ?)
                        """,
                        (
                            node_id,
                            str(raw["kind"]),
                            str(raw["title"]),
                            raw.get("details"),
                            str(raw["status"]),
                            raw.get("due_date"),
                            timestamp,
                            timestamp,
                        ),
                    )
                    persisted.append(
                        {
                            **raw,
                            "node_id": node_id,
                            "classification": "ordinary",
                            "revision": 1,
                            "created_at": timestamp,
                            "updated_at": timestamp,
                        }
                    )

                persisted_edges: list[dict[str, object]] = []
                incoming: set[str] = set()
                for raw in raw_edges:
                    if not isinstance(raw, dict):
                        raise VaultError(
                            "class_teacher_work_plan_integrity_error",
                            "AI 方案关系未通过完整性校验",
                            status_code=409,
                        )
                    source_key = str(raw["source_draft_key"])
                    target_key = str(raw["target_draft_key"])
                    source_id = ids[source_key]
                    target_id = ids[target_key]
                    relation = str(raw["relation"])
                    incoming.add(target_key)
                    connection.execute(
                        """
                        INSERT INTO work_edges (
                            source_node_id, target_node_id, relation, created_at
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (source_id, target_id, relation, timestamp),
                    )
                    persisted_edges.append(
                        {
                            "source_node_id": source_id,
                            "target_node_id": target_id,
                            "relation": relation,
                        }
                    )
                if parent_node_id is not None:
                    roots = [key for key in ids if key not in incoming]
                    for root_key in roots:
                        target_id = ids[root_key]
                        connection.execute(
                            """
                            INSERT INTO work_edges (
                                source_node_id, target_node_id, relation, created_at
                            ) VALUES (?, ?, 'review_of', ?)
                            """,
                            (parent_node_id, target_id, timestamp),
                        )
                        persisted_edges.append(
                            {
                                "source_node_id": parent_node_id,
                                "target_node_id": target_id,
                                "relation": "review_of",
                            }
                        )
                goal_id = next(
                    (
                        str(item["node_id"])
                        for item in persisted
                        if item["kind"] == "goal"
                    ),
                    None,
                )
                result = {
                    "created": True,
                    "goal_id": goal_id,
                    "parent_node_id": parent_node_id,
                    "model_operation_id": model_operation_id,
                    "nodes": persisted,
                    "edges": persisted_edges,
                    "physical_request_count": int(proposal["physical_request_count"]),
                }
                self._remember(connection, operation_id, "work.plan.confirm", result)
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def update_node(
        self,
        *,
        node_id: str,
        revision: int,
        status: str,
        due_date: str | None,
        operation_id: str,
    ) -> dict[str, object]:
        self._validate_operation_id(operation_id)
        if status not in _STATUSES:
            raise VaultError(
                "class_teacher_work_status_invalid",
                "任务状态无效",
                status_code=422,
            )
        normalized_due = _iso_date(due_date, label="任务日期")
        if not self.database.exists:
            raise VaultError(
                "class_teacher_work_node_not_found",
                "普通工作项不存在",
                status_code=404,
            )
        with closing(self.database.connect()) as connection:
            with connection:
                replay = self._replay(connection, operation_id, "work.node.update")
                if replay is not None:
                    return replay
                row = connection.execute(
                    "SELECT * FROM work_nodes WHERE node_id = ?",
                    (node_id,),
                ).fetchone()
                if row is None:
                    raise VaultError(
                        "class_teacher_work_node_not_found",
                        "普通工作项不存在",
                        status_code=404,
                    )
                if int(row["revision"]) != int(revision):
                    raise VaultError(
                        "class_teacher_work_revision_conflict",
                        "任务已经变化，请刷新后再保存",
                        status_code=409,
                    )
                updated_at = _now()
                connection.execute(
                    """
                    UPDATE work_nodes
                    SET status = ?, due_date = ?, revision = revision + 1,
                        updated_at = ?
                    WHERE node_id = ?
                    """,
                    (status, normalized_due, updated_at, node_id),
                )
                changed = connection.execute(
                    "SELECT * FROM work_nodes WHERE node_id = ?",
                    (node_id,),
                ).fetchone()
                result = self._node(changed)
                self._remember(connection, operation_id, "work.node.update", result)
                return result

    def enqueue_sensitive_projection(
        self,
        *,
        projection_id: str,
        due_date: str | None,
        status: str,
        source_revision: int,
        operation_id: str,
        projection_kind: str = "task",
    ) -> dict[str, object]:
        """Accept only a pre-sanitised, identity-free projection contract."""

        self._validate_operation_id(operation_id)
        if status not in _STATUSES:
            raise VaultError(
                "class_teacher_projection_status_invalid",
                "敏感事项投影状态无效",
                status_code=422,
            )
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", projection_id):
            raise VaultError(
                "class_teacher_projection_id_invalid",
                "敏感事项投影编号无效",
                status_code=422,
            )
        if projection_kind not in {"task", "sop"}:
            raise VaultError(
                "class_teacher_projection_kind_invalid",
                "敏感事项投影类型无效",
                status_code=422,
            )
        projection_title = (
            _SOP_PROJECTION_TITLE if projection_kind == "sop" else _PROJECTION_TITLE
        )
        payload = {
            "projection_id": projection_id,
            "kind": "restricted_projection",
            "projection_kind": projection_kind,
            "title": projection_title,
            "status": status,
            "due_date": _iso_date(due_date, label="投影日期"),
            "source_revision": int(source_revision),
        }
        self.database.initialize_schema() if not self.database.exists else None
        with closing(self.database.connect()) as connection:
            with connection:
                existing = connection.execute(
                    """
                    SELECT payload_json, state FROM work_projection_outbox
                    WHERE operation_id = ?
                    """,
                    (operation_id,),
                ).fetchone()
                if existing is None:
                    timestamp = _now()
                    connection.execute(
                        """
                        INSERT INTO work_projection_outbox (
                            event_id, operation_id, projection_id, payload_json,
                            state, attempts, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, 'pending', 0, ?, ?)
                        """,
                        (
                            uuid4().hex,
                            operation_id,
                            projection_id,
                            json.dumps(payload, ensure_ascii=False, sort_keys=True),
                            timestamp,
                            timestamp,
                        ),
                    )
                elif json.loads(str(existing["payload_json"])) != payload:
                    raise VaultError(
                        "class_teacher_operation_conflict",
                        "同一操作编号不能用于不同的敏感投影",
                        status_code=409,
                    )
        self.drain_projection_outbox()
        return payload

    def drain_projection_outbox(self) -> int:
        if not self.database.exists:
            return 0
        applied = 0
        with closing(self.database.connect()) as connection:
            with connection:
                rows = connection.execute(
                    """
                    SELECT * FROM work_projection_outbox
                    WHERE state = 'pending'
                    ORDER BY created_at, event_id
                    """
                ).fetchall()
                for row in rows:
                    payload = json.loads(str(row["payload_json"]))
                    projection_id = str(payload["projection_id"])
                    projection_title = str(payload.get("title") or _PROJECTION_TITLE)
                    current = connection.execute(
                        """
                        SELECT node_id, revision FROM work_nodes
                        WHERE source_projection_id = ?
                        """,
                        (projection_id,),
                    ).fetchone()
                    timestamp = _now()
                    source_revision = int(payload["source_revision"])
                    if current is None:
                        connection.execute(
                            """
                            INSERT INTO work_nodes (
                                node_id, kind, classification, title, details,
                                status, due_date, source_projection_id,
                                revision, created_at, updated_at
                            ) VALUES (
                                ?, 'restricted_projection',
                                'restricted_projection', ?, NULL, ?, ?, ?, ?, ?, ?
                            )
                            """,
                            (
                                uuid4().hex,
                                projection_title,
                                str(payload["status"]),
                                payload.get("due_date"),
                                projection_id,
                                max(1, source_revision),
                                timestamp,
                                timestamp,
                            ),
                        )
                    elif source_revision >= int(current["revision"]):
                        connection.execute(
                            """
                            UPDATE work_nodes
                            SET title = ?, status = ?, due_date = ?,
                                revision = ?, updated_at = ?
                            WHERE source_projection_id = ?
                            """,
                            (
                                projection_title,
                                str(payload["status"]),
                                payload.get("due_date"),
                                max(1, source_revision),
                                timestamp,
                                projection_id,
                            ),
                        )
                    connection.execute(
                        """
                        UPDATE work_projection_outbox
                        SET state = 'applied', attempts = attempts + 1,
                            updated_at = ?
                        WHERE event_id = ?
                        """,
                        (timestamp, str(row["event_id"])),
                    )
                    applied += 1
        return applied

    def _require_node(self, node_id: str):
        if not self.database.exists:
            row = None
        else:
            with closing(self.database.connect()) as connection:
                row = connection.execute(
                    "SELECT * FROM work_nodes WHERE node_id = ?",
                    (node_id,),
                ).fetchone()
        if row is None:
            raise VaultError(
                "class_teacher_work_node_not_found",
                "普通工作项不存在",
                status_code=404,
            )
        return row

    @staticmethod
    def _ordinary_title(value: str) -> str:
        title = " ".join(str(value or "").split())
        if not title:
            raise VaultError(
                "class_teacher_work_text_required",
                "请先写下最新情况",
                status_code=422,
            )
        if len(title) > 240:
            raise VaultError(
                "class_teacher_work_text_too_long",
                "最新情况不能超过 240 个字符",
                status_code=422,
            )
        findings = SensitiveContentPolicy.ordinary_findings(title)
        if findings:
            raise VaultError(
                "class_teacher_work_sensitive_content",
                "这段内容可能涉及具体学生，请从底部敏感入口记录",
                status_code=422,
                details={"categories": list(findings)},
            )
        return title

    @staticmethod
    def _node(row: Any) -> dict[str, object]:
        return {
            "node_id": str(row["node_id"]),
            "kind": str(row["kind"]),
            "classification": str(row["classification"]),
            "title": str(row["title"]),
            "details": None if row["details"] is None else str(row["details"]),
            "status": str(row["status"]),
            "due_date": None if row["due_date"] is None else str(row["due_date"]),
            "revision": int(row["revision"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }

    @staticmethod
    def _snapshot(
        nodes: list[dict[str, object]],
        edges: list[dict[str, object]],
        *,
        today: str,
        start: str | None,
        end: str | None,
    ) -> dict[str, object]:
        active = [node for node in nodes if node["status"] not in {"completed", "cancelled"}]
        today_nodes = [node for node in active if node["due_date"] == today]
        overdue = [
            node
            for node in active
            if isinstance(node["due_date"], str) and node["due_date"] < today
        ]
        waiting = [node for node in active if node["status"] == "waiting"]
        return {
            "as_of": today,
            "start_date": start,
            "end_date": end,
            "nodes": nodes,
            "edges": edges,
            "today": today_nodes,
            "overdue": overdue,
            "waiting": waiting,
        }

    @staticmethod
    def _validate_operation_id(operation_id: str) -> None:
        if _OPERATION_ID.fullmatch(str(operation_id or "")) is None:
            raise VaultError(
                "class_teacher_operation_id_invalid",
                "操作编号无效",
                status_code=422,
            )

    @staticmethod
    def _replay(connection: Any, operation_id: str, operation_type: str):
        row = connection.execute(
            """
            SELECT operation_type, result_json FROM work_operations
            WHERE operation_id = ?
            """,
            (operation_id,),
        ).fetchone()
        if row is None:
            return None
        if str(row["operation_type"]) != operation_type:
            raise VaultError(
                "class_teacher_operation_conflict",
                "同一操作编号不能用于不同操作",
                status_code=409,
            )
        decoded = json.loads(str(row["result_json"]))
        if not isinstance(decoded, dict):
            raise VaultError(
                "class_teacher_work_integrity_error",
                "普通工作记录未通过完整性校验",
                status_code=409,
            )
        return decoded

    @staticmethod
    def _remember(
        connection: Any,
        operation_id: str,
        operation_type: str,
        result: dict[str, object],
    ) -> None:
        connection.execute(
            """
            INSERT INTO work_operations (
                operation_id, operation_type, result_json, created_at
            ) VALUES (?, ?, ?, ?)
            """,
            (
                operation_id,
                operation_type,
                json.dumps(result, ensure_ascii=False, sort_keys=True),
                _now(),
            ),
        )

    @staticmethod
    def _confirmed_model_plan(connection: Any, model_operation_id: str):
        rows = connection.execute(
            """
            SELECT operation_id, result_json FROM work_operations
            WHERE operation_type = 'work.plan.confirm'
            """
        ).fetchall()
        for row in rows:
            try:
                decoded = json.loads(str(row["result_json"]))
            except json.JSONDecodeError:
                continue
            if (
                isinstance(decoded, dict)
                and str(decoded.get("model_operation_id") or "")
                == model_operation_id
            ):
                return str(row["operation_id"])
        return None


__all__ = ["WorkGraph"]
