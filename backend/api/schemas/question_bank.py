from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer

from backend.api.schemas.jobs import JobResponse


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
    folder_name: str | None = None
    textbook_version: str | None = None
    curriculum_volume_id: str | None = None
    import_status: str | None = None
    created_at: str
    updated_at: str
    question_count: int
    tagged_question_count: int
    tagged_any_question_count: int
    evidence_question_count: int
    criteria_question_count: int
    criteria_needs_review_count: int
    complete_analysis_count: int
    skill_unlinked_question_count: int = 0


class QuestionPaperListResponse(_QuestionBankModel):
    items: list[QuestionPaperListItem]
    total: int


class QuestionTaskPaper(_QuestionBankModel):
    id: int
    title: str | None = None


class QuestionTaskContext(_QuestionBankModel):
    papers: list[QuestionTaskPaper]
    source_filename: str | None = None


class QuestionPaperMetadataInput(_QuestionBankModel):
    title: str = Field(min_length=1, max_length=255)
    year: str | None = Field(default=None, max_length=24)
    province: str | None = Field(default=None, max_length=48)
    city: str | None = Field(default=None, max_length=48)
    district: str | None = Field(default=None, max_length=48)
    exam_type: str | None = Field(default=None, max_length=48)
    grade: str | None = Field(default=None, max_length=48)
    semester: str | None = Field(default=None, max_length=48)
    folder_name: str | None = Field(default=None, max_length=80)
    textbook_version: str | None = Field(default=None, max_length=100)


class QuestionPaperMetadataUpdateRequest(_QuestionBankModel):
    expected_updated_at: str = Field(min_length=1, max_length=64)
    metadata: QuestionPaperMetadataInput


class QuestionPaperMetadataWriteResponse(QuestionPaperMetadataInput):
    id: int
    updated_at: str


class QuestionPaperStateChangeRequest(_QuestionBankModel):
    expected_updated_at: str = Field(min_length=1, max_length=64)


class QuestionPaperStateWriteResponse(_QuestionBankModel):
    id: int
    deleted: bool
    import_status: str = Field(min_length=1, max_length=64)
    updated_at: str
    affected_question_count: int = Field(ge=0)


class QuestionPaperPermanentDeleteSelection(_QuestionBankModel):
    id: int = Field(gt=0)
    expected_updated_at: str = Field(min_length=1, max_length=64)


class QuestionPaperPermanentDeleteImpactRequest(_QuestionBankModel):
    selections: list[QuestionPaperPermanentDeleteSelection] = Field(
        min_length=1,
        max_length=200,
    )


class QuestionPaperPermanentDeleteImpactResponse(_QuestionBankModel):
    paper_count: int = Field(ge=1)
    question_count: int = Field(ge=0)
    tag_count: int = Field(ge=0)
    analysis_record_count: int = Field(ge=0)
    training_link_count: int = Field(ge=0)
    knowledge_graph_link_count: int = Field(ge=0)
    owned_file_count: int = Field(ge=0)
    shared_file_count: int = Field(ge=0)
    taxonomy_proposal_count: int = Field(ge=0)
    permanent_delete_phrase: str


class QuestionPaperPermanentDeleteRequest(QuestionPaperPermanentDeleteImpactRequest):
    confirmation_phrase: str = Field(min_length=1, max_length=80)
    request_token: str = Field(pattern=r"^[0-9a-f]{32}$")


class QuestionPaperPermanentDeleteResponse(_QuestionBankModel):
    deleted_paper_ids: list[int]
    deleted_question_count: int = Field(ge=0)
    deleted_tag_count: int = Field(ge=0)
    deleted_analysis_record_count: int = Field(ge=0)
    removed_training_link_count: int = Field(ge=0)
    removed_knowledge_graph_link_count: int = Field(ge=0)
    deleted_file_count: int = Field(ge=0)
    skipped_shared_file_count: int = Field(ge=0)
    storage_cleanup_pending: bool


class CurriculumSourceRef(_QuestionBankModel):
    node_id: str
    relative_url: str


class CurriculumKnowledgePoint(_QuestionBankModel):
    id: str
    order: int = Field(ge=1)
    label: str
    display_name: str
    parent_knowledge_id: str
    source_ref: CurriculumSourceRef


class CurriculumSection(_QuestionBankModel):
    id: str
    knowledge_id: str
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
    display_name: str
    source_ref: CurriculumSourceRef
    knowledge_points: list[CurriculumKnowledgePoint]


class CurriculumChapter(_QuestionBankModel):
    id: str
    knowledge_id: str
    order: int = Field(ge=1)
    number: str | None = None
    title: str
    label: str
    kind: Literal["chapter", "activity"]
    display_name: str
    source_ref: CurriculumSourceRef
    exam_scope_values: list[str]
    sections: list[CurriculumSection]


class CurriculumVolumeStatistics(_QuestionBankModel):
    raw_nodes: int = Field(ge=0)
    excluded_nodes: int = Field(ge=0)
    retained_nodes: int = Field(ge=0)


class CurriculumCatalogStatistics(CurriculumVolumeStatistics):
    chapters: int = Field(ge=0)
    sections: int = Field(ge=0)
    knowledge_points: int = Field(ge=0)


class CurriculumVolume(_QuestionBankModel):
    id: str
    order: int = Field(ge=1)
    label: str
    grade: str
    semester: str
    textbook_version: str
    source: dict[str, Any]
    statistics: CurriculumVolumeStatistics
    chapters: list[CurriculumChapter]


class CurriculumCatalog(_QuestionBankModel):
    schema_version: Literal[2]
    catalog_id: str
    knowledge_standard_id: str
    publisher: str
    subject: str
    edition: str
    statistics: CurriculumCatalogStatistics
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
        "thought",
        "model",
        "prerequisite",
        "skill",
        "special_type",
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
    html: str = ""
    asset_indexes: list[int]
    asset_urls: list[str]


class QuestionRichContentMetadata(_QuestionBankModel):
    available: bool
    question_block_count: int
    answer_block_count: int
    question_blocks: list[QuestionRichTextBlock]
    answer_blocks: list[QuestionRichTextBlock]


class QuestionDirectSkill(_QuestionBankModel):
    stable_key: str
    display_name: str


class QuestionSkillHit(_QuestionBankModel):
    point_id: str
    point_label: str


class QuestionDuplicateMember(_QuestionBankModel):
    id: int
    paper_id: int | None
    question_number: str
    paper_title: str | None


class QuestionListItem(_QuestionBankModel):
    id: int
    duplicate_of_question_id: int | None = None
    duplicate_labels_reused: bool = False
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
    criteria_needs_review: bool
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
    evidence_point_count: int | None = None
    duplicate_members: list[QuestionDuplicateMember] | None = None
    skills: list[QuestionDirectSkill] | None = None
    skill_hits: list[QuestionSkillHit] | None = None

    @model_serializer(mode="wrap")
    def serialize_optional_skills(self, handler):
        result = handler(self)
        for key in ("skills", "skill_hits", "evidence_point_count", "duplicate_members"):
            if result.get(key) is None:
                result.pop(key, None)
        return result


class QuestionListResponse(_QuestionBankModel):
    items: list[QuestionListItem]
    total: int
    page: int
    page_size: int
    total_pages: int


class QuestionRefListItem(_QuestionBankModel):
    id: int
    paper_id: int
    question_number: str


class QuestionRefListResponse(_QuestionBankModel):
    items: list[QuestionRefListItem]
    total: int
    page: int
    page_size: int
    total_pages: int


class QuestionErrorPattern(_QuestionBankModel):
    id: int
    category: str | None = None
    pattern: str
    explanation: str
    trigger_kind: Literal["option", "wrong_answer", "step", "observation"]
    trigger_value: str
    status: Literal["candidate", "confirmed"]
    source: str
    has_evidence: bool
    skill_key: str | None = None
    skill_label: str | None = None
    skill_keys: list[str] = Field(default_factory=list)
    skill_labels: list[str] = Field(default_factory=list)
    skill_source: Literal["teacher", "criterion", "question"] | None = None


class QuestionSkillOption(_QuestionBankModel):
    key: str
    label: str


class QuestionDetailResponse(QuestionListItem):
    page_range: str | None = None
    assets: list[QuestionAssetLink]
    previews: list[QuestionPreviewMetadata]
    error_patterns: list[QuestionErrorPattern] = Field(default_factory=list)
    wrong_option_letters: list[str] = Field(default_factory=list)
    selectable_skills: list[QuestionSkillOption] = Field(default_factory=list)


class QuestionErrorPatternEditRequest(_QuestionBankModel):
    action: Literal["edit", "reject"]
    expected_pattern: str
    pattern: str | None = None
    category: str | None = None
    skill_key: str | None = None


class QuestionSolutionEvidenceResponse(_QuestionBankModel):
    question_id: int = Field(gt=0)
    available: bool
    evidence_version_id: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    status: Literal["proposed", "approved", "rejected", "superseded", "stale"] | None = None
    evidence: dict[str, Any] | None = None
    part_assessments: list[dict[str, Any]] = Field(default_factory=list)
    assessment_revision: str | None = None


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
    thoughts: list[QuestionFacetItem]
    models: list[QuestionFacetItem]
    special_types: list[QuestionFacetItem]
    error_types: list[QuestionFacetItem]
    error_pattern_categories: list[QuestionFacetItem]
    student_levels: list[QuestionFacetItem]
    teaching_stages: list[QuestionFacetItem]
    sub_skills: list[QuestionFacetItem]
    question_types: list[QuestionFacetItem]
    years: list[QuestionFacetItem]
    exam_types: list[QuestionFacetItem]
    grades: list[QuestionFacetItem]


class SimilarityReason(_QuestionBankModel):
    kind: Literal[
        "knowledge_point",
        "skill",
        "method",
        "model",
        "difficulty",
        "wording",
        "question_type",
        "text_fragment",
    ]
    values: list[str]


class SimilarQuestionItem(QuestionListItem):
    similarity_score: float = Field(ge=0.0, le=1.0)
    similarity_reasons: list[SimilarityReason]


class SimilarQuestionListResponse(_QuestionBankModel):
    question_id: int
    items: list[SimilarQuestionItem]


class QuestionTagWriteRequest(_QuestionBankModel):
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    tags: list[QuestionTagWriteItem] = Field(max_length=100)


class QuestionTagWriteItem(QuestionTagResponse):
    tag_value: str = Field(min_length=1, max_length=160)
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


class QuestionImportJobSubmitRequest(_QuestionBankModel):
    curriculum_volume_id: str | None = Field(default=None, max_length=80)


class QuestionImportRequestResponse(_QuestionBankModel):
    request_id: str
    upload_id: str
    filename: str
    size: int
    sha256: str
    status: Literal["pending"]


class QuestionTaggingJobRequest(_QuestionBankModel):
    question_ids: list[int] = Field(min_length=1, max_length=500)
    curriculum_volume_id: str = Field(min_length=1, max_length=80)
    source_job_id: int | None = Field(default=None, gt=0)
    force_retag: bool = False
    client_request_token: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{32}$",
    )


class QuestionRepairJobRequest(_QuestionBankModel):
    curriculum_volume_id: str = Field(min_length=1, max_length=80)
    kind: Literal['skills', 'analysis', 'all'] = 'all'
    question_ids: list[int] = Field(min_length=1, max_length=500)
    fingerprint: str = Field(pattern=r'^[0-9a-f]{64}$')
    client_request_token: str = Field(pattern=r'^[0-9a-f]{32}$')


class QuestionJobRetryRequest(_QuestionBankModel):
    question_ids: list[int] | None = Field(default=None, min_length=1, max_length=500)


class AnswerDraftJobRequest(_QuestionBankModel):
    question_ids: list[int] = Field(min_length=1, max_length=500)


class TrainingCriterionPointSchema(_QuestionBankModel):
    point_id: str = Field(min_length=2, max_length=64)
    target: str = Field(min_length=1, max_length=2000)
    observable_evidence: str = Field(min_length=1, max_length=8000)
    equivalent_rules: list[str] = Field(default_factory=list, max_length=30)
    counterexamples: list[str] = Field(default_factory=list, max_length=30)
    depends_on: list[str] = Field(default_factory=list, max_length=50)


class TrainingCriterionDraftSchema(_QuestionBankModel):
    schema_version: Literal[
        "training-criteria-draft-v1",
        "judgment-points-v1",
    ] = "training-criteria-draft-v1"
    question_id: int = Field(gt=0)
    source_content_hash: str = Field(default="", max_length=64)
    question_type: str = Field(default="", max_length=64)
    points: list[TrainingCriterionPointSchema] = Field(
        min_length=1,
        max_length=50,
    )
    auxiliary_rules: list[str] = Field(default_factory=list, max_length=50)
    rationale: str = Field(default="", max_length=2000)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source_kind: Literal[
        "combined_model",
        "confirmed_rubric_adapter",
    ] = "combined_model"
    solution_evidence: dict[str, Any] | None = None


class TrainingCriterionVersionResponse(_QuestionBankModel):
    version_id: str
    question_id: int
    version_number: int
    parent_version_id: str | None = None
    source_content_hash: str
    schema_version: str
    status: Literal[
        "proposed",
        "approved",
        "rejected",
        "superseded",
        "stale",
    ]
    source_kind: Literal[
        "combined_model",
        "confirmed_rubric_adapter",
        "teacher_manual",
        "backfill",
    ]
    source_reference: str
    criteria: TrainingCriterionDraftSchema
    criteria_hash: str
    quality_status: Literal["passed", "failed"]
    quality_codes: list[str]
    created_by: str
    decision_by: str | None = None
    decision_note: str | None = None
    decided_at: str | None = None
    created_at: str
    updated_at: str


class TrainingCriterionWorkspaceResponse(_QuestionBankModel):
    question_id: int
    state: Literal[
        "missing",
        "proposed",
        "approved",
        "rejected",
        "superseded",
        "stale",
        "available",
    ]
    available: bool
    revision: int = Field(ge=0)
    current_source_hash: str
    compatible_source_hashes: list[str] = Field(default_factory=list, exclude=True)
    read_failure: str | None = Field(default=None, exclude=True)
    current_version: TrainingCriterionVersionResponse | None = None
    approved_version: TrainingCriterionVersionResponse | None = None
    versions: list[TrainingCriterionVersionResponse]


class TrainingCriterionDraftWriteRequest(_QuestionBankModel):
    expected_revision: int = Field(ge=0)
    parent_version_id: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    request_token: str = Field(pattern=r"^[0-9a-fA-F]{32}$")
    reason: str = Field(min_length=1, max_length=500)
    points: list[TrainingCriterionPointSchema] = Field(
        min_length=1,
        max_length=50,
    )
    auxiliary_rules: list[str] = Field(default_factory=list, max_length=50)
    rationale: str = Field(default="", max_length=2000)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class TrainingCriterionReviewRequest(_QuestionBankModel):
    version_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_revision: int = Field(ge=0)
    action: Literal["approve", "reject"]
    reason: str = Field(min_length=1, max_length=500)


class TrainingCriterionBackfillCreateRequest(_QuestionBankModel):
    question_ids: list[int] = Field(min_length=1, max_length=500)
    request_token: str = Field(pattern=r"^[0-9a-fA-F]{32}$")
    mode: Literal["missing_only", "regenerate"] = "missing_only"


class TrainingCriterionBackfillRetryRequest(_QuestionBankModel):
    request_token: str = Field(pattern=r"^[0-9a-fA-F]{32}$")
    question_ids: list[int] | None = Field(
        default=None,
        min_length=1,
        max_length=500,
    )


class TrainingCriterionBackfillItemResponse(_QuestionBankModel):
    question_id: int
    status: Literal[
        "pending",
        "running",
        "succeeded",
        "failed",
        "cancelled",
        "skipped",
    ]
    version_id: str | None = None
    error_category: str
    attempt_count: int = Field(ge=0)


class TrainingCriterionBackfillRunResponse(_QuestionBankModel):
    run_id: str
    mode: Literal["missing_only", "regenerate"]
    status: Literal[
        "pending",
        "running",
        "partial",
        "succeeded",
        "failed",
        "cancelled",
    ]
    question_ids: list[int]
    created_at: str
    updated_at: str
    finished_at: str | None = None
    items: list[TrainingCriterionBackfillItemResponse]


class TrainingCriterionBackfillStartResponse(_QuestionBankModel):
    run: TrainingCriterionBackfillRunResponse
    job: JobResponse | None = None


TaxonomyDimension = Literal[
    "curriculum",
    "knowledge",
    "ability",
    "method",
    "thought",
    "model",
    "special_type",
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
    thought: list[TaxonomyTermResponse]
    model: list[TaxonomyTermResponse]
    special_type: list[TaxonomyTermResponse]


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
    active_question_refs: list[int] = Field(default_factory=list)
    unavailable_question_ref_count: int = Field(default=0, ge=0)
    actionable: bool = True
    resolved_term_ids: list[str] = Field(default_factory=list)
    status: Literal["pending", "approved", "merged", "rejected", "retired"]
    created_at: str | None = None
    updated_at: str | None = None


class TaxonomyProposalCounts(_QuestionBankModel):
    pending: int = Field(ge=0)
    actionable: int = Field(default=0, ge=0)
    historical_unavailable: int = Field(default=0, ge=0)


class TaxonomyProposalListResponse(_QuestionBankModel):
    revision: int = Field(ge=0)
    evidence_revision: int = Field(default=0, ge=0)
    items: list[TaxonomyProposalResponse]
    counts: TaxonomyProposalCounts


class TaxonomyProposalReviewRequest(_QuestionBankModel):
    decision: Literal["approve", "edit", "merge", "reject"]
    edited_name: str | None = Field(default=None, min_length=1, max_length=160)
    target_term_id: str | None = Field(default=None, min_length=1, max_length=100)
    target_term_ids: list[str] = Field(default_factory=list, max_length=12)
    question_ids: list[int] | None = Field(
        default=None,
        max_length=500,
    )
    expected_revision: int = Field(ge=0)
    request_token: str = Field(pattern=r"^[0-9a-fA-F]{32}$")


class TaxonomyProposalApplicationFailure(_QuestionBankModel):
    question_id: int = Field(gt=0)
    category: Literal["question_not_found", "write_failed"]
    message: str


class TaxonomyProposalApplication(_QuestionBankModel):
    status: Literal["not_requested", "applied", "partial", "failed"]
    selected_question_ids: list[int]
    applied_question_ids: list[int]
    failures: list[TaxonomyProposalApplicationFailure]


class TaxonomyProposalReviewResponse(_QuestionBankModel):
    revision: int = Field(ge=0)
    proposal: TaxonomyProposalResponse
    approved_term: TaxonomyTermResponse | None = None
    approved_terms: list[TaxonomyTermResponse] = Field(default_factory=list)
    application: TaxonomyProposalApplication = Field(
        default_factory=lambda: TaxonomyProposalApplication(
            status="not_requested",
            selected_question_ids=[],
            applied_question_ids=[],
            failures=[],
        )
    )
    application_status: Literal[
        "not_requested",
        "pending",
        "applied",
        "partial",
        "failed",
    ] = "not_requested"
    application_token: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{32}$",
    )


class TaxonomyProposalApplicationRetryRequest(_QuestionBankModel):
    application_token: str = Field(pattern=r"^[0-9a-fA-F]{32}$")
    request_token: str = Field(pattern=r"^[0-9a-fA-F]{32}$")
    question_ids: list[int] | None = Field(
        default=None,
        min_length=1,
        max_length=500,
    )


class TaxonomySuggestionCreateRequest(_QuestionBankModel):
    proposal_ids: list[str] = Field(min_length=1, max_length=200)
    expected_revision: int = Field(ge=0)
    request_token: str = Field(pattern=r"^[0-9a-fA-F]{32}$")


class TaxonomySuggestionRetryRequest(_QuestionBankModel):
    request_token: str = Field(pattern=r"^[0-9a-fA-F]{32}$")


class TaxonomySuggestionDecision(_QuestionBankModel):
    relation_kind: Literal[
        "exact",
        "broader",
        "narrower",
        "related",
        "new_core_candidate",
        "wrong_dimension",
        "reject",
        "uncertain",
    ]
    target_term_ids: list[str] = Field(default_factory=list, max_length=12)
    reason: str = ""
    confidence: float = Field(ge=0.0, le=1.0)
    source: Literal["local_exact", "ai"]
    legacy_format: bool = False
    evidence_question_ids: list[int] = Field(default_factory=list)
    taxonomy_revision: int = Field(default=0, ge=0)
    graph_release_id: str = ""


class TaxonomySuggestionError(_QuestionBankModel):
    category: str
    message: str


class TaxonomySuggestionItem(_QuestionBankModel):
    proposal_id: str
    dimension: TaxonomyDimension
    proposed_name: str
    question_refs: list[int] = Field(default_factory=list)
    status: Literal[
        "pending",
        "running",
        "suggested",
        "failed",
        "cancelled",
        "stale",
    ]
    attempts: int = Field(ge=0)
    taxonomy_revision: int = Field(default=0, ge=0)
    evidence_revision: int = Field(default=0, ge=0)
    graph_release_id: str = ""
    suggestion: TaxonomySuggestionDecision | None = None
    error: TaxonomySuggestionError | None = None


class TaxonomySuggestionProgress(_QuestionBankModel):
    total: int = Field(ge=0)
    processed: int = Field(ge=0)
    completed: int = Field(ge=0)
    failed: int = Field(ge=0)
    pending: int = Field(ge=0)
    cancelled: int = Field(ge=0)


class TaxonomySuggestionRunResponse(_QuestionBankModel):
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    status: Literal[
        "queued",
        "running",
        "completed",
        "partial",
        "failed",
        "cancelling",
        "cancelled",
        "stale",
    ]
    taxonomy_revision: int = Field(ge=0)
    evidence_revision: int = Field(default=0, ge=0)
    graph_release_id: str = ""
    stale: bool
    created_at: str
    updated_at: str
    items: list[TaxonomySuggestionItem]
    progress: TaxonomySuggestionProgress
    retryable: bool = False


class TaxonomySuggestionStartResponse(_QuestionBankModel):
    job: JobResponse
    run: TaxonomySuggestionRunResponse


class TaxonomySuggestionBatchPreviewRequest(_QuestionBankModel):
    base_revision: int = Field(ge=0)
    policy_version: str = Field(default="strict-exact-v1", max_length=80)


class TaxonomySuggestionBatchManualDecision(_QuestionBankModel):
    proposal_id: str
    decision: Literal["merge", "approve", "reject", "defer"]
    target_term_ids: list[str] = Field(default_factory=list, max_length=12)
    edited_name: str | None = Field(default=None, max_length=160)


class TaxonomySuggestionBatchApplyRequest(_QuestionBankModel):
    base_revision: int = Field(ge=0)
    request_token: str = Field(pattern=r"^[0-9a-fA-F]{32}$")
    policy_version: str = Field(default="strict-exact-v1", max_length=80)
    accepted_manual_decisions: list[TaxonomySuggestionBatchManualDecision] = Field(
        default_factory=list,
        max_length=200,
    )


class TaxonomySuggestionBatchPreviewResponse(_QuestionBankModel):
    run_id: str
    base_revision: int = Field(ge=0)
    evidence_revision: int = Field(ge=0)
    graph_release_id: str = ""
    policy_version: str
    policy_fingerprint: str
    items: list[dict[str, Any]]
    counts: dict[str, int]


class TaxonomyReviewOperationResponse(_QuestionBankModel):
    operation_id: str
    request_token: str
    status: str
    base_revision: int = Field(ge=0)
    taxonomy_revision: int = Field(ge=0)
    automated_proposal_ids: list[str] = Field(default_factory=list)
    teacher_confirmed_proposal_ids: list[str] = Field(default_factory=list)
    skipped: list[dict[str, str]] = Field(default_factory=list)
    remaining_count: int = Field(default=0, ge=0)
    outbox: list[dict[str, Any]] = Field(default_factory=list)
    undo_status: str
    application: dict[str, Any] | None = None
    undo_taxonomy_revision: int | None = Field(default=None, ge=0)


class TaxonomyReviewOperationUndoRequest(_QuestionBankModel):
    expected_revision: int = Field(ge=0)
    request_token: str = Field(pattern=r"^[0-9a-fA-F]{32}$")


class SkillCandidateRunCreateRequest(_QuestionBankModel):
    curriculum_volume_id: str = Field(min_length=1, max_length=120)
    fingerprint: str = Field(min_length=1, max_length=128)
    client_request_token: str = Field(pattern=r"^[0-9a-fA-F]{32}$")


class SkillCandidateRunRetryRequest(_QuestionBankModel):
    client_request_token: str = Field(pattern=r"^[0-9a-fA-F]{32}$")


class SkillCandidateReviewEdits(_QuestionBankModel):
    kind: Literal[
        "link_existing",
        "new_skill",
        "merge_into_approved",
        "keep_section",
    ]
    skill_key: str = ""
    approved_skill_id: str = ""
    section_key: str = ""
    name: str = ""
    include: str = ""
    exclude: str = ""
    examples: list[str] = Field(default_factory=list, max_length=8)


class SkillCandidateReviewRequest(_QuestionBankModel):
    decision: Literal["accept", "reject", "reopen"]
    expected_revision: int = Field(ge=0)
    request_token: str = Field(pattern=r"^[0-9a-fA-F]{32}$")
    edits: SkillCandidateReviewEdits | None = None
    gap_keys: list[str] | None = Field(default=None, max_length=200)


class SkillCandidateApprovedUpdateRequest(_QuestionBankModel):
    name: str = Field(default="", max_length=120)
    include: str = Field(default="", max_length=2000)
    exclude: str = Field(default="", max_length=2000)
    examples: list[str] = Field(default_factory=list, max_length=8)
    expected_revision: int = Field(ge=0)
    request_token: str = Field(pattern=r"^[0-9a-fA-F]{32}$")
