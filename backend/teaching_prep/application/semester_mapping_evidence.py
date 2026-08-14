from __future__ import annotations

import math
import re
from collections.abc import Mapping
from statistics import median


_TOC_LINE = re.compile(
    r"^\s*(?P<title>.+?)(?:\.{2,}|…+|\s{2,})\s*(?P<page>\d{1,4})\s*$"
)
_HEADING_PREFIX = re.compile(
    r"^(?:第[一二三四五六七八九十百\d]+[章节课时]|\d+(?:\.\d+){0,2}\s*)"
)
_PAGE_REFERENCE = re.compile(r"(?P<track>听|作|活|评)\s*(?P<page>\d{1,4})")
_INLINE_PAGE_REFERENCE = re.compile(
    r"^(?P<title>.+?)(?:[/／]|[.·…⋯_]{2,}|\s)\s*(?P<page>\d{1,4})\s*$"
)
_MAX_TOC_ENTRIES = 160
_MAX_ANCHORS = 120
_EXPLICIT_LESSON_ANCHOR = re.compile(r"第\s*\d+\s*课时")


class DirectoryEvidenceBuilder:
    """Build the small, reviewable evidence contract used by semester mapping."""

    def build(self, snapshot: Mapping[str, object]) -> dict[str, object]:
        materials = _mapping_list(snapshot.get("materials"))
        if not materials:
            return _empty_evidence()
        material = materials[0]
        units = _mapping_list(material.get("units"))
        if not units:
            return _empty_evidence()

        initial_count = min(30, max(12, math.ceil(len(units) * 0.10)))
        maximum_count = min(40, max(initial_count, math.ceil(len(units) * 0.25)))
        scanned_count = initial_count
        preferred_page_track = _preferred_page_track(
            str(material.get("display_name") or "")
        )
        entries = _toc_entries(
            units[:scanned_count],
            preferred_page_track=preferred_page_track,
        )
        while scanned_count < maximum_count:
            if entries and not _toc_continues_near_end(entries, scanned_count):
                break
            scanned_count = min(maximum_count, scanned_count + 5)
            entries = _toc_entries(
                units[:scanned_count],
                preferred_page_track=preferred_page_track,
            )

        offsets = _calibration_offsets(entries, units, scanned_count)
        calibrated_offset, offset_issues, offset_agreed = _choose_printed_offset(
            offsets,
            entries=entries,
            total_units=len(units),
            material_role=str(material.get("material_role") or ""),
        )
        resolved = _resolved_ranges(
            entries,
            total_units=len(units),
            offset=calibrated_offset,
        )
        anchors = _anchors(
            units,
            scanned_count=scanned_count,
            resolved_ranges=resolved,
        )
        issues: list[str] = []
        if not entries:
            issues.append("前置页未识别到带页码的目录，已改用稀疏正文锚点。")
        elif calibrated_offset is None:
            issues.append("识别到目录，但未能用正文标题校准书上页码与 PDF 页码。")
        elif len(offsets) < 2:
            issues.append("目录页码只找到一个正文锚点，页码换算仍需人工核对。")
        issues.extend(offset_issues)

        confidence = "low"
        strategy = "sparse_outline"
        if entries:
            strategy = "toc_unverified"
            confidence = "medium"
        if entries and calibrated_offset is not None and offset_agreed:
            strategy = "toc_calibrated"
            confidence = "high"

        return {
            "strategy": strategy,
            "total_unit_count": len(units),
            "directory_page_unit_indices": _directory_page_unit_indices(
                units[:scanned_count],
                entries=entries,
            ),
            "scanned_unit_indices": [
                int(unit.get("unit_index") or index)
                for index, unit in enumerate(units[:scanned_count], start=1)
            ],
            "toc_entries": entries,
            "resolved_ranges": resolved,
            "anchors": anchors,
            "printed_to_pdf_offset": calibrated_offset,
            "confidence": confidence,
            "issues": issues,
            "full_page_text_sent": False,
        }


def build_directory_evidence(snapshot: Mapping[str, object]) -> dict[str, object]:
    return DirectoryEvidenceBuilder().build(snapshot)


def evidence_character_estimate(evidence: Mapping[str, object]) -> int:
    total = 0
    for collection in ("toc_entries", "resolved_ranges", "anchors", "issues"):
        for item in evidence.get(collection, []) if isinstance(evidence.get(collection), list) else []:
            total += len(str(item))
    return total


def _toc_entries(
    units: list[Mapping[str, object]],
    *,
    preferred_page_track: str | None = None,
) -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    inline_directory_active = False
    for unit in units:
        layout_entries = _layout_toc_entries(
            unit,
            preferred_page_track=preferred_page_track,
            start_index=len(found),
        )
        if layout_entries:
            found.extend(layout_entries)
            inline_directory_active = True
            if len(found) >= _MAX_TOC_ENTRIES:
                return found[:_MAX_TOC_ENTRIES]
            continue
        text = "\n".join(
            part for part in (
                str(unit.get("title") or "").strip(),
                str(unit.get("text_excerpt") or "").strip(),
            ) if part
        )
        lines = text.splitlines()
        inline_matches = [
            _INLINE_PAGE_REFERENCE.match(line)
            for line in lines
        ]
        has_directory_header = (
            "目录" in text or "CONTENTS" in text.upper()
        )
        inline_match_count = sum(
            item is not None for item in inline_matches
        )
        allow_inline_page_references = (
            has_directory_header
            or (inline_directory_active and inline_match_count > 0)
        )
        matched_inline_reference = False
        for line, cached_inline_match in zip(
            lines,
            inline_matches,
            strict=True,
        ):
            match = _TOC_LINE.match(line)
            if match is not None:
                inline_match = None
            elif allow_inline_page_references:
                inline_match = cached_inline_match
            else:
                inline_match = None
            if match is None and inline_match is None:
                continue
            if inline_match is not None:
                matched_inline_reference = True
            source_match = match or inline_match
            assert source_match is not None
            title = _clean_title(source_match.group("title"))
            page = int(source_match.group("page"))
            if len(title) < 2 or page < 1:
                continue
            found.append(
                {
                    "evidence_id": f"toc-{len(found) + 1:03d}",
                    "level": _heading_level(title),
                    "title": title,
                    "printed_page": page,
                    "source_unit": int(unit.get("unit_index") or 1),
                }
            )
            if len(found) >= _MAX_TOC_ENTRIES:
                return found
        inline_directory_active = (
            allow_inline_page_references and matched_inline_reference
        )
    return found


def _layout_toc_entries(
    unit: Mapping[str, object],
    *,
    preferred_page_track: str | None,
    start_index: int,
) -> list[dict[str, object]]:
    summary = unit.get("object_summary")
    if not isinstance(summary, Mapping):
        return []
    layout = summary.get("ocr_layout")
    if not isinstance(layout, Mapping) or layout.get("version") != 1:
        return []
    raw_items = layout.get("items")
    if not isinstance(raw_items, list):
        return []
    items = [
        item
        for item in (_layout_item(value) for value in raw_items)
        if item
    ]
    if not items:
        return []

    page_has_directory_header = any(
        "目录" in str(item["text"])
        or "CONTENTS" in str(item["text"]).upper()
        for item in items
    )
    if _uses_spread_columns(summary, items):
        rows_by_half = {
            half: _layout_rows(items, half=half)
            for half in (0, 1)
        }
    else:
        rows_by_half = {0: _layout_rows_full(items)}
    active_halves = {
        half
        for half, rows in rows_by_half.items()
        if page_has_directory_header
        or sum(
            _row_has_page_reference(row)
            for row in rows
        )
        >= 3
    }

    result: list[dict[str, object]] = []
    ordered_rows = [
        row
        for half in sorted(active_halves)
        for row in rows_by_half[half]
    ]
    for row_index, row in enumerate(ordered_rows):
        inline_entries = _inline_tracked_entries(row)
        if inline_entries:
            for title, page_refs, confidence in inline_entries:
                result.append(
                    _layout_evidence_item(
                        unit=unit,
                        start_index=start_index,
                        result_index=len(result),
                        title=title,
                        page_refs=page_refs,
                        preferred_page_track=preferred_page_track,
                        confidence=confidence,
                    )
                )
            continue
        text = " ".join(str(item["text"]) for item in row)
        matches = list(_PAGE_REFERENCE.finditer(text))
        generic_reference = None if matches else _generic_page_reference(row)
        if matches:
            title = _clean_layout_title(text[: matches[0].start()])
            page_refs = {
                match.group("track"): int(match.group("page"))
                for match in matches
            }
        elif generic_reference is not None:
            generic_title, generic_page = generic_reference
            title = _clean_layout_title(generic_title)
            page_refs = {"页": generic_page}
        else:
            title = _structural_heading(
                row,
                next_row=(
                    ordered_rows[row_index + 1]
                    if row_index + 1 < len(ordered_rows)
                    else None
                ),
            )
            if title is None:
                continue
            page_refs = {}
        if len(title) < 2:
            continue
        confidence = min(float(item["confidence"]) for item in row)
        result.append(
            _layout_evidence_item(
                unit=unit,
                start_index=start_index,
                result_index=len(result),
                title=title,
                page_refs=page_refs,
                preferred_page_track=(
                    "页" if generic_reference is not None else preferred_page_track
                ),
                confidence=confidence,
            )
        )
    return result


def _directory_page_unit_indices(
    units: list[Mapping[str, object]],
    *,
    entries: list[dict[str, object]],
) -> list[int]:
    found = {
        int(item.get("source_unit") or 0)
        for item in entries
        if int(item.get("source_unit") or 0) > 0
    }
    for unit in units:
        summary = unit.get("object_summary")
        if not isinstance(summary, Mapping):
            continue
        layout = summary.get("ocr_layout")
        raw_items = layout.get("items") if isinstance(layout, Mapping) else None
        if not isinstance(raw_items, list):
            continue
        texts = [
            str(item.get("text") or "")
            for item in raw_items
            if isinstance(item, Mapping)
        ]
        if any("目录" in text or "CONTENTS" in text.upper() for text in texts):
            found.add(int(unit.get("unit_index") or 0))
    found.discard(0)
    return sorted(found)


def _layout_evidence_item(
    *,
    unit: Mapping[str, object],
    start_index: int,
    result_index: int,
    title: str,
    page_refs: Mapping[str, int],
    preferred_page_track: str | None,
    confidence: float,
) -> dict[str, object]:
    selected_track = None
    if preferred_page_track in page_refs:
        selected_track = preferred_page_track
    elif preferred_page_track is None and page_refs:
        selected_track = next(iter(page_refs))
    return {
        "evidence_id": f"toc-{start_index + result_index + 1:03d}",
        "level": _heading_level(title),
        "title": title,
        "printed_page": (
            int(page_refs[selected_track])
            if selected_track is not None
            else None
        ),
        "printed_page_track": selected_track,
        "page_refs": dict(page_refs),
        "source_unit": int(unit.get("unit_index") or 1),
        "confidence": round(confidence, 4),
        "source": "ocr_layout",
    }


def _inline_tracked_entries(
    row: list[dict[str, object]],
) -> list[tuple[str, dict[str, int], float]]:
    result: list[tuple[str, dict[str, int], float]] = []
    for item in row:
        text = str(item["text"])
        matches = list(_PAGE_REFERENCE.finditer(text))
        if not matches:
            continue
        title = _clean_layout_title(text[: matches[0].start()].rstrip("/／ "))
        if len(title) < 2:
            continue
        result.append(
            (
                title,
                {
                    match.group("track"): int(match.group("page"))
                    for match in matches
                },
                float(item["confidence"]),
            )
        )
    return result if result else []


def _structural_heading(
    row: list[dict[str, object]],
    *,
    next_row: list[dict[str, object]] | None = None,
) -> str | None:
    text = _clean_layout_title(
        " ".join(str(item["text"]) for item in row)
    )
    compact = re.sub(r"\s+", "", text)
    if compact == "本章内容我会学":
        return text
    if (
        len(compact) <= 48
        and re.match(r"^[1-9]\d{0,1}[\u4e00-\u9fff]", compact)
        and not re.match(r"^\d+[.．、]", compact)
    ):
        return text
    next_text = re.sub(
        r"\s+",
        "",
        " ".join(str(item["text"]) for item in (next_row or [])),
    )
    if (
        2 <= len(compact) <= 32
        and re.fullmatch(r"[\u4e00-\u9fff]+", compact)
        and compact not in {"目录", "作业手册", "听课手册", "质量评估卷"}
        and re.match(r"^第1课时", next_text)
    ):
        return text
    return None


def _uses_spread_columns(
    summary: Mapping[str, object],
    items: list[dict[str, object]],
) -> bool:
    try:
        width = float(summary.get("width") or 0)
        height = float(summary.get("height") or 0)
    except (TypeError, ValueError):
        width = 0
        height = 0
    if width > 0 and height > 0:
        return width > height * 1.1
    marker_halves = {
        0 if float(item["x"]) < 0.5 else 1
        for item in items
        if _PAGE_REFERENCE.fullmatch(str(item["text"]).strip())
    }
    return marker_halves == {0, 1}


def _row_has_page_reference(row: list[dict[str, object]]) -> bool:
    text = " ".join(str(item["text"]) for item in row)
    return bool(_PAGE_REFERENCE.search(text)) or _generic_page_reference(row) is not None


def _generic_page_reference(
    row: list[dict[str, object]],
) -> tuple[str, int] | None:
    if len(row) >= 2:
        final_text = str(row[-1]["text"]).strip()
        if re.fullmatch(r"\d{1,4}", final_text) and float(row[-1]["x0"]) >= 0.68:
            title = " ".join(str(item["text"]) for item in row[:-1]).strip()
            if title:
                return title, int(final_text)
    text = " ".join(str(item["text"]) for item in row).strip()
    match = _INLINE_PAGE_REFERENCE.fullmatch(text)
    if match is None:
        return None
    return match.group("title"), int(match.group("page"))


def _layout_item(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    try:
        text = str(value.get("text") or "").strip()
        confidence = float(value.get("confidence") or 0)
        x0 = float(value["x0"])
        y0 = float(value["y0"])
        x1 = float(value["x1"])
        y1 = float(value["y1"])
    except (KeyError, TypeError, ValueError):
        return None
    if not text or confidence < 0.35 or x1 <= x0 or y1 <= y0:
        return None
    if not all(0 <= item <= 1 for item in (x0, y0, x1, y1)):
        return None
    return {
        "text": text,
        "confidence": confidence,
        "x0": x0,
        "y0": y0,
        "x1": x1,
        "y1": y1,
        "x": (x0 + x1) / 2,
        "y": (y0 + y1) / 2,
        "height": y1 - y0,
        "width": x1 - x0,
    }


def _layout_rows(
    items: list[dict[str, object]],
    *,
    half: int,
) -> list[list[dict[str, object]]]:
    candidates = sorted(
        (
            item
            for item in items
            if (float(item["x"]) < 0.5) == (half == 0)
            and float(item["height"]) <= float(item["width"]) * 2.5
        ),
        key=lambda item: (float(item["y"]), float(item["x"])),
    )
    rows: list[list[dict[str, object]]] = []
    for item in candidates:
        nearest = min(
            rows,
            key=lambda row: abs(_row_y(row) - float(item["y"])),
            default=None,
        )
        tolerance = max(0.008, float(item["height"]) * 0.7)
        if nearest is not None and abs(_row_y(nearest) - float(item["y"])) <= tolerance:
            nearest.append(item)
        else:
            rows.append([item])
    return [
        sorted(row, key=lambda item: float(item["x"]))
        for row in rows
    ]


def _layout_rows_full(
    items: list[dict[str, object]],
) -> list[list[dict[str, object]]]:
    candidates = sorted(
        (
            item
            for item in items
            if float(item["height"]) <= float(item["width"]) * 2.5
        ),
        key=lambda item: (float(item["y"]), float(item["x"])),
    )
    rows: list[list[dict[str, object]]] = []
    for item in candidates:
        nearest = min(
            rows,
            key=lambda row: abs(_row_y(row) - float(item["y"])),
            default=None,
        )
        tolerance = max(0.008, float(item["height"]) * 0.7)
        if nearest is not None and abs(_row_y(nearest) - float(item["y"])) <= tolerance:
            nearest.append(item)
        else:
            rows.append([item])
    return [
        sorted(row, key=lambda item: float(item["x"]))
        for row in rows
    ]


def _row_y(row: list[dict[str, object]]) -> float:
    return sum(float(item["y"]) for item in row) / len(row)


def _clean_layout_title(value: str) -> str:
    value = re.sub(r"[.·…⋯_]{2,}", " ", value)
    return _clean_title(value)


def _preferred_page_track(display_name: str) -> str | None:
    if "作业" in display_name:
        return "作"
    if "听课" in display_name:
        return "听"
    if "活页" in display_name or "评价卷" in display_name:
        return "活"
    return None


def _toc_continues_near_end(entries: list[dict[str, object]], scanned_count: int) -> bool:
    return any(int(item["source_unit"]) >= scanned_count - 1 for item in entries[-8:])


def _calibration_offsets(
    entries: list[dict[str, object]],
    units: list[Mapping[str, object]],
    scanned_count: int,
) -> list[int]:
    offsets: list[int] = []
    candidates = _spread(entries, 7)
    for entry in candidates:
        if not isinstance(entry.get("printed_page"), int):
            continue
        title = _searchable_title(str(entry["title"]))
        if len(title) < 3:
            continue
        source_unit = int(entry.get("source_unit") or 0)
        for unit in units:
            if int(unit.get("unit_index") or 0) <= source_unit:
                continue
            haystack = _searchable_title(
                f"{unit.get('title') or ''} {unit.get('text_excerpt') or ''}"
            )
            if title in haystack or (
                len(haystack) >= 3 and haystack[:80] in title
            ):
                offsets.append(
                    int(unit.get("unit_index") or 1) - int(entry["printed_page"])
                )
                break
    return offsets


def _choose_printed_offset(
    offsets: list[int],
    *,
    entries: list[dict[str, object]],
    total_units: int,
    material_role: str,
) -> tuple[int | None, list[str], bool]:
    extra_issues: list[str] = []
    if not offsets:
        return None, extra_issues, False
    median_offset = round(median(offsets))
    if len(offsets) < 2:
        return median_offset, extra_issues, False
    spread = max(offsets) - min(offsets)
    if spread <= 2:
        return median_offset, extra_issues, True
    extra_issues.append("不同目录锚点的页码偏移不一致，映射范围需要重点复核。")
    if material_role != "textbook":
        return median_offset, extra_issues, False
    ranked: list[tuple[int, bool, int]] = []
    for seed in sorted(set(offsets)):
        cluster = [item for item in offsets if abs(item - seed) <= 2]
        cluster_offset = round(median(cluster))
        ranked.append(
            (
                len(cluster),
                not _offset_collapses_front(
                    entries,
                    cluster_offset,
                    total_units,
                ),
                cluster_offset,
            )
        )
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    best_count, best_safe, best_offset = ranked[0]
    if best_safe and best_count >= 2:
        return best_offset, extra_issues, False
    extra_issues.append(
        "页码校准偏差过大，已改用书上页码对照 PDF，需教师按原页复核。"
    )
    return 0, extra_issues, False


def _offset_collapses_front(
    entries: list[dict[str, object]],
    offset: int,
    total_units: int,
) -> bool:
    printed = [
        int(item["printed_page"])
        for item in entries
        if isinstance(item.get("printed_page"), int)
    ]
    if len(printed) < 3:
        return False
    starts = [
        max(1, min(total_units, page + offset))
        for page in printed
    ]
    collapsed = sum(start == 1 for start in starts)
    return collapsed >= max(3, (len(starts) + 2) // 3)


def _resolved_ranges(
    entries: list[dict[str, object]],
    *,
    total_units: int,
    offset: int | None,
) -> list[dict[str, object]]:
    if offset is None:
        return []
    page_entries = [
        item
        for item in entries
        if isinstance(item.get("printed_page"), int)
    ]
    resolved: list[dict[str, object]] = []
    starts = [
        max(1, min(total_units, int(item["printed_page"]) + offset))
        for item in page_entries
    ]
    for index, entry in enumerate(page_entries):
        start = starts[index]
        next_start = starts[index + 1] if index + 1 < len(starts) else total_units + 1
        end = max(start, min(total_units, next_start - 1))
        resolved.append(
            {
                "evidence_id": f"range-{index + 1:03d}",
                "toc_evidence_id": entry["evidence_id"],
                "title": entry["title"],
                "level": entry["level"],
                "printed_page": entry["printed_page"],
                "start_unit": start,
                "end_unit": end,
            }
        )
    return resolved


def _anchors(
    units: list[Mapping[str, object]],
    *,
    scanned_count: int,
    resolved_ranges: list[dict[str, object]],
) -> list[dict[str, object]]:
    indices = {1, scanned_count, len(units)}
    for item in _spread(resolved_ranges, 10):
        start = int(item["start_unit"])
        end = int(item["end_unit"])
        indices.update((start, (start + end) // 2, end))
    if not resolved_ranges:
        # OCR-only workbooks often have no reliably parsed dotted TOC rows,
        # while their locally extracted page headings still identify lessons
        # well. Keep those headings as bounded semantic evidence so the model
        # can suggest a page and the teacher can widen or narrow it locally.
        indices.update(range(1, min(len(units), 12) + 1))
        indices.update(
            int(unit.get("unit_index") or 0)
            for unit in units
            if _EXPLICIT_LESSON_ANCHOR.search(
                "\n".join(
                    (
                        str(unit.get("title") or ""),
                        str(unit.get("text_excerpt") or "")[:500],
                    )
                )
            )
        )
        step = max(1, len(units) // 8)
        indices.update(range(1, len(units) + 1, step))
    result: list[dict[str, object]] = []
    by_index = {int(unit.get("unit_index") or 0): unit for unit in units}
    for unit_index in sorted(indices)[:_MAX_ANCHORS]:
        unit = by_index.get(unit_index)
        if unit is None:
            continue
        excerpt = str(unit.get("text_excerpt") or "").strip()[:240]
        title = str(unit.get("title") or "").strip()[:160]
        if not excerpt and not title:
            continue
        result.append(
            {
                "evidence_id": f"anchor-{unit_index:04d}",
                "unit_index": unit_index,
                "title": title or None,
                "text_excerpt": excerpt,
            }
        )
    return result


def _spread(items: list[dict[str, object]], limit: int) -> list[dict[str, object]]:
    if len(items) <= limit:
        return items
    return [items[round(index * (len(items) - 1) / (limit - 1))] for index in range(limit)]


def _heading_level(title: str) -> str:
    if re.match(r"^第.+章", title) or re.match(r"^\d+\s*[^.\d]", title):
        return "chapter"
    if re.match(r"^第.+节", title) or re.match(r"^\d+\.\d+", title):
        return "section"
    return "lesson"


def _clean_title(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" .·…")[:160]


def _searchable_title(value: str) -> str:
    return re.sub(r"[\W_]+", "", _HEADING_PREFIX.sub("", value)).lower()


def _mapping_list(value: object) -> list[Mapping[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _empty_evidence() -> dict[str, object]:
    return {
        "strategy": "sparse_outline",
        "total_unit_count": 0,
        "directory_page_unit_indices": [],
        "scanned_unit_indices": [],
        "toc_entries": [],
        "resolved_ranges": [],
        "anchors": [],
        "printed_to_pdf_offset": None,
        "confidence": "low",
        "issues": ["资料没有可用于目录推断的页面。"],
        "full_page_text_sent": False,
    }


__all__ = [
    "DirectoryEvidenceBuilder",
    "build_directory_evidence",
    "evidence_character_estimate",
]
