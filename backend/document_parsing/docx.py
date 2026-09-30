from __future__ import annotations

import io
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.document_parsing.question_blocks import (
    _extract_choice_answer_sequence,
    _question_type_hints_from_section_headings,
    flatten_answer_blocks,
    image_paths_from_rich_text,
    parse_plain_question_blocks,
    parse_rich_question_blocks,
    rich_blocks_plain_text,
    split_inline_main_question_paragraphs,
)
from question_bank.parsers.type_detector import (
    RepeatedQuestionNumberError,
    subq_mark_labels,
    validate_section_numbering,
)


class ControlledDocxWriteError(RuntimeError):
    """A caller-owned asset write failed and must not be hidden by fallback."""


def parse_docx_question_blocks(
    file_bytes: bytes,
    *,
    fallback_doc_text: str = "",
    temporary_root: str | Path,
    asset_root: str | Path | None = None,
    register_created_file: Callable[[Path], None] | None = None,
    write_created_file: Callable[[Path, bytes], None] | None = None,
    document_text_out: list[str] | None = None,
    preserve_preview_xml: bool = False,
) -> list[dict[str, Any]]:
    rich_failed = False
    parsed_text: list[str] = []
    try:
        rich_blocks = _extract_rich_question_blocks(
            file_bytes,
            temporary_root=temporary_root,
            asset_root=asset_root,
            register_created_file=register_created_file,
            write_created_file=write_created_file,
            document_text_out=parsed_text,
            preserve_preview_xml=preserve_preview_xml,
        )
    except (ControlledDocxWriteError, RepeatedQuestionNumberError):
        raise
    except Exception:
        rich_blocks = None
        rich_failed = True
    if document_text_out is not None:
        document_text_out.extend(parsed_text)
    if rich_blocks:
        return rich_blocks
    text = fallback_doc_text or "\n".join(parsed_text)
    if not text:
        from backend.document_parsing.text import extract_docx_text
        text = extract_docx_text(file_bytes)
        if document_text_out is not None:
            document_text_out.append(text)
    blocks = parse_plain_question_blocks(text)
    if rich_failed:
        for block in blocks:
            block.setdefault("parse_warnings", []).append("Word 图文结构未能完整读取，当前为文字提取结果；请核对配图、公式与题目边界。")
            block["needs_review"] = True
            block["local_answer_trusted"] = False
    return blocks


def _extract_rich_question_blocks(
    file_bytes: bytes,
    *,
    temporary_root: str | Path,
    asset_root: str | Path | None = None,
    register_created_file: Callable[[Path], None] | None = None,
    write_created_file: Callable[[Path, bytes], None] | None = None,
    document_text_out: list[str] | None = None,
    preserve_preview_xml: bool = False,
) -> list[dict[str, Any]] | None:
    from question_bank.importers.batch_importer import (
        map_rich_content_by_number,
        partition_ambiguous_floating_images,
    )
    from question_bank.importers.docx_importer import import_docx

    temporary_path = Path(temporary_root)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    source_path = temporary_path / f"_rich_split_{timestamp}.docx"

    def controlled_writer(path: Path, content: bytes) -> None:
        assert write_created_file is not None
        try:
            write_created_file(path, content)
        except Exception as error:
            raise ControlledDocxWriteError() from error

    extracted = import_docx(
        io.BytesIO(file_bytes),
        source_name=source_path,
        asset_root=asset_root,
        asset_root_is_output_dir=write_created_file is not None,
        register_created_file=register_created_file,
        write_created_file=(
            controlled_writer if write_created_file is not None else None
        ),
    )
    if document_text_out is not None:
        document_text_out.append(rich_blocks_plain_text(extracted.rich_paragraphs))
    from question_bank.importers.batch_importer import _split_answer_text
    question_text, _ = _split_answer_text(extracted.text)
    validate_section_numbering(question_text)
    normalized_paragraphs, ambiguous_assets = partition_ambiguous_floating_images(
        split_inline_main_question_paragraphs(extracted.rich_paragraphs)
    )
    content = map_rich_content_by_number(
        normalized_paragraphs,
        source_file=str(source_path),
    )
    question_map = content.get("question") if isinstance(content, dict) else {}
    answer_map = content.get("answer") if isinstance(content, dict) else {}
    if not isinstance(question_map, dict) or not question_map:
        return None

    def numeric_key(value: str) -> int:
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return 999

    question_numbers = [
        str(numeric_key(number_key))
        for number_key in sorted(question_map, key=numeric_key)
        if 1 <= numeric_key(number_key) <= 99
    ]
    choice_answers = _extract_choice_answer_sequence(
        rich_blocks_plain_text(flatten_answer_blocks(answer_map)),
        question_numbers=question_numbers,
    )
    section_hints = _question_type_hints_from_section_headings(
        rich_blocks_plain_text(extracted.rich_paragraphs)
    )
    blocks: list[dict[str, Any]] = []
    for number_key in sorted(question_map, key=numeric_key):
        number = numeric_key(number_key)
        if not (1 <= number <= 99):
            continue
        block = parse_rich_question_blocks(
            number,
            question_map.get(number_key) or [],
            answer_map.get(number_key) if isinstance(answer_map, dict) else None,
            choice_answers,
            section_type=section_hints.get(str(number), ""),
        )
        if block:
            source_answers = answer_map.get(number_key, []) if isinstance(answer_map, dict) else []
            warnings: list[str] = [str(warning) for p in [*(question_map.get(number_key) or []), *source_answers] for warning in p.get("parse_warnings", [])]
            question_parts = subq_mark_labels(block.get("question_text", ""))
            answer_parts = set(re.findall(r"【小题\s*(\d+)】", rich_blocks_plain_text(source_answers)))
            if question_parts and answer_parts and set(question_parts) != answer_parts:
                warnings.append(f"题面有 {len(question_parts)} 个小问，同号答案有 {len(answer_parts)} 个小问；请核对原卷合并或删题后的答案对应关系。")
            if warnings:
                block["parse_warnings"] = warnings
                block["needs_review"] = True
                block["local_answer_trusted"] = False
            blocks.append(block)
    by_number = {int(b["question_id"][1:]): b for b in blocks}
    for answer_number in answer_map or {}:
        number = numeric_key(answer_number)
        if number not in by_number and by_number:
            previous = [n for n in by_number if n < number]
            owner = by_number[max(previous) if previous else min(by_number)]
            owner.setdefault("parse_warnings", []).append(f"答案区保留了第 {number} 题，但题面没有同号大题；请核对是否需要并入本题。")
            owner["needs_review"] = True
            owner["local_answer_trusted"] = False

    # Preserve every body image, including VML pictures, images on headings and
    # pictures belonging to an answer whose original question was removed.
    # Unassigned pictures use the existing manual image review lane.
    assigned = {path for b in blocks for key in ("question_html", "answer_html", "analysis_html") for path in image_paths_from_rich_text(b.get(key, ""))}
    assigned.update(str(a["path"]) for a in ambiguous_assets)
    missing = [p for p in extracted.image_paths if p not in assigned]
    if blocks and missing:
        for path in missing:
            if len(blocks) == 1:
                blocks[0]["question_html"] += f"\n[[IMAGE:{path}]]"
                blocks[0].setdefault("image_paths", []).append(path)
            else:
                ambiguous_assets.append({"path": path, "previous_question_id": blocks[0]["question_id"], "next_question_id": blocks[1]["question_id"], "source_section": "question"})
        blocks[0].setdefault("parse_warnings", []).append(f"有 {len(missing)} 张图片没有明确的题号位置，已保留供核对归属。")
        blocks[0]["needs_review"] = True
    if blocks and ambiguous_assets:
        blocks[0]["_ambiguous_assets"] = ambiguous_assets
    if blocks and preserve_preview_xml:
        # Keep the already extracted XML for preview projection. Images use the
        # owned asset lane; the preview renderer does not resolve Word drawings.
        blocks[0]["_word_paragraphs"] = [
            {"text": re.sub(r"\[\[IMAGE:[^\]\r\n]+\]\]", "", str(p.get("text") or "")),
             "xml": str(p["xml"])}
            for p in extracted.rich_paragraphs
            if p.get("xml")
        ]
    return blocks or None
