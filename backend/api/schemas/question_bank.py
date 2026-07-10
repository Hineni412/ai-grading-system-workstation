from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


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
