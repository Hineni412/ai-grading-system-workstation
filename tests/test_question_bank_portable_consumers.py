from __future__ import annotations

from pathlib import Path

from question_bank.exporters.base_exporter import _resolve_image_path
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
