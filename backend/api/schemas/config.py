from __future__ import annotations

import base64
import binascii
from typing import Any

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

    @field_validator("retry_question_ids")
    @classmethod
    def _normalize_question_ids(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        normalized = list(dict.fromkeys(str(item or "").strip() for item in value))
        if not normalized or any(not item or len(item) > 100 for item in normalized):
            raise ValueError("retry_question_ids must contain nonblank bounded ids")
        return normalized
