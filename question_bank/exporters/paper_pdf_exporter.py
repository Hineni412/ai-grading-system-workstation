"""Ordinary assembly PDF export using the local LaTeX engine.

Reuses published question content and the existing Word text/OMML parser.
PDF is explicit: unsupported content fails without publishing a partial file.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from docx import Document

from question_bank.document_pipeline.legacy_exports import (
    data_root_for_database,
    published_math_metadata,
)
from question_bank.document_pipeline.word_renderer import answer_space_lines
from question_bank.personalized_papers import latex_render as lr
from question_bank.personalized_papers.latex_render import (
    LatexRenderError,
    TectonicCompiler,
)
from question_bank.services.assembly_basket_state import SectionSpec
from question_bank.services.question_read_service import QuestionBankReadService
from question_bank.services.rich_content_service import (
    strip_question_source_score,
    strip_question_source_score_blocks,
)

from .latex_layout import (
    MACROS,
    TABLE_PROBE_MACROS,
    PaperLatexLayout,
    continuation_footer,
    escape_text,
    table_choices,
)
from .paper_docx_exporter import (
    _add_text_and_images,
    _canonical_type_group,
    _dedupe_paths,
    _default_title,
    _group_indexed_questions,
    _image_paths_from_text,
    _resolve_image_path,
    _rich_blocks,
    _rich_image_paths,
    _safe_filename,
    _section_index_label,
    _unembedded_image_paths,
)


def _checked_image(value: str, data_root: Path) -> Path:
    path = _resolve_image_path(value, data_root=data_root)
    if path is None:
        raise LatexRenderError("题目配图缺失，请检查题目或选择 Word 导出")
    resolved = path.resolve()
    if data_root.resolve() not in resolved.parents:
        raise LatexRenderError("题目配图超出题库文件范围，请检查题目")
    return resolved


def _blocks(question: dict, metadata, key: str, data_root: Path) -> list[dict]:
    """Use original rich XML, or parse text through the existing Word parser.

    The temporary Word document lives only in memory; image relationships point
    at original assets. No preview preparation or real data writes occur.
    """
    blocks = list(_rich_blocks(metadata.rich_content, key))
    answer = key == "answer_blocks"
    text = str(question.get("answer_text" if answer else "question_text") or "")
    if not answer:
        blocks = strip_question_source_score_blocks(
            blocks, question_number=str(question.get("question_number") or ""),
        )
        text = strip_question_source_score(
            text, question_number=str(question.get("question_number") or ""),
        )
    image_values = _dedupe_paths(
        [
            *(question.get("answer_image_paths" if answer else "image_paths") or []),
            *_image_paths_from_text(text),
        ]
    )
    paths = [_checked_image(value, data_root) for value in image_values]
    if not answer:
        paths = [
            Path(path)
            for path in _unembedded_image_paths(
                paths,
                _rich_image_paths(metadata.rich_content, "answer_blocks", data_root),
            )
        ]
    if blocks:
        embedded = {
            _checked_image(value, data_root)
            for block in blocks
            for value in (block.get("image_relationships") or {}).values()
        }
        paths = [Path(path) for path in _unembedded_image_paths(paths, embedded)]
        if not paths:
            return blocks
        text = ""
    document = Document()
    fallbacks = []
    _add_text_and_images(
        document,
        text or ("暂无答案" if answer and not blocks else ""),
        extra_image_paths=[str(path) for path in paths],
        expressions=metadata.expressions,
        fallback_sink=fallbacks,
        data_root=data_root,
    )
    if fallbacks:
        raise LatexRenderError("公式暂不支持 PDF，请检查题目或选择 Word 导出")
    by_hash = {hashlib.sha256(path.read_bytes()).digest(): path for path in paths}
    relationships = {}
    for relationship_id, relationship in document.part.rels.items():
        if relationship.reltype.endswith("/image"):
            asset = by_hash.get(hashlib.sha256(relationship.target_part.blob).digest())
            if asset is None:
                raise LatexRenderError("无法核对题目配图，请选择 Word 导出")
            relationships[relationship_id] = asset.relative_to(
                data_root.resolve()
            ).as_posix()
    for node in document.element.body:
        if lr._local_name(node.tag) in {"p", "tbl"}:
            blocks.append({"xml": node.xml, "image_relationships": relationships})
    return blocks


def _groups(questions: list[dict], grouped_by_type: bool, sections):
    if sections:
        by_id = {int(question["id"]): question for question in questions}
        seen = set()
        groups = []
        index = 1
        for section in sections:
            group = []
            for question_id in section.question_ids:
                if question_id in by_id and question_id not in seen:
                    group.append((index, by_id[question_id]))
                    seen.add(question_id)
                    index += 1
            if group:
                groups.append((section.title.strip(), group))
        if seen != set(by_id):
            raise ValueError("分节未包含全部题目，请重新核对草稿")
        return groups
    if grouped_by_type:
        return [
            (title, group)
            for title, group in zip(
                ("选择题", "填空题", "解答题"), _group_indexed_questions(questions)
            )
            if group
        ]
    return [("", list(enumerate(questions, start=1)))]


def _choice_answer_table(group) -> str:
    numbers = [
        index
        for index, question in group
        if _canonical_type_group(question.get("question_type")) == "选择题"
    ]
    chunks = []
    for start in range(0, len(numbers), 10):
        row = numbers[start : start + 10]
        width = (174 - 3 * len(row) - 0.141 * (len(row) + 1)) / len(row)
        spec = "|" + "|".join(f"p{{{width:.3f}mm}}" for _ in row) + "|"
        chunks.append(
            r"\LayoutKeep{\setlength{\tabcolsep}{1.5mm}\begin{tabular}{"
            + spec
            + r"}\hline "
            + " & ".join(map(str, row))
            + r"\\\hline "
            + " & ".join(r"\rule{0pt}{6mm}" for _ in row)
            + r"\\\hline\end{tabular}}"
        )
    return "\n".join(chunks)


def _render_source(
    groups,
    prepared,
    *,
    data_root,
    title,
    header_text,
    include_answer,
    include_answer_space,
    include_student_fields,
    choices,
    footer,
    probes,
):
    layout = PaperLatexLayout(choices)
    body = [r"\begin{center}{\Large\bfseries " + escape_text(title) + r"}\end{center}"]
    if header_text:
        body.append(r"\begin{center}" + escape_text(header_text) + r"\end{center}")
    if include_student_fields:
        body.append(
            r"姓名：\underline{\hspace{30mm}}\hspace{8mm}班级：\underline{\hspace{30mm}}"
            r"\hspace{8mm}日期：\underline{\hspace{30mm}}\par"
        )
    for section_index, (heading, group) in enumerate(groups):
        if heading:
            body.append(
                r"\section*{"
                + escape_text(f"{_section_index_label(section_index)}、{heading}")
                + "}"
            )
            if include_answer_space:
                body.append(_choice_answer_table(group))
        for index, question in group:
            lines = answer_space_lines(
                question.get("question_type"), question.get("question_text")
            )
            body.append(
                layout.flow(
                    prepared[int(question["id"])][0],
                    data_root=data_root,
                    number=index,
                    answer_lines=lines if include_answer_space else 0,
                )
            )
    if include_answer:
        body.append(r"\newpage\section*{答案解析}")
        layout.mode = "answer"
        for section_index, (heading, group) in enumerate(groups):
            if heading:
                body.append(
                    r"\section*{"
                    + escape_text(
                        f"{_section_index_label(section_index)}、{heading}答案"
                    )
                    + "}"
                )
            for index, question in group:
                body.append(
                    layout.flow(
                        prepared[int(question["id"])][1],
                        data_root=data_root,
                        number=index,
                    )
                )
    header = lr._HEADER.replace(
        r"\setmainfont{SimSun}", r"\setmainfont{Times New Roman}"
    )
    header = header.replace("__HEADER_LINE__", "")
    header = header.replace(
        r"\begin{document}",
        MACROS + TABLE_PROBE_MACROS + "\n" + footer + "\n" + r"\begin{document}",
    )
    return (
        header
        + "\n"
        + ("\n".join(layout.probes) if probes else "")
        + "\n"
        + "\n".join(body)
        + lr._FOOTER
    )


def export_question_paper_pdf(
    db_path: str | Path,
    question_ids: list[int],
    output_dir: str | Path,
    *,
    title: str,
    include_answer: bool = False,
    grouped_by_type: bool = False,
    header_text: str | None = None,
    sections: list[SectionSpec] | None = None,
    include_answer_space: bool = True,
    include_student_fields: bool = True,
    compiler: TectonicCompiler | None = None,
    check_cancelled=None,
) -> Path:
    engine = compiler or TectonicCompiler()
    if not engine.available:
        raise LatexRenderError("本机缺少 PDF 排版引擎，请选择 Word 导出")
    questions = QuestionBankReadService(Path(db_path)).get_questions_for_export(
        question_ids
    )
    if not questions or len(questions) != len(question_ids):
        raise ValueError("题目为空或已不可用，请重新核对草稿")
    data_root = data_root_for_database(db_path)
    prepared = {}
    for question in questions:
        metadata = published_math_metadata(
            int(question["id"]),
            data_root=data_root,
            question_text=str(question.get("question_text") or ""),
            answer_text=str(question.get("answer_text") or ""),
        )
        prepared[int(question["id"])] = (
            _blocks(question, metadata, "question_blocks", data_root),
            _blocks(question, metadata, "answer_blocks", data_root)
            if include_answer
            else [],
        )
    groups = _groups(questions, grouped_by_type, sections)
    output_root = Path(output_dir).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    destination = output_root / ((_safe_filename(title) or "question_paper") + ".pdf")
    choices = {}
    footer = ""
    with tempfile.TemporaryDirectory(dir=output_root, prefix=".latex-") as temporary:
        work = Path(temporary)
        for attempt in range(4):
            if check_cancelled:
                check_cancelled()
            try:
                source = _render_source(
                    groups,
                    prepared,
                    data_root=data_root,
                    title=title.strip() or _default_title(),
                    header_text=header_text,
                    include_answer=include_answer,
                    include_answer_space=include_answer_space,
                    include_student_fields=include_student_fields,
                    choices=choices,
                    footer=footer,
                    probes=attempt == 0,
                )
            except (
                LatexRenderError,
                ET.ParseError,
                ValueError,
                TypeError,
                KeyError,
                OSError,
            ):
                raise LatexRenderError(
                    "题目内容暂不支持 PDF，请检查题目或选择 Word 导出"
                ) from None
            try:
                engine.compile(
                    source,
                    work / "paper.pdf",
                    auxiliary_directory=work,
                    offline=True,
                    validate_layout=attempt > 0,
                )
            except (RuntimeError, OSError, subprocess.SubprocessError, UnicodeError):
                # Compiler logs may contain question text and real asset paths.
                raise LatexRenderError(
                    "PDF 编译失败，请检查本机排版环境或选择 Word 导出"
                ) from None
            if check_cancelled:
                check_cancelled()
            try:
                next_choices = (
                    table_choices((work / "paper.measures").read_text(encoding="utf-8"))
                    if attempt == 0 and r"\LayoutTableProbe{" in source
                    else choices
                )
                next_footer = continuation_footer(
                    (work / "paper.positions").read_text(encoding="utf-8")
                )
            except (OSError, ValueError, KeyError):
                raise LatexRenderError("PDF 分页测量失败，请选择 Word 导出") from None
            if attempt > 0 and next_choices == choices and next_footer == footer:
                shutil.copyfile(work / "paper.pdf", destination)
                return destination
            choices, footer = next_choices, next_footer
    raise LatexRenderError("PDF 分页未稳定，请选择 Word 导出")


__all__ = ["export_question_paper_pdf"]
