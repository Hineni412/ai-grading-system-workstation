from __future__ import annotations

from pydantic import BaseModel, Field


class HomeroomPreferenceUpdate(BaseModel):
    homeroom_class: str | None = Field(default=None, max_length=240)
    expected_revision: int = Field(ge=0)
    expected_source_revision: str = Field(min_length=64, max_length=64)
    operation_id: str = Field(min_length=8, max_length=128)


class ConversationStartRequest(BaseModel):
    subject_id: str | None = Field(default=None, min_length=8, max_length=128)


class TurnAppendRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    message: str = Field(min_length=1, max_length=4000)
    operation_id: str = Field(min_length=8, max_length=128)


class ManualRouteRequest(BaseModel):
    mode: str


class DraftUpdateRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    content: dict[str, object]
    subject_refs: list[dict[str, str]] | None = Field(default=None, max_length=50)


class DraftAIRevisionRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    instruction: str = Field(min_length=1, max_length=2000)
    operation_id: str = Field(min_length=8, max_length=128)


class HandoffAdoptRequest(BaseModel):
    draft_revision: int = Field(ge=1)
    target_revision: str = Field(min_length=1, max_length=128)
    operation_id: str = Field(min_length=8, max_length=128)


__all__ = [
    "ConversationStartRequest",
    "DraftAIRevisionRequest",
    "DraftUpdateRequest",
    "HandoffAdoptRequest",
    "HomeroomPreferenceUpdate",
    "ManualRouteRequest",
    "TurnAppendRequest",
]
