from __future__ import annotations

import io
import sqlite3
import zipfile
from dataclasses import dataclass
from pathlib import Path

import fitz
import pytest
from PIL import Image

from question_bank.database.schema import initialize_database
from question_bank.document_pipeline import (
    ApplyReviewCommand,
    DocumentPipelineConflict,
    DocumentSource,
    DocumentState,
    ExportState,
    ExportWordCommand,
    ManualQuestionRegion,
    OcrLine,
    PrepareSourceCommand,
    PublishCommand,
    QuestionBankPublicationAdapter,
    QuestionDocumentPipeline,
    QuestionReviewDecision,
    RecognitionSource,
    ReviewState,
    SourceRegion,
    TextLayerState,
    validate_docx,
)


def _region(
    page_number: int,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    *,
    source: RecognitionSource = RecognitionSource.MANUAL,
    confidence: float = 1.0,
) -> SourceRegion:
    return SourceRegion(
        page_number=page_number,
        polygon=((x0, y0), (x1, y0), (x1, y1), (x0, y1)),
        source=source,
        confidence=confidence,
    )


def _text_pdf(*page_lines: tuple[float, float, str]) -> bytes:
    document = fitz.open()
    page = document.new_page(width=600, height=800)
    for x, y, text in page_lines:
        page.insert_text((x, y), text, fontsize=12)
    content = document.tobytes()
    document.close()
    return content


def _scanned_pdf(page_count: int = 1) -> bytes:
    document = fitz.open()
    for _ in range(page_count):
        page = document.new_page(width=600, height=800)
        image = Image.new("RGB", (600, 800), "white")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        page.insert_image(page.rect, stream=buffer.getvalue())
    content = document.tobytes()
    document.close()
    return content


@dataclass
class FakeOcr:
    lines_by_page: dict[int, tuple[OcrLine, ...]]
    calls: list[int]

    def recognize(self, png_bytes: bytes, *, page_number: int) -> tuple[OcrLine, ...]:
        assert png_bytes.startswith(b"\x89PNG")
        self.calls.append(page_number)
        return self.lines_by_page.get(page_number, ())


class ExplodingOcr:
    def recognize(self, png_bytes: bytes, *, page_number: int) -> tuple[OcrLine, ...]:
        raise AssertionError(f"OCR must not run for text PDF page {page_number}")


def _line(
    text: str,
    box: tuple[float, float, float, float],
    *,
    confidence: float = 0.45,
) -> OcrLine:
    x0, y0, x1, y1 = box
    return OcrLine(
        text=text,
        polygon=((x0, y0), (x1, y0), (x1, y1), (x0, y1)),
        confidence=confidence,
        engine_version="fake-ocr/1",
    )


def test_scan_double_column_cross_page_and_low_confidence_require_review(
    tmp_path: Path,
) -> None:
    ocr = FakeOcr(
        {
            1: (
                _line("2. right column", (0.60, 0.10, 0.94, 0.18)),
                _line("1. left start $x^2$", (0.05, 0.10, 0.42, 0.18)),
                _line("left detail", (0.05, 0.20, 0.42, 0.28)),
            ),
            2: (
                _line("unrelated left", (0.05, 0.10, 0.42, 0.18)),
                _line("continued on page two", (0.60, 0.10, 0.94, 0.18)),
            ),
        },
        [],
    )
    pipeline = QuestionDocumentPipeline(tmp_path / "workspace", ocr_adapter=ocr)
    snapshot = pipeline.prepare_source(
        PrepareSourceCommand(
            operation_id="scan-cross-page",
            source=DocumentSource(
                source_id="source-scan-1",
                filename="scan.pdf",
                media_type="application/pdf",
                content=_scanned_pdf(2),
            ),
            manual_questions=(
                ManualQuestionRegion(
                    question_id="manual-q1",
                    question_number="1",
                    question_regions=(
                        _region(1, 0.0, 0.0, 0.50, 0.40),
                        _region(2, 0.50, 0.0, 1.0, 0.40),
                    ),
                ),
            ),
        )
    )

    assert ocr.calls == [1, 2]
    assert [item.text for item in snapshot.pages[0].blocks] == [
        "1. left start $x^2$",
        "left detail",
        "2. right column",
    ]
    assert snapshot.questions[0].question_text == (
        "1. left start $x^2$\nleft detail\ncontinued on page two"
    )
    assert snapshot.state == DocumentState.REVIEW_REQUIRED
    assert "ocr_review_required" in snapshot.questions[0].blocking_issues
    assert (
        snapshot.questions[0].math_expressions[0].review_state == ReviewState.CANDIDATE
    )
    assert all(
        page.text_layer_state == TextLayerState.LOCAL_OCR for page in snapshot.pages
    )


def test_reviewed_math_exports_editable_omml_and_reports_original_image_fallback(
    tmp_path: Path,
) -> None:
    ocr = FakeOcr({1: (_line("1. placeholder", (0.05, 0.05, 0.95, 0.25)),)}, [])
    workspace = tmp_path / "workspace"
    pipeline = QuestionDocumentPipeline(workspace, ocr_adapter=ocr)
    prepared = pipeline.prepare_source(
        PrepareSourceCommand(
            operation_id="word-math",
            source=DocumentSource(
                source_id="source-math-1",
                filename="scan.pdf",
                media_type="application/pdf",
                content=_scanned_pdf(),
            ),
        )
    )
    reviewed = pipeline.apply_review(
        ApplyReviewCommand(
            operation_id=prepared.operation_id,
            expected_snapshot_revision=prepared.snapshot_revision,
            decisions=(
                QuestionReviewDecision(
                    question_id=prepared.questions[0].question_id,
                    question_text=r"Solve $x^2+1$ and preserve $\unknown{x}$.",
                    answer_status=ReviewState.MISSING,
                    confirm_content=True,
                ),
            ),
        )
    )
    assert reviewed.state == DocumentState.READY
    assert (
        reviewed.questions[0].math_expressions[0].review_state
        == ReviewState.TEACHER_VERIFIED
    )
    assert reviewed.questions[0].math_expressions[1].fallback_asset

    output = tmp_path / "math-export.docx"
    receipt = pipeline.export_word(
        ExportWordCommand(
            operation_id=reviewed.operation_id,
            expected_snapshot_revision=reviewed.snapshot_revision,
            output_path=str(output),
            question_ids=(reviewed.questions[0].question_id,),
        )
    )
    assert receipt.state == ExportState.COMPLETE_WITH_FALLBACKS
    assert len(receipt.fallbacks) == 1
    assert receipt.fallbacks[0].asset_path == reviewed.pages[0].rendered_asset
    assert validate_docx(output) == ()
    with zipfile.ZipFile(output) as archive:
        document_xml = archive.read("word/document.xml")
        assert b"<m:oMath" in document_xml
        assert any(name.startswith("word/media/") for name in archive.namelist())


def _review_single_question(
    pipeline: QuestionDocumentPipeline,
    *,
    operation_id: str,
) -> tuple:
    prepared = pipeline.prepare_source(
        PrepareSourceCommand(
            operation_id=operation_id,
            source=DocumentSource(
                source_id=f"source-{operation_id}",
                filename="paper.pdf",
                media_type="application/pdf",
                content=_text_pdf((40, 80, "1. Solve $x^2$")),
            ),
        )
    )
    reviewed = pipeline.apply_review(
        ApplyReviewCommand(
            operation_id=operation_id,
            expected_snapshot_revision=prepared.snapshot_revision,
            decisions=(
                QuestionReviewDecision(
                    question_id=prepared.questions[0].question_id,
                    confirm_content=True,
                    answer_status=ReviewState.MISSING,
                ),
            ),
        )
    )
    return prepared, reviewed


def test_publication_reconcile_finishes_an_interrupted_staged_file_move(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "databases" / "question_bank.db"
    initialize_database(db_path)
    workspace = tmp_path / "pipeline"
    pipeline = QuestionDocumentPipeline(workspace, ocr_adapter=ExplodingOcr())
    _, reviewed = _review_single_question(
        pipeline,
        operation_id="publication-recover-staged",
    )
    adapter = QuestionBankPublicationAdapter(
        db_path=db_path,
        data_root=tmp_path,
        pipeline_workspace=workspace,
    )
    pipeline.publisher = adapter
    receipt = pipeline.publish_questions(
        PublishCommand(
            operation_id=reviewed.operation_id,
            expected_snapshot_revision=reviewed.snapshot_revision,
            question_ids=(reviewed.questions[0].question_id,),
        )
    )
    manifest_path = adapter._manifest_path(reviewed.operation_id)
    manifest = adapter._read_json(manifest_path)
    sidecar_record = next(
        item for item in manifest["staged_files"] if item["kind"] == "sidecar"
    )
    final_sidecar = adapter._resolve_data_path(sidecar_record["final_path"])
    staged_sidecar = adapter._resolve_data_path(sidecar_record["staged_path"])
    staged_sidecar.parent.mkdir(parents=True, exist_ok=True)
    final_sidecar.replace(staged_sidecar)
    manifest["state"] = "publishing"
    adapter._write_validated_manifest(manifest_path, manifest)

    assert adapter.reconcile(reviewed.operation_id) == "published"
    assert final_sidecar.is_file()
    assert not adapter._staging_root(reviewed.operation_id).exists()
    assert (
        pipeline.publish_questions(
            PublishCommand(
                operation_id=reviewed.operation_id,
                expected_snapshot_revision=reviewed.snapshot_revision,
                question_ids=(reviewed.questions[0].question_id,),
            )
        )
        == receipt
    )


def test_publication_rolls_back_database_when_sidecar_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "databases" / "question_bank.db"
    initialize_database(db_path)
    workspace = tmp_path / "pipeline"
    pipeline = QuestionDocumentPipeline(workspace, ocr_adapter=ExplodingOcr())
    _, reviewed = _review_single_question(pipeline, operation_id="publication-fail")
    adapter = QuestionBankPublicationAdapter(
        db_path=db_path,
        data_root=tmp_path,
        pipeline_workspace=workspace,
    )
    pipeline.publisher = adapter

    def fail_sidecar(*args, **kwargs):
        raise OSError("synthetic sidecar failure")

    monkeypatch.setattr(adapter, "_write_owned_sidecar", fail_sidecar)
    with pytest.raises(OSError, match="synthetic sidecar failure"):
        pipeline.publish_questions(
            PublishCommand(
                operation_id=reviewed.operation_id,
                expected_snapshot_revision=reviewed.snapshot_revision,
                question_ids=(reviewed.questions[0].question_id,),
            )
        )
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0] == 0
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM question_document_publications"
            ).fetchone()[0]
            == 0
        )


def test_edit_after_review_invalidates_confirmation_and_old_revision(
    tmp_path: Path,
) -> None:
    pipeline = QuestionDocumentPipeline(
        tmp_path / "workspace", ocr_adapter=ExplodingOcr()
    )
    _, reviewed = _review_single_question(pipeline, operation_id="review-invalidation")
    changed = pipeline.apply_review(
        ApplyReviewCommand(
            operation_id=reviewed.operation_id,
            expected_snapshot_revision=reviewed.snapshot_revision,
            decisions=(
                QuestionReviewDecision(
                    question_id=reviewed.questions[0].question_id,
                    question_text="1. changed but not confirmed",
                    confirm_content=False,
                ),
            ),
        )
    )
    assert changed.questions[0].review_state == ReviewState.CANDIDATE
    assert (
        "teacher_content_confirmation_required" in changed.questions[0].blocking_issues
    )
    with pytest.raises(DocumentPipelineConflict):
        pipeline.apply_review(
            ApplyReviewCommand(
                operation_id=reviewed.operation_id,
                expected_snapshot_revision=reviewed.snapshot_revision,
                decisions=(
                    QuestionReviewDecision(
                        question_id=reviewed.questions[0].question_id,
                        confirm_content=True,
                    ),
                ),
            )
        )
