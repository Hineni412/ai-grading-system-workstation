from __future__ import annotations

from pathlib import Path

import pytest

from question_bank.services.asset_path_service import (
    AmbiguousQuestionBankAssetPathError,
    resolve_question_bank_asset_path,
)


def _make_path_inaccessible(
    monkeypatch: pytest.MonkeyPatch,
    inaccessible: Path,
) -> None:
    original_is_file = Path.is_file

    def raise_for_inaccessible(path: Path, *args: object, **kwargs: object) -> bool:
        if path == inaccessible:
            raise OSError("network path unavailable")
        return original_is_file(path, *args, **kwargs)

    monkeypatch.setattr(Path, "is_file", raise_for_inaccessible)


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


def test_data_relative_path_prefers_data_root_over_current_working_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "portable" / "user_data"
    archived = data_root / "question_bank" / "raw_papers" / "paper_hash.docx"
    archived.parent.mkdir(parents=True)
    archived.write_bytes(b"portable-paper")

    working_dir = tmp_path / "working"
    shadow = working_dir / "question_bank" / "raw_papers" / "paper_hash.docx"
    shadow.parent.mkdir(parents=True)
    shadow.write_bytes(b"wrong-paper")
    monkeypatch.chdir(working_dir)

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


def test_inaccessible_absolute_path_uses_unique_filename_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "user_data"
    fallback = data_root / "question_bank" / "extracted_images" / "set" / "unique.png"
    fallback.parent.mkdir(parents=True)
    fallback.write_bytes(b"image")
    inaccessible = (tmp_path / "offline" / "unique.png").resolve(strict=False)
    _make_path_inaccessible(monkeypatch, inaccessible)

    assert resolve_question_bank_asset_path(inaccessible, data_root=data_root) == fallback


def test_ambiguous_filename_fallback_raises_instead_of_guessing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "user_data"
    for folder in ("set-a", "set-b"):
        image = data_root / "question_bank" / "extracted_images" / folder / "same.png"
        image.parent.mkdir(parents=True)
        image.write_bytes(folder.encode())
    inaccessible = (tmp_path / "offline" / "same.png").resolve(strict=False)
    _make_path_inaccessible(monkeypatch, inaccessible)

    with pytest.raises(AmbiguousQuestionBankAssetPathError):
        resolve_question_bank_asset_path(inaccessible, data_root=data_root)


def test_missing_asset_returns_original_path(tmp_path: Path) -> None:
    missing = Path("question_bank/raw_papers/missing.docx")

    assert resolve_question_bank_asset_path(missing, data_root=tmp_path) == missing
