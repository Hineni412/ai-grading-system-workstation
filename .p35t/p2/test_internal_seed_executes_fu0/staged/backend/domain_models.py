"""Shared grading domain data with no service-layer dependencies."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class QuestionGradingDetail:
    question_id: str
    score_awarded: float
    deduction_reason: str | None
    knowledge_id: str = "UNKNOWN"
    error_category: str | None = None
    error_summary: str | None = None
    confidence_score: float | None = None
    knowledge_ids: list[str] = field(default_factory=list)
    secondary_errors: list["SecondaryError"] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class SecondaryError:
    category: str
    summary: str
    evidence: str = ""


@dataclass
class GradingResult:
    student_name: str
    total_score: float
    student_score: float
    needs_human_review: bool
    grading_details: list[QuestionGradingDetail]
    raw_json: dict[str, Any]


@dataclass
class ExamPaperGroup:
    front_image: Path
    back_image: Path
    student_name: str
    student_id: int | None = None
    detected_name: str | None = None
    source_label: str = ""
    enhanced_front_image: Path | None = None
    enhanced_back_image: Path | None = None
    match_method: str = "exact"
    match_score: float = 1.0
