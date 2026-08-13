from __future__ import annotations

from pathlib import Path

import fitz

from question_bank.importers.types import ExtractedDocument


def import_pdf(source_file: str | Path) -> ExtractedDocument:
    path = Path(source_file)
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
