from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from answer_region_geometry import scaled_region_bbox
from question_id_contract import question_id_coordinates, resolve_known_question_id


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
    annotate_only_deductions: bool = False,
    label_position: str = "right",
    summary_labels: bool = False,
    annotation_layout: str = "overlay",
    outer_margin_scale: float = 0.95,
    question_anchors: dict[str, dict[str, Any]] | None = None,
) -> tuple[Path, Path]:
    output_front.parent.mkdir(parents=True, exist_ok=True)
    output_back.parent.mkdir(parents=True, exist_ok=True)

    score_box_anchors: dict[str, dict[str, Any]] = {}
    if annotation_layout == "question_score_boxes":
        score_box_anchors = _resolve_score_box_anchors(
            regions,
            question_scores,
            question_anchors or {},
        )

    with Image.open(front_image) as src_front:
        img_front = src_front.convert("RGB")
        if annotation_layout == "question_score_boxes":
            img_front = _render_question_score_boxes(
                img_front,
                page="front",
                question_scores=question_scores,
                question_anchors=score_box_anchors,
            )
        elif annotation_layout == "outer_margin":
            img_front = _render_outer_margin_page(
                img_front,
                [r for r in regions if r.get("page") == "front"],
                question_scores,
                side="right",
                scale=outer_margin_scale,
                annotate_only_deductions=annotate_only_deductions,
            )
        else:
            draw_regions_on_image(
                img_front,
                [r for r in regions if r.get("page") == "front"],
                question_scores,
                annotate_only_deductions=annotate_only_deductions,
                label_position=label_position,
                summary_labels=summary_labels,
            )
        _save_rgb_image(img_front, output_front)

    with Image.open(back_image) as src_back:
        img_back = src_back.convert("RGB")
        if annotation_layout == "question_score_boxes":
            img_back = _render_question_score_boxes(
                img_back,
                page="back",
                question_scores=question_scores,
                question_anchors=score_box_anchors,
            )
        elif annotation_layout == "outer_margin":
            img_back = _render_outer_margin_page(
                img_back,
                [r for r in regions if r.get("page") == "back"],
                question_scores,
                side="left",
                scale=outer_margin_scale,
                annotate_only_deductions=annotate_only_deductions,
            )
        else:
            draw_regions_on_image(
                img_back,
                [r for r in regions if r.get("page") == "back"],
                question_scores,
                annotate_only_deductions=annotate_only_deductions,
                label_position=label_position,
                summary_labels=summary_labels,
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


def _render_outer_margin_page(
    source: Image.Image,
    regions: list[dict[str, Any]],
    question_scores: dict[str, dict[str, Any]],
    *,
    side: str,
    scale: float,
    annotate_only_deductions: bool,
) -> Image.Image:
    if side not in {"left", "right"}:
        raise ValueError("outer annotation side must be left or right")
    safe_scale = max(0.90, min(0.98, float(scale)))
    source_width, source_height = source.size
    scaled_width = max(1, round(source_width * safe_scale))
    scaled_height = max(1, round(source_height * safe_scale))
    offset_x = 0 if side == "right" else source_width - scaled_width
    offset_y = max(0, (source_height - scaled_height) // 2)
    resized = source.resize(
        (scaled_width, scaled_height),
        Image.Resampling.LANCZOS,
    )
    canvas = Image.new("RGB", source.size, "white")
    canvas.paste(resized, (offset_x, offset_y))

    draw = ImageDraw.Draw(canvas)
    scale_base = max(1, min(source_width, source_height))
    line_width = max(2, int(scale_base * 0.002))
    marker_length = max(8, int(scale_base * 0.014))
    rail_start = scaled_width if side == "right" else 0
    rail_end = source_width if side == "right" else source_width - scaled_width
    divider_x = rail_start if side == "right" else rail_end
    draw.line(
        (divider_x, 0, divider_x, source_height),
        fill=(210, 145, 145),
        width=max(1, line_width // 2),
    )

    font_size = max(11, min(26, int(scale_base * 0.015)))
    font = _load_font(font_size)
    pad = max(2, int(font_size * 0.18))
    rail_width = max(1, source_width - scaled_width)
    text_width = max(font_size * 2, rail_width - pad * 4)
    annotations: list[dict[str, Any]] = []

    for region in regions:
        qid = str(
            region.get("mapped_question_id")
            or region.get("detected_question_id")
            or ""
        )
        score_item = question_scores.get(qid, {})
        awarded = score_item.get("score_awarded")
        full = score_item.get("max_score")
        deduction = _deduction_amount(awarded, full)
        if annotate_only_deductions and deduction <= 0:
            continue

        x, y, right, bottom = scaled_region_bbox(
            region,
            source_width,
            source_height,
        )
        scaled_box = (
            offset_x + round(x * safe_scale),
            offset_y + round(y * safe_scale),
            offset_x + round(right * safe_scale),
            offset_y + round(bottom * safe_scale),
        )
        sx, sy, sright, sbottom = scaled_box
        marker_y = max(0, min(source_height - 1, sy))
        if side == "right":
            marker_x = max(0, min(scaled_width - 1, sright))
            draw.line(
                (
                    marker_x - marker_length,
                    marker_y,
                    marker_x,
                    marker_y,
                ),
                fill=(196, 34, 34),
                width=line_width,
            )
            draw.line(
                (
                    marker_x,
                    marker_y,
                    marker_x,
                    min(source_height - 1, marker_y + marker_length),
                ),
                fill=(196, 34, 34),
                width=line_width,
            )
        else:
            marker_x = max(offset_x, min(source_width - 1, sx))
            draw.line(
                (
                    marker_x,
                    marker_y,
                    marker_x + marker_length,
                    marker_y,
                ),
                fill=(196, 34, 34),
                width=line_width,
            )
            draw.line(
                (
                    marker_x,
                    marker_y,
                    marker_x,
                    min(source_height - 1, marker_y + marker_length),
                ),
                fill=(196, 34, 34),
                width=line_width,
            )

        label = _outer_margin_label(qid, score_item)
        lines = _wrap_label(label, font, text_width)
        boxes = [draw.textbbox((0, 0), line, font=font) for line in lines]
        line_height = max(
            (box[3] - box[1] for box in boxes),
            default=font_size,
        )
        line_gap = max(1, int(font_size * 0.12))
        card_height = (
            len(lines) * line_height
            + max(0, len(lines) - 1) * line_gap
            + pad * 2
        )
        annotations.append(
            {
                "target_y": max(
                    0,
                    min(
                        source_height - 1,
                        (sy + sbottom) // 2,
                    ),
                ),
                "lines": lines,
                "line_height": line_height,
                "line_gap": line_gap,
                "card_height": card_height,
            }
        )

    annotations.sort(key=lambda item: int(item["target_y"]))
    gap = max(3, int(font_size * 0.25))
    cursor_y = pad
    for annotation in annotations:
        card_height = int(annotation["card_height"])
        desired_y = int(annotation["target_y"]) - card_height // 2
        top = max(cursor_y, desired_y)
        top = min(top, max(pad, source_height - card_height - pad))
        cursor_y = top + card_height + gap
        left = (
            scaled_width + pad
            if side == "right"
            else pad
        )
        right = (
            source_width - pad
            if side == "right"
            else rail_width - pad
        )
        if right <= left:
            right = left + 1
        draw.rounded_rectangle(
            (left, top, right, top + card_height),
            radius=max(2, pad),
            fill=(255, 244, 244),
            outline=(210, 85, 85),
            width=max(1, line_width // 2),
        )
        target_y = int(annotation["target_y"])
        if side == "right":
            draw.line(
                (divider_x, target_y, left, target_y),
                fill=(196, 34, 34),
                width=max(1, line_width // 2),
            )
        else:
            draw.line(
                (right, target_y, divider_x, target_y),
                fill=(196, 34, 34),
                width=max(1, line_width // 2),
            )
        text_y = top + pad
        for line in annotation["lines"]:
            draw.text(
                (left + pad, text_y),
                line,
                fill=(150, 20, 20),
                font=font,
            )
            text_y += int(annotation["line_height"]) + int(
                annotation["line_gap"]
            )

    return canvas


def _outer_margin_label(
    question_id: str,
    score_item: dict[str, Any],
) -> str:
    awarded = score_item.get("score_awarded")
    full = score_item.get("max_score")
    if awarded is not None and full is not None:
        score_text = (
            f"{question_id} "
            f"{_format_score(awarded)}/{_format_score(full)}"
        )
    elif awarded is not None:
        score_text = f"{question_id} {_format_score(awarded)}"
    else:
        score_text = question_id or "未映射"
    reason = _compact_reason(
        str(score_item.get("deduction_reason") or "")
    )
    if not reason:
        return score_text
    return f"{score_text} {reason[:12]}"


def draw_regions_on_image(
    image: Image.Image,
    regions: list[dict[str, Any]],
    question_scores: dict[str, dict[str, Any]],
    *,
    annotate_only_deductions: bool = False,
    label_position: str = "right",
    summary_labels: bool = False,
) -> None:
    draw = ImageDraw.Draw(image)
    scale_base = max(1, min(image.width, image.height))
    if annotate_only_deductions:
        font_size = max(18, min(42, int(scale_base * 0.024)))
    elif summary_labels:
        font_size = max(22, min(48, int(scale_base * 0.0275)))
    else:
        font_size = max(26, min(58, int(scale_base * 0.038)))
    font = _load_font(font_size)
    pad = max(6, int(font_size * 0.28))
    line_width = max(2, int(scale_base * 0.0025))

    for region in regions:
        x, y, right, bottom = scaled_region_bbox(region, image.width, image.height)
        w = right - x
        h = bottom - y
        qid = str(region.get("mapped_question_id") or region.get("detected_question_id") or "")

        score_item = question_scores.get(qid, {})
        awarded = score_item.get("score_awarded")
        full = score_item.get("max_score")
        reason = str(score_item.get("deduction_reason") or "")
        question_type = str(score_item.get("question_type") or "")
        deduction = _deduction_amount(awarded, full)

        if annotate_only_deductions and deduction <= 0:
            continue

        draw.rectangle([x, y, right, bottom], outline=(255, 0, 0), width=line_width)

        if summary_labels:
            if awarded is not None and full is not None:
                label = f"{qid} {_format_score(awarded)}/{_format_score(full)}"
                if deduction > 0 and reason.strip():
                    max_reason_chars = max(10, min(22, int(image.width / max(font_size, 1) * 0.24)))
                    short_reason = reason[:max_reason_chars] + ("..." if len(reason) > max_reason_chars else "")
                    label = f"{label}，{short_reason}"
            elif awarded is not None:
                label = f"{qid} {_format_score(awarded)}"
            else:
                label = qid or "UNMAPPED"
        elif annotate_only_deductions:
            if deduction <= 0:
                continue
            if question_type in {"proof", "calculation", "comprehensive"}:
                label = f"{qid} {_format_score(awarded)}/{_format_score(full)}"
                if reason.strip():
                    label = f"{label}，{_compact_reason(reason)}"
            else:
                label = f"{qid} 扣{_format_score(deduction)}/{_format_score(full)}"
        elif awarded is not None and full is not None:
            main_text = f"{qid} {awarded}/{full}"
            max_reason_chars = max(14, min(32, int(image.width / max(font_size, 1) * 0.42)))
            short_reason = reason[:max_reason_chars] + ("..." if len(reason) > max_reason_chars else "")
            label = main_text if not short_reason else f"{main_text} | {short_reason}"
        elif qid:
            label = qid
        else:
            label = "UNMAPPED"

        max_label_width = max(int(image.width * 0.22), int(font_size * 8))
        lines = _wrap_label(label, font, max_label_width) if annotate_only_deductions else [label]
        line_boxes = [draw.textbbox((0, 0), line, font=font) for line in lines]
        label_w = max((box[2] - box[0] for box in line_boxes), default=0) + pad * 2
        line_height = max((box[3] - box[1] for box in line_boxes), default=font_size)
        line_gap = max(2, int(font_size * 0.18))
        label_h = len(lines) * line_height + max(0, len(lines) - 1) * line_gap + pad * 2
        if label_position == "bottom_right":
            tx = min(max(0, x + w - label_w - 4), max(0, image.width - label_w - 2))
            ty = min(max(0, y + h - label_h - 4), max(0, image.height - label_h - 2))
        else:
            tx = min(max(0, x + w + 8), max(0, image.width - label_w - 2))
            ty = max(0, y)

        draw.rectangle(
            [
                tx - pad,
                ty - pad,
                tx + label_w - pad,
                ty + label_h - pad,
            ],
            fill=None,
            outline=(255, 0, 0),
            width=max(1, line_width // 2),
        )
        cursor_y = ty
        for line in lines:
            draw.text((tx, cursor_y), line, fill=(180, 0, 0), font=font)
            cursor_y += line_height + line_gap


def _deduction_amount(awarded: Any, full: Any) -> float:
    try:
        if awarded is None or full is None:
            return 0.0
        return max(0.0, round(float(full) - float(awarded), 2))
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


def _compact_reason(reason: str) -> str:
    text = " ".join(str(reason or "").replace("\n", " ").split())
    prefixes = ["扣分原因：", "扣分：", "原因："]
    for prefix in prefixes:
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
    return text[:64]


def _wrap_label(text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    text = str(text or "").strip()
    if not text:
        return [""]
    lines: list[str] = []
    current = ""
    for char in text:
        candidate = current + char
        try:
            width = font.getbbox(candidate)[2] - font.getbbox(candidate)[0]
        except Exception:
            width = len(candidate) * 12
        if current and width > max_width:
            lines.append(current)
            current = char
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines[:4]


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
        except Exception:
            continue
    return ImageFont.load_default()
