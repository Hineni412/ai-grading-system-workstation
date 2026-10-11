from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from backend.answer_regions.answer_region_geometry import scaled_region_bbox
from backend.question_id_contract import question_id_coordinates, resolve_known_question_id


class AnnotationCoverageError(ValueError):
    """Raised when a scored question cannot be placed on either paper page."""


def render_annotated_paper(
    front_image: Path,
    back_image: Path,
    regions: list[dict[str, Any]],
    question_scores: dict[str, dict[str, Any]],
    output_front: Path,
    output_back: Path,
    *,
    question_anchors: dict[str, dict[str, Any]] | None = None,
) -> tuple[Path, Path]:
    output_front.parent.mkdir(parents=True, exist_ok=True)
    output_back.parent.mkdir(parents=True, exist_ok=True)

    score_box_anchors = _resolve_score_box_anchors(
        regions,
        question_scores,
        question_anchors or {},
    )

    with Image.open(front_image) as src_front:
        img_front = _render_question_score_boxes(
            src_front.convert("RGB"),
            page="front",
            question_scores=question_scores,
            question_anchors=score_box_anchors,
        )
        _save_rgb_image(img_front, output_front)

    with Image.open(back_image) as src_back:
        img_back = _render_question_score_boxes(
            src_back.convert("RGB"),
            page="back",
            question_scores=question_scores,
            question_anchors=score_box_anchors,
        )
        _save_rgb_image(img_back, output_back)

    return output_front, output_back


def _resolve_score_box_anchors(
    regions: list[dict[str, Any]],
    question_scores: dict[str, dict[str, Any]],
    supplied_anchors: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    expected_ids = [
        str(question_id)
        for question_id, score in question_scores.items()
        if score.get("score_awarded") is not None
    ]
    resolved: dict[str, dict[str, Any]] = {}

    for raw_question_id, anchor in supplied_anchors.items():
        question_id = _resolve_parent_score_id(raw_question_id, expected_ids)
        if question_id is None or not _usable_anchor(anchor):
            continue
        resolved[question_id] = dict(anchor)

    fallback_candidates: list[tuple[str, dict[str, Any]]] = []
    for region in regions:
        raw_question_id = str(
            region.get("mapped_question_id")
            or region.get("detected_question_id")
            or ""
        ).strip()
        question_id = _resolve_parent_score_id(raw_question_id, expected_ids)
        if question_id is None or not _usable_anchor(region):
            continue
        candidate = dict(region)
        candidate["kind"] = "region"
        fallback_candidates.append((question_id, candidate))

    fallback_candidates.sort(
        key=lambda item: (
            1 if str(item[1].get("page") or "front") == "back" else 0,
            _numeric_anchor_value(item[1].get("y")),
            _numeric_anchor_value(item[1].get("x")),
        )
    )
    for question_id, candidate in fallback_candidates:
        resolved.setdefault(question_id, candidate)

    missing = [question_id for question_id in expected_ids if question_id not in resolved]
    if missing:
        raise AnnotationCoverageError(
            "以下已评分题目未找到可用的印刷题号或题框锚点，"
            f"已停止导出以避免静默漏标：{', '.join(missing)}"
        )
    return resolved


def _resolve_parent_score_id(
    raw_question_id: object,
    expected_ids: list[str],
) -> str | None:
    resolved = resolve_known_question_id(raw_question_id, expected_ids)
    if resolved is not None:
        return resolved
    coordinates = question_id_coordinates(raw_question_id)
    if coordinates is None:
        return None
    parent_id = f"Q{coordinates[0]}"
    return parent_id if parent_id in expected_ids else None


def _render_question_score_boxes(
    source: Image.Image,
    *,
    page: str,
    question_scores: dict[str, dict[str, Any]],
    question_anchors: dict[str, dict[str, Any]],
) -> Image.Image:
    canvas = source.copy()
    draw = ImageDraw.Draw(canvas)
    width, height = canvas.size
    scale_base = max(1, min(width, height))
    safe_x = max(25, int(round(width * 0.025)))
    safe_y = max(25, int(round(height * 0.018)))
    font_size = max(14, min(30, int(round(scale_base * 0.018))))
    font = _load_font(font_size)
    pad_x = max(6, int(round(font_size * 0.42)))
    pad_y = max(4, int(round(font_size * 0.28)))
    gap = max(6, int(round(font_size * 0.38)))
    line_width = max(2, int(round(scale_base * 0.002)))
    occupied: list[tuple[int, int, int, int]] = []

    page_items = [
        (question_id, anchor)
        for question_id, anchor in question_anchors.items()
        if str(anchor.get("page") or "front").strip().lower() == page
    ]
    page_items.sort(
        key=lambda item: (
            _numeric_anchor_value(item[1].get("y")),
            _numeric_anchor_value(item[1].get("x")),
            item[0],
        )
    )

    for question_id, anchor in page_items:
        score_item = question_scores[question_id]
        label = _score_only_label(score_item)
        text_box = draw.textbbox((0, 0), label, font=font)
        text_width = max(1, text_box[2] - text_box[0])
        text_height = max(1, text_box[3] - text_box[1])
        box_width = text_width + pad_x * 2
        box_height = text_height + pad_y * 2
        anchor_left, anchor_top, anchor_right, anchor_bottom = scaled_region_bbox(
            anchor,
            width,
            height,
        )

        right = anchor_left - gap
        left = right - box_width
        place_above = (
            str(anchor.get("kind") or "").strip().lower() == "region"
        )
        if left < safe_x:
            left = max(
                safe_x,
                min(anchor_left, width - safe_x - box_width),
            )
            right = left + box_width
            place_above = True
        if right > width - safe_x:
            left = max(
                safe_x,
                min(anchor_left, width - safe_x - box_width),
            )
            right = left + box_width
            place_above = True

        if place_above:
            top = anchor_top - box_height - gap
            if top < safe_y:
                top = anchor_bottom + gap
        else:
            anchor_height = max(1, anchor_bottom - anchor_top)
            top = anchor_top + anchor_height // 2 - box_height // 2
        top = max(safe_y, min(int(top), height - safe_y - box_height))
        box = _avoid_score_box_collisions(
            (int(left), int(top), int(right), int(top + box_height)),
            occupied,
            safe_y=safe_y,
            max_bottom=height - safe_y,
            gap=gap,
        )
        occupied.append(box)
        left, top, right, bottom = box

        draw.rounded_rectangle(
            box,
            radius=max(3, pad_y),
            fill=(255, 255, 255),
            outline=(198, 32, 32),
            width=line_width,
        )
        draw.text(
            (
                left + pad_x,
                top + (bottom - top - text_height) // 2 - text_box[1],
            ),
            label,
            fill=(155, 18, 18),
            font=font,
        )
    return canvas


def _avoid_score_box_collisions(
    box: tuple[int, int, int, int],
    occupied: list[tuple[int, int, int, int]],
    *,
    safe_y: int,
    max_bottom: int,
    gap: int,
) -> tuple[int, int, int, int]:
    left, top, right, bottom = box
    height = bottom - top
    for prior in occupied:
        if not _boxes_overlap((left, top, right, bottom), prior, gap=gap):
            continue
        top = prior[3] + gap
        bottom = top + height
    if bottom <= max_bottom:
        return left, top, right, bottom

    top = max(safe_y, max_bottom - height)
    bottom = top + height
    for prior in reversed(occupied):
        if not _boxes_overlap((left, top, right, bottom), prior, gap=gap):
            continue
        bottom = prior[1] - gap
        top = bottom - height
    return left, max(safe_y, top), right, max(safe_y, top) + height


def _boxes_overlap(
    first: tuple[int, int, int, int],
    second: tuple[int, int, int, int],
    *,
    gap: int,
) -> bool:
    return not (
        first[2] + gap <= second[0]
        or second[2] + gap <= first[0]
        or first[3] + gap <= second[1]
        or second[3] + gap <= first[1]
    )


def _score_only_label(score_item: dict[str, Any]) -> str:
    awarded = score_item.get("score_awarded")
    full = score_item.get("max_score")
    if awarded is None or full is None:
        raise AnnotationCoverageError("已评分题目的得分或满分缺失，无法生成得分框。")
    return f"{_format_score(awarded)}/{_format_score(full)}"


def _usable_anchor(anchor: dict[str, Any]) -> bool:
    if str(anchor.get("page") or "front").strip().lower() not in {"front", "back"}:
        return False
    return (
        _numeric_anchor_value(anchor.get("w")) > 0
        and _numeric_anchor_value(anchor.get("h")) > 0
    )


def _numeric_anchor_value(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _format_score(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:g}"


def _save_rgb_image(image: Image.Image, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if image.mode != "RGB":
        image = image.convert("RGB")
    image.save(output_path, quality=95)


def _load_font(size: int = 18) -> ImageFont.ImageFont:
    candidates = [
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simsun.ttc",
        "C:/Windows/Fonts/arial.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, ValueError):
            continue
    return ImageFont.load_default()
