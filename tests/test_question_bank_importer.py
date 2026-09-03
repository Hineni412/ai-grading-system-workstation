from __future__ import annotations

import hashlib
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
from question_bank.services.rich_content_service import (
    load_question_rich_content,
    save_question_rich_content,
)
from tests.question_bank_support import QuestionBankTestStore


def test_rich_content_drops_next_question_section_heading_on_save_and_load(
    tmp_path: Path,
) -> None:
    root = tmp_path / "rich-content"
    sidecar = save_question_rich_content(
        4,
        question_blocks=[
            {"kind": "paragraph", "text": "优美比为（ ）。"},
            {
                "kind": "paragraph",
                "text": "二．填空题（共5小题）\n[[IMAGE:D:/next-question.png]]",
            },
        ],
        answer_blocks=[{"kind": "paragraph", "text": "答案 C"}],
        root=root,
    )

    assert sidecar is not None
    payload = load_question_rich_content(4, root=root)
    assert payload is not None
    assert [block["text"] for block in payload["question_blocks"]] == [
        "优美比为（ ）。"
    ]
    assert [block["text"] for block in payload["answer_blocks"]] == ["答案 C"]


def test_rich_mapping_treats_hashed_source_stem_as_paper_title_noise() -> None:
    mapped = batch_importer.map_rich_content_by_number(
        [
            {"text": "1．第一题", "image_relationships": {}},
            {"text": "0526学情小结", "image_relationships": {}},
        ],
        source_file="0526学情小结_852c34e4ef74.docx",
    )

    assert [block["text"] for block in mapped["question"]["1"]] == ["1．第一题"]


def test_scanned_pdf_without_text_is_reported_as_needing_ocr(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"%PDF-1.4\n")
    db_path = tmp_path / "question_bank.db"

    monkeypatch.setattr(
        batch_importer,
        "_extract_paper",
        lambda path, **_kwargs: ExtractedDocument(
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


def test_parse_paper_text_type_overrides_win_over_heuristic() -> None:
    text = (
        "1．如图，在△ABC中，填写表格：y＝______，并说明理由。\n"
        "2．若AD∥BC，则∠ABD＝______°。\n"
    )
    parsed = parse_paper_text(
        text,
        source_file="regular_paper.docx",
        page_range="document",
        type_overrides={"1": "解答题（证明）"},
    )

    by_number = {question.question_number: question for question in parsed.questions}
    assert by_number["1"].question_type == "解答题（证明）"
    # No override: the local heuristic still applies.
    assert by_number["2"].question_type == "填空题"


def test_parse_paper_text_does_not_broadcast_document_level_images() -> None:
    parsed = parse_paper_text(
        (
            "1．这是一道足够长的纯文字题目内容\n"
            "2．这是含图的题目内容[[IMAGE:extracted.png]]\n"
            "答案：\n"
            "1. 1\n"
            "2. 2"
        ),
        source_file="tmp_sample.docx",
        page_range="document",
        has_images=True,
        needs_image_review=True,
        image_paths=["extracted.png"],
    )
    by_number = {item.question_number: item for item in parsed.questions}

    assert by_number["1"].has_images is False
    assert by_number["1"].image_paths == []
    assert by_number["2"].has_images is True
    assert by_number["2"].image_paths == ["extracted.png"]


def test_cross_paper_duplicate_question_is_kept_for_complete_paper_import(tmp_path: Path) -> None:
    db_path = tmp_path / "question_bank.db"
    service = QuestionBankTestStore(db_path)
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
        lambda path, **_kwargs: ExtractedDocument(
            source_file=str(path),
            page_range="document",
            text="1. 这是长度足够的测试题目\n答案：\n1. 42",
            rich_paragraphs=[
                {
                    "text": "1. 这是长度足够的测试题目",
                    "xml": "<w:p>browser-safe-rich-content</w:p>",
                    "image_relationships": {},
                }
            ],
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
    assert (data_root / "question_bank" / "rich_content" / "question_1.json").exists()
    assert source.exists()
    assert result.files[0].source_file == paper_source


def test_docx_image_assets_follow_the_import_data_root(tmp_path: Path, monkeypatch) -> None:
    data_root = tmp_path / "isolated-data"
    source = tmp_path / "paper.docx"
    source.write_bytes(b"paper-content")
    captured: dict[str, Path | None] = {}

    def fake_import_docx(path: Path, *, asset_root: Path | None = None) -> ExtractedDocument:
        captured["asset_root"] = asset_root
        return ExtractedDocument(
            source_file=str(path),
            page_range="document",
            text="1. 这是一道用于验证图片存储位置的测试题目",
        )

    monkeypatch.setattr(batch_importer, "import_docx", fake_import_docx)

    result = batch_importer.import_scanned_papers(
        [ScannedPaper(source_file=str(source), file_type="docx")],
        data_root / "databases" / "question_bank.db",
        data_root=data_root,
        archive_sources=False,
    )

    assert result.failed_files == 0
    assert captured["asset_root"] == data_root.resolve() / "question_bank" / "extracted_images"


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
        lambda _path, **_kwargs: (_ for _ in ()).throw(AssertionError("duplicate should skip parsing")),
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


def test_exact_fingerprint_in_trash_creates_a_fresh_active_paper(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    archived = tmp_path / "paper.docx"
    archived.write_bytes(b"paper-content")
    fingerprint = batch_importer._file_fingerprint(archived)
    with connect(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO papers (
                title, source_file, content_fingerprint, import_status
            ) VALUES (?, ?, ?, 'deleted')
            """,
            ("paper", "old.docx", fingerprint),
        )
        paper_id = int(cursor.lastrowid)
    monkeypatch.setattr(
        batch_importer,
        "_extract_paper",
        lambda path, **_kwargs: ExtractedDocument(
            source_file=str(path),
            page_range="document",
            text="1．这是一道全新入库的数学题目",
            needs_ocr=False,
        ),
    )

    result = batch_importer._import_scanned_paper(
        archived,
        db_path,
        stored_source_file="question_bank/raw_papers/paper.docx",
        source_title="paper",
        metadata=PaperMetadata(),
        question_range=None,
    )

    assert result.status == "needs_review"
    assert result.paper_id != paper_id
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, import_status FROM papers ORDER BY id"
        ).fetchall()
        assert [(int(row["id"]), row["import_status"]) for row in rows] == [
            (paper_id, "deleted"),
            (result.paper_id, "needs_review"),
        ]


def test_exact_fingerprint_in_active_bank_reuses_existing_paper_identity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    archived = tmp_path / "paper.docx"
    archived.write_bytes(b"paper-content")
    fingerprint = batch_importer._file_fingerprint(archived)
    with connect(db_path) as conn:
        paper_id = int(
            conn.execute(
                """
                INSERT INTO papers (
                    title, source_file, content_fingerprint, import_status
                ) VALUES (?, ?, ?, 'imported')
                """,
                ("已有试卷", "old.docx", fingerprint),
            ).lastrowid
        )
    monkeypatch.setattr(
        batch_importer,
        "_extract_paper",
        lambda _path: (_ for _ in ()).throw(
            AssertionError("active fingerprint collision should skip parsing")
        ),
    )

    result = batch_importer._import_scanned_paper(
        archived,
        db_path,
        stored_source_file="question_bank/raw_papers/renamed.docx",
        source_title="另一名称",
        metadata=PaperMetadata(),
        question_range=None,
    )

    assert result.status == "duplicate"
    assert result.paper_id == paper_id


def test_same_title_with_different_fingerprint_imports_as_a_new_paper(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    with connect(db_path) as conn:
        original_id = int(
            conn.execute(
                """
                INSERT INTO papers (
                    title, source_file, content_fingerprint, import_status
                ) VALUES (?, ?, ?, 'imported')
                """,
                ("同名试卷", "old.docx", "a" * 64),
            ).lastrowid
        )
    archived = tmp_path / "同名试卷.docx"
    archived.write_bytes(b"different-paper-content")
    monkeypatch.setattr(
        batch_importer,
        "_extract_paper",
        lambda path, **_kwargs: ExtractedDocument(
            source_file=str(path),
            page_range="document",
            text="1. 这是另一份内容不同的测试题目",
        ),
    )

    result = batch_importer._import_scanned_paper(
        archived,
        db_path,
        stored_source_file="question_bank/raw_papers/同名试卷.docx",
        source_title="同名试卷",
        metadata=PaperMetadata(),
        question_range=None,
    )

    assert result.status == "needs_review"
    assert result.paper_id != original_id
    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 2


def test_infer_semester_from_parenthesized_filename_marker() -> None:
    metadata = infer_metadata_from_filename("2024-2025学年深圳市七年级（下）期末数学试卷.docx")

    assert metadata.semester == "下学期"


def _extracted_with_floating_image(tmp_path: Path) -> ExtractedDocument:
    floating = tmp_path / "floating.png"
    floating.write_bytes(b"floating-image-bytes")
    inline = tmp_path / "inline.png"
    inline.write_bytes(b"inline-image-bytes")
    paragraphs = [
        {"text": "1. 第一题题干内容", "xml": "", "image_relationships": {}},
        {
            "text": f"[[IMAGE:{floating}]]",
            "xml": "<wp:anchor/>",
            "image_relationships": {},
        },
        {"text": "2. 第二题题干内容", "xml": "", "image_relationships": {}},
        {
            "text": f"第二题已有插图\n[[IMAGE:{inline}]]",
            "xml": "",
            "image_relationships": {},
        },
        {"text": "3. 第三题题干内容", "xml": "", "image_relationships": {}},
    ]
    return ExtractedDocument(
        source_file="tmp_asset_override_paper.docx",
        page_range="document",
        text="\n".join(str(item["text"]) for item in paragraphs),
        has_images=True,
        needs_image_review=True,
        image_paths=[str(floating), str(inline)],
        rich_paragraphs=paragraphs,
    )


def test_apply_asset_overrides_ignore_removes_floating_image(tmp_path: Path) -> None:
    extracted = _extracted_with_floating_image(tmp_path)
    floating = extracted.image_paths[0]
    digest = hashlib.sha256(Path(floating).read_bytes()).hexdigest()

    result = batch_importer.apply_asset_overrides(
        extracted,
        [
            {
                "sha256": digest,
                "action": "ignore",
                "question_number": None,
                "asset_kind": None,
            }
        ],
    )

    assert floating not in result.text
    assert "1. 第一题题干内容\n2. 第二题题干内容" in result.text
    assert result.image_paths == extracted.image_paths
    parsed = parse_paper_text(
        result.text,
        source_file="tmp_asset_override_paper.docx",
        page_range="document",
    )
    by_number = {item.question_number: item for item in parsed.questions}
    assert floating not in by_number["1"].image_paths
    assert floating not in by_number["2"].image_paths
    assert extracted.image_paths[1] in by_number["2"].image_paths


def test_apply_asset_overrides_bind_moves_image_to_next_question(tmp_path: Path) -> None:
    extracted = _extracted_with_floating_image(tmp_path)
    floating, inline = extracted.image_paths
    digest = hashlib.sha256(Path(floating).read_bytes()).hexdigest()

    result = batch_importer.apply_asset_overrides(
        extracted,
        [
            {
                "sha256": digest,
                "action": "bind",
                "question_number": "2",
                "asset_kind": "question",
            }
        ],
    )

    assert [str(item["text"]) for item in result.rich_paragraphs] == [
        "1. 第一题题干内容",
        "2. 第二题题干内容",
        f"第二题已有插图\n[[IMAGE:{inline}]]",
        f"[[IMAGE:{floating}]]",
        "3. 第三题题干内容",
    ]
    parsed = parse_paper_text(
        result.text,
        source_file="tmp_asset_override_paper.docx",
        page_range="document",
    )
    by_number = {item.question_number: item for item in parsed.questions}
    assert floating not in by_number["1"].image_paths
    assert by_number["2"].image_paths == [inline, floating]
    assert floating not in by_number["3"].image_paths


def test_apply_asset_overrides_keeps_document_when_bind_target_missing(
    tmp_path: Path,
) -> None:
    extracted = _extracted_with_floating_image(tmp_path)
    floating = extracted.image_paths[0]
    digest = hashlib.sha256(Path(floating).read_bytes()).hexdigest()

    result = batch_importer.apply_asset_overrides(
        extracted,
        [
            {
                "sha256": digest,
                "action": "bind",
                "question_number": "9",
                "asset_kind": "question",
            }
        ],
    )

    assert result.text == extracted.text
    assert [item["text"] for item in result.rich_paragraphs] == [
        item["text"] for item in extracted.rich_paragraphs
    ]


def test_apply_asset_overrides_skips_unknown_sha256(tmp_path: Path) -> None:
    extracted = _extracted_with_floating_image(tmp_path)
    digest = hashlib.sha256(b"not-in-document").hexdigest()

    result = batch_importer.apply_asset_overrides(
        extracted,
        [
            {
                "sha256": digest,
                "action": "ignore",
                "question_number": None,
                "asset_kind": None,
            }
        ],
    )

    assert result.text == extracted.text
    assert result.image_paths == extracted.image_paths


def _paragraph_record_for_test(text: str, num_id: int, ilvl: int):
    """构造带 Word 自动编号（numPr）的段落。"""
    document = Document()
    paragraph = document.add_paragraph(text)
    p_pr = paragraph._p.get_or_add_pPr()  # noqa: SLF001 - build the exact numbering structure.
    p_pr.append(
        parse_xml(
            f'<w:numPr {nsdecls("w")}>'
            f'<w:ilvl w:val="{ilvl}"/><w:numId w:val="{num_id}"/>'
            f"</w:numPr>"
        )
    )
    return document, paragraph


def test_parse_paper_text_merges_backward_numbering_and_keeps_first_answer() -> None:
    # 深圳高级中学卷事故场景：第 3 题内部小步骤“1．”“2．”曾被当成新题，
    # 答案区同号解析块顶掉了第 1、2 题的答案。
    text = (
        "1．第一题题干内容足够长\n"
        "2．第二题题干内容足够长\n"
        "3．这是一道探究题的题干内容足够长\n"
        "【初步理解】\n"
        "1．（1）这是第三题内部的小步骤一\n"
        "【深入探究】\n"
        "2．这是第三题内部的小步骤二\n"
        "参考答案\n"
        "1．【答案】D\n"
        "2．【答案】B\n"
        "3．【答案】见解析\n"
        "【分析】第三题的分析\n"
        "【解答】\n"
        "1．（1）小步骤一的解析\n"
        "2．小步骤二的解析\n"
        "【点评】第三题的点评"
    )
    parsed = parse_paper_text(text, source_file="regular_paper.docx", page_range="document")

    by_number = {question.question_number: question for question in parsed.questions}
    assert [question.question_number for question in parsed.questions] == ["1", "2", "3"]
    # 答案不再被后面的同号解析块顶掉
    assert by_number["1"].answer_text == "【答案】D"
    assert by_number["2"].answer_text == "【答案】B"
    # 小步骤并回第 3 题的题干和答案
    assert "小步骤一" in by_number["3"].question_text
    assert "小步骤二" in by_number["3"].question_text
    assert "小步骤一的解析" in (by_number["3"].answer_text or "")
    assert "【点评】第三题的点评" in (by_number["3"].answer_text or "")
    # 合并过异常编号的题目标记为需复核
    assert by_number["3"].needs_review is True
    assert by_number["1"].needs_review is False
    assert parsed.review_count >= 1
    assert any("倒序" in reason or "重复" in reason for reason in parsed.review_reasons)


def test_parse_paper_text_flags_numbering_gaps_without_merging() -> None:
    parsed = parse_paper_text(
        "1．题目一内容足够长\n2．题目二内容足够长\n5．题目五内容足够长",
        source_file="regular_paper.docx",
        page_range="document",
    )

    # 前向跳号仍然正常切分，但体检给出断号提示
    assert [question.question_number for question in parsed.questions] == ["1", "2", "5"]
    assert any("不连续" in reason for reason in parsed.review_reasons)
    assert parsed.review_count >= 1


def test_parse_paper_text_skips_continuity_check_for_range_import() -> None:
    parsed = parse_paper_text(
        "3．题目三内容足够长\n4．题目四内容足够长",
        source_file="regular_paper.docx",
        page_range="document",
        question_range="3-4",
    )

    assert [question.question_number for question in parsed.questions] == ["3", "4"]
    assert parsed.review_reasons == ()


def test_parse_paper_text_flags_unmatched_answer_numbers() -> None:
    parsed = parse_paper_text(
        "1．题目一内容足够长\n2．题目二内容足够长\n答案\n1．A\n2．B\n3．C",
        source_file="regular_paper.docx",
        page_range="document",
    )

    assert any("无对应题目" in reason for reason in parsed.review_reasons)
    assert parsed.review_count >= 1


def test_parse_paper_text_flags_answer_label_inside_question_stem() -> None:
    parsed = parse_paper_text(
        "1．题目一内容足够长【答案】D\n2．题目二内容足够长",
        source_file="regular_paper.docx",
        page_range="document",
    )

    by_number = {question.question_number: question for question in parsed.questions}
    assert by_number["1"].needs_review is True


def test_document_title_line_filtered_without_filename_match() -> None:
    # 源文件归档改名后文件名过滤失效（0526学情小结事故），文档首行兜底过滤
    text = (
        "0526学情小结\n"
        "1．第一题题干内容足够长\n"
        "2．第二题题干内容足够长\n"
        "0526学情小结\n"
        "参考答案\n"
        "1．A\n"
        "2．B"
    )
    parsed = parse_paper_text(text, source_file="source_e51f1eb781da.docx", page_range="document")

    by_number = {question.question_number: question for question in parsed.questions}
    assert "0526学情小结" not in by_number["2"].question_text


def test_map_rich_content_merges_backward_numbering_into_current_question() -> None:
    mapped = batch_importer.map_rich_content_by_number(
        [
            {"text": "1．第一题", "image_relationships": {}},
            {"text": "2．第二题", "image_relationships": {}},
            {"text": "1．内部小步骤", "image_relationships": {}},
        ],
        source_file="regular_paper.docx",
    )

    assert [block["text"] for block in mapped["question"]["1"]] == ["1．第一题"]
    assert [block["text"] for block in mapped["question"]["2"]] == [
        "2．第二题",
        "1．内部小步骤",
    ]


def test_map_rich_content_ignores_sublevel_numbering_marker() -> None:
    mapped = batch_importer.map_rich_content_by_number(
        [
            {"text": "1．第一题", "image_relationships": {}, "numbering_level": 0},
            {"text": "1．子层级列表项", "image_relationships": {}, "numbering_level": 1},
        ],
        source_file="regular_paper.docx",
    )

    assert [block["text"] for block in mapped["question"]["1"]] == [
        "1．第一题",
        "1．子层级列表项",
    ]


def test_map_rich_content_filters_repeated_doc_title_line() -> None:
    mapped = batch_importer.map_rich_content_by_number(
        [
            {"text": "0526学情小结", "image_relationships": {}},
            {"text": "1．第一题", "image_relationships": {}},
            {"text": "2．第二题", "image_relationships": {}},
            {"text": "0526学情小结", "image_relationships": {}},
            {"text": "参考答案", "image_relationships": {}},
            {"text": "1．A", "image_relationships": {}},
        ],
        source_file="source_e51f1eb781da.docx",
    )

    assert [block["text"] for block in mapped["question"]["2"]] == ["2．第二题"]


def test_docx_importer_restarts_auto_number_per_word_list(tmp_path: Path) -> None:
    from question_bank.importers.docx_importer import _paragraph_record

    document, first = _paragraph_record_for_test("第一题题干", num_id=5, ilvl=0)
    _, second = _paragraph_record_for_test("第二题题干", num_id=5, ilvl=0)
    _, answer_first = _paragraph_record_for_test("第一题答案", num_id=6, ilvl=0)

    state: dict = {"counters": {}}
    record_first = _paragraph_record(first, document, tmp_path, {}, state)
    record_second = _paragraph_record(second, document, tmp_path, {}, state)
    record_answer = _paragraph_record(answer_first, document, tmp_path, {}, state)

    assert record_first["text"].startswith("1. ")
    assert record_second["text"].startswith("2. ")
    # 答案区另起编号列表时重新从 1 开始，而不是接着题目继续数
    assert record_answer["text"].startswith("1. ")
    assert record_first["numbering_level"] == 0


def test_docx_importer_marks_sublevel_numbering_without_auto_prefix(tmp_path: Path) -> None:
    from question_bank.importers.docx_importer import _paragraph_record

    document, paragraph = _paragraph_record_for_test("1．手敲编号的子步骤", num_id=5, ilvl=1)

    record = _paragraph_record(paragraph, document, tmp_path, {}, {"counters": {}})

    # 子层级段落不追加自动编号，并携带层级信息供下游识别
    assert record["text"] == "1．手敲编号的子步骤"
    assert record["numbering_level"] == 1
