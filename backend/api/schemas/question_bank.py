from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _QuestionBankModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class QuestionPaperListItem(_QuestionBankModel):
    id: int
    title: str | None = None
    source_type: Literal["docx", "pdf", "other"]
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
    tagged_any_question_count: int


class QuestionPaperListResponse(_QuestionBankModel):
    items: list[QuestionPaperListItem]
    total: int


class CurriculumSection(_QuestionBankModel):
    id: str
    order: int = Field(ge=1)
    number: str | None = None
    title: str
    label: str
    kind: Literal[
        "activity",
        "activity_group",
        "exercise",
        "lesson",
        "optional_lesson",
        "reflection",
        "review",
    ]


class CurriculumChapter(_QuestionBankModel):
    id: str
    order: int = Field(ge=1)
    number: str | None = None
    title: str
    label: str
    kind: Literal["chapter", "activity"]
    exam_scope_values: list[str]
    sections: list[CurriculumSection]


class CurriculumVolume(_QuestionBankModel):
    id: str
    label: str
    grade: str
    semester: str
    textbook_version: str
    chapters: list[CurriculumChapter]


class CurriculumCatalog(_QuestionBankModel):
    schema_version: Literal[1]
    catalog_id: str
    publisher: str
    subject: str
    edition: str
    volumes: list[CurriculumVolume]


class QuestionTagResponse(_QuestionBankModel):
    tag_type: Literal[
        "ability",
        "canonical_knowledge_id",
        "curriculum_section",
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


class QuestionRichInlineSegment(_QuestionBankModel):
    text: str
    superscript: bool = False
    subscript: bool = False
    underline: bool = False
    line_break: bool = False


class QuestionRichTableCell(_QuestionBankModel):
    segments: list[QuestionRichInlineSegment]


class QuestionRichTableRow(_QuestionBankModel):
    cells: list[QuestionRichTableCell]


class QuestionRichTextBlock(_QuestionBankModel):
    kind: Literal["paragraph", "table"]
    text: str
    segments: list[QuestionRichInlineSegment]
    rows: list[QuestionRichTableRow]
    asset_indexes: list[int]
    asset_urls: list[str]


class QuestionRichContentMetadata(_QuestionBankModel):
    available: bool
    question_block_count: int
    answer_block_count: int
    question_blocks: list[QuestionRichTextBlock]
    answer_blocks: list[QuestionRichTextBlock]


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
    rich_content: QuestionRichContentMetadata


class QuestionListResponse(_QuestionBankModel):
    items: list[QuestionListItem]
    total: int
    page: int
    page_size: int
    total_pages: int


class QuestionDetailResponse(QuestionListItem):
    page_range: str | None = None
    assets: list[QuestionAssetLink]
    previews: list[QuestionPreviewMetadata]


class QuestionFacetItem(_QuestionBankModel):
    value: str
    count: int = Field(ge=1)


class QuestionFacetsResponse(_QuestionBankModel):
    exam_scopes: list[QuestionFacetItem]
    curriculum_sections: list[QuestionFacetItem]
    knowledge_points: list[QuestionFacetItem]
    curriculum_chapters: list[QuestionFacetItem]
    abilities: list[QuestionFacetItem]
    methods: list[QuestionFacetItem]
    models: list[QuestionFacetItem]
    student_levels: list[QuestionFacetItem]
    teaching_stages: list[QuestionFacetItem]
    sub_skills: list[QuestionFacetItem]
    question_types: list[QuestionFacetItem]
    years: list[QuestionFacetItem]
    exam_types: list[QuestionFacetItem]
    grades: list[QuestionFacetItem]


class SimilarQuestionItem(QuestionListItem):
    similarity_score: float = Field(ge=0.0, le=1.0)
    similarity_reasons: list[str]


class SimilarQuestionListResponse(_QuestionBankModel):
    question_id: int
    items: list[SimilarQuestionItem]


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


TaxonomyDimension = Literal[
    "curriculum",
    "knowledge",
    "ability",
    "method",
    "model",
]


class TaxonomyTermResponse(_QuestionBankModel):
    id: str
    dimension: TaxonomyDimension
    name: str
    aliases: list[str] = Field(default_factory=list)
    status: Literal["active", "retired"] = "active"


class TaxonomyDimensionsResponse(_QuestionBankModel):
    curriculum: list[TaxonomyTermResponse]
    knowledge: list[TaxonomyTermResponse]
    ability: list[TaxonomyTermResponse]
    method: list[TaxonomyTermResponse]
    model: list[TaxonomyTermResponse]


class TaxonomyCatalogResponse(_QuestionBankModel):
    schema_version: Literal[1]
    catalog_id: str
    revision: int = Field(ge=0)
    dimensions: TaxonomyDimensionsResponse


class TaxonomyProposalResponse(_QuestionBankModel):
    id: str
    dimension: TaxonomyDimension
    proposed_name: str
    edited_name: str | None = None
    definition: str = ""
    reason: str = ""
    nearest_id: str = ""
    why_not_reuse: str = ""
    question_refs: list[int] = Field(default_factory=list)
    status: Literal["pending", "approved", "merged", "rejected", "retired"]
    created_at: str | None = None
    updated_at: str | None = None


class TaxonomyProposalCounts(_QuestionBankModel):
    pending: int = Field(ge=0)


class TaxonomyProposalListResponse(_QuestionBankModel):
    revision: int = Field(ge=0)
    items: list[TaxonomyProposalResponse]
    counts: TaxonomyProposalCounts


class TaxonomyProposalReviewRequest(_QuestionBankModel):
    decision: Literal["approve", "edit", "merge", "reject"]
    edited_name: str | None = Field(default=None, min_length=1, max_length=36)
    target_term_id: str | None = Field(default=None, min_length=1, max_length=100)
    expected_revision: int = Field(ge=0)
    request_token: str = Field(pattern=r"^[0-9a-fA-F]{32}$")


class TaxonomyProposalReviewResponse(_QuestionBankModel):
    revision: int = Field(ge=0)
    proposal: TaxonomyProposalResponse
    approved_term: TaxonomyTermResponse | None = None
    application_status: Literal[
        "not_requested",
        "pending",
        "applied",
        "failed",
    ] = "not_requested"
