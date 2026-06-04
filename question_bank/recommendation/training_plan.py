from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from question_bank.database.paths import question_bank_db_path
from question_bank.recommendation.recommendation_engine import recommend_for_weak_point


CLASS_LAYERS = (
    ("基础回补组", 0.0, 0.4),
    ("巩固提升组", 0.4, 0.7),
    ("低优先级组", 0.7, 1.01),
)


def generate_student_training_plan(
    student_mastery: Mapping[str, Any] | Any,
    filters: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    profile = _as_mapping(student_mastery)
    weak_points = [_as_mapping(item) for item in profile.get("weak_points", [])]
    group_plan = generate_group_training_plan(weak_points, filters)
    return {
        "student_id": _text(profile.get("student_id")),
        "student_name": _text(profile.get("student_name")),
        "class_id": _text(profile.get("class_id")),
        "exam_id": _text(profile.get("exam_id")),
        "weak_points": weak_points,
        "recommendations": group_plan["recommendations"],
    }


def generate_group_training_plan(
    group_weak_points: Iterable[Mapping[str, Any] | Any],
    filters: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    settings = dict(filters or {})
    db_path = Path(settings.get("db_path") or question_bank_db_path())
    limit = int(settings.get("per_weak_point_limit") or 5)
    weak_points = _aggregate_weak_points(_as_mapping(item) for item in group_weak_points)
    recommendations: list[dict[str, Any]] = []
    seen_question_ids: set[int] = set()
    paper_counts: defaultdict[str, int] = defaultdict(int)

    for weak_point in weak_points:
        for recommendation in recommend_for_weak_point(db_path, weak_point, limit=limit):
            question_id = int(recommendation["question_id"])
            paper_key = _text(recommendation.get("source_paper")) or f"question-{question_id}"
            if question_id in seen_question_ids or paper_counts[paper_key] >= 2:
                continue
            seen_question_ids.add(question_id)
            paper_counts[paper_key] += 1
            recommendations.append(recommendation)

    recommendations.sort(key=lambda item: (item["suggested_order"], -item["recommend_score"], item["question_id"]))
    for order, recommendation in enumerate(recommendations, start=1):
        recommendation["suggested_order"] = order
    return {
        "group_weak_points": weak_points,
        "recommendations": recommendations,
    }


def generate_class_training_plan(
    class_mastery: Iterable[Mapping[str, Any] | Any] | Mapping[str, Any],
    filters: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    profiles = _profiles_from_class_mastery(class_mastery)
    layers: list[dict[str, Any]] = []
    for layer_name, lower, upper in CLASS_LAYERS:
        layer_weak_points: list[dict[str, Any]] = []
        student_ids: set[str] = set()
        for profile in profiles:
            profile_student_id = _text(profile.get("student_id"))
            for weak_point in profile.get("weak_points", []):
                item = _as_mapping(weak_point)
                mastery = _rate(item.get("mastery"), default=0.0)
                if lower <= mastery < upper:
                    layer_weak_points.append(item)
                    if profile_student_id:
                        student_ids.add(profile_student_id)
        group_plan = generate_group_training_plan(layer_weak_points, filters)
        layers.append(
            {
                "layer_name": layer_name,
                "covered_student_count": len(student_ids),
                "group_weak_points": group_plan["group_weak_points"],
                "recommendations": group_plan["recommendations"],
            }
        )
    return {
        "layers": layers,
    }


def _aggregate_weak_points(items: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    masteries: defaultdict[str, list[float]] = defaultdict(list)
    for item in items:
        knowledge_point = _text(item.get("knowledge_point"))
        if not knowledge_point:
            continue
        normalized_key = knowledge_point.casefold()
        result = grouped.setdefault(
            normalized_key,
            {
                "knowledge_point": knowledge_point,
                "mastery": 0.0,
                "stability": item.get("stability"),
                "error_types": [],
                "related_question_ids": [],
                "recommended_level": _text(item.get("recommended_level")),
                "priority": _priority(item.get("priority")),
            },
        )
        masteries[normalized_key].append(_rate(item.get("mastery"), default=0.0))
        result["priority"] = max(result["priority"], _priority(item.get("priority")))
        if not result["recommended_level"]:
            result["recommended_level"] = _text(item.get("recommended_level"))
        _extend_unique(result["error_types"], _values(item.get("error_types")))
        _extend_unique(result["related_question_ids"], _values(item.get("related_question_ids")))

    for key, result in grouped.items():
        values = masteries[key]
        result["mastery"] = round(sum(values) / len(values), 4) if values else 0.0
    return sorted(grouped.values(), key=lambda item: (-item["priority"], item["mastery"], item["knowledge_point"]))


def _profiles_from_class_mastery(
    class_mastery: Iterable[Mapping[str, Any] | Any] | Mapping[str, Any],
) -> list[dict[str, Any]]:
    if isinstance(class_mastery, Mapping):
        raw_profiles = class_mastery.get("students") or class_mastery.get("profiles") or []
    else:
        raw_profiles = class_mastery
    return [_as_mapping(profile) for profile in raw_profiles]


def _as_mapping(value: Mapping[str, Any] | Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "to_dict"):
        converted = value.to_dict()
        if isinstance(converted, Mapping):
            return dict(converted)
    return {}


def _extend_unique(target: list[str], values: Iterable[str]) -> None:
    for value in values:
        if value and value not in target:
            target.append(value)


def _values(value: object) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        return [_text(value)] if _text(value) else []
    try:
        return [_text(item) for item in value if _text(item)]
    except TypeError:
        return [_text(value)] if _text(value) else []


def _priority(value: object) -> int:
    try:
        priority = int(value)
    except (TypeError, ValueError):
        return 1
    return min(max(priority, 1), 5)


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
