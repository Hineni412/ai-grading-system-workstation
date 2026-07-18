from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.api.schemas.question_bank import QuestionTagResponse


class _AssemblyModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AssemblySectionResponse(_AssemblyModel):
    id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=80)
    question_ids: list[int] = Field(max_length=500)


class AssemblyDraftPayload(_AssemblyModel):
    basket_ids: list[int] = Field(default_factory=list, max_length=500)
    order_ids: list[int] = Field(default_factory=list, max_length=500)
    sections: list[AssemblySectionResponse] = Field(default_factory=list, max_length=100)
    title: str = Field(default="", max_length=120)
    header_text: str = Field(default="", max_length=200)
    include_answer: bool = True
    layout_mode: Literal["sequential", "grouped_by_type", "sections"] = "sequential"
    preview_mode: Literal["student", "teacher"] = "teacher"


class AssemblyDraftResponse(AssemblyDraftPayload):
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")


class AssemblyDraftWriteRequest(_AssemblyModel):
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    draft: AssemblyDraftPayload


class AssemblyExportSubmitRequest(_AssemblyModel):
    draft_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    format: Literal["docx", "markdown"] = "docx"


class AssemblyRecordRestoreRequest(_AssemblyModel):
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")


class AssemblyQuestionItem(_AssemblyModel):
    id: int
    revision: str
    question_number: str
    question_type: str | None = None
    question_text: str
    answer_text: str | None = None
    difficulty: str | None = None
    paper_title: str | None = None
    tags: list[QuestionTagResponse]
    asset_urls: list[str]
    score_value: int | None = Field(default=None, ge=0)


class AssemblyQuestionListResponse(_AssemblyModel):
    items: list[AssemblyQuestionItem]
    missing_question_ids: list[int]


class AssemblyRecordResponse(_AssemblyModel):
    id: str
    title: str
    question_ids: list[int]
    order_ids: list[int]
    sections: list[AssemblySectionResponse]
    export_format: Literal["docx", "markdown"]
    filename: str
    include_answer: bool
    created_at: str
    question_count: int
    question_type_summary: dict[str, int]
    download_url: str


class AssemblyRecordListResponse(_AssemblyModel):
    items: list[AssemblyRecordResponse]
    total: int


class AssemblyRecordDeleteResponse(_AssemblyModel):
    record_id: str
    deleted: bool
