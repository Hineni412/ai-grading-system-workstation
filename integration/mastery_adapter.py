from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from integration.mastery_schema import StudentMasteryProfile, WeakPoint
from path_manager import resolve_stored_file_path
from question_bank.current_knowledge import CurrentKnowledgeResolver

ROW_CONTAINER_KEYS = ("weak_point_rows", "rows", "items")
ERROR_SPLIT_PATTERN = re.compile(r"[;；\n]+")


def read_mastery_json(path: str | Path) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows: object = payload
    if isinstance(payload, Mapping):
        for key in ROW_CONTAINER_KEYS:
            if isinstance(payload.get(key), list):
                rows = payload[key]
                break
    if not isinstance(rows, list):
        raise ValueError("Mastery JSON must be a row list or contain weak_point_rows.")
    return [dict(row) for row in rows if isinstance(row, Mapping)]


def read_mastery_sqlite(
    path: str | Path,
    *,
    exam_id: object | None = None,
    student_id: object | None = None,
) -> list[dict[str, Any]]:
    query = """
        SELECT
            sr.session_id,
            s.id AS student_id,
            s.student_code,
            s.name AS student_name,
            s.class_name,
            sd.question_id,
            sd.knowledge_ids,
            sd.score_awarded,
            sd.deduction_reason,
            sd.error_category,
            sd.error_summary
        FROM session_details sd
        JOIN session_results sr ON sr.id = sd.result_id
        JOIN students s ON s.id = sr.student_id
        WHERE 1 = 1
    """
    params: list[object] = []
    if exam_id not in (None, ""):
        query += " AND sr.session_id = ?"
        params.append(exam_id)
    if student_id not in (None, ""):
        query += " AND s.id = ?"
        params.append(student_id)
    query += " ORDER BY s.id ASC, sr.session_id ASC, sd.id ASC"

    db_uri = f"{Path(path).resolve().as_uri()}?mode=ro"
    with sqlite3.connect(db_uri, uri=True) as conn:
        conn.row_factory = sqlite3.Row
        detail_rows = [dict(row) for row in conn.execute(query, params).fetchall()]
        rubric_labels = _load_sqlite_rubric_labels(conn)
    return _aggregate_sqlite_detail_rows(detail_rows, rubric_labels=rubric_labels)


def adapt_mastery_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    current_knowledge: CurrentKnowledgeResolver,
    exam_id: object | None = None,
    student_id: object | None = None,
) -> list[StudentMasteryProfile]:
    profiles: dict[tuple[str, str, str], StudentMasteryProfile] = {}
    requested_student_id = _clean_string(student_id)

    for row in rows:
        normalized_student_id = _student_id(row)
        if requested_student_id and requested_student_id != normalized_student_id:
            continue

        normalized_class_id = _first_text(row, "class_id", "class_name")
        normalized_exam_id = _clean_string(exam_id) or _first_text(row, "exam_id", "session_id")
        profile_key = (normalized_student_id, normalized_class_id, normalized_exam_id)
        profile = profiles.setdefault(
            profile_key,
            StudentMasteryProfile(
                student_id=normalized_student_id,
                student_name=_first_text(row, "student_name", "name"),
                class_id=normalized_class_id,
                exam_id=normalized_exam_id,
            ),
        )
        weak_point = _weak_point_from_row(row, current_knowledge)
        if weak_point is not None:
            profile.weak_points.append(weak_point)

    return list(profiles.values())


def compute_priority(mastery: float) -> int:
    if mastery < 0.2:
        return 5
    if mastery < 0.4:
        return 4
    if mastery < 0.6:
        return 3
    if mastery < 0.8:
        return 2
    return 1


def _weak_point_from_row(
    row: Mapping[str, Any],
    current_knowledge: CurrentKnowledgeResolver,
) -> WeakPoint | None:
    mastery = _mastery_value(row)
    priority = _priority_value(row.get("priority"), mastery)
    raw_knowledge_ids = _knowledge_ids(row)
    raw_knowledge_point = _first_text(row, "knowledge_point", "knowledge_label", "knowledge_id")
    source_values = [raw_knowledge_point, *raw_knowledge_ids]
    canonical = next(
        (
            term
            for value in source_values
            if (term := current_knowledge.canonical_term(value)) is not None
        ),
        None,
    )
    if canonical is None or not current_knowledge.resolve_many(source_values):
        return None

    raw_error_types = _raw_error_types(row)
    normalized_errors = _normalize_error_types(raw_error_types)
    return WeakPoint(
        knowledge_point=canonical[1],
        canonical_knowledge_id=canonical[0],
        mastery=mastery,
        stability=_optional_rate(row.get("stability")),
        error_types=normalized_errors,
        related_question_ids=_related_question_ids(row),
        recommended_level=_clean_string(row.get("recommended_level")),
        priority=priority,
        raw_knowledge_ids=raw_knowledge_ids,
        raw_knowledge_point=raw_knowledge_point,
        raw_error_types=raw_error_types,
    )


def _aggregate_sqlite_detail_rows(
    detail_rows: Iterable[Mapping[str, Any]],
    *,
    rubric_labels: Mapping[tuple[str, str, str], str] | None = None,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    label_lookup = dict(rubric_labels or {})
    for row in detail_rows:
        for knowledge_id in _knowledge_ids(row):
            session_id = _first_text(row, "session_id", "exam_id")
            question_id = _first_text(row, "question_id")
            knowledge_label = label_lookup.get((session_id, question_id, knowledge_id), knowledge_id)
            group_key = (
                _student_id(row),
                session_id,
                knowledge_id,
            )
            item = grouped.setdefault(
                group_key,
                {
                    "student_id": row.get("student_id"),
                    "student_code": row.get("student_code"),
                    "student_name": row.get("student_name"),
                    "class_name": row.get("class_name"),
                    "session_id": row.get("session_id"),
                    "knowledge_id": knowledge_id,
                    "knowledge_label": knowledge_label,
                    "related_question_ids": [],
                    "error_types": [],
                    "sample_reasons": [],
                    "_mastery_total": 0.0,
                    "_item_count": 0,
                },
            )
            item["_mastery_total"] += _detail_mastery(row.get("score_awarded"))
            item["_item_count"] += 1
            _extend_unique(item["related_question_ids"], _to_string_list(row.get("question_id")))
            _extend_unique(item["error_types"], _to_string_list(row.get("error_category")))
            _extend_unique(
                item["sample_reasons"],
                _to_string_list(row.get("deduction_reason")) + _to_string_list(row.get("error_summary")),
            )

    result: list[dict[str, Any]] = []
    for item in grouped.values():
        item_count = max(1, int(item.pop("_item_count")))
        mastery_total = float(item.pop("_mastery_total"))
        sample_reasons = item["sample_reasons"]
        item["item_count"] = item_count
        item["weighted_score_rate"] = round(mastery_total / item_count * 100, 2)
        item["sample_reasons"] = "；".join(sample_reasons)
        result.append(item)
    return result


def _student_id(row: Mapping[str, Any]) -> str:
    return _first_text(row, "student_id", "student_code")


def _mastery_value(row: Mapping[str, Any]) -> float:
    if row.get("mastery") not in (None, ""):
        return _rate(row.get("mastery"), default=0.0)
    return _rate(row.get("weighted_score_rate"), default=0.0)


def _priority_value(value: object, mastery: float) -> int:
    try:
        priority = int(value)
    except (TypeError, ValueError):
        return compute_priority(mastery)
    return priority if 1 <= priority <= 5 else compute_priority(mastery)


def _raw_error_types(row: Mapping[str, Any]) -> list[str]:
    explicit = _to_string_list(row.get("error_types"))
    if explicit:
        return explicit
    categories = _to_string_list(row.get("error_category"))
    if categories:
        return categories
    return _to_string_list(row.get("sample_reasons"))


def _normalize_error_types(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        normalized = _clean_string(value)
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def _related_question_ids(row: Mapping[str, Any]) -> list[str]:
    explicit = _to_string_list(row.get("related_question_ids"))
    if explicit:
        return explicit
    return _to_string_list(row.get("question_id"))


def _knowledge_ids(row: Mapping[str, Any]) -> list[str]:
    raw_ids = row.get("knowledge_ids") or row.get("knowledge_points")
    if isinstance(raw_ids, str):
        try:
            raw_ids = json.loads(raw_ids)
        except json.JSONDecodeError:
            pass
    knowledge_ids = _knowledge_id_list(raw_ids)
    if knowledge_ids:
        return knowledge_ids
    primary = _first_text(row, "knowledge_id", "knowledge_label", "knowledge_point")
    return [primary] if primary else [""]


def _load_sqlite_rubric_labels(conn: sqlite3.Connection) -> dict[tuple[str, str, str], str]:
    tables = {
        str(row["name"])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    }
    if "grading_sessions" not in tables:
        return {}
    labels: dict[tuple[str, str, str], str] = {}
    data_root = _sqlite_data_root(conn)
    for row in conn.execute("SELECT id, rubric_path FROM grading_sessions").fetchall():
        session_id = _clean_string(row["id"])
        rubric_path = resolve_stored_file_path(row["rubric_path"], data_root=data_root)
        if not session_id or not rubric_path.exists():
            continue
        try:
            rubric = json.loads(rubric_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for question in rubric.get("questions", []) if isinstance(rubric, Mapping) else []:
            if not isinstance(question, Mapping):
                continue
            qid = _clean_string(question.get("question_id"))
            for knowledge_id in _knowledge_ids(question):
                labels[(session_id, qid, knowledge_id)] = _knowledge_label_from_rubric_question(knowledge_id, question)
            parts = question.get("parts")
            if isinstance(parts, list):
                for part in parts:
                    if not isinstance(part, Mapping):
                        continue
                    pid = _clean_string(part.get("part_id"))
                    for knowledge_id in _knowledge_ids(part) or _knowledge_ids(question):
                        labels[(session_id, pid, knowledge_id)] = _knowledge_label_from_rubric_question(knowledge_id, question)
    return labels


def _sqlite_data_root(conn: sqlite3.Connection) -> Path | None:
    try:
        db_row = conn.execute("PRAGMA database_list").fetchone()
        db_path = Path(_clean_string(db_row[2])) if db_row else Path()
    except Exception:
        return None
    return db_path.parent.parent if db_path.parent.name == "databases" else None


def _knowledge_label_from_rubric_question(knowledge_id: str, question: Mapping[str, Any]) -> str:
    point_label = _knowledge_point_labels_from_question(question).get(_clean_string(knowledge_id))
    if point_label:
        return point_label
    for key in ("knowledge_name", "knowledge_label", "knowledge_text", "stem_summary"):
        text = _clean_string(question.get(key))
        if text:
            return text
    return knowledge_id


def _knowledge_id_list(value: object) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        return _to_string_list(value)
    if isinstance(value, Iterable) and not isinstance(value, Mapping):
        result: list[str] = []
        for item in value:
            if isinstance(item, Mapping):
                text = _first_text(item, "knowledge_id", "id", "knowledge_name", "name", "knowledge_label", "label")
            else:
                text = _clean_string(item)
            if text and text not in result:
                result.append(text)
        return result
    if isinstance(value, Mapping):
        text = _first_text(value, "knowledge_id", "id", "knowledge_name", "name", "knowledge_label", "label")
        return [text] if text else []
    return _to_string_list(value)


def _knowledge_point_labels_from_question(question: Mapping[str, Any]) -> dict[str, str]:
    labels: dict[str, str] = {}
    raw_points = question.get("knowledge_points")
    if isinstance(raw_points, str):
        try:
            raw_points = json.loads(raw_points)
        except json.JSONDecodeError:
            raw_points = ERROR_SPLIT_PATTERN.split(raw_points)
    if isinstance(raw_points, Iterable) and not isinstance(raw_points, (str, bytes, Mapping)):
        for point in raw_points:
            if isinstance(point, Mapping):
                kid = _first_text(point, "knowledge_id", "id")
                label = _first_text(point, "knowledge_name", "name", "knowledge_label", "label", "knowledge_text")
                if kid and label:
                    labels.setdefault(kid, label)
                elif label:
                    labels.setdefault(label, label)
            else:
                label = _clean_string(point)
                if label:
                    labels.setdefault(label, label)

    primary_kid = _clean_string(question.get("knowledge_id"))
    primary_label = _first_text(question, "knowledge_name", "knowledge_label", "knowledge_text")
    if primary_kid and primary_label:
        labels.setdefault(primary_kid, primary_label)
    return labels


def _detail_mastery(score_awarded: object) -> float:
    try:
        score = float(score_awarded)
    except (TypeError, ValueError):
        return 0.0
    return min(max(score, 0.0), 1.0)


def _extend_unique(target: list[str], values: Iterable[str]) -> None:
    for value in values:
        if value and value not in target:
            target.append(value)


def _to_string_list(value: object) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        items = ERROR_SPLIT_PATTERN.split(value)
    elif isinstance(value, Iterable) and not isinstance(value, Mapping):
        items = value
    else:
        items = [value]

    result: list[str] = []
    for item in items:
        text = _clean_string(item)
        if text and text not in result:
            result.append(text)
    return result


def _first_text(row: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        text = _clean_string(row.get(key))
        if text:
            return text
    return ""


def _clean_string(value: object) -> str:
    return str(value or "").strip()


def _optional_rate(value: object) -> float | None:
    if value in (None, ""):
        return None
    return _rate(value, default=0.0)


def _rate(value: object, *, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number > 1:
        number /= 100
    return round(min(max(number, 0.0), 1.0), 4)
