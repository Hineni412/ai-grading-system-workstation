"""Regression scenarios from the intake/dedup audit, using synthetic sources."""

from __future__ import annotations

import io
import sqlite3
from dataclasses import replace
from pathlib import Path

import fitz
from PIL import Image

from backend.document_parsing.question_blocks import parse_rich_question_blocks
from question_bank.services.duplicate_analysis_copy_service import exact_question_key


def _pdf(pages):
    with fitz.open() as doc:
        for lines in pages:
            page = doc.new_page(width=600, height=800)
            for x, y, text in lines:
                page.insert_text((x, y), text, fontsize=12)
        return doc.tobytes()


def test_two_column_crops_do_not_capture_neighbor_and_last_question_crosses_page():
    from rubric_auto_cropper import extract_pdf_question_images

    payload = _pdf(
        [
            [
                (40, 80, "1. LEFT"),
                (40, 150, "left continuation"),
                (340, 80, "2. RIGHT"),
            ],
            [(40, 80, "3. LAST"), (40, 720, "last page one")],
            [(40, 80, "last continuation"), (40, 500, "Answer"), (40, 550, "1. A")],
        ]
    )
    crops = extract_pdf_question_images(
        payload, [{"question_id": f"Q{i}"} for i in (1, 2, 3)], scale=1
    )
    left = Image.open(io.BytesIO(crops["Q1"]["question"]))
    last = Image.open(io.BytesIO(crops["Q3"]["question"]))
    assert left.width < 340
    assert last.height > 1000
    assert last.height < 1300  # stops before answers, including page three
    assert crops["Q1"]["answer"]


def test_numbered_steps_inside_later_question_are_not_repeated_main_numbers():
    from question_bank.parsers.type_detector import validate_section_numbering

    validate_section_numbering(
        "一、选择题\n1. 选择题\n二、解答题\n2. 根据下列步骤进行操作\n1. 第一步\n2. 第二步"
    )


def test_garbled_pdf_text_uses_existing_local_ocr_adapter(tmp_path, monkeypatch):
    from question_bank.document_pipeline.pipeline import QuestionDocumentPipeline
    from question_bank.document_pipeline.adapters import OcrLine
    from question_bank.importers import mineru_parse
    from question_bank.importers.batch_importer import _extract_paper

    class FakeOCR:
        def recognize(self, content, *, page_number):
            return [
                OcrLine(
                    "1. Compute 2+3 and write the result.",
                    ((0.05, 0.1), (0.8, 0.1), (0.8, 0.2), (0.05, 0.2)),
                    1,
                    "synthetic-local-ocr",
                )
            ]

    monkeypatch.setattr(mineru_parse, "_run_mineru", lambda path: None)
    source = tmp_path / "synthetic-garbled.pdf"
    source.write_bytes(_pdf([[(40, 100, "!@#$%^&*()!@#$%^&*()")]]))
    result = _extract_paper(
        source,
        document_pipeline=QuestionDocumentPipeline(
            tmp_path / "ocr", ocr_adapter=FakeOCR()
        ),
        operation_id="garbled",
    )
    assert result.ocr_applied and not result.needs_ocr
    assert "Compute 2+3" in result.text
    assert result.pdf_layout["pages"][0][0]["bbox"] == (0.05, 0.1, 0.8, 0.2)
