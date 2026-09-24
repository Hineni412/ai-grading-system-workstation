from __future__ import annotations

import io
from pathlib import Path


def extract_docx_text(file_bytes: bytes) -> str:
    """Read body text through the same ordered Word reader used for splitting.

    Repeated answers, formulas and table cells retain their document positions.
    Headers/footers are page furniture, not extra questions appended to the paper.
    The image writer is intentionally a no-op: this function owns no asset files.
    """
    from question_bank.importers.docx_importer import import_docx
    from backend.document_parsing.question_blocks import rich_blocks_plain_text

    extracted = import_docx(
        io.BytesIO(file_bytes),
        source_name="text-only.docx",
        asset_root=Path("text-only-assets"),
        asset_root_is_output_dir=True,
        write_created_file=lambda _path, _content: None,
    )
    text = rich_blocks_plain_text(extracted.rich_paragraphs).strip()
    if not text and not extracted.has_images:
        raise ValueError("Word 文档未解析到有效文本")
    return text


def extract_pdf_text(pdf_bytes: bytes) -> str:
    """Extract page text in PDF page order without rendering images."""
    import fitz

    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    text_blocks: list[str] = []
    try:
        for page_index in range(len(document)):
            text_blocks.append(document[page_index].get_text())
    finally:
        document.close()
    return "\n".join(text_blocks)
