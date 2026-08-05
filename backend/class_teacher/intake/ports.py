from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class PreparedTask:
    task_id: str
    request_fingerprint: str


@dataclass(frozen=True, slots=True)
class TaskSnapshot:
    task_id: str
    state: str
    dispatch_evidence: str = "not_started"


class WorkspaceAITaskPort(Protocol):
    def prepare(self, *, operation_id: str, request: dict[str, object]) -> PreparedTask: ...

    def dispatch(
        self,
        *,
        operation_id: str,
        prepared_task_id: str,
        request_fingerprint: str,
    ) -> TaskSnapshot: ...

    def get(self, *, task_id: str | None = None, operation_id: str | None = None) -> TaskSnapshot: ...

    def cancel(self, *, operation_id: str) -> TaskSnapshot: ...

    def adopt(
        self,
        *,
        handoff_id: str,
        draft_revision: int,
        target_revision: str,
    ) -> dict[str, object]: ...


class UnavailableWorkspaceAITaskPort:
    """Frozen seam used until TW-F1 is synchronized into this branch."""

    def prepare(self, *, operation_id: str, request: dict[str, object]) -> PreparedTask:
        raise RuntimeError("workspace_ai_task_port_unavailable")

    def dispatch(
        self,
        *,
        operation_id: str,
        prepared_task_id: str,
        request_fingerprint: str,
    ) -> TaskSnapshot:
        raise RuntimeError("workspace_ai_task_port_unavailable")

    def get(self, *, task_id: str | None = None, operation_id: str | None = None) -> TaskSnapshot:
        raise RuntimeError("workspace_ai_task_port_unavailable")

    def cancel(self, *, operation_id: str) -> TaskSnapshot:
        raise RuntimeError("workspace_ai_task_port_unavailable")

    def adopt(self, *, handoff_id: str, draft_revision: int, target_revision: str) -> dict[str, object]:
        raise RuntimeError("workspace_ai_task_port_unavailable")


class FakeWorkspaceAITaskPort:
    def __init__(self, *, state: str = "queued") -> None:
        self.state = state
        self.prepare_calls: list[dict[str, object]] = []
        self.dispatch_calls: list[dict[str, str]] = []
        self.adopt_calls: list[dict[str, object]] = []
        self._by_operation: dict[str, PreparedTask] = {}

    def prepare(self, *, operation_id: str, request: dict[str, object]) -> PreparedTask:
        self.prepare_calls.append(dict(request))
        existing = self._by_operation.get(operation_id)
        if existing is not None:
            return existing
        prepared = PreparedTask(f"task-{operation_id}", f"fingerprint-{operation_id}")
        self._by_operation[operation_id] = prepared
        return prepared

    def dispatch(
        self,
        *,
        operation_id: str,
        prepared_task_id: str,
        request_fingerprint: str,
    ) -> TaskSnapshot:
        if not any(item["operation_id"] == operation_id for item in self.dispatch_calls):
            self.dispatch_calls.append({
                "operation_id": operation_id,
                "prepared_task_id": prepared_task_id,
                "request_fingerprint": request_fingerprint,
            })
        return TaskSnapshot(prepared_task_id, self.state)

    def get(self, *, task_id: str | None = None, operation_id: str | None = None) -> TaskSnapshot:
        prepared = self._by_operation.get(str(operation_id or ""))
        resolved_task_id = str(task_id or (prepared.task_id if prepared else ""))
        if not resolved_task_id:
            raise KeyError("task_not_found")
        return TaskSnapshot(resolved_task_id, self.state)

    def cancel(self, *, operation_id: str) -> TaskSnapshot:
        prepared = self._by_operation.get(operation_id)
        if prepared is None:
            raise KeyError("task_not_found")
        return TaskSnapshot(prepared.task_id, "cancelled", "not_started")

    def adopt(self, *, handoff_id: str, draft_revision: int, target_revision: str) -> dict[str, object]:
        call = {
            "handoff_id": handoff_id,
            "draft_revision": draft_revision,
            "target_revision": target_revision,
        }
        self.adopt_calls.append(call)
        return call


__all__ = [
    "FakeWorkspaceAITaskPort",
    "PreparedTask",
    "TaskSnapshot",
    "UnavailableWorkspaceAITaskPort",
    "WorkspaceAITaskPort",
]
