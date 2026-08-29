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


class JobSummaryResponse(BaseModel):
    id: int
    job_type: str
    status: str
    progress: float
    stage: str
    detail: str
    created_at: str
    started_at: str | None = None
    updated_at: str
    finished_at: str | None = None


class JobSummaryListResponse(BaseModel):
    items: list[JobSummaryResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class JobStatusBatchRequest(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=50)


class JobStatusBatchItem(BaseModel):
    id: int
    found: bool
    job: JobResponse | None = None


class JobStatusBatchResponse(BaseModel):
    items: list[JobStatusBatchItem]
