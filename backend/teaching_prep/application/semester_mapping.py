from __future__ import annotations

import re
from collections.abc import Mapping

from backend.teaching_prep.domain.errors import TeachingPrepValidationError


_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_ROLE_PURPOSES = {
    "textbook": "textbook",
    "reference_ppt": "reference_ppt",
    "exercise_workbook": "exercise",
    "homework_workbook": "exercise",
    "answer_book": "answer",
    "supplement": "supplement",
}


def validate_semester_mapping_payload(
    raw: Mapping[str, object],
    *,
    snapshot: Mapping[str, object],
) -> dict[str, object]:
    if set(raw) != {"tree", "mappings", "uncertainties"}:
        raise TeachingPrepValidationError(
            "semester mapping proposal has unexpected fields"
        )
    tree = _list(raw.get("tree"), "tree")
    mappings = _list(raw.get("mappings"), "mappings")
    uncertainties = _list(raw.get("uncertainties"), "uncertainties")
    existing_lessons = {
        str(item["id"])
        for item in _mapping_list(snapshot.get("lessons"), "lessons")
        if item.get("node_type") == "lesson"
    }
    if tree and _mapping_list(snapshot.get("lessons"), "lessons"):
        raise TeachingPrepValidationError(
            "a proposed lesson tree can only apply to an empty semester"
        )

    normalized_tree: list[dict[str, object]] = []
    proposal_lesson_refs: set[str] = set()
    all_keys: set[str] = set()
    lesson_count = 0
    if len(tree) > 30:
        raise TeachingPrepValidationError("too many proposed chapters")
    for chapter_raw in tree:
        chapter = _mapping(chapter_raw, "chapter")
        _require_exact(chapter, {"key", "title", "sections"}, "chapter")
        chapter_key = _proposal_key(chapter.get("key"), all_keys)
        sections = _list(chapter.get("sections"), "sections")
        if len(sections) > 30:
            raise TeachingPrepValidationError(
                "too many proposed sections in a chapter"
            )
        normalized_sections: list[dict[str, object]] = []
        for section_raw in sections:
            section = _mapping(section_raw, "section")
            _require_exact(section, {"key", "title", "lessons"}, "section")
            section_key = _proposal_key(section.get("key"), all_keys)
            lessons = _list(section.get("lessons"), "lessons")
            if len(lessons) > 30:
                raise TeachingPrepValidationError(
                    "too many proposed lessons in a section"
                )
            normalized_lessons: list[dict[str, object]] = []
            for lesson_raw in lessons:
                lesson = _mapping(lesson_raw, "lesson")
                _require_exact(
                    lesson,
                    {"key", "title", "duration_minutes"},
                    "lesson",
                )
                lesson_key = _proposal_key(lesson.get("key"), all_keys)
                duration = lesson.get("duration_minutes")
                if (
                    isinstance(duration, bool)
                    or not isinstance(duration, int)
                    or duration < 1
                    or duration > 300
                ):
                    raise TeachingPrepValidationError(
                        "proposed lesson duration is invalid"
                    )
                lesson_ref = f"proposal:{lesson_key}"
                proposal_lesson_refs.add(lesson_ref)
                lesson_count += 1
                normalized_lessons.append(
                    {
                        "key": lesson_key,
                        "title": _title(lesson.get("title")),
                        "duration_minutes": duration,
                    }
                )
            normalized_sections.append(
                {
                    "key": section_key,
                    "title": _title(section.get("title")),
                    "lessons": normalized_lessons,
                }
            )
        normalized_tree.append(
            {
                "key": chapter_key,
                "title": _title(chapter.get("title")),
                "sections": normalized_sections,
            }
        )
    if lesson_count > 500:
        raise TeachingPrepValidationError("too many proposed lessons")

    materials = {
        str(item["record_id"]): item
        for item in _mapping_list(snapshot.get("materials"), "materials")
    }
    valid_lesson_refs = existing_lessons | proposal_lesson_refs
    normalized_mappings: list[dict[str, object]] = []
    seen: set[tuple[str, str, int, int]] = set()
    for mapping_raw in mappings:
        mapping = _mapping(mapping_raw, "mapping")
        _require_exact(
            mapping,
            {"material_record_id", "lesson_ref", "start_unit", "end_unit"},
            "mapping",
        )
        record_id = str(mapping.get("material_record_id") or "")
        material = materials.get(record_id)
        if material is None:
            raise TeachingPrepValidationError(
                "mapping refers to an unavailable semester material"
            )
        lesson_ref = str(mapping.get("lesson_ref") or "")
        if lesson_ref not in valid_lesson_refs:
            raise TeachingPrepValidationError(
                "mapping refers to an unavailable lesson"
            )
        start = mapping.get("start_unit")
        end = mapping.get("end_unit")
        unit_count = int(material["unit_count"])
        if (
            isinstance(start, bool)
            or isinstance(end, bool)
            or not isinstance(start, int)
            or not isinstance(end, int)
            or start < 1
            or end < start
            or end > unit_count
        ):
            raise TeachingPrepValidationError(
                "mapping page range is invalid"
            )
        identity = (record_id, lesson_ref, start, end)
        if identity in seen:
            raise TeachingPrepValidationError(
                "mapping contains a duplicate page range"
            )
        seen.add(identity)
        normalized_mappings.append(
            {
                "material_record_id": record_id,
                "lesson_ref": lesson_ref,
                "start_unit": start,
                "end_unit": end,
                "purpose": _ROLE_PURPOSES[str(material["material_role"])],
            }
        )

    normalized_uncertainties: list[str] = []
    for item in uncertainties:
        text = str(item or "").strip()
        if not text or len(text) > 500:
            raise TeachingPrepValidationError(
                "proposal uncertainty is invalid"
            )
        normalized_uncertainties.append(text)
    if len(normalized_uncertainties) > 100:
        raise TeachingPrepValidationError("too many proposal uncertainties")
    return {
        "tree": normalized_tree,
        "mappings": normalized_mappings,
        "uncertainties": normalized_uncertainties,
    }


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TeachingPrepValidationError(f"{label} must be an object")
    return value


def _list(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise TeachingPrepValidationError(f"{label} must be a list")
    return value


def _mapping_list(value: object, label: str) -> list[Mapping[str, object]]:
    return [_mapping(item, label) for item in _list(value, label)]


def _require_exact(
    value: Mapping[str, object],
    fields: set[str],
    label: str,
) -> None:
    if set(value) != fields:
        raise TeachingPrepValidationError(
            f"{label} has unexpected fields"
        )


def _proposal_key(value: object, seen: set[str]) -> str:
    key = str(value or "").strip()
    if not _KEY.fullmatch(key) or key in seen:
        raise TeachingPrepValidationError(
            "proposal tree key is invalid or duplicated"
        )
    seen.add(key)
    return key


def _title(value: object) -> str:
    title = str(value or "").strip()
    if not title or len(title) > 160:
        raise TeachingPrepValidationError("proposal title is invalid")
    return title


__all__ = ["validate_semester_mapping_payload"]
