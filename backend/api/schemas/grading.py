from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class GradingRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grading_mode: Literal["full_paper", "hybrid_batch"] = "full_paper"
    failed_only: bool = False
    enhance_images: bool = True
    max_workers: int | None = Field(default=None, ge=1, le=32)
    requests_per_minute: int | None = Field(default=None, ge=1, le=1000)
    resume_run_id: int | None = Field(default=None, ge=1)
    upload_revision: int | None = Field(default=None, ge=0)
    decision_revision: int | None = Field(default=None, ge=0)
    confirm_pending_issues: bool = False


class GradingRunCancelRequest(BaseModel):
    job_id: int = Field(gt=0)
