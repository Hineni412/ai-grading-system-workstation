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
    status: Literal["processing", "succeeded", "failed", "replaced"]
    template: TemplateUploadResponse | None = None


class RegionDraftRequest(BaseModel):
    revision: int = Field(default=0, ge=0)
    regions: list[dict[str, Any]] = Field(default_factory=list)


class RegionDraftResponse(BaseModel):
    status: str
    session_id: int
    template_id: int
    template_fingerprint: str
    draft_path: str | None = None
    draft: dict[str, Any] | None = None
    quarantined_path: str | None = None


class RegionCommitRequest(BaseModel):
    regions: list[dict[str, Any]] = Field(default_factory=list)
    image_sizes: dict[str, tuple[int, int]]
    template_matches: bool = True
    expected_template_fingerprint: str | None = None


class RegionIssueResponse(BaseModel):
    code: str
    message: str
    region_uuid: str | None = None
    question_id: str | None = None


class RegionCommitResponse(BaseModel):
    committed: bool
    snapshot_pending: bool
    snapshot_path: str | None = None
    error: str | None = None
    issues: list[RegionIssueResponse]
    region_count: int
