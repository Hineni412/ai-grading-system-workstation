"""MinerU full-parse entry for scanned PDF import (layout + formula + OCR).

Unlike the line-level text OCR in ``backend/document_parsing/local_ocr.py``/``document_pipeline``,
``mineru.parse(tier="basic")`` runs layout analysis and PP-FormulaNet formula
recognition on top of OCR, returning Markdown with LaTeX (``$...$``) formulas.
All model files must already exist locally; import never triggers downloads.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

LOGGER = logging.getLogger(__name__)

MINERU_MODELS_DIR = (
    Path(__file__).resolve().parents[2]
    / "runtime"
    / "models"
    / "mineru"
    / "MinerU-4_models_onnx"
)

_REQUIRED = (
    "Layout/PP-DocLayoutV2/inference.onnx",
    "Layout/PP-DocLayoutV2/inference.yml",
    "MFR/pp_formulanet_plus_m/PP-FormulaNet_plus-M.onnx",
    "MFR/pp_formulanet_plus_m/PP-FormulaNet_plus-M_inference.yml",
    "OCR/paddleocr/ch_PP-OCRv6_tiny_det_infer.onnx",
    "OCR/paddleocr/ch_PP-OCRv6_tiny_det_inference.yml",
    "OCR/paddleocr/ch_PP-OCRv6_small_rec_infer.onnx",
    "OCR/paddleocr/ch_PP-OCRv6_small_rec_inference.yml",
    "OCR/paddleocr/seal_PP-OCRv4_det_infer.onnx",
    "OCR/paddleocr/seal_PP-OCRv4_det_inference.yml",
    "Table/slanet-plus.onnx",
    "Table/unet.onnx",
    "Table/PP-LCNet_x1_0_table_cls.onnx",
)


def mineru_full_available() -> bool:
    """True only when every model file needed by the basic tier is present."""
    return all((MINERU_MODELS_DIR / name).is_file() for name in _REQUIRED)


def _run_mineru(path: Path):
    """加载模型目录并跑一次 MinerU 完整解析；不可用/失败返回 None。"""
    if not mineru_full_available():
        return None
    # Point MinerU at the pre-bundled model tree and forbid runtime downloads.
    os.environ.setdefault("MINERU_MODEL_BASE_DIR", str(MINERU_MODELS_DIR.parent))
    os.environ.setdefault("MINERU_MODEL_SOURCE", "local")
    try:
        import mineru
        from mineru.config import config
    except Exception:
        LOGGER.warning("MinerU full parse unavailable: mineru import failed", exc_info=True)
        return None
    try:
        if Path(config.model.base_dir).resolve() != MINERU_MODELS_DIR.parent.resolve():
            config.model.base_dir = str(MINERU_MODELS_DIR.parent)
    except Exception:
        LOGGER.warning(
            "MinerU full parse unavailable: cannot set model base_dir to %s",
            MINERU_MODELS_DIR.parent,
            exc_info=True,
        )
        return None
    try:
        return mineru.parse(str(path), tier="basic", ocr_mode="ocr")
    except Exception:
        LOGGER.warning("MinerU full parse failed for %s", path, exc_info=True)
        return None


def parse_pdf_full(path: Path, *, layout_out: dict | None = None) -> str | None:
    """Parse a scanned PDF into Markdown with LaTeX, or None when unavailable."""
    result = _run_mineru(path)
    if result is None:
        return None
    if layout_out is not None:
        _copy_layout(result, layout_out)
    try:
        structured = _render_structured(result)
        if structured:
            return structured
    except Exception:
        LOGGER.warning(
            "MinerU structured render failed for %s; falling back to markdown",
            path,
            exc_info=True,
        )
    try:
        markdown = result.markdown()
        return str(markdown or "").strip() or None
    except Exception:
        LOGGER.warning("MinerU markdown fallback failed for %s", path, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# 结构化渲染：用 MinerU 块的坐标与图注把图片归到正确的题、选项图配字母。
# ---------------------------------------------------------------------------

_QUESTION_START = re.compile(r"^\s*(\d{1,2})\s*[.．、]")
_CAPTION_QUESTION_NO = re.compile(r"第\s*(\d+)\s*题")
_FOOTER_CAPTION = re.compile(r"^(?:第\s*\d+\s*题\s*图?|图\s*\d+)$")
_OPTION_LETTERS = ("A", "B", "C", "D")
_DROP_BLOCK_TYPES = {
    "header",
    "doc_title",
    "page_number",
    "aside_text",
    "page_footnote",
    "index",
    "code",
}
_VISUAL_TYPES = {"image", "chart"}
_CAPTION_SUFFIXES = ("_caption", "_footnote")


@dataclass
class _Record:
    page_idx: int
    order: int
    kind: str  # text | title | table | image | footer_caption
    bbox: tuple[float, float, float, float] | None
    text: str = ""
    image_data: str = ""
    captions: list[str] = field(default_factory=list)
    fallback: _Question | None = None  # 阅读顺序归题兜底


@dataclass
class _Question:
    number: int
    items: list[_Record] = field(default_factory=list)
    images: list[_Record] = field(default_factory=list)
    option_images: dict[str, _Record] = field(default_factory=dict)


def _block_type(block: object) -> str:
    return str(getattr(getattr(block, "type", ""), "value", getattr(block, "type", "")))


def _span_text(block: object) -> str:
    content = getattr(block, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for span in content:
            span_type = str(getattr(span, "type", ""))
            value = getattr(span, "content", "")
            if span_type == "equation_inline":
                parts.append(f"${value}$")
            elif isinstance(value, str):
                parts.append(value)
            elif isinstance(value, list):
                parts.append("".join(str(getattr(s, "content", "")) for s in value))
        return "".join(parts)
    return ""


def _bbox(block: object) -> tuple[float, float, float, float] | None:
    raw = getattr(block, "bbox", None)
    if raw is None or len(raw) != 4:
        return None
    return tuple(float(v) for v in raw)


def _copy_layout(result: object, target: dict) -> None:
    pages: dict[int, list[dict]] = {}
    for record in _collect_records(result):
        if record.bbox and record.text:
            pages.setdefault(record.page_idx, []).append({
                "text": record.text, "bbox": record.bbox,
            })
    target["pages"] = pages


def _collect_records(result: object) -> list[_Record]:
    records: list[_Record] = []
    order = 0
    for page in getattr(result, "pages", []) or []:
        page_idx = int(getattr(page, "page_idx", 0) or 0)
        for block in getattr(page, "blocks", []) or []:
            block_type = _block_type(block)
            bbox = _bbox(block)
            if block_type in _DROP_BLOCK_TYPES:
                continue
            if block_type == "footer":
                text = re.sub(r"\s+", "", _span_text(block))
                if text and _FOOTER_CAPTION.match(text):
                    records.append(
                        _Record(page_idx, order, "footer_caption", bbox, text=text)
                    )
                    order += 1
                continue
            if block_type in ("text", "equation", "ref_text", "list"):
                content = getattr(block, "content", None)
                if block_type == "equation":
                    text = f"$${str(content or '').strip()}$$"
                elif block_type == "list" and isinstance(content, list):
                    text = "\n".join(
                        _span_text(child) for child in content if hasattr(child, "type")
                    )
                else:
                    text = _span_text(block)
                records.append(_Record(page_idx, order, "text", bbox, text=text))
                order += 1
                continue
            if block_type == "paragraph_title":
                records.append(
                    _Record(page_idx, order, "title", bbox, text=_span_text(block))
                )
                order += 1
                continue
            if block_type in _VISUAL_TYPES:
                image_data = ""
                captions: list[str] = []
                for child in getattr(block, "content", []) or []:
                    child_type = _block_type(child)
                    if child_type.endswith("_body"):
                        image_data = str(getattr(child, "image_base64", "") or "")
                    elif child_type.endswith(_CAPTION_SUFFIXES):
                        caption = _span_text(child).strip()
                        if caption:
                            captions.append(re.sub(r"\s+", " ", caption))
                records.append(
                    _Record(
                        page_idx,
                        order,
                        "image",
                        bbox,
                        image_data=image_data,
                        captions=captions,
                    )
                )
                order += 1
                continue
            if block_type == "table":
                text = _table_to_markdown(str(getattr(block, "content", "") or ""))
                records.append(_Record(page_idx, order, "table", bbox, text=text))
                order += 1
                continue
    return records


def _table_to_markdown(html: str) -> str:
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, flags=re.S | re.I)
    lines: list[str] = []
    for index, row in enumerate(rows):
        cells = [
            re.sub(r"<[^>]+>", "", cell).strip()
            for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, flags=re.S | re.I)
        ]
        if not cells:
            continue
        lines.append("|" + "|".join(cells) + "|")
        if index == 0:
            lines.append("|" + "|".join("---" for _ in cells) + "|")
    return "\n".join(lines)


def _question_number(record: _Record) -> int | None:
    first_line = record.text.strip().splitlines()[0] if record.text.strip() else ""
    match = _QUESTION_START.match(first_line)
    return int(match.group(1)) if match else None


def _rescue_footer_captions(records: list[_Record]) -> None:
    images = [r for r in records if r.kind == "image"]
    for record in [r for r in records if r.kind == "footer_caption"]:
        best: _Record | None = None
        best_distance = float("inf")
        if record.bbox is not None:
            fx0, fy0, fx1, _fy1 = record.bbox
            for image in images:
                if image.page_idx != record.page_idx or image.bbox is None:
                    continue
                ix0, _iy0, ix1, iy1 = image.bbox
                if iy1 > fy0 + 0.02:
                    continue
                overlap = min(fx1, ix1) - max(fx0, ix0)
                if overlap <= 0:
                    continue
                distance = fy0 - iy1
                if distance < best_distance:
                    best, best_distance = image, distance
        if best is not None and record.text not in best.captions:
            best.captions.append(record.text)
        records.remove(record)


def _text_bboxes(question: _Question, page_idx: int) -> list[tuple[float, float, float, float]]:
    return [
        item.bbox
        for item in question.items
        if item.page_idx == page_idx and item.bbox is not None
    ]


def _assign_images(
    records: list[_Record],
    questions: list[_Question],
    preamble: list[_Record],
) -> None:
    by_number = {q.number: q for q in questions}
    # 每页有文字的题（按文档顺序），供几何归题找"上一题/下一题"。
    page_questions: dict[int, list[_Question]] = {}
    for question in questions:
        for item in question.items:
            if item.bbox is None:
                continue
            bucket = page_questions.setdefault(item.page_idx, [])
            if question not in bucket:
                bucket.append(question)

    current: _Question | None = None
    ordered_questions: list[_Question] = []
    for record in records:
        if record.kind in ("text", "title", "table"):
            number = _question_number(record)
            if number is not None and number in by_number and by_number[number] not in ordered_questions:
                current = by_number[number]
                ordered_questions.append(current)
            if current is None:
                preamble.append(record)
            else:
                current.items.append(record)
        elif record.kind == "image":
            record.fallback = current

    for record in [r for r in records if r.kind == "image"]:
        target = _image_target_question(
            record, by_number=by_number, page_questions=page_questions
        )
        if target is None:
            target = record.fallback
        if target is None:
            preamble.append(record)
        else:
            target.images.append(record)


def _image_target_question(
    record: _Record,
    *,
    by_number: dict[int, _Question],
    page_questions: dict[int, list[_Question]],
) -> _Question | None:
    # (i) 图注题号：允许前向引用（图先于题文出现）。
    for caption in record.captions:
        match = _CAPTION_QUESTION_NO.search(caption)
        if match:
            target = by_number.get(int(match.group(1)))
            if target is not None:
                return target
    # (ii) 几何归属：图在某题文本之下、下一题之上；或与题文行高重叠且在其右侧。
    if record.bbox is None:
        return None
    x0, y0, _x1, y1 = record.bbox
    page = page_questions.get(record.page_idx, [])
    best: _Question | None = None
    best_distance = float("inf")
    for index, question in enumerate(page):
        boxes = _text_bboxes(question, record.page_idx)
        if not boxes:
            continue
        last_y1 = max(box[3] for box in boxes)
        next_y0 = float("inf")
        for following in page[index + 1 :]:
            following_boxes = _text_bboxes(following, record.page_idx)
            if following_boxes:
                next_y0 = min(box[1] for box in following_boxes)
                break
        below = y0 >= last_y1 - 0.01 and y0 < next_y0 + 0.01
        right_side = any(
            y0 < box[3] and y1 > box[1] and x0 >= box[2] - 0.02 for box in boxes
        )
        if not (below or right_side):
            continue
        distance = 0.0 if right_side and not below else max(0.0, y0 - last_y1)
        if distance < best_distance:
            best, best_distance = question, distance
    return best


def _pair_option_images(question: _Question) -> None:
    letter_blocks = [
        item
        for item in question.items
        if item.kind == "text" and item.text.strip() in _OPTION_LETTERS
    ]
    if len(question.images) < 3 or len(letter_blocks) < 3:
        return
    pairs: dict[str, _Record] = {}
    used_images: set[int] = set()
    for letter in letter_blocks:
        if letter.bbox is None:
            continue
        lx0, ly0, lx1, ly1 = letter.bbox
        center_x = (lx0 + lx1) / 2
        center_y = (ly0 + ly1) / 2
        for image in question.images:
            if id(image) in used_images or image.bbox is None:
                continue
            if image.page_idx != letter.page_idx:
                continue
            ix0, iy0, ix1, iy1 = image.bbox
            below = ix0 <= center_x <= ix1 and ly0 >= iy1 - 0.02
            left_same_row = (
                abs(center_y - (iy0 + iy1) / 2) < 0.02 and lx1 <= ix0 + 0.02
            )
            if below or left_same_row:
                pairs[letter.text.strip()] = image
                used_images.add(id(image))
                break
    if len(pairs) < 3:
        return
    question.option_images = pairs
    question.images = [i for i in question.images if id(i) not in used_images]
    removed_ids = {id(letter) for letter in letter_blocks if letter.text.strip() in pairs}
    question.items = [item for item in question.items if id(item) not in removed_ids]


def _image_attachments(question: _Question) -> dict[int, list[_Record]]:
    """把题图挂到"同题同页、图正上方的最后一个文本块"之后。

    找不到（图在题文之前/前一页）则挂到第一个文本块。
    返回 item id → 图片列表，同挂点多图保持阅读顺序。
    """
    attachments: dict[int, list[_Record]] = {}
    first_item = question.items[0] if question.items else None
    for image in question.images:
        target = None
        if image.bbox is not None:
            best_y = -1.0
            for item in question.items:
                if item.page_idx != image.page_idx or item.bbox is None:
                    continue
                if item.bbox[3] <= image.bbox[1] + 0.01 and item.bbox[3] > best_y:
                    best_y, target = item.bbox[3], item
        if target is None:
            target = first_item
        if target is not None:
            attachments.setdefault(id(target), []).append(image)
        else:
            attachments.setdefault(-1, []).append(image)
    return attachments


def _image_markdown(record: _Record) -> str:
    caption = " ".join(record.captions).strip()
    src = record.image_data
    if src and not src.startswith("data:"):
        mime = "image/jpeg" if src.startswith("/9j/") else "image/png"
        src = f"data:{mime};base64,{src}"
    return f"![{caption}]({src})"


def _analyze(result: object) -> tuple[list[_Record], list[_Question]]:
    """收集记录并完成题号切分、图归题与选项图配对。"""
    records = _collect_records(result)
    _rescue_footer_captions(records)

    # 先按文本块找题号（两遍：先收集题号表，再归块），题号来源 text/title。
    numbers = [
        number
        for record in records
        if record.kind in ("text", "title")
        for number in [_question_number(record)]
        if number is not None
    ]
    questions: list[_Question] = []
    seen: set[int] = set()
    for number in numbers:
        if number not in seen:
            seen.add(number)
            questions.append(_Question(number))
    preamble: list[_Record] = []
    _assign_images(records, questions, preamble)

    for question in questions:
        _pair_option_images(question)
    return preamble, questions


def _emit_markdown(preamble: list[_Record], questions: list[_Question]) -> str:
    lines: list[str] = []

    def emit(record: _Record) -> None:
        if record.kind == "title":
            for line in record.text.splitlines():
                if line.strip():
                    lines.append(f"## {line.strip()}")
        elif record.kind == "image":
            lines.append(_image_markdown(record))
        else:
            for line in record.text.splitlines():
                if line.strip():
                    lines.append(line.rstrip())

    for record in preamble:
        emit(record)
    for question in questions:
        attachments = _image_attachments(question)
        emitted_images: set[int] = set()
        for item in question.items:
            emit(item)
            for image in attachments.get(id(item), []):
                emit(image)
                emitted_images.add(id(image))
        for image in attachments.get(-1, []) + [
            i for i in question.images if id(i) not in emitted_images
        ]:
            emit(image)
        for letter in _OPTION_LETTERS:
            image = question.option_images.get(letter)
            if image is not None:
                lines.append(f"{letter}. {_image_markdown(image)}")
    return "\n\n".join(lines)


def _render_structured(result: object) -> str:
    records = _collect_records(result)
    if not records:
        return ""
    preamble, questions = _analyze(result)
    return _emit_markdown(preamble, questions)


# ---------------------------------------------------------------------------
# 彩色答案层双 PDF 解析：学生版出 Markdown，答案版红字按坐标归题。
# ---------------------------------------------------------------------------

_ANSWER_CHOICE_LINE = re.compile(r"^[（(]?\s*([A-D])\s*[)）]?$")
_ANSWER_SOLUTION_LEAD = re.compile(r"^\s*(?:解答|解|答|证明|证|分析|∵|∴|由|即)")
_ANSWER_TYPE_BY_KIND = {
    "choice": "选择题",
    "fill": "填空题",
    "solution": "解答题",
}


def _question_page_anchors(
    questions: list[_Question],
) -> tuple[dict[int, list[tuple[int, float]]], dict[int, int]]:
    """每页的题锚点 [(题号, 该题页内首个文本块 y0)]（按 y 排序）与页内末题。"""
    anchors: dict[int, list[tuple[int, float]]] = {}
    last_on_page: dict[int, int] = {}
    for question in questions:
        per_page: dict[int, float] = {}
        for item in question.items:
            if item.bbox is None:
                continue
            page_y0 = per_page.setdefault(item.page_idx, item.bbox[1])
            per_page[item.page_idx] = min(page_y0, item.bbox[1])
        for page_idx, first_y0 in per_page.items():
            anchors.setdefault(page_idx, []).append((question.number, first_y0))
            last_on_page[page_idx] = question.number
    for entries in anchors.values():
        entries.sort(key=lambda entry: entry[1])
    return anchors, last_on_page


def _answer_block_target(
    record: _Record,
    *,
    anchors: dict[int, list[tuple[int, float]]],
    last_on_page: dict[int, int],
    known_questions: set[int],
) -> int | None:
    if record.bbox is None:
        return None
    center_y = (record.bbox[1] + record.bbox[3]) / 2
    entries = anchors.get(record.page_idx, [])
    target = None
    for number, first_y0 in entries:
        if first_y0 <= center_y + 0.01:
            target = number
        else:
            break
    if target is not None:
        return target
    # 本页第一题之前 → 最近前页的末题。
    for page_idx in sorted(last_on_page, reverse=True):
        if page_idx < record.page_idx:
            return last_on_page[page_idx]
    return None


def _piece_choice_letter(piece: str) -> str:
    """答案碎片中的选项字母：完整匹配优先，短碎片兜底提取首个唯一 A–D。"""
    squashed = re.sub(r"\s+", "", piece)
    match = _ANSWER_CHOICE_LINE.match(squashed)
    if match:
        return match.group(1)
    if len(squashed) <= 4:
        letters = re.findall(r"[A-D]", squashed)
        # 只允许一个 A–D 字母，且其余字符不含其它拉丁字母（排除 "cm"/"BD" 等）。
        remainder = squashed
        for letter in letters:
            remainder = remainder.replace(letter, "", 1)
        if len(letters) == 1 and not re.search(r"[A-Za-z]", remainder):
            return letters[0]
    return ""


def _classify_answer_pieces(pieces: list[str]) -> tuple[str, str]:
    """returns (kind, answer_text)；kind ∈ choice|fill|solution。"""
    choice_letter = ""
    solution_pieces: list[str] = []
    fill_pieces: list[str] = []
    rest: list[str] = []
    for piece in pieces:
        letter = _piece_choice_letter(piece)
        if letter:
            # 一题有多个字母碎片（如正文变式里的字母）时取阅读顺序第一个。
            if not choice_letter:
                choice_letter = letter
        elif _ANSWER_SOLUTION_LEAD.match(piece.strip()):
            solution_pieces.append(piece)
        else:
            fill_pieces.append(piece)
            rest.append(piece)
    if choice_letter:
        extra = [p for p in pieces if not _piece_choice_letter(p)]
        answer = choice_letter + ("\n" + "\n".join(extra) if extra else "")
        return "choice", answer
    if solution_pieces:
        return "solution", "\n".join(solution_pieces + rest)
    return "fill", "；".join(fill_pieces)


def _question_text_records(
    questions: list[_Question],
) -> tuple[dict[int, list[tuple[tuple[float, float, float, float], int, str]]], dict[int, int]]:
    """每页 [(非图片块 bbox, 题号, kind)] 与每页末题（用于跨页兜底）。"""
    by_page: dict[
        int, list[tuple[tuple[float, float, float, float], int, str]]
    ] = {}
    last_on_page: dict[int, int] = {}
    for question in questions:
        for item in question.items:
            if item.bbox is None or item.kind == "image":
                continue
            by_page.setdefault(item.page_idx, []).append(
                (item.bbox, question.number, item.kind)
            )
            last_on_page[item.page_idx] = question.number
    return by_page, last_on_page


def _x_overlap(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    return min(a[2], b[2]) - max(a[0], b[0])


def _region_target(
    bbox: tuple[float, float, float, float],
    page_idx: int,
    *,
    text_records: dict[int, list[tuple[tuple[float, float, float, float], int, str]]],
    last_on_page: dict[int, int],
) -> int | None:
    """答案区域归题：优先同一视觉行（y 重叠且 x 重叠），再同列最近上方块，
    再同列最近下方块，最后任意上方块；都没有 → 上一页末题。"""
    rx0, ry0, rx1, ry1 = bbox
    records = text_records.get(page_idx, [])

    def _col(rec_bbox: tuple[float, float, float, float]) -> float:
        return min(rec_bbox[2], rx1) - max(rec_bbox[0], rx0)

    # 1) 同一视觉行：y 区间真实重叠，x 有重叠。
    # 只用容差贴合的候选会落在两题边界上翻转归属，必须要求实际重叠。
    row_hits = [
        (rec, q, min(rec[3], ry1) - max(rec[1], ry0))
        for rec, q, _k in records
        if _col(rec) > 0 and min(rec[3], ry1) - max(rec[1], ry0) > 0.002
    ]
    if row_hits:
        best = max(
            row_hits,
            key=lambda triple: triple[2]
            + _col(triple[0])
            - abs((triple[0][1] + triple[0][3]) / 2 - (ry0 + ry1) / 2),
        )
        return best[1]
    # 2) 同列最近上方块。
    above = [
        (rec, q) for rec, q, _k in records if _col(rec) > 0 and rec[3] <= ry0 + 0.01
    ]
    if above:
        return max(above, key=lambda pair: pair[0][3])[1]
    # 3) 同列最近下方块（答案碎片落在题干上方一行的情况）。
    below = [
        (rec, q) for rec, q, _k in records if _col(rec) > 0 and rec[1] >= ry1 - 0.01
    ]
    if below:
        return min(below, key=lambda pair: pair[0][1])[1]
    # 4) 任意上方最近块。
    any_above = [(rec, q) for rec, q, _k in records if rec[3] <= ry0 + 0.01]
    if any_above:
        return max(any_above, key=lambda pair: pair[0][3])[1]
    # 5) 本页所有题之前 → 最近前页末题。
    for prev in sorted(last_on_page, reverse=True):
        if prev < page_idx:
            return last_on_page[prev]
    return None


_REGION_OCR_ZOOM = 8.0
_REGION_PAGE_PAD = 120.0
# 小区域（行内短答案/单字）交给本地行级 OCR：裁剪行高渲到约 64px，补 16px 白边。
_REGION_LINE_HEIGHT_PX = 64.0
_REGION_LINE_PAD_PX = 16
_REGION_SMALL_MAX_WIDTH = 0.08
_REGION_SMALL_MAX_HEIGHT = 0.03

# 教师用书里用答案色印刷的装饰性栏目标签，不是题目答案。
_DECORATIVE_LABEL = re.compile(
    r"^\s*(知识点|易错点|重难点|易混点|疑点|盲点|关键点|方法点拨|解题技巧"
    r"|综合题|基础题|中档题|提高题|拓展题|例题|变式|练习|巩固)\s*\d*\s*[.、．]?\s*$"
)


def _is_decorative_label(text: str) -> bool:
    return bool(_DECORATIVE_LABEL.match(text.strip()))


def _region_is_small(region: object) -> bool:
    """行内/窄小答案碎片：行级 OCR 比整页 MinerU 稳。"""
    width = region.bbox_norm[2] - region.bbox_norm[0]
    height = region.bbox_norm[3] - region.bbox_norm[1]
    return (
        getattr(region, "kind", "") == "inline"
        or width < _REGION_SMALL_MAX_WIDTH
        or height < _REGION_SMALL_MAX_HEIGHT
    )


def _region_clip(page: object, region: object) -> object:
    import fitz

    rect = page.rect
    clip = fitz.Rect(
        region.bbox_norm[0] * rect.width,
        region.bbox_norm[1] * rect.height,
        region.bbox_norm[2] * rect.width,
        region.bbox_norm[3] * rect.height,
    )
    clip.x0 = max(0.0, clip.x0 - 2)
    clip.y0 = max(0.0, clip.y0 - 2)
    clip.x1 = min(rect.width, clip.x1 + 2)
    clip.y1 = min(rect.height, clip.y1 + 2)
    return clip


def _region_line_image(page: object, region: object) -> object:
    """裁剪答案区域并渲染到约 64px 行高、16px 白边的 BGR numpy 图。"""
    import fitz
    import numpy as np

    clip = _region_clip(page, region)
    zoom = min(12.0, max(2.0, _REGION_LINE_HEIGHT_PX / max(clip.height, 1.0)))
    pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip)
    channels = pixmap.n
    raw = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
        pixmap.height, pixmap.width, channels
    )
    rgb = raw[:, :, :3]
    pad = _REGION_LINE_PAD_PX
    canvas = np.full(
        (rgb.shape[0] + 2 * pad, rgb.shape[1] + 2 * pad, 3), 255, dtype=np.uint8
    )
    canvas[pad : pad + rgb.shape[0], pad : pad + rgb.shape[1]] = rgb
    return canvas[:, :, ::-1]  # RGB → BGR


def _ocr_region_line(page: object, region: object) -> str:
    """用本地 PP-OCR 识别单个答案碎片（适合单字/短答案/单个选项字母）。"""
    try:
        from backend.document_parsing.local_ocr import get_local_ocr

        image = _region_line_image(page, region)
        rows, _elapsed = get_local_ocr()(image)
    except Exception:
        LOGGER.warning("local OCR failed for answer region %s", region, exc_info=True)
        return ""
    if not rows:
        return ""
    rows = sorted(rows, key=lambda row: min(pt[0] for pt in row[0]))
    return "".join(str(row[1]).strip() for row in rows if row[1]).strip()


def _ocr_answer_regions(
    answers_pdf: Path, regions: list
) -> list[str]:
    """逐区域 OCR 答案文本。

    行内/窄小区域走本地 PP-OCR 行级识别（MinerU 对孤立单字不稳定）；
    多行 block 区域仍按高倍率独立成页送 MinerU（要保留 LaTeX）。
    返回与 regions 同序的文本列表。
    """
    import fitz

    texts = [""] * len(regions)
    big_indices = [
        index for index, region in enumerate(regions) if not _region_is_small(region)
    ]

    source = fitz.open(answers_pdf)
    try:
        for index, region in enumerate(regions):
            if index in big_indices:
                continue
            page = source[region.page_idx]
            texts[index] = _ocr_region_line(page, region)
        if big_indices:
            document = fitz.open()
            try:
                for index in big_indices:
                    region = regions[index]
                    page = source[region.page_idx]
                    clip = _region_clip(page, region)
                    pixmap = page.get_pixmap(
                        matrix=fitz.Matrix(_REGION_OCR_ZOOM, _REGION_OCR_ZOOM),
                        clip=clip,
                    )
                    out_page = document.new_page(
                        width=pixmap.width + 2 * _REGION_PAGE_PAD,
                        height=pixmap.height + 2 * _REGION_PAGE_PAD,
                    )
                    out_page.insert_image(
                        fitz.Rect(
                            _REGION_PAGE_PAD,
                            _REGION_PAGE_PAD,
                            _REGION_PAGE_PAD + pixmap.width,
                            _REGION_PAGE_PAD + pixmap.height,
                        ),
                        pixmap=pixmap,
                    )
                ocr_path = answers_pdf.parent / "answer_regions.pdf"
                document.save(ocr_path, garbage=3, deflate=True)
            finally:
                document.close()
            result = _run_mineru(ocr_path)
            if result is not None:
                records = _collect_records(result)
                for sub_idx, index in enumerate(big_indices):
                    pieces = [
                        record.text.strip()
                        for record in records
                        if record.page_idx == sub_idx
                        and record.kind in ("text", "title", "equation", "table")
                        and record.text.strip()
                    ]
                    texts[index] = "\n".join(pieces)
    finally:
        source.close()
    return texts


def _save_answer_crop(page: object, region: object, path: Path) -> None:
    """把答案区域裁图存盘，供人工核对。"""
    import fitz

    clip = _region_clip(page, region)
    pixmap = page.get_pixmap(matrix=fitz.Matrix(4.0, 4.0), clip=clip)
    path.parent.mkdir(parents=True, exist_ok=True)
    pixmap.save(str(path))


def _answers_from_regions(
    answers_pdf: Path,
    regions: list,
    questions: list[_Question],
) -> tuple[dict[int, str], dict[int, str], dict[int, list]]:
    """按 PDF 层的答案区域几何归题，再逐区域 OCR 出答案文本。

    返回 (answers, types, inline_regions)；inline_regions 记录每题归到的
    inline 答案区域，供把空格线按坐标注入题干。
    """
    text_records, last_on_page = _question_text_records(questions)

    # 页眉/节标题噪声：落在本栏第一题之前（留 0.02 容差）的区域剔除。
    # first_top 只看 text 块：title 可能是节标题（"中档题""综合题"），
    # 用它会把判定线下移，放跑位于其上方一点点的页眉。
    first_top: dict[tuple[int, int], float] = {}
    for page_idx, entries in text_records.items():
        for bbox, _q, kind in entries:
            if kind != "text":
                continue
            col = 0 if (bbox[0] + bbox[2]) / 2 < 0.5 else 1
            key = (page_idx, col)
            first_top[key] = min(first_top.get(key, 1.0), bbox[1])

    kept = []
    for region in regions:
        cx = (region.bbox_norm[0] + region.bbox_norm[2]) / 2
        col = 0 if cx < 0.5 else 1
        top = first_top.get((region.page_idx, col))
        if top is not None and region.bbox_norm[3] < top - 0.02:
            continue
        kept.append(region)
    if not kept:
        return {}, {}, {}

    # 先归题（裁图文件名带题号，便于人工核对），再逐区域 OCR。
    targets = [
        _region_target(
            region.bbox_norm,
            region.page_idx,
            text_records=text_records,
            last_on_page=last_on_page,
        )
        for region in kept
    ]
    try:
        import fitz

        source = fitz.open(answers_pdf)
        try:
            crop_dir = answers_pdf.parent / "answer_crops"
            for idx, (region, target) in enumerate(zip(kept, targets)):
                name = f"q{target if target is not None else 'x'}_p{region.page_idx}_{idx}.png"
                _save_answer_crop(source[region.page_idx], region, crop_dir / name)
        finally:
            source.close()
    except Exception:
        LOGGER.warning("answer-crop dump failed for %s", answers_pdf, exc_info=True)

    texts = _ocr_answer_regions(answers_pdf, kept)

    inline_regions: dict[int, list] = {}
    pieces_by_question: dict[int, list[tuple[int, float, float, str]]] = {}
    for region, text, target in zip(kept, texts, targets):
        if target is None:
            LOGGER.warning(
                "answer region on page %s could not be attributed: %r",
                region.page_idx,
                text[:40],
            )
            continue
        # 全部区域都记下：填空题的答案若独占一行（如换行后的 "13 cm"）会被
        # 判成 block，但同样对应题干里的一处空格线，由题型决定是否注入。
        inline_regions.setdefault(target, []).append(region)
        text = text.strip()
        if not text or _is_decorative_label(text):
            continue
        # 双栏教辅：先按栏再按 y 排，否则左右栏的解答段会按 y 交错。
        column = 1 if region.bbox_norm[0] >= 0.48 else 0
        pieces_by_question.setdefault(target, []).append(
            (region.page_idx, column, region.bbox_norm[1], region.bbox_norm[0], text)
        )

    answers: dict[int, str] = {}
    types: dict[int, str] = {}
    for number, entries in pieces_by_question.items():
        pieces = [text for _p, _c, _y, _x, text in sorted(entries)]
        kind, answer_text = _classify_answer_pieces(pieces)
        answers[number] = answer_text
        types[number] = _ANSWER_TYPE_BY_KIND[kind]
    return answers, types, inline_regions


def _inject_inline_blanks(
    questions: list[_Question],
    inline_regions: dict[int, list],
    types: dict[int, str] | None = None,
) -> None:
    """按坐标把空格线 " ____" 注入题干文本项。

    行内答案被擦除后原位写入了占位文字，但 MinerU 可能整段丢弃或读成
    乱码公式；这里直接按答案区域的 bbox 把 " ____" 插回对应文本块的
    相对位置。若该位置附近已有空格形态（MinerU 读出了占位符），跳过。
    选择题的品红区域是答案字母而非空格，不注入。
    """
    if not inline_regions:
        return
    from question_bank.importers.batch_importer import (
        _BLANK_GROUP,
        _normalize_ocr_blanks,
    )

    edits: dict[int, list[tuple[int, _Record]]] = {}
    for question in questions:
        # 只有填空题才注入空格线：选择题的红字是答案字母，解答题的红字是解答正文。
        if types is not None and types.get(question.number) != "填空题":
            continue
        regions = inline_regions.get(question.number)
        if not regions:
            continue
        items = [item for item in question.items if item.bbox and item.text.strip()]
        if not items:
            continue
        # 学生版 PDF 里写入的占位符多数已被 MinerU 读出；只在读出的空格
        # 少于答案区域数时才按坐标补注入，避免同一处出现两条空格线。
        existing = _normalize_ocr_blanks(
            "\n".join(item.text for item in question.items)
        ).count("____")
        if existing >= len(regions):
            continue
        for region in regions:
            rcy = (region.bbox_norm[1] + region.bbox_norm[3]) / 2
            rx0 = region.bbox_norm[0]
            matched = [
                item
                for item in items
                if item.page_idx == region.page_idx
                and item.bbox[1] - 0.015 <= rcy <= item.bbox[3] + 0.015
            ]
            if matched:

                def _key(item: _Record) -> tuple:
                    x0, y0, x1, y1 = item.bbox
                    x_hit = 0 if x0 - 0.02 <= rx0 <= x1 + 0.02 else 1
                    return (x_hit, abs((y0 + y1) / 2 - rcy), abs(rx0 - x0))

                item = min(matched, key=_key)
                x0, _y0, x1, _y1 = item.bbox
                frac = min(max((rx0 - x0) / max(x1 - x0, 1e-6), 0.0), 1.0)
                # 空白总在文字之后；贴行首的区域多半是绑定错的装饰红字，跳过，
                # 绝不能把 " ____" 插到 "8." 前面把题号弄丢。
                if frac <= 0.06:
                    continue
                if frac >= 0.8:
                    offset = len(item.text)
                else:
                    offset = -1  # 下方统一吸附边界
            else:
                # 区域落在所有文本块 bbox 之外（MinerU 块 bbox 常只覆盖首行）：
                # 空格实际在该行文字续行上，附到区域上方最近文本块的末尾。
                above = [
                    item
                    for item in items
                    if item.page_idx == region.page_idx and item.bbox[3] <= rcy + 0.01
                ]
                if not above:
                    continue
                item = max(above, key=lambda i: i.bbox[3])
                offset = len(item.text)
            if offset == -1:
                offset = int(round(frac * len(item.text)))
                # 吸附到最近的空格/公式边界，避免插进单词或 LaTeX 中间。
                best = None
                for shift in range(0, 7):
                    for cand in (offset + shift, offset - shift):
                        if 0 <= cand <= len(item.text) and (
                            cand == len(item.text)
                            or item.text[cand - 1 : cand].isspace()
                            or item.text[cand : cand + 1].isspace()
                        ):
                            best = cand
                            break
                    if best is not None:
                        break
                if best is not None:
                    offset = best
            prefix = re.match(r"\s*\d{1,2}\s*[.、．]\s*", item.text)
            floor = prefix.end() if prefix else len(item.text) - len(item.text.lstrip())
            if offset <= floor:
                continue
            # 占位符被 MinerU 读出（____/□/乱码公式组）时不再注入；
            # 垃圾公式组可能很长，看匹配区间是否覆盖插入点而不是小窗口。
            window = item.text[max(0, offset - 10) : offset + 10]
            covered = any(
                m.start() - 4 <= offset <= m.end() + 4
                for m in _BLANK_GROUP.finditer(item.text)
            )
            if "____" in window or "□" in window or covered:
                continue
            for match in re.finditer(r"\$[^$]+\$", item.text):
                if match.start() < offset < match.end():
                    offset = (
                        match.end()
                        if offset - match.start() > match.end() - offset
                        else match.start()
                    )
                    break
            edits.setdefault(id(item), []).append((offset, item))
    for item_edits in edits.values():
        item = item_edits[0][1]
        for offset, _ in sorted(item_edits, key=lambda e: e[0], reverse=True):
            item.text = item.text[:offset] + " ____" + item.text[offset:]


def parse_pdf_full_with_answers(
    student_pdf: Path, answers_pdf: Path, regions: list | None = None,
    *, layout_out: dict | None = None,
) -> tuple[str, dict[int, str], dict[int, str]] | None:
    """解析彩色答案层拆分出的两份 PDF。

    返回 (学生版 Markdown, {题号: 答案}, {题号: 题型})；
    学生版解析失败返回 None，答案侧失败则返空答案。
    regions 为 pdf_importer.split_colored_answer_layers 返回的答案区域；
    未提供时回退到对 answers.pdf 整页 MinerU 解析的坐标归题。
    """
    student_result = _run_mineru(student_pdf)
    if student_result is None:
        return None
    if layout_out is not None:
        _copy_layout(student_result, layout_out)
    try:
        preamble, questions = _analyze(student_result)
    except Exception:
        LOGGER.warning(
            "MinerU structured render failed for %s; falling back to markdown",
            student_pdf,
            exc_info=True,
        )
        try:
            markdown = str(student_result.markdown() or "").strip()
            questions = []
            answers: dict[int, str] = {}
            types: dict[int, str] = {}
        except Exception:
            LOGGER.warning("MinerU markdown fallback failed for %s", student_pdf, exc_info=True)
            return None
    else:
        answers = {}
        types = {}
        if regions and questions:
            try:
                answers, types, inline_regions = _answers_from_regions(
                    answers_pdf, regions, questions
                )
                _inject_inline_blanks(questions, inline_regions, types)
            except Exception:
                LOGGER.warning(
                    "answer-region OCR failed for %s", answers_pdf, exc_info=True
                )
            markdown = _emit_markdown(preamble, questions)
            if not markdown:
                try:
                    markdown = str(student_result.markdown() or "").strip()
                except Exception:
                    LOGGER.warning(
                        "MinerU markdown fallback failed for %s",
                        student_pdf,
                        exc_info=True,
                    )
                    return None
            if not markdown:
                return None
            return markdown, answers, types
        markdown = _emit_markdown(preamble, questions)
        if not markdown:
            try:
                markdown = str(student_result.markdown() or "").strip()
            except Exception:
                LOGGER.warning(
                    "MinerU markdown fallback failed for %s", student_pdf, exc_info=True
                )
                return None
        if not markdown:
            return None
    if not markdown:
        return None

    answers_result = _run_mineru(answers_pdf)
    if answers_result is None or not questions:
        return markdown, answers, types

    answer_records = [
        record
        for record in _collect_records(answers_result)
        if record.kind in ("text", "title") and record.text.strip()
    ]
    anchors, last_on_page = _question_page_anchors(questions)
    known = {question.number for question in questions}
    pieces_by_question: dict[int, list[tuple[int, int, str]]] = {}
    for order, record in enumerate(answer_records):
        target = _answer_block_target(
            record,
            anchors=anchors,
            last_on_page=last_on_page,
            known_questions=known,
        )
        if target is None or target not in known:
            LOGGER.warning(
                "answer-layer block on page %s could not be attributed: %r",
                record.page_idx,
                record.text[:40],
            )
            continue
        pieces_by_question.setdefault(target, []).append(
            (record.page_idx, order, record.text.strip())
        )
    for number, entries in pieces_by_question.items():
        pieces = [text for _page, _order, text in sorted(entries)]
        kind, answer_text = _classify_answer_pieces(pieces)
        answers[number] = answer_text
        types[number] = _ANSWER_TYPE_BY_KIND[kind]
    return markdown, answers, types
