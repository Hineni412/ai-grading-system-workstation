from __future__ import annotations

import ast
import json
from dataclasses import dataclass, field
from typing import Any


DIFFICULTY_SCALE_VERSION = "junior-full-range-2026-09-v2"
DIFFICULTY_SCALE_GUIDANCE = (
    "难度使用统一教学标尺 junior-full-range-2026-09-v2，1—10覆盖初中从识别概念到中考压轴的完整跨度，"
    "9—10应当用于真正的压轴难题，不因其少见而回避：1—2为识别概念、直接代入或单步基本运算；"
    "3为熟悉情境下独立完成基本关系与计算；4—5为常规应用、若干相连步骤或一次常见转化；"
    "6为偏难，需要不直接给出的关键条件、辅助构造或较强综合推理；"
    "7—8为综合挑战，需要连续关键转化、非常规构造或较复杂分类讨论；"
    "9—10为压轴最后一问一级，多重关键转化、完整分类讨论与含参或动态分析同时出现。"
    "锚点示例：已知两直角边直接求斜边通常2—3；熟悉的折断或高差模型列式求解通常3—5；"
    "需自行作辅助线并串联多个几何关系才求出未知量可评6；"
    "旋转、折叠、剪拼中多种情况与连续构造结合可评7—8；"
    "新定义或含参动点与多段分类讨论、最值比较结合的压轴小问可评9—10。例子仅作锚点，须以实际推理要求说明理由。"
    "不按考试分值、学生得分率、小问数量、题干长短或是否含根式直接定级；多小问题逐问估计，"
    "整题按最难小问检查，不取平均，也不把一问难度复制给其余问。"
)


LIST_FIELDS = (
    "method_tags",
    "thought_tags",
    "ability_tags",
    "math_model_tags",
    "special_type_tags",
)
MAX_TAG_LENGTH = 160
MAX_ABILITY_TAGS = 2
PREDICTED_PATTERN_MAX = 6  # 选择题最多 A–F 六个选项；非选择题仍限 3 条。
PREDICTED_GENERAL_MAX = 3
PREDICTED_PATTERN_NAME_MAX = 80
PREDICTED_PATTERN_TRIGGER_VALUE_MAX = 160
PREDICTED_TRIGGER_KINDS = ("option", "wrong_answer", "step", "observation")
PART_FEATURES_MAX_PARTS = 8
PART_FEATURE_EVIDENCE_MAX = 240
PART_FEATURE_PART_LABEL_MAX = 24
# 情境类别（方案 4 节 context 数值之外另存的筛选维度）。
PART_CONTEXT_KINDS = ("无情境", "生活情境", "科学跨学科", "数学文化", "新定义")
PROPOSABLE_TAG_DIMENSIONS = (
    "curriculum",
    "knowledge",
    "ability",
    "method",
    "thought",
    "model",
    "special_type",
)

# 标准难度逐小问特征取值范围（公式见 question_bank/services/standard_difficulty.py）。
PART_FEATURE_RANGES: dict[str, tuple[int, int]] = {
    "solo": (1, 4),
    "reasoning": (0, 2),
    "computation": (0, 2),
    "context": (0, 2),
    "hidden": (0, 2),
    "cases": (0, 2),
    "param_dynamic": (0, 1),
    "trap": (0, 1),
    "knowledge": (0, 2),
}
PART_FEATURE_ORDER = tuple(PART_FEATURE_RANGES)

# 兼容读取：历史 error_type 标签的固定类别集合，只用于题库筛选面等读取侧，
# 新打标不再生成 error_type 标签（改为 question_error_patterns 的预测错法）。
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
    textbook_version: str | None = None
    curriculum_volume_id: str | None = None
    exam_type: str | None = None
    district: str | None = None
    has_images: bool = False
    corpus_stats: dict[str, Any] = field(default_factory=dict)
    existing_tags: list[str] = field(default_factory=list)
    existing_tags_by_dimension: dict[str, list[str]] = field(default_factory=dict)
    # 当前判定点（解答依据）版本的小问与判定点标识；part_features.part_id 与
    # predicted_error_patterns 的 step 触发值必须从这里取。
    evidence_parts: list[dict[str, Any]] = field(default_factory=list)

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
            "evidence_parts": [
                {
                    "part_id": _clean_text(item.get("part_id")),
                    "part_label": _clean_text(item.get("part_label")),
                    "evidence_point_ids": [
                        _clean_text(point)
                        for point in _coerce_sequence(
                            item.get("evidence_point_ids")
                        )
                        if _clean_text(point)
                    ],
                }
                for item in self.evidence_parts
                if isinstance(item, dict) and _clean_text(item.get("part_id"))
            ],
        }


@dataclass(frozen=True)
class TagAnalysis:
    """整题打标签结果（八上重打方案版）。

    知识点、前置知识、章与小节归属不再由本结构携带，全部由判定点关联
    （evidence_point_knowledge_links）派生；error_type、学生层次、
    教学阶段、canonical_knowledge_id 等旧维度同样不再生成。
    """

    method_tags: list[str]
    thought_tags: list[str]
    ability_tags: list[str]
    math_model_tags: list[str]
    difficulty: int | None
    reason: str
    confidence: float = 0.8
    taxonomy_revision: int = 0
    proposed_tags: list[dict[str, str]] = field(default_factory=list)
    special_type_tags: list[str] = field(default_factory=list)
    predicted_error_patterns: list[dict[str, str]] = field(default_factory=list)
    part_features: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TagAnalysis":
        if not isinstance(payload, dict):
            payload = {}
        return cls(
            method_tags=_normalize_tags(payload.get("method_tags")),
            thought_tags=_normalize_tags(payload.get("thought_tags")),
            ability_tags=_normalize_tags(payload.get("ability_tags"))[:MAX_ABILITY_TAGS],
            math_model_tags=_normalize_tags(payload.get("math_model_tags")),
            difficulty=_normalize_score(payload.get("difficulty")),
            reason=_normalize_text_value(payload.get("reason")),
            confidence=_normalize_confidence(payload.get("confidence")),
            taxonomy_revision=_normalize_revision(payload.get("taxonomy_revision")),
            proposed_tags=_normalize_proposed_tags(payload.get("proposed_tags")),
            special_type_tags=_normalize_tags(payload.get("special_type_tags")),
            predicted_error_patterns=_normalize_predicted_error_patterns(
                payload.get("predicted_error_patterns")
            ),
            part_features=_normalize_part_features(
                payload.get("part_features")
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "method_tags": self.method_tags,
            "thought_tags": self.thought_tags,
            "ability_tags": self.ability_tags,
            "math_model_tags": self.math_model_tags,
            "special_type_tags": self.special_type_tags,
            "difficulty": self.difficulty,
            "predicted_error_patterns": self.predicted_error_patterns,
            "part_features": self.part_features,
            "taxonomy_revision": self.taxonomy_revision,
            "proposed_tags": self.proposed_tags,
            "reason": self.reason,
            "confidence": self.confidence,
        }


def predicted_pattern_categories() -> tuple[str, ...]:
    """预测典型错法允许的大类（沿用错因体系 7 类；滞后导入避免环）。"""

    try:
        from backend.error_causes import CAUSE_CATEGORIES
    except ImportError:
        return ()
    return tuple(CAUSE_CATEGORIES)


def normalize_predicted_category(value: object) -> str:
    """预测错法大类归一到 7 类固定词表；无法归类返回空串。"""

    try:
        from backend.error_causes import normalize_cause_category
    except ImportError:
        return ""
    return str(normalize_cause_category(value) or "")


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


def _normalize_predicted_error_patterns(value: object) -> list[dict[str, str]]:
    if not isinstance(value, (list, tuple)):
        return []
    patterns: list[dict[str, str]] = []
    seen: set[str] = set()
    general_count = 0
    ordered = sorted(
        value,
        key=lambda item: 0 if isinstance(item, dict)
        and str(item.get("trigger_kind") or "").strip().casefold() == "option" else 1,
    )
    for item in ordered:
        if not isinstance(item, dict):
            continue
        category = normalize_predicted_category(item.get("category"))
        pattern = _clean_text(item.get("pattern") or item.get("name"))
        if not category or not pattern:
            continue
        if len(pattern) > PREDICTED_PATTERN_NAME_MAX:
            continue
        trigger_kind = _clean_text(item.get("trigger_kind")).casefold()
        if trigger_kind not in PREDICTED_TRIGGER_KINDS:
            trigger_kind = "observation"
        trigger_value = _clean_text(item.get("trigger_value"))[
            :PREDICTED_PATTERN_TRIGGER_VALUE_MAX
        ]
        if trigger_kind == "observation":
            trigger_value = ""
        if trigger_kind == "option":
            trigger_value = trigger_value.upper()
            if len(trigger_value) != 1 or trigger_value not in "ABCDEF":
                continue
        elif general_count >= PREDICTED_GENERAL_MAX:
            continue
        key = f"{trigger_kind}|{trigger_value.casefold()}|{pattern.casefold()}"
        if key in seen:
            continue
        seen.add(key)
        patterns.append(
            {
                "category": category,
                "pattern": pattern,
                "explanation": _clean_text(item.get("explanation"))[:160],
                "trigger_kind": trigger_kind,
                "trigger_value": trigger_value,
            }
        )
        if trigger_kind != "option":
            general_count += 1
        if len(patterns) >= PREDICTED_PATTERN_MAX:
            break
    return patterns


def _normalize_feature_int(value: object, field_name: str) -> int | None:
    minimum, maximum = PART_FEATURE_RANGES[field_name]
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return number if minimum <= number <= maximum else None


def _normalize_context_kind(value: object, context_score: int) -> str:
    """情境类别词校验：必须是 5 个固定值之一；context=0 时兜底无情境。"""

    kind = _clean_text(value)
    if kind in PART_CONTEXT_KINDS:
        return kind
    return "无情境" if context_score == 0 else ""


def _normalize_part_features(value: object) -> list[dict[str, Any]]:
    """逐小问特征归一；part_id 缺失或任一必填特征非法则丢弃该小问。"""

    if not isinstance(value, (list, tuple)):
        return []
    parts: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        part_id = _clean_text(item.get("part_id"))
        if not part_id:
            continue
        if part_id.casefold() in seen_ids:
            continue
        features = {
            name: _normalize_feature_int(item.get(name), name)
            for name in PART_FEATURE_ORDER
        }
        if any(features[name] is None for name in PART_FEATURE_ORDER):
            continue
        context_kind = _normalize_context_kind(
            item.get("context_kind"),
            int(features["context"]),
        )
        if not context_kind:
            continue
        seen_ids.add(part_id.casefold())
        parts.append(
            {
                "part_id": part_id,
                "part_label": _clean_text(item.get("part_label"))[
                    :PART_FEATURE_PART_LABEL_MAX
                ],
                **{name: int(features[name]) for name in PART_FEATURE_ORDER},
                "context_kind": context_kind,
                "evidence": _clean_text(item.get("evidence"))[
                    :PART_FEATURE_EVIDENCE_MAX
                ],
            }
        )
        if len(parts) >= PART_FEATURES_MAX_PARTS:
            break
    return parts


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
