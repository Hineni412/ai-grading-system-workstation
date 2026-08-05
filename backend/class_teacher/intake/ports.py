from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from backend.workspaces.ai_tasks.models import OpaqueRef, PrepareRequest


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

    def mark_handoff(self, *, handoff_id: str, state: str) -> None: ...


class UnavailableWorkspaceAITaskPort:
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

    def mark_handoff(self, *, handoff_id: str, state: str) -> None:
        raise RuntimeError("workspace_ai_task_port_unavailable")


class SharedWorkspaceAITaskPort:
    """B-owned translation from the frozen domain seam to TW-F1.

    TW-F1 intentionally stores only opaque references.  The two B operations
    share its registered ``class_teacher.intake`` presentation; the Adapter
    distinguishes triage from draft revision by the domain context refs.
    """

    def __init__(self, service, *, model_identity, conversations) -> None:
        self.service = service
        self.model_identity = model_identity
        self.conversations = conversations

    def prepare(self, *, operation_id: str, request: dict[str, object]) -> PreparedTask:
        destination = self.model_identity.destination_snapshot()
        snapshot = self.service.prepare(
            operation_id,
            PrepareRequest(
                module="class_teacher",
                task_kind="class_teacher.intake",
                source_ref=_ref(request.get("source_ref")),
                context_refs=tuple(_ref(item) for item in _refs(request.get("context_refs"))),
                prompt_contract_version=str(request.get("prompt_contract_version") or ""),
                model_destination_fingerprint=str(destination.get("destination_fingerprint") or ""),
                return_target=str(request.get("return_target") or "class_teacher.home"),
            ),
        )
        return PreparedTask(snapshot.task_id, "")

    def dispatch(
        self,
        *,
        operation_id: str,
        prepared_task_id: str,
        request_fingerprint: str,
    ) -> TaskSnapshot:
        del request_fingerprint
        value = self.service.dispatch(operation_id, prepared_task_id=prepared_task_id)
        self._bind_handoffs(value)
        return _snapshot(value)

    def get(self, *, task_id: str | None = None, operation_id: str | None = None) -> TaskSnapshot:
        value = self.service.get(task_id=task_id, operation_id=operation_id)
        self._bind_handoffs(value)
        return _snapshot(value)

    def cancel(self, *, operation_id: str) -> TaskSnapshot:
        return _snapshot(self.service.cancel(operation_id))

    def adopt(self, *, handoff_id: str, draft_revision: int, target_revision: str) -> dict[str, object]:
        common = self._common_handoff(handoff_id)
        result = self.service.adopt(
            common.handoff_id,
            module="class_teacher",
            draft_revision=str(draft_revision),
            target_revision=str(target_revision),
        )
        return {
            "adoption_id": result.adoption_id,
            "object_ref": result.object_ref,
            "receipt_revision": result.receipt_revision,
            "target_revision": result.target_revision,
        }

    def mark_handoff(self, *, handoff_id: str, state: str) -> None:
        common = self._common_handoff(handoff_id)
        if common.adoption_state == state:
            return
        self.service.mark_handoff(common.handoff_id, state)

    def _common_handoff(self, domain_handoff_id: str):
        domain = self.conversations.handoff_for_adapter(domain_handoff_id)
        task_id = self.conversations.task_id_for_handoff(domain_handoff_id)
        snapshot = self.service.get(task_id=task_id)
        self._bind_handoffs(snapshot)
        match = next(
            (item for item in snapshot.handoffs if item.work_item_id == domain["work_item_id"]),
            None,
        )
        if match is None:
            raise RuntimeError("workspace_ai_handoff_unavailable")
        return match

    def _bind_handoffs(self, snapshot) -> None:
        for handoff in snapshot.handoffs:
            try:
                previous = self.conversations.bind_common_handoff(
                    handoff.draft_ref.id,
                    handoff.handoff_id,
                )
                if previous and previous != handoff.handoff_id:
                    try:
                        self.service.mark_handoff(previous, "stale")
                    except Exception:
                        pass
            except Exception:
                # A safe task projection can be read before the B proposal is visible.
                continue


class FakeWorkspaceAITaskPort:
    def __init__(self, *, state: str = "queued") -> None:
        self.state = state
        self.prepare_calls: list[dict[str, object]] = []
        self.dispatch_calls: list[dict[str, str]] = []
        self.adopt_calls: list[dict[str, object]] = []
        self.mark_calls: list[dict[str, str]] = []
        self._by_operation: dict[str, PreparedTask] = {}
        self._adopt_handler: Callable[..., dict[str, object]] | None = None

    def bind_adoption(self, handler: Callable[..., dict[str, object]]) -> None:
        self._adopt_handler = handler

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
        return TaskSnapshot(prepared.task_id, "cancelled_before_dispatch", "not_started")

    def adopt(self, *, handoff_id: str, draft_revision: int, target_revision: str) -> dict[str, object]:
        call = {
            "handoff_id": handoff_id,
            "draft_revision": draft_revision,
            "target_revision": target_revision,
        }
        self.adopt_calls.append(call)
        if self._adopt_handler is None:
            return call
        return self._adopt_handler(
            token="",
            handoff_id=handoff_id,
            draft_revision=draft_revision,
            target_revision=target_revision,
            operation_id=f"fake-{handoff_id}",
            adoption_id=f"fake-{handoff_id}",
        )

    def mark_handoff(self, *, handoff_id: str, state: str) -> None:
        self.mark_calls.append({"handoff_id": handoff_id, "state": state})


def _refs(value: object) -> list[object]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _ref(value: object) -> OpaqueRef:
    if not isinstance(value, dict):
        raise ValueError("workspace AI reference is invalid")
    return OpaqueRef(
        kind=str(value.get("kind") or ""),
        id=str(value.get("id") or ""),
        revision=str(value.get("revision") or ""),
    )


def _snapshot(value) -> TaskSnapshot:
    return TaskSnapshot(value.task_id, value.status, value.dispatch_evidence)


__all__ = [
    "FakeWorkspaceAITaskPort",
    "PreparedTask",
    "SharedWorkspaceAITaskPort",
    "TaskSnapshot",
    "UnavailableWorkspaceAITaskPort",
    "WorkspaceAITaskPort",
]
