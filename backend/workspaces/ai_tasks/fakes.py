from __future__ import annotations

from dataclasses import dataclass, field

from .model_gateway import WorkspaceAITaskModelGateway
from .models import (
    AdapterResult,
    AdoptionResult,
    HandoffSnapshot,
    StoredTask,
)


@dataclass(slots=True)
class FakeWorkspaceAITaskAdapter:
    module: str
    result: AdapterResult
    calls: list[str] = field(default_factory=list)
    receipts: dict[str, AdoptionResult] = field(default_factory=dict)
    fail_execute: Exception | None = None
    fail_after_receipt: bool = False
    persisted_results: dict[str, AdapterResult] = field(default_factory=dict)
    discarded_unknown_operations: list[str] = field(default_factory=list)

    def execute(
        self,
        task: StoredTask,
        *,
        model_gateway: WorkspaceAITaskModelGateway,
    ) -> AdapterResult:
        del model_gateway
        self.calls.append(task.operation_id)
        if self.fail_execute is not None:
            raise self.fail_execute
        self.persisted_results[task.task_id] = self.result
        return self.result

    def recover(self, task: StoredTask) -> AdapterResult | None:
        return self.persisted_results.get(task.task_id)

    def discard_result_unknown(self, task: StoredTask) -> None:
        self.discarded_unknown_operations.append(task.operation_id)

    def adopt(
        self,
        handoff: HandoffSnapshot,
        *,
        adoption_id: str,
        draft_revision: str,
        target_revision: str,
    ) -> AdoptionResult:
        existing = self.receipts.get(adoption_id)
        if existing is not None:
            return existing
        result = AdoptionResult(
            adoption_id=adoption_id,
            object_ref=f"{self.module}:object:{handoff.work_item_id}",
            receipt_revision="1",
            target_revision=target_revision,
        )
        self.receipts[adoption_id] = result
        if self.fail_after_receipt:
            self.fail_after_receipt = False
            raise RuntimeError("synthetic response loss after domain commit")
        return result

    def find_adoption(self, adoption_id: str) -> AdoptionResult | None:
        return self.receipts.get(adoption_id)


__all__ = ["FakeWorkspaceAITaskAdapter"]
