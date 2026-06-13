from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image


@dataclass(frozen=True)
class PreviewDensity:
    name: str
    body_font_rem: float
    line_height: float
    answer_font_rem: float
    header_font_rem: float
    teacher_box_font_rem: float
    separator_margin_px: int


COMPACT_DENSITY = PreviewDensity(
    name="紧凑",
    body_font_rem=0.96,
    line_height=1.62,
    answer_font_rem=0.92,
    header_font_rem=0.98,
    teacher_box_font_rem=0.88,
    separator_margin_px=10,
)

COMFORTABLE_DENSITY = PreviewDensity(
    name="舒适",
    body_font_rem=1.05,
    line_height=1.8,
    answer_font_rem=1.0,
    header_font_rem=1.05,
    teacher_box_font_rem=0.92,
    separator_margin_px=15,
)


def resolve_preview_density(value: object) -> PreviewDensity:
    text = str(value or "").strip()
    if text == COMFORTABLE_DENSITY.name:
        return COMFORTABLE_DENSITY
    return COMPACT_DENSITY


def image_display_width(path: str | Path, *, image_count: int, scale_percent: int = 90) -> int:
    base_width = _base_image_width(path, image_count=image_count)
    scale = max(70, min(130, int(scale_percent or 90))) / 100
    return max(80, int(round(base_width * scale)))


def _base_image_width(path: str | Path, *, image_count: int) -> int:
    if image_count > 1:
        if image_count == 2:
            base = 280
        elif image_count == 3:
            base = 220
        else:
            base = 150

        image_path = Path(path)
        try:
            with Image.open(image_path) as image:
                width, _ = image.size
                if width > 0:
                    return min(base, width)
        except Exception:
            pass
        return base

    image_path = Path(path)
    try:
        with Image.open(image_path) as image:
            width, height = image.size
    except Exception:
        return 320

    if height <= 0:
        return 320
    aspect_ratio = width / height
    if aspect_ratio >= 2.2:
        return 520
    return 320


__all__ = [
    "PreviewDensity",
    "image_display_width",
    "resolve_preview_density",
]
