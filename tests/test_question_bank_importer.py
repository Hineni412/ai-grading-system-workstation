from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls

from question_bank.importers import batch_importer
from question_bank.importers.batch_importer import (
    PaperMetadata,
    ParsedPaperText,
    ParsedQuestion,
    ScannedPaper,
    _without_existing_duplicate_questions,
    infer_metadata_from_filename,
    parse_paper_text,
)
from question_bank.database.schema import connect
from question_bank.database.schema import initialize_database
from question_bank.importers.types import ExtractedDocument
from question_bank.importers.docx_importer import _get_paragraph_rich_text
from question_bank.models.question import QuestionCreate
from question_bank.services.question_service import QuestionService


def test_scanned_pdf_without_text_is_reported_as_needing_ocr(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"%PDF-1.4\n")
    db_path = tmp_path / "question_bank.db"

    monkeypatch.setattr(
        batch_importer,
        "_extract_paper",
        lambda path: ExtractedDocument(
            source_file=str(path),
            page_range="",
            text="",
            needs_ocr=True,
        ),
    )

    result = batch_importer._import_scanned_paper(
        source,
        db_path,
        metadata=PaperMetadata(),
        question_range=None,
    )

    assert result.status == "needs_ocr"
    assert result.question_count == 0


def test_docx_importer_reads_omath_inside_word_run() -> None:
    document = Document()
    paragraph = document.add_paragraph()
    paragraph.add_run("14．（5分）计算：")
    formula_run = paragraph.add_run()
    formula_run._r.append(  # noqa: SLF001 - build the exact Word structure that triggered the import bug.
        parse_xml(
            f"""
            <m:oMath {nsdecls("m")}>
              <m:r><m:t>|-1|+</m:t></m:r>
              <m:sSup>
                <m:e><m:r><m:t>(-2)</m:t></m:r></m:e>
                <m:sup><m:r><m:t>3</m:t></m:r></m:sup>
              </m:sSup>
            </m:oMath>
            """
        )
    )
    paragraph.add_run("．")

    text = _get_paragraph_rich_text(paragraph)

    assert "|-1|+" in text
    assert "(-2)<sup>3</sup>" in text


def test_parse_paper_text_only_skips_blocks_shorter_than_six_visible_chars() -> None:
    parsed = parse_paper_text(
        "1．ABCDE\n2．ABCDEF\n3．七年级计算题",
        source_file="regular_paper.docx",
        page_range="document",
    )

    assert [question.question_number for question in parsed.questions] == ["2", "3"]


def test_cross_paper_duplicate_question_is_kept_for_complete_paper_import(tmp_path: Path) -> None:
    db_path = tmp_path / "question_bank.db"
    service = QuestionService(db_path)
    service.add_question(
        QuestionCreate(
            question_number="2",
            question_text="（3分）习近平总书记在一次中国品牌论坛开幕式中为品牌强国建设指明了前进方向。",
        )
    )
    parsed = ParsedPaperText(
        questions=[
            ParsedQuestion(
                question_number="1",
                question_text="（3分）习近平总书记在一次中国品牌论坛开幕式中为品牌强国建设指明了前进方向。",
                source_file="new_paper.docx",
                page_range="document",
            ),
            ParsedQuestion(
                question_number="2",
                question_text="（3分）习近平总书记在一次中国品牌论坛开幕式中为品牌强国建设指明了前进方向。",
                source_file="new_paper.docx",
                page_range="document",
            ),
        ],
        answer_match_count=0,
        review_count=0,
    )

    result = _without_existing_duplicate_questions(db_path, parsed)

    assert [question.question_number for question in result.questions] == ["1"]


def test_infer_national_zhongkao_region_metadata_from_filename() -> None:
    metadata = infer_metadata_from_filename("2025年浙江省杭州市中考数学试卷.docx")

    assert metadata.province == "浙江省"
    assert metadata.city == "杭州市"
    assert metadata.exam_type == "中考"
    assert metadata.grade == "九年级"


def test_infer_shenzhen_region_metadata_from_filename() -> None:
    metadata = infer_metadata_from_filename("2025年广东省深圳市中考数学试卷.docx")

    assert metadata.province == "广东省"
    assert metadata.city == "深圳市"


def test_import_archives_local_source_and_persists_data_relative_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    data_root = tmp_path / "user_data"
    db_path = data_root / "databases" / "question_bank.db"
    source = tmp_path / "outside" / "paper.docx"
    source.parent.mkdir()
    source.write_bytes(b"paper-content")

    monkeypatch.setattr(
        batch_importer,
        "_extract_paper",
        lambda path: ExtractedDocument(
            source_file=str(path),
            page_range="document",
            text="1. 这是长度足够的测试题目\n答案：\n1. 42",
        ),
    )

    result = batch_importer.import_scanned_papers(
        [ScannedPaper(source_file=str(source), file_type="docx")],
        db_path,
        data_root=data_root,
    )

    with connect(db_path) as conn:
        paper_title, paper_source = conn.execute(
            "SELECT title, source_file FROM papers"
        ).fetchone()
        question_source = conn.execute("SELECT source_file FROM questions").fetchone()[0]
    assert paper_title == "paper"
    assert paper_source.startswith("question_bank/raw_papers/")
    assert question_source == paper_source
    assert (data_root / paper_source).exists()
    assert source.exists()
    assert result.files[0].source_file == paper_source


def test_archived_import_uses_original_title_for_legacy_duplicate_detection(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO papers (title, source_file, content_fingerprint) VALUES ('paper', 'old.docx', NULL)"
        )
    archived = tmp_path / "paper_0123456789ab.docx"
    archived.write_bytes(b"paper-content")
    monkeypatch.setattr(
        batch_importer,
        "_extract_paper",
        lambda _path: (_ for _ in ()).throw(AssertionError("duplicate should skip parsing")),
    )

    result = batch_importer._import_scanned_paper(
        archived,
        db_path,
        stored_source_file="question_bank/raw_papers/paper_0123456789ab.docx",
        source_title="paper",
        metadata=PaperMetadata(),
        question_range=None,
    )

    assert result.status == "duplicate"


def test_infer_semester_from_parenthesized_filename_marker() -> None:
    metadata = infer_metadata_from_filename("2024-2025学年深圳市七年级（下）期末数学试卷.docx")

    assert metadata.semester == "下学期"
