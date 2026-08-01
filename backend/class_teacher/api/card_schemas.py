from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class PortraitPayload(BaseModel):
    summary: str = Field(min_length=1, max_length=4000)
    strengths: list[str] = Field(default_factory=list, max_length=30)
    needs: list[str] = Field(default_factory=list, max_length=30)
    open_questions: list[str] = Field(default_factory=list, max_length=30)


class SopPayload(BaseModel):
    title: str = Field(min_length=1, max_length=1000)
    steps: list[str] = Field(min_length=1, max_length=30)
    review_date: str | None = None


class StudentCardConfirmRequest(BaseModel):
    model_operation_id: str = Field(min_length=8, max_length=128)
    operation_id: str = Field(min_length=8, max_length=128)
    portrait: PortraitPayload
    sop: SopPayload


class StudentCardEntryResponse(BaseModel):
    entry_id: str
    subject_id: str
    revision: int
    model_operation_id: str
    teacher_quote: str
    portrait: dict[str, object]
    sop: dict[str, object]
    model_draft: str
    teacher_confirmed_at: str
    projection_state: Literal["pending", "applied"]
    created_at: str
    saved: bool = True


class StudentCardResponse(BaseModel):
    subject: dict[str, object]
    entries: list[dict[str, object]]
    existing_records: list[dict[str, object]]
    support_plans: list[dict[str, object]]


class StudentCardListResponse(BaseModel):
    items: list[StudentCardResponse]


__all__ = [
    "StudentCardConfirmRequest",
    "StudentCardEntryResponse",
    "StudentCardListResponse",
]
