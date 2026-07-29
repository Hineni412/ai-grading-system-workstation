from __future__ import annotations

import re

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


class DeleteSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: str
    confirmation_name: str

    @field_validator("expected_revision")
    @classmethod
    def _revision(cls, value: str) -> str:
        clean = str(value or "").strip().casefold()
        if re.fullmatch(r"[0-9a-f]{64}", clean) is None:
            raise ValueError("must be a sha256 revision")
        return clean

    @field_validator("confirmation_name")
    @classmethod
    def _confirmation_name(cls, value: str) -> str:
        clean = str(value or "").strip()
        if not clean:
            raise ValueError("must be nonblank")
        return clean


class PermanentDeleteSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: str
    confirmation_phrase: str

    @field_validator("expected_revision")
    @classmethod
    def _revision(cls, value: str) -> str:
        clean = str(value or "").strip().casefold()
        if re.fullmatch(r"[0-9a-f]{64}", clean) is None:
            raise ValueError("must be a sha256 revision")
        return clean

    @field_validator("confirmation_phrase")
    @classmethod
    def _confirmation_phrase(cls, value: str) -> str:
        clean = str(value or "").strip()
        if not clean:
            raise ValueError("must be nonblank")
        return clean


class QuestionBankSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config_revision: str
    client_request_token: str
    curriculum_volume_id: str | None = Field(default=None, max_length=80)

    @field_validator("config_revision")
    @classmethod
    def _revision(cls, value: str) -> str:
        clean = str(value or "").strip().casefold()
        if re.fullmatch(r"[0-9a-f]{64}", clean) is None:
            raise ValueError("must be a sha256 revision")
        return clean

    @field_validator("client_request_token")
    @classmethod
    def _request_token(cls, value: str) -> str:
        clean = str(value or "").strip().casefold()
        if re.fullmatch(r"[0-9a-f]{32}", clean) is None:
            raise ValueError("must be 32 lowercase hexadecimal characters")
        return clean

    @field_validator("curriculum_volume_id")
    @classmethod
    def _curriculum_volume_id(cls, value: str | None) -> str | None:
        clean = str(value or "").strip()
        return clean or None


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


class SessionDeletionImpactResponse(BaseModel):
    session: SessionSummary
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    active_jobs: int = Field(ge=0)
    active_grading_runs: int = Field(ge=0)
    can_archive: bool
    can_permanently_delete: bool
    permanent_delete_phrase: str
    permanent_counts: dict[str, int] = Field(default_factory=dict)
    storage_counts: dict[str, int] = Field(default_factory=dict)
    question_bank_counts: dict[str, int] = Field(default_factory=dict)
    blocking_training_tasks: list[str] = Field(default_factory=list)
    # One-release compatibility field for the former recycle-bin API client.
    can_delete: bool


class SessionPermanentDeletionResponse(BaseModel):
    session_id: int = Field(gt=0)
    db_counts: dict[str, int] = Field(default_factory=dict)
    question_bank_counts: dict[str, int] = Field(default_factory=dict)
    deleted_files: int = Field(ge=0)
    deleted_dirs: int = Field(ge=0)
    skipped_shared: int = Field(ge=0)
    storage_cleanup_pending: bool = False
    recovered_interrupted_delete: bool = False


class SessionPendingCleanup(BaseModel):
    session_id: int = Field(gt=0)
    deleted_files: int = Field(ge=0)
    deleted_dirs: int = Field(ge=0)
    skipped_shared: int = Field(ge=0)


class SessionPendingCleanupListResponse(BaseModel):
    items: list[SessionPendingCleanup]
    total: int = Field(ge=0)


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
