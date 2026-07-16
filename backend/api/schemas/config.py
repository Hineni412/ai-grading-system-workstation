from __future__ import annotations

import base64
import binascii
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


class ConfigQuestionPreviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")
    question_type: str = Field(min_length=1, max_length=100)
    question_preview: str = Field(max_length=500)
    answer_preview: str = Field(max_length=500)
    answer_present: bool
    needs_review: bool
    local_answer_trusted: bool
    has_question_asset: bool
    has_answer_asset: bool


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


class ConfigSourceSubmissionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["processing", "succeeded", "failed"]
    source: ConfigSourceResponse | None = None


class ConfigSourceQuestionDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")
    question_type: Literal[
        "choice",
        "fill_blank",
        "calculation",
        "proof",
        "comprehensive",
    ]
    excluded: bool


class ConfigSourceGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    source_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    generation_mode: Literal["per_question", "whole_document"]
    decisions: list[ConfigSourceQuestionDecisionRequest] = Field(
        default_factory=list,
        max_length=500,
    )
    client_request_token: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{32}$",
    )


class ConfigGenerationQuestionImages(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=28_000_000)
    answer: str | None = Field(default=None, max_length=28_000_000)

    @field_validator("question", "answer")
    @classmethod
    def _validate_base64_image(cls, value: str | None) -> str | None:
        if value is None:
            return None
        clean = str(value).strip()
        if not clean:
            return None
        try:
            decoded = base64.b64decode(clean, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("question image values must be valid base64") from exc
        if not decoded or len(decoded) > 20 * 1024 * 1024:
            raise ValueError("question image values must decode to 1..20971520 bytes")
        return clean


class ConfigGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmed_blocks: list[dict[str, Any]] = Field(min_length=1, max_length=500)
    document_text: str = Field(default="", max_length=2_000_000)
    question_images: dict[str, ConfigGenerationQuestionImages] = Field(
        default_factory=dict,
        max_length=500,
    )

    @field_validator("question_images")
    @classmethod
    def _validate_question_image_ids(
        cls,
        value: dict[str, ConfigGenerationQuestionImages],
    ) -> dict[str, ConfigGenerationQuestionImages]:
        if any(not str(key).strip() or len(str(key)) > 100 for key in value):
            raise ValueError("question image ids must be nonblank and at most 100 characters")
        return value


class ConfigGenerationRetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_job_id: int = Field(gt=0)
    retry_question_ids: list[str] | None = Field(default=None, min_length=1, max_length=500)
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
    score: float | None = Field(default=None, ge=0, le=100)
    standard_answer: str | None = Field(default=None, max_length=20_000)
    accepted_answers: list[str] | None = Field(default=None, max_length=200)
    answer_only_max_score: float | None = Field(default=None, ge=0, le=100)
    require_final_answer: bool | None = None
    required_elements: list[str] | None = Field(default=None, max_length=200)
    deduction_rules: list[str] | None = Field(default=None, max_length=200)
    final_answer_rule: str | None = Field(default=None, max_length=20_000)


class ManualPartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    part_id: str = Field(min_length=1, max_length=100)
    score: float = Field(gt=0, le=100)
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


ConfigEditorCommandRequest = SplitScoringUnitRequest | ReplaceScoringUnitsRequest


class ConfigEditorSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    edits: list[ConfigEditorEditRequest] = Field(default_factory=list, max_length=1000)
    commands: list[ConfigEditorCommandRequest] = Field(default_factory=list, max_length=200)


class ConfigEditorRefineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    commands: list[ConfigEditorCommandRequest] = Field(default_factory=list, max_length=200)
    client_request_token: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{32}$",
    )


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
    knowledge: str
    answer_only_max_score: float | None
    require_final_answer: bool | None
    required_elements: list[str]
    deduction_rules: list[str]
    final_answer_rule: str


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
