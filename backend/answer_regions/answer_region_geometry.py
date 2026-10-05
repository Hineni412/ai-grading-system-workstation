from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from PIL import Image

from path_manager import resolve_stored_file_path

_SOURCE_WIDTH_KEYS = ("source_image_width", "template_image_width", "image_width")
_SOURCE_HEIGHT_KEYS = ("source_image_height", "template_image_height", "image_height")


def scaled_region_bbox(
    region: dict[str, Any],
    image_width: int,
    image_height: int,
    *,
    padding: int = 0,
) -> tuple[int, int, int, int]:
    x = _int_value(region.get("x"))
    y = _int_value(region.get("y"))
    w = _int_value(region.get("w"))
    h = _int_value(region.get("h"))
    if w <= 0 or h <= 0:
        return (0, 0, max(1, int(image_width)), max(1, int(image_height)))

    source_width = _first_positive_float(region, _SOURCE_WIDTH_KEYS)
    source_height = _first_positive_float(region, _SOURCE_HEIGHT_KEYS)
    if source_width and source_height:
        scale_x = int(image_width) / source_width
        scale_y = int(image_height) / source_height
        left_value = int(round(x * scale_x))
        top_value = int(round(y * scale_y))
        right_value = int(round((x + w) * scale_x))
        bottom_value = int(round((y + h) * scale_y))
    else:
        left_value = x
        top_value = y
        right_value = x + w
        bottom_value = y + h

    pad = max(0, int(padding))
    left = _clamp(left_value - pad, 0, int(image_width) - 1)
    top = _clamp(top_value - pad, 0, int(image_height) - 1)
    right = _clamp(right_value + pad, left + 1, int(image_width))
    bottom = _clamp(bottom_value + pad, top + 1, int(image_height))
    return (left, top, right, bottom)


def attach_region_source_image_sizes(
    regions: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    page_sizes: dict[str, tuple[int, int]],
) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for region in regions or []:
        item = dict(region)
        page = _region_page(item)
        size = page_sizes.get(page)
        if size:
            item["source_image_width"] = int(size[0])
            item["source_image_height"] = int(size[1])
        enriched.append(item)
    return enriched


def answer_regions_with_template_source_sizes(
    db: Any,
    session_id: int,
    *,
    data_root: Path | None = None,
) -> list[dict[str, Any]]:
    regions = db.templates.list_answer_regions(session_id)
    template = db.templates.get_session_template(session_id)
    if not template:
        return [dict(region) for region in regions]

    page_sizes: dict[str, tuple[int, int]] = {}
    for page in ("front", "back"):
        path = resolve_stored_file_path(template.get(f"{page}_template_path"), data_root=data_root)
        size = _image_size(path)
        if size:
            page_sizes[page] = size
    if not page_sizes:
        return [dict(region) for region in regions]
    return attach_region_source_image_sizes(regions, page_sizes)


def _image_size(path: Path) -> tuple[int, int] | None:
    try:
        with Image.open(path) as image:
            return int(image.width), int(image.height)
    except (OSError, ValueError, Image.DecompressionBombError):
        return None


def _region_page(region: dict[str, Any]) -> str:
    return "back" if str(region.get("page") or "front").strip().lower() == "back" else "front"


def _first_positive_float(region: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        number = _positive_float(region.get(key))
        if number is not None:
            return number
    return None


def _positive_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def _int_value(value: Any, default: int = 0) -> int:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return default


def _clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(value, maximum))
