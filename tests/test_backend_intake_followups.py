"""Regression scenarios from the intake/dedup audit, using synthetic sources."""
from __future__ import annotations

import io
import sqlite3
from dataclasses import replace
from pathlib import Path

import fitz
import pytest
from PIL import Image

from backend.document_parsing.question_blocks import parse_plain_question_blocks, parse_rich_question_blocks
from question_bank.importers.batch_importer import parse_paper_text
from question_bank.parsers.type_detector import RepeatedQuestionNumberError, question_section_type
from question_bank.services.duplicate_analysis_copy_service import exact_question_key


@pytest.mark.parametrize("heading", ["一、单项选择题", "第一部分 选择题", "一．选一选"])
def test_section_aliases_share_exam_and_bank_type(heading):
    text = f"{heading}\n1. 这是试题题面\n参考答案\n1. A"
    assert parse_plain_question_blocks(text)[0]["question_type"] == "choice"
    assert parse_paper_text(text, source_file="synthetic.docx", page_range="1").questions[0].question_type == "选择题"


def test_answer_brand_does_not_change_stem_type():
    blocks = parse_plain_question_blocks("1. 购买十件商品，比较两种方案的费用。\n参考答案\n1. 选择A品牌更划算。")
    assert blocks[0]["question_type"] == "comprehensive"
    assert blocks[0]["needs_review"] is False


def test_unknown_section_resets_hint_and_repeated_numbers_fail():
    assert question_section_type("二、新题型体验") == ""
    text = "一、选择题\n1. 下列各式中正确的一项是\n二、新题型体验\n2. 求两种方案分别需要的费用。"
    assert parse_plain_question_blocks(text)[1]["question_type"] == "comprehensive"
    for parser in (parse_plain_question_blocks, lambda text: parse_paper_text(text, source_file="synthetic", page_range="1")):
        with pytest.raises(RepeatedQuestionNumberError):
            parser("一、选择题\n1. 题面甲\n二、填空题\n1. 题面乙____")


def test_same_line_rich_stem_answer_and_analysis_are_preserved():
    block = parse_rich_question_blocks(1, [{"text": "1. 求2+3的值。【答案】5【解析】直接相加。"}], [], {})
    assert "求2+3" in block["question_text"]
    assert block["answer_text"] == "5"
    assert "直接相加" in str(block)


def _pdf(pages):
    with fitz.open() as doc:
        for lines in pages:
            page = doc.new_page(width=600, height=800)
            for x, y, text in lines:
                page.insert_text((x, y), text, fontsize=12)
        return doc.tobytes()


def _scan_pdf():
    payload = _pdf([[(40,100,"1. SCANNED LEFT"),(340,100,"2. SCANNED RIGHT")]])
    with fitz.open(stream=payload, filetype="pdf") as original, fitz.open() as scanned:
        page = scanned.new_page(width=600,height=800)
        page.insert_image(page.rect, stream=original[0].get_pixmap().tobytes("png"))
        return scanned.tobytes()


def test_two_column_crops_do_not_capture_neighbor_and_last_question_crosses_page():
    from rubric_auto_cropper import extract_pdf_question_images
    payload = _pdf([[(40,80,"1. LEFT"),(40,150,"left continuation"),(340,80,"2. RIGHT")],
                    [(40,80,"3. LAST"),(40,720,"last page one")],
                    [(40,80,"last continuation"),(40,500,"Answer"),(40,550,"1. A")]])
    crops = extract_pdf_question_images(payload, [{"question_id":f"Q{i}"} for i in (1,2,3)], scale=1)
    left = Image.open(io.BytesIO(crops["Q1"]["question"]))
    last = Image.open(io.BytesIO(crops["Q3"]["question"]))
    assert left.width < 340
    assert last.height > 1000
    assert last.height < 1300  # stops before answers, including page three
    assert crops["Q1"]["answer"]


def test_scan_crops_use_supplied_layout_without_text_layer():
    from rubric_auto_cropper import extract_pdf_question_images
    payload = _scan_pdf()
    result = extract_pdf_question_images(payload, [{"question_id":"Q1"},{"question_id":"Q2"}],
        layout_pages={0:[{"text":"1. question", "bbox":[.05,.1,.45,.2]},
                         {"text":"2. question", "bbox":[.55,.1,.95,.2]}]}, scale=1)
    assert set(result) == {"Q1","Q2"}
    assert Image.open(io.BytesIO(result["Q1"]["question"])).width < 350


def test_exam_pdf_reuses_bank_extraction_and_layout(tmp_path, monkeypatch):
    import asyncio
    from tests.test_config_source_service import service, chunks
    from question_bank.importers import batch_importer
    from question_bank.importers.types import ExtractedDocument
    calls = []
    def extract(path, **kwargs):
        calls.append(path)
        return ExtractedDocument(str(path), "1", "1. Compute 2+3.", pdf_layout={"pages":{
            0:[{"text":"1. Compute 2+3.","bbox":[.08,.1,.8,.2]}]}})
    monkeypatch.setattr(batch_importer, "_extract_paper", extract)
    record = asyncio.run(service(tmp_path).stage_and_parse(
        session_id=7, filename="synthetic-scan.pdf", chunks=chunks(_scan_pdf()),
    ))
    assert len(calls) == 1
    assert record.questions[0].question_id == "Q1"
    assert record.private_blocks[0]["semantic_source"] == "images"
    assert service(tmp_path).load(session_id=7, source_id=record.source_id).questions


def test_image_analysis_never_sends_ocr_as_semantic_text():
    from question_bank.training_criteria.adapters import question_analysis_input_from_config_source
    from question_bank.training_criteria.analysis import QuestionAnalysisImage
    source = {"semantic_source":"images", "question_text":"BROKEN OCR", "analysis":"BROKEN ANALYSIS",
              "reference_solution":{"canonical_answer":"BROKEN ANSWER"}, "question_type":"proof"}
    with pytest.raises(ValueError, match="裁图"):
        question_analysis_input_from_config_source(source, question_id=1, curriculum_volume_id="g8-upper")
    picture = io.BytesIO()
    Image.new("RGB", (40,40), "white").save(picture, format="PNG")
    question = question_analysis_input_from_config_source(source, question_id=1, curriculum_volume_id="g8-upper",
        images=[QuestionAnalysisImage(role="question", mime_type="image/png", content=picture.getvalue())])
    assert question.semantic_source == "images"
    assert "BROKEN" not in str(question)


@pytest.mark.parametrize("ids", [["part-1"],["part-1","part-3"],["part-1","part-1"]])
def test_incomplete_wrong_or_duplicate_part_difficulty_is_invalid(ids):
    from tests.test_question_bank_ai_tagging_quality import _analysis, _part_feature
    from question_bank.models.tag_schema import TaggingContext
    from question_bank.services.ai_tagging_service import _evaluate_analysis_quality
    analysis = _analysis(part_features=[_part_feature(part_id=part) for part in ids])
    context = TaggingContext(question_text="测试两问", evidence_parts=[{"part_id":"part-1"},{"part_id":"part-2"}])
    assert _evaluate_analysis_quality(analysis, context)[0] == "invalid"


def test_exact_identity_normalizes_word_latex_but_keeps_changed_exponents(tmp_path):
    from question_bank.document_pipeline.math_omml import restricted_latex_to_omml
    word = {"question_text":"求x<sup>2</sup>+1的值"}
    rich = {"question_blocks":[{"xml":restricted_latex_to_omml("x^2")}]}
    word_key = exact_question_key(word, data_root=tmp_path, rich_content=rich)
    latex_key = exact_question_key({"question_text":r"求$x^2$+1的值"}, data_root=tmp_path)
    assert word_key and word_key == latex_key
    assert word_key != exact_question_key({"question_text":r"求$x^3$+1的值"}, data_root=tmp_path)


def test_backfill_preview_is_readonly_and_apply_repairs_cycles_and_bad_links(tmp_path):
    from question_bank.database.schema import initialize_database, connect
    from question_bank.database.backfill_question_duplicate_links import backfill_duplicate_links
    root = tmp_path / "isolated"
    db = root / "databases" / "question_bank.db"
    initialize_database(db)
    with connect(db) as conn:
        for qid in range(1,5):
            conn.execute("INSERT INTO questions(id,question_number,question_text,answer_text) VALUES(?,?,?,?)",
                (qid,str(qid),"求2+3的值" if qid<4 else "求2+4的值","5"))
        conn.execute("INSERT INTO question_tags(question_id,tag_type,tag_value,source) VALUES(2,'ability','运算能力','manual')")
        for qid,target in [(1,2),(2,1),(4,1)]:
            conn.execute("INSERT INTO question_duplicate_links(question_id,duplicate_of_question_id,match_kind,signature) VALUES(?,?,'exact','old')", (qid,target))
    before = db.read_bytes()
    preview = backfill_duplicate_links(db)
    assert db.read_bytes() == before
    assert preview["applied"] is False and preview["invalid_links"] == 1
    assert preview["group_details"][0]["canonical_id"] == 2
    applied = backfill_duplicate_links(db, apply=True)
    assert applied["inserted"] == 2
    with connect(db) as conn:
        assert [tuple(row) for row in conn.execute("SELECT question_id,duplicate_of_question_id FROM question_duplicate_links ORDER BY question_id")] == [(1,2),(3,2)]
        assert conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0] == 4
    assert backfill_duplicate_links(db, apply=True)["inserted"] == 0


def test_near_search_crosses_type_labels_and_returns_best_match():
    from question_bank.importers.batch_importer import _DuplicateIndex, _near_duplicate_hint, ParsedQuestion
    index = _DuplicateIndex()
    index.add(1, question_text="已知三角形ABC的边长为3、4、5，求其面积。", answer_text="6", question_type="填空题", paper_title="合成一", key="")
    index.add(2, question_text="已知三角形ABC的边长为3、4、6，求其面积。", answer_text="", question_type="解答题", paper_title="合成二", key="")
    question = ParsedQuestion(question_number="1",question_text="已知三角形ABC的边长为3、4、5，求其面积。",
        question_type="解答题", answer_text="6", source_file="test",page_range="1")
    hint = _near_duplicate_hint(question, index)
    assert hint["matched_question_id"] == 1
    assert hint["requires_review"] and hint["match_kind"] == "suspected"


def test_numbered_steps_inside_later_question_are_not_repeated_main_numbers():
    from question_bank.parsers.type_detector import validate_section_numbering
    validate_section_numbering("一、选择题\n1. 选择题\n二、解答题\n2. 根据下列步骤进行操作\n1. 第一步\n2. 第二步")


def test_duplicate_family_follows_chain_but_excludes_changed_content(tmp_path):
    from tests.test_error_patterns import _make_bank_db
    from backend.error_patterns import session_bank_context
    path = tmp_path / "databases" / "question_bank.db"
    conn = _make_bank_db(path)
    conn.execute("INSERT INTO questions(id,question_text,answer_text,question_type) SELECT 303,question_text,answer_text,question_type FROM questions WHERE id=202")
    conn.execute("INSERT INTO question_duplicate_links(question_id,duplicate_of_question_id) VALUES(303,202)")
    conn.execute("INSERT INTO grading_question_links(grading_session_id,source_question_id,bank_question_id) VALUES('10','Q2',303)")
    conn.commit()
    assert session_bank_context(path, 9)["Q1"]["bank_ids"] == {101,202,303}
    conn.execute("UPDATE questions SET question_text='另一道完全不同的合成试题' WHERE id=101")
    conn.commit();conn.close()
    context = session_bank_context(path, 9)["Q1"]
    assert context["bank_ids"] == {202,303}
    assert context["linked"] == {(10,"Q2")}


def test_garbled_pdf_text_uses_existing_local_ocr_adapter(tmp_path, monkeypatch):
    from question_bank.document_pipeline.pipeline import QuestionDocumentPipeline
    from question_bank.document_pipeline.adapters import OcrLine
    from question_bank.importers import mineru_parse
    from question_bank.importers.batch_importer import _extract_paper
    class FakeOCR:
        def recognize(self, content, *, page_number):
            return [OcrLine("1. Compute 2+3 and write the result.",((.05,.1),(.8,.1),(.8,.2),(.05,.2)),1,"synthetic-local-ocr")]
    monkeypatch.setattr(mineru_parse,"_run_mineru",lambda path:None)
    source = tmp_path / "synthetic-garbled.pdf"
    source.write_bytes(_pdf([[(40,100,"!@#$%^&*()!@#$%^&*()")]]))
    result = _extract_paper(source, document_pipeline=QuestionDocumentPipeline(tmp_path/"ocr",ocr_adapter=FakeOCR()), operation_id="garbled")
    assert result.ocr_applied and not result.needs_ocr
    assert "Compute 2+3" in result.text
    assert result.pdf_layout["pages"][0][0]["bbox"] == (.05,.1,.8,.2)


def test_normal_pdf_still_checks_colored_answer_layer(tmp_path,monkeypatch):
    from question_bank.importers import batch_importer
    source = tmp_path / "synthetic-color.pdf"
    source.write_bytes(_pdf([[(40,100,"1. Normal text layer is usable.")]]))
    calls=[]
    def colored(path, *,image_dir,layout_out):
        calls.append(path)
        layout_out["question_pdf"]=b"synthetic student layer"
        return "1. Student stem without the colored answer.\n参考答案\n1. 5", {}
    monkeypatch.setattr(batch_importer,"_extract_with_colored_layers",colored)
    result=batch_importer._extract_paper(source)
    assert calls==[source]
    assert "Student stem" in result.text
    assert result.pdf_layout["question_pdf"]==b"synthetic student layer"
