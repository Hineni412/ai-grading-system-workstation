from __future__ import annotations

import json
from pathlib import Path

import pytest

from question_bank.services.asset_path_service import (
    AmbiguousQuestionBankAssetPathError,
    resolve_question_bank_asset_path,
)
from question_bank.services.file_cache import (
    cached_file_bytes,
    cached_parsed_file,
    clear_file_caches,
)


@pytest.fixture(autouse=True)
def _clean_caches():
    clear_file_caches()
    yield
    clear_file_caches()


def test_cached_file_bytes_refreshes_after_modification(tmp_path: Path) -> None:
    target = tmp_path / "asset.bin"
    target.write_bytes(b"first")

    assert cached_file_bytes(target) == b"first"
    assert cached_file_bytes(target) == b"first"

    target.write_bytes(b"second-longer")
    assert cached_file_bytes(target) == b"second-longer"


def test_cached_parsed_file_parses_once_and_invalidates_on_change(
    tmp_path: Path,
) -> None:
    target = tmp_path / "data.json"
    target.write_text('{"value": 1}', encoding="utf-8")
    calls = []

    def parse(path: Path):
        calls.append(path)
        return json.loads(path.read_text(encoding="utf-8"))

    assert cached_parsed_file(target, parse) == {"value": 1}
    assert cached_parsed_file(target, parse) == {"value": 1}
    assert len(calls) == 1

    target.write_text('{"value": 22}', encoding="utf-8")
    assert cached_parsed_file(target, parse) == {"value": 22}
    assert len(calls) == 2


def test_cached_asset_resolution_detects_added_search_dir_file(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "user_data"
    folder_a = data_root / "question_bank" / "extracted_images" / "set-a"
    folder_b = data_root / "question_bank" / "extracted_images" / "set-b"
    folder_a.mkdir(parents=True)
    folder_b.mkdir(parents=True)
    first = folder_a / "figure.png"
    first.write_bytes(b"a")

    subdirs = ("question_bank/extracted_images",)
    assert (
        resolve_question_bank_asset_path(
            "figure.png", data_root=data_root, search_subdirs=subdirs
        )
        == first
    )
    # A cached hit returns the same path.
    assert (
        resolve_question_bank_asset_path(
            "figure.png", data_root=data_root, search_subdirs=subdirs
        )
        == first
    )

    (folder_b / "figure.png").write_bytes(b"b")
    with pytest.raises(AmbiguousQuestionBankAssetPathError):
        resolve_question_bank_asset_path(
            "figure.png", data_root=data_root, search_subdirs=subdirs
        )


def test_cached_asset_resolution_recomputes_after_file_removal(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "user_data"
    folder = data_root / "question_bank" / "extracted_images"
    folder.mkdir(parents=True)
    image = folder / "gone.png"
    image.write_bytes(b"x")
    subdirs = ("question_bank/extracted_images",)

    assert (
        resolve_question_bank_asset_path(
            "gone.png", data_root=data_root, search_subdirs=subdirs
        )
        == image
    )
    image.unlink()
    # The stale hit is rejected by the existence re-check; the resolution
    # falls back to the data-root relative candidate like an uncached call.
    assert (
        resolve_question_bank_asset_path(
            "gone.png", data_root=data_root, search_subdirs=subdirs
        )
        == (data_root / "gone.png").resolve(strict=False)
    )
