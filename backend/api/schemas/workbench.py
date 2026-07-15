from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from backend.api.schemas.jobs import JobSummaryResponse
from backend.api.schemas.sessions import SessionProgress, SessionSummary


class WorkbenchReviewSummary(BaseModel):
    question_count: int
    item_count: int


class WorkbenchAnomalySummary(BaseModel):
    unmatched_papers: int
    scan_issue_students: int
    failed_papers: int


class RecentSessionSummary(BaseModel):
    session: SessionSummary
    progress: SessionProgress


class WorkbenchOverviewResponse(BaseModel):
    current_session: SessionSummary | None = None
    progress: SessionProgress | None = None
    review: WorkbenchReviewSummary | None = None
    anomalies: WorkbenchAnomalySummary | None = None
    recent_jobs: list[JobSummaryResponse]
    recent_sessions: list[RecentSessionSummary]
    updated_at: str


class SessionAnomalyResponse(BaseModel):
    anomaly_id: str
    anomaly_type: Literal["unmatched_paper", "scan_issue", "grading_failed"]
    display_name: str
    student_code: str | None = None
    class_name: str | None = None
    status: str
    detail: str | None = None
    created_at: str | None = None


class SessionAnomalyListResponse(BaseModel):
    items: list[SessionAnomalyResponse]
    total: int
    page: int
    page_size: int
    total_pages: int
