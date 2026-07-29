from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.api.schemas.jobs import JobResponse


class ScoreExcelOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hide_bottom_enabled: bool = True
    hide_bottom_n: int = Field(default=8, ge=0, le=100)
    manual_hidden_student_ids: list[int] = Field(
        default_factory=list,
        max_length=500,
    )

    @field_validator("manual_hidden_student_ids")
    @classmethod
    def _normalize_student_ids(cls, value: list[int]) -> list[int]:
        if any(
            not isinstance(student_id, int)
            or isinstance(student_id, bool)
            or student_id <= 0
            for student_id in value
        ):
            raise ValueError("manual hidden student ids must be positive integers")
        return sorted(set(value))

    @model_validator(mode="after")
    def _normalize_disabled_bottom_rule(self) -> "ScoreExcelOptions":
        if not self.hide_bottom_enabled:
            self.hide_bottom_n = 0
        return self


class ReportExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_type: Literal["score_excel", "annotated_original_pdf"] = "score_excel"
    force_regenerate: bool = False
    excel_options: ScoreExcelOptions | None = None

    @model_validator(mode="after")
    def _validate_report_options(self) -> "ReportExportRequest":
        if self.report_type == "annotated_original_pdf":
            if self.excel_options is not None:
                raise ValueError("excel options are only valid for score_excel")
            return self
        if self.excel_options is None:
            self.excel_options = ScoreExcelOptions()
        return self


class ReportExportHistoryItem(JobResponse):
    is_current_revision: bool
    file_status: Literal[
        "pending",
        "available",
        "expired",
        "failed",
        "cancelled",
        "unavailable",
    ]


class ReportExportContextResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score_revision: str
    has_results: bool
    jobs: list[ReportExportHistoryItem]
    total: int
    page: int
    page_size: int
    total_pages: int
