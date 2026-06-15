from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


DEFAULT_WEIGHTS = {
    "concept": 0.40,
    "frequency": 0.35,
    "gradient": 0.10,
    "diversity": 0.15,
}

LEVEL_DIFFICULTY_RANGES = {
    "基础": (1, 3),
    "基础到中档": (3, 5),
    "中档到提升": (5, 7),
    "压轴突破": (8, 10),
    "入门补缺": (1, 3),
    "基础巩固": (3, 5),
    "中档提升": (5, 7),
    "综合突破": (7, 9),
    "压轴拔高": (8, 10),
}


@dataclass(frozen=True, slots=True)
class CandidateScore:
    eligible: bool
    total_score: float
    components: dict[str, float]
    weights: dict[str, float]
    warnings: tuple[str, ...] = ()


def score_candidate(
    *,
    concept_match: object,
    mapping_status: object,
    frequency_fit: object | None,
    gradient_fit: object | None,
    diversity_fit: object | None,
    weights: Mapping[str, object] | None = None,
    sub_skill_boost: float | None = None,
) -> CandidateScore:
    resolved_weights = _validated_weights(weights)
    components = {
        "concept": _rate(concept_match, default=0.0),
        "frequency": _rate(frequency_fit, default=0.5),
        "gradient": _rate(gradient_fit, default=0.5),
        "diversity": _rate(diversity_fit, default=0.5),
    }
    warnings: list[str] = []
    if frequency_fit is None:
        warnings.append("候选题缺少考频数据")
    if gradient_fit is None:
        warnings.append("候选题缺少难度标签")
    if diversity_fit is None:
        warnings.append("候选题缺少方法、模型或来源信息")

    eligible = _text(mapping_status).casefold() == "confirmed" and components["concept"] > 0
    if not eligible:
        warnings.append("知识点映射未确认或候选题与目标概念不匹配")
        return CandidateScore(
            eligible=False,
            total_score=0.0,
            components=components,
            weights=resolved_weights,
            warnings=tuple(warnings),
        )
    total = sum(components[key] * resolved_weights[key] for key in DEFAULT_WEIGHTS)
    if sub_skill_boost is not None and sub_skill_boost > 0.0:
        total *= (1.0 + 0.3 * sub_skill_boost)
    return CandidateScore(
        eligible=True,
        total_score=round(total, 4),
        components=components,
        weights=resolved_weights,
        warnings=tuple(warnings),
    )


def frequency_fit_score(metrics: object | None) -> float:
    if metrics is None or not bool(_metric_value(metrics, "available", False)):
        return 0.5
    if bool(_metric_value(metrics, "shenzhen_fit_available", False)):
        score = (
            _rate(_metric_value(metrics, "shenzhen_fit_score"), default=0.0) * 0.55
            + _rate(_metric_value(metrics, "shenzhen_questions_per_paper"), default=0.0) * 0.25
            + _rate(_metric_value(metrics, "national_questions_per_paper"), default=0.0) * 0.20
        )
        return round(score, 4)
    category_rate = _rate(_metric_value(metrics, "questions_per_paper"), default=0.0)
    skill_rate = _rate(_metric_value(metrics, "skill_frequency"), default=0.0)
    skill_available = bool(_metric_value(metrics, "skill_available", False))
    if skill_available and skill_rate > 0:
        return round(category_rate * 0.6 + min(1.0, skill_rate) * 0.4, 4)
    return category_rate


def normalize_score_1_to_5(value: object, default: float = 0.5) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return default
    if not 1 <= score <= 5:
        return default
    return round(score / 5, 4)


def preferred_difficulty_range(weak_point: Mapping[str, Any]) -> tuple[int, int]:
    recommended_level = _text(weak_point.get("recommended_level"))
    if recommended_level in LEVEL_DIFFICULTY_RANGES:
        return LEVEL_DIFFICULTY_RANGES[recommended_level]

    mastery = _rate(weak_point.get("mastery"), default=0.0)
    if mastery < 0.4:
        return (1, 4)
    if mastery < 0.7:
        return (4, 7)
    return (6, 9)


def difficulty_match_score(difficulty: object, weak_point: Mapping[str, Any]) -> float:
    normalized_difficulty = parse_difficulty(difficulty)
    if normalized_difficulty is None:
        return 0.4

    lower, upper = preferred_difficulty_range(weak_point)
    if lower <= normalized_difficulty <= upper:
        return 1.0
    if normalized_difficulty in (lower - 1, upper + 1):
        return 0.6
    return 0.2


def tag_match_score(weak_point: Mapping[str, Any], tags: Mapping[str, list[str]]) -> float:
    weak_canonical = _normalized_values([weak_point.get("canonical_knowledge_id")])
    question_canonical = _normalized_values(tags.get("canonical_knowledge_id", []))
    canonical_overlap = weak_canonical.intersection(question_canonical)
    weak_knowledge = _normalized_values([weak_point.get("knowledge_point")])
    question_knowledge = _normalized_values(tags.get("knowledge_point", []))
    overlap = weak_knowledge.intersection(question_knowledge)
    score = 0.65 if canonical_overlap else (0.6 if overlap else 0.0)

    weak_errors = _normalized_values(weak_point.get("error_types", []))
    question_errors = _normalized_values(tags.get("error_type", []))
    if weak_errors.intersection(question_errors):
        score += 0.35
    if len(overlap) > 1:
        score += 0.05
    return round(min(score, 1.0), 4)


def recommendation_score(
    *,
    mastery: float,
    frequency_rate: object | None,
    tag_score: float,
    difficulty_score: float,
    shenzhen_fit_score: object | None = None,
    shenzhen_frequency_rate: object | None = None,
    national_frequency_rate: object | None = None,
    skill_frequency_rate: object | None = None,
) -> float:
    if shenzhen_fit_score is not None:
        total = (
            _rate(shenzhen_fit_score, default=0.0) * 0.40
            + _rate(tag_score, default=0.0) * 0.25
            + _rate(difficulty_score, default=0.0) * 0.15
            + _rate(shenzhen_frequency_rate, default=0.0) * 0.10
            + _rate(national_frequency_rate, default=0.0) * 0.10
        )
        return round(total, 4)
    mastery_rate = _rate(mastery, default=0.0)
    base_total = (
        (1 - mastery_rate) * 0.35
        + _rate(tag_score, default=0.0) * 0.30
        + _rate(difficulty_score, default=0.0) * 0.20
    )
    if frequency_rate is None and skill_frequency_rate is None:
        return round(base_total / 0.85, 4)
    freq_r = _rate(frequency_rate, default=0.0)
    skill_r = _rate(skill_frequency_rate, default=0.0)
    if skill_r > 0:
        # 两项考频都有数据时，混合评分：类别 0.6 + 技能 0.4
        total = base_total + freq_r * 0.09 + skill_r * 0.06
    else:
        # 只有类别考频时，保持原始权重
        total = base_total + freq_r * 0.15
    return round(total, 4)


def parse_difficulty(value: object) -> int | None:
    text_value = _text(value).casefold()
    legacy_values = {"easy": 2, "medium": 3, "hard": 4}
    if text_value in legacy_values:
        return legacy_values[text_value]
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return None
    return number if 1 <= number <= 10 else None


def _normalized_values(values: object) -> set[str]:
    if values in (None, ""):
        return set()
    if isinstance(values, str):
        raw_values = [values]
    else:
        try:
            raw_values = list(values)
        except TypeError:
            raw_values = [values]
    return {_text(value).casefold() for value in raw_values if _text(value)}


def _rate(value: object, *, default: float) -> float:
    try:
        rate = float(value)
    except (TypeError, ValueError):
        return default
    if rate > 1:
        rate /= 100
    return min(max(rate, 0.0), 1.0)


def _text(value: object) -> str:
    return str(value or "").strip()


def _validated_weights(weights: Mapping[str, object] | None) -> dict[str, float]:
    source = dict(DEFAULT_WEIGHTS if weights is None else weights)
    if set(source) != set(DEFAULT_WEIGHTS):
        raise ValueError(f"weights must contain exactly: {', '.join(DEFAULT_WEIGHTS)}")
    try:
        resolved = {key: float(source[key]) for key in DEFAULT_WEIGHTS}
    except (TypeError, ValueError) as exc:
        raise ValueError("weights must be numeric") from exc
    if any(value < 0 or value > 1 for value in resolved.values()):
        raise ValueError("weights must be between 0 and 1")
    if abs(sum(resolved.values()) - 1.0) > 1e-9:
        raise ValueError("weights must sum to 1")
    return resolved


def _metric_value(metrics: object, key: str, default: object = 0.0) -> object:
    if isinstance(metrics, Mapping):
        return metrics.get(key, default)
    return getattr(metrics, key, default)


__all__ = [
    "CandidateScore",
    "DEFAULT_WEIGHTS",
    "difficulty_match_score",
    "frequency_fit_score",
    "normalize_score_1_to_5",
    "parse_difficulty",
    "preferred_difficulty_range",
    "recommendation_score",
    "score_candidate",
    "tag_match_score",
]
