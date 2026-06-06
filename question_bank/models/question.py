from __future__ import annotations

from dataclasses import dataclass, field


ALLOWED_TAG_TYPES = {
    "knowledge_point",
    "method",
    "ability",
    "error_type",
    "model",
    "exam_scope",
    "canonical_knowledge_id",
    "prerequisite",
    "teaching_stage",
    "student_level",
}


@dataclass(frozen=True)
class TagCreate:
    tag_type: str
    tag_value: str
    confidence: float | None = None
    source: str | None = None


@dataclass(frozen=True)
class QuestionCreate:
    question_number: str
    question_text: str
    paper_id: int | None = None
    question_type: str | None = None
    answer_text: str | None = None
    source_file: str | None = None
    page_range: str | None = None
    image_paths: list[str] = field(default_factory=list)
    difficulty: str | None = None
    needs_review: bool = False
    has_images: bool = False
    needs_image_review: bool = False
    tags: list[TagCreate] = field(default_factory=list)


@dataclass(frozen=True)
class QuestionUpdate:
    question_number: str
    question_text: str
    paper_id: int | None = None
    question_type: str | None = None
    answer_text: str | None = None
    source_file: str | None = None
    page_range: str | None = None
    image_paths: list[str] = field(default_factory=list)
    difficulty: str | None = None
    needs_review: bool = False
    has_images: bool = False
    needs_image_review: bool = False
