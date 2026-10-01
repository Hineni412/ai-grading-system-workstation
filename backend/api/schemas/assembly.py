from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.api.schemas.question_bank import (
    QuestionRichContentMetadata,
    QuestionTagResponse,
)


class _AssemblyModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AssemblySectionResponse(_AssemblyModel):
    id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=80)
    question_ids: list[int] = Field(max_length=500)


class AssemblyDraftPayload(_AssemblyModel):
    practice_rules: bool = False
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
    format: Literal["docx"] = "docx"
    source: Literal["ai"] | None = None


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
    rich_content: QuestionRichContentMetadata
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
    source: Literal["ai"] | None = None
    download_url: str


class AssemblyRecordListResponse(_AssemblyModel):
    items: list[AssemblyRecordResponse]
    total: int


class AssemblyRecordDeleteResponse(_AssemblyModel):
    record_id: str
    deleted: bool


class AssemblyAssistantRequest(_AssemblyModel):
    class_id: str = Field(min_length=1, max_length=100)
    curriculum_volume_id: str = Field(min_length=1, max_length=100)
    chapter_id: str = Field(default="", max_length=100)
    teaching_progress_chapter_id: str = Field(default="", max_length=100)
    target_keys: list[str] | None = Field(default=None, max_length=100)
    question_type: Literal["", "选择题", "多选题", "填空题", "解答题"] = ""
    difficulty_min: float = Field(default=1, ge=1, le=8)
    difficulty_max: float = Field(default=8, ge=1, le=8)
    exclude_exam_originals: bool = True
    exclude_recent: bool = True


class AssemblyWeakness(_AssemblyModel):
    knowledge_key: str
    knowledge_point: str
    mastery: float | None = Field(ge=0, le=1)
    weak_student_count: int = Field(ge=0)
    weak_tier_student_count: int = Field(default=0, ge=0)
    tier: Literal["stable", "unsteady", "weak", "insufficient"] = "insufficient"
    evidence_student_count: int = Field(ge=0)
    exam_score_rate: float | None = Field(ge=0, le=1)
    evidence_count: int = Field(ge=0)
    candidate_count: int | None = Field(ge=0)
    target_difficulty: float | None = Field(default=None, ge=1, le=10)


class AssemblyAssistantCandidate(_AssemblyModel):
    question_id: int = Field(gt=0)
    target_keys: list[str]
    practice_kind: Literal["focus", "foundation"] = "focus"
    match_level: int | None = Field(default=None, ge=1, le=4)
    match_label: str = "按已选目标关联"
    selection_kind: Literal["direct", "task_matched", "supplement"] | None = None
    difficulty: float | None = Field(default=None, ge=1, le=10)
    difficulty_band: Literal["suitable", "lower", "higher", "unknown"] = "unknown"
    suitable_student_count: int = 0
    remediation_student_count: int = 0
    consolidation_student_count: int = 0
    new_practice_student_count: int = 0
    uncertain_student_count: int = 0
    difficulty_basis: str = ""
    similar_question_ids: list[int] = Field(default_factory=list)
    direct_target_keys: list[str] = Field(default_factory=list)


class AssemblyAssistantResponse(_AssemblyModel):
    student_count: int
    evidence_student_count: int
    exam_student_count: int
    exam_score_rate: float | None
    exam_count: int
    weaknesses: list[AssemblyWeakness]
    selected_target_keys: list[str]
    candidate_total: int
    candidates: list[AssemblyAssistantCandidate]
