from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any

from PIL import Image

from llm_client import LLMClient


def detect_answer_regions_llm(
    image_path: Path,
    page: str,
    question_ids: list[str],
    llm_client: LLMClient,
    ocr_model: str | None = None,
) -> list[dict[str, Any]]:
    """Send the full-page template image to LLM and ask it to locate all answer regions.

    Returns a list of region dicts compatible with the answer_regions DB schema.
    """
    img = Image.open(image_path).convert("RGB")
    orig_w, orig_h = img.size

    # Encode image as JPEG bytes
    import io
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    img_bytes = buf.getvalue()

    qid_list_str = ", ".join(question_ids) if question_ids else "（请根据图片自行判断）"

    prompt = f"""你是一个试卷版式分析专家。请分析以下答题模板图片（{page}面），找出所有答题区域（即供学生书写答案的矩形框）。

已知该试卷包含的题号列表：{qid_list_str}

要求：
1. 识别图片中每个答题框的像素坐标（左上角 x, y，宽度 w，高度 h）
2. 将每个框与对应题号匹配（题号来自上方列表）
3. 图片原始尺寸为 {orig_w} x {orig_h} 像素，所有坐标为绝对像素值
4. 只输出 JSON 数组，格式如下（不要输出任何其他内容）：

[
  {{"question_id": "1", "x": 120, "y": 300, "w": 800, "h": 250}},
  {{"question_id": "2", "x": 120, "y": 580, "w": 800, "h": 300}}
]

注意：
- x, y 为框的左上角坐标（像素）
- w 为框的宽度，h 为框的高度
- question_id 必须来自已知题号列表（若无法匹配则填 null）
- 若某题号找不到对应框，可以跳过
- 若某框无法识别题号，question_id 填 null
"""

    try:
        raw = llm_client.json_from_images(
            prompt,
            [img_bytes],
            model=ocr_model,
        )
    except Exception:
        # Fallback: try text extraction with JSON parsing
        try:
            text = llm_client.text_from_images(prompt, [img_bytes], model=ocr_model)
            raw = _extract_json_list(text)
        except Exception:
            return []

    if not isinstance(raw, list):
        if isinstance(raw, dict):
            # Maybe wrapped: {"regions": [...]}
            for v in raw.values():
                if isinstance(v, list):
                    raw = v
                    break
            else:
                return []
        else:
            return []

    regions: list[dict[str, Any]] = []
    for idx, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            continue
        try:
            x = int(item.get("x", 0))
            y = int(item.get("y", 0))
            w = int(item.get("w", 0))
            h = int(item.get("h", 0))
        except (TypeError, ValueError):
            continue

        # Sanity check
        if w < 10 or h < 10:
            continue
        if x < 0 or y < 0 or x + w > orig_w * 1.05 or y + h > orig_h * 1.05:
            # Clamp rather than skip
            x = max(0, min(x, orig_w - 1))
            y = max(0, min(y, orig_h - 1))
            w = min(w, orig_w - x)
            h = min(h, orig_h - y)
            if w < 10 or h < 10:
                continue

        qid_raw = item.get("question_id")
        qid = str(qid_raw).strip() if qid_raw is not None else None
        if not qid or qid.lower() in ("null", "none", ""):
            qid = None

        regions.append(
            {
                "page": page,
                "region_order": idx,
                "x": x,
                "y": y,
                "w": w,
                "h": h,
                "detected_question_id": qid,
                "mapped_question_id": qid,
                "confidence": 0.85 if qid else 0.3,
                "is_confirmed": False,
            }
        )

    return regions


def _extract_json_list(text: str) -> list:
    """Try to extract a JSON array from LLM free-text response."""
    # Look for [...] block
    match = re.search(r"\[[\s\S]*?\]", text)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            pass
    return []


# ---------------------------------------------------------------------------
# Legacy CV-based detection kept as fallback (now unused by default)
# ---------------------------------------------------------------------------

def detect_answer_regions(
    image_path: Path,
    page: str,
    llm_client: LLMClient | None = None,
    ocr_model: str | None = None,
    min_area_ratio: float = 0.002,
) -> list[dict[str, Any]]:
    """CV contour-based detection (legacy fallback). Prefer detect_answer_regions_llm."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return []

    img = cv2.imread(str(image_path))
    if img is None:
        raise FileNotFoundError(f"无法读取模板图片: {image_path}")

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    edges = cv2.Canny(blur, 80, 180)

    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    edges = cv2.dilate(edges, kernel, iterations=1)

    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    h_img, w_img = gray.shape
    min_area = h_img * w_img * float(min_area_ratio)

    boxes: list[tuple[int, int, int, int]] = []
    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)
        area = w * h
        if area < min_area:
            continue
        if w < 40 or h < 20:
            continue
        ratio = w / max(h, 1)
        if ratio < 0.5 or ratio > 25:
            continue

        peri = cv2.arcLength(cnt, True)
        approx = cv2.approxPolyDP(cnt, 0.03 * peri, True)
        if len(approx) < 4:
            continue

        boxes.append((x, y, w, h))

    boxes = _dedup_boxes(boxes)
    boxes.sort(key=lambda b: (b[1], b[0]))

    regions: list[dict[str, Any]] = []
    for idx, (x, y, w, h) in enumerate(boxes, start=1):
        regions.append(
            {
                "page": page,
                "region_order": idx,
                "x": int(x),
                "y": int(y),
                "w": int(w),
                "h": int(h),
                "detected_question_id": None,
                "mapped_question_id": None,
                "confidence": 0.0,
                "is_confirmed": False,
            }
        )

    return regions


def _dedup_boxes(boxes: list[tuple[int, int, int, int]]) -> list[tuple[int, int, int, int]]:
    if not boxes:
        return []

    result: list[tuple[int, int, int, int]] = []
    for box in boxes:
        x, y, w, h = box
        keep = True
        for bx, by, bw, bh in result:
            iou = _iou((x, y, w, h), (bx, by, bw, bh))
            if iou > 0.75:
                keep = False
                break
        if keep:
            result.append(box)
    return result


def _iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    ax1, ay1, aw, ah = a
    bx1, by1, bw, bh = b
    ax2, ay2 = ax1 + aw, ay1 + ah
    bx2, by2 = bx1 + bw, by1 + bh

    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)

    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    union = aw * ah + bw * bh - inter
    if union <= 0:
        return 0.0
    return inter / union
