from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class _ModelProfileModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelProfileUpdateRequest(_ModelProfileModel):
    base_url: str | None = Field(default=None, max_length=2048)
    api_key: str | None = Field(default=None, max_length=8192)
    ocr_model: str | None = Field(default=None, max_length=200)
    grading_model: str | None = Field(default=None, max_length=200)
    config_base_url: str | None = Field(default=None, max_length=2048)
    config_api_key: str | None = Field(default=None, max_length=8192)
    config_model: str | None = Field(default=None, max_length=200)


class ModelProfileResponse(_ModelProfileModel):
    name: str
    base_url: str
    has_api_key: bool
    ocr_model: str
    grading_model: str
    config_base_url: str
    has_config_api_key: bool
    config_model: str


class ModelProfileStateResponse(_ModelProfileModel):
    profiles: list[ModelProfileResponse]
    active_profile_name: str | None = None
    active_profile: ModelProfileResponse | None = None
