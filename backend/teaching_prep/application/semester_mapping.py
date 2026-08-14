from __future__ import annotations

import re
from collections.abc import Mapping

from backend.teaching_prep.application.semester_mapping_evidence import (
    build_directory_evidence,
)
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
    snapshot_lessons = _mapping_list(snapshot.get("lessons"), "lessons")
    existing_lessons = {
        str(item["id"])
        for item in snapshot_lessons
        if item.get("node_type") == "lesson"
    }
    evidence = snapshot.get("directory_evidence")
    toc_items, ranges_by_toc = _local_toc_contract(
        evidence,
        existing_tree=bool(existing_lessons),
    )
    annotations = _semantic_annotations(
        raw.get("annotations"),
        toc_items=toc_items,
        existing_tree=bool(existing_lessons),
    )
    matches = _list(raw.get("matches"), "matches")
    uncertainties = _list(raw.get("uncertainties"), "uncertainties")
    materials = _mapping_list(snapshot.get("materials"), "materials")
    if len(materials) != 1:
        raise TeachingPrepValidationError(
            "semantic mapping requires exactly one material"
        )
    record_id = str(materials[0].get("record_id") or "")
    local_uncertainties = [str(item) for item in uncertainties]
    if existing_lessons:
        tree: list[dict[str, object]] = []
        unique_lesson_refs_by_title = _unique_lesson_refs_by_title(
            snapshot_lessons
        )
        fallback_lesson_refs_by_evidence = {
            item["evidence_id"]: unique_lesson_refs_by_title[item["title"]]
            for item in annotations
            if item["title"] in unique_lesson_refs_by_title
        }
        matches = _complete_existing_matches_locally(
            matches,
            lesson_titles={
                str(item.get("id") or ""): str(item.get("title") or "")
                for item in snapshot_lessons
                if item.get("node_type") == "lesson"
            },
            toc_items=toc_items,
            ranges_by_toc=ranges_by_toc,
            fallback_lesson_refs_by_evidence=(
                fallback_lesson_refs_by_evidence
            ),
        )
        mappings = _materialize_existing_matches(
            matches,
            existing_lessons=existing_lessons,
            lesson_titles={
                str(item.get("id") or ""): str(item.get("title") or "")
                for item in snapshot_lessons
                if item.get("node_type") == "lesson"
            },
            fallback_lesson_refs_by_evidence=(
                fallback_lesson_refs_by_evidence
            ),
            record_id=record_id,
            ranges_by_toc=ranges_by_toc,
            uncertainties=local_uncertainties,
        )
        mappings = _share_textbook_section_page_ranges(
            mappings,
            snapshot_lessons=snapshot_lessons,
            material_role=str(materials[0].get("material_role") or ""),
            record_id=record_id,
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
    if tree and existing_lessons:
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
        existing_lessons
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
    *,
    existing_tree: bool,
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
    if existing_tree:
        # A workbook page containing answer choices can look like a tiny TOC
        # to OCR (for example, rows titled only "A," or "D,").  Such rows
        # have no locally resolved page range and therefore cannot safely be
        # used to create a lesson link.  Keep only TOC evidence that the local
        # parser can actually turn into pages; otherwise fall back to the
        # already parsed page-heading anchors below.
        toc_items = {
            evidence_id: item
            for evidence_id, item in toc_items.items()
            if evidence_id in ranges_by_toc
        }
        ranges_by_toc = {
            evidence_id: item
            for evidence_id, item in ranges_by_toc.items()
            if evidence_id in toc_items
        }
    if not toc_items:
        raw_anchors = value.get("anchors")
        if isinstance(raw_anchors, list):
            for item in raw_anchors:
                if not isinstance(item, Mapping):
                    continue
                evidence_id = str(item.get("evidence_id") or "").strip()
                unit_index = item.get("unit_index")
                if (
                    not evidence_id
                    or isinstance(unit_index, bool)
                    or not isinstance(unit_index, int)
                    or unit_index < 1
                ):
                    continue
                toc_items[evidence_id] = item
                ranges_by_toc[evidence_id] = {
                    "start_unit": unit_index,
                    "end_unit": unit_index,
                }
    return toc_items, ranges_by_toc


def _semantic_annotations(
    value: object,
    *,
    toc_items: Mapping[str, Mapping[str, object]],
    existing_tree: bool,
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
            if existing_tree and not kind:
                kind = "other"
            else:
                raise TeachingPrepModelResponseError(
                    "semantic annotation kind is unavailable",
                    error_code=(
                        "semester_mapping_model_semantic_contract_violation"
                    ),
                )
        if kind == "lesson" and not _EXPLICIT_LESSON_TITLE.search(title):
            if existing_tree:
                kind = "section"
            else:
                raise TeachingPrepModelResponseError(
                    "lesson annotation is not an explicit numbered lesson",
                    error_code=(
                        "semester_mapping_model_semantic_contract_violation"
                    ),
                )
        if kind in _HIERARCHICAL_KINDS and (
            not chapter_title or not section_title
        ):
            if existing_tree:
                kind = "other"
            else:
                raise TeachingPrepModelResponseError(
                    "lesson annotation omitted its hierarchy",
                    error_code=(
                        "semester_mapping_model_semantic_contract_violation"
                    ),
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
    if not existing_tree and seen != set(toc_items):
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


def _complete_existing_matches_locally(
    raw_matches: list[object],
    *,
    lesson_titles: Mapping[str, str],
    toc_items: Mapping[str, Mapping[str, object]],
    ranges_by_toc: Mapping[str, Mapping[str, object]],
    fallback_lesson_refs_by_evidence: Mapping[str, str],
) -> list[object]:
    """Keep a weak model's partial answer usable without inventing pages.

    Some text-only compatible models return only the first few lessons even
    when the contract asks for every existing lesson.  The local parser can
    safely complete those omissions: an explicit matching lesson heading is
    proposed as a one-page anchor; otherwise the lesson is retained as an
    unmatched uncertainty for teacher review.
    """
    completed = list(raw_matches)
    decided: set[str] = set()
    for raw in raw_matches:
        if not isinstance(raw, Mapping):
            continue
        lesson_ref = str(raw.get("lesson_ref") or "").strip()
        if lesson_ref in lesson_titles:
            decided.add(lesson_ref)
            continue
        evidence_ids = raw.get("evidence_ids")
        if not isinstance(evidence_ids, list) or not evidence_ids:
            continue
        fallback_refs = {
            fallback_lesson_refs_by_evidence[evidence_id]
            for evidence_id in (
                str(item or "").strip() for item in evidence_ids
            )
            if evidence_id in fallback_lesson_refs_by_evidence
        }
        if len(fallback_refs) == 1:
            decided.update(fallback_refs)
    for lesson_ref, title in lesson_titles.items():
        if lesson_ref in decided:
            continue
        evidence_id = _best_local_lesson_evidence(
            title,
            toc_items=toc_items,
            ranges_by_toc=ranges_by_toc,
        )
        completed.append(
            {
                "lesson_ref": lesson_ref,
                "evidence_ids": [evidence_id] if evidence_id else [],
                "basis": (
                    "本机课时标题匹配"
                    if evidence_id
                    else "模型漏项且本机无可靠匹配"
                ),
            }
        )
    return completed


def _best_local_lesson_evidence(
    lesson_title: str,
    *,
    toc_items: Mapping[str, Mapping[str, object]],
    ranges_by_toc: Mapping[str, Mapping[str, object]],
) -> str:
    lesson_number = _lesson_number(lesson_title)
    lesson_core = _lesson_match_text(lesson_title)
    if len(set(lesson_core)) < 4:
        return ""
    candidates: list[tuple[float, str]] = []
    lesson_chars = set(lesson_core)
    for evidence_id, item in toc_items.items():
        if evidence_id not in ranges_by_toc:
            continue
        evidence_text = " ".join(
            (
                str(item.get("title") or ""),
                str(item.get("text_excerpt") or ""),
            )
        )
        evidence_number = _lesson_number(evidence_text)
        if (
            lesson_number is not None
            and evidence_number is not None
            and lesson_number != evidence_number
        ):
            continue
        normalized_evidence = _lesson_match_text(evidence_text)
        if not normalized_evidence:
            continue
        coverage = sum(
            character in normalized_evidence
            for character in lesson_chars
        ) / len(lesson_chars)
        exact = lesson_core in normalized_evidence
        same_number = (
            lesson_number is not None
            and lesson_number == evidence_number
        )
        if not exact and not (same_number and coverage >= 0.8):
            continue
        candidates.append(
            (coverage + (1.0 if exact else 0.0) + (0.5 if same_number else 0.0), evidence_id)
        )
    if not candidates:
        return ""
    candidates.sort(reverse=True)
    if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
        return ""
    return candidates[0][1]


def _lesson_number(value: str) -> int | None:
    match = re.search(r"第\s*(\d+)\s*课时", str(value or ""))
    return int(match.group(1)) if match is not None else None


def _lesson_match_text(value: str) -> str:
    without_number = re.sub(
        r"第\s*\d+\s*课时",
        "",
        str(value or "").lower(),
    )
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", without_number)


def _materialize_existing_matches(
    raw_matches: list[object],
    *,
    existing_lessons: set[str],
    lesson_titles: Mapping[str, str],
    fallback_lesson_refs_by_evidence: Mapping[str, str],
    record_id: str,
    ranges_by_toc: Mapping[str, Mapping[str, object]],
    uncertainties: list[str],
) -> list[dict[str, object]]:
    if len(raw_matches) > 160:
        raise TeachingPrepValidationError("too many semantic matches")
    mappings: list[dict[str, object]] = []
    assigned_pairs: set[tuple[str, str]] = set()
    covered_toc: set[str] = set()
    merged_decisions: dict[str, dict[str, object]] = {}
    decision_order: list[str] = []
    for raw in raw_matches:
        item = _mapping(raw, "match")
        if set(item) != {"lesson_ref", "evidence_ids", "basis"}:
            raise TeachingPrepModelResponseError(
                "semantic match contains forbidden fields",
                error_code="semester_mapping_model_semantic_contract_violation",
            )
        evidence_ids = _list(item.get("evidence_ids"), "evidence_ids")
        if len(evidence_ids) > 20:
            raise TeachingPrepValidationError(
                "semantic match evidence IDs are invalid"
            )
        normalized_evidence_ids = [
            str(raw_id or "").strip() for raw_id in evidence_ids
        ]
        lesson_ref = str(item.get("lesson_ref") or "").strip()
        if lesson_ref not in existing_lessons:
            fallback_refs = {
                fallback_lesson_refs_by_evidence[evidence_id]
                for evidence_id in normalized_evidence_ids
                if evidence_id in fallback_lesson_refs_by_evidence
            }
            if (
                len(fallback_refs) != 1
                or len(normalized_evidence_ids)
                != sum(
                    evidence_id in fallback_lesson_refs_by_evidence
                    for evidence_id in normalized_evidence_ids
                )
            ):
                raise TeachingPrepModelResponseError(
                    "semantic match refers to an unavailable lesson",
                    error_code="semester_mapping_unavailable_lesson",
                )
            lesson_ref = next(iter(fallback_refs))
        basis = str(item.get("basis") or "").strip()
        if not basis or len(basis) > 120:
            raise TeachingPrepValidationError("semantic match basis is invalid")
        decision = merged_decisions.get(lesson_ref)
        if decision is None:
            decision = {"basis": basis, "evidence_ids": []}
            merged_decisions[lesson_ref] = decision
            decision_order.append(lesson_ref)
        decided_evidence_ids = decision["evidence_ids"]
        for evidence_id in normalized_evidence_ids:
            if evidence_id not in decided_evidence_ids:
                decided_evidence_ids.append(evidence_id)
    if set(decision_order) != existing_lessons:
        raise TeachingPrepValidationError(
            "semantic mapping did not decide every existing lesson"
        )
    for lesson_ref in decision_order:
        decision = merged_decisions[lesson_ref]
        basis = str(decision["basis"])
        decided_evidence_ids = decision["evidence_ids"]
        if not decided_evidence_ids:
            title = lesson_titles.get(lesson_ref) or "已有课时"
            uncertainties.append(f"{title}暂未对应：{basis}")
            continue
        for toc_id in decided_evidence_ids:
            identity = (lesson_ref, toc_id)
            if identity in assigned_pairs or toc_id not in ranges_by_toc:
                raise TeachingPrepModelResponseError(
                    "semantic match evidence is duplicated or unavailable",
                    error_code="semester_mapping_model_semantic_evidence_mismatch",
                )
            assigned_pairs.add(identity)
            covered_toc.add(toc_id)
            mappings.append(
                _local_mapping(
                    record_id=record_id,
                    lesson_ref=lesson_ref,
                    toc_id=toc_id,
                    local_range=ranges_by_toc[toc_id],
                    basis=basis,
                )
            )
    if ranges_by_toc and covered_toc != set(ranges_by_toc):
        uncertainties.append("部分目录行未能对应到已有课时，需教师复核。")
    return mappings


def _share_textbook_section_page_ranges(
    mappings: list[dict[str, object]],
    *,
    snapshot_lessons: list[Mapping[str, object]],
    material_role: str,
    record_id: str,
    uncertainties: list[str],
) -> list[dict[str, object]]:
    if material_role != "textbook":
        return mappings
    by_id = {
        str(item.get("id") or ""): item
        for item in snapshot_lessons
        if str(item.get("id") or "")
    }

    def section_id_for(lesson_ref: str) -> str:
        current = by_id.get(lesson_ref)
        visited: set[str] = set()
        while current is not None:
            node_id = str(current.get("id") or "")
            if not node_id or node_id in visited:
                break
            visited.add(node_id)
            if current.get("node_type") == "section":
                return node_id
            parent_id = str(current.get("parent_id") or "")
            current = by_id.get(parent_id) if parent_id else None
        return ""

    lessons_by_section: dict[str, list[str]] = {}
    titles = {
        str(item.get("id") or ""): str(item.get("title") or "")
        for item in snapshot_lessons
        if item.get("node_type") == "lesson"
    }
    for item in snapshot_lessons:
        if item.get("node_type") != "lesson":
            continue
        lesson_ref = str(item.get("id") or "")
        section_id = section_id_for(lesson_ref)
        if lesson_ref and section_id:
            lessons_by_section.setdefault(section_id, []).append(lesson_ref)
    if not lessons_by_section:
        return mappings

    rewritten = list(mappings)
    for lesson_refs in lessons_by_section.values():
        section_mappings = [
            item
            for item in rewritten
            if str(item.get("lesson_ref") or "") in lesson_refs
        ]
        if not section_mappings:
            continue
        start = min(int(item["start_unit"]) for item in section_mappings)
        end = max(int(item["end_unit"]) for item in section_mappings)
        evidence: list[str] = []
        for item in section_mappings:
            for ref in item.get("evidence_refs") or []:
                text = str(ref or "").strip()
                if text and text not in evidence:
                    evidence.append(text)
        basis = "同小节共用教材页码"
        seen: set[str] = set()
        next_rows: list[dict[str, object]] = []
        for item in rewritten:
            lesson_ref = str(item.get("lesson_ref") or "")
            if lesson_ref not in lesson_refs:
                next_rows.append(item)
                continue
            if lesson_ref in seen:
                continue
            updated = dict(item)
            updated["start_unit"] = start
            updated["end_unit"] = end
            if evidence:
                updated["evidence_refs"] = evidence[:20]
            next_rows.append(updated)
            seen.add(lesson_ref)
        for lesson_ref in lesson_refs:
            if lesson_ref in seen:
                continue
            next_rows.append(
                {
                    "material_record_id": record_id,
                    "lesson_ref": lesson_ref,
                    "start_unit": start,
                    "end_unit": end,
                    "basis": basis,
                    "evidence_refs": evidence[:20],
                }
            )
            seen.add(lesson_ref)
            title = titles.get(lesson_ref) or ""
            if title:
                prefix = f"{title}暂未对应"
                for index, item in enumerate(list(uncertainties)):
                    if str(item).startswith(prefix):
                        uncertainties.pop(index)
                        break
        rewritten = next_rows
    return rewritten


def _unique_lesson_refs_by_title(
    snapshot_lessons: list[Mapping[str, object]],
) -> dict[str, str]:
    refs_by_title: dict[str, set[str]] = {}
    for item in snapshot_lessons:
        if item.get("node_type") != "lesson":
            continue
        title = str(item.get("title") or "").strip()
        lesson_ref = str(item.get("id") or "").strip()
        if title and lesson_ref:
            refs_by_title.setdefault(title, set()).add(lesson_ref)
    return {
        title: next(iter(lesson_refs))
        for title, lesson_refs in refs_by_title.items()
        if len(lesson_refs) == 1
    }


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


def rematerialize_existing_mapping_payload(
    payload: Mapping[str, object],
    *,
    snapshot: Mapping[str, object],
) -> dict[str, object]:
    """Rebuild page ranges locally from stored lesson matches. No model call."""
    if payload.get("generation_source") == "local_reference_ppt_names":
        raise TeachingPrepValidationError(
            "local reference PPT mappings do not use printed-page recomputation"
        )
    materials = _mapping_list(snapshot.get("materials"), "materials")
    if len(materials) != 1:
        raise TeachingPrepValidationError(
            "page recomputation requires exactly one material"
        )
    material = materials[0]
    record_id = str(material.get("record_id") or "")
    material_role = str(material.get("material_role") or "")
    new_evidence = build_directory_evidence(snapshot)
    snapshot_lessons = _mapping_list(snapshot.get("lessons"), "lessons")
    existing_lessons = {
        str(item["id"])
        for item in snapshot_lessons
        if item.get("node_type") == "lesson"
    }
    toc_items, ranges_by_toc = _local_toc_contract(
        new_evidence,
        existing_tree=bool(existing_lessons),
    )
    old_toc_by_id = _toc_by_id(payload.get("directory_evidence"))
    lesson_titles = {
        str(item.get("id") or ""): str(item.get("title") or "")
        for item in snapshot_lessons
        if item.get("node_type") == "lesson"
    }
    uncertainties: list[str] = [
        "已按本机页码规则重算页段，未调用模型。"
    ]
    rebuilt: list[dict[str, object]] = []
    seen_lessons: set[str] = set()
    for mapping in _mapping_list(payload.get("mappings"), "mappings"):
        lesson_ref = str(mapping.get("lesson_ref") or "").strip()
        if not lesson_ref or lesson_ref in seen_lessons:
            continue
        if existing_lessons and lesson_ref not in existing_lessons:
            continue
        seen_lessons.add(lesson_ref)
        old_titles = [
            str(old_toc_by_id[ref].get("title") or "")
            for ref in _string_list(mapping.get("evidence_refs"))
            if ref in old_toc_by_id
        ]
        toc_id = _match_recompute_toc(
            lesson_title=lesson_titles.get(lesson_ref, ""),
            old_titles=old_titles,
            toc_items=toc_items,
            ranges_by_toc=ranges_by_toc,
        )
        if not toc_id:
            title = lesson_titles.get(lesson_ref) or "已有课时"
            uncertainties.append(f"{title}暂未对应：页码重算后未找到可靠目录行")
            continue
        rebuilt.append(
            _local_mapping(
                record_id=record_id,
                lesson_ref=lesson_ref,
                toc_id=toc_id,
                local_range=ranges_by_toc[toc_id],
                basis="本机按页脚页码重算",
            )
        )
    rebuilt = _share_textbook_section_page_ranges(
        rebuilt,
        snapshot_lessons=snapshot_lessons,
        material_role=material_role,
        record_id=record_id,
        uncertainties=uncertainties,
    )
    purpose = _ROLE_PURPOSES.get(material_role, "supplement")
    for item in rebuilt:
        item["purpose"] = purpose
    return {
        "tree": list(payload.get("tree") or []),
        "mappings": rebuilt,
        "uncertainties": list(dict.fromkeys(uncertainties))[:100],
        "source_material_record_ids": list(
            payload.get("source_material_record_ids") or [record_id]
        ),
        "directory_evidence": new_evidence,
    }


def _toc_by_id(value: object) -> dict[str, Mapping[str, object]]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, Mapping[str, object]] = {}
    raw = value.get("toc_entries")
    if not isinstance(raw, list):
        return result
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        evidence_id = str(item.get("evidence_id") or "").strip()
        if evidence_id:
            result[evidence_id] = item
    return result


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item or "").strip() for item in value if str(item or "").strip()]


def _match_recompute_toc(
    *,
    lesson_title: str,
    old_titles: list[str],
    toc_items: Mapping[str, Mapping[str, object]],
    ranges_by_toc: Mapping[str, Mapping[str, object]],
) -> str:
    for title in (*old_titles, lesson_title):
        found = _best_local_lesson_evidence(
            title,
            toc_items=toc_items,
            ranges_by_toc=ranges_by_toc,
        )
        if found:
            return found
    for title in old_titles:
        core = _lesson_match_text(title)
        if len(set(core)) < 4:
            continue
        hits = [
            evidence_id
            for evidence_id, item in toc_items.items()
            if evidence_id in ranges_by_toc
            and (
                core in _lesson_match_text(str(item.get("title") or ""))
                or _lesson_match_text(str(item.get("title") or "")) in core
            )
        ]
        if len(hits) == 1:
            return hits[0]
    return ""


__all__ = [
    "materialize_semantic_mapping_payload",
    "rematerialize_existing_mapping_payload",
    "validate_semester_mapping_payload",
]
