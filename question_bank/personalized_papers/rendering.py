from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from io import BytesIO
from pathlib import Path
from typing import Any, Protocol

import cv2
import fitz
from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Mm, Pt, RGBColor
from PIL import Image


LAYOUT_VERSION = "personalized-paper-school-a4-v1"
PAGE_IDENTITY_VERSION = "P4P2"
LEGACY_PAGE_IDENTITY_VERSION = "P4P1"
DOCX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
PDF_MEDIA_TYPE = "application/pdf"

_INK = RGBColor(30, 41, 59)
_BLUE = RGBColor(30, 84, 120)
_MUTED = RGBColor(100, 116, 139)
_LIGHT = "E8EEF5"
_FONT_LATIN = "Calibri"
_FONT_EAST_ASIA = "Microsoft YaHei"


class PaperRenderError(RuntimeError):
    pass


class PdfConversionAdapter(Protocol):
    def convert(self, source_docx: Path, output_pdf: Path) -> None: ...


class OfficePdfConverter:
    """Convert a reviewed DOCX through WPS/Word or a local LibreOffice binary."""

    def __init__(self, *, timeout_seconds: int = 120) -> None:
        self.timeout_seconds = int(timeout_seconds)

    def convert(self, source_docx: Path, output_pdf: Path) -> None:
        source = Path(source_docx).resolve()
        destination = Path(output_pdf).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            script = Path(__file__).with_name("office_to_pdf.ps1")
            pwsh = shutil.which("pwsh") or r"C:\Program Files\PowerShell\7\pwsh.exe"
            if Path(pwsh).exists() and script.is_file():
                completed = subprocess.run(
                    [
                        str(pwsh),
                        "-NoLogo",
                        "-NoProfile",
                        "-File",
                        str(script),
                        "-InputPath",
                        str(source),
                        "-OutputPath",
                        str(destination),
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if (
                    completed.returncode == 0
                    and destination.is_file()
                    and destination.stat().st_size > 0
                ):
                    return
        executable = shutil.which("soffice") or shutil.which("libreoffice")
        if executable:
            completed = subprocess.run(
                [
                    executable,
                    "--headless",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    str(destination.parent),
                    str(source),
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
            produced = destination.parent / f"{source.stem}.pdf"
            if completed.returncode == 0 and produced.is_file():
                if produced != destination:
                    os.replace(produced, destination)
                if destination.stat().st_size > 0:
                    return
        raise PaperRenderError("no compatible DOCX to PDF converter is available")


def render_review_docx(
    snapshot: Mapping[str, Any],
    *,
    data_root: Path,
    output_path: Path,
) -> None:
    document = Document()
    _configure_document(document)
    student = _mapping(snapshot.get("student"))
    items = _mappings(snapshot.get("items"))
    paper_id = str(snapshot["paper_instance_id"])
    series_version = int(snapshot["series_version"])
    budget = _mapping(snapshot.get("budget"))

    header = document.sections[0].header.paragraphs[0]
    header.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _add_run(
        header,
        (
            f"{student.get('student_name') or student.get('student_code') or student.get('student_id')}"
            f"  ·  个性化训练卷 V{series_version}"
        ),
        size=9,
        color=_MUTED,
    )

    title = document.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title.paragraph_format.space_after = Pt(4)
    _add_run(title, "个性化训练卷（审核稿）", size=22, bold=True, color=_BLUE)
    subtitle = document.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    subtitle.paragraph_format.space_after = Pt(12)
    _add_run(
        subtitle,
        "请在 WPS 中检查题目、分页与留白；确认后再生成冻结 PDF。",
        size=10,
        color=_MUTED,
    )

    metadata = document.add_table(rows=2, cols=3)
    metadata.autofit = False
    _set_fixed_table_geometry(
        metadata,
        column_widths=(3288, 3288, 3289),
        total_width=9865,
        indent=120,
    )
    cells = [
        ("学生", str(student.get("student_name") or student.get("student_code") or "学生")),
        ("班级", str(student.get("class_id") or "-")),
        ("版本", f"V{series_version}"),
        ("题量", f"{len(items)} 题"),
        ("预计时长", f"{int(snapshot.get('estimated_minutes') or 0)} 分钟"),
        (
            "判定预算",
            f"{int(budget.get('estimated_total_tokens') or 0)} / "
            f"{int(budget.get('context_window_tokens') or 0)}",
        ),
    ]
    for index, (label, value) in enumerate(cells):
        cell = metadata.cell(index // 3, index % 3)
        cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        cell.text = ""
        paragraph = cell.paragraphs[0]
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        paragraph.paragraph_format.space_before = Pt(3)
        paragraph.paragraph_format.space_after = Pt(3)
        _add_run(paragraph, f"{label}\n", size=8.5, color=_MUTED)
        _add_run(paragraph, value, size=10.5, bold=True, color=_INK)
        _cell_shading(cell, _LIGHT)
    _set_table_borders(metadata, "D7DEE8")

    identity = document.add_paragraph()
    identity.paragraph_format.space_before = Pt(8)
    identity.paragraph_format.space_after = Pt(10)
    _add_run(
        identity,
        f"卷实例：{paper_id}  ·  版式：{LAYOUT_VERSION}",
        size=7.5,
        color=_MUTED,
    )

    for index, item in enumerate(items, start=1):
        if index > 1:
            separator = document.add_paragraph()
            separator.paragraph_format.keep_with_next = True
            separator.paragraph_format.space_before = Pt(8)
            separator.paragraph_format.space_after = Pt(6)
            _paragraph_bottom_border(separator, color="CBD5E1", size="6")
        question = _mapping(item.get("question_snapshot"))
        context = _mapping(question.get("tagging_context"))
        recommendation = _mapping(item.get("recommendation_snapshot"))
        question_number = str(
            recommendation.get("question_number")
            or context.get("question_number")
            or index
        )
        source = str(recommendation.get("source_paper") or "")
        heading = document.add_paragraph()
        heading.paragraph_format.keep_with_next = True
        heading.paragraph_format.space_before = Pt(4)
        heading.paragraph_format.space_after = Pt(4)
        _add_run(
            heading,
            f"{index}. {source + ' · ' if source else ''}原题第 {question_number} 题",
            size=12,
            bold=True,
            color=_INK,
        )
        body = document.add_paragraph()
        body.paragraph_format.keep_with_next = True
        body.paragraph_format.space_after = Pt(6)
        body.paragraph_format.line_spacing = 1.25
        _add_run(
            body,
            str(context.get("question_text") or ""),
            size=11,
            color=_INK,
        )
        image_paragraph_start = len(document.paragraphs)
        _add_question_images(
            document,
            question,
            data_root=Path(data_root),
        )
        for image_paragraph in document.paragraphs[image_paragraph_start:]:
            image_paragraph.paragraph_format.keep_with_next = True
        marker = document.add_paragraph()
        marker.paragraph_format.keep_with_next = True
        marker.paragraph_format.space_after = Pt(3)
        _add_run(
            marker,
            f"任务题码：{item['task_item_code']}",
            size=7.5,
            color=_MUTED,
        )
        answer_lines = _answer_line_count(str(context.get("question_type") or ""))
        for line_index in range(answer_lines):
            line = document.add_paragraph()
            line.paragraph_format.keep_with_next = (
                line_index < answer_lines - 1
            )
            line.paragraph_format.space_before = Pt(5)
            line.paragraph_format.space_after = Pt(5)
            _paragraph_bottom_border(line, color="CBD5E1", size="4")

    footer = document.sections[0].footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _add_run(footer, "审核稿 · 尚未冻结 · ", size=8, color=_MUTED)
    _add_run(footer, "第 ", size=8, color=_MUTED)
    _add_field(footer, "PAGE")
    _add_run(footer, " 页 / 共 ", size=8, color=_MUTED)
    _add_field(footer, "NUMPAGES")
    _add_run(footer, " 页", size=8, color=_MUTED)

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    document.save(output)
    if not output.is_file() or output.stat().st_size <= 0:
        raise PaperRenderError("review DOCX was not created")


def stamp_frozen_pdf(
    source_pdf: Path,
    output_pdf: Path,
    *,
    paper_instance_id: str,
    paper_batch_id: str,
    series_version: int,
    signing_secret: str,
    reviewed_docx_sha256: str,
    layout_version: str = LAYOUT_VERSION,
) -> tuple[dict[str, Any], ...]:
    source = fitz.open(source_pdf)
    try:
        if source.page_count <= 0:
            raise PaperRenderError("converted PDF has no pages")
        total_pages = source.page_count
        pages: list[dict[str, Any]] = []
        for page_number, page in enumerate(source, start=1):
            signature = page_signature(
                signing_secret=signing_secret,
                paper_batch_id=paper_batch_id,
                paper_instance_id=paper_instance_id,
                series_version=series_version,
                page_number=page_number,
                total_pages=total_pages,
                reviewed_docx_sha256=reviewed_docx_sha256,
                layout_version=layout_version,
            )
            identity = page_identity(
                paper_instance_id=paper_instance_id,
                series_version=series_version,
                page_number=page_number,
                total_pages=total_pages,
                signature=signature,
            )
            qr = _qr_png(identity)
            width = float(page.rect.width)
            height = float(page.rect.height)
            footer_top = max(height - 62.0, 0.0)
            page.draw_rect(
                fitz.Rect(22.0, footer_top - 3.0, width - 18.0, height - 8.0),
                color=(1, 1, 1),
                fill=(1, 1, 1),
                overlay=True,
            )
            page.insert_text(
                fitz.Point(26.0, footer_top + 13.0),
                f"Page {page_number}/{total_pages}  Instance {paper_instance_id[:12]}",
                fontsize=7.5,
                fontname="helv",
                color=(0.25, 0.32, 0.4),
                overlay=True,
            )
            page.insert_text(
                fitz.Point(26.0, footer_top + 27.0),
                f"Identity {signature[:16]}  Layout {layout_version}",
                fontsize=6.5,
                fontname="helv",
                color=(0.35, 0.4, 0.47),
                overlay=True,
            )
            page.insert_image(
                fitz.Rect(width - 70.0, footer_top, width - 18.0, footer_top + 52.0),
                stream=qr,
                overlay=True,
            )
            pages.append(
                {
                    "page_number": page_number,
                    "total_pages": total_pages,
                    "page_identity": identity,
                    "page_signature": signature,
                    "layout_version": layout_version,
                    "width_points": width,
                    "height_points": height,
                }
            )
        output = Path(output_pdf)
        output.parent.mkdir(parents=True, exist_ok=True)
        source.save(output, garbage=4, deflate=True)
    finally:
        source.close()

    pdf_sha256 = _file_sha256(output_pdf)
    with fitz.open(output_pdf) as frozen:
        if frozen.page_count != len(pages):
            raise PaperRenderError("frozen PDF page count changed")
        finalized: list[dict[str, Any]] = []
        for page, item in zip(frozen, pages, strict=True):
            pixmap = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
            finalized.append(
                {
                    **item,
                    "page_content_hash": hashlib.sha256(
                        pixmap.samples
                    ).hexdigest(),
                    "pdf_sha256": pdf_sha256,
                }
            )
    return tuple(finalized)


def page_identity(
    *,
    paper_instance_id: str,
    series_version: int,
    page_number: int,
    total_pages: int,
    signature: str,
) -> str:
    # P4P2 stays inside QR's compact alphanumeric mode. The previous
    # lowercase, pipe-delimited byte payload produced data-dependent OpenCV
    # decode failures even before printing.
    return ":".join(
        (
            PAGE_IDENTITY_VERSION,
            paper_instance_id.upper(),
            str(int(series_version)),
            str(int(page_number)),
            str(int(total_pages)),
            signature.upper(),
        )
    )


def page_signature(
    *,
    signing_secret: str,
    paper_batch_id: str,
    paper_instance_id: str,
    series_version: int,
    page_number: int,
    total_pages: int,
    reviewed_docx_sha256: str,
    layout_version: str,
    identity_version: str = PAGE_IDENTITY_VERSION,
) -> str:
    message = "|".join(
        (
            identity_version,
            paper_batch_id,
            paper_instance_id,
            str(int(series_version)),
            str(int(page_number)),
            str(int(total_pages)),
            reviewed_docx_sha256,
            layout_version,
        )
    )
    return hmac.new(
        bytes.fromhex(signing_secret),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:32]


def decode_page_identity(value: str) -> dict[str, Any]:
    raw = str(value or "")
    if raw.startswith(f"{PAGE_IDENTITY_VERSION}:"):
        parts = raw.split(":")
    elif raw.startswith(f"{LEGACY_PAGE_IDENTITY_VERSION}|"):
        parts = raw.split("|")
    else:
        raise ValueError("page identity is invalid")
    if len(parts) != 6 or parts[0] not in {
        PAGE_IDENTITY_VERSION,
        LEGACY_PAGE_IDENTITY_VERSION,
    }:
        raise ValueError("page identity is invalid")
    instance_id, version, page, total, signature = parts[1:]
    if (
        len(instance_id) != 64
        or len(signature) != 32
    ):
        raise ValueError("page identity is invalid")
    try:
        version_number = int(version)
        page_number = int(page)
        total_pages = int(total)
    except ValueError as exc:
        raise ValueError("page identity is invalid") from exc
    if (
        version_number <= 0
        or page_number <= 0
        or total_pages <= 0
        or page_number > total_pages
    ):
        raise ValueError("page identity is invalid")
    return {
        "identity_version": parts[0],
        "paper_instance_id": instance_id.casefold(),
        "series_version": version_number,
        "page_number": page_number,
        "total_pages": total_pages,
        "page_signature": signature.casefold(),
    }


def inspect_docx(
    source: Path,
    *,
    paper_instance_id: str,
    task_item_codes: Sequence[str],
    question_texts: Sequence[str],
) -> None:
    try:
        document = Document(source)
    except Exception as exc:  # noqa: BLE001
        raise PaperRenderError("reviewed file is not a readable DOCX") from exc
    text = "\n".join(
        paragraph.text
        for paragraph in _all_paragraphs(document)
        if paragraph.text
    )
    normalized = _normalized_text(text)
    instance_marker = _normalized_text(paper_instance_id)
    if not instance_marker or instance_marker not in normalized:
        raise PaperRenderError(
            "reviewed DOCX no longer matches the frozen question list"
        )
    _assert_ordered_text(normalized, task_item_codes)
    _assert_ordered_text(normalized, question_texts)


def pdf_page_count(path: Path) -> int:
    try:
        with fitz.open(path) as document:
            return int(document.page_count)
    except Exception as exc:  # noqa: BLE001
        raise PaperRenderError("PDF output is invalid") from exc


def _configure_document(document: Document) -> None:
    section = document.sections[0]
    section.page_width = Mm(210)
    section.page_height = Mm(297)
    section.top_margin = Mm(20)
    section.bottom_margin = Mm(22)
    section.left_margin = Mm(18)
    section.right_margin = Mm(18)
    section.header_distance = Mm(8)
    section.footer_distance = Mm(8)
    section.start_type = WD_SECTION.NEW_PAGE
    styles = document.styles
    normal = styles["Normal"]
    normal.font.name = _FONT_LATIN
    normal.font.size = Pt(11)
    normal.font.color.rgb = _INK
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), _FONT_EAST_ASIA)
    normal.paragraph_format.space_after = Pt(6)
    normal.paragraph_format.line_spacing = 1.25
    for name, size, color, before, after in (
        ("Heading 1", 16, _BLUE, 18, 10),
        ("Heading 2", 13, _BLUE, 14, 7),
        ("Heading 3", 12, _INK, 10, 5),
    ):
        style = styles[name]
        style.font.name = _FONT_LATIN
        style.font.size = Pt(size)
        style.font.color.rgb = color
        style._element.rPr.rFonts.set(qn("w:eastAsia"), _FONT_EAST_ASIA)
        style.paragraph_format.space_before = Pt(before)
        style.paragraph_format.space_after = Pt(after)


def _add_question_images(
    document: Document,
    question: Mapping[str, Any],
    *,
    data_root: Path,
) -> None:
    images = _mappings(question.get("images"))
    for image in images:
        if image.get("role") != "question":
            continue
        relative = Path(str(image.get("asset_path") or ""))
        resolved = (data_root / relative).resolve()
        root = data_root.resolve()
        if root not in resolved.parents or not resolved.is_file():
            raise PaperRenderError("frozen question image is missing")
        payload = resolved.read_bytes()
        try:
            document.add_picture(BytesIO(payload), width=Mm(120))
        except Exception:
            with Image.open(BytesIO(payload)) as source:
                converted = BytesIO()
                source.convert("RGB").save(converted, format="PNG")
                converted.seek(0)
                document.add_picture(converted, width=Mm(120))


def _answer_line_count(question_type: str) -> int:
    value = str(question_type or "").casefold()
    if any(token in value for token in ("选择", "choice", "填空", "fill")):
        return 2
    if any(token in value for token in ("证明", "proof", "作图", "construction")):
        return 7
    return 5


def _add_run(
    paragraph,
    text: str,
    *,
    size: float,
    color: RGBColor,
    bold: bool = False,
) -> None:
    run = paragraph.add_run(text)
    run.font.name = _FONT_LATIN
    run.font.size = Pt(size)
    run.font.color.rgb = color
    run.bold = bold
    run._element.rPr.rFonts.set(qn("w:eastAsia"), _FONT_EAST_ASIA)


def _add_field(paragraph, instruction: str) -> None:
    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    code = OxmlElement("w:instrText")
    code.set(qn("xml:space"), "preserve")
    code.text = instruction
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    text = OxmlElement("w:t")
    text.text = "1"
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run._r.extend((begin, code, separate, text, end))


def _paragraph_bottom_border(paragraph, *, color: str, size: str) -> None:
    properties = paragraph._p.get_or_add_pPr()
    borders = properties.find(qn("w:pBdr"))
    if borders is None:
        borders = OxmlElement("w:pBdr")
        properties.append(borders)
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), size)
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), color)
    borders.append(bottom)


def _cell_shading(cell, fill: str) -> None:
    properties = cell._tc.get_or_add_tcPr()
    shading = OxmlElement("w:shd")
    shading.set(qn("w:fill"), fill)
    properties.append(shading)


def _set_table_borders(table, color: str) -> None:
    properties = table._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        element = OxmlElement(f"w:{edge}")
        element.set(qn("w:val"), "single")
        element.set(qn("w:sz"), "4")
        element.set(qn("w:space"), "0")
        element.set(qn("w:color"), color)
        borders.append(element)
    properties.append(borders)


def _set_fixed_table_geometry(
    table,
    *,
    column_widths: Sequence[int],
    total_width: int,
    indent: int,
) -> None:
    properties = table._tbl.tblPr
    for tag, value, width_type in (
        ("w:tblW", total_width, "dxa"),
        ("w:tblInd", indent, "dxa"),
    ):
        element = properties.find(qn(tag))
        if element is None:
            element = OxmlElement(tag)
            properties.append(element)
        element.set(qn("w:w"), str(int(value)))
        element.set(qn("w:type"), width_type)
    layout = properties.find(qn("w:tblLayout"))
    if layout is None:
        layout = OxmlElement("w:tblLayout")
        properties.append(layout)
    layout.set(qn("w:type"), "fixed")
    grid_columns = list(table._tbl.tblGrid)
    for index, width in enumerate(column_widths):
        if index < len(grid_columns):
            grid_column = grid_columns[index]
        else:
            grid_column = OxmlElement("w:gridCol")
            table._tbl.tblGrid.append(grid_column)
        grid_column.set(qn("w:w"), str(int(width)))
    for row in table.rows:
        for index, cell in enumerate(row.cells):
            properties = cell._tc.get_or_add_tcPr()
            width = properties.find(qn("w:tcW"))
            if width is None:
                width = OxmlElement("w:tcW")
                properties.append(width)
            width.set(qn("w:w"), str(int(column_widths[index])))
            width.set(qn("w:type"), "dxa")


def _qr_png(payload: str) -> bytes:
    params = cv2.QRCodeEncoder_Params()
    params.correction_level = cv2.QRCodeEncoder_CORRECT_LEVEL_M
    # A fixed, slightly roomier symbol avoids OpenCV's data-dependent AUTO
    # version choices while the P4P2 payload remains comfortably within
    # capacity.
    params.version = 6
    encoder = cv2.QRCodeEncoder_create(params)
    image = encoder.encode(payload)
    image = cv2.copyMakeBorder(
        image,
        4,
        4,
        4,
        4,
        cv2.BORDER_CONSTANT,
        value=255,
    )
    image = cv2.resize(
        image,
        None,
        fx=5,
        fy=5,
        interpolation=cv2.INTER_NEAREST,
    )
    success, encoded = cv2.imencode(".png", image)
    if not success:
        raise PaperRenderError("page identity QR could not be encoded")
    return bytes(encoded)


def _all_paragraphs(document: Document):
    for paragraph in document.paragraphs:
        yield paragraph
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                yield from cell.paragraphs
    for section in document.sections:
        yield from section.header.paragraphs
        yield from section.footer.paragraphs


def _normalized_text(value: object) -> str:
    return "".join(str(value or "").split())


def _assert_ordered_text(
    haystack: str,
    values: Sequence[str],
) -> None:
    cursor = 0
    for value in values:
        marker = _normalized_text(value)
        position = haystack.find(marker, cursor)
        if not marker or position < 0:
            raise PaperRenderError(
                "reviewed DOCX no longer matches the frozen question list"
            )
        cursor = position + len(marker)


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _mappings(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = [
    "DOCX_MEDIA_TYPE",
    "LAYOUT_VERSION",
    "OfficePdfConverter",
    "PDF_MEDIA_TYPE",
    "PAGE_IDENTITY_VERSION",
    "PaperRenderError",
    "PdfConversionAdapter",
    "decode_page_identity",
    "inspect_docx",
    "page_signature",
    "pdf_page_count",
    "render_review_docx",
    "stamp_frozen_pdf",
]
