from __future__ import annotations

import hashlib
import json
import re
import threading
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from typing import Mapping, Protocol
from uuid import uuid4

from .content_policy import SensitiveContentPolicy
from .crypto import canonical_json
from .errors import VaultError
from .model_approval import (
    ModelDestinationChanged,
    ModelDispatchDisabled,
    ModelResultUnknown,
)
from .ordinary_database import OrdinaryWorkDatabase


_OPERATION_ID = re.compile(r"[A-Za-z0-9_-]{8,128}")
_DRAFT_KEY = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}")
_PREVIEW_SECONDS = 10 * 60
_NODE_KINDS = {
    "goal",
    "task",
    "waiting",
    "decision",
    "collection",
    "communication",
    "sop",
}
_INITIAL_STATUSES = {"pending", "waiting"}
_EDGE_RELATIONS = {"contains", "depends_on", "next"}
_CALL_OPERATION_TYPE = "work.plan.invoke"


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat()


def _destination_identity(
    *,
    available: bool,
    model_provider: str | None,
    model_endpoint: str | None,
    model: str | None,
) -> dict[str, object]:
    identity: dict[str, object] = {
        "available": bool(available),
        "model_provider": model_provider,
        "model_endpoint": model_endpoint,
        "model": model,
    }
    identity["destination_fingerprint"] = hashlib.sha256(
        canonical_json(identity)
    ).hexdigest()
    return identity


class WorkPlanningGateway(Protocol):
    """The model seam used by ordinary-work planning.

    The payload is the complete external message.  Adapters must not append a
    hidden system prompt or retry automatically.
    """

    model_name: str

    def is_available(self) -> bool: ...

    def destination_snapshot(self) -> dict[str, object]: ...

    def invoke(
        self,
        *,
        payload: dict[str, object],
        operation_id: str,
        purpose: str = "ordinary_work_plan",
        data_classification: str = "ordinary",
        expected_destination_fingerprint: str | None = None,
    ) -> str: ...


class DisabledWorkPlanningGateway:
    model_name = "未启用真实模型"

    def is_available(self) -> bool:
        return False

    def destination_snapshot(self) -> dict[str, object]:
        return _destination_identity(
            available=False,
            model_provider=None,
            model_endpoint=None,
            model=None,
        )

    def invoke(
        self,
        *,
        payload: dict[str, object],
        operation_id: str,
        purpose: str = "ordinary_work_plan",
        data_classification: str = "ordinary",
        expected_destination_fingerprint: str | None = None,
    ) -> str:
        current = self.destination_snapshot()
        if (
            expected_destination_fingerprint is not None
            and current["destination_fingerprint"]
            != expected_destination_fingerprint
        ):
            raise ModelDestinationChanged("synthetic destination changed")
        _ = (payload, operation_id, purpose, data_classification)
        raise ModelDispatchDisabled("ordinary work planning is unavailable")


class FakeWorkPlanningGateway:
    """Synthetic adapter for tests; it never reaches a network."""

    model_name = "synthetic-work-planner"

    def __init__(
        self,
        *,
        result: Mapping[str, object] | str,
        available: bool = True,
        unknown: bool = False,
        model_provider: str = "synthetic.invalid",
        model_endpoint: str = "https://synthetic.invalid/v1",
        model: str = "synthetic-work-planner",
    ) -> None:
        self.result = result
        self.available = available
        self.unknown = unknown
        self.model_provider = model_provider
        self.model_endpoint = model_endpoint
        self.model = model
        self.calls: list[dict[str, object]] = []

    def is_available(self) -> bool:
        return self.available

    def destination_snapshot(self) -> dict[str, object]:
        return _destination_identity(
            available=self.available,
            model_provider=self.model_provider if self.available else None,
            model_endpoint=self.model_endpoint if self.available else None,
            model=self.model if self.available else None,
        )

    def invoke(
        self,
        *,
        payload: dict[str, object],
        operation_id: str,
        purpose: str = "ordinary_work_plan",
        data_classification: str = "ordinary",
        expected_destination_fingerprint: str | None = None,
    ) -> str:
        current = self.destination_snapshot()
        if (
            expected_destination_fingerprint is not None
            and current["destination_fingerprint"]
            != expected_destination_fingerprint
        ):
            raise ModelDestinationChanged("synthetic destination changed")
        self.calls.append(
            {
                "payload": payload,
                "operation_id": operation_id,
                "purpose": purpose,
                "data_classification": data_classification,
                "expected_destination_fingerprint": expected_destination_fingerprint,
            }
        )
        if not self.available:
            raise ModelDispatchDisabled("synthetic planner unavailable")
        if self.unknown:
            raise ModelResultUnknown("synthetic result unknown")
        if isinstance(self.result, str):
            return self.result
        return json.dumps(dict(self.result), ensure_ascii=False)


class WorkPlanning:
    """Exact-preview, one-call, validated planning module.

    Local code resolves only explicit dates, blocks sensitive text, validates
    the returned graph and records an idempotent call receipt.  It never
    invents workflow steps.  A valid plan is still only a proposal; WorkGraph
    persists it after a separate teacher confirmation.
    """

    def __init__(
        self,
        database: OrdinaryWorkDatabase,
        gateway: WorkPlanningGateway | None = None,
    ) -> None:
        self.database = database
        self.gateway = gateway or DisabledWorkPlanningGateway()
        self._previews: dict[str, dict[str, object]] = {}
        self._active_operations: dict[str, str] = {}
        self._lock = threading.RLock()

    def prepare(
        self,
        *,
        source_text: str,
        final_due_date: str | None,
        mode: str,
        parent_context: dict[str, object] | None = None,
        local_context: dict[str, object] | None = None,
    ) -> dict[str, object]:
        if mode not in {"new_work", "progress_update"}:
            raise VaultError(
                "class_teacher_work_plan_mode_invalid",
                "普通工作规划方式无效",
                status_code=422,
            )
        preview_id = uuid4().hex
        expires_at = _now() + timedelta(seconds=_PREVIEW_SECONDS)
        exact_payload: dict[str, object] = {
            "purpose": "ordinary_work_plan",
            "planning_mode": mode,
            "task_text": source_text,
            "final_due_date": final_due_date,
            "date_semantics": "date-only",
            "parent_context": parent_context,
            "instructions": [
                "根据教师这一次输入的真实语境拆解任务，不使用任何预置业务模板。",
                "识别完成目标所需的具体节点、依赖关系，并从最终日期向前倒排；信息不足时返回 questions，不得猜测。",
                "只返回 JSON；不得诊断、认定、惩戒、自动外发、自动完成或自动结案。",
                "没有最终日期时不得编造节点日期；所有日期只能是 YYYY-MM-DD，且不能晚于 final_due_date。",
            ],
            "output_contract": {
                "kind": "plan 或 follow_up",
                "questions": ["需要教师补充的问题；没有则为空数组"],
                "assumptions": ["模型采用且需教师复核的假设；没有则为空数组"],
                "nodes": [
                    {
                        "id": "本次方案内唯一编号",
                        "kind": sorted(_NODE_KINDS),
                        "title": "节点标题",
                        "details": "具体说明或 null",
                        "status": sorted(_INITIAL_STATUSES),
                        "due_date": "YYYY-MM-DD 或 null",
                    }
                ],
                "edges": [
                    {
                        "source_id": "已有节点编号",
                        "target_id": "已有节点编号",
                        "relation": sorted(_EDGE_RELATIONS),
                    }
                ],
            },
        }
        destination = self._gateway_destination()
        destination_fingerprint = str(destination["destination_fingerprint"])
        fingerprint = hashlib.sha256(
            canonical_json(
                {
                    "exact_payload": exact_payload,
                    "destination_fingerprint": destination_fingerprint,
                }
            )
        ).hexdigest()
        stored = {
            "preview_id": preview_id,
            "source_text": source_text,
            "final_due_date": final_due_date,
            "mode": mode,
            "exact_payload": exact_payload,
            "fingerprint": fingerprint,
            "destination_fingerprint": destination_fingerprint,
            "expires_at": _iso(expires_at),
            "local_context": dict(local_context or {}),
            "claimed_operation_id": None,
            "in_flight": False,
        }
        with self._lock:
            self._prune_previews()
            self._previews[preview_id] = stored
        return {
            "preview_id": preview_id,
            "source_text": source_text,
            "final_due_date": final_due_date,
            "date_semantics": "date-only",
            "exact_payload": exact_payload,
            "fingerprint": fingerprint,
            "expires_at": stored["expires_at"],
            "model_provider": destination.get("model_provider"),
            "model_endpoint": destination.get("model_endpoint"),
            "model_name": destination.get("model") or "未启用真实模型",
            "destination_fingerprint": destination_fingerprint,
            "model_enabled": bool(destination.get("available")),
            "max_physical_requests": 1,
            "physical_request_count": 0,
        }

    def invoke(
        self,
        *,
        preview_id: str,
        fingerprint: str,
        operation_id: str,
    ) -> dict[str, object]:
        self._validate_operation_id(operation_id)
        with self._lock:
            self._prune_previews()
            preview = self._previews.get(str(preview_id or ""))
            if preview is None:
                raise VaultError(
                    "class_teacher_work_plan_preview_not_found",
                    "AI 发送预览不存在或已过期，请重新生成",
                    status_code=404,
                )
            if str(preview["fingerprint"]) != str(fingerprint or ""):
                raise VaultError(
                    "class_teacher_work_plan_preview_changed",
                    "发送内容已经变化，请重新预览",
                    status_code=409,
                )
            claimed = preview.get("claimed_operation_id")
            if claimed is not None and str(claimed) != operation_id:
                raise VaultError(
                    "class_teacher_work_plan_preview_already_confirmed",
                    "这份发送预览已经确认，不能再次调用 AI",
                    status_code=409,
                )
            if bool(preview.get("in_flight")):
                raise VaultError(
                    "class_teacher_work_plan_in_progress",
                    "AI 正在处理这份方案，请等待当前请求返回",
                    status_code=409,
                )
            preview["claimed_operation_id"] = operation_id
            preview["in_flight"] = True

        existing = self._receipt(operation_id)
        if existing is not None:
            with self._lock:
                preview["in_flight"] = False
            if str(existing.get("preview_id")) != preview_id:
                raise VaultError(
                    "class_teacher_operation_conflict",
                    "同一操作编号不能用于不同的 AI 发送预览",
                    status_code=409,
                )
            return self._recover_receipt(operation_id, existing)

        receipt: dict[str, object] = {
            "preview_id": preview_id,
            "fingerprint": fingerprint,
            "destination_fingerprint": preview["destination_fingerprint"],
            "state": "claimed",
            "physical_request_count": 0,
            "error_category": None,
            "exact_payload": preview["exact_payload"],
            "local_context": preview["local_context"],
            "plan": None,
            "plan_fingerprint": None,
            "questions": [],
            "assumptions": [],
            "teacher_confirmation_required": False,
        }
        with self._lock:
            active_preview = self._active_operations.get(operation_id)
            if active_preview is not None and active_preview != preview_id:
                preview["in_flight"] = False
                raise VaultError(
                    "class_teacher_operation_conflict",
                    "同一操作编号不能用于不同的 AI 发送预览",
                    status_code=409,
                )
            self._active_operations[operation_id] = preview_id
        try:
            inserted = self._insert_receipt(operation_id, receipt)
        except Exception:
            with self._lock:
                if self._active_operations.get(operation_id) == preview_id:
                    self._active_operations.pop(operation_id, None)
                preview["in_flight"] = False
            raise
        if not inserted:
            with self._lock:
                if self._active_operations.get(operation_id) == preview_id:
                    self._active_operations.pop(operation_id, None)
                preview["in_flight"] = False
            existing = self._receipt(operation_id)
            if existing is None:
                raise VaultError(
                    "class_teacher_work_plan_integrity_error",
                    "AI 规划操作记录未通过完整性校验",
                    status_code=409,
                )
            if str(existing.get("preview_id")) != preview_id:
                raise VaultError(
                    "class_teacher_operation_conflict",
                    "同一操作编号不能用于不同的 AI 发送预览",
                    status_code=409,
                )
            return self._recover_receipt(operation_id, existing)

        try:
            try:
                raw_result = self.gateway.invoke(
                    payload=dict(preview["exact_payload"]),
                    operation_id=operation_id,
                    purpose="ordinary_work_plan",
                    data_classification="ordinary",
                    expected_destination_fingerprint=str(
                        preview["destination_fingerprint"]
                    ),
                )
            except ModelDestinationChanged:
                result = {
                    **receipt,
                    "state": "destination_changed",
                    "error_category": "destination_changed",
                }
            except ModelDispatchDisabled:
                result = {
                    **receipt,
                    "state": "unavailable",
                    "error_category": "model_disabled",
                }
            except ModelResultUnknown:
                result = {
                    **receipt,
                    "state": "result_unknown",
                    "physical_request_count": 1,
                    "error_category": "result_unknown",
                }
            except Exception:
                result = {
                    **receipt,
                    "state": "result_unknown",
                    "physical_request_count": 1,
                    "error_category": "result_unknown",
                }
            else:
                try:
                    parsed = self._validate_result(
                        raw_result,
                        mode=str(preview["mode"]),
                        final_due_date=(
                            None
                            if preview["final_due_date"] is None
                            else str(preview["final_due_date"])
                        ),
                    )
                except VaultError as exc:
                    result = {
                        **receipt,
                        "state": "invalid_result",
                        "physical_request_count": 1,
                        "error_category": exc.code,
                    }
                else:
                    questions = parsed["questions"]
                    assumptions = parsed["assumptions"]
                    if parsed["kind"] == "follow_up" or questions:
                        result = {
                            **receipt,
                            "state": "needs_information",
                            "physical_request_count": 1,
                            "questions": questions,
                            "assumptions": assumptions,
                        }
                    else:
                        plan = {
                            "nodes": parsed["nodes"],
                            "edges": parsed["edges"],
                            "assumptions": assumptions,
                        }
                        plan_fingerprint = hashlib.sha256(
                            canonical_json(plan)
                        ).hexdigest()
                        result = {
                            **receipt,
                            "state": "succeeded",
                            "physical_request_count": 1,
                            "plan": plan,
                            "plan_fingerprint": plan_fingerprint,
                            "assumptions": assumptions,
                            "teacher_confirmation_required": True,
                        }
            self._replace_receipt(operation_id, result)
            return self._public_result(operation_id, result)
        finally:
            with self._lock:
                if self._active_operations.get(operation_id) == preview_id:
                    self._active_operations.pop(operation_id, None)
                preview["in_flight"] = False

    def status(self, *, operation_id: str) -> dict[str, object]:
        self._validate_operation_id(operation_id)
        receipt = self._receipt(operation_id)
        if receipt is None:
            raise VaultError(
                "class_teacher_work_plan_operation_not_found",
                "AI 规划操作不存在",
                status_code=404,
            )
        with self._lock:
            if receipt.get("state") == "claimed" and operation_id in self._active_operations:
                return self._public_result(
                    operation_id,
                    {**receipt, "state": "in_progress"},
                )
        return self._recover_receipt(operation_id, receipt)

    def confirmable(
        self,
        *,
        operation_id: str,
        plan_fingerprint: str,
    ) -> dict[str, object]:
        result = self.status(operation_id=operation_id)
        if result["state"] != "succeeded" or not isinstance(result.get("plan"), dict):
            raise VaultError(
                "class_teacher_work_plan_not_confirmable",
                "AI 方案尚未成功，不能写入工作图",
                status_code=409,
            )
        if str(result.get("plan_fingerprint") or "") != str(plan_fingerprint or ""):
            raise VaultError(
                "class_teacher_work_plan_changed",
                "AI 方案已经变化，请重新核对",
                status_code=409,
            )
        return result

    def _recover_receipt(
        self,
        operation_id: str,
        receipt: dict[str, object],
    ) -> dict[str, object]:
        if receipt.get("state") == "claimed":
            receipt = {
                **receipt,
                "state": "result_unknown",
                "physical_request_count": 1,
                "error_category": "interrupted_after_claim",
            }
            self._replace_receipt(operation_id, receipt)
        return self._public_result(operation_id, receipt)

    @staticmethod
    def _public_result(
        operation_id: str,
        receipt: dict[str, object],
    ) -> dict[str, object]:
        return {
            "preview_id": str(receipt["preview_id"]),
            "operation_id": operation_id,
            "state": str(receipt["state"]),
            "physical_request_count": int(receipt["physical_request_count"]),
            "error_category": receipt.get("error_category"),
            "questions": list(receipt.get("questions") or []),
            "assumptions": list(receipt.get("assumptions") or []),
            "plan": receipt.get("plan"),
            "plan_fingerprint": receipt.get("plan_fingerprint"),
            "teacher_confirmation_required": bool(
                receipt.get("teacher_confirmation_required")
            ),
            "local_context": dict(receipt.get("local_context") or {}),
        }

    def _validate_result(
        self,
        raw_result: str,
        *,
        mode: str,
        final_due_date: str | None,
    ) -> dict[str, object]:
        try:
            decoded = json.loads(raw_result)
        except (json.JSONDecodeError, TypeError) as exc:
            raise self._invalid("AI 返回的内容不是有效 JSON") from exc
        if not isinstance(decoded, dict):
            raise self._invalid("AI 返回的方案结构无效")
        kind = str(decoded.get("kind") or "").strip()
        if kind not in {"plan", "follow_up"}:
            raise self._invalid("AI 返回了不支持的方案类型")
        questions = self._string_list(decoded.get("questions"), label="追问")
        assumptions = self._string_list(decoded.get("assumptions"), label="假设")
        self._assert_safe_model_text([*questions, *assumptions])
        if kind == "follow_up":
            if not questions:
                raise self._invalid("AI 表示信息不足但没有返回追问")
            return {
                "kind": kind,
                "questions": questions,
                "assumptions": assumptions,
                "nodes": [],
                "edges": [],
            }

        raw_nodes = decoded.get("nodes")
        raw_edges = decoded.get("edges")
        if not isinstance(raw_nodes, list) or not raw_nodes or len(raw_nodes) > 24:
            raise self._invalid("AI 方案节点数量无效")
        if not isinstance(raw_edges, list) or len(raw_edges) > 64:
            raise self._invalid("AI 方案关系数量无效")

        nodes: list[dict[str, object]] = []
        node_ids: set[str] = set()
        for raw in raw_nodes:
            if not isinstance(raw, dict):
                raise self._invalid("AI 方案节点结构无效")
            node_id = str(raw.get("id") or "").strip()
            if _DRAFT_KEY.fullmatch(node_id) is None or node_id in node_ids:
                raise self._invalid("AI 方案节点编号无效或重复")
            node_ids.add(node_id)
            node_kind = str(raw.get("kind") or "").strip()
            status = str(raw.get("status") or "").strip()
            if node_kind not in _NODE_KINDS or status not in _INITIAL_STATUSES:
                raise self._invalid("AI 方案节点类型或初始状态无效")
            title = self._bounded_text(raw.get("title"), label="节点标题", maximum=160)
            details_raw = raw.get("details")
            details = (
                None
                if details_raw is None
                else self._bounded_text(details_raw, label="节点说明", maximum=800)
            )
            due_date = self._plan_date(raw.get("due_date"), final_due_date)
            nodes.append(
                {
                    "draft_key": node_id,
                    "kind": node_kind,
                    "title": title,
                    "details": details,
                    "status": status,
                    "due_date": due_date,
                }
            )

        goal_ids = {
            str(node["draft_key"])
            for node in nodes
            if node["kind"] == "goal"
        }
        if mode == "new_work" and len(goal_ids) != 1:
            raise self._invalid("新工作方案必须且只能包含一个目标节点")
        if mode == "progress_update" and goal_ids:
            raise self._invalid("最新情况分支不能新建另一个目标节点")

        edges: list[dict[str, str]] = []
        edge_keys: set[tuple[str, str, str]] = set()
        adjacency: dict[str, set[str]] = {node_id: set() for node_id in node_ids}
        undirected: dict[str, set[str]] = {node_id: set() for node_id in node_ids}
        for raw in raw_edges:
            if not isinstance(raw, dict):
                raise self._invalid("AI 方案关系结构无效")
            source = str(raw.get("source_id") or "").strip()
            target = str(raw.get("target_id") or "").strip()
            relation = str(raw.get("relation") or "").strip()
            if (
                source not in node_ids
                or target not in node_ids
                or source == target
                or relation not in _EDGE_RELATIONS
            ):
                raise self._invalid("AI 方案关系引用了无效节点")
            key = (source, target, relation)
            if key in edge_keys:
                raise self._invalid("AI 方案包含重复关系")
            edge_keys.add(key)
            adjacency[source].add(target)
            undirected[source].add(target)
            undirected[target].add(source)
            edges.append(
                {
                    "source_draft_key": source,
                    "target_draft_key": target,
                    "relation": relation,
                }
            )
        self._assert_acyclic(adjacency)
        if len(node_ids) > 1:
            start = next(iter(goal_ids or node_ids))
            reached = {start}
            pending = [start]
            while pending:
                current = pending.pop()
                for neighbor in undirected[current] - reached:
                    reached.add(neighbor)
                    pending.append(neighbor)
            if reached != node_ids:
                raise self._invalid("AI 方案存在与主流程无关的孤立节点")

        all_text = " ".join(
            [
                *(str(node["title"]) for node in nodes),
                *(str(node.get("details") or "") for node in nodes),
                *questions,
                *assumptions,
            ]
        )
        self._assert_safe_model_text([all_text])
        return {
            "kind": kind,
            "questions": questions,
            "assumptions": assumptions,
            "nodes": nodes,
            "edges": edges,
        }

    @staticmethod
    def _assert_acyclic(adjacency: dict[str, set[str]]) -> None:
        indegree = {node_id: 0 for node_id in adjacency}
        for targets in adjacency.values():
            for target in targets:
                indegree[target] += 1
        pending = [node_id for node_id, degree in indegree.items() if degree == 0]
        visited = 0
        while pending:
            current = pending.pop()
            visited += 1
            for target in adjacency[current]:
                indegree[target] -= 1
                if indegree[target] == 0:
                    pending.append(target)
        if visited != len(adjacency):
            raise WorkPlanning._invalid("AI 方案关系形成了循环")

    @staticmethod
    def _string_list(value: object, *, label: str) -> list[str]:
        if not isinstance(value, list) or len(value) > 8:
            raise WorkPlanning._invalid(f"AI 返回的{label}列表无效")
        return [
            WorkPlanning._bounded_text(item, label=label, maximum=240)
            for item in value
        ]

    @staticmethod
    def _bounded_text(value: object, *, label: str, maximum: int) -> str:
        clean = " ".join(str(value or "").split())
        if not clean or len(clean) > maximum:
            raise WorkPlanning._invalid(f"AI 返回的{label}无效")
        return clean

    @staticmethod
    def _assert_safe_model_text(values: list[str]) -> None:
        findings: list[str] = []
        for value in values:
            findings.extend(SensitiveContentPolicy.model_output_findings(value))
        if findings:
            raise WorkPlanning._invalid(
                "AI 返回内容包含敏感身份或禁止由模型决定的事项"
            )

    @staticmethod
    def _plan_date(value: object, final_due_date: str | None) -> str | None:
        if value is None or not str(value).strip():
            return None
        try:
            parsed = date.fromisoformat(str(value).strip()).isoformat()
        except ValueError as exc:
            raise WorkPlanning._invalid("AI 返回了无效日期") from exc
        if final_due_date is None:
            raise WorkPlanning._invalid("AI 在没有最终日期时编造了节点日期")
        if parsed > final_due_date:
            raise WorkPlanning._invalid("AI 返回的节点日期晚于最终日期")
        return parsed

    @staticmethod
    def _invalid(message: str) -> VaultError:
        return VaultError(
            "class_teacher_work_plan_invalid_result",
            message,
            status_code=422,
        )

    def _insert_receipt(
        self,
        operation_id: str,
        receipt: dict[str, object],
    ) -> bool:
        self.database.initialize_schema() if not self.database.exists else None
        with closing(self.database.connect()) as connection:
            try:
                with connection:
                    connection.execute(
                        """
                        INSERT INTO work_operations (
                            operation_id, operation_type, result_json, created_at
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (
                            operation_id,
                            _CALL_OPERATION_TYPE,
                            json.dumps(receipt, ensure_ascii=False, sort_keys=True),
                            _iso(),
                        ),
                    )
                return True
            except Exception:
                existing = self._receipt(operation_id)
                if existing is None:
                    raise
                return False

    def _replace_receipt(self, operation_id: str, receipt: dict[str, object]) -> None:
        with closing(self.database.connect()) as connection:
            with connection:
                changed = connection.execute(
                    """
                    UPDATE work_operations
                    SET result_json = ?
                    WHERE operation_id = ? AND operation_type = ?
                    """,
                    (
                        json.dumps(receipt, ensure_ascii=False, sort_keys=True),
                        operation_id,
                        _CALL_OPERATION_TYPE,
                    ),
                ).rowcount
        if changed != 1:
            raise VaultError(
                "class_teacher_work_plan_integrity_error",
                "AI 规划操作记录未通过完整性校验",
                status_code=409,
            )

    def _receipt(self, operation_id: str) -> dict[str, object] | None:
        if not self.database.exists:
            return None
        with closing(self.database.connect()) as connection:
            row = connection.execute(
                """
                SELECT operation_type, result_json FROM work_operations
                WHERE operation_id = ?
                """,
                (operation_id,),
            ).fetchone()
        if row is None:
            return None
        if str(row["operation_type"]) != _CALL_OPERATION_TYPE:
            raise VaultError(
                "class_teacher_operation_conflict",
                "同一操作编号不能用于不同操作",
                status_code=409,
            )
        try:
            decoded = json.loads(str(row["result_json"]))
        except json.JSONDecodeError as exc:
            raise VaultError(
                "class_teacher_work_plan_integrity_error",
                "AI 规划操作记录未通过完整性校验",
                status_code=409,
            ) from exc
        if not isinstance(decoded, dict):
            raise VaultError(
                "class_teacher_work_plan_integrity_error",
                "AI 规划操作记录未通过完整性校验",
                status_code=409,
            )
        return decoded

    def _prune_previews(self) -> None:
        now = _now()
        expired = [
            preview_id
            for preview_id, preview in self._previews.items()
            if datetime.fromisoformat(str(preview["expires_at"])) <= now
            and not bool(preview.get("in_flight"))
        ]
        for preview_id in expired:
            self._previews.pop(preview_id, None)

    def _gateway_destination(self) -> dict[str, object]:
        snapshot = getattr(self.gateway, "destination_snapshot", None)
        if callable(snapshot):
            value = snapshot()
            if isinstance(value, dict) and isinstance(
                value.get("destination_fingerprint"),
                str,
            ):
                return dict(value)
        available = self.gateway.is_available()
        model = self.gateway.model_name if available else None
        return _destination_identity(
            available=available,
            model_provider=None,
            model_endpoint=None,
            model=model,
        )

    @staticmethod
    def _validate_operation_id(operation_id: str) -> None:
        if _OPERATION_ID.fullmatch(str(operation_id or "")) is None:
            raise VaultError(
                "class_teacher_operation_id_invalid",
                "操作编号无效",
                status_code=422,
            )


__all__ = [
    "DisabledWorkPlanningGateway",
    "FakeWorkPlanningGateway",
    "WorkPlanning",
    "WorkPlanningGateway",
]
