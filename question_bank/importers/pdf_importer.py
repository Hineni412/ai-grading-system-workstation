from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import fitz

from question_bank.document_pipeline.contracts import (
    DocumentSource,
    ManualQuestionRegion,
    PrepareSourceCommand,
    TextLayerState,
)
from question_bank.importers.types import ExtractedDocument

if TYPE_CHECKING:
    from question_bank.document_pipeline.pipeline import QuestionDocumentPipeline


def import_pdf(
    source_file: str | Path,
    *,
    document_pipeline: "QuestionDocumentPipeline | None" = None,
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
