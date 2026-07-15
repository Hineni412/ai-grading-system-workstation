from __future__ import annotations

import asyncio
import io
import json
import re
import zipfile
from collections.abc import AsyncIterator
from pathlib import Path

import fitz
import pytest
from docx import Document
from docx.shared import Inches
from PIL import Image

from backend.config_workspace.sources import (
    ConfigSourceChangedError,
    ConfigSourceInvalidError,
    ConfigSourceNotFoundError,
    ConfigSourceService,
    ConfigSourceTooLargeError,
    ConfigSourceTypeUnsupportedError,
    QuestionDecision,
)


async def chunks(*parts: bytes) -> AsyncIterator[bytes]:
    for part in parts:
        yield part


def service(tmp_path: Path, **kwargs: object) -> ConfigSourceService:
    return ConfigSourceService(tmp_path, **kwargs)


def _png_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (24, 18), "navy").save(output, format="PNG")
    return output.getvalue()


def _docx_bytes(*, question: str = "1. Solve x squared.", with_image: bool = False) -> bytes:
    document = Document()
    paragraph = document.add_paragraph()
    paragraph.add_run(question)
    rich = paragraph.add_run(" rich")
    rich.bold = True
    exponent = paragraph.add_run("2")
    exponent.font.superscript = True
    if with_image:
        paragraph.add_run().add_picture(io.BytesIO(_png_bytes()), width=Inches(0.25))
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def _pdf_bytes(*, question: bool = True, answer: bool = True, pages: int = 1) -> bytes:
    document = fitz.open()
    for index in range(pages):
        page = document.new_page()
        if index == 0:
            lines = ["Synthetic exam"]
            if question:
                lines.extend(["1. Compute 1 + 1.", "A. 1   B. 2   C. 3   D. 4"])
            if answer:
                lines.extend(["Answer", "1. B"])
            page.insert_text((72, 72), "\n".join(lines), fontsize=12)
    payload = document.tobytes()
    document.close()
    return payload


def valid_source_bytes(filename: str) -> bytes:
    return _pdf_bytes() if filename.casefold().endswith(".pdf") else _docx_bytes()


@pytest.mark.parametrize("filename", ["../数学卷.docx", "C:\\private\\数学卷.pdf"])
def test_source_filename_is_reduced_to_basename(tmp_path: Path, filename: str) -> None:
    record = asyncio.run(
        service(tmp_path).stage_and_parse(
            session_id=7,
            filename=filename,
            chunks=chunks(valid_source_bytes(filename)),
        )
    )

    assert record.safe_filename in {"数学卷.docx", "数学卷.pdf"}
    assert "private" not in record.safe_filename
    assert re.fullmatch(r"[0-9a-f]{32}", record.source_id)
    assert re.fullmatch(r"[0-9a-f]{64}", record.source_revision)


def test_stream_overflow_removes_only_new_source_files(tmp_path: Path) -> None:
    source_service = service(tmp_path, max_upload_bytes=4)

    with pytest.raises(ConfigSourceTooLargeError):
        asyncio.run(
            source_service.stage_and_parse(
                session_id=7,
                filename="paper.pdf",
                chunks=chunks(b"123", b"45"),
            )
        )

    assert list((tmp_path / "config_sources").rglob("*.tmp")) == []
    assert list((tmp_path / "config_sources").rglob("manifest.json")) == []


def test_docx_rich_text_and_image_are_kept_in_controlled_source_dir(tmp_path: Path) -> None:
    record = asyncio.run(
        service(tmp_path).stage_and_parse(
            session_id=7,
            filename="rich.docx",
            chunks=chunks(_docx_bytes(with_image=True)),
        )
    )

    assert record.questions
    assert record.questions[0].has_question_asset is True
    assert record.private_blocks[0]["question_html"] != record.questions[0].question_preview
    assert all(
        path.is_relative_to(record.manifest_path.parent)
        for path in record.manifest_path.parent.rglob("*")
        if path.is_file()
    )
    content, media_type = service(tmp_path).read_asset(
        session_id=7,
        source_id=record.source_id,
        question_id=record.questions[0].question_id,
        asset_kind="question",
    )
    assert content == _png_bytes()
    assert media_type == "image/png"


def test_pdf_text_crops_and_whole_pages_reload_from_manifest(tmp_path: Path) -> None:
    created = asyncio.run(
        service(tmp_path).stage_and_parse(
            session_id=7,
            filename="paper.pdf",
            chunks=chunks(_pdf_bytes()),
        )
    )

    reloaded = service(tmp_path).load(session_id=7, source_id=created.source_id)

    assert reloaded.private_document_text
    assert reloaded.private_blocks
    assert reloaded.private_whole_page_images
    assert reloaded.questions[0].has_question_asset is True
    assert reloaded.questions[0].has_answer_asset is True
    assert reloaded.private_question_images["Q1"]["question"]
    assert reloaded.private_question_images["Q1"]["answer"]


def test_public_projection_has_no_paths_text_or_base64(tmp_path: Path) -> None:
    record = asyncio.run(
        service(tmp_path).stage_and_parse(
            session_id=7,
            filename="paper.pdf",
            chunks=chunks(_pdf_bytes()),
        )
    )

    body = record.public_snapshot()
    encoded = json.dumps(body, ensure_ascii=False)

    assert set(body) == {
        "session_id",
        "source_id",
        "source_revision",
        "safe_filename",
        "suffix",
        "size_bytes",
        "sha256_prefix",
        "parse_state",
        "questions",
    }
    assert body["sha256_prefix"] == record.sha256[:12]
    assert "document_text" not in encoded
    assert "private_" not in encoded
    assert "base64" not in encoded.casefold()
    assert str(tmp_path) not in encoded
    assert record.private_document_text not in encoded


def test_public_preview_strips_html_image_markers_and_caps_long_text(tmp_path: Path) -> None:
    long_question = "1. <b>" + ("very long question " * 80) + "</b> [[IMAGE:C:/private/a.png]]"
    record = asyncio.run(
        service(tmp_path).stage_and_parse(
            session_id=7,
            filename="long.docx",
            chunks=chunks(_docx_bytes(question=long_question)),
        )
    )

    preview = record.questions[0].question_preview
    assert len(preview) == 500
    assert "<b>" not in preview
    assert "[[IMAGE:" not in preview
    assert "private" not in preview


def test_no_detected_questions_is_a_reloadable_ready_source(tmp_path: Path) -> None:
    record = asyncio.run(
        service(tmp_path).stage_and_parse(
            session_id=7,
            filename="cover.pdf",
            chunks=chunks(_pdf_bytes(question=False, answer=False)),
        )
    )

    assert record.questions == ()
    assert record.public_snapshot()["parse_state"] == "ready"
    assert service(tmp_path).load(session_id=7, source_id=record.source_id).questions == ()


def test_malformed_docx_and_mismatched_magic_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigSourceInvalidError):
        asyncio.run(
            service(tmp_path).stage_and_parse(
                session_id=7,
                filename="broken.docx",
                chunks=chunks(b"PK\x03\x04not-a-zip"),
            )
        )
    with pytest.raises(ConfigSourceTypeUnsupportedError):
        asyncio.run(
            service(tmp_path).stage_and_parse(
                session_id=7,
                filename="wrong.docx",
                chunks=chunks(_pdf_bytes()),
            )
        )


def test_docx_member_and_expanded_limits_are_checked_before_parser(tmp_path: Path) -> None:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "12345")
        archive.writestr("word/document.xml", "12345")

    with pytest.raises(ConfigSourceTooLargeError):
        asyncio.run(
            service(
                tmp_path,
                max_docx_member_bytes=4,
                max_docx_expanded_bytes=8,
            ).stage_and_parse(
                session_id=7,
                filename="large.docx",
                chunks=chunks(payload.getvalue()),
            )
        )


def test_pdf_page_limit_is_checked_before_render(tmp_path: Path) -> None:
    with pytest.raises(ConfigSourceTooLargeError):
        asyncio.run(
            service(tmp_path).stage_and_parse(
                session_id=7,
                filename="501-pages.pdf",
                chunks=chunks(_pdf_bytes(question=False, answer=False, pages=501)),
            )
        )


def test_source_session_mismatch_and_replaced_active_source_fail_closed(tmp_path: Path) -> None:
    first = asyncio.run(
        service(tmp_path).stage_and_parse(
            session_id=7,
            filename="first.pdf",
            chunks=chunks(_pdf_bytes()),
        )
    )
    with pytest.raises(ConfigSourceNotFoundError):
        service(tmp_path).load(session_id=8, source_id=first.source_id)

    second = asyncio.run(
        service(tmp_path).stage_and_parse(
            session_id=7,
            filename="second.pdf",
            chunks=chunks(_pdf_bytes()),
        )
    )
    assert second.source_id != first.source_id
    with pytest.raises(ConfigSourceChangedError):
        service(tmp_path).load(session_id=7, source_id=first.source_id)
    assert service(tmp_path).load(
        session_id=7,
        source_id=first.source_id,
        require_active=False,
    ).source_revision == first.source_revision


def test_failed_replacement_keeps_previous_active_pointer(tmp_path: Path) -> None:
    current = asyncio.run(
        service(tmp_path).stage_and_parse(
            session_id=7,
            filename="current.pdf",
            chunks=chunks(_pdf_bytes()),
        )
    )

    with pytest.raises(ConfigSourceInvalidError):
        asyncio.run(
            service(tmp_path).stage_and_parse(
                session_id=7,
                filename="bad.pdf",
                chunks=chunks(b"%PDF-not-valid"),
            )
        )

    assert (
        service(tmp_path).load(session_id=7, source_id=current.source_id).source_id
        == current.source_id
    )


def test_active_pointer_swaps_only_after_new_manifest_record_validates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_service = service(tmp_path)
    current = asyncio.run(
        current_service.stage_and_parse(
            session_id=7,
            filename="current.pdf",
            chunks=chunks(_pdf_bytes()),
        )
    )
    replacement_service = service(tmp_path)

    def reject_manifest(*_args: object, **_kwargs: object):
        raise ConfigSourceInvalidError()

    monkeypatch.setattr(replacement_service, "_record_from_manifest", reject_manifest)

    with pytest.raises(ConfigSourceInvalidError):
        asyncio.run(
            replacement_service.stage_and_parse(
                session_id=7,
                filename="replacement.pdf",
                chunks=chunks(_pdf_bytes()),
            )
        )

    assert (
        current_service.load(session_id=7, source_id=current.source_id).source_id
        == current.source_id
    )


def test_parser_failure_after_asset_write_removes_all_new_owned_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import rubric_auto_cropper
    import session_manager

    monkeypatch.setattr(rubric_auto_cropper, "extract_pdf_text", lambda _payload: "text")
    monkeypatch.setattr(
        session_manager,
        "preview_question_blocks_from_docx_text",
        lambda _text: [
            {"question_id": "Q1", "question_type": "choice", "text": "one"},
            {"question_id": "Q2", "question_type": "choice", "text": "two"},
        ],
    )
    monkeypatch.setattr(
        rubric_auto_cropper,
        "extract_pdf_question_images",
        lambda _payload, _blocks: {
            "Q1": {"question": _png_bytes(), "answer": None},
            "Q2": {"question": b"not-an-image", "answer": None},
        },
    )
    monkeypatch.setattr(
        rubric_auto_cropper,
        "extract_pdf_images",
        lambda _payload: [_png_bytes()],
    )

    with pytest.raises(ConfigSourceInvalidError):
        asyncio.run(
            service(tmp_path).stage_and_parse(
                session_id=7,
                filename="partial.pdf",
                chunks=chunks(_pdf_bytes()),
            )
        )

    source_root = tmp_path / "config_sources" / "session-7"
    assert [path for path in source_root.rglob("*") if path.is_file()] == []


def test_cleanup_retains_referenced_old_source_and_removes_exact_owned_files_only(
    tmp_path: Path,
) -> None:
    source_service = service(tmp_path)
    first = asyncio.run(
        source_service.stage_and_parse(
            session_id=7,
            filename="first.pdf",
            chunks=chunks(_pdf_bytes()),
        )
    )
    asyncio.run(
        source_service.stage_and_parse(
            session_id=7,
            filename="second.pdf",
            chunks=chunks(_pdf_bytes()),
        )
    )
    sentinel = first.manifest_path.parent / "not-owned.txt"
    sentinel.write_text("keep", encoding="utf-8")

    assert source_service.cleanup_inactive(
        session_id=7,
        referenced_source_ids={first.source_id},
    ) == ()
    assert first.manifest_path.exists()

    removed = source_service.cleanup_inactive(session_id=7, referenced_source_ids=set())
    assert removed == (first.source_id,)
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert first.manifest_path.parent.is_dir()
    assert not first.manifest_path.exists()


def test_teacher_decisions_filter_and_override_only_known_questions(tmp_path: Path) -> None:
    record = asyncio.run(
        service(tmp_path).stage_and_parse(
            session_id=7,
            filename="paper.pdf",
            chunks=chunks(_pdf_bytes()),
        )
    )

    prepared = service(tmp_path).apply_teacher_decisions(
        record,
        [QuestionDecision(question_id="Q1", question_type="proof", excluded=False)],
    )

    assert prepared.confirmed_blocks[0]["question_type"] == "proof"
    assert prepared.confirmed_blocks[0]["question_type_confirmed"] is True
    assert prepared.document_text == record.private_document_text
    assert prepared.question_images["Q1"]["question"]
    with pytest.raises(ValueError, match="unknown question"):
        service(tmp_path).apply_teacher_decisions(
            record,
            [QuestionDecision(question_id="Q404", question_type="choice", excluded=True)],
        )
