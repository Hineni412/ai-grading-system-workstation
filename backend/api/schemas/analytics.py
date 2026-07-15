from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class AnalysisScope(BaseModel):
    session_id: int
    class_name: str | None = None
    question_id: str | None = None


class QuestionAnalysisItem(BaseModel):
    class_name: str
    question_id: str
    max_score: float | None = None
    score_rate: float | None = None
    average_score: float | None = None
    deduction_count: int
    attempt_count: int
    metric_status: Literal["ready", "missing_max_score", "no_attempts"]


class QuestionAnalysisListResponse(BaseModel):
    scope: AnalysisScope
    classes: list[str]
    items: list[QuestionAnalysisItem]
    total: int
    page: int
    page_size: int
    total_pages: int


class StudentAnalysisItem(BaseModel):
    result_id: int
    detail_id: int
    student_id: int
    student_code: str | None = None
    student_name: str
    class_name: str
    question_id: str
    score_awarded: float
    max_score: float | None = None
    deduction_amount: float | None = None
    deduction_reason: str | None = None
    needs_review: bool
    evidence_url: str


class StudentAnalysisListResponse(BaseModel):
    scope: AnalysisScope
    items: list[StudentAnalysisItem]
    total: int
    page: int
    page_size: int
    total_pages: int
