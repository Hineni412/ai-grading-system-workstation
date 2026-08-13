from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class WeakPoint:
    knowledge_point: str
    mastery: float
    canonical_knowledge_id: str = ""
    stability: float | None = None
    error_types: list[str] = field(default_factory=list)
    related_question_ids: list[str] = field(default_factory=list)
    recommended_level: str = ""
    priority: int = 1
    raw_knowledge_ids: list[str] = field(default_factory=list)
    raw_knowledge_point: str = ""
    raw_error_types: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "knowledge_point": self.knowledge_point,
            "canonical_knowledge_id": self.canonical_knowledge_id,
            "mastery": self.mastery,
            "stability": self.stability,
            "error_types": list(self.error_types),
            "related_question_ids": list(self.related_question_ids),
            "recommended_level": self.recommended_level,
            "priority": self.priority,
            "raw_knowledge_ids": list(self.raw_knowledge_ids),
            "raw_knowledge_point": self.raw_knowledge_point,
            "raw_error_types": list(self.raw_error_types),
        }


@dataclass(slots=True)
class StudentMasteryProfile:
    student_id: str
    student_name: str = ""
    class_id: str = ""
    exam_id: str = ""
    weak_points: list[WeakPoint] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "student_id": self.student_id,
            "student_name": self.student_name,
            "class_id": self.class_id,
            "exam_id": self.exam_id,
            "weak_points": [item.to_dict() for item in self.weak_points],
        }
