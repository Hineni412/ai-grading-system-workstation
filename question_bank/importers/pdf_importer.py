from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import fitz

LOGGER = logging.getLogger(__name__)

from question_bank.document_pipeline.contracts import (
    DocumentSource,
    ManualQuestionRegion,
    PrepareSourceCommand,
    TextLayerState,
)
from question_bank.importers.types import ExtractedDocument

if TYPE_CHECKING:
    from question_bank.document_pipeline.pipeline import QuestionDocumentPipeline


def embedded_text_usable(text: str) -> bool:
    """A nonempty font map may still yield punctuation garbage instead of text."""
    chars = [char for char in text if not char.isspace()]
    return bool(chars) and sum(char.isalnum() for char in chars) / len(chars) >= 0.4


def import_pdf(
    source_file: str | Path,
    *,
    document_pipeline: QuestionDocumentPipeline | None = None,
    operation_id: str | None = None,
    source_id: str | None = None,
    manual_questions: tuple[ManualQuestionRegion, ...] = (),
) -> ExtractedDocument:
    path = Path(source_file)
    if document_pipeline is not None:
        if not operation_id:
            raise ValueError("operation_id is required for document pipeline import")
        snapshot = document_pipeline.prepare_source(
            PrepareSourceCommand(
                operation_id=operation_id,
                source=DocumentSource(
                    source_id=source_id or f"pdf-{operation_id}",
                    filename=path.name,
                    media_type="application/pdf",
                    content=path.read_bytes(),
                ),
                manual_questions=manual_questions,
            )
        )
        text = "\n".join(
            block.text
            for page in snapshot.pages
            for block in page.blocks
            if block.text
        ).strip()
        return ExtractedDocument(
            source_file=str(path),
            page_range=_page_range(len(snapshot.pages)),
            text=text,
            needs_ocr=any(
                page.text_layer_state != TextLayerState.EMBEDDED
                for page in snapshot.pages
            ),
            has_images=bool(snapshot.pages),
            needs_image_review=any(
                page.transform.requires_review for page in snapshot.pages
            ),
            image_paths=[page.rendered_asset for page in snapshot.pages],
            document_snapshot=snapshot,
        )
    with fitz.open(path) as document:
        page_text = [page.get_text("text").strip() for page in document]
        text = "\n".join(item for item in page_text if item).strip()
        page_range = _page_range(document.page_count)
    return ExtractedDocument(
        source_file=str(path),
        page_range=page_range,
        text=text,
        needs_ocr=not bool(text),
    )


def _page_range(page_count: int) -> str:
    if page_count <= 0:
        return ""
    if page_count == 1:
        return "1"
    return f"1-{page_count}"


# ---------------------------------------------------------------------------
# 彩色答案层拆分：教辅把答案印成品红等彩色字形，按颜色把 PDF 拆成
# 学生版（答案擦除、短答案补下划线）和答案版（只留答案色文字）。
# ---------------------------------------------------------------------------

# 彩色判定：RGB 通道极差超过该值才算彩色（黑/灰文字通道相等）。
_COLOR_SPREAD = 100
# 彩色字符占比与绝对数量的下限，低于则判定为普通 PDF。
_MIN_COLOR_RATIO = 0.03
_MIN_COLOR_CHARS = 10
# 答案色聚类容差（每个 RGB 通道）。
_COLOR_TOLERANCE = 10
# 行内短答案：答案簇宽 < 行宽的比例上限，命中则擦除后补下划线。
_INLINE_CLUSTER_MAX_WIDTH_RATIO = 0.6
# 簇内相邻 span 的最大水平间距（字号倍数）。
_CLUSTER_MAX_GAP_RATIO = 1.5
# 簇合并成块的垂直间距（字号倍数）。
_BLOCK_MAX_YGAP_RATIO = 0.6


@dataclass(frozen=True)
class AnswerRegion:
    page_idx: int
    bbox_norm: tuple[float, float, float, float]
    kind: str  # "inline" | "block"


@dataclass(frozen=True)
class ColoredLayers:
    student_pdf: Path
    answers_pdf: Path
    regions: list[AnswerRegion]
    answer_color: tuple[int, int, int]


def _span_rgb(color: int) -> tuple[int, int, int]:
    return ((color >> 16) & 0xFF, (color >> 8) & 0xFF, color & 0xFF)


def _color_distance(a: tuple[int, int, int], b: tuple[int, int, int]) -> int:
    return max(abs(x - y) for x, y in zip(a, b))


def split_colored_answer_layers(
    path: Path, work_dir: Path
) -> ColoredLayers | None:
    """把含彩色答案字形的 PDF 拆成学生版与答案版。

    普通 PDF（无彩色或彩色占比过低）返回 None。
    """
    path = Path(path)
    work_dir = Path(work_dir)
    document = fitz.open(path)
    try:
        # 收集全部文本 span（fitz 会把不同颜色拆成不同 line 实体，
        # 所以"行"要按 y 带重叠自行判断，不能直接信 dict 的 line）。
        page_spans: dict[int, list[dict]] = {}
        color_counts: dict[tuple[int, int, int], int] = {}
        total_chars = 0
        for page_idx, page in enumerate(document):
            page_dict = page.get_text("dict")
            for block in page_dict.get("blocks", []):
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        text = span.get("text", "")
                        rgb = _span_rgb(int(span.get("color", 0)))
                        size = float(span.get("size", 10.0))
                        page_spans.setdefault(page_idx, []).append(
                            {
                                "bbox": fitz.Rect(span["bbox"]),
                                "rgb": rgb,
                                "size": size,
                                "text": text,
                            }
                        )
                        total_chars += len(text)
                        if max(rgb) - min(rgb) > _COLOR_SPREAD:
                            color_counts[rgb] = (
                                color_counts.get(rgb, 0) + len(text)
                            )
        colored_chars = sum(color_counts.values())
        if (
            not color_counts
            or colored_chars < _MIN_COLOR_CHARS
            or colored_chars / max(total_chars, 1) < _MIN_COLOR_RATIO
        ):
            return None
        answer_color = max(color_counts.items(), key=lambda kv: kv[1])[0]

        def is_answer_span(span: dict) -> bool:
            return _color_distance(span["rgb"], answer_color) <= _COLOR_TOLERANCE

        # 聚簇答案 span：同页、y 带重叠、水平间距 <1.5 字号 → 同簇；
        # 簇与黑字行同处一条视觉行且宽度 < 行宽 60% → inline。
        clusters: list[dict] = []
        for page_idx, spans in page_spans.items():
            answer_spans = sorted(
                (s for s in spans if is_answer_span(s)),
                key=lambda s: (s["bbox"].y0, s["bbox"].x0),
            )
            plain_spans = [s for s in spans if not is_answer_span(s)]
            rows: list[list[dict]] = []
            for span in answer_spans:
                if rows and span["bbox"].y0 <= max(
                    s["bbox"].y1 for s in rows[-1]
                ):
                    rows[-1].append(span)
                else:
                    rows.append([span])
            for row in rows:
                row.sort(key=lambda s: s["bbox"].x0)
                group = [row[0]]
                for span in row[1:]:
                    prev = group[-1]
                    gap = span["bbox"].x0 - prev["bbox"].x1
                    if gap < _CLUSTER_MAX_GAP_RATIO * max(prev["size"], 1.0):
                        group.append(span)
                    else:
                        clusters.append(_cluster_record(page_idx, group, plain_spans))
                        group = [span]
                clusters.append(_cluster_record(page_idx, group, plain_spans))

        regions = _merge_regions(clusters, document)

        # 学生版：擦答案色 span，行内短答案补黑色下划线。
        student = fitz.open(path)
        for cluster in clusters:
            page = student[cluster["page_idx"]]
            page.add_redact_annot(cluster["bbox"], fill=(1, 1, 1))
        for page in student:
            page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)
        # 行内短答案原位写入 □ 占位串：连续下划线 OCR 读不出、
        # 字母串会被并入公式；□ 在公式里固定读成 \Box，随后归一成 "____"。
        for cluster in clusters:
            if not cluster["inline"]:
                continue
            page = student[cluster["page_idx"]]
            bbox = cluster["bbox"]
            size = max(cluster["size"], 4.0)
            count = max(4, min(6, round(bbox.width / (size * 0.8))))
            page.insert_text(
                (bbox.x0, bbox.y1 - 1.0),
                "□" * count,
                fontsize=size,
                fontname="china-s",
                color=(0, 0, 0),
            )
        work_dir.mkdir(parents=True, exist_ok=True)
        student_path = work_dir / "student.pdf"
        answers_path = work_dir / "answers.pdf"
        student.save(student_path, garbage=3, deflate=True)
        student.close()

        # 答案版：擦掉所有非答案色文字 span，只留答案色。
        answers = fitz.open(path)
        for page_idx, page in enumerate(answers):
            for span in page_spans.get(page_idx, []):
                if not is_answer_span(span):
                    page.add_redact_annot(span["bbox"], fill=(1, 1, 1))
            page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)
        answers.save(answers_path, garbage=3, deflate=True)
        answers.close()

        return ColoredLayers(
            student_pdf=student_path,
            answers_pdf=answers_path,
            regions=regions,
            answer_color=answer_color,
        )
    finally:
        document.close()


_TWO_COLUMN_MIN_RATIO = 0.7


def _two_column_midline(plain_spans: list[dict]) -> float | None:
    """页面为双栏版式时返回栏间中线 x，否则 None。

    以黑字 span 的整体横向范围取中点，≥70% 的 span 完全落在中点一侧
    （不跨线）即视为双栏。
    """
    if len(plain_spans) < 8:
        return None
    x0 = min(s["bbox"].x0 for s in plain_spans)
    x1 = max(s["bbox"].x1 for s in plain_spans)
    midline = (x0 + x1) / 2
    clear = sum(1 for s in plain_spans if s["bbox"].x1 <= midline or s["bbox"].x0 >= midline)
    return midline if clear >= _TWO_COLUMN_MIN_RATIO * len(plain_spans) else None


def _cluster_record(
    page_idx: int, spans: list[dict], plain_spans: list[dict]
) -> dict:
    bbox = fitz.Rect(spans[0]["bbox"])
    size = max(s["size"] for s in spans)
    for span in spans[1:]:
        bbox |= fitz.Rect(span["bbox"])
    # 同一视觉行上的非答案 span：y 区间重叠即视为同行。
    # 双栏页面（多数黑字不跨页面中线）只看本栏，否则另一栏的黑字会把
    # 整行红色解答误判成行内空格答案。
    row_plain = [
        s
        for s in plain_spans
        if s["bbox"].y0 < bbox.y1 and bbox.y0 < s["bbox"].y1
    ]
    midline = _two_column_midline(plain_spans)
    if midline is not None:
        cluster_side = (bbox.x0 + bbox.x1) / 2 < midline
        row_plain = [
            s
            for s in row_plain
            if ((s["bbox"].x0 + s["bbox"].x1) / 2 < midline) == cluster_side
        ]
    if row_plain:
        line = fitz.Rect(bbox)
        for s in row_plain:
            line |= s["bbox"]
        line_width = max(line.width, 1.0)
    else:
        line_width = bbox.width
    inline = bool(row_plain) and bbox.width < _INLINE_CLUSTER_MAX_WIDTH_RATIO * line_width
    return {
        "page_idx": page_idx,
        "bbox": bbox,
        "size": size,
        "inline": inline,
    }


def _merge_regions(clusters: list[dict], document: fitz.Document) -> list[AnswerRegion]:
    """同页、垂直相邻 <0.6 字号且左边界接近的簇合并成块。"""
    if not clusters:
        return []
    merged: list[dict] = []
    for cluster in sorted(clusters, key=lambda c: (c["page_idx"], c["bbox"].y0, c["bbox"].x0)):
        placed = False
        if merged:
            last = merged[-1]
            if last["page_idx"] == cluster["page_idx"]:
                y_gap = cluster["bbox"].y0 - last["bbox"].y1
                left_gap = abs(cluster["bbox"].x0 - last["bbox"].x0)
                if (
                    0 <= y_gap < _BLOCK_MAX_YGAP_RATIO * last["size"]
                    and left_gap < _CLUSTER_MAX_GAP_RATIO * last["size"]
                ):
                    last["bbox"] |= cluster["bbox"]
                    last["inline"] = last["inline"] and cluster["inline"]
                    placed = True
        if not placed:
            merged.append({**cluster, "bbox": fitz.Rect(cluster["bbox"])})
    regions: list[AnswerRegion] = []
    for entry in merged:
        page = document[entry["page_idx"]]
        width = max(page.rect.width, 1.0)
        height = max(page.rect.height, 1.0)
        bbox = entry["bbox"]
        regions.append(
            AnswerRegion(
                page_idx=entry["page_idx"],
                bbox_norm=(
                    bbox.x0 / width,
                    bbox.y0 / height,
                    bbox.x1 / width,
                    bbox.y1 / height,
                ),
                kind="inline" if entry["inline"] else "block",
            )
        )
    return regions
