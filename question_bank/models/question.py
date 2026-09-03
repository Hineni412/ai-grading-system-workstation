from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field


ALLOWED_TAG_TYPES = {
    "knowledge_point",
    "method",
    "thought",
    "ability",
    "error_type",
    "model",
    "special_type",
    "exam_scope",
    "curriculum_section",
    "canonical_knowledge_id",
    "prerequisite",
    "teaching_stage",
    "student_level",
    "sub_skill",
    "measured_skill_name",
    "supporting_skill_name",
}

CORE_ANALYSIS_TAG_TYPES = ("knowledge_point", "ability", "exam_scope")

# 与 question_bank/services/similarity_service.py 的 IMAGE_MARKER_PATTERN 保持一致；
# models 层不 import services 层，故在此单独定义，修改时必须两处同步。
_IMAGE_MARKER_PATTERN = re.compile(r"\[\[IMAGE:.+?\]\]")


def duplicate_question_key(question: Mapping[str, object]) -> str:
    """Canonical identity key for "exactly the same question" detection.

    ``[[IMAGE:...]]`` image markers are stripped first (their embedded paths
    are per-import and never stable), then the remaining question text plus
    answer text is compared whitespace-insensitively; empty when the question
    text itself is empty.  Used by import-time duplicate linking and by the
    tag-analysis reuse lookup, so the two must never drift apart.
    """
    question_text = re.sub(
        r"\s+",
        "",
        _IMAGE_MARKER_PATTERN.sub("", str(question.get("question_text") or "")),
    ).strip()
    answer_text = re.sub(
        r"\s+",
        "",
        _IMAGE_MARKER_PATTERN.sub("", str(question.get("answer_text") or "")),
    ).strip()
    return f"{question_text}\n{answer_text}" if question_text else ""


def has_complete_analysis_tags(question: Mapping[str, object]) -> bool:
    raw_tags = question.get("tags")
    tags = raw_tags if isinstance(raw_tags, (list, tuple)) else ()
    seen = {
        str(tag.get("tag_type") or "").strip()
        for tag in tags
        if isinstance(tag, Mapping) and str(tag.get("tag_value") or "").strip()
    }
    try:
        difficulty = float(question.get("difficulty") or 0)
    except (TypeError, ValueError):
        difficulty = 0
    return (
        all(tag_type in seen for tag_type in CORE_ANALYSIS_TAG_TYPES)
        and 1 <= difficulty <= 10
    )


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
