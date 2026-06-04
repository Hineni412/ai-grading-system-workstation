from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


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
) -> tuple[Path, Path]:
    output_front.parent.mkdir(parents=True, exist_ok=True)
    output_back.parent.mkdir(parents=True, exist_ok=True)

    with Image.open(front_image) as src_front:
        img_front = src_front.convert("RGB")
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
        x = int(region.get("x", 0))
        y = int(region.get("y", 0))
        w = int(region.get("w", 0))
        h = int(region.get("h", 0))
        qid = str(region.get("mapped_question_id") or region.get("detected_question_id") or "")

        score_item = question_scores.get(qid, {})
        awarded = score_item.get("score_awarded")
        full = score_item.get("max_score")
        reason = str(score_item.get("deduction_reason") or "")
        question_type = str(score_item.get("question_type") or "")
        deduction = _deduction_amount(awarded, full)

        if annotate_only_deductions and deduction <= 0:
            continue

        draw.rectangle([x, y, x + w, y + h], outline=(255, 0, 0), width=line_width)

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
