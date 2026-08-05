"""Metadata-only AI task coordination shared by teacher workspaces."""

from .models import (
    AdapterResult,
    AdoptionResult,
    HandoffDraft,
    PrepareRequest,
    TaskSnapshot,
)
from .service import WorkspaceAITaskService

__all__ = [
    "AdapterResult",
    "AdoptionResult",
    "HandoffDraft",
    "PrepareRequest",
    "TaskSnapshot",
    "WorkspaceAITaskService",
]
