from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class GradingRunRequest(BaseModel):
    grading_mode: Literal["full_paper", "hybrid_batch"] = "full_paper"
    failed_only: bool = False
    enhance_images: bool = True
    max_workers: int | None = Field(default=None, ge=1, le=32)
    requests_per_minute: int | None = Field(default=None, ge=1, le=1000)
    exams_dir: str | None = None
    resume_run_id: int | None = Field(default=None, ge=1)
