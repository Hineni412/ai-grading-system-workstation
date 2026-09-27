from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _AuthoringModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


AuthoringKind = Literal["decompose", "adapt"]


class AuthoringTaskCardItem(_AuthoringModel):
    id: str
    label: str
    method: str
    description: str


class AuthoringTaskCardCatalogResponse(_AuthoringModel):
    items: list[AuthoringTaskCardItem]


class AuthoringWorkSummary(_AuthoringModel):
    work_id: str
    kind: AuthoringKind
    title: str
    source_question_id: int | None
    source_question_number: str
    source_question_snippet: str
    current_version: int = Field(ge=0)
    created_at: str
    updated_at: str


class AuthoringWorkListResponse(_AuthoringModel):
    items: list[AuthoringWorkSummary]
    total: int


class AuthoringVersionSummary(_AuthoringModel):
    version_no: int = Field(ge=1)
    created_at: str


class AuthoringWorkDetail(AuthoringWorkSummary):
    source_snapshot: dict[str, Any]
    task_card: dict[str, Any]
    versions: list[AuthoringVersionSummary]
    latest_content: dict[str, Any] | None


class AuthoringWorkCreateRequest(_AuthoringModel):
    kind: AuthoringKind
    source_question_id: int = Field(gt=0)
    task_card: dict[str, Any] | None = None
    title: str = Field(default="", max_length=200)
    operation_token: str = Field(pattern=r"^[0-9a-fA-F]{32}$")


class AuthoringVersionCreateRequest(_AuthoringModel):
    content: dict[str, Any]
    base_version: int = Field(ge=0)
    operation_token: str = Field(pattern=r"^[0-9a-fA-F]{32}$")


class AuthoringVersionResponse(_AuthoringModel):
    work_id: str
    version_no: int = Field(ge=1)
    content: dict[str, Any]
    created_at: str
    current_version: int = Field(ge=0)


class AuthoringWorkDeleteResponse(_AuthoringModel):
    work_id: str
    deleted: bool
