from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class JobSubmitRequest(BaseModel):
    payload: dict[str, Any] = Field(default_factory=dict)


class JobResponse(BaseModel):
    id: int
    job_type: str
    payload: dict[str, Any]
    result: dict[str, Any]
    status: str
    progress: float
    stage: str
    detail: str
    error: str | None = None
    cancel_requested: bool
    created_at: str
    started_at: str | None = None
    updated_at: str
    finished_at: str | None = None
