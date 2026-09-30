from __future__ import annotations

import json
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

# 八上重打方案：整题知识点与章归属改由判定点关联派生；写入侧在无可用链接时
# 落下 tag_status='derived_pending' 标记。归属"已就绪" = 已派生出
# 知识点+章/小节行，或已明确标记为待派生（打标本身已完成、等判定点关联）。
# 派生也可能只产出前置知识行（全为 supporting_prerequisite 关联）或只挂
# 无小节锚点的跨章节技能键——这两种落库形态同样算归属就绪。
DERIVED_PENDING_STATUS = "derived_pending"
OWNERSHIP_TAG_TYPES = ("knowledge_point", "exam_scope", "curriculum_section")


def analysis_ownership_satisfied(
    tag_types: object,
    *,
    derived_pending: bool = False,
) -> bool:
    seen = {str(tag_type).strip() for tag_type in tag_types or ()}
    derived = (
        "knowledge_point" in seen
        and bool(seen & {"exam_scope", "curriculum_section"})
    ) or "prerequisite" in seen
    return derived or derived_pending

# Exact identity keeps each image's position; similarity search has a separate,
# deliberately looser text comparison.
_IMAGE_MARKER_PATTERN = re.compile(
    r"\[\[IMAGE:([^\]|]*?)(?:\|[^\]]*)?\]\]",
    re.IGNORECASE | re.DOTALL,
)
EXACT_QUESTION_KEY_PREFIX = "exact-v4:"


def normalize_identity_text(value: object) -> str:
    # Do not use NFKC: it turns exponents such as ² into ordinary digits.
    text = str(value or "").translate({**{n: n - 0xFEE0 for n in range(0xFF01, 0xFF5F)},
                                      ord("−"): "-", ord("﹣"): "-"})
    return re.sub(r"\s+", "", text)


def duplicate_question_key(question: Mapping[str, object]) -> str:
    """Exact question identity; visual evidence must be supplied by the reader.

    Numbering and answers belong to the source paper, while the stem (including
    options and formula symbols) and every illustration identify the exercise.
    Missing image evidence deliberately produces no identity.
    """
    text = str(question.get("question_text") or "").strip()
    number = str(question.get("question_number") or "").strip()
    if number:
        text = re.sub(r"^\s*" + re.escape(number) + r"\s*[.．、)）](?!\d)\s*", "", text, count=1)
    text = re.sub(r"^(?:[（(]\s*\d+(?:\.\d+)?\s*分\s*[）)]\s*)+", "", text)
    pictures = question.get("image_content_keys")
    has_visual = bool(_IMAGE_MARKER_PATTERN.search(text) or question.get("image_paths")
                      or question.get("has_images") or question.get("source_regions"))
    if has_visual and not pictures:
        return ""
    marker_keys = question.get("image_marker_keys") or {}
    if any(path not in marker_keys for path in _IMAGE_MARKER_PATTERN.findall(text)):
        return ""
    text = _IMAGE_MARKER_PATTERN.sub(lambda match: "[[IMAGE:" + str(marker_keys[match.group(1)]) + "]]", text)
    text = normalize_identity_text(text)
    if not text or (question.get("visual_evidence_missing")):
        return ""
    return EXACT_QUESTION_KEY_PREFIX + json.dumps({"text": text, "options": question.get("options") or [],
                                     "formulas": question.get("formula_content") or [],
                                     "images": pictures or []}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def has_complete_analysis_tags(question: Mapping[str, object]) -> bool:
    raw_tags = question.get("tags")
    tags = raw_tags if isinstance(raw_tags, (list, tuple)) else ()
    seen = {
        str(tag.get("tag_type") or "").strip()
        for tag in tags
        if isinstance(tag, Mapping) and str(tag.get("tag_value") or "").strip()
    }
    derived_pending = any(
        isinstance(tag, Mapping)
        and str(tag.get("tag_type") or "").strip() == "tag_status"
        and str(tag.get("tag_value") or "").strip() == DERIVED_PENDING_STATUS
        for tag in tags
    )
    try:
        difficulty = float(question.get("difficulty") or 0)
    except (TypeError, ValueError):
        difficulty = 0
    return (
        "ability" in seen
        and analysis_ownership_satisfied(seen, derived_pending=derived_pending)
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
