from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, Pt

from question_bank.services.question_read_service import QuestionBankReadService
from question_bank.services.assembly_basket_state import SectionSpec
from question_bank.exporters.base_exporter import (
    _resolve_image_path,
    apply_exporter_layout,
    extract_and_format_options,
    add_noborder_table,
)
from question_bank.document_pipeline.word_renderer import (
    SharedWordQuestionRenderer,
    WordStyleProfile,
    add_answer_space,
    answer_space_lines,
)
from question_bank.document_pipeline.contracts import FormulaFallback, MathExpression
from question_bank.document_pipeline.legacy_exports import (
    PublishedMathMetadata,
    data_root_for_database,
    published_math_metadata,
    save_validated_legacy_export,
)
from question_bank.exporters.export_config import ExportConfig



LOGGER = logging.getLogger(__name__)
IMAGE_MARKER_PATTERN = re.compile(r"\[\[IMAGE:(?P<path>.+?)\]\]")
LEADING_QUESTION_NUMBER_PATTERN = re.compile(r"^\s*(?:第\s*)?\d{1,3}\s*(?:[.．、]|题)[ \t]*")

_CN_SECTION_NUMS = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十",
                     "十一", "十二", "十三", "十四", "十五", "十六", "十七", "十八", "十九", "二十"]


def _image_paths_from_text(value: object) -> list[str]:
    return [match.group("path").strip() for match in IMAGE_MARKER_PATTERN.finditer(str(value or ""))]


def _canonical_type_group(qtype: str | None) -> str:
    qtype = str(qtype or "").strip()
    if qtype in ["choice", "single_choice", "multiple_choice", "multi_choice", "选择题", "多选题"]:
        return "选择题"
    if qtype in ["fill_blank", "blank", "填空题"]:
        return "填空题"
    return "解答题"


def _section_index_label(index: int) -> str:
    if 0 <= index < len(_CN_SECTION_NUMS):
        return _CN_SECTION_NUMS[index]
    return str(index + 1)


def _render_question_body(
    document: Document,
    question: dict[str, Any],
    index: int,
    config: ExportConfig,
    metadata_by_id: dict[int, PublishedMathMetadata],
    data_root: Path,
    fallbacks: list[FormulaFallback],
    *,
    trailing_blank: int = 0,
) -> None:
    """渲染单个题目的正文（题号 + 富文本/纯文本 + 图片）。

    抽出来供 sections 分支复用，消除原三段式里重复的渲染块。
    """
    inline_prefix = _question_prefix(index)
    question_paragraph_start = len(document.paragraphs)
    minimum_lines = answer_space_lines(
        question.get("question_type"),
        question.get("question_text"),
    )
    style_profile = WordStyleProfile.from_export_config(config)
    metadata = metadata_by_id[int(question["id"])]
    rich_content = metadata.rich_content
    appended, embedded_paths = _add_rich_blocks(
        document,
        _rich_blocks(rich_content, "question_blocks"),
        strip_leading_number=True,
        inline_prefix=inline_prefix,
        config=config,
        compact_standalone_images_with_text=(minimum_lines == 0),
    )

    all_image_paths = _dedupe_paths([
        *(question.get("image_paths") or []),
        *_image_paths_from_text(question.get("question_text") or "")
    ])
    resolved_all_paths = []
    for p in all_image_paths:
        rp = _resolve_image_path(p, data_root=data_root)
        if rp:
            resolved_all_paths.append(str(rp))
    missing_images = [p for p in resolved_all_paths if p not in embedded_paths]

    if not appended:
        _add_text_and_images(
            document,
            question.get("question_text") or "",
            extra_image_paths=question.get("image_paths") or [],
            strip_leading_number=True,
            config=config,
            question_id=f"question-{int(question['id'])}",
            expressions=metadata.expressions,
            fallback_sink=fallbacks,
            data_root=data_root,
            inline_prefix=inline_prefix,
        )
    elif missing_images:
        _add_images(document, missing_images)
    if minimum_lines:
        add_answer_space(
            document,
            question_paragraphs=document.paragraphs[question_paragraph_start:],
            minimum_lines=minimum_lines,
            content_width_dxa=style_profile.content_width_dxa,
        )
    else:
        for _ in range(trailing_blank):
            document.add_paragraph("")


def _render_answer_body(
    document: Document,
    question: dict[str, Any],
    index: int,
    config: ExportConfig,
    metadata_by_id: dict[int, PublishedMathMetadata],
    data_root: Path,
    fallbacks: list[FormulaFallback],
    *,
    trailing_blank: int = 1,
) -> None:
    """渲染单个题目的答案正文。"""
    inline_prefix = _question_prefix(index)
    metadata = metadata_by_id[int(question["id"])]
    rich_content = metadata.rich_content
    appended, embedded_paths = _add_rich_blocks(
        document,
        _rich_blocks(rich_content, "answer_blocks"),
        strip_leading_number=True,
        inline_prefix=inline_prefix,
        config=config,
    )

    all_image_paths = _dedupe_paths([*_image_paths_from_text(question.get("answer_text") or "")])
    resolved_all_paths = []
    for p in all_image_paths:
        rp = _resolve_image_path(p, data_root=data_root)
        if rp:
            resolved_all_paths.append(str(rp))
    missing_images = [p for p in resolved_all_paths if p not in embedded_paths]

    if not appended:
        _add_text_and_images(
            document,
            question.get("answer_text") or "暂无答案",
            strip_leading_number=True,
            config=config,
            question_id=f"question-{int(question['id'])}-answer",
            fallback_sink=fallbacks,
            data_root=data_root,
            inline_prefix=inline_prefix,
        )
    elif missing_images:
        _add_images(document, missing_images)
    for _ in range(trailing_blank):
        document.add_paragraph("")


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
    sections: list[SectionSpec] | None = None,
    config: ExportConfig | None = None,
) -> Path:
    service = QuestionBankReadService(Path(db_path))
    questions = service.get_questions_for_export(question_ids)
    questions = [question for question in questions if question is not None]
    if not questions:
        raise ValueError("试题篮为空，无法导出 Word")

    output_path = _output_path(output_dir, title)
    document = Document()
    active_config = config or ExportConfig()
    apply_exporter_layout(document, active_config)
    data_root = data_root_for_database(db_path)
    style_profile = WordStyleProfile.from_export_config(active_config)
    metadata_by_id = {
        int(question["id"]): published_math_metadata(
            int(question["id"]),
            data_root=data_root,
            question_text=str(question.get("question_text") or ""),
            answer_text=str(question.get("answer_text") or ""),
        )
        for question in questions
    }
    fallbacks: list[FormulaFallback] = []

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

    if sections:
        # AI 智能分类编排：按外部传入的 sections 渲染
        question_by_id_docx = {int(q["id"]): q for q in questions}
        next_index = 1
        # 为答案区准备：每个 section 的 [(index, question)] 列表
        sections_indexed: list[tuple[str, list[tuple[int, dict[str, Any]]]]] = []

        for sec_idx, sec in enumerate(sections):
            sec_questions: list[tuple[int, dict[str, Any]]] = []
            for qid in sec.question_ids:
                q = question_by_id_docx.get(int(qid))
                if not q:
                    continue
                sec_questions.append((next_index, q))
                next_index += 1
            if not sec_questions:
                continue
            heading = f"{_section_index_label(sec_idx)}、{sec.title.strip()}"
            document.add_heading(heading, level=1)

            # 选择题答题区表格：段内若含选择题则触发
            choice_in_sec = [(idx, q) for idx, q in sec_questions if _canonical_type_group(q.get("question_type")) == "选择题"]
            if choice_in_sec:
                _add_choice_answer_table(document, choice_in_sec)

            for idx, q in sec_questions:
                _render_question_body(
                    document,
                    q,
                    idx,
                    active_config,
                    metadata_by_id,
                    data_root,
                    fallbacks,
                    trailing_blank=0,
                )

            sections_indexed.append((sec.title.strip(), sec_questions))

        # 答案区：同样按 sections 分段
        if include_answer:
            document.add_page_break()
            document.add_heading("答案", level=1)
            for sec_idx, (sec_title, sec_qs) in enumerate(sections_indexed):
                heading = f"{_section_index_label(sec_idx)}、{sec_title} 答案"
                document.add_heading(heading, level=2)
                for idx, q in sec_qs:
                    _render_answer_body(
                        document,
                        q,
                        idx,
                        active_config,
                        metadata_by_id,
                        data_root,
                        fallbacks,
                        trailing_blank=1,
                    )

        save_validated_legacy_export(
            document,
            output_path,
            operation_namespace="ordinary-word",
            question_ids=tuple(f"question-{int(item['id'])}" for item in questions),
            content_revisions=tuple(
                metadata_by_id[int(item["id"])].content_revision for item in questions
            ),
            style=style_profile,
            fallbacks=fallbacks,
        )
        return output_path

    if grouped_by_type:
        choices, blanks, solutions = _group_indexed_questions(questions, active_config.numbering_mode)

        if choices:
            document.add_heading("一、选择题", level=1)
            _add_choice_answer_table(document, choices)
            for index, question in choices:
                _render_question_body(
                    document, question, index, active_config,
                    metadata_by_id, data_root, fallbacks, trailing_blank=0,
                )

        if blanks:
            heading_title = "二、填空题" if choices else "一、填空题"
            document.add_heading(heading_title, level=1)
            for index, question in blanks:
                _render_question_body(
                    document, question, index, active_config,
                    metadata_by_id, data_root, fallbacks, trailing_blank=0,
                )

        if solutions:
            if choices and blanks:
                heading_title = "三、解答题"
            elif choices or blanks:
                heading_title = "二、解答题"
            else:
                heading_title = "一、解答题"
            document.add_heading(heading_title, level=1)
            for index, question in solutions:
                _render_question_body(
                    document, question, index, active_config,
                    metadata_by_id, data_root, fallbacks, trailing_blank=0,
                )
    else:
        for index, question in indexed_questions:
            _render_question_body(
                document, question, index, active_config,
                metadata_by_id, data_root, fallbacks, trailing_blank=0,
            )

    if include_answer:
        document.add_page_break()
        document.add_heading("答案", level=1)

        if grouped_by_type:
            choices, blanks, solutions = _group_indexed_questions(questions, active_config.numbering_mode)
            for heading, group in (
                ("选择题答案", choices),
                ("填空题答案", blanks),
                ("解答题答案", solutions),
            ):
                if not group:
                    continue
                document.add_heading(heading, level=2)
                for index, question in group:
                    _render_answer_body(
                        document, question, index, active_config,
                        metadata_by_id, data_root, fallbacks, trailing_blank=1,
                    )
        else:
            for index, question in indexed_questions:
                _render_answer_body(
                    document, question, index, active_config,
                    metadata_by_id, data_root, fallbacks,
                    trailing_blank=1 if index < len(questions) else 0,
                )

    save_validated_legacy_export(
        document,
        output_path,
        operation_namespace="ordinary-word",
        question_ids=tuple(f"question-{int(item['id'])}" for item in questions),
        content_revisions=tuple(
            metadata_by_id[int(item["id"])].content_revision for item in questions
        ),
        style=style_profile,
        fallbacks=fallbacks,
    )
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


def _rich_blocks(rich_content: dict[str, object] | None, key: str) -> list[dict[str, object]]:
    if not isinstance(rich_content, dict):
        return []
    blocks = rich_content.get(key)
    if not isinstance(blocks, list):
        return []
    return [block for block in blocks if isinstance(block, dict)]


def _add_rich_blocks(
    document: Document,
    blocks: list[dict[str, object]],
    *,
    strip_leading_number: bool = False,
    inline_prefix: str = "",
    config: ExportConfig | None = None,
    compact_standalone_images_with_text: bool = False,
) -> tuple[bool, set[str]]:
    renderer = SharedWordQuestionRenderer(
        style=WordStyleProfile.from_export_config(config or ExportConfig()),
        asset_resolver=_resolve_image_path,
    )
    result = renderer.add_rich_blocks(
        document,
        blocks,
        strip_leading_number=strip_leading_number,
        inline_prefix=inline_prefix,
        compact_standalone_images_with_text=compact_standalone_images_with_text,
    )
    return result.appended, set(result.embedded_assets)


def _add_text_and_images(
    document: Document,
    text: object,
    *,
    extra_image_paths: list[object] | None = None,
    strip_leading_number: bool = False,
    config: ExportConfig | None = None,
    question_id: str = "ordinary-export",
    expressions: tuple[MathExpression, ...] = (),
    fallback_sink: list[FormulaFallback] | None = None,
    data_root: str | Path | None = None,
    inline_prefix: str = "",
) -> None:
    active_config = config or ExportConfig()
    raw_text = str(text or "")
    inline_paths = [match.group("path").strip() for match in IMAGE_MARKER_PATTERN.finditer(raw_text)]
    clean_text = IMAGE_MARKER_PATTERN.sub("", raw_text).strip()
    if strip_leading_number:
        clean_text = LEADING_QUESTION_NUMBER_PATTERN.sub("", clean_text, count=1).lstrip()

    if clean_text:
        renderer = SharedWordQuestionRenderer(
            style=WordStyleProfile.from_export_config(active_config),
            asset_resolver=lambda value: _resolve_image_path(value, data_root=data_root),
        )

        prefix_pending = inline_prefix

        def render_line(value: str, *, suffix: str) -> None:
            nonlocal prefix_pending
            fallbacks = renderer.add_text(
                document,
                value,
                question_id=question_id,
                expressions=expressions,
                inline_prefix=prefix_pending,
            )
            prefix_pending = ""
            if fallback_sink is not None:
                fallback_sink.extend(fallbacks)
            for fallback in fallbacks:
                LOGGER.warning(
                    "Word formula fallback for %s: %s",
                    fallback.expression_id,
                    fallback.reason,
                )

        def render_cell(paragraph, value: str, *, suffix: str) -> None:
            fallbacks = renderer.add_to_paragraph(
                paragraph,
                value,
                question_id=question_id,
                expressions=expressions,
            )
            if fallback_sink is not None:
                fallback_sink.extend(fallbacks)
            for fallback in fallbacks:
                LOGGER.warning(
                    "Word formula fallback for %s: %s",
                    fallback.expression_id,
                    fallback.reason,
                )

        # Check if we can extract options
        stem, options = extract_and_format_options(clean_text)
        if options:
            # Render stem
            for line_index, line in enumerate(stem.splitlines()):
                if line.strip():
                    render_line(line.strip(), suffix=f"stem-{line_index + 1}")
            # Render options in table/columns
            max_len = max(len(opt_text) for _, opt_text in options)
            if max_len <= 6:
                table = add_noborder_table(document, 1, 4)
                for i, (label, opt_text) in enumerate(options):
                    cell = table.cell(0, i)
                    p = cell.paragraphs[0]
                    render_cell(p, f"{label}. {opt_text}", suffix=f"option-{i + 1}")
            elif max_len <= 15:
                table = add_noborder_table(document, 2, 2)
                opt_coords = [(0, 0), (0, 1), (1, 0), (1, 1)]
                for idx, (label, opt_text) in enumerate(options):
                    r_idx, c_idx = opt_coords[idx]
                    cell = table.cell(r_idx, c_idx)
                    p = cell.paragraphs[0]
                    render_cell(p, f"{label}. {opt_text}", suffix=f"option-{idx + 1}")
            else:
                for option_index, (label, opt_text) in enumerate(options):
                    render_line(
                        f"{label}. {opt_text}",
                        suffix=f"option-{option_index + 1}",
                    )
        else:
            for line_index, line in enumerate(clean_text.splitlines()):
                if line.strip():
                    render_line(line.strip(), suffix=f"line-{line_index + 1}")

    elif inline_prefix:
        paragraph = document.add_paragraph(inline_prefix)
        paragraph.paragraph_format.line_spacing = active_config.line_spacing
        paragraph.paragraph_format.keep_with_next = True

    image_paths = _dedupe_paths([*(extra_image_paths or []), *inline_paths])
    if image_paths and document.paragraphs:
        document.paragraphs[-1].paragraph_format.keep_with_next = True
    _add_images(document, image_paths, data_root=data_root)


def _add_images(
    document: Document,
    image_paths: list[object],
    *,
    data_root: str | Path | None = None,
) -> None:
    for image_path in image_paths:
        path = _resolve_image_path(str(image_path), data_root=data_root)
        if path is None:
            continue
        try:
            document.add_picture(str(path), width=Inches(4.8))
            paragraph = document.paragraphs[-1]
            paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            paragraph.paragraph_format.keep_together = True
        except Exception:  # noqa: BLE001
            continue


def _dedupe_paths(paths: list[object]) -> list[str]:
    deduped: list[str] = []
    for path in paths:
        text = str(path or "").strip()
        if text and text not in deduped:
            deduped.append(text)
    return deduped


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


def _question_prefix(index: int) -> str:
    return f"{index}. "


__all__ = ["export_question_paper_docx"]
