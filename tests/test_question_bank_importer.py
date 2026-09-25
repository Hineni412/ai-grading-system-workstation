from __future__ import annotations

import hashlib
from pathlib import Path

import fitz
from docx import Document
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls

from question_bank.document_pipeline import OcrLine, QuestionDocumentPipeline
from question_bank.importers import batch_importer
from question_bank.importers.batch_importer import (
    PaperMetadata,
    ScannedPaper,
    infer_metadata_from_filename,
    parse_paper_text,
)
from question_bank.database.schema import connect
from question_bank.database.schema import initialize_database
from question_bank.importers.types import ExtractedDocument
from question_bank.importers.docx_importer import _get_paragraph_rich_text
from question_bank.services.rich_content_service import (
    load_question_rich_content,
    save_question_rich_content,
)


def test_drawing_before_question_number_keeps_preview_and_import_aligned(tmp_path: Path) -> None:
    import io
    from PIL import Image
    from backend.document_parsing.docx import parse_docx_question_blocks

    image = io.BytesIO()
    Image.new("RGB", (24, 16), "white").save(image, format="PNG")
    image.seek(0)
    document = Document()
    document.add_paragraph("1. 第一题：计算十加二的结果。")
    paragraph = document.add_paragraph()
    paragraph.add_run().add_picture(image)
    paragraph.add_run("2. 第二题：按图中的程序计算。")
    document.add_paragraph("3. 第三题：计算二十减三的结果。")
    document.add_paragraph("答案和解析")
    document.add_paragraph("1. 【答案】12")
    document.add_paragraph("2. 【答案】8")
    document.add_paragraph("3. 【答案】17")
    source = tmp_path / "配图前置试卷.docx"
    document.save(source)

    preview = parse_docx_question_blocks(
        source.read_bytes(), temporary_root=tmp_path / "preview",
        asset_root=tmp_path / "preview-assets",
    )
    assert [q["question_id"] for q in preview] == ["Q1", "Q2", "Q3"]
    assert "2. 第二题" not in preview[1]["question_html"]
    assert preview[1]["image_paths"]

    # Existing manifests retain their original source and revision; normalize
    # only the displayed text when reopening an older preview.
    from backend.config_workspace.sources import _config_rich_content
    legacy = _config_rich_content(
        {"question_html": "[[IMAGE:legacy.png]]2. 第二题：按图中的程序计算。"},
        session_id=1, source_id="a" * 32, question_id="Q2",
        has_question_asset=True, has_answer_asset=False,
    )
    assert all("2. 第二题" not in b["text"] for b in legacy["question_blocks"])
    assert any(b["asset_urls"] for b in legacy["question_blocks"])

    database = tmp_path / "question_bank.db"
    result = batch_importer._import_scanned_paper(
        source, database, metadata=PaperMetadata(), question_range=None,
        asset_root=tmp_path / "bank-assets", rich_content_root=tmp_path / "rich",
    )
    assert result.question_count == 3
    with connect(database) as conn:
        saved = conn.execute(
            "SELECT question_number, question_text, has_images, image_paths FROM questions ORDER BY id"
        ).fetchall()
    assert [row["question_number"] for row in saved] == ["1", "2", "3"]
    assert [row["has_images"] for row in saved] == [0, 1, 0]
    assert "第二题" not in saved[0]["question_text"]
    assert "第三题" not in saved[1]["question_text"]
    assert "第二题" in saved[1]["question_text"]
    assert load_question_rich_content(2, root=tmp_path / "rich")


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
        type_overrides={"1": "解答题"},
    )

    by_number = {question.question_number: question for question in parsed.questions}
    assert by_number["1"].question_type == "解答题"
    # No override: the local heuristic still applies.
    assert by_number["2"].question_type == "填空题"


def test_parse_paper_text_legacy_subtype_override_splits_into_subtype_tag() -> None:
    # 兼容输入：旧任务负载里的六值题型拆成归一大类 + 子类标签值。
    text = "1．如图，在△ABC中，填写表格：y＝______，并说明理由。\n"
    parsed = parse_paper_text(
        text,
        source_file="regular_paper.docx",
        page_range="document",
        type_overrides={"1": "解答题（证明）"},
    )

    (question,) = parsed.questions
    assert question.question_type == "解答题"
    assert question.essay_subtype == "证明"


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


def test_import_writes_essay_subtype_tags_in_the_same_transaction(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    archived = tmp_path / "paper.docx"
    archived.write_bytes(b"paper-content")
    monkeypatch.setattr(
        batch_importer,
        "_extract_paper",
        lambda path, **_kwargs: ExtractedDocument(
            source_file=str(path),
            page_range="document",
            text=(
                "1．（8分）用尺规作图作出∠ABC的平分线，并证明你的结论。\n"
                "2．（5分）计算：|-1|+(-2)^3 的值。\n"
                "3．（6分）阅读材料，回答下列问题：材料中的规律是什么？\n"
                "4．若AD∥BC，则∠ABD＝______°。\n"
            ),
            needs_ocr=False,
        ),
    )

    result = batch_importer._import_scanned_paper(
        archived,
        db_path,
        stored_source_file="question_bank/raw_papers/paper.docx",
        source_title="subtype paper",
        metadata=PaperMetadata(),
        question_range=None,
    )

    assert result.question_count == 4
    with connect(db_path) as conn:
        questions = {
            str(row["question_number"]): (int(row["id"]), str(row["question_type"]))
            for row in conn.execute(
                "SELECT id, question_number, question_type FROM questions"
            )
        }
        tags = {
            (int(row["question_id"]), str(row["tag_value"])): (
                float(row["confidence"]),
                str(row["source"]),
            )
            for row in conn.execute(
                "SELECT question_id, tag_value, confidence, source "
                "FROM question_tags WHERE tag_type = 'special_type'"
            )
        }
    # 画图优先于证明：带尺规作图的证明题标"画图"。
    assert questions["1"][1] == "解答题"
    assert tags[(questions["1"][0], "画图")] == (0.8, "type_detector")
    # 开头指令式计算题标"计算"。
    assert questions["2"][1] == "解答题"
    assert tags[(questions["2"][0], "计算")] == (0.8, "type_detector")
    # 无强信号的解答题不猜子类，保持未标注。
    assert questions["3"][1] == "解答题"
    assert not any(qid == questions["3"][0] for qid, _value in tags)
    # 填空题不参与子类标注。
    assert questions["4"][1] == "填空题"
    assert not any(qid == questions["4"][0] for qid, _value in tags)
    assert len(tags) == 2


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


def _pdf_with_text_lines(lines: list[str]) -> bytes:
    """单页 PDF：写入真实文字层（供快速路径用例）。"""
    document = fitz.open()
    page = document.new_page(width=600, height=800)
    for index, line in enumerate(lines):
        page.insert_text(
            (40, 60 + index * 28), line, fontsize=12, fontname="china-s"
        )
    content = document.tobytes()
    document.close()
    return content


def _scanned_pdf_from_lines(lines: list[str]) -> bytes:
    """把文字渲染成图片再嵌入新 PDF，保证页面没有可抽取的文字层。"""
    text_document = fitz.open()
    page = text_document.new_page(width=600, height=800)
    for index, line in enumerate(lines):
        page.insert_text(
            (40, 60 + index * 28), line, fontsize=12, fontname="china-s"
        )
    pixmap = page.get_pixmap(alpha=False)
    png = pixmap.tobytes("png")
    text_document.close()
    document = fitz.open()
    scanned_page = document.new_page(width=pixmap.width, height=pixmap.height)
    scanned_page.insert_image(scanned_page.rect, stream=png)
    content = document.tobytes()
    document.close()
    return content


class _FakeOcrAdapter:
    """按页返回预设文本行的 OCR 替身，记录调用页码。"""

    def __init__(self, lines_by_page: dict[int, tuple[OcrLine, ...]]) -> None:
        self.lines_by_page = lines_by_page
        self.calls: list[int] = []

    def recognize(self, png_bytes: bytes, *, page_number: int) -> tuple[OcrLine, ...]:
        assert png_bytes.startswith(b"\x89PNG")
        self.calls.append(page_number)
        return self.lines_by_page.get(page_number, ())


def _ocr_line(text: str, y0: float, y1: float) -> OcrLine:
    return OcrLine(
        text=text,
        polygon=((0.05, y0), (0.95, y0), (0.95, y1), (0.05, y1)),
        confidence=0.9,
        engine_version="fake-ocr/1",
    )


_PAPER_LINES = [
    "1. 题干甲内容足够长",
    "2. 题干乙内容足够长",
    "参考答案",
    "1. 答案甲",
    "2. 答案乙",
]


def test_scanned_pdf_import_runs_local_ocr_and_marks_questions_for_review(
    tmp_path: Path,
) -> None:
    source = tmp_path / "scan.pdf"
    source.write_bytes(_scanned_pdf_from_lines(_PAPER_LINES))
    workspace = tmp_path / "ws"
    ocr = _FakeOcrAdapter(
        {
            1: tuple(
                _ocr_line(text, 0.08 + index * 0.1, 0.16 + index * 0.1)
                for index, text in enumerate(_PAPER_LINES)
            )
        }
    )
    pipeline = QuestionDocumentPipeline(workspace, ocr_adapter=ocr)
    db_path = tmp_path / "data" / "databases" / "question_bank.db"

    result = batch_importer.import_scanned_papers(
        [ScannedPaper(source_file=str(source), file_type="pdf")],
        db_path,
        data_root=tmp_path / "data",
        document_pipeline=pipeline,
        # 固定跳过完整解析，避免本机模型文件齐备时跑真实 MinerU。
        full_parser=lambda path: None,
    )

    assert ocr.calls == [1]
    (file_result,) = result.files
    assert file_result.status == "needs_review"
    assert file_result.question_count == 2
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT question_number, question_text, answer_text, needs_review "
            "FROM questions ORDER BY question_number"
        ).fetchall()
        (paper_status,) = conn.execute("SELECT import_status FROM papers").fetchone()
    assert paper_status == "needs_review"
    assert [row["question_number"] for row in rows] == ["1", "2"]
    assert all(int(row["needs_review"]) == 1 for row in rows)
    assert "题干甲" in rows[0]["question_text"]
    assert "答案甲" in rows[0]["answer_text"]
    assert "答案甲" not in rows[0]["question_text"]
    assert "答案乙" in rows[1]["answer_text"]
    # 文档管线快照落在注入的 workspace_root 下。
    assert list(workspace.rglob("snapshot.json"))


def test_scanned_pdf_with_empty_ocr_result_still_reports_needs_ocr(
    tmp_path: Path,
) -> None:
    source = tmp_path / "scan-empty.pdf"
    source.write_bytes(_scanned_pdf_from_lines(["占位行"]))
    ocr = _FakeOcrAdapter({})
    pipeline = QuestionDocumentPipeline(tmp_path / "ws", ocr_adapter=ocr)
    db_path = tmp_path / "data" / "databases" / "question_bank.db"

    result = batch_importer.import_scanned_papers(
        [ScannedPaper(source_file=str(source), file_type="pdf")],
        db_path,
        data_root=tmp_path / "data",
        document_pipeline=pipeline,
        full_parser=lambda path: None,
    )

    assert ocr.calls == [1]
    (file_result,) = result.files
    assert file_result.status == "needs_ocr"
    assert file_result.question_count == 0
    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 0


def test_text_layer_pdf_import_uses_fast_path_without_ocr(tmp_path: Path) -> None:
    source = tmp_path / "paper.pdf"
    source.write_bytes(_pdf_with_text_lines(_PAPER_LINES))
    workspace = tmp_path / "ws"
    ocr = _FakeOcrAdapter({})
    pipeline = QuestionDocumentPipeline(workspace, ocr_adapter=ocr)
    db_path = tmp_path / "data" / "databases" / "question_bank.db"

    def forbidden_full_parser(path: Path) -> str | None:
        raise AssertionError("text-layer PDF must not reach the full parser")

    result = batch_importer.import_scanned_papers(
        [ScannedPaper(source_file=str(source), file_type="pdf")],
        db_path,
        data_root=tmp_path / "data",
        document_pipeline=pipeline,
        full_parser=forbidden_full_parser,
    )

    assert ocr.calls == []
    (file_result,) = result.files
    assert file_result.status == "imported"
    assert file_result.question_count == 2
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT needs_review FROM questions ORDER BY question_number"
        ).fetchall()
    assert all(int(row["needs_review"]) == 0 for row in rows)
    # 快速路径不经过文档管线，不产生快照文件。
    assert not list(workspace.rglob("snapshot.json"))


def test_scanned_pdf_full_parse_supplies_latex_and_marks_review(tmp_path: Path) -> None:
    source = tmp_path / "scan-formula.pdf"
    source.write_bytes(_scanned_pdf_from_lines(_PAPER_LINES))
    workspace = tmp_path / "ws"
    ocr = _FakeOcrAdapter({})
    pipeline = QuestionDocumentPipeline(workspace, ocr_adapter=ocr)
    markdown = (
        "1. 已知 $x^2+1$ 的最小值，求 x 的取值范围\n"
        "2. 题干乙内容足够长\n"
        "参考答案\n"
        "1. 答案甲\n"
        "2. 答案乙\n"
    )
    full_calls: list[Path] = []

    def fake_full_parser(path: Path) -> str | None:
        full_calls.append(path)
        return markdown

    db_path = tmp_path / "data" / "databases" / "question_bank.db"
    result = batch_importer.import_scanned_papers(
        [ScannedPaper(source_file=str(source), file_type="pdf")],
        db_path,
        data_root=tmp_path / "data",
        document_pipeline=pipeline,
        full_parser=fake_full_parser,
    )

    # 完整解析优先于行级 OCR 管线：OCR 替身没有被调用。
    assert len(full_calls) == 1
    assert ocr.calls == []
    (file_result,) = result.files
    assert file_result.status == "needs_review"
    assert file_result.question_count == 2
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT question_number, question_text, answer_text, needs_review "
            "FROM questions ORDER BY question_number"
        ).fetchall()
    assert [row["question_number"] for row in rows] == ["1", "2"]
    assert all(int(row["needs_review"]) == 1 for row in rows)
    assert "$x^2+1$" in rows[0]["question_text"]
    # 答案区被剥离到 answer_text，不残留在题干里。
    assert "答案甲" in rows[0]["answer_text"]
    assert "答案甲" not in rows[0]["question_text"]
    assert "答案乙" in rows[1]["answer_text"]


def test_scanned_pdf_full_parse_none_falls_back_to_line_ocr(tmp_path: Path) -> None:
    source = tmp_path / "scan-fallback.pdf"
    source.write_bytes(_scanned_pdf_from_lines(_PAPER_LINES))
    workspace = tmp_path / "ws"
    ocr = _FakeOcrAdapter(
        {
            1: tuple(
                _ocr_line(text, 0.08 + index * 0.1, 0.16 + index * 0.1)
                for index, text in enumerate(_PAPER_LINES)
            )
        }
    )
    pipeline = QuestionDocumentPipeline(workspace, ocr_adapter=ocr)
    db_path = tmp_path / "data" / "databases" / "question_bank.db"

    result = batch_importer.import_scanned_papers(
        [ScannedPaper(source_file=str(source), file_type="pdf")],
        db_path,
        data_root=tmp_path / "data",
        document_pipeline=pipeline,
        full_parser=lambda path: None,
    )

    assert ocr.calls == [1]
    (file_result,) = result.files
    assert file_result.status == "needs_review"
    assert file_result.question_count == 2
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT question_text, answer_text FROM questions ORDER BY question_number"
        ).fetchall()
    assert "题干甲" in rows[0]["question_text"]
    assert "答案甲" in rows[0]["answer_text"]


def test_scanned_pdf_full_parse_none_without_pipeline_reports_needs_ocr(
    tmp_path: Path,
) -> None:
    source = tmp_path / "scan-nopipeline.pdf"
    source.write_bytes(_scanned_pdf_from_lines(_PAPER_LINES))
    db_path = tmp_path / "data" / "databases" / "question_bank.db"

    result = batch_importer.import_scanned_papers(
        [ScannedPaper(source_file=str(source), file_type="pdf")],
        db_path,
        data_root=tmp_path / "data",
        full_parser=lambda path: None,
    )

    (file_result,) = result.files
    assert file_result.status == "needs_ocr"
    assert file_result.question_count == 0
    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0] == 0


def test_scanned_pdf_full_parse_empty_string_still_uses_line_ocr(tmp_path: Path) -> None:
    source = tmp_path / "scan-empty-full.pdf"
    source.write_bytes(_scanned_pdf_from_lines(_PAPER_LINES))
    ocr = _FakeOcrAdapter(
        {
            1: tuple(
                _ocr_line(text, 0.08 + index * 0.1, 0.16 + index * 0.1)
                for index, text in enumerate(_PAPER_LINES)
            )
        }
    )
    pipeline = QuestionDocumentPipeline(tmp_path / "ws", ocr_adapter=ocr)
    db_path = tmp_path / "data" / "databases" / "question_bank.db"

    result = batch_importer.import_scanned_papers(
        [ScannedPaper(source_file=str(source), file_type="pdf")],
        db_path,
        data_root=tmp_path / "data",
        document_pipeline=pipeline,
        full_parser=lambda path: "",
    )

    assert ocr.calls == [1]
    (file_result,) = result.files
    assert file_result.status == "needs_review"
    assert file_result.question_count == 2


def test_mineru_full_available_requires_every_model_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from question_bank.importers import mineru_parse

    models_dir = tmp_path / "MinerU-4_models_onnx"
    monkeypatch.setattr(mineru_parse, "MINERU_MODELS_DIR", models_dir)
    assert mineru_parse.mineru_full_available() is False
    for relative in mineru_parse._REQUIRED[:-1]:
        target = models_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"model-bytes")
    # 还差最后一个文件时仍不可用。
    assert mineru_parse.mineru_full_available() is False
    last = models_dir / mineru_parse._REQUIRED[-1]
    last.parent.mkdir(parents=True, exist_ok=True)
    last.write_bytes(b"model-bytes")
    assert mineru_parse.mineru_full_available() is True


def test_materialize_markdown_images_stamps_200dpi_and_drops_decorative(
    tmp_path: Path,
) -> None:
    import base64
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (400, 200), "white").save(buffer, format="JPEG")
    payload = base64.b64encode(buffer.getvalue()).decode("ascii")
    image_dir = tmp_path / "images"
    text, saved = batch_importer._materialize_markdown_images(
        f"![](data:image/jpeg;base64,{payload})", image_dir
    )
    assert len(saved) == 1
    assert "[[IMAGE:" in text
    with Image.open(saved[0]) as image:
        dpi = image.info.get("dpi")
        assert dpi is not None
        assert abs(float(dpi[0]) - 200) < 1
        assert abs(float(dpi[1]) - 200) < 1

    small = io.BytesIO()
    Image.new("RGB", (30, 30), "white").save(small, format="PNG")
    small_payload = base64.b64encode(small.getvalue()).decode("ascii")
    tiny_dir = tmp_path / "tiny"
    tiny_text, tiny_saved = batch_importer._materialize_markdown_images(
        f"![](data:image/png;base64,{small_payload})", tiny_dir
    )
    assert tiny_saved == []
    assert "[[IMAGE:" not in tiny_text
    assert not tiny_dir.exists() or not list(tiny_dir.iterdir())


def test_merge_image_caption_lines_folds_caption_into_marker() -> None:
    merged = batch_importer._merge_image_caption_lines(
        "[[IMAGE:x.png]]\n第3题图\n题干文字"
    )
    assert "[[IMAGE:x.png|caption=第3题图]]" in merged
    assert "\n第3题图\n" not in merged
    match = batch_importer._IMAGE_MARKER.search(merged)
    assert match is not None
    assert match.group("path") == "x.png"
    assert match.group("caption") == "第3题图"

    # 图注在图片之前同样合并。
    merged_before = batch_importer._merge_image_caption_lines(
        "第3题图\n[[IMAGE:y.png]]"
    )
    assert "[[IMAGE:y.png|caption=第3题图]]" in merged_before
    # 孤立图注删除。
    assert batch_importer._merge_image_caption_lines("题干\n示意图\n其余") == (
        "题干\n其余"
    )
    # 下游消费方取 path 仍不含 caption。
    from question_bank.exporters import paper_docx_exporter
    from question_bank.exporters import paper_markdown_exporter
    from question_bank.training_criteria import adapters

    marker = "[[IMAGE:x.png|caption=第3题图]]"
    assert (
        paper_docx_exporter.IMAGE_MARKER_PATTERN.search(marker).group("path")
        == "x.png"
    )
    assert (
        paper_markdown_exporter._IMAGE_MARKER.search(marker).group("path")
        == "x.png"
    )
    assert adapters._IMAGE_MARKER.search(marker).group("path") == "x.png"


def test_ocr_section_noise_stripped_only_on_ocr_path() -> None:
    text = (
        "1. 求图中直角三角形斜边的长度，写出完整的计算过程。\n"
        "知识点2 利用勾股定理求面积"
    )
    cleaned = parse_paper_text(
        text, source_file="paper_a.docx", page_range="document", ocr_noise=True
    )
    assert "知识点" not in cleaned.questions[0].question_text
    kept = parse_paper_text(
        text, source_file="paper_a.docx", page_range="document", ocr_noise=False
    )
    assert "知识点2" in kept.questions[0].question_text


def test_bracket_section_heading_kept_inside_question_body() -> None:
    tail = (
        "1. 探究勾股定理，回答下面的问题，并写出过程。\n"
        "观察图形写出结论。\n"
        "【发现并提出问题】"
    )
    parsed_tail = parse_paper_text(
        tail, source_file="paper_b.docx", page_range="document", ocr_noise=True
    )
    assert "发现并提出问题" not in parsed_tail.questions[0].question_text
    middle = (
        "1. 探究勾股定理，回答下面的问题，并写出过程。\n"
        "【发现并提出问题】\n"
        "观察图形写出结论。\n"
        "再比较两种结果的大小。"
    )
    parsed_middle = parse_paper_text(
        middle, source_file="paper_b.docx", page_range="document", ocr_noise=True
    )
    assert "发现并提出问题" in parsed_middle.questions[0].question_text


def test_triangle_placeholder_marks_fill_blank() -> None:
    from question_bank.parsers.type_detector import detect_question_type

    assert (
        detect_question_type("已知矩形边长 3 和 4，则对角线为▲。") == "填空题"
    )
    # △ABC 是三角形记号，不是填空占位。
    assert detect_question_type("在△ABC中，∠A=90°，求证某结论。") != "填空题"


def test_normalize_mineru_formulas_collapses_block_and_wraps_bare_latex() -> None:
    collapsed = batch_importer._normalize_mineru_formulas(
        "前文\n$$\n\\because AD = 24 m\n$$\n后文"
    )
    assert "$\\because AD = 24 m$" in collapsed
    assert "$$" not in collapsed
    bare = batch_importer._normalize_mineru_formulas("1 3 ~ \\mathrm { c m }")
    assert bare == "$1 3 ~ \\mathrm { c m }$"
    # 正文行（中文字符 ≥4 个）不包。
    prose = batch_importer._normalize_mineru_formulas(
        "图中阴影部分的面积可用 \\frac 表示，请计算。"
    )
    assert not prose.startswith("$")


def test_split_inline_teacher_answers_moves_embedded_answers() -> None:
    from question_bank.importers.batch_importer import (
        ParsedPaperText,
        ParsedQuestion,
        _split_inline_teacher_answers,
    )

    parsed = ParsedPaperText(
        questions=[
            ParsedQuestion(
                question_number="1",
                question_text="阴影部分的面积是（D）A.10 B.28 C.100 D.50",
                source_file="paper.docx",
                page_range="document",
            ),
            ParsedQuestion(
                question_number="2",
                question_text="证明两三角形全等并写出依据。\n解:连接AC，由SSS可得。",
                source_file="paper.docx",
                page_range="document",
            ),
        ],
        answer_match_count=0,
        review_count=0,
    )
    fixed = _split_inline_teacher_answers(parsed)
    first, second = fixed.questions
    assert first.answer_text == "D"
    assert "（D）" not in first.question_text
    assert "A.10" in first.question_text
    assert second.answer_text is not None and second.answer_text.startswith("解")
    assert "解:连接AC" not in second.question_text


def test_split_inline_teacher_answers_prefers_main_question_answer() -> None:
    # 教辅题块尾部常并入“【变式】”子题，子题自带的（A）不能顶掉本题的 (C)。
    from question_bank.importers.batch_importer import (
        ParsedPaperText,
        ParsedQuestion,
        _split_inline_teacher_answers,
    )

    parsed = ParsedPaperText(
        questions=[
            ParsedQuestion(
                question_number="7",
                question_text=(
                    "则A所代表的正方形的面积为 (C) A.10 B.28 C.100 D. 不能确定\n"
                    "[[IMAGE:x.jpg]]\n"
                    "【变式】如图，最大正方形的面积为（A）A.20 B.10 C.5 D.2"
                ),
                source_file="paper.pdf",
                page_range="document",
            ),
        ],
        answer_match_count=0,
        review_count=0,
    )
    (fixed,) = _split_inline_teacher_answers(parsed).questions
    assert fixed.answer_text == "C"
    assert "(C)" not in fixed.question_text
    assert "则A所代表" in fixed.question_text


def test_parse_paper_text_section_hint_fill_blank() -> None:
    # OCR 路径：节标题“二、填空题”把探测器漏掉的填空题改判回来。
    text = (
        "二、填空题（本大题共5小题）\n"
        "9. 若a=2b，则a/b = A O\n"
        "10. 已知矩形的边长分别为3和4，则该矩形的对角线长为▲。"
    )
    parsed = parse_paper_text(
        text,
        source_file="ocr_paper.pdf",
        page_range="document",
        ocr_noise=True,
    )
    by_number = {q.question_number: q for q in parsed.questions}
    assert by_number["9"].question_type == "填空题"
    assert by_number["10"].question_type == "填空题"

    # 非 OCR 路径不受节标题影响。
    parsed_plain = parse_paper_text(
        text,
        source_file="regular_paper.docx",
        page_range="document",
    )
    by_number = {q.question_number: q for q in parsed_plain.questions}
    assert by_number["9"].question_type == "解答题"
