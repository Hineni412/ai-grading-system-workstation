from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ScanAnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enhance_images: bool = True
    ocr_workers: int | None = Field(default=None, ge=1, le=32)
    front_page_parity: Literal["odd", "even"] | None = None


class ScanUploadFileResponse(BaseModel):
    id: str
    name: str
    media_type: str
    size_bytes: int = Field(ge=0)
    sha256_prefix: str = Field(pattern=r"^[0-9a-f]{12}$")
    added_at: str


class ScanUploadBatchResponse(BaseModel):
    batch_id: str
    revision: int = Field(ge=0)
    state: Literal["draft", "frozen"]
    files: list[ScanUploadFileResponse]
    file_count: int = Field(ge=0)
    total_bytes: int = Field(ge=0)
    frozen_at: str | None = None


class ScanUploadResponse(BaseModel):
    duplicate: bool
    file: ScanUploadFileResponse


class ScanUploadFreezeRequest(BaseModel):
    expected_revision: int = Field(ge=0)


class ScanGradingWorkspaceResponse(BaseModel):
    session_id: int = Field(gt=0)
    upload_batch: ScanUploadBatchResponse
    grading_run: "GradingRunSummaryResponse | None" = None
    scan_analysis_job: "ScanAnalysisJobSummaryResponse | None" = None


class ScanAnalysisJobSummaryResponse(BaseModel):
    id: int = Field(gt=0)
    status: str
    progress: float = Field(ge=0, le=1)
    updated_at: str
    cancel_requested: bool
    scan_batch_id: str = Field(min_length=1)


class GradingRunCountsResponse(BaseModel):
    graded: int = Field(ge=0)
    grading: int = Field(ge=0)
    pending: int = Field(ge=0)
    skipped: int = Field(ge=0)
    failed: int = Field(ge=0)
    conflict: int = Field(ge=0)
    total: int = Field(ge=0)


class GradingRunSummaryResponse(BaseModel):
    run_id: int = Field(gt=0)
    job_id: int | None = Field(default=None, gt=0)
    job_status: str | None = None
    progress: float | None = Field(default=None, ge=0, le=1)
    started_at: str | None = None
    updated_at: str | None = None
    mode: Literal["full_paper", "hybrid_batch"]
    state: str
    counts: GradingRunCountsResponse
    allowed_actions: list[str]


class ScanDecisionItem(BaseModel):
    target_type: Literal["group", "issue"]
    target_id: str = Field(min_length=1)
    action: Literal["match", "invalid", "pending"]
    student_id: int | None = Field(default=None, gt=0)


class ScanDecisionRequest(BaseModel):
    expected_revision: int = Field(ge=0)
    decisions: list[ScanDecisionItem] = Field(default_factory=list)


class ScanDecisionResponse(BaseModel):
    revision: int = Field(ge=0)
    decisions: list[ScanDecisionItem]
    pending_issue_count: int = Field(ge=0)


class ScanPreflightResponse(BaseModel):
    revision: int = Field(ge=0)
    summary: dict[str, int]
    groups: list[dict[str, Any]]
    issues: list[dict[str, Any]]
    absent_students: list[dict[str, Any]]
    warnings: list[str]
    decisions: list[ScanDecisionItem]
    pending_issue_count: int = Field(ge=0)
