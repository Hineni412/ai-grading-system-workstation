from __future__ import annotations

from pathlib import Path

import pytest

from question_bank.exporters.base_exporter import _resolve_image_path
from question_bank.services import preview_service
from question_bank.services.preview_service import _resolve_source_file
from question_bank.services.rich_content_backfill_service import _resolve_backfill_source


def test_preview_resolves_data_relative_source_before_conversion(tmp_path: Path) -> None:
    data_root = tmp_path / "user_data"
    source = data_root / "question_bank" / "raw_papers" / "paper_hash.docx"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"docx")

    resolved = _resolve_source_file(
        {"source_file": "question_bank/raw_papers/paper_hash.docx"},
        data_root=data_root,
    )

    assert resolved == source


def test_exporter_resolves_legacy_absolute_image_after_data_move(tmp_path: Path) -> None:
    data_root = tmp_path / "user_data"
    image = data_root / "question_bank" / "extracted_images" / "set" / "rId1.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"image")

    resolved = _resolve_image_path(
        r"D:\old\user_data\question_bank\extracted_images\set\rId1.png",
        data_root=data_root,
    )

    assert resolved == image


def test_rich_content_backfill_resolves_archived_source(tmp_path: Path) -> None:
    data_root = tmp_path / "user_data"
    source = data_root / "question_bank" / "raw_papers" / "paper_hash.docx"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"docx")

    assert _resolve_backfill_source(
        "question_bank/raw_papers/paper_hash.docx",
        data_root=data_root,
    ) == source


def test_preview_generation_keeps_portable_source_in_preview_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    physical = tmp_path / "user_data" / "question_bank" / "raw_papers" / "paper.docx"
    physical.parent.mkdir(parents=True)
    physical.write_bytes(b"docx")
    stored = "question_bank/raw_papers/paper.docx"
    captured_sources: list[str] = []

    class _Context:
        def __init__(self, value: object) -> None:
            self.value = value

        def __enter__(self) -> object:
            return self.value

        def __exit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(preview_service, "_resolve_source_file", lambda _question: physical)
    monkeypatch.setattr(preview_service, "_preview_pdf", lambda _path: _Context(physical))
    monkeypatch.setattr(preview_service.fitz, "open", lambda _path: _Context(object()))
    monkeypatch.setattr(preview_service, "_extract_lines", lambda _document: [])
    monkeypatch.setattr(
        preview_service,
        "_render_preview",
        lambda *_args, source_file, **_kwargs: captured_sources.append(source_file) or "ready",
    )

    preview_service._generate_one(
        object(),
        {"id": 1, "source_file": stored},
        tmp_path / "previews",
    )

    assert captured_sources == [stored, stored]
