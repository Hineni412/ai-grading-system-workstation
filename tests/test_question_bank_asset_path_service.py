from __future__ import annotations

from pathlib import Path

import pytest

from question_bank.services.asset_path_service import (
    AmbiguousQuestionBankAssetPathError,
    resolve_question_bank_asset_path,
)


def test_resolves_data_relative_question_bank_path(tmp_path: Path) -> None:
    data_root = tmp_path / "user_data"
    archived = data_root / "question_bank" / "raw_papers" / "paper_hash.docx"
    archived.parent.mkdir(parents=True)
    archived.write_bytes(b"paper")

    assert (
        resolve_question_bank_asset_path(
            "question_bank/raw_papers/paper_hash.docx",
            data_root=data_root,
        )
        == archived
    )


def test_rebases_legacy_absolute_question_image_path(tmp_path: Path) -> None:
    data_root = tmp_path / "user_data"
    image = data_root / "question_bank" / "extracted_images" / "set" / "rId1.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"image")

    resolved = resolve_question_bank_asset_path(
        r"D:\old\project\user_data\question_bank\extracted_images\set\rId1.png",
        data_root=data_root,
    )

    assert resolved == image


def test_ambiguous_filename_fallback_raises_instead_of_guessing(tmp_path: Path) -> None:
    data_root = tmp_path / "user_data"
    for folder in ("set-a", "set-b"):
        image = data_root / "question_bank" / "extracted_images" / folder / "same.png"
        image.parent.mkdir(parents=True)
        image.write_bytes(folder.encode())

    with pytest.raises(AmbiguousQuestionBankAssetPathError):
        resolve_question_bank_asset_path("Z:/missing/same.png", data_root=data_root)


def test_missing_asset_returns_original_path(tmp_path: Path) -> None:
    missing = Path("question_bank/raw_papers/missing.docx")

    assert resolve_question_bank_asset_path(missing, data_root=tmp_path) == missing
