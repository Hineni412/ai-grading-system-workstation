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
