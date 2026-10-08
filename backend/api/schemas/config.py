from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)


class SessionConfigRequest(BaseModel):
    rubric: dict[str, Any]
    answer_key: dict[str, Any]
    meta: dict[str, Any] = Field(default_factory=lambda: {"warnings": []})


class SessionConfigResponse(BaseModel):
    session_id: int
    rubric_path: str
    answer_key_path: str
    template_config_path: str | None = None
    source_paper_path: str | None = None
    source_paper_sha256: str | None = None
    rubric: dict[str, Any]
    answer_key: dict[str, Any]


class ConfigRichInlineSegmentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(max_length=200_000)
    superscript: bool
    subscript: bool
    underline: bool
    line_break: bool


class ConfigRichTableCellResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    segments: list[ConfigRichInlineSegmentResponse] = Field(max_length=2_000)


class ConfigRichTableRowResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cells: list[ConfigRichTableCellResponse] = Field(max_length=100)


class ConfigRichBlockResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["paragraph", "table"]
    text: str = Field(max_length=500_000)
    segments: list[ConfigRichInlineSegmentResponse] = Field(max_length=5_000)
    rows: list[ConfigRichTableRowResponse] = Field(max_length=500)
    html: str = Field(default="", max_length=500_000)
    asset_indexes: list[int] = Field(max_length=5_000)
    asset_urls: list[str] = Field(max_length=5_000)


class ConfigRichContentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available: bool
    question_block_count: int = Field(ge=0, le=5_000)
    answer_block_count: int = Field(ge=0, le=5_000)
    question_blocks: list[ConfigRichBlockResponse] = Field(max_length=5_000)
    answer_blocks: list[ConfigRichBlockResponse] = Field(max_length=5_000)


class ConfigQuestionPreviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")
    question_type: str = Field(min_length=1, max_length=100)
    question_preview: str = Field(max_length=500)
    answer_preview: str = Field(max_length=500)
    answer_present: bool
    needs_review: bool
    question_type_review_required: bool = False
    question_type_review_reason: str = Field(default="", max_length=200)
    question_type_basis: str = Field(default="", max_length=200)
    parse_warnings: list[str] = Field(default_factory=list, max_length=20)
    local_answer_trusted: bool
    has_question_asset: bool
    has_answer_asset: bool
    rich_content: ConfigRichContentResponse


class ConfigAmbiguousAssetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(pattern=r"^A[1-9]\d{0,3}$")
    previous_question_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")
    next_question_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")
    source_section: Literal["question", "answer"]
    asset_url: str = Field(pattern=r"^/api/sessions/[1-9]\d*/config/sources/[0-9a-f]{32}/ambiguous-assets/A[1-9]\d{0,3}$")


class ConfigSourceAssetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: str = Field(pattern=r"^[AP][1-9]\d{0,3}$")
    asset_url: str = Field(
        pattern=(
            r"^/api/sessions/[1-9]\d*/config/sources/[0-9a-f]{32}/"
            r"(?:ambiguous-assets/A[1-9]\d{0,3}|questions/[A-Za-z0-9_-]{1,100}/assets/(?:question|answer)(?:/\d+)?)$"
        )
    )
    assignment_state: Literal["automatic", "uncertain"]
    question_id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9_-]{1,100}$",
    )
    asset_kind: Literal["question", "answer"]
    candidate_question_ids: list[str] = Field(max_length=500)


class ConfigSourceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: int = Field(gt=0)
    source_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    source_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    safe_filename: str = Field(min_length=1, max_length=255)
    suffix: Literal[".docx", ".pdf"]
    size_bytes: int = Field(gt=0, le=200 * 1024 * 1024)
    sha256_prefix: str = Field(pattern=r"^[0-9a-f]{12}$")
    parse_state: Literal["ready"]
    questions: list[ConfigQuestionPreviewResponse] = Field(max_length=500)
    ambiguous_assets: list[ConfigAmbiguousAssetResponse] = Field(
        default_factory=list,
        max_length=5_000,
    )
    assets: list[ConfigSourceAssetResponse] = Field(
        default_factory=list,
        max_length=5_000,
    )


class ConfigSourceSubmissionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["processing", "succeeded", "failed", "replaced"]
    source: ConfigSourceResponse | None = None


class ConfigSourceDuplicateItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")
    kind: Literal[
        "exact_reusable",
        "exact_needs_analysis",
        "image_uncertain",
        "answer_conflict",
        "variant",
        "suspected",
        "same_session",
    ]
    matched_question_id: int = Field(gt=0)
    matched_paper_title: str = Field(max_length=500)
    matched_question_number: str = Field(default="", max_length=100)
    similarity: float = Field(ge=0.0, le=1.0)
    matched_question_excerpt: str = Field(max_length=200)
    bank_answer_text: str | None = Field(default=None, max_length=20_000)
    suggested_answer_override: str | None = Field(default=None, max_length=20_000)
    reason: str = Field(max_length=300)


class ConfigSourceDuplicatesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    source_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    items: list[ConfigSourceDuplicateItemResponse] = Field(max_length=500)


class ConfigQuestionGenerationStateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")
    state: Literal["pending", "running", "passed", "blocked", "failed"]
    reason: str = Field(default="", max_length=160)
    retryable: bool
    category: str = Field(default="", pattern=r"^[a-z_]{0,80}$")


class ConfigGenerationQuestionStatesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: int = Field(gt=0)
    questions: list[ConfigQuestionGenerationStateResponse] = Field(max_length=500)


class ConfigSourceQuestionDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")
    question_type: Literal[
        "choice",
        "fill_blank",
        "calculation",
        "proof",
        "comprehensive",
    ] | None = None
    excluded: bool
    answer_confirmed: bool = False
    answer_override: str | None = Field(default=None, max_length=20_000)
    bank_match: Literal["same", "different", "reanalyze"] | None = None
    bank_question_id: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _validate_answer_override(self) -> ConfigSourceQuestionDecisionRequest:
        if self.answer_override is not None:
            self.answer_override = self.answer_override.strip()
            if not self.answer_override:
                raise ValueError("answer_override must be nonblank")
            if not self.answer_confirmed:
                raise ValueError("answer_override requires answer_confirmed=true")
        if self.bank_match == "same" and self.bank_question_id is None:
            raise ValueError("bank_match='same' requires bank_question_id")
        return self


class ConfigAmbiguousAssetDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(pattern=r"^[AP][1-9]\d{0,3}$")
    action: Literal["bind", "ignore"]
    question_id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9_-]{1,100}$",
    )
    asset_kind: Literal["question", "answer"] | None = None

    @model_validator(mode="after")
    def _validate_binding(self) -> ConfigAmbiguousAssetDecisionRequest:
        if self.action == "ignore":
            if self.question_id is not None or self.asset_kind is not None:
                raise ValueError("ignored asset cannot have a binding target")
        elif self.question_id is None or self.asset_kind is None:
            raise ValueError("bound asset requires a question and role")
        return self


class ConfigSourceGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    source_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    generation_mode: Literal["batched", "per_question", "whole_document"] = "batched"

    @field_validator("generation_mode")
    @classmethod
    def _normalize_legacy_generation_mode(cls, value: str) -> str:
        # Older clients called this mode "per_question". Keep accepting the
        # wire value while always executing the new three-question batches.
        return "batched" if value == "per_question" else value
    decisions: list[ConfigSourceQuestionDecisionRequest] = Field(
        default_factory=list,
        max_length=500,
    )
    asset_decisions: list[ConfigAmbiguousAssetDecisionRequest] = Field(
        default_factory=list,
        max_length=5_000,
    )
    sync_to_question_bank: bool = False
    chapter_type_authorization: dict[str, Any] | None = None
    curriculum_volume_id: str | None = Field(default=None, max_length=80)
    regenerate_question_ids: list[str] | None = Field(
        default=None,
        min_length=1,
        max_length=500,
    )
    base_revision: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    client_request_token: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{32}$",
    )

    @field_validator("regenerate_question_ids")
    @classmethod
    def _normalize_regenerate_question_ids(
        cls,
        value: list[str] | None,
    ) -> list[str] | None:
        if value is None:
            return None
        normalized = list(
            dict.fromkeys(str(item or "").strip() for item in value)
        )
        if not normalized or any(
            not item or len(item) > 100
            for item in normalized
        ):
            raise ValueError(
                "regenerate_question_ids must contain nonblank bounded ids"
            )
        return normalized

    @field_validator("curriculum_volume_id")
    @classmethod
    def _normalize_curriculum_volume_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", normalized):
            raise ValueError("curriculum_volume_id is invalid")
        return normalized

    @model_validator(mode="after")
    def _validate_targeted_regeneration(self) -> ConfigSourceGenerationRequest:
        targeted = self.regenerate_question_ids is not None
        if targeted != (self.base_revision is not None):
            raise ValueError(
                "regenerate_question_ids and base_revision must be provided together"
            )
        if targeted and (
            self.generation_mode != "batched"
            or not self.sync_to_question_bank
        ):
            raise ValueError(
                "targeted regeneration must be batched and sync to question bank"
            )
        if self.sync_to_question_bank and self.curriculum_volume_id is None:
            raise ValueError(
                "curriculum_volume_id is required before question analysis"
            )
        return self


class ConfigGenerationRetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_job_id: int = Field(gt=0)
    retry_question_ids: list[str] | None = Field(default=None, min_length=1, max_length=500)
    confirm_uncertain_retry: bool = False
    client_request_token: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{32}$",
    )

    @field_validator("retry_question_ids")
    @classmethod
    def _normalize_question_ids(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        normalized = list(dict.fromkeys(str(item or "").strip() for item in value))
        if not normalized or any(not item or len(item) > 100 for item in normalized):
            raise ValueError("retry_question_ids must contain nonblank bounded ids")
        return normalized


class ConfigEditorEditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row_id: str = Field(min_length=1, max_length=64)
    score: float | None = Field(default=None, allow_inf_nan=False)
    standard_answer: str | None = Field(default=None, max_length=20_000)
    accepted_answers: list[str] | None = Field(default=None, max_length=200)
    answer_only_max_score: float | None = Field(default=None, allow_inf_nan=False)
    require_final_answer: bool | None = None
    required_elements: list[str] | None = Field(default=None, max_length=200)
    deduction_rules: list[str] | None = Field(default=None, max_length=200)
    part_deduction_rules: list[str] | None = Field(default=None, max_length=200)
    final_answer_rule: str | None = Field(default=None, max_length=20_000)

    @computed_field(return_type=bool)
    @property
    def answer_only_max_score_provided(self) -> bool:
        return "answer_only_max_score" in self.model_fields_set


class ManualPartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    part_id: str = Field(min_length=1, max_length=100)
    score: float = Field(allow_inf_nan=False)
    core_goal: str = Field(min_length=1, max_length=20_000)


class SplitScoringUnitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["split"]
    question_id: str = Field(min_length=1, max_length=100)
    count: int = Field(ge=2, le=20)
    style: Literal["subquestion", "blank"]


class ReplaceScoringUnitsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["replace_parts"]
    question_id: str = Field(min_length=1, max_length=100)
    parts: list[ManualPartRequest] = Field(min_length=1, max_length=100)


class ManualStepRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: str = Field(min_length=1, max_length=100)
    score: float = Field(allow_inf_nan=False)
    core_goal: str = Field(min_length=1, max_length=20_000)


class ManualQuestionPartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    part_id: str = Field(min_length=1, max_length=100)
    steps: list[ManualStepRequest] = Field(min_length=1, max_length=100)


class ReplaceQuestionStructureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["replace_question_structure"]
    question_id: str = Field(min_length=1, max_length=100)
    parts: list[ManualQuestionPartRequest] = Field(min_length=1, max_length=100)


ConfigEditorCommandRequest = (
    SplitScoringUnitRequest
    | ReplaceScoringUnitsRequest
    | ReplaceQuestionStructureRequest
)


class ConfigEditorSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    edits: list[ConfigEditorEditRequest] = Field(default_factory=list, max_length=1000)
    commands: list[ConfigEditorCommandRequest] = Field(default_factory=list, max_length=200)


class ConfigEditorRowResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row_id: str
    question_id: str
    part_id: str
    step_id: str
    part_label: str
    question_type: str
    core_goal: str
    score: float
    standard_answer: str
    accepted_answers: list[str]
    match_rule: str
    answer_only_max_score: float | None
    require_final_answer: bool | None
    required_elements: list[str]
    deduction_rules: list[str]
    part_deduction_rules: list[str]
    final_answer_rule: str
    response_mode: str = ""
    allow_alternative_methods: bool = True
    equivalent_rules: list[str] = Field(default_factory=list)
    answer_kind: Literal["fixed", "conditions"] = "fixed"


class ConfigEditorIssueResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    severity: str
    row_id: str | None
    field: str
    message: str


class ConfigEditorSourceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    safe_filename: str
    suffix: Literal[".docx", ".pdf"]
    sha256_prefix: str = Field(pattern=r"^[0-9a-f]{12}$")


class ConfigEditorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: int
    configured: bool
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    rows: list[ConfigEditorRowResponse]
    total_score: float
    issues: list[ConfigEditorIssueResponse]
    source: ConfigEditorSourceResponse | None


class ConfigSaveResultResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config_saved: bool
    mapping_status: Literal["not_present", "refreshed", "reconfirm_required"]
    mapping_message: str


class ConfigEditorSaveResponse(ConfigEditorResponse):
    save_result: ConfigSaveResultResponse
