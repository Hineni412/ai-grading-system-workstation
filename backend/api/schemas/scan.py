from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ScanAnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enhance_images: bool = True
    ocr_workers: int | None = Field(default=None, ge=1, le=100)
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
    replacement_batch: ScanUploadBatchResponse | None = None
    grading_run: "GradingRunSummaryResponse | None" = None
    grading_job: "GradingJobSummaryResponse | None" = None
    scan_analysis_job: "ScanAnalysisJobSummaryResponse | None" = None


class GradingJobSummaryResponse(BaseModel):
    id: int = Field(gt=0)
    status: str
    progress: float = Field(ge=0, le=1)
    updated_at: str
    cancel_requested: bool
    scan_batch_id: str = Field(min_length=1)


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
    mode: Literal["ai", "full_paper", "hybrid_batch"]
    state: str
    counts: GradingRunCountsResponse
    allowed_actions: list[str]
    incomplete_result_count: int = Field(default=0, ge=0)
    incomplete_item_count: int = Field(default=0, ge=0)


class ScanDecisionItem(BaseModel):
    target_type: Literal["group", "issue"]
    target_id: str = Field(min_length=1)
    action: Literal["match", "invalid", "pending"]
    student_id: int | None = Field(default=None, gt=0)


class ScanDecisionRequest(BaseModel):
    expected_revision: int = Field(ge=0)
    decisions: list[ScanDecisionItem] = Field(default_factory=list)
    allow_partial_matches: bool = False


class ScanDecisionResponse(BaseModel):
    revision: int = Field(ge=0)
    decisions: list[ScanDecisionItem]
    pending_issue_count: int = Field(ge=0)
    ready_to_grade: int = Field(ge=0)
    summary: ScanPreflightSummaryResponse
    absent_students: list[dict[str, Any]]
    match_conflicts: list[dict[str, Any]]
    rejected_conflicts: list[dict[str, Any]] = Field(default_factory=list)


class ScanPreflightSummaryResponse(BaseModel):
    auto_matched: int = Field(ge=0)
    ready_to_grade: int = Field(ge=0)
    issues: int = Field(ge=0)
    absent_candidates: int = Field(ge=0)
    total_pages: int = Field(ge=0)
    scanned_papers: int = Field(default=0, ge=0)
    matched_papers: int = Field(default=0, ge=0)
    unique_students: int = Field(default=0, ge=0)
    invalid_papers: int = Field(default=0, ge=0)
    unresolved_papers: int = Field(default=0, ge=0)
    conflicting_papers: int = Field(default=0, ge=0)


class ScanPageAssignmentResponse(BaseModel):
    first_page_role: Literal["front", "back"]
    front_page_parity: Literal["odd", "even"]


class ScanPreflightIdentityResponse(BaseModel):
    method: str
    auto: int = Field(ge=0)
    needs_confirmation: int = Field(ge=0)
    model_requests: int = Field(ge=0)


class ScanPreflightResponse(BaseModel):
    revision: int = Field(ge=0)
    summary: ScanPreflightSummaryResponse
    page_assignment: ScanPageAssignmentResponse
    groups: list[dict[str, Any]]
    issues: list[dict[str, Any]]
    absent_students: list[dict[str, Any]]
    warnings: list[str]
    decisions: list[ScanDecisionItem]
    pending_issue_count: int = Field(ge=0)
    match_conflicts: list[dict[str, Any]] = Field(default_factory=list)
    identity: ScanPreflightIdentityResponse | None = None


class ScanStudentMatchOptionResponse(BaseModel):
    id: int = Field(gt=0)
    student_code: str
    name: str
    class_name: str | None = None
    pinyin_initials: str
    pinyin_full: str


class ScanStudentMatchOptionsResponse(BaseModel):
    items: list[ScanStudentMatchOptionResponse]
