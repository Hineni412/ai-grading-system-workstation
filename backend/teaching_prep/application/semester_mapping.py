from __future__ import annotations

import re
from collections.abc import Mapping

from backend.teaching_prep.domain.errors import (
    TeachingPrepModelResponseError,
    TeachingPrepValidationError,
)


_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_ROLE_PURPOSES = {
    "textbook": "textbook",
    "reference_ppt": "reference_ppt",
    "exercise_workbook": "exercise",
    "homework_workbook": "exercise",
    "answer_book": "answer",
    "supplement": "supplement",
}
_SEMANTIC_KINDS = {
    "chapter",
    "section",
    "lesson",
    "special",
    "review",
    "assessment",
    "auxiliary",
    "other",
}
_HIERARCHICAL_KINDS = {"lesson", "special", "review", "assessment"}
_LESSON_KINDS = {"lesson"}
_EXPLICIT_LESSON_TITLE = re.compile(r"第\s*\d+\s*课时")


def materialize_semantic_mapping_payload(
    raw: Mapping[str, object],
    *,
    snapshot: Mapping[str, object],
) -> dict[str, object]:
    """Join model semantics to locally owned page evidence.

    The model is deliberately unable to submit unit or printed-page values.
    Its complete output is checked against the local TOC evidence set before
    deterministic lesson-tree and page-range proposals are materialized.
    """
    if set(raw) != {"annotations", "matches", "uncertainties"}:
        raise TeachingPrepModelResponseError(
            "semester mapping model violated the semantic-only contract",
            error_code="semester_mapping_model_semantic_contract_violation",
        )
    evidence = snapshot.get("directory_evidence")
    toc_items, ranges_by_toc = _local_toc_contract(evidence)
    annotations = _semantic_annotations(
        raw.get("annotations"),
        toc_items=toc_items,
    )
    matches = _list(raw.get("matches"), "matches")
    uncertainties = _list(raw.get("uncertainties"), "uncertainties")
    existing_lessons = {
        str(item["id"])
        for item in _mapping_list(snapshot.get("lessons"), "lessons")
        if item.get("node_type") == "lesson"
    }
    materials = _mapping_list(snapshot.get("materials"), "materials")
    if len(materials) != 1:
        raise TeachingPrepValidationError(
            "semantic mapping requires exactly one material"
        )
    record_id = str(materials[0].get("record_id") or "")
    local_uncertainties = [str(item) for item in uncertainties]
    if existing_lessons:
        tree: list[dict[str, object]] = []
        mappings = _materialize_existing_matches(
            matches,
            existing_lessons=existing_lessons,
            record_id=record_id,
            ranges_by_toc=ranges_by_toc,
            uncertainties=local_uncertainties,
        )
    else:
        if matches:
            raise TeachingPrepModelResponseError(
                "initial semantic mapping must not match existing lessons",
                error_code="semester_mapping_model_semantic_contract_violation",
            )
        tree, mappings = _materialize_initial_tree(
            annotations,
            record_id=record_id,
            ranges_by_toc=ranges_by_toc,
            uncertainties=local_uncertainties,
        )
    return {
        "tree": tree,
        "mappings": mappings,
        "uncertainties": list(dict.fromkeys(local_uncertainties))[:100],
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
    snapshot_lessons = _mapping_list(snapshot.get("lessons"), "lessons")
    existing_lessons = {
        str(item["id"])
        for item in snapshot_lessons
        if item.get("node_type") == "lesson"
    }
    if tree and snapshot_lessons:
        raise TeachingPrepModelResponseError(
            "semester mapping model attempted to replace the existing "
            "lesson tree",
            error_code="semester_mapping_existing_tree_replaced",
        )

    normalized_tree: list[dict[str, object]] = []
    proposal_lesson_refs: set[str] = set()
    source_lesson_keys: set[str] = set()
    normalized_lesson_refs: dict[str, str] = {}
    lesson_count = 0
    if len(tree) > 30:
        raise TeachingPrepValidationError("too many proposed chapters")
    for chapter_index, chapter_raw in enumerate(tree, start=1):
        chapter = _mapping(chapter_raw, "chapter")
        _require_exact(chapter, {"key", "title", "sections"}, "chapter")
        chapter_key = f"chapter_{chapter_index:03d}"
        sections = _list(chapter.get("sections"), "sections")
        if len(sections) > 30:
            raise TeachingPrepValidationError(
                "too many proposed sections in a chapter"
            )
        normalized_sections: list[dict[str, object]] = []
        for section_index, section_raw in enumerate(sections, start=1):
            section = _mapping(section_raw, "section")
            _require_exact(section, {"key", "title", "lessons"}, "section")
            section_key = (
                f"section_{chapter_index:03d}_{section_index:03d}"
            )
            lessons = _list(section.get("lessons"), "lessons")
            if not lessons:
                raise TeachingPrepValidationError(
                    "a proposed section must contain at least one lesson"
                )
            if len(lessons) > 30:
                raise TeachingPrepValidationError(
                    "too many proposed lessons in a section"
                )
            normalized_lessons: list[dict[str, object]] = []
            for lesson_index, lesson_raw in enumerate(lessons, start=1):
                lesson = _mapping(lesson_raw, "lesson")
                _require_exact(
                    lesson,
                    {"key", "title", "duration_minutes"},
                    "lesson",
                )
                source_lesson_key = _source_lesson_key(
                    lesson.get("key"),
                    source_lesson_keys,
                )
                lesson_key = (
                    f"lesson_{chapter_index:03d}_{section_index:03d}_"
                    f"{lesson_index:03d}"
                )
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
                normalized_lesson_refs[
                    f"proposal:{source_lesson_key}"
                ] = lesson_ref
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
    evidence = snapshot.get("directory_evidence")
    evidence_items = _evidence_items(evidence)
    valid_evidence_ids = set(evidence_items)
    valid_lesson_refs = existing_lessons | proposal_lesson_refs
    normalized_mappings: list[dict[str, object]] = []
    seen: set[tuple[str, str, int, int]] = set()
    for mapping_raw in mappings:
        mapping = _mapping(mapping_raw, "mapping")
        required_fields = {
            "material_record_id", "lesson_ref", "start_unit", "end_unit"
        }
        optional_fields = {"basis", "evidence_refs"}
        if not required_fields.issubset(mapping) or not set(mapping).issubset(
            required_fields | optional_fields
        ):
            raise TeachingPrepValidationError("mapping has unexpected fields")
        record_id = str(mapping.get("material_record_id") or "")
        material = materials.get(record_id)
        if material is None:
            raise TeachingPrepValidationError(
                "mapping refers to an unavailable semester material"
            )
        raw_lesson_ref = str(mapping.get("lesson_ref") or "")
        lesson_ref = normalized_lesson_refs.get(
            raw_lesson_ref,
            raw_lesson_ref,
        )
        if lesson_ref not in valid_lesson_refs:
            raise TeachingPrepModelResponseError(
                "semester mapping model referred to a lesson outside the "
                "existing tree",
                error_code="semester_mapping_unavailable_lesson",
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
        evidence_refs = _evidence_refs(
            mapping.get("evidence_refs"),
            valid_ids=valid_evidence_ids,
        )
        if not evidence_refs:
            evidence_refs = _overlapping_evidence_refs(
                evidence_items,
                start=start,
                end=end,
            )
        basis = str(mapping.get("basis") or "").strip()
        if len(basis) > 500:
            raise TeachingPrepValidationError("mapping basis is invalid")
        if not basis:
            basis = (
                "依据目录页码与正文锚点推断，需教师结合原页复核。"
                if evidence_refs
                else "模型未提供映射依据，需教师结合原页复核。"
            )
        normalized_mappings.append(
            {
                "material_record_id": record_id,
                "lesson_ref": lesson_ref,
                "start_unit": start,
                "end_unit": end,
                "purpose": _ROLE_PURPOSES[str(material["material_role"])],
                "basis": basis,
                "evidence_refs": evidence_refs,
            }
        )

    normalized_mappings = _coalesce_adjacent_ranges(normalized_mappings)

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
    if (
        snapshot_lessons
        and not normalized_uncertainties
        and _has_unmapped_material_units(materials, normalized_mappings)
    ):
        raise TeachingPrepModelResponseError(
            "semester mapping model omitted uncertainty for unmapped pages",
            error_code="semester_mapping_unexplained_coverage_gap",
        )
    return {
        "tree": normalized_tree,
        "mappings": normalized_mappings,
        "uncertainties": normalized_uncertainties,
    }


def _has_unmapped_material_units(
    materials: Mapping[str, Mapping[str, object]],
    mappings: list[dict[str, object]],
) -> bool:
    ranges_by_material: dict[str, list[tuple[int, int]]] = {
        record_id: [] for record_id in materials
    }
    for item in mappings:
        record_id = str(item["material_record_id"])
        if record_id in ranges_by_material:
            ranges_by_material[record_id].append(
                (int(item["start_unit"]), int(item["end_unit"]))
            )
    for record_id, material in materials.items():
        next_uncovered = 1
        for start, end in sorted(ranges_by_material[record_id]):
            if start > next_uncovered:
                return True
            next_uncovered = max(next_uncovered, end + 1)
        if next_uncovered <= int(material["unit_count"]):
            return True
    return False


def _local_toc_contract(
    value: object,
) -> tuple[dict[str, Mapping[str, object]], dict[str, Mapping[str, object]]]:
    if not isinstance(value, Mapping):
        return {}, {}
    toc_items: dict[str, Mapping[str, object]] = {}
    raw_toc = value.get("toc_entries")
    if isinstance(raw_toc, list):
        for item in raw_toc:
            if not isinstance(item, Mapping):
                continue
            evidence_id = str(item.get("evidence_id") or "").strip()
            if evidence_id:
                toc_items[evidence_id] = item
    ranges_by_toc: dict[str, Mapping[str, object]] = {}
    raw_ranges = value.get("resolved_ranges")
    if isinstance(raw_ranges, list):
        for item in raw_ranges:
            if not isinstance(item, Mapping):
                continue
            toc_id = str(item.get("toc_evidence_id") or "").strip()
            if toc_id in toc_items:
                ranges_by_toc[toc_id] = item
    return toc_items, ranges_by_toc


def _semantic_annotations(
    value: object,
    *,
    toc_items: Mapping[str, Mapping[str, object]],
) -> list[dict[str, str]]:
    raw_items = _list(value, "annotations")
    if len(raw_items) > 160:
        raise TeachingPrepValidationError("too many semantic annotations")
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in raw_items:
        item = _mapping(raw, "annotation")
        if set(item) != {
            "evidence_id",
            "title",
            "chapter_title",
            "section_title",
            "kind",
        }:
            raise TeachingPrepModelResponseError(
                "semantic annotation contains forbidden fields",
                error_code="semester_mapping_model_semantic_contract_violation",
            )
        evidence_id = str(item.get("evidence_id") or "").strip()
        if evidence_id not in toc_items or evidence_id in seen:
            raise TeachingPrepModelResponseError(
                "semantic annotation refers to unavailable evidence",
                error_code="semester_mapping_model_semantic_evidence_mismatch",
            )
        seen.add(evidence_id)
        title = _title(item.get("title"))
        chapter_title = _semantic_title(item.get("chapter_title"))
        section_title = _semantic_title(item.get("section_title"))
        kind = str(item.get("kind") or "").strip()
        if kind not in _SEMANTIC_KINDS:
            raise TeachingPrepModelResponseError(
                "semantic annotation kind is unavailable",
                error_code="semester_mapping_model_semantic_contract_violation",
            )
        if kind == "lesson" and not _EXPLICIT_LESSON_TITLE.search(title):
            raise TeachingPrepModelResponseError(
                "lesson annotation is not an explicit numbered lesson",
                error_code="semester_mapping_model_semantic_contract_violation",
            )
        if kind in _HIERARCHICAL_KINDS and (
            not chapter_title or not section_title
        ):
            raise TeachingPrepModelResponseError(
                "lesson annotation omitted its hierarchy",
                error_code="semester_mapping_model_semantic_contract_violation",
            )
        result.append(
            {
                "evidence_id": evidence_id,
                "title": title,
                "chapter_title": chapter_title,
                "section_title": section_title,
                "kind": kind,
            }
        )
    if seen != set(toc_items):
        raise TeachingPrepModelResponseError(
            "semantic annotations did not cover every local directory row",
            error_code="semester_mapping_model_semantic_evidence_incomplete",
        )
    return result


def _semantic_title(value: object) -> str:
    title = str(value or "").strip()
    if len(title) > 160:
        raise TeachingPrepValidationError("semantic hierarchy title is invalid")
    return title


def _materialize_initial_tree(
    annotations: list[dict[str, str]],
    *,
    record_id: str,
    ranges_by_toc: Mapping[str, Mapping[str, object]],
    uncertainties: list[str],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    chapters: list[dict[str, object]] = []
    chapter_lookup: dict[str, dict[str, object]] = {}
    section_lookup: dict[tuple[str, str], dict[str, object]] = {}
    mappings: list[dict[str, object]] = []
    lesson_number = 0
    for item in annotations:
        if item["kind"] not in _LESSON_KINDS:
            continue
        chapter_title = item["chapter_title"]
        section_title = item["section_title"]
        chapter = chapter_lookup.get(chapter_title)
        if chapter is None:
            chapter = {
                "key": f"semantic_chapter_{len(chapters) + 1:03d}",
                "title": chapter_title,
                "sections": [],
            }
            chapters.append(chapter)
            chapter_lookup[chapter_title] = chapter
        section_key = (chapter_title, section_title)
        section = section_lookup.get(section_key)
        if section is None:
            section = {
                "key": (
                    f"semantic_section_{len(chapters):03d}_"
                    f"{len(chapter['sections']) + 1:03d}"
                ),
                "title": section_title,
                "lessons": [],
            }
            chapter["sections"].append(section)
            section_lookup[section_key] = section
        lesson_number += 1
        lesson_key = f"semantic_lesson_{lesson_number:03d}"
        section["lessons"].append(
            {
                "key": lesson_key,
                "title": item["title"],
                "duration_minutes": 45,
            }
        )
        local_range = ranges_by_toc.get(item["evidence_id"])
        if local_range is None:
            uncertainties.append(
                f"{item['title']}尚未完成本地页码校准。"
            )
            continue
        mappings.append(
            _local_mapping(
                record_id=record_id,
                lesson_ref=f"proposal:{lesson_key}",
                toc_id=item["evidence_id"],
                local_range=local_range,
                basis="本地目录定位",
            )
        )
    return chapters, mappings


def _materialize_existing_matches(
    raw_matches: list[object],
    *,
    existing_lessons: set[str],
    record_id: str,
    ranges_by_toc: Mapping[str, Mapping[str, object]],
    uncertainties: list[str],
) -> list[dict[str, object]]:
    if len(raw_matches) > 160:
        raise TeachingPrepValidationError("too many semantic matches")
    mappings: list[dict[str, object]] = []
    assigned: set[str] = set()
    for raw in raw_matches:
        item = _mapping(raw, "match")
        if set(item) != {"lesson_ref", "evidence_ids", "basis"}:
            raise TeachingPrepModelResponseError(
                "semantic match contains forbidden fields",
                error_code="semester_mapping_model_semantic_contract_violation",
            )
        lesson_ref = str(item.get("lesson_ref") or "").strip()
        if lesson_ref not in existing_lessons:
            raise TeachingPrepModelResponseError(
                "semantic match refers to an unavailable lesson",
                error_code="semester_mapping_unavailable_lesson",
            )
        evidence_ids = _list(item.get("evidence_ids"), "evidence_ids")
        if not evidence_ids or len(evidence_ids) > 20:
            raise TeachingPrepValidationError(
                "semantic match evidence IDs are invalid"
            )
        basis = str(item.get("basis") or "").strip()
        if not basis or len(basis) > 120:
            raise TeachingPrepValidationError("semantic match basis is invalid")
        for raw_id in evidence_ids:
            toc_id = str(raw_id or "").strip()
            if toc_id in assigned or toc_id not in ranges_by_toc:
                raise TeachingPrepModelResponseError(
                    "semantic match evidence is duplicated or unavailable",
                    error_code="semester_mapping_model_semantic_evidence_mismatch",
                )
            assigned.add(toc_id)
            mappings.append(
                _local_mapping(
                    record_id=record_id,
                    lesson_ref=lesson_ref,
                    toc_id=toc_id,
                    local_range=ranges_by_toc[toc_id],
                    basis=basis,
                )
            )
    if ranges_by_toc and assigned != set(ranges_by_toc):
        uncertainties.append("部分目录行未能对应到已有课时，需教师复核。")
    return mappings


def _local_mapping(
    *,
    record_id: str,
    lesson_ref: str,
    toc_id: str,
    local_range: Mapping[str, object],
    basis: str,
) -> dict[str, object]:
    range_id = str(local_range.get("evidence_id") or "").strip()
    return {
        "material_record_id": record_id,
        "lesson_ref": lesson_ref,
        "start_unit": int(local_range["start_unit"]),
        "end_unit": int(local_range["end_unit"]),
        "basis": basis,
        "evidence_refs": [toc_id, range_id] if range_id else [toc_id],
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


def _source_lesson_key(value: object, seen: set[str]) -> str:
    key = str(value or "").strip()
    if not _KEY.fullmatch(key) or key in seen:
        raise TeachingPrepValidationError(
            "proposal lesson key is invalid or duplicated"
        )
    seen.add(key)
    return key


def _title(value: object) -> str:
    title = str(value or "").strip()
    if not title or len(title) > 160:
        raise TeachingPrepValidationError("proposal title is invalid")
    return title


def _evidence_items(value: object) -> dict[str, Mapping[str, object]]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, Mapping[str, object]] = {}
    for collection in ("toc_entries", "resolved_ranges", "anchors"):
        raw_items = value.get(collection)
        if not isinstance(raw_items, list):
            continue
        for raw in raw_items:
            if not isinstance(raw, Mapping):
                continue
            evidence_id = str(raw.get("evidence_id") or "").strip()
            if evidence_id:
                result[evidence_id] = raw
    return result


def _evidence_refs(value: object, *, valid_ids: set[str]) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 20:
        raise TeachingPrepValidationError("mapping evidence refs are invalid")
    result: list[str] = []
    for item in value:
        evidence_id = str(item or "").strip()
        if evidence_id not in valid_ids:
            raise TeachingPrepValidationError(
                "mapping refers to unavailable directory evidence"
            )
        if evidence_id not in result:
            result.append(evidence_id)
    return result


def _overlapping_evidence_refs(
    evidence: Mapping[str, Mapping[str, object]],
    *,
    start: int,
    end: int,
) -> list[str]:
    result: list[str] = []
    for evidence_id, item in evidence.items():
        unit = item.get("unit_index")
        range_start = item.get("start_unit")
        range_end = item.get("end_unit")
        overlaps = (
            isinstance(unit, int) and start <= unit <= end
        ) or (
            isinstance(range_start, int)
            and isinstance(range_end, int)
            and range_start <= end
            and range_end >= start
        )
        if overlaps:
            result.append(evidence_id)
        if len(result) >= 8:
            break
    return result


def _coalesce_adjacent_ranges(
    mappings: list[dict[str, object]],
) -> list[dict[str, object]]:
    ordered = sorted(
        mappings,
        key=lambda item: (
            str(item["material_record_id"]),
            str(item["lesson_ref"]),
            int(item["start_unit"]),
            int(item["end_unit"]),
        ),
    )
    merged: list[dict[str, object]] = []
    for item in ordered:
        previous = merged[-1] if merged else None
        if (
            previous is not None
            and previous["material_record_id"] == item["material_record_id"]
            and previous["lesson_ref"] == item["lesson_ref"]
            and previous["purpose"] == item["purpose"]
            and int(item["start_unit"]) <= int(previous["end_unit"]) + 1
        ):
            previous["end_unit"] = max(
                int(previous["end_unit"]), int(item["end_unit"])
            )
            bases = [str(previous["basis"]), str(item["basis"])]
            previous["basis"] = "；".join(dict.fromkeys(bases))[:500]
            previous["evidence_refs"] = list(
                dict.fromkeys(
                    [
                        *list(previous["evidence_refs"]),
                        *list(item["evidence_refs"]),
                    ]
                )
            )[:20]
            continue
        merged.append(dict(item))
    return merged


__all__ = [
    "materialize_semantic_mapping_payload",
    "validate_semester_mapping_payload",
]
