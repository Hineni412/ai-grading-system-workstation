from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class ReviewRubricPointResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    part_id: str
    part_label: str
    step_id: str
    core_goal: str
    score: float
    standard_answer: str
    accepted_answers: list[str]
    match_rule: str
    required_elements: list[str]
    deduction_rules: list[str]
    answer_only_max_score: float | None
    require_final_answer: bool | None
    final_answer_rule: str
    answer_kind: Literal["fixed", "conditions"] = "fixed"


class ReviewRubricSectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str
    parent_question_id: str
    question_type: str | None
    max_score: float
    knowledge_labels: list[str]
    points: list[ReviewRubricPointResponse]


__all__ = [
    "ReviewRubricPointResponse",
    "ReviewRubricSectionResponse",
]
