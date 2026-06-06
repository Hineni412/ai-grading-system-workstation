from __future__ import annotations

from collections.abc import Mapping
from typing import Any


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
    if frequency_rate is None:
        return round(base_total / 0.85, 4)
    total = base_total + _rate(frequency_rate, default=0.0) * 0.15
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
