from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from backend.document_parsing.question_blocks import (
    _extract_choice_answer_sequence,
    _question_type_hints_from_section_headings,
    flatten_answer_blocks,
    parse_plain_question_blocks,
    parse_rich_question_blocks,
    rich_blocks_plain_text,
    split_inline_main_question_paragraphs,
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
) -> list[dict[str, Any]]:
    try:
        rich_blocks = _extract_rich_question_blocks(
            file_bytes,
            temporary_root=temporary_root,
            asset_root=asset_root,
            register_created_file=register_created_file,
            write_created_file=write_created_file,
        )
    except ControlledDocxWriteError:
        raise
    except Exception:
        rich_blocks = None
    return rich_blocks or parse_plain_question_blocks(fallback_doc_text or "")


def _extract_rich_question_blocks(
    file_bytes: bytes,
    *,
    temporary_root: str | Path,
    asset_root: str | Path | None = None,
    register_created_file: Callable[[Path], None] | None = None,
    write_created_file: Callable[[Path, bytes], None] | None = None,
) -> list[dict[str, Any]] | None:
    from question_bank.importers.batch_importer import map_rich_content_by_number
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
    content = map_rich_content_by_number(
        split_inline_main_question_paragraphs(extracted.rich_paragraphs),
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
            blocks.append(block)
    return blocks or None
