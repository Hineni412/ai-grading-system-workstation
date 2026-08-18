from __future__ import annotations

from typing import Literal

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
    teaching_prep_model: str | None = Field(default=None, max_length=200)
    class_teacher_model: str | None = Field(default=None, max_length=200)
    request_speed_mode: Literal[
        "automatic",
        "conservative",
        "custom",
    ] | None = None
    max_concurrent_requests: int | None = Field(default=None, ge=1, le=100)
    requests_per_minute: int | None = Field(default=None, ge=1, le=10_000)
    batch_enabled: bool | None = None
    batch_model: str | None = Field(default=None, max_length=200)
    batch_base_url: str | None = Field(default=None, max_length=2048)
    batch_api_key: str | None = Field(default=None, max_length=8192)


class ModelProfileResponse(_ModelProfileModel):
    name: str
    base_url: str
    has_api_key: bool
    ocr_model: str
    grading_model: str
    config_base_url: str
    has_config_api_key: bool
    config_model: str
    teaching_prep_model: str
    class_teacher_model: str
    request_speed_mode: Literal["automatic", "conservative", "custom"]
    max_concurrent_requests: int
    requests_per_minute: int
    batch_enabled: bool
    batch_model: str
    batch_base_url: str
    has_batch_api_key: bool


class ModelTaskBinding(_ModelProfileModel):
    profile_name: str | None = Field(default=None, max_length=80)
    model: str = Field(default="", max_length=200)


class ModelTaskBindingsUpdateRequest(_ModelProfileModel):
    content_generation: ModelTaskBinding
    grading: ModelTaskBinding
    teaching_prep: ModelTaskBinding
    class_teacher: ModelTaskBinding


class ModelProfileStateResponse(_ModelProfileModel):
    profiles: list[ModelProfileResponse]
    active_profile_name: str | None = None
    active_profile: ModelProfileResponse | None = None
    task_bindings: dict[
        Literal[
            "content_generation",
            "grading",
            "teaching_prep",
            "class_teacher",
        ],
        ModelTaskBinding,
    ]


class ModelExecutionStatusResponse(_ModelProfileModel):
    mode: Literal["automatic", "conservative", "custom"]
    configured_max_in_flight: int = Field(ge=1, le=100)
    effective_max_in_flight: int = Field(ge=1, le=100)
    requests_per_minute: int = Field(ge=1, le=10_000)
    active: int = Field(ge=0)
    queued: int = Field(ge=0)
    peak_active: int = Field(ge=0)
    physical_request_count: int = Field(ge=0)
    limiting_reason: Literal["configured", "provider_overload", "recovering"]
