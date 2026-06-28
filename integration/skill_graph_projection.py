from __future__ import annotations

from typing import Any, Iterable, Mapping


def build_skill_graph_rows(profile: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    students = profile.get("students")
    if not isinstance(students, list):
        return rows
    for student in students:
        if not isinstance(student, Mapping):
            continue
        student_id = _positive_int(student.get("student_id"))
        if student_id is None:
            continue
        weak_points = student.get("weak_points")
        if not isinstance(weak_points, list):
            continue
        for weak_point in weak_points:
            if not isinstance(weak_point, Mapping):
                continue
            skill_id = _positive_int(weak_point.get("skill_id"))
            if skill_id is None:
                continue
            mastery = _number(weak_point.get("mastery"))
            reasons = weak_point.get("actionable_reasons")
            reason_values = reasons if isinstance(reasons, list) else []
            source_refs = weak_point.get("source_question_refs")
            rows.append(
                {
                    "student_id": student_id,
                    "student_code": str(student.get("student_code") or ""),
                    "student_name": str(student.get("student_name") or ""),
                    "skill_id": skill_id,
                    "knowledge_id": f"skill:{skill_id}",
                    "knowledge_label": str(weak_point.get("skill_name") or ""),
                    "topic_name": str(weak_point.get("topic_name") or ""),
                    "weighted_score_rate": round(mastery * 100, 2),
                    "deduction_count": _integer(weak_point.get("deduction_count")),
                    "item_count": _integer(weak_point.get("evidence_count")),
                    "sample_reasons": "；".join(
                        _unique_text(str(value or "").strip() for value in reason_values)
                    ),
                    "source_question_refs": [
                        dict(value)
                        for value in source_refs
                        if isinstance(value, Mapping)
                    ]
                    if isinstance(source_refs, list)
                    else [],
                }
            )
    return rows


def build_question_tag_graph_rows(profile: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    students = profile.get("students")
    if not isinstance(students, list):
        return rows
    for student in students:
        if not isinstance(student, Mapping):
            continue
        student_id = _positive_int(student.get("student_id"))
        if student_id is None:
            continue
        weak_points = student.get("weak_points")
        if not isinstance(weak_points, list):
            continue
        for weak_point in weak_points:
            if not isinstance(weak_point, Mapping):
                continue
            knowledge_point = str(weak_point.get("knowledge_point") or "").strip()
            if not knowledge_point:
                continue
            knowledge_key = str(
                weak_point.get("knowledge_key") or f"knowledge_point:{knowledge_point}"
            ).strip()
            mastery = _number(weak_point.get("mastery"))
            reasons = weak_point.get("actionable_reasons")
            reason_values = reasons if isinstance(reasons, list) else []
            source_refs = weak_point.get("source_question_refs")
            tag_context = weak_point.get("tag_context")
            error_counts = weak_point.get("error_counts")
            rows.append(
                {
                    "student_id": student_id,
                    "student_code": str(student.get("student_code") or ""),
                    "student_name": str(student.get("student_name") or ""),
                    "knowledge_key": knowledge_key,
                    "knowledge_label": knowledge_point,
                    "weighted_score_rate": round(mastery * 100, 2),
                    "deduction_count": _integer(weak_point.get("deduction_count")),
                    "item_count": _integer(weak_point.get("evidence_count")),
                    "sample_reasons": "；".join(
                        _unique_text(str(value or "").strip() for value in reason_values)
                    ),
                    "source_question_refs": [
                        dict(value)
                        for value in source_refs
                        if isinstance(value, Mapping)
                    ]
                    if isinstance(source_refs, list)
                    else [],
                    "tag_context": dict(tag_context)
                    if isinstance(tag_context, Mapping)
                    else {},
                    "error_counts": dict(error_counts)
                    if isinstance(error_counts, Mapping)
                    else {"primary": {}, "secondary": {}},
                }
            )
    return rows


def _positive_int(value: object) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _integer(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _number(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _unique_text(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


__all__ = ["build_question_tag_graph_rows", "build_skill_graph_rows"]
