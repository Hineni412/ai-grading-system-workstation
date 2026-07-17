from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CreateSessionRequest(BaseModel):
    name: str
    rubric_path: str
    answer_key_path: str
    source_paper_path: str = ""
    source_paper_sha256: str = ""

    @field_validator("name", "rubric_path", "answer_key_path")
    @classmethod
    def _required_text(cls, value: str) -> str:
        clean = str(value or "").strip()
        if not clean:
            raise ValueError("must be nonblank")
        return clean

    @field_validator("source_paper_path", "source_paper_sha256")
    @classmethod
    def _optional_text(cls, value: str) -> str:
        return str(value or "").strip()


class CreateSessionDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str


class RenameSessionRequest(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def _required_text(cls, value: str) -> str:
        clean = str(value or "").strip()
        if not clean:
            raise ValueError("must be nonblank")
        return clean


class SessionSummary(BaseModel):
    id: int
    name: str
    status: str
    is_deleted: bool = False
    deleted_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class SessionDetail(SessionSummary):
    rubric_path: str
    answer_key_path: str
    template_config_path: str | None = None
    source_paper_path: str | None = None
    source_paper_sha256: str | None = None
    question_bank_sync_state: str
    question_bank_sync_details: dict = Field(default_factory=dict)
    question_bank_sync_error: str | None = None
    question_bank_sync_updated_at: str | None = None


class SessionListResponse(BaseModel):
    items: list[SessionSummary]
    total: int


class SessionProgress(BaseModel):
    total_papers: int
    matched_papers: int
    unmatched_papers: int
    graded_papers: int
    failed_papers: int
    grading_papers: int
    needs_human_review: int
    absent_students: int
    scan_issue_students: int
    progress_percent: float


class SessionTemplateResponse(BaseModel):
    id: int
    session_id: int
    pages: dict[str, dict[str, str]]
    is_confirmed: bool
    regions_snapshot_pending: bool
    created_at: str | None = None
    updated_at: str | None = None


class AnswerRegionResponse(BaseModel):
    id: int
    region_uuid: str
    session_id: int
    template_id: int
    page: str
    region_order: int
    x: int
    y: int
    w: int
    h: int
    detected_question_id: str | None = None
    mapped_question_id: str | None = None
    confidence: float
    is_confirmed: bool
    mapping_status: str
    multi_region_confirmed: bool
    created_at: str | None = None
    updated_at: str | None = None


class AnswerRegionListResponse(BaseModel):
    items: list[AnswerRegionResponse]
    total: int
