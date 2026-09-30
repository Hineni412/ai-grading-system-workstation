from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from integration.mastery_adapter import adapt_mastery_rows, read_mastery_json
from path_manager import get_path_manager
from question_bank.current_knowledge import (
    CurrentKnowledgeResolver,
    CurrentKnowledgeUnavailable,
)

DEFAULT_SAMPLE_PATH = (
    Path(__file__).resolve().parents[1]
    / "data_sample"
    / "grading_results"
    / "fake_student_mastery.json"
)


def load_sample_mastery_rows(path: str | Path | None = None) -> list[dict[str, Any]]:
    return read_mastery_json(path or DEFAULT_SAMPLE_PATH)


def list_sample_students(rows: Iterable[Mapping[str, Any]] | None = None) -> list[dict[str, str]]:
    source_rows = rows if rows is not None else load_sample_mastery_rows()
    students: dict[str, dict[str, str]] = {}
    for row in source_rows:
        student_id = _first_text(row, "student_id", "student_code")
        if not student_id or student_id in students:
            continue
        student_name = _first_text(row, "student_name", "name")
        class_id = _first_text(row, "class_id", "class_name")
        label_parts = [part for part in (student_name, class_id, student_id) if part]
        students[student_id] = {
            "student_id": student_id,
            "student_name": student_name,
            "class_id": class_id,
            "label": " | ".join(label_parts),
        }
    return list(students.values())


def build_sample_debug_payload(
    student_id: object,
    *,
    rows: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    source_rows = [dict(row) for row in (rows if rows is not None else load_sample_mastery_rows())]
    normalized_student_id = _text(student_id)
    raw_rows = [
        row
        for row in source_rows
        if _first_text(row, "student_id", "student_code") == normalized_student_id
    ]
    try:
        current_knowledge = CurrentKnowledgeResolver.from_active_database(
            get_path_manager().qb_db_path
        )
    except CurrentKnowledgeUnavailable:
        profiles = []
    else:
        profiles = adapt_mastery_rows(
            raw_rows,
            current_knowledge=current_knowledge,
            student_id=normalized_student_id,
        )
    return {
        "student_id": normalized_student_id,
        "raw_rows": raw_rows,
        "normalized": profiles[0].to_dict() if profiles else None,
    }


def _first_text(row: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        text = _text(row.get(key))
        if text:
            return text
    return ""


def _text(value: object) -> str:
    return str(value or "").strip()
