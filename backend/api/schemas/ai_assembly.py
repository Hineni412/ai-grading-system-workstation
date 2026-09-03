from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _AiAssemblyModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AssemblySpecRowPayload(_AiAssemblyModel):
    question_type: str = Field(min_length=1, max_length=40)
    count: int = Field(ge=1, le=200)
    knowledge_points: list[str] = Field(default_factory=list, max_length=50)
    difficulty: int | None = Field(default=None, ge=1, le=9)
    score: float | None = Field(default=None, gt=0, le=1000)


class AssemblySpecPayload(_AiAssemblyModel):
    title: str = Field(default="", max_length=120)
    rows: list[AssemblySpecRowPayload] = Field(min_length=1, max_length=100)
    scope_knowledge_points: list[str] = Field(default_factory=list, max_length=500)


class SpecGenerationRequestPayload(_AiAssemblyModel):
    template_paper_id: int | None = Field(default=None, gt=0)
    scope_keys: list[str] = Field(default_factory=list, max_length=200)
    difficulty_ratio: dict[str, int] = Field(default_factory=dict, max_length=20)
    type_counts: dict[str, int] = Field(default_factory=dict, max_length=40)
    exam_types: list[str] = Field(default_factory=list, max_length=20)
    years: list[int] = Field(default_factory=list, max_length=50)
    free_text: str = Field(default="", max_length=2000)
    current_spec: AssemblySpecPayload | None = None
    locked_question_ids: list[int] = Field(default_factory=list, max_length=500)
    new_instruction: str = Field(default="", max_length=2000)


class AiAssemblySpecJobSubmitRequest(_AiAssemblyModel):
    request: SpecGenerationRequestPayload


class AiAssemblyPreflightResponse(_AiAssemblyModel):
    configured: bool
    service_name: str | None = None
    model_name: str | None = None
    call_count: Literal[1] = 1
    estimated_total_tokens: int = Field(ge=0)


class AiAssemblySelectRequest(_AiAssemblyModel):
    spec: AssemblySpecPayload
    dedupe_enabled: bool = True
    exclude_ids: list[int] = Field(default_factory=list, max_length=500)


class AiAssemblyRowSelectionResponse(_AiAssemblyModel):
    row_index: int
    question_ids: list[int]


class AiAssemblyRelaxationResponse(_AiAssemblyModel):
    step: str
    title: str
    sacrifice: str
    knowledge_points: list[str] = Field(default_factory=list)


class AiAssemblyGapResponse(_AiAssemblyModel):
    row_index: int
    missing: int
    candidates: int
    excluded_by_dedupe: int
    suggestions: list[AiAssemblyRelaxationResponse]


class AiAssemblySelectResponse(_AiAssemblyModel):
    rows: list[AiAssemblyRowSelectionResponse]
    gaps: list[AiAssemblyGapResponse]


class AiAssemblyTemplateEntryResponse(_AiAssemblyModel):
    question_number: str
    question_type: str
    difficulty: int | None = None
    score: float | None = None


class AiAssemblyTemplateStructureResponse(_AiAssemblyModel):
    paper_id: int
    paper_title: str
    entries: list[AiAssemblyTemplateEntryResponse]
