"""Request models for the 智学网 score-import endpoints."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class ScoreImportPlanRequest(BaseModel):
    """Shared plan parameters for preview and confirm."""

    upload_ids: list[str] = Field(default_factory=list, max_length=64)
    sheet_classes: dict[str, dict[str, str]] = Field(default_factory=dict)
    part_choices: dict[str, Literal["parts", "whole"]] = Field(default_factory=dict)
    item_max: dict[str, float] = Field(default_factory=dict)
    student_overrides: dict[str, int | None] = Field(default_factory=dict)

    @field_validator("upload_ids")
    @classmethod
    def _upload_id_format(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for item in value:
            token = str(item).strip().lower()
            if len(token) != 64 or any(c not in "0123456789abcdef" for c in token):
                raise ValueError("upload_ids must be sha256 hex digests")
            cleaned.append(token)
        return cleaned

    @field_validator("item_max")
    @classmethod
    def _positive_item_max(cls, value: dict[str, float]) -> dict[str, float]:
        for key, max_score in value.items():
            if max_score <= 0:
                raise ValueError(f"item_max[{key}] must be positive")
        return value


class ScoreImportConfirmRequest(ScoreImportPlanRequest):
    """Confirm adds the preview revision and the replace flag."""

    preview_revision: str = Field(min_length=1)
    replace_existing: bool = False
