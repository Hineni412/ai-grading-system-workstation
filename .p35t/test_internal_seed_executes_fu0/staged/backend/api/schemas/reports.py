from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from backend.api.schemas.jobs import JobResponse


class ReportExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_type: Literal["score_excel", "annotated_original_pdf"] = "score_excel"
    force_regenerate: bool = False


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
