from __future__ import annotations

import ast
import json
from dataclasses import dataclass, field
from typing import Any

from question_bank.taxonomy.registry import canonicalize_error_type


LIST_FIELDS = (
    "knowledge_points",
    "method_tags",
    "ability_tags",
    "math_model_tags",
    "special_type_tags",
    "error_prone_points",
    "prerequisite_points",
)
MAX_TAG_LENGTH = 36
PROPOSABLE_TAG_DIMENSIONS = (
    "curriculum",
    "knowledge",
    "ability",
    "method",
    "model",
    "special_type",
)
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

# 子技能提炼维度（半受控引导）：AI 在产出 sub_skills 时按这些维度提炼，
# 不强制封闭词表，但禁止复读知识点原词。用于贯穿打标侧与薄弱点侧，让推荐 boost 真正生效。
SUB_SKILL_DIMENSIONS = (
    "判定法",      # SAS判定 / SSS判定 / AAS判定 / HL判定 / 平行线判定
    "作法构造",    # 尺规作图 / 辅助线构造 / 角平分线作法 / 中点作法
    "计算类型",    # 面积计算 / 角度计算 / 和差计算 / 求值 / 化简
    "数学模型",    # 手拉手模型 / 半角模型 / 将军饮马模型 / 一线三等角
    "性质应用",    # 等边对等角 / 三线合一 / 垂径定理应用 / 切线性质
)
SUB_SKILL_KEYWORD_HINTS = (
    "SAS判定", "SSS判定", "AAS判定", "ASA判定", "HL判定",
    "尺规作图", "辅助线构造", "角平分线作法", "中点作法",
    "面积计算", "角度计算", "和差计算", "求值", "化简",
    "手拉手模型", "半角模型", "将军饮马模型", "一线三等角", "旋转模型", "折叠模型",
    "等边对等角", "三线合一", "垂径定理", "切线性质",
)


@dataclass(frozen=True)
class TaggingContext:
    question_text: str
    answer_text: str | None = None
    question_number: str | None = None
    question_type: str | None = None
    grade: str | None = None
    semester: str | None = None
    textbook_version: str | None = None
    curriculum_volume_id: str | None = None
    exam_type: str | None = None
    district: str | None = None
    has_images: bool = False
    corpus_stats: dict[str, Any] = field(default_factory=dict)
    existing_tags: list[str] = field(default_factory=list)
    existing_tags_by_dimension: dict[str, list[str]] = field(default_factory=dict)

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
            "textbook_version": _clean_optional(self.textbook_version),
            "curriculum_volume_id": _clean_optional(self.curriculum_volume_id),
            "exam_type": _clean_optional(self.exam_type),
            "district": _clean_optional(self.district),
            "has_images": bool(self.has_images),
            "corpus_stats": self.corpus_stats if isinstance(self.corpus_stats, dict) else {},
            "existing_tags": _normalize_tags(self.existing_tags),
            "existing_tags_by_dimension": {
                str(dimension): _normalize_tags(values)
                for dimension, values in self.existing_tags_by_dimension.items()
                if str(dimension).strip()
            }
            if isinstance(self.existing_tags_by_dimension, dict)
            else {},
        }


@dataclass(frozen=True)
class TagAnalysis:
    knowledge_points: list[str]
    method_tags: list[str]
    ability_tags: list[str]
    math_model_tags: list[str]
    difficulty: int | None
    error_prone_points: list[str]
    prerequisite_points: list[str]
    textbook_chapter: str
    teaching_stage: str
    suitable_student_level: str
    reason: str
    confidence: float = 0.8
    taxonomy_revision: int = 0
    proposed_tags: list[dict[str, str]] = field(default_factory=list)
    special_type_tags: list[str] = field(default_factory=list)
    textbook_chapters: list[str] = field(default_factory=list)
    curriculum_sections: list[str] = field(default_factory=list)
    # 主知识点稳定编码（受控，须来自 registry 的 KP_* 候选表）。
    canonical_knowledge_id: str = ""
    # 仅用于读取历史数据。P3.5 起 AI 不再生成或保存这些自由词字段。
    sub_skills: list[str] = field(default_factory=list)
    measured_skills: list[str] = field(default_factory=list)
    supporting_skills: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TagAnalysis":
        textbook_chapters = _normalize_tags(payload.get("textbook_chapters"))
        if not textbook_chapters:
            textbook_chapters = _normalize_tags(payload.get("textbook_chapter"))
        return cls(
            knowledge_points=_normalize_tags(payload.get("knowledge_points")),
            method_tags=_normalize_tags(payload.get("method_tags")),
            ability_tags=_normalize_tags(payload.get("ability_tags")),
            math_model_tags=_normalize_tags(payload.get("math_model_tags")),
            difficulty=_normalize_score(payload.get("difficulty")),
            error_prone_points=_normalize_error_tags(payload.get("error_prone_points")),
            prerequisite_points=_normalize_tags(payload.get("prerequisite_points")),
            textbook_chapter=textbook_chapters[0] if textbook_chapters else "",
            teaching_stage=_normalize_text_value(payload.get("teaching_stage")),
            suitable_student_level=_normalize_student_level(payload.get("suitable_student_level")),
            reason=_normalize_text_value(payload.get("reason")),
            confidence=_normalize_confidence(payload.get("confidence")),
            taxonomy_revision=_normalize_revision(payload.get("taxonomy_revision")),
            proposed_tags=_normalize_proposed_tags(payload.get("proposed_tags")),
            special_type_tags=_normalize_tags(payload.get("special_type_tags")),
            textbook_chapters=textbook_chapters,
            curriculum_sections=_normalize_tags(payload.get("curriculum_sections")),
            canonical_knowledge_id=_normalize_canonical_id(payload.get("canonical_knowledge_id")),
            sub_skills=_normalize_tags(payload.get("sub_skills")),
            measured_skills=_normalize_tags(payload.get("measured_skills")),
            supporting_skills=_normalize_tags(payload.get("supporting_skills")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "knowledge_points": self.knowledge_points,
            "method_tags": self.method_tags,
            "ability_tags": self.ability_tags,
            "math_model_tags": self.math_model_tags,
            "difficulty": self.difficulty,
            "error_prone_points": self.error_prone_points,
            "prerequisite_points": self.prerequisite_points,
            "textbook_chapter": self.textbook_chapter,
            "textbook_chapters": self.textbook_chapters,
            "curriculum_sections": self.curriculum_sections,
            "teaching_stage": self.teaching_stage,
            "suitable_student_level": self.suitable_student_level,
            "reason": self.reason,
            "confidence": self.confidence,
            "taxonomy_revision": self.taxonomy_revision,
            "proposed_tags": self.proposed_tags,
            "special_type_tags": self.special_type_tags,
            "canonical_knowledge_id": self.canonical_knowledge_id,
            "sub_skills": self.sub_skills,
            "measured_skills": self.measured_skills,
            "supporting_skills": self.supporting_skills,
        }


def _normalize_tags(value: object) -> list[str]:
    values = _coerce_sequence(value)
    tags: list[str] = []
    seen: set[str] = set()
    for item in values:
        tag = _clean_text(item)
        if not tag or len(tag) > MAX_TAG_LENGTH or tag in seen:
            continue
        tags.append(tag)
        seen.add(tag)
    return tags


def _normalize_canonical_id(value: object) -> str:
    """对 AI 产出的 canonical_knowledge_id 做基本清洗与大小写归一。

    严格校验（是否在 registry 中）由 service 层负责；此处只做格式规范，
    统一转为小写 kp_* 形式，避免大小写不一致导致的隐性依赖。
    """
    text = _clean_text(value)
    if not text:
        return ""
    lower = text.casefold().strip()
    if lower.startswith("kp_"):
        return lower
    # 兼容 AI 偶尔返回纯编码前缀（如 geo_line_angle）的情况
    return lower


def _normalize_error_tags(value: object) -> list[str]:
    values = _coerce_sequence(value)
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


def _normalize_score(value: object) -> int | None:
    try:
        score = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return score if 1 <= score <= 10 else None


def _normalize_confidence(value: object) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        confidence = 0.8
    return round(min(1.0, max(0.0, confidence)), 4)


def _normalize_revision(value: object) -> int:
    try:
        revision = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, revision)


def _normalize_proposed_tags(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        dimension = _clean_text(item.get("dimension")).casefold()
        name = _clean_text(item.get("name") or item.get("proposed_name"))
        if dimension not in PROPOSABLE_TAG_DIMENSIONS:
            continue
        if not name or len(name) > MAX_TAG_LENGTH:
            continue
        key = (dimension, name.casefold())
        if key in seen:
            continue
        seen.add(key)
        normalized.append(
            {
                "dimension": dimension,
                "name": name,
                "definition": _clean_text(item.get("definition"))[:240],
                "reason": _clean_text(item.get("reason"))[:500],
                "nearest_id": _clean_text(item.get("nearest_id"))[:80],
                "why_not_reuse": _clean_text(item.get("why_not_reuse"))[:500],
            }
        )
        if len(normalized) >= 2:
            break
    return normalized


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
    return ""


def _clean_optional(value: object) -> str | None:
    cleaned = _clean_text(value)
    return cleaned or None


def _clean_text(value: object) -> str:
    return str(value or "").strip()


def _normalize_text_value(value: object) -> str:
    if isinstance(value, (list, tuple, set)):
        for item in value:
            cleaned = _clean_text(item)
            if cleaned:
                return cleaned
        return ""
    parsed = _parse_string_sequence(value)
    if parsed is not None:
        for item in parsed:
            cleaned = _clean_text(item)
            if cleaned:
                return cleaned
        return ""
    return _clean_text(value)


def _coerce_sequence(value: object) -> list[object]:
    if isinstance(value, (list, tuple, set)):
        return list(value)
    parsed = _parse_string_sequence(value)
    if parsed is not None:
        return parsed
    cleaned = _clean_text(value)
    return [cleaned] if cleaned else []


def _parse_string_sequence(value: object) -> list[object] | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not (text.startswith("[") and text.endswith("]")):
        return None
    for parser in (json.loads, ast.literal_eval):
        try:
            parsed = parser(text)
        except (ValueError, SyntaxError, TypeError, json.JSONDecodeError):
            continue
        if isinstance(parsed, (list, tuple, set)):
            return list(parsed)
    return None
