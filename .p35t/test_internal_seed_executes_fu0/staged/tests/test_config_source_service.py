from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import io
import json
import os
import re
import subprocess
import threading
import zipfile
from collections.abc import AsyncIterator
from pathlib import Path

import fitz
import pytest
from docx import Document
from docx.shared import Inches
from PIL import Image

from backend.config_workspace.sources import (
    ConfigAssetNotFoundError,
    ConfigSourceChangedError,
    ConfigSourceInvalidError,
    ConfigSourceNotFoundError,
    ConfigSourceService,
    ConfigSourceTooLargeError,
    ConfigSourceTypeUnsupportedError,
    QuestionDecision,
)
from backend.config_workspace.secure_fs import (
    SecureFilesystemError,
    SecureRootFilesystem,
)


async def chunks(*parts: bytes) -> AsyncIterator[bytes]:
    for part in parts:
        yield part


def service(tmp_path: Path, **kwargs: object) -> ConfigSourceService:
    return ConfigSourceService(tmp_path, **kwargs)


@pytest.mark.skipif(os.name != "nt", reason="Windows service root junction regression")
@pytest.mark.parametrize("junction_location", ["root", "parent"])
def test_service_and_api_dependency_do_not_resolve_away_root_junction(
    tmp_path: Path,
    junction_location: str,
) -> None:
    from backend.api.dependencies import get_config_source_service

    target_parent = tmp_path / "target-parent"
    target_parent.mkdir()
    if junction_location == "root":
        junction = tmp_path / "root-junction"
        requested = junction
        target = target_parent
    else:
        target = target_parent
        (target / "upload-root").mkdir()
        junction = tmp_path / "parent-junction"
        requested = junction / "upload-root"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip("junction creation is unavailable")
    try:
        with pytest.raises(SecureFilesystemError):
            SecureRootFilesystem(requested)
        with pytest.raises(SecureFilesystemError) as service_error:
            ConfigSourceService(requested)
        with pytest.raises(SecureFilesystemError) as dependency_error:
            get_config_source_service(requested)
    finally:
        os.rmdir(junction)

    assert str(requested) not in str(service_error.value)
    assert str(requested) not in str(dependency_error.value)


def _png_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (24, 18), "navy").save(output, format="PNG")
    return output.getvalue()


def _alternate_png_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (24, 18), "crimson").save(output, format="PNG")
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


def _docx_with_multiline_proof_answer() -> bytes:
    document = Document()
    document.add_paragraph("1. 证明：若 a=b，则 a+c=b+c。")
    document.add_paragraph("参考答案")
    document.add_paragraph("1. 【答案】结论成立")
    document.add_paragraph("【解答】结论成立")
    document.add_paragraph("由 a=b，等式两边同时加 c，得到 a+c=b+c。")
    document.add_paragraph("所以原命题得证。")
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


def test_cancelled_upload_propagates_and_removes_owned_temporary_file(
    tmp_path: Path,
) -> None:
    async def cancelled_chunks() -> AsyncIterator[bytes]:
        yield b"%PDF-1.7"
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            service(tmp_path).stage_and_parse(
                session_id=7,
                filename="cancelled.pdf",
                chunks=cancelled_chunks(),
            )
        )

    source_root = tmp_path / "config_sources" / "session-7"
    assert [path for path in source_root.rglob("*") if path.is_file()] == []


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


def test_docx_public_preview_keeps_short_answer_and_complete_solution(
    tmp_path: Path,
) -> None:
    record = asyncio.run(
        service(tmp_path).stage_and_parse(
            session_id=7,
            filename="proof.docx",
            chunks=chunks(_docx_with_multiline_proof_answer()),
        )
    )

    question = record.public_snapshot()["questions"][0]
    complete_answer = "\n".join(
        str(block.get("text") or "")
        for block in question["rich_content"]["answer_blocks"]
    )
    assert question["answer_preview"] == "结论成立"
    assert complete_answer.splitlines().count("结论成立") == 1
    assert "由 a=b，等式两边同时加 c" in complete_answer
    assert "所以原命题得证" in complete_answer


def test_pdf_public_preview_and_generation_keep_ocr_text_internal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import rubric_auto_cropper
    import backend.config_workspace.sources as sources_module

    monkeypatch.setattr(
        sources_module,
        "extract_pdf_text",
        lambda _payload: "CORRUPTED OCR POSITIONING TEXT",
    )
    monkeypatch.setattr(
        sources_module,
        "parse_plain_question_blocks",
        lambda _text: [
            {
                "question_id": "Q1",
                "question_type": "proof",
                "text": "CORRUPTED QUESTION OCR",
                "question_text": "CORRUPTED QUESTION OCR",
                "answer_text": "CORRUPTED ANSWER OCR",
                "analysis": "CORRUPTED ANALYSIS OCR",
                "canonical_answer": "CORRUPTED CANONICAL OCR",
                "local_answer_trusted": True,
                "needs_review": False,
            }
        ],
    )
    monkeypatch.setattr(
        rubric_auto_cropper,
        "extract_pdf_question_images",
        lambda _payload, _blocks: {
            "Q1": {"question": _png_bytes(), "answer": _alternate_png_bytes()}
        },
    )
    monkeypatch.setattr(
        rubric_auto_cropper,
        "extract_pdf_images",
        lambda _payload: [_png_bytes()],
    )

    source_service = service(tmp_path)
    record = asyncio.run(
        source_service.stage_and_parse(
            session_id=7,
            filename="paper.pdf",
            chunks=chunks(_pdf_bytes()),
        )
    )

    question = record.public_snapshot()["questions"][0]
    public_text = "\n".join(
        str(block.get("text") or "")
        for key in ("question_blocks", "answer_blocks")
        for block in question["rich_content"][key]
    )
    prepared = source_service.apply_teacher_decisions(record, [])

    assert question["question_preview"] == ""
    assert question["answer_preview"] == ""
    assert "CORRUPTED" not in public_text
    assert question["rich_content"]["question_blocks"][0]["asset_urls"]
    assert question["rich_content"]["answer_blocks"][0]["asset_urls"]
    assert record.private_blocks[0]["question_text"] == "CORRUPTED QUESTION OCR"
    assert prepared.confirmed_blocks[0]["semantic_source"] == "images"
    assert prepared.confirmed_blocks[0]["question_text"] == "CORRUPTED QUESTION OCR"
    assert prepared.question_images["Q1"]["question"]
    assert prepared.question_images["Q1"]["answer"]


def test_pdf_missing_crop_stays_image_semantic_without_text_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import rubric_auto_cropper
    import backend.config_workspace.sources as sources_module

    monkeypatch.setattr(sources_module, "extract_pdf_text", lambda _payload: "1. OCR only")
    monkeypatch.setattr(
        sources_module,
        "parse_plain_question_blocks",
        lambda _text: [
            {
                "question_id": "Q1",
                "question_type": "choice",
                "text": "OCR QUESTION MUST NOT BECOME A FALLBACK",
                "answer_text": "OCR ANSWER MUST NOT BECOME A FALLBACK",
                "local_answer_trusted": True,
                "needs_review": False,
            }
        ],
    )
    monkeypatch.setattr(
        rubric_auto_cropper,
        "extract_pdf_question_images",
        lambda _payload, _blocks: {},
    )
    monkeypatch.setattr(
        rubric_auto_cropper,
        "extract_pdf_images",
        lambda _payload: [_png_bytes()],
    )

    source_service = service(tmp_path)
    record = asyncio.run(
        source_service.stage_and_parse(
            session_id=7,
            filename="missing-crop.pdf",
            chunks=chunks(_pdf_bytes()),
        )
    )

    question = record.public_snapshot()["questions"][0]
    prepared = source_service.apply_teacher_decisions(record, [])

    assert question["question_preview"] == ""
    assert question["answer_preview"] == ""
    assert question["answer_present"] is False
    assert question["rich_content"]["question_blocks"] == []
    assert question["rich_content"]["answer_blocks"] == []
    assert prepared.confirmed_blocks[0]["semantic_source"] == "images"
    assert prepared.question_images == {}


def test_docx_public_preview_and_generation_keep_text_semantics(
    tmp_path: Path,
) -> None:
    record = asyncio.run(
        service(tmp_path).stage_and_parse(
            session_id=7,
            filename="proof.docx",
            chunks=chunks(_docx_with_multiline_proof_answer()),
        )
    )

    question = record.public_snapshot()["questions"][0]
    prepared = service(tmp_path).apply_teacher_decisions(record, [])

    assert "若 a=b" in question["question_preview"]
    assert question["answer_preview"] == "结论成立"
    assert prepared.confirmed_blocks[0].get("semantic_source") != "images"
    assert "若 a=b" in prepared.confirmed_blocks[0]["question_text"]


def test_controlled_docx_parser_does_not_use_path_scratch_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_mkdir = Path.mkdir
    original_write_bytes = Path.write_bytes
    original_unlink = Path.unlink
    violations: list[tuple[str, Path]] = []

    def is_parser_scratch(path: Path) -> bool:
        return path.name.startswith("_rich_split_") or (
            path.name == "assets" and path.parent.name == "p"
        )

    def guarded_mkdir(path: Path, *args: object, **kwargs: object) -> None:
        if is_parser_scratch(path):
            violations.append(("mkdir", path))
            raise AssertionError("ordinary parser mkdir is forbidden")
        original_mkdir(path, *args, **kwargs)

    def guarded_write_bytes(path: Path, data: bytes) -> int:
        if is_parser_scratch(path) or path.name.startswith("rId"):
            violations.append(("write_bytes", path))
            raise AssertionError("ordinary parser write is forbidden")
        return original_write_bytes(path, data)

    def guarded_unlink(path: Path, *args: object, **kwargs: object) -> None:
        if is_parser_scratch(path):
            violations.append(("unlink", path))
            raise AssertionError("ordinary parser unlink is forbidden")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", guarded_mkdir)
    monkeypatch.setattr(Path, "write_bytes", guarded_write_bytes)
    monkeypatch.setattr(Path, "unlink", guarded_unlink)

    record = asyncio.run(
        service(tmp_path).stage_and_parse(
            session_id=7,
            filename="memory.docx",
            chunks=chunks(_docx_bytes(with_image=True)),
        )
    )

    assert record.questions[0].has_question_asset is True
    assert violations == []


def test_concurrent_docx_parse_does_not_redirect_an_unrelated_caller(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import session_manager
    from question_bank.importers import docx_importer

    independent_temporary_root = tmp_path / "independent-temporary"
    independent_asset_root = tmp_path / "independent-assets"
    monkeypatch.setattr(
        session_manager,
        "_resolve_upload_config_dir",
        lambda: str(independent_temporary_root),
    )
    monkeypatch.setattr(
        docx_importer,
        "project_data_root",
        lambda: independent_asset_root,
    )
    original_import = docx_importer.import_docx
    roles = threading.local()
    service_ready = threading.Event()
    independent_ready = threading.Event()
    independent_finished = threading.Event()

    def synchronized_import(source_file: str | Path, **kwargs: object):
        if getattr(roles, "value", "") == "independent":
            independent_ready.set()
            assert service_ready.wait(timeout=10)
            try:
                return original_import(source_file, **kwargs)
            finally:
                independent_finished.set()
        service_ready.set()
        assert independent_ready.wait(timeout=10)
        assert independent_finished.wait(timeout=10)
        return original_import(source_file, **kwargs)

    monkeypatch.setattr(docx_importer, "import_docx", synchronized_import)
    payload = _docx_bytes(with_image=True)

    def parse_service_source():
        roles.value = "service"
        return asyncio.run(
            service(tmp_path / "service-root").stage_and_parse(
                session_id=7,
                filename="service.docx",
                chunks=chunks(payload),
            )
        )

    def parse_independent_source():
        roles.value = "independent"
        return session_manager.preview_question_blocks_from_docx_bytes(
            payload,
            fallback_doc_text="1. fallback",
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        service_future = executor.submit(parse_service_source)
        independent_future = executor.submit(parse_independent_source)
        record = service_future.result(timeout=20)
        independent_blocks = independent_future.result(timeout=20)

    independent_paths = [
        Path(path)
        for block in independent_blocks
        for path in block.get("image_paths", [])
    ]
    expected_root = independent_asset_root / "question_bank" / "extracted_images"
    assert independent_paths
    assert all(path.is_relative_to(expected_root) for path in independent_paths)
    assert record.questions[0].has_question_asset is True


@pytest.mark.skipif(os.name != "nt", reason="Windows DOCX junction race regression")
def test_docx_parser_junction_swap_cannot_write_outside_controlled_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_service = service(tmp_path)
    outside = tmp_path.parent / f"{tmp_path.name}-docx-outside"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")
    parked: Path | None = None
    junction: Path | None = None
    swapped = False
    original_checkpoint = source_service._files._before_handle_use

    def swap_parser_root(operation: str, path: Path) -> None:
        nonlocal parked, junction, swapped
        original_checkpoint(operation, path)
        if swapped or operation != "mkdir" or path.name != "assets":
            return
        junction = path.parent
        parked = junction.with_name("p-parked")
        junction.rename(parked)
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            parked.rename(junction)
            pytest.skip("junction creation is unavailable")
        swapped = True

    monkeypatch.setattr(source_service._files, "_before_handle_use", swap_parser_root)
    record = None
    failed_safely = False
    try:
        try:
            record = asyncio.run(
                source_service.stage_and_parse(
                    session_id=7,
                    filename="race.docx",
                    chunks=chunks(_docx_bytes(with_image=True)),
                )
            )
        except ConfigSourceInvalidError:
            failed_safely = True
    finally:
        if swapped and junction is not None and parked is not None:
            os.rmdir(junction)
            parked.rename(junction)
        outside_files = sorted(path.name for path in outside.iterdir())
        outside_content = sentinel.read_text(encoding="utf-8")
        for path in outside.iterdir():
            path.unlink()
        outside.rmdir()

    assert swapped is True
    assert failed_safely or (
        record is not None and record.questions[0].has_question_asset is True
    )
    assert outside_files == ["sentinel.txt"]
    assert outside_content == "keep"


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


def test_source_parse_runs_off_event_loop(tmp_path: Path, monkeypatch) -> None:
    source_service = service(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    original = source_service._parse_pdf

    def blocking_parse(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=5)
        return original(*args, **kwargs)

    monkeypatch.setattr(source_service, "_parse_pdf", blocking_parse)

    async def exercise() -> None:
        task = asyncio.create_task(
            source_service.stage_and_parse(
                session_id=7,
                filename="paper.pdf",
                chunks=chunks(_pdf_bytes()),
            )
        )
        assert await asyncio.to_thread(entered.wait, 5)
        await asyncio.sleep(0)
        release.set()
        await task

    asyncio.run(exercise())


def test_derived_asset_budget_fails_closed_and_cleans_owned_files(
    tmp_path: Path,
) -> None:
    source_service = service(tmp_path, max_image_bytes=8)

    with pytest.raises(ConfigSourceInvalidError):
        asyncio.run(
            source_service.stage_and_parse(
                session_id=7,
                filename="paper.pdf",
                chunks=chunks(_pdf_bytes()),
            )
        )

    assert list(tmp_path.rglob("manifest.json")) == []
    assert [path for path in tmp_path.rglob("*") if path.is_file()] == []


def test_manifest_load_enforces_serialized_size_limit(tmp_path: Path) -> None:
    record = asyncio.run(
        service(tmp_path).stage_and_parse(
            session_id=7,
            filename="paper.pdf",
            chunks=chunks(_pdf_bytes()),
        )
    )
    with pytest.raises(ConfigSourceInvalidError):
        service(tmp_path, max_manifest_bytes=8).load(
            session_id=7,
            source_id=record.source_id,
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


@pytest.mark.parametrize("tamper_target", ["source", "asset"])
def test_active_pointer_final_integrity_check_rejects_post_parse_tampering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tamper_target: str,
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
    original_record = replacement_service._record_from_manifest

    def record_then_tamper(manifest_path: Path, manifest: dict[str, object]):
        record = original_record(manifest_path, manifest)
        if tamper_target == "source":
            record.private_source_path.write_bytes(
                record.private_source_path.read_bytes() + b"tampered"
            )
        else:
            raw_assets = manifest["asset_files"]
            assert isinstance(raw_assets, dict)
            question_assets = raw_assets["Q1"]
            assert isinstance(question_assets, dict)
            asset_name = question_assets["question"]
            assert isinstance(asset_name, str)
            (manifest_path.parent / asset_name).write_bytes(_alternate_png_bytes())
        return record

    monkeypatch.setattr(
        replacement_service,
        "_record_from_manifest",
        record_then_tamper,
    )

    with pytest.raises(ConfigSourceInvalidError):
        asyncio.run(
            replacement_service.stage_and_parse(
                session_id=7,
                filename="replacement.pdf",
                chunks=chunks(_pdf_bytes()),
            )
        )

    assert current_service.load(
        session_id=7,
        source_id=current.source_id,
    ).source_id == current.source_id
    manifests = list((tmp_path / "config_sources" / "session-7").rglob("manifest.json"))
    assert manifests == [current.manifest_path]


def test_targeted_asset_read_rejects_valid_bytes_outside_pinned_inventory(
    tmp_path: Path,
) -> None:
    source_service = service(tmp_path)
    record = asyncio.run(
        source_service.stage_and_parse(
            session_id=7,
            filename="paper.pdf",
            chunks=chunks(_pdf_bytes()),
        )
    )
    manifest = json.loads(record.manifest_path.read_text(encoding="utf-8"))
    filename = manifest["asset_files"]["Q1"]["question"]
    (record.manifest_path.parent / filename).write_bytes(_alternate_png_bytes())

    with pytest.raises(ConfigAssetNotFoundError):
        source_service.read_asset(
            session_id=7,
            source_id=record.source_id,
            question_id="Q1",
            asset_kind="question",
        )


def test_private_record_load_rejects_source_bytes_outside_pinned_inventory(
    tmp_path: Path,
) -> None:
    source_service = service(tmp_path)
    record = asyncio.run(
        source_service.stage_and_parse(
            session_id=7,
            filename="paper.pdf",
            chunks=chunks(_pdf_bytes()),
        )
    )
    record.private_source_path.write_bytes(record.private_source_path.read_bytes() + b"x")

    with pytest.raises(ConfigSourceInvalidError):
        source_service.load(session_id=7, source_id=record.source_id)


def test_parser_failure_after_asset_write_removes_all_new_owned_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import rubric_auto_cropper
    import backend.config_workspace.sources as sources_module

    monkeypatch.setattr(
        sources_module,
        "extract_pdf_text",
        lambda _payload: "text",
    )
    monkeypatch.setattr(
        sources_module,
        "parse_plain_question_blocks",
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
    original_write = sources_module._write_bytes_atomic
    sentinel_path: Path | None = None

    def write_with_unowned_sentinel(path: Path, content: bytes, *args: object, **kwargs: object) -> None:
        nonlocal sentinel_path
        original_write(path, content, *args, **kwargs)
        if sentinel_path is None:
            sentinel_path = path.parent / "not-owned.txt"
            sentinel_path.write_text("keep", encoding="utf-8")

    monkeypatch.setattr(sources_module, "_write_bytes_atomic", write_with_unowned_sentinel)

    with pytest.raises(ConfigSourceInvalidError):
        asyncio.run(
            service(tmp_path).stage_and_parse(
                session_id=7,
                filename="partial.pdf",
                chunks=chunks(_pdf_bytes()),
            )
        )

    source_root = tmp_path / "config_sources" / "session-7"
    assert sentinel_path is not None
    assert sentinel_path.read_text(encoding="utf-8") == "keep"
    assert [
        path
        for path in source_root.rglob("*")
        if path.is_file() and path != sentinel_path
    ] == []


@pytest.mark.parametrize("active_state", ["missing", "corrupt"])
def test_cleanup_fails_closed_when_active_pointer_is_unavailable(
    tmp_path: Path,
    active_state: str,
) -> None:
    source_service = service(tmp_path)
    first = asyncio.run(
        source_service.stage_and_parse(
            session_id=7,
            filename="first.pdf",
            chunks=chunks(_pdf_bytes()),
        )
    )
    second = asyncio.run(
        source_service.stage_and_parse(
            session_id=7,
            filename="second.pdf",
            chunks=chunks(_pdf_bytes()),
        )
    )
    active_path = tmp_path / "config_sources" / "session-7" / "active.json"
    if active_state == "missing":
        active_path.unlink()
    else:
        active_path.write_text("not-json", encoding="utf-8")

    assert source_service.cleanup_inactive(
        session_id=7,
        referenced_source_ids=set(),
    ) == ()
    assert first.manifest_path.exists()
    assert second.manifest_path.exists()


def test_cleanup_fails_closed_when_active_image_is_corrupt(tmp_path: Path) -> None:
    source_service = service(tmp_path)
    first = asyncio.run(
        source_service.stage_and_parse(
            session_id=7,
            filename="first.pdf",
            chunks=chunks(_pdf_bytes()),
        )
    )
    active = asyncio.run(
        source_service.stage_and_parse(
            session_id=7,
            filename="active.pdf",
            chunks=chunks(_pdf_bytes()),
        )
    )
    manifest = json.loads(active.manifest_path.read_text(encoding="utf-8"))
    active_asset = active.manifest_path.parent / manifest["asset_files"]["Q1"]["question"]
    active_asset.write_bytes(b"truncated-image")

    assert source_service.cleanup_inactive(
        session_id=7,
        referenced_source_ids=set(),
    ) == ()
    assert first.manifest_path.exists()
    assert active.manifest_path.exists()


@pytest.mark.parametrize(
    "tamper_kind",
    [
        "missing_inventory",
        "valid_image_replacement",
        "private_block",
        "asset_map",
        "whole_page",
        "orphan_owned",
    ],
)
def test_cleanup_fails_closed_when_active_complete_identity_is_inconsistent(
    tmp_path: Path,
    tamper_kind: str,
) -> None:
    source_service = service(tmp_path)
    first = asyncio.run(
        source_service.stage_and_parse(
            session_id=7,
            filename="first.pdf",
            chunks=chunks(_pdf_bytes()),
        )
    )
    active = asyncio.run(
        source_service.stage_and_parse(
            session_id=7,
            filename="active.pdf",
            chunks=chunks(_pdf_bytes()),
        )
    )
    manifest = json.loads(active.manifest_path.read_text(encoding="utf-8"))
    inventory = manifest.get("file_inventory")
    assert isinstance(inventory, dict), "controlled manifest needs complete inventory"
    question_asset = manifest["asset_files"]["Q1"]["question"]
    assert isinstance(question_asset, str)

    if tamper_kind == "missing_inventory":
        inventory.pop(question_asset)
    elif tamper_kind == "valid_image_replacement":
        (active.manifest_path.parent / question_asset).write_bytes(
            _alternate_png_bytes()
        )
    elif tamper_kind == "private_block":
        manifest["private_blocks"][0]["question_html"] = "tampered semantics"
    elif tamper_kind == "asset_map":
        manifest["asset_files"]["Q1"]["question"] = manifest["asset_files"]["Q1"][
            "answer"
        ]
    elif tamper_kind == "whole_page":
        manifest["whole_page_files"] = []
    else:
        orphan_name = "orphan.png"
        orphan_content = _alternate_png_bytes()
        (active.manifest_path.parent / orphan_name).write_bytes(orphan_content)
        manifest["owned_files"].append(orphan_name)
        inventory[orphan_name] = {
            "role": "whole_page",
            "size_bytes": len(orphan_content),
            "sha256": hashlib.sha256(orphan_content).hexdigest(),
        }

    if tamper_kind != "valid_image_replacement":
        active.manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False),
            encoding="utf-8",
        )

    assert source_service.cleanup_inactive(
        session_id=7,
        referenced_source_ids=set(),
    ) == ()
    assert first.manifest_path.exists()
    assert active.manifest_path.exists()


def test_cleanup_does_not_trust_inactive_owned_files_without_complete_identity(
    tmp_path: Path,
) -> None:
    source_service = service(tmp_path)
    inactive = asyncio.run(
        source_service.stage_and_parse(
            session_id=7,
            filename="inactive.pdf",
            chunks=chunks(_pdf_bytes()),
        )
    )
    active = asyncio.run(
        source_service.stage_and_parse(
            session_id=7,
            filename="active.pdf",
            chunks=chunks(_pdf_bytes()),
        )
    )
    manifest = json.loads(inactive.manifest_path.read_text(encoding="utf-8"))
    orphan = inactive.manifest_path.parent / "not-semantically-owned.png"
    orphan.write_bytes(_png_bytes())
    manifest["owned_files"].append(orphan.name)
    manifest["file_inventory"][orphan.name] = {
        "role": "whole_page",
        "page_index": 999,
        "size_bytes": orphan.stat().st_size,
        "sha256": hashlib.sha256(orphan.read_bytes()).hexdigest(),
    }
    inactive.manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    assert source_service.cleanup_inactive(
        session_id=7,
        referenced_source_ids=set(),
    ) == ()
    assert inactive.manifest_path.exists()
    assert orphan.exists()
    assert active.manifest_path.exists()


@pytest.mark.parametrize("corruption", ["source_bytes", "sha256", "revision"])
def test_cleanup_fails_closed_when_active_source_identity_is_inconsistent(
    tmp_path: Path,
    corruption: str,
) -> None:
    source_service = service(tmp_path)
    first = asyncio.run(
        source_service.stage_and_parse(
            session_id=7,
            filename="first.pdf",
            chunks=chunks(_pdf_bytes()),
        )
    )
    active = asyncio.run(
        source_service.stage_and_parse(
            session_id=7,
            filename="active.pdf",
            chunks=chunks(_pdf_bytes()),
        )
    )
    manifest = json.loads(active.manifest_path.read_text(encoding="utf-8"))
    active_path = tmp_path / "config_sources" / "session-7" / "active.json"
    if corruption == "source_bytes":
        active.private_source_path.write_bytes(active.private_source_path.read_bytes() + b"x")
    elif corruption == "sha256":
        manifest["sha256"] = hashlib.sha256(b"different-source").hexdigest()
        active.manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False),
            encoding="utf-8",
        )
    else:
        mismatched_revision = "f" * 64
        if mismatched_revision == active.source_revision:
            mismatched_revision = "e" * 64
        manifest["source_revision"] = mismatched_revision
        active.manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False),
            encoding="utf-8",
        )
        active_path.write_text(
            json.dumps(
                {
                    "source_id": active.source_id,
                    "source_revision": mismatched_revision,
                }
            ),
            encoding="utf-8",
        )

    assert source_service.cleanup_inactive(
        session_id=7,
        referenced_source_ids=set(),
    ) == ()
    assert first.manifest_path.exists()
    assert active.manifest_path.exists()


def test_cleanup_with_integral_active_source_removes_old_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import backend.config_workspace.sources as sources_module

    source_service = service(tmp_path)
    first = asyncio.run(
        source_service.stage_and_parse(
            session_id=7,
            filename="first.pdf",
            chunks=chunks(_pdf_bytes()),
        )
    )
    active = asyncio.run(
        source_service.stage_and_parse(
            session_id=7,
            filename="active.pdf",
            chunks=chunks(_pdf_bytes()),
        )
    )

    def reject_base64(_content: bytes) -> bytes:
        raise AssertionError("cleanup integrity validation encoded an image")

    monkeypatch.setattr(sources_module.base64, "b64encode", reject_base64)

    assert source_service.cleanup_inactive(
        session_id=7,
        referenced_source_ids=set(),
    ) == (first.source_id,)
    assert not first.manifest_path.exists()
    assert active.manifest_path.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows junction regression")
def test_load_rejects_session_ancestor_junction(tmp_path: Path) -> None:
    source_service = service(tmp_path)
    record = asyncio.run(
        source_service.stage_and_parse(
            session_id=7,
            filename="paper.pdf",
            chunks=chunks(_pdf_bytes()),
        )
    )
    session_dir = tmp_path / "config_sources" / "session-7"
    moved_session_dir = tmp_path / "junction-target"
    session_dir.rename(moved_session_dir)
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(session_dir), str(moved_session_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        moved_session_dir.rename(session_dir)
        pytest.skip("junction creation is unavailable")
    try:
        with pytest.raises(ConfigSourceInvalidError):
            source_service.load(session_id=7, source_id=record.source_id)
    finally:
        os.rmdir(session_dir)


@pytest.mark.skipif(os.name != "nt", reason="Windows junction race regression")
def test_asset_read_rejects_session_swapped_to_junction_after_static_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_service = service(tmp_path)
    record = asyncio.run(
        source_service.stage_and_parse(
            session_id=7,
            filename="paper.pdf",
            chunks=chunks(_pdf_bytes()),
        )
    )
    manifest = json.loads(record.manifest_path.read_text(encoding="utf-8"))
    target_name = manifest["asset_files"]["Q1"]["question"]
    session_dir = tmp_path / "config_sources" / "session-7"
    parked_session = tmp_path / "parked-read-session"
    outside_session = tmp_path.parent / f"{tmp_path.name}-outside-read-session"
    outside_source = outside_session / record.source_id
    outside_source.mkdir(parents=True)
    (outside_source / target_name).write_bytes(_png_bytes())
    sentinel = outside_source / "external-sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")
    original_load = source_service._load_metadata
    original_owned_path = source_service._owned_path
    state = {"armed": False, "swapped": False}

    def load_then_arm(*args: object, **kwargs: object):
        metadata = original_load(*args, **kwargs)
        state["armed"] = True
        return metadata

    def owned_path_then_swap(source_dir: Path, name: object) -> Path:
        path = original_owned_path(source_dir, name)
        if state["armed"] and not state["swapped"] and name == target_name:
            session_dir.rename(parked_session)
            result = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(session_dir), str(outside_session)],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                parked_session.rename(session_dir)
                pytest.skip("junction creation is unavailable")
            state["swapped"] = True
        return path

    monkeypatch.setattr(source_service, "_load_metadata", load_then_arm)
    monkeypatch.setattr(source_service, "_owned_path", owned_path_then_swap)
    sentinel_content: str | None = None
    try:
        with pytest.raises(ConfigAssetNotFoundError):
            source_service.read_asset(
                session_id=7,
                source_id=record.source_id,
                question_id="Q1",
                asset_kind="question",
            )
    finally:
        if state["swapped"]:
            os.rmdir(session_dir)
            parked_session.rename(session_dir)
        if sentinel.exists():
            sentinel_content = sentinel.read_text(encoding="utf-8")
        for path in outside_source.iterdir():
            path.unlink()
        outside_source.rmdir()
        outside_session.rmdir()
    assert sentinel_content == "keep"


@pytest.mark.skipif(os.name != "nt", reason="Windows junction race regression")
def test_cleanup_rejects_session_swapped_to_junction_after_owned_path_checks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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
            filename="active.pdf",
            chunks=chunks(_pdf_bytes()),
        )
    )
    first_manifest = json.loads(first.manifest_path.read_text(encoding="utf-8"))
    session_dir = tmp_path / "config_sources" / "session-7"
    parked_session = tmp_path / "parked-cleanup-session"
    outside_session = tmp_path.parent / f"{tmp_path.name}-outside-cleanup-session"
    outside_source = outside_session / first.source_id
    outside_source.mkdir(parents=True)
    for name in first_manifest["owned_files"]:
        (outside_source / name).write_bytes(b"external")
    sentinel = outside_source / "manifest.json"
    original_owned_path = source_service._owned_path
    state = {"swapped": False}

    def owned_path_then_swap(source_dir: Path, name: object) -> Path:
        path = original_owned_path(source_dir, name)
        if (
            not state["swapped"]
            and source_dir.name == first.source_id
            and name == "manifest.json"
        ):
            session_dir.rename(parked_session)
            result = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(session_dir), str(outside_session)],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                parked_session.rename(session_dir)
                pytest.skip("junction creation is unavailable")
            state["swapped"] = True
        return path

    monkeypatch.setattr(source_service, "_owned_path", owned_path_then_swap)
    sentinel_content: bytes | None = None
    try:
        assert source_service.cleanup_inactive(
            session_id=7,
            referenced_source_ids=set(),
        ) == ()
    finally:
        if state["swapped"]:
            os.rmdir(session_dir)
            parked_session.rename(session_dir)
        if sentinel.exists():
            sentinel_content = sentinel.read_bytes()
        for path in outside_source.iterdir():
            path.unlink()
        outside_source.rmdir()
        outside_session.rmdir()
    assert sentinel_content == b"external"


def test_single_asset_read_reads_only_the_requested_asset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_service = service(tmp_path)
    record = asyncio.run(
        source_service.stage_and_parse(
            session_id=7,
            filename="paper.pdf",
            chunks=chunks(_pdf_bytes()),
        )
    )
    manifest = json.loads(record.manifest_path.read_text(encoding="utf-8"))
    target_name = manifest["asset_files"]["Q1"]["question"]
    original_read_bytes = source_service._files.read_bytes
    reads: list[str] = []

    def tracked_read_bytes(path: Path, **kwargs: object) -> bytes:
        if path.name.startswith(("asset-", "whole-page-")):
            reads.append(path.name)
        return original_read_bytes(path, **kwargs)

    monkeypatch.setattr(source_service._files, "read_bytes", tracked_read_bytes)

    content, media_type = source_service.read_asset(
        session_id=7,
        source_id=record.source_id,
        question_id="Q1",
        asset_kind="question",
    )

    assert content
    assert media_type.startswith("image/")
    assert reads == [target_name]


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
