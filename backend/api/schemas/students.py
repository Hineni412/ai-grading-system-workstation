from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class WrongQuestionBookPreviewRequest(BaseModel):
    curriculum_volume_id: str = Field(min_length=1, max_length=100)
    student_ids: list[int] = Field(min_length=1, max_length=500)
    session_ids: list[int] | None = None
    scope_keys: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("student_ids", "session_ids")
    @classmethod
    def _positive_ids(cls, values: list[int] | None) -> list[int] | None:
        if values is None:
            return None
        if any(value <= 0 for value in values):
            raise ValueError("IDs must be positive")
        return sorted(set(values))

    @field_validator("scope_keys")
    @classmethod
    def _scope_keys(cls, values: list[str]) -> list[str]:
        result = []
        for raw in values:
            key = str(raw or '').strip().casefold()
            if not key.startswith(('kp_', 'ki_')):
                raise ValueError('scope keys must use chapter or section identities')
            if key not in result:
                result.append(key)
        return result


class WrongQuestionBookSubmitRequest(WrongQuestionBookPreviewRequest):
    session_ids: list[int] = Field(min_length=1)
    include_source_label: bool = True
    include_answer_space: bool = True
    client_request_token: str = Field(pattern=r"^[0-9a-f]{32}$")


class StudentUpsertItem(BaseModel):
    student_code: str
    name: str
    class_name: str | None = None

    @field_validator("student_code", "name")
    @classmethod
    def _required_text(cls, value: str) -> str:
        clean = str(value or "").strip()
        if not clean:
            raise ValueError("must be nonblank")
        return clean

    @field_validator("class_name")
    @classmethod
    def _optional_text(cls, value: str | None) -> str | None:
        clean = str(value or "").strip()
        return clean or None


class StudentUpsertRequest(BaseModel):
    items: list[StudentUpsertItem] = Field(min_length=1)


class StudentUpdateRequest(StudentUpsertItem):
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")


class StudentResponse(BaseModel):
    id: int
    student_code: str
    name: str
    class_name: str | None = None
    created_at: str | None = None


class StudentListResponse(BaseModel):
    items: list[StudentResponse]
    total: int


class StudentUpsertResponse(BaseModel):
    inserted: int
    updated: int
    total: int
    items: list[StudentResponse]


class StudentDeleteResponse(BaseModel):
    deleted_students: int
    deleted_results: int
    deleted_details: int
    deleted_annotations: int
    deleted_attendance: int
    unlinked_papers: int
    backup_created: bool
    roster_revision: str = Field(pattern=r"^[0-9a-f]{64}$")


class StudentMutationResponse(BaseModel):
    student: StudentResponse
    roster_revision: str = Field(pattern=r"^[0-9a-f]{64}$")


class StudentDeletionCounts(BaseModel):
    deleted_students: int
    deleted_results: int
    deleted_details: int
    deleted_annotations: int
    deleted_attendance: int
    unlinked_papers: int


class StudentDeletionImpactResponse(BaseModel):
    student: StudentResponse
    counts: StudentDeletionCounts
    roster_revision: str = Field(pattern=r"^[0-9a-f]{64}$")


class StudentImportMapping(BaseModel):
    student_code: str | None = None
    name: str | None = None
    class_name: str | None = None


class StudentImportCounts(BaseModel):
    insert: int
    update: int
    unchanged: int
    invalid: int
    duplicate: int


class StudentImportRowResponse(BaseModel):
    source_row: int
    student_code: str
    name: str
    class_name: str | None = None
    operation: Literal["insert", "update", "unchanged", "invalid", "duplicate"]
    selectable: bool
    issues: list[str]
    existing: StudentResponse | None = None


class StudentImportPreviewResponse(BaseModel):
    filename: str
    columns: list[str]
    mapping: StudentImportMapping
    counts: StudentImportCounts
    rows: list[StudentImportRowResponse]
    roster_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    issues: list[str]


class StudentWorkspaceResponse(BaseModel):
    items: list[StudentResponse]
    total: int
    page: int
    page_size: int
    total_pages: int
    class_names: list[str]
    roster_revision: str = Field(pattern=r"^[0-9a-f]{64}$")


class StudentImportCommitRequest(BaseModel):
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    items: list[StudentUpsertItem] = Field(min_length=1, max_length=10000)


class StudentImportCommitResponse(BaseModel):
    inserted: int
    updated: int
    unchanged: int
    total: int
    roster_revision: str = Field(pattern=r"^[0-9a-f]{64}$")


class StudentExamResultItem(BaseModel):
    detail_id: int
    question_id: str
    bank_question_id: int | None = None
    score_awarded: float
    max_score: float | None = None
    deduction_amount: float | None = None
    deduction_reason: str | None = None
    error_category: str | None = None
    error_summary: str | None = None
    evidence_url: str | None


class StudentExamResultSession(BaseModel):
    session_id: int
    session_name: str
    graded_at: str | None = None
    exam_created_at: str | None = None
    result_id: int
    student_score: float
    total_score: float
    items: list[StudentExamResultItem]


class StudentExamResultsResponse(BaseModel):
    student: StudentResponse
    sessions: list[StudentExamResultSession]
    total_sessions: int
    page: int
    page_size: int
    total_pages: int
