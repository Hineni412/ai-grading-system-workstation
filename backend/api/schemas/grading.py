from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class GradingRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grading_mode: Literal["ai"] = "ai"
    failed_only: bool = False
    enhance_images: bool = True
    max_workers: int | None = Field(default=None, ge=1, le=100)
    requests_per_minute: int | None = Field(default=None, ge=1, le=10_000)
    resume_run_id: int | None = Field(default=None, ge=1)
    upload_revision: int | None = Field(default=None, ge=0)
    decision_revision: int | None = Field(default=None, ge=0)
    confirm_pending_issues: bool = False


class GradingPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grading_mode: Literal["ai", "manual"]


class GradingPlanIssueResponse(BaseModel):
    code: str
    message: str


class GradingPlanResponse(BaseModel):
    session_id: int
    scan_batch_id: str
    upload_revision: int
    decision_revision: int
    mode: Literal["ai", "full_paper", "manual", "hybrid_batch"]
    status: Literal["ready", "blocked"]
    counts: dict[str, int]
    requests: dict[str, int]
    batching: dict[str, int]
    warnings: list[GradingPlanIssueResponse]
    blockers: list[GradingPlanIssueResponse]
    created_at: str


class GradingRunCancelRequest(BaseModel):
    job_id: int | None = Field(default=None, gt=0)
