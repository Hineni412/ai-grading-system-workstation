from __future__ import annotations

import io
from pathlib import Path
import zipfile

import fitz
from docx import Document


def _docx_with_duplicate_and_table() -> bytes:
    document = Document()
    document.add_paragraph("Question one")
    document.add_paragraph("Question one")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Left"
    table.cell(0, 1).text = "Right"
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def _two_page_pdf() -> bytes:
    document = fitz.open()
    first = document.new_page()
    first.insert_text((72, 72), "Alpha page")
    second = document.new_page()
    second.insert_text((72, 72), "Beta page")
    payload = document.tobytes()
    document.close()
    return payload


def _docx_with_ordered_header_footer_parts() -> bytes:
    payload = io.BytesIO(_docx_with_duplicate_and_table())
    namespace = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    with zipfile.ZipFile(payload, "a") as archive:
        for name, text in [
            ("word/header2.xml", "HEADER-2"),
            ("word/footer2.xml", "FOOTER-2"),
            ("word/header1.xml", "HEADER-1"),
            ("word/footer1.xml", "FOOTER-1"),
        ]:
            archive.writestr(
                name,
                (
                    f'<w:root xmlns:w="{namespace}"><w:p><w:r>'
                    f"<w:t>{text}</w:t></w:r></w:p></w:root>"
                ),
            )
    return payload.getvalue()


def _docx_with_multiline_proof_answer() -> bytes:
    document = Document()
    document.add_paragraph("1. 证明：若 a=b，则 a+c=b+c。")
    document.add_paragraph("参考答案")
    document.add_paragraph("1. 【答案】结论成立")
    document.add_paragraph("由 a=b，等式两边同时加 c，得到 a+c=b+c。")
    document.add_paragraph("所以原命题得证。")
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def test_text_extractors_preserve_docx_order_deduplication_and_pdf_pages() -> None:
    from backend.document_parsing import extract_docx_text, extract_pdf_text

    assert extract_docx_text(_docx_with_duplicate_and_table()) == (
        "Question one\nLeft | Right\nLeft\nRight"
    )
    assert extract_pdf_text(_two_page_pdf()) == "Alpha page\n\nBeta page\n"


def test_docx_header_and_footer_parts_keep_zip_member_order() -> None:
    from backend.document_parsing import extract_docx_text

    extracted = extract_docx_text(_docx_with_ordered_header_footer_parts())

    assert extracted.splitlines()[-4:] == [
        "HEADER-2",
        "FOOTER-2",
        "HEADER-1",
        "FOOTER-1",
    ]


def test_plain_parser_preserves_inline_split_and_answer_mapping() -> None:
    from backend.document_parsing import parse_plain_question_blocks

    blocks = parse_plain_question_blocks(
        "\n".join(
            [
                "一、选择题",
                "10. Tenth question.",
                "【答案】A",
                "Continuation. 11. Eleventh question.",
                "【答案】B",
                "12. Twelfth question.",
                "【答案】C",
            ]
        )
    )

    assert [block["question_id"] for block in blocks] == ["Q10", "Q11", "Q12"]
    assert blocks[0]["canonical_answer"] == ""
    assert blocks[1]["canonical_answer"] == "B"
    assert blocks[2]["canonical_answer"] == "C"


def test_plain_parser_trusts_an_explicitly_mapped_bare_single_blank_answer() -> None:
    from backend.document_parsing import parse_plain_question_blocks

    blocks = parse_plain_question_blocks(
        "\n".join(
            [
                "二、填空题",
                "5. 求这个角的度数：____。",
                "参考答案",
                "5. 70°",
            ]
        )
    )

    assert len(blocks) == 1
    assert blocks[0]["question_id"] == "Q5"
    assert blocks[0]["canonical_answer"] == "70"
    assert blocks[0]["local_answer_trusted"] is True
    assert blocks[0]["needs_review"] is False


def test_plain_parser_does_not_flatten_subparted_worked_question_into_fill_blank() -> None:
    from backend.document_parsing import parse_plain_question_blocks

    blocks = parse_plain_question_blocks(
        "\n".join(
            [
                "三、解答题",
                "11. 如图，在△ABC中，∠ACB＝90°，点D在斜边AB上，AD＝AC，"
                "设∠A＝x°，∠BCD＝y°。",
                "（1）填写表格：x 取 20、40、60、80 时，y 分别为 ____。",
                "（2）猜想y与x的数量关系，并说明理由。",
                "（3）在图1的条件下，点E在AB边上，且BE＝BC，求∠DCE的度数。",
            ]
        )
    )

    assert len(blocks) == 1
    assert blocks[0]["question_type"] != "fill_blank"
    assert blocks[0]["question_type"] in {"calculation", "proof", "comprehensive"}


def test_broken_rich_docx_falls_back_without_model_or_files(tmp_path: Path) -> None:
    from backend.document_parsing import parse_docx_question_blocks

    blocks = parse_docx_question_blocks(
        b"not-a-docx",
        fallback_doc_text="1. Local fallback question.",
        temporary_root=tmp_path / "parser",
        asset_root=tmp_path / "assets",
    )

    assert [block["question_id"] for block in blocks] == ["Q1"]
    assert blocks[0]["question_text"] == "Local fallback question."
    assert not (tmp_path / "parser").exists()
    assert not (tmp_path / "assets").exists()


def test_docx_parser_keeps_unheaded_proof_steps_after_short_answer(
    tmp_path: Path,
) -> None:
    from backend.document_parsing import parse_docx_question_blocks

    blocks = parse_docx_question_blocks(
        _docx_with_multiline_proof_answer(),
        temporary_root=tmp_path / "parser",
        asset_root=tmp_path / "assets",
    )

    assert len(blocks) == 1
    complete_answer = "\n".join(
        [
            str(blocks[0].get("answer_text") or ""),
            str(blocks[0].get("analysis") or ""),
        ]
    )
    assert "由 a=b，等式两边同时加 c" in complete_answer
    assert "所以原命题得证" in complete_answer


def test_legacy_imports_delegate_to_the_document_parser() -> None:
    import rubric_auto_cropper
    import session_manager
    from backend.document_parsing import (
        extract_docx_text,
        extract_pdf_text,
        parse_plain_question_blocks,
    )

    assert session_manager.extract_docx_text is extract_docx_text
    assert (
        session_manager.preview_question_blocks_from_docx_text
        is parse_plain_question_blocks
    )
    assert rubric_auto_cropper.extract_pdf_text is extract_pdf_text


def test_production_source_uses_parser_module_and_parser_has_no_model_dependency() -> None:
    source = Path("backend/config_workspace/sources.py").read_text(encoding="utf-8")
    package_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(Path("backend/document_parsing").glob("*.py"))
    )

    assert "backend.document_parsing" in source
    assert "import session_manager" not in source
    assert "from session_manager import preview_question_blocks_from_docx_text" not in source
    assert "llm_client" not in package_sources
    assert "backend.llm" not in package_sources
    assert "LLMClient" not in package_sources
    assert "session_manager" not in package_sources


def test_floating_image_between_adjacent_questions_becomes_manual_candidate() -> None:
    from question_bank.importers.batch_importer import (
        partition_ambiguous_floating_images,
    )

    paragraphs = [
        {"text": "4. 第一题"},
        {
            "text": "[[IMAGE:C:/controlled/floating.png]]",
            "xml": "<w:p><wp:anchor /></w:p>",
        },
        {"text": "5. 第二题"},
        {"text": "[[IMAGE:C:/controlled/inline.png]]", "xml": "<w:p />"},
    ]

    retained, candidates = partition_ambiguous_floating_images(paragraphs)

    assert [item["text"] for item in retained] == [
        "4. 第一题",
        "5. 第二题",
        "[[IMAGE:C:/controlled/inline.png]]",
    ]
    assert candidates == [{
        "path": "C:/controlled/floating.png",
        "previous_question_id": "Q4",
        "next_question_id": "Q5",
        "source_section": "question",
    }]
