from __future__ import annotations

import re
from typing import Any

from session_manager import _canonical_question_id


_SOURCE_WIDTH_KEYS = ("source_image_width", "template_image_width", "image_width")
_SOURCE_HEIGHT_KEYS = ("source_image_height", "template_image_height", "image_height")
_PAGE_ORDER = {"front": 0, "back": 1}


def build_major_evidence_groups(
    parent_id: str,
    part_ids: list[str],
    regions: list[dict],
    overlap_threshold: float = 0.90,
) -> list[dict]:
    ordered_part_ids = [str(part_id).strip() for part_id in part_ids if str(part_id).strip()]
    if not ordered_part_ids:
        ordered_part_ids = [str(parent_id).strip()]

    normalized_part_ids = {_normalize_part_id(part_id): part_id for part_id in ordered_part_ids}
    normalized_parent_id = _normalize_parent_id(parent_id)

    relevant_regions = [
        dict(region)
        for region in regions or []
        if _region_page(region) in _PAGE_ORDER and _is_relevant_region(region, normalized_parent_id, normalized_part_ids)
    ]
    if not relevant_regions:
        raise ValueError(f"No regions found for major question {parent_id}")

    explicit_regions_by_part: dict[str, list[dict[str, Any]]] = {part_id: [] for part_id in ordered_part_ids}
    parent_regions: list[dict[str, Any]] = []

    for region in relevant_regions:
        normalized_region_id = _normalize_region_id(region)
        matched_part_id = normalized_part_ids.get(normalized_region_id)
        if matched_part_id is not None:
            explicit_regions_by_part[matched_part_id].append(region)
        elif normalized_region_id == normalized_parent_id:
            parent_regions.append(region)

    unresolved_part_ids = [part_id for part_id in ordered_part_ids if not explicit_regions_by_part[part_id]]
    seed_groups: list[dict[str, Any]] = []

    for part_id in ordered_part_ids:
        for region in explicit_regions_by_part[part_id]:
            seed_groups.append(
                {
                    "page": _region_page(region),
                    "bbox": _bbox_from_region(region),
                    "part_ids": [part_id],
                }
            )

    parent_part_ids = unresolved_part_ids if any(explicit_regions_by_part.values()) else list(ordered_part_ids)
    for region in parent_regions:
        if parent_part_ids:
            seed_groups.append(
                {
                    "page": _region_page(region),
                    "bbox": _bbox_from_region(region),
                    "part_ids": list(parent_part_ids),
                }
            )

    if not seed_groups:
        raise ValueError(f"No usable regions found for major question {parent_id}")

    groups_with_order: list[dict[str, Any]] = []
    for seed_index, seed_group in enumerate(seed_groups):
        item = dict(seed_group)
        item["_seed_index"] = seed_index
        groups_with_order.append(item)

    by_page: dict[str, list[dict[str, Any]]] = {}
    for group in groups_with_order:
        by_page.setdefault(str(group["page"]), []).append(group)

    merged_groups: list[dict[str, Any]] = []
    for page in ("front", "back"):
        page_groups = by_page.get(page, [])
        if not page_groups:
            continue
        for component in _cluster_page_groups(page_groups, overlap_threshold):
            component = sorted(component, key=lambda item: item["_seed_index"])
            merged_groups.append(
                {
                    "page": page,
                    "bbox": _union_bbox([item["bbox"] for item in component]),
                    "part_ids": _ordered_part_union(component, ordered_part_ids),
                    "_seed_index": component[0]["_seed_index"],
                }
            )

    merged_groups.sort(key=lambda item: (_PAGE_ORDER.get(str(item["page"]), 99), item["_seed_index"]))
    return [
        {
            "page": item["page"],
            "bbox": item["bbox"],
            "part_ids": item["part_ids"],
        }
        for item in merged_groups
    ]


def _cluster_page_groups(page_groups: list[dict[str, Any]], overlap_threshold: float) -> list[list[dict[str, Any]]]:
    components: list[list[dict[str, Any]]] = []
    visited: set[int] = set()

    for index in range(len(page_groups)):
        if index in visited:
            continue
        stack = [index]
        component_indexes: list[int] = []
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            component_indexes.append(current)
            for neighbor in range(len(page_groups)):
                if neighbor in visited:
                    continue
                if _regions_overlap(page_groups[current]["bbox"], page_groups[neighbor]["bbox"], overlap_threshold):
                    stack.append(neighbor)
        components.append([page_groups[item_index] for item_index in component_indexes])
    return components


def _ordered_part_union(groups: list[dict[str, Any]], ordered_part_ids: list[str]) -> list[str]:
    seen = {
        str(part_id)
        for group in groups
        for part_id in group.get("part_ids", [])
    }
    return [part_id for part_id in ordered_part_ids if part_id in seen]


def _regions_overlap(left: dict[str, Any], right: dict[str, Any], overlap_threshold: float) -> bool:
    if _same_geometry(left, right):
        return True

    intersection_area = _intersection_area(left, right)
    if intersection_area <= 0:
        return False

    smaller_area = min(_area(left), _area(right))
    if smaller_area <= 0:
        return False
    return (intersection_area / smaller_area) >= float(overlap_threshold)


def _same_geometry(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return (
        _number_value(left.get("x")) == _number_value(right.get("x"))
        and _number_value(left.get("y")) == _number_value(right.get("y"))
        and _number_value(left.get("w")) == _number_value(right.get("w"))
        and _number_value(left.get("h")) == _number_value(right.get("h"))
    )


def _intersection_area(left: dict[str, Any], right: dict[str, Any]) -> float:
    left_x = _number_value(left.get("x"))
    left_y = _number_value(left.get("y"))
    left_w = max(0.0, _number_value(left.get("w")))
    left_h = max(0.0, _number_value(left.get("h")))
    right_x = _number_value(right.get("x"))
    right_y = _number_value(right.get("y"))
    right_w = max(0.0, _number_value(right.get("w")))
    right_h = max(0.0, _number_value(right.get("h")))

    overlap_w = min(left_x + left_w, right_x + right_w) - max(left_x, right_x)
    overlap_h = min(left_y + left_h, right_y + right_h) - max(left_y, right_y)
    if overlap_w <= 0 or overlap_h <= 0:
        return 0.0
    return overlap_w * overlap_h


def _area(region: dict[str, Any]) -> float:
    return max(0.0, _number_value(region.get("w"))) * max(0.0, _number_value(region.get("h")))


def _union_bbox(regions: list[dict[str, Any]]) -> dict[str, Any]:
    left = min(_number_value(region.get("x")) for region in regions)
    top = min(_number_value(region.get("y")) for region in regions)
    right = max(_number_value(region.get("x")) + _number_value(region.get("w")) for region in regions)
    bottom = max(_number_value(region.get("y")) + _number_value(region.get("h")) for region in regions)

    bbox = {
        "x": _simplify_number(left),
        "y": _simplify_number(top),
        "w": _simplify_number(right - left),
        "h": _simplify_number(bottom - top),
    }
    for key in (*_SOURCE_WIDTH_KEYS, *_SOURCE_HEIGHT_KEYS):
        value = _first_present(regions, key)
        if value is not None:
            bbox[key] = value
    return bbox


def _bbox_from_region(region: dict[str, Any]) -> dict[str, Any]:
    bbox = {
        "x": _simplify_number(_number_value(region.get("x"))),
        "y": _simplify_number(_number_value(region.get("y"))),
        "w": _simplify_number(_number_value(region.get("w"))),
        "h": _simplify_number(_number_value(region.get("h"))),
    }
    for key in (*_SOURCE_WIDTH_KEYS, *_SOURCE_HEIGHT_KEYS):
        if key in region and region.get(key) is not None:
            bbox[key] = region.get(key)
    return bbox


def _first_present(regions: list[dict[str, Any]], key: str) -> Any:
    for region in regions:
        if key in region and region.get(key) is not None:
            return region.get(key)
    return None


def _is_relevant_region(region: dict[str, Any], normalized_parent_id: str, normalized_part_ids: dict[str, str]) -> bool:
    normalized_region_id = _normalize_region_id(region)
    return normalized_region_id == normalized_parent_id or normalized_region_id in normalized_part_ids


def _normalize_region_id(region: dict[str, Any]) -> str:
    raw_value = region.get("mapped_question_id") or region.get("detected_question_id")
    text = str(raw_value or "").strip()
    if _looks_like_part_id(text):
        return _normalize_part_id(text)
    return _normalize_parent_id(text)


def _normalize_part_id(value: str) -> str:
    text = str(value or "").strip()
    match = re.match(
        r"^(?:Q|q)?\s*(\d+)\s*[-_]\s*[Pp]?\s*(\d+)$",
        text,
    )
    if not match:
        match = re.match(
            r"^(?:Q|q)?\s*(\d+)\s*[\(（]\s*[Pp]?\s*(\d+)\s*[\)）]$",
            text,
        )
    if match:
        return f"{int(match.group(1))}-{int(match.group(2))}"
    return _normalize_parent_id(text)


def _normalize_parent_id(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return _canonical_question_id(text, text)


def _looks_like_part_id(value: str) -> bool:
    text = str(value or "").strip()
    return bool(
        re.match(
            r"^(?:Q|q)?\s*\d+\s*[-_]\s*[Pp]?\s*\d+$",
            text,
        )
        or re.match(
            r"^(?:Q|q)?\s*\d+\s*[\(（]\s*[Pp]?\s*\d+\s*[\)）]$",
            text,
        )
    )


def _region_page(region: dict[str, Any]) -> str:
    return "back" if str(region.get("page") or "front").strip().lower() == "back" else "front"


def _number_value(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _simplify_number(value: float) -> int | float:
    rounded = round(float(value))
    if abs(float(value) - rounded) <= 1e-9:
        return int(rounded)
    return float(value)
