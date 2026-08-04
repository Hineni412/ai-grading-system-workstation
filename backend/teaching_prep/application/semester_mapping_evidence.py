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
_MAX_TOC_ENTRIES = 160
_MAX_ANCHORS = 28


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
        entries = _toc_entries(units[:scanned_count])
        while (
            entries
            and scanned_count < maximum_count
            and _toc_continues_near_end(entries, scanned_count)
        ):
            scanned_count = min(maximum_count, scanned_count + 5)
            entries = _toc_entries(units[:scanned_count])

        offsets = _calibration_offsets(entries, units, scanned_count)
        calibrated_offset = round(median(offsets)) if offsets else None
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

        confidence = "low"
        strategy = "sparse_outline"
        if entries:
            strategy = "toc_unverified"
            confidence = "medium"
        if entries and calibrated_offset is not None and len(offsets) >= 2:
            spread = max(offsets) - min(offsets)
            if spread <= 2:
                strategy = "toc_calibrated"
                confidence = "high"
            else:
                issues.append("不同目录锚点的页码偏移不一致，映射范围需要重点复核。")

        return {
            "strategy": strategy,
            "total_unit_count": len(units),
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


def _toc_entries(units: list[Mapping[str, object]]) -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    for unit in units:
        text = "\n".join(
            part for part in (
                str(unit.get("title") or "").strip(),
                str(unit.get("text_excerpt") or "").strip(),
            ) if part
        )
        for line in text.splitlines():
            match = _TOC_LINE.match(line)
            if match is None:
                continue
            title = _clean_title(match.group("title"))
            page = int(match.group("page"))
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
    return found


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


def _resolved_ranges(
    entries: list[dict[str, object]],
    *,
    total_units: int,
    offset: int | None,
) -> list[dict[str, object]]:
    if offset is None:
        return []
    resolved: list[dict[str, object]] = []
    starts = [max(1, min(total_units, int(item["printed_page"]) + offset)) for item in entries]
    for index, entry in enumerate(entries):
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
