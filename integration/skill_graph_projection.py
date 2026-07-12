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
                        _public_source_reference(value)
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
    return sorted(
        rows,
        key=lambda row: (row["knowledge_key"], row["student_id"]),
    )


def build_question_tag_graph_nodes(
    rows: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        knowledge_key = str(row.get("knowledge_key") or "").strip()
        knowledge_label = str(row.get("knowledge_label") or "").strip()
        student_id = _positive_int(row.get("student_id"))
        if not knowledge_key or not knowledge_label or student_id is None:
            continue
        bucket = grouped.setdefault(
            knowledge_key,
            {
                "knowledge_label": knowledge_label,
                "student_ids": set(),
                "item_count": 0,
                "deduction_count": 0,
                "weighted_mastery_sum": 0.0,
                "weight_sum": 0,
                "tag_context": {},
                "error_counts": {"primary": {}, "secondary": {}},
            },
        )
        bucket["student_ids"].add(student_id)
        item_count = max(_integer(row.get("item_count")), 0)
        mastery_weight = max(item_count, 1)
        bucket["item_count"] += item_count
        bucket["deduction_count"] += max(
            _integer(row.get("deduction_count")),
            0,
        )
        bucket["weighted_mastery_sum"] += (
            max(min(_number(row.get("weighted_score_rate")) / 100.0, 1.0), 0.0)
            * mastery_weight
        )
        bucket["weight_sum"] += mastery_weight
        _merge_tag_context(bucket["tag_context"], row.get("tag_context"))
        _merge_error_counts(bucket["error_counts"], row.get("error_counts"))

    nodes: list[dict[str, Any]] = []
    for knowledge_key in sorted(grouped):
        bucket = grouped[knowledge_key]
        weight_sum = int(bucket["weight_sum"])
        nodes.append(
            {
                "knowledge_key": knowledge_key,
                "knowledge_label": bucket["knowledge_label"],
                "student_count": len(bucket["student_ids"]),
                "item_count": int(bucket["item_count"]),
                "deduction_count": int(bucket["deduction_count"]),
                "average_mastery": round(
                    float(bucket["weighted_mastery_sum"]) / weight_sum,
                    4,
                )
                if weight_sum
                else 0.0,
                "tag_context": bucket["tag_context"],
                "error_counts": bucket["error_counts"],
            }
        )
    return nodes


def build_question_tag_graph_evidence(
    profile: Mapping[str, Any],
    knowledge_key: str,
) -> list[dict[str, Any]]:
    target = str(knowledge_key or "").strip()
    items: list[dict[str, Any]] = []
    seen: set[tuple[int, int, str, int]] = set()
    students = profile.get("students")
    if not target or not isinstance(students, list):
        return items
    for student in students:
        if not isinstance(student, Mapping):
            continue
        student_id = _positive_int(student.get("student_id"))
        weak_points = student.get("weak_points")
        if student_id is None or not isinstance(weak_points, list):
            continue
        for weak_point in weak_points:
            if (
                not isinstance(weak_point, Mapping)
                or str(weak_point.get("knowledge_key") or "").strip() != target
            ):
                continue
            knowledge_label = str(
                weak_point.get("knowledge_point") or ""
            ).strip()
            references = weak_point.get("source_question_refs")
            if not isinstance(references, list):
                continue
            for reference in references:
                if not isinstance(reference, Mapping):
                    continue
                session_id = _integer(reference.get("session_id"))
                question_id = str(reference.get("question_id") or "")
                bank_question_id = _integer(reference.get("bank_question_id"))
                identity = (
                    student_id,
                    session_id,
                    question_id,
                    bank_question_id,
                )
                if identity in seen:
                    continue
                seen.add(identity)
                raw_context = weak_point.get("tag_context")
                raw_errors = weak_point.get("error_counts")
                items.append(
                    {
                        "student_id": student_id,
                        "student_code": str(student.get("student_code") or ""),
                        "student_name": str(student.get("student_name") or ""),
                        "class_id": str(student.get("class_id") or ""),
                        "knowledge_key": target,
                        "knowledge_label": knowledge_label,
                        "session_id": session_id,
                        "session_name": str(reference.get("session_name") or ""),
                        "question_id": question_id,
                        "bank_question_id": bank_question_id,
                        "score_awarded": _number(reference.get("score_awarded")),
                        "full_score": _number(reference.get("full_score")),
                        "score_rate": _optional_number(reference.get("score_rate")),
                        "tag_context": _normalized_tag_context(raw_context),
                        "actionable_reasons": _unique_text(
                            str(value or "").strip()
                            for value in weak_point.get("actionable_reasons", [])
                        )
                        if isinstance(weak_point.get("actionable_reasons"), list)
                        else [],
                        "error_counts": _normalized_error_counts(raw_errors),
                    }
                )
    return sorted(
        items,
        key=lambda item: (
            item["session_id"],
            item["student_id"],
            item["question_id"],
            item["bank_question_id"],
        ),
    )


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


def _optional_number(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _public_source_reference(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "session_id": _integer(value.get("session_id")),
        "session_name": str(value.get("session_name") or ""),
        "question_id": str(value.get("question_id") or ""),
        "bank_question_id": _integer(value.get("bank_question_id")),
        "score_awarded": _number(value.get("score_awarded")),
        "full_score": _number(value.get("full_score")),
        "score_rate": _optional_number(value.get("score_rate")),
    }


def _normalized_tag_context(value: object) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    if not isinstance(value, Mapping):
        return result
    for tag_type, raw_values in value.items():
        if not isinstance(raw_values, list):
            continue
        values = _unique_text(str(item or "").strip() for item in raw_values)
        if values:
            result[str(tag_type)] = values
    return result


def _normalized_error_counts(value: object) -> dict[str, dict[str, int]]:
    result = {"primary": {}, "secondary": {}}
    if not isinstance(value, Mapping):
        return result
    for category in result:
        raw_counts = value.get(category)
        if not isinstance(raw_counts, Mapping):
            continue
        result[category] = {
            str(label): max(_integer(count), 0)
            for label, count in raw_counts.items()
            if str(label).strip() and max(_integer(count), 0) > 0
        }
    return result


def _merge_tag_context(
    target: dict[str, list[str]],
    value: object,
) -> None:
    for tag_type, values in _normalized_tag_context(value).items():
        target[tag_type] = _unique_text([*target.get(tag_type, []), *values])


def _merge_error_counts(
    target: dict[str, dict[str, int]],
    value: object,
) -> None:
    for category, counts in _normalized_error_counts(value).items():
        target_counts = target.setdefault(category, {})
        for label, count in counts.items():
            target_counts[label] = target_counts.get(label, 0) + count


def _unique_text(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


__all__ = [
    "build_question_tag_graph_evidence",
    "build_question_tag_graph_nodes",
    "build_question_tag_graph_rows",
    "build_skill_graph_rows",
]
