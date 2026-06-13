from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

from docx import Document
from docx.oxml import parse_xml
from docx.oxml.ns import qn
from docx.shared import Cm, Inches, Pt

from question_bank.services.question_service import QuestionService
from question_bank.services.rich_content_service import load_question_rich_content
from question_bank.exporters.base_exporter import (
    _resolve_image_path,
    apply_exporter_layout,
    add_paragraph_with_latex,
    extract_and_format_options,
    add_noborder_table,
    latex_to_png
)
from question_bank.exporters.export_config import ExportConfig



LOGGER = logging.getLogger(__name__)
IMAGE_MARKER_PATTERN = re.compile(r"\[\[IMAGE:(?P<path>.+?)\]\]")
REL_EMBED_ATTR = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"
LEADING_QUESTION_NUMBER_PATTERN = re.compile(r"^\s*(?:第\s*)?\d{1,3}\s*(?:[.．、]|题)[ \t]*")


def _image_paths_from_text(value: object) -> list[str]:
    return [match.group("path").strip() for match in IMAGE_MARKER_PATTERN.finditer(str(value or ""))]


def _canonical_type_group(qtype: str | None) -> str:
    qtype = str(qtype or "").strip()
    if qtype in ["choice", "single_choice", "multiple_choice", "multi_choice", "选择题", "多选题"]:
        return "选择题"
    if qtype in ["fill_blank", "blank", "填空题"]:
        return "填空题"
    return "解答题"


def _add_choice_answer_table(document: Document, choice_questions: list[tuple[int, dict[str, Any]]]) -> None:
    if not choice_questions:
        return

    document.add_paragraph("选择题答题区：")
    num_cols = len(choice_questions)

    table = document.add_table(rows=2, cols=num_cols + 1)
    table.style = 'Table Grid'

    table.cell(0, 0).paragraphs[0].text = "题号"
    table.cell(1, 0).paragraphs[0].text = "答案"

    for i, (index, _) in enumerate(choice_questions, start=1):
        table.cell(0, i).paragraphs[0].text = str(index)
        table.cell(1, i).paragraphs[0].text = ""

    document.add_paragraph("")



def export_question_paper_docx(
    db_path: str | Path,
    question_ids: list[int],
    output_dir: str | Path,
    *,
    title: str,
    include_answer: bool = False,
    ensure_previews: bool = True,
    grouped_by_type: bool = False,
    header_text: str | None = None,
    config: ExportConfig | None = None,
) -> Path:
    service = QuestionService(Path(db_path))
    service.initialize_database()
    questions = [service.get_question(int(question_id)) for question_id in question_ids]
    questions = [question for question in questions if question is not None]
    if not questions:
        raise ValueError("试题篮为空，无法导出 Word")

    output_path = _output_path(output_dir, title)
    document = Document()
    active_config = config or ExportConfig()
    apply_exporter_layout(document, active_config)

    if header_text:
        header_p = document.add_paragraph()
        header_p.alignment = 1 # Center
        hrun = header_p.add_run(header_text.strip())
        hrun.font.size = Pt(14)
        hrun.bold = True

    document.add_heading(title.strip() or _default_title(), level=0)
    document.add_paragraph("姓名：________________    班级：________________    日期：________________")
    document.add_paragraph("")


    indexed_questions = list(enumerate(questions, start=1))

    if grouped_by_type:
        choices, blanks, solutions = _group_indexed_questions(questions, active_config.numbering_mode)

        # 1. 选择题
        if choices:
            document.add_heading("一、选择题", level=1)
            _add_choice_answer_table(document, choices)

            for index, question in choices:
                document.add_paragraph(f"{index}. {_source_label(question)}")
                rich_content = load_question_rich_content(int(question["id"]))
                appended, embedded_paths = _add_rich_blocks(document, _rich_blocks(rich_content, "question_blocks"), strip_leading_number=True)

                all_image_paths = _dedupe_paths([
                    *(question.get("image_paths") or []),
                    *_image_paths_from_text(question.get("question_text") or "")
                ])
                resolved_all_paths = []
                for p in all_image_paths:
                    rp = _resolve_image_path(p)
                    if rp:
                        resolved_all_paths.append(str(rp))
                missing_images = [p for p in resolved_all_paths if p not in embedded_paths]

                if not appended:
                    _add_text_and_images(
                        document,
                        question.get("question_text") or "",
                        extra_image_paths=question.get("image_paths") or [],
                        strip_leading_number=True,
                        config=active_config,
                    )
                elif missing_images:
                    _add_images(document, missing_images)
                document.add_paragraph("")

        # 2. 填空题
        if blanks:
            heading_title = "二、填空题" if choices else "一、填空题"
            document.add_heading(heading_title, level=1)
            for index, question in blanks:
                document.add_paragraph(f"{index}. {_source_label(question)}")
                rich_content = load_question_rich_content(int(question["id"]))
                appended, embedded_paths = _add_rich_blocks(document, _rich_blocks(rich_content, "question_blocks"), strip_leading_number=True)

                all_image_paths = _dedupe_paths([
                    *(question.get("image_paths") or []),
                    *_image_paths_from_text(question.get("question_text") or "")
                ])
                resolved_all_paths = []
                for p in all_image_paths:
                    rp = _resolve_image_path(p)
                    if rp:
                        resolved_all_paths.append(str(rp))
                missing_images = [p for p in resolved_all_paths if p not in embedded_paths]

                if not appended:
                    _add_text_and_images(
                        document,
                        question.get("question_text") or "",
                        extra_image_paths=question.get("image_paths") or [],
                        strip_leading_number=True,
                        config=active_config,
                    )
                elif missing_images:
                    _add_images(document, missing_images)
                document.add_paragraph("")

        # 3. 解答题
        if solutions:
            if choices and blanks:
                heading_title = "三、解答题"
            elif choices or blanks:
                heading_title = "二、解答题"
            else:
                heading_title = "一、解答题"
            document.add_heading(heading_title, level=1)
            for index, question in solutions:
                document.add_paragraph(f"{index}. {_source_label(question)}")
                rich_content = load_question_rich_content(int(question["id"]))
                appended, embedded_paths = _add_rich_blocks(document, _rich_blocks(rich_content, "question_blocks"), strip_leading_number=True)

                all_image_paths = _dedupe_paths([
                    *(question.get("image_paths") or []),
                    *_image_paths_from_text(question.get("question_text") or "")
                ])
                resolved_all_paths = []
                for p in all_image_paths:
                    rp = _resolve_image_path(p)
                    if rp:
                        resolved_all_paths.append(str(rp))
                missing_images = [p for p in resolved_all_paths if p not in embedded_paths]

                if not appended:
                    _add_text_and_images(
                        document,
                        question.get("question_text") or "",
                        extra_image_paths=question.get("image_paths") or [],
                        strip_leading_number=True,
                        config=active_config,
                    )
                elif missing_images:
                    _add_images(document, missing_images)
                for _ in range(6):
                    document.add_paragraph("")
                document.add_paragraph("")
    else:
        for index, question in indexed_questions:
            document.add_paragraph(f"{index}. {_source_label(question)}")
            rich_content = load_question_rich_content(int(question["id"]))
            appended, embedded_paths = _add_rich_blocks(document, _rich_blocks(rich_content, "question_blocks"), strip_leading_number=True)

            all_image_paths = _dedupe_paths([
                *(question.get("image_paths") or []),
                *_image_paths_from_text(question.get("question_text") or "")
            ])
            resolved_all_paths = []
            for p in all_image_paths:
                rp = _resolve_image_path(p)
                if rp:
                    resolved_all_paths.append(str(rp))
            missing_images = [p for p in resolved_all_paths if p not in embedded_paths]

            if not appended:
                _add_text_and_images(
                    document,
                    question.get("question_text") or "",
                    extra_image_paths=question.get("image_paths") or [],
                    strip_leading_number=True,
                    config=active_config,
                )
            elif missing_images:
                _add_images(document, missing_images)
            document.add_paragraph("")

    if include_answer:
        document.add_page_break()
        document.add_heading("答案", level=1)

        if grouped_by_type:
            choices, blanks, solutions = _group_indexed_questions(questions, active_config.numbering_mode)

            if choices:
                document.add_heading("选择题答案", level=2)
                for index, question in choices:
                    document.add_paragraph(f"{index}. {_source_label(question)}")
                    rich_content = load_question_rich_content(int(question["id"]))
                    appended, embedded_paths = _add_rich_blocks(document, _rich_blocks(rich_content, "answer_blocks"), strip_leading_number=True)

                    all_image_paths = _dedupe_paths([
                        *_image_paths_from_text(question.get("answer_text") or "")
                    ])
                    resolved_all_paths = []
                    for p in all_image_paths:
                        rp = _resolve_image_path(p)
                        if rp:
                            resolved_all_paths.append(str(rp))
                    missing_images = [p for p in resolved_all_paths if p not in embedded_paths]

                    if not appended:
                        _add_text_and_images(document, question.get("answer_text") or "暂无答案", strip_leading_number=True, config=active_config)
                    elif missing_images:
                        _add_images(document, missing_images)
                    document.add_paragraph("")

            if blanks:
                document.add_heading("填空题答案", level=2)
                for index, question in blanks:
                    document.add_paragraph(f"{index}. {_source_label(question)}")
                    rich_content = load_question_rich_content(int(question["id"]))
                    appended, embedded_paths = _add_rich_blocks(document, _rich_blocks(rich_content, "answer_blocks"), strip_leading_number=True)

                    all_image_paths = _dedupe_paths([
                        *_image_paths_from_text(question.get("answer_text") or "")
                    ])
                    resolved_all_paths = []
                    for p in all_image_paths:
                        rp = _resolve_image_path(p)
                        if rp:
                            resolved_all_paths.append(str(rp))
                    missing_images = [p for p in resolved_all_paths if p not in embedded_paths]

                    if not appended:
                        _add_text_and_images(document, question.get("answer_text") or "暂无答案", strip_leading_number=True, config=active_config)
                    elif missing_images:
                        _add_images(document, missing_images)
                    document.add_paragraph("")

            if solutions:
                document.add_heading("解答题答案", level=2)
                for index, question in solutions:
                    document.add_paragraph(f"{index}. {_source_label(question)}")
                    rich_content = load_question_rich_content(int(question["id"]))
                    appended, embedded_paths = _add_rich_blocks(document, _rich_blocks(rich_content, "answer_blocks"), strip_leading_number=True)

                    all_image_paths = _dedupe_paths([
                        *_image_paths_from_text(question.get("answer_text") or "")
                    ])
                    resolved_all_paths = []
                    for p in all_image_paths:
                        rp = _resolve_image_path(p)
                        if rp:
                            resolved_all_paths.append(str(rp))
                    missing_images = [p for p in resolved_all_paths if p not in embedded_paths]

                    if not appended:
                        _add_text_and_images(document, question.get("answer_text") or "暂无答案", strip_leading_number=True, config=active_config)
                    elif missing_images:
                        _add_images(document, missing_images)
                    document.add_paragraph("")
        else:
            for index, question in indexed_questions:
                document.add_paragraph(f"{index}. {_source_label(question)}")
                rich_content = load_question_rich_content(int(question["id"]))
                appended, embedded_paths = _add_rich_blocks(document, _rich_blocks(rich_content, "answer_blocks"), strip_leading_number=True)

                all_image_paths = _dedupe_paths([
                    *_image_paths_from_text(question.get("answer_text") or "")
                ])
                resolved_all_paths = []
                for p in all_image_paths:
                    rp = _resolve_image_path(p)
                    if rp:
                        resolved_all_paths.append(str(rp))
                missing_images = [p for p in resolved_all_paths if p not in embedded_paths]

                if not appended:
                    _add_text_and_images(document, question.get("answer_text") or "暂无答案", strip_leading_number=True, config=active_config)
                elif missing_images:
                    _add_images(document, missing_images)
                if index < len(questions):
                    document.add_paragraph("")

    document.save(output_path)
    return output_path


def _group_indexed_questions(
    questions: list[dict[str, object]],
    numbering_mode: str = "global",
) -> tuple[list[tuple[int, dict[str, object]]], list[tuple[int, dict[str, object]]], list[tuple[int, dict[str, object]]]]:
    raw_choices: list[dict[str, object]] = []
    raw_blanks: list[dict[str, object]] = []
    raw_solutions: list[dict[str, object]] = []
    for question in questions:
        group = _canonical_type_group(question.get("question_type"))
        if group == "选择题":
            raw_choices.append(question)
        elif group == "填空题":
            raw_blanks.append(question)
        else:
            raw_solutions.append(question)

    grouped: list[list[tuple[int, dict[str, object]]]] = []
    next_index = 1
    for raw_group in (raw_choices, raw_blanks, raw_solutions):
        if numbering_mode == "per_section":
            next_index = 1
        numbered_group = []
        for question in raw_group:
            numbered_group.append((next_index, question))
            next_index += 1
        grouped.append(numbered_group)
    return grouped[0], grouped[1], grouped[2]


def _apply_compact_layout(document: Document) -> None:
    for section in document.sections:
        section.top_margin = Cm(1.3)
        section.bottom_margin = Cm(1.3)
        section.left_margin = Cm(1.45)
        section.right_margin = Cm(1.45)
    normal_style = document.styles["Normal"]
    normal_style.font.name = "SimSun"
    normal_style._element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")  # noqa: SLF001
    normal_style.font.size = Pt(10.5)
    normal_style.paragraph_format.space_before = Pt(0)
    normal_style.paragraph_format.space_after = Pt(0)
    normal_style.paragraph_format.line_spacing = 1.15


def _rich_blocks(rich_content: dict[str, object] | None, key: str) -> list[dict[str, object]]:
    if not isinstance(rich_content, dict):
        return []
    blocks = rich_content.get(key)
    if not isinstance(blocks, list):
        return []
    return [block for block in blocks if isinstance(block, dict)]


def _add_rich_blocks(document: Document, blocks: list[dict[str, object]], *, strip_leading_number: bool = False) -> tuple[bool, set[str]]:
    appended = False
    embedded_paths: set[str] = set()
    for index, block in enumerate(blocks):
        xml = str(block.get("xml") or "").strip()
        if not xml:
            continue
        try:
            element = parse_xml(xml)
            if strip_leading_number and index == 0:
                _strip_leading_question_number_from_element(element)
            block_embedded = _rewrite_image_relationships(document, element, block.get("image_relationships"))
            embedded_paths.update(block_embedded)
            _append_to_document_body(document, element)
            appended = True
        except Exception:  # noqa: BLE001
            LOGGER.exception("Failed to append rich Word paragraph block")
    return appended, embedded_paths


def _rewrite_image_relationships(document: Document, element, image_relationships: object) -> set[str]:
    embedded_paths: set[str] = set()
    if not isinstance(image_relationships, dict):
        return embedded_paths
    relationship_map: dict[str, str] = {}
    for old_relationship_id, image_path in image_relationships.items():
        resolved_path = _resolve_image_path(str(image_path))
        if not resolved_path:
            continue
        try:
            new_relationship_id, _ = document.part.get_or_add_image(str(resolved_path))
            relationship_map[str(old_relationship_id)] = new_relationship_id
            embedded_paths.add(str(resolved_path))
        except Exception:  # noqa: BLE001
            LOGGER.exception("Failed to attach rich Word image %s", resolved_path)
            continue
    if not relationship_map:
        return embedded_paths
    for child in element.iter():
        old_relationship_id = child.get(REL_EMBED_ATTR)
        if old_relationship_id in relationship_map:
            child.set(REL_EMBED_ATTR, relationship_map[old_relationship_id])
    return embedded_paths


def _append_to_document_body(document: Document, element) -> None:
    body = document._body._element  # noqa: SLF001 - python-docx has no public paragraph XML append API.
    if len(body) and str(body[-1].tag).endswith("}sectPr"):
        body.insert(len(body) - 1, element)
    else:
        body.append(element)


def _add_text_and_images(
    document: Document,
    text: object,
    *,
    extra_image_paths: list[object] | None = None,
    strip_leading_number: bool = False,
    config: ExportConfig | None = None,
) -> None:
    active_config = config or ExportConfig()
    raw_text = str(text or "")
    inline_paths = [match.group("path").strip() for match in IMAGE_MARKER_PATTERN.finditer(raw_text)]
    clean_text = IMAGE_MARKER_PATTERN.sub("", raw_text).strip()
    if strip_leading_number:
        clean_text = LEADING_QUESTION_NUMBER_PATTERN.sub("", clean_text, count=1).lstrip()

    if clean_text:
        # Check if we can extract options
        stem, options = extract_and_format_options(clean_text)
        if options:
            # Render stem
            for line in stem.splitlines():
                if line.strip():
                    add_paragraph_with_latex(document, line.strip(), active_config)
            # Render options in table/columns
            max_len = max(len(opt_text) for _, opt_text in options)
            if max_len <= 6:
                table = add_noborder_table(document, 1, 4)
                for i, (label, opt_text) in enumerate(options):
                    cell = table.cell(0, i)
                    p = cell.paragraphs[0]
                    runs = parse_latex_runs(f"{label}. {opt_text}")
                    for run_type, content in runs:
                        if run_type == "text":
                            p.add_run(content)
                        elif run_type == "inline_math":
                            png_path = latex_to_png(content, active_config)
                            if png_path and png_path.exists():
                                from PIL import Image
                                with Image.open(png_path) as img:
                                    w_px, h_px = img.size
                                w_pt = (w_px / active_config.latex_dpi) * 72 * 0.85
                                run = p.add_run()
                                run.add_picture(str(png_path), width=Pt(w_pt))
                            else:
                                p.add_run(f"${content}$")
            elif max_len <= 15:
                table = add_noborder_table(document, 2, 2)
                opt_coords = [(0, 0), (0, 1), (1, 0), (1, 1)]
                for idx, (label, opt_text) in enumerate(options):
                    r_idx, c_idx = opt_coords[idx]
                    cell = table.cell(r_idx, c_idx)
                    p = cell.paragraphs[0]
                    runs = parse_latex_runs(f"{label}. {opt_text}")
                    for run_type, content in runs:
                        if run_type == "text":
                            p.add_run(content)
                        elif run_type == "inline_math":
                            png_path = latex_to_png(content, active_config)
                            if png_path and png_path.exists():
                                from PIL import Image
                                with Image.open(png_path) as img:
                                    w_px, h_px = img.size
                                w_pt = (w_px / active_config.latex_dpi) * 72 * 0.85
                                run = p.add_run()
                                run.add_picture(str(png_path), width=Pt(w_pt))
                            else:
                                p.add_run(f"${content}$")
            else:
                for label, opt_text in options:
                    add_paragraph_with_latex(document, f"{label}. {opt_text}", active_config)
        else:
            for line in clean_text.splitlines():
                if line.strip():
                    add_paragraph_with_latex(document, line.strip(), active_config)

    image_paths = _dedupe_paths([*(extra_image_paths or []), *inline_paths])
    _add_images(document, image_paths)


def _add_images(document: Document, image_paths: list[object]) -> None:
    for image_path in image_paths:
        path = Path(str(image_path))
        if not path.exists():
            continue
        try:
            document.add_picture(str(path), width=Inches(4.8))
        except Exception:  # noqa: BLE001
            continue


def _dedupe_paths(paths: list[object]) -> list[str]:
    deduped: list[str] = []
    for path in paths:
        text = str(path or "").strip()
        if text and text not in deduped:
            deduped.append(text)
    return deduped


def _strip_leading_question_number_from_element(element) -> None:
    for child in element.iter():
        if child.tag != qn("w:t"):
            continue
        text = child.text or ""
        if not text:
            continue
        stripped = LEADING_QUESTION_NUMBER_PATTERN.sub("", text, count=1)
        if stripped != text:
            child.text = stripped.lstrip()
        return


def _output_path(output_dir: str | Path, title: str) -> Path:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    safe_title = _safe_filename(title) or "question_paper"
    return output_dir / f"{safe_title}_{timestamp}.docx"


def _default_title() -> str:
    return f"{datetime.now().strftime('%Y-%m-%d')}习题"


def _safe_filename(value: str) -> str:
    return re.sub(r'[<>:"/\\|?*\s]+', "_", str(value or "")).strip("._")


def _source_label(question: dict) -> str:
    parts = [question.get("year"), question.get("district"), question.get("exam_type")]
    label = " ".join(str(part).strip() for part in parts if str(part or "").strip())
    number = str(question.get("question_number") or "").strip()
    if number:
        return f"{label or '本地题库'} 第{number}题"
    return label or "本地题库"


__all__ = ["export_question_paper_docx"]
