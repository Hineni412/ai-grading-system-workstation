from __future__ import annotations

from typing import Protocol

from .model_gateway import WorkspaceAITaskModelGateway
from .models import AdapterResult, AdoptionResult, HandoffSnapshot, StoredTask


class WorkspaceAITaskAdapter(Protocol):
    module: str

    def execute(
        self,
        task: StoredTask,
        *,
        model_gateway: WorkspaceAITaskModelGateway,
    ) -> AdapterResult:
        """Read domain-owned input, call the model seam, and persist a proposal."""

    def recover(self, task: StoredTask) -> AdapterResult | None:
        """Load an already-persisted proposal without issuing another model request."""

    def adopt(
        self,
        handoff: HandoffSnapshot,
        *,
        adoption_id: str,
        draft_revision: str,
        target_revision: str,
    ) -> AdoptionResult:
        """Persist the domain object and receipt in one domain transaction."""

    def find_adoption(self, adoption_id: str) -> AdoptionResult | None:
        """Return a domain receipt after a cross-database response loss."""
