from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class TemplateUpdateRequest(BaseModel):
    front_template_path: str
    back_template_path: str
    ai_analysis_path: str | None = None
    template_config_path: str | None = None
    regions_path: str | None = None

    @field_validator("front_template_path", "back_template_path")
    @classmethod
    def _required_text(cls, value: str) -> str:
        clean = str(value or "").strip()
        if not clean:
            raise ValueError("must be nonblank")
        return clean

    @field_validator("ai_analysis_path", "template_config_path", "regions_path")
    @classmethod
    def _optional_text(cls, value: str | None) -> str | None:
        clean = str(value or "").strip()
        return clean or None


class TemplatePageResponse(BaseModel):
    url: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class TemplateUploadResponse(BaseModel):
    session_id: int = Field(gt=0)
    template_id: int = Field(gt=0)
    template_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    first_page_role: Literal["front", "back"]
    pages: dict[Literal["front", "back"], TemplatePageResponse]
    is_confirmed: bool
    regions_snapshot_pending: bool


class TemplateUploadSubmissionResponse(BaseModel):
    status: Literal["processing", "succeeded", "failed", "replaced", "abandoned"]
    template: TemplateUploadResponse | None = None


class TemplatePageAssignmentRequest(BaseModel):
    first_page_role: Literal["front", "back"]
    expected_template_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class TemplatePageAssignmentResponse(BaseModel):
    changed: bool
    draft_sync_pending: bool
    template: TemplateUploadResponse


class RegionReadinessResponse(BaseModel):
    session_id: int = Field(gt=0)
    scoring_configured: bool
    template_present: bool
    template_ready: bool


class RegionDraftStateResponse(BaseModel):
    status: Literal["missing", "compatible", "incompatible", "corrupt"]
    revision: int = Field(ge=0)
    regions: list[dict[str, Any]]


class QuestionBindingOptionResponse(BaseModel):
    value: str
    label: str


class RegionWorkspaceResponse(BaseModel):
    session_id: int = Field(gt=0)
    template: TemplateUploadResponse
    formal_regions: list[dict[str, Any]]
    draft: RegionDraftStateResponse
    automatic_candidates: list[str]
    manual_question_options: list[QuestionBindingOptionResponse]
    issues: list[RegionIssueResponse]
    template_ready: bool


class RegionDraftRequest(BaseModel):
    revision: int = Field(default=0, ge=0)
    regions: list[dict[str, Any]] = Field(default_factory=list)
    expected_template_fingerprint: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    expected_revision: int | None = Field(default=None, ge=0)


class RegionDraftResponse(BaseModel):
    status: str
    session_id: int
    template_id: int
    template_fingerprint: str
    draft: dict[str, Any] | None = None


class RegionCommitRequest(BaseModel):
    regions: list[dict[str, Any]] = Field(default_factory=list)
    image_sizes: dict[str, tuple[int, int]]
    template_matches: bool = True
    expected_template_fingerprint: str | None = None


class RegionSnapshotRetryRequest(BaseModel):
    expected_template_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class RegionIssueResponse(BaseModel):
    code: str
    message: str
    region_uuid: str | None = None
    question_id: str | None = None


class RegionCommitResponse(BaseModel):
    committed: bool
    snapshot_pending: bool
    error: str | None = None
    issues: list[RegionIssueResponse]
    region_count: int
