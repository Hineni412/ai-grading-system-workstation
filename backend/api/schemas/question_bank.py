from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _QuestionBankModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class QuestionPaperListItem(_QuestionBankModel):
    id: int
    title: str | None = None
    year: str | None = None
    province: str | None = None
    city: str | None = None
    district: str | None = None
    exam_type: str | None = None
    grade: str | None = None
    semester: str | None = None
    textbook_version: str | None = None
    import_status: str | None = None
    created_at: str
    updated_at: str
    question_count: int
    tagged_question_count: int


class QuestionPaperListResponse(_QuestionBankModel):
    items: list[QuestionPaperListItem]
    total: int


class QuestionTagResponse(_QuestionBankModel):
    tag_type: Literal[
        "ability",
        "canonical_knowledge_id",
        "error_type",
        "exam_scope",
        "knowledge_point",
        "measured_skill_name",
        "method",
        "model",
        "prerequisite",
        "student_level",
        "sub_skill",
        "supporting_skill_name",
        "teaching_stage",
    ]
    tag_value: str
    confidence: float | None = None


class QuestionListItem(_QuestionBankModel):
    id: int
    revision: str
    paper_id: int | None = None
    question_number: str
    question_type: str | None = None
    question_text: str
    answer_text: str | None = None
    difficulty: str | None = None
    typicality: str | None = None
    reason: str | None = None
    needs_review: bool
    has_images: bool
    needs_image_review: bool
    created_at: str
    updated_at: str
    paper_title: str | None = None
    year: str | None = None
    province: str | None = None
    city: str | None = None
    district: str | None = None
    exam_type: str | None = None
    grade: str | None = None
    semester: str | None = None
    textbook_version: str | None = None
    tags: list[QuestionTagResponse]
    asset_urls: list[str]


class QuestionListResponse(_QuestionBankModel):
    items: list[QuestionListItem]
    total: int
    page: int
    page_size: int
    total_pages: int


class QuestionAssetLink(_QuestionBankModel):
    index: int
    url: str


class QuestionPreviewBox(_QuestionBankModel):
    x0: float | None = None
    y0: float | None = None
    x1: float | None = None
    y1: float | None = None


class QuestionPreviewMetadata(_QuestionBankModel):
    preview_type: Literal["question", "answer"]
    status: str
    page_number: int | None = None
    bbox: QuestionPreviewBox | None = None
    updated_at: str
    url: str | None = None


class QuestionRichTextBlock(_QuestionBankModel):
    text: str
    asset_indexes: list[int]
    asset_urls: list[str]


class QuestionRichContentMetadata(_QuestionBankModel):
    available: bool
    question_block_count: int
    answer_block_count: int
    question_blocks: list[QuestionRichTextBlock]
    answer_blocks: list[QuestionRichTextBlock]


class QuestionDetailResponse(QuestionListItem):
    page_range: str | None = None
    assets: list[QuestionAssetLink]
    rich_content: QuestionRichContentMetadata
    previews: list[QuestionPreviewMetadata]


class QuestionTagWriteRequest(_QuestionBankModel):
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    tags: list["QuestionTagWriteItem"] = Field(max_length=100)


class QuestionTagWriteItem(QuestionTagResponse):
    tag_value: str = Field(min_length=1, max_length=36)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class QuestionStateChangeRequest(_QuestionBankModel):
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")


class QuestionWriteResponse(_QuestionBankModel):
    question_id: int
    revision: str
    deleted: bool
    tags: list[QuestionTagResponse]


class QuestionImportUploadResponse(_QuestionBankModel):
    upload_id: str
    filename: str
    suffix: Literal[".docx", ".pdf"]
    size: int
    sha256: str


class QuestionImportRequestCreate(_QuestionBankModel):
    upload_id: str = Field(pattern=r"^[0-9a-fA-F]{32}$")


class QuestionImportRequestResponse(_QuestionBankModel):
    request_id: str
    upload_id: str
    filename: str
    size: int
    sha256: str
    status: Literal["pending"]


class QuestionTaggingJobRequest(_QuestionBankModel):
    question_ids: list[int] = Field(min_length=1, max_length=500)
    source_job_id: int | None = Field(default=None, gt=0)


class QuestionJobRetryRequest(_QuestionBankModel):
    question_ids: list[int] | None = Field(default=None, min_length=1, max_length=500)
