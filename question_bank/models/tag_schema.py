from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from question_bank.taxonomy.registry import canonicalize_error_type


LIST_FIELDS = (
    "knowledge_points",
    "method_tags",
    "ability_tags",
    "math_model_tags",
    "error_prone_points",
    "prerequisite_points",
)
MAX_TAG_LENGTH = 36
STUDENT_LEVELS = ("入门补缺", "基础巩固", "中档提升", "综合突破", "压轴拔高")
ERROR_PRONE_CATEGORIES = (
    "条件识别不完整",
    "概念理解不清",
    "公式/定理误用",
    "运算化简错误",
    "图形关系识别错误",
    "辅助线思路缺失",
    "分类讨论遗漏",
    "数形转化困难",
    "题意阅读偏差",
    "书写依据不完整",
    "单位/符号错误",
    "综合建模困难",
)


@dataclass(frozen=True)
class TaggingContext:
    question_text: str
    answer_text: str | None = None
    question_number: str | None = None
    question_type: str | None = None
    grade: str | None = None
    semester: str | None = None
    exam_type: str | None = None
    district: str | None = None
    corpus_stats: dict[str, Any] = field(default_factory=dict)
    existing_tags: list[str] = field(default_factory=list)

    @property
    def has_answer(self) -> bool:
        return bool(str(self.answer_text or "").strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_text": _clean_text(self.question_text),
            "answer_text": _clean_optional(self.answer_text),
            "question_number": _clean_optional(self.question_number),
            "question_type": _clean_optional(self.question_type),
            "grade": _clean_optional(self.grade),
            "semester": _clean_optional(self.semester),
            "exam_type": _clean_optional(self.exam_type),
            "district": _clean_optional(self.district),
            "corpus_stats": self.corpus_stats if isinstance(self.corpus_stats, dict) else {},
            "existing_tags": _normalize_tags(self.existing_tags),
        }


@dataclass(frozen=True)
class TagAnalysis:
    knowledge_points: list[str]
    method_tags: list[str]
    ability_tags: list[str]
    math_model_tags: list[str]
    difficulty: int
    typicality: int
    error_prone_points: list[str]
    prerequisite_points: list[str]
    textbook_chapter: str
    teaching_stage: str
    suitable_student_level: str
    reason: str

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TagAnalysis":
        return cls(
            knowledge_points=_normalize_tags(payload.get("knowledge_points")),
            method_tags=_normalize_tags(payload.get("method_tags")),
            ability_tags=_normalize_tags(payload.get("ability_tags")),
            math_model_tags=_normalize_tags(payload.get("math_model_tags")),
            difficulty=_normalize_score(payload.get("difficulty")),
            typicality=_normalize_score(payload.get("typicality")),
            error_prone_points=_normalize_error_tags(payload.get("error_prone_points")),
            prerequisite_points=_normalize_tags(payload.get("prerequisite_points")),
            textbook_chapter=_clean_text(payload.get("textbook_chapter")),
            teaching_stage=_clean_text(payload.get("teaching_stage")),
            suitable_student_level=_normalize_student_level(payload.get("suitable_student_level")),
            reason=_clean_text(payload.get("reason")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "knowledge_points": self.knowledge_points,
            "method_tags": self.method_tags,
            "ability_tags": self.ability_tags,
            "math_model_tags": self.math_model_tags,
            "difficulty": self.difficulty,
            "typicality": self.typicality,
            "error_prone_points": self.error_prone_points,
            "prerequisite_points": self.prerequisite_points,
            "textbook_chapter": self.textbook_chapter,
            "teaching_stage": self.teaching_stage,
            "suitable_student_level": self.suitable_student_level,
            "reason": self.reason,
        }


def _normalize_tags(value: object) -> list[str]:
    values = value if isinstance(value, (list, tuple, set)) else []
    tags: list[str] = []
    seen: set[str] = set()
    for item in values:
        tag = _clean_text(item)
        if not tag or len(tag) > MAX_TAG_LENGTH or tag in seen:
            continue
        tags.append(tag)
        seen.add(tag)
    return tags


def _normalize_error_tags(value: object) -> list[str]:
    values = value if isinstance(value, (list, tuple, set)) else []
    tags: list[str] = []
    seen: set[str] = set()
    for item in values:
        tag = _map_error_tag(_clean_text(item))
        if not tag or tag in seen:
            continue
        tags.append(tag)
        seen.add(tag)
    return tags


def _map_error_tag(text: str) -> str:
    if not text:
        return ""
    canonical = canonicalize_error_type(text)
    if canonical and canonical != text:
        return canonical
    if text in ERROR_PRONE_CATEGORIES:
        return text
    keyword_categories = (
        ("图形关系识别错误", ("读图", "图像", "图形", "角关系", "对应角", "内错角", "同位角", "折叠前后", "位置关系")),
        ("条件识别不完整", ("条件", "对应", "漏找", "漏用", "已知", "无法推导", "未识别")),
        ("概念理解不清", ("概念", "定义", "本质", "混淆")),
        ("公式/定理误用", ("公式", "定理", "性质", "判定", "误用")),
        ("运算化简错误", ("运算", "计算", "化简", "代换", "求值")),
        ("辅助线思路缺失", ("辅助线", "构造")),
        ("分类讨论遗漏", ("分类", "讨论", "遗漏")),
        ("数形转化困难", ("数形", "转化", "坐标")),
        ("题意阅读偏差", ("题意", "阅读", "审题")),
        ("书写依据不完整", ("依据", "书写", "证明", "逻辑")),
        ("单位/符号错误", ("单位", "符号")),
        ("综合建模困难", ("建模", "模型", "综合")),
    )
    for category, keywords in keyword_categories:
        if any(keyword in text for keyword in keywords):
            return category
    return text if len(text) <= MAX_TAG_LENGTH else ""


def _normalize_score(value: object) -> int:
    try:
        score = int(value)
    except (TypeError, ValueError):
        score = 1
    return min(10, max(1, score))


def _normalize_student_level(value: object) -> str:
    text = _clean_text(value)
    if text in STUDENT_LEVELS:
        return text
    aliases = {
        "入门": "入门补缺",
        "基础": "基础巩固",
        "中等": "中档提升",
        "提高": "综合突破",
        "拔高": "压轴拔高",
        "压轴": "压轴拔高",
    }
    for token, normalized in aliases.items():
        if token in text:
            return normalized
    return text or "中档提升"


def _clean_optional(value: object) -> str | None:
    cleaned = _clean_text(value)
    return cleaned or None


def _clean_text(value: object) -> str:
    return str(value or "").strip()
