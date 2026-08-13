from __future__ import annotations

from pathlib import Path

from question_bank.services.source_paper_archive_service import (
    archive_source_bytes,
    archive_source_paper,
)


def test_archive_copies_without_removing_source_and_reuses_identical_content(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "user_data"
    source = tmp_path / "outside" / "期中试卷.docx"
    source.parent.mkdir()
    source.write_bytes(b"same-paper")

    first = archive_source_paper(source, data_root=data_root)
    second_source = tmp_path / "other" / "renamed.docx"
    second_source.parent.mkdir()
    second_source.write_bytes(b"same-paper")
    second = archive_source_paper(second_source, data_root=data_root)

    assert source.read_bytes() == b"same-paper"
    assert second_source.read_bytes() == b"same-paper"
    assert first.physical_path == second.physical_path
    assert first.stored_path.startswith("question_bank/raw_papers/")
    assert first.sha256 == second.sha256
    assert second.reused is True


def test_same_name_with_different_content_creates_distinct_archives(tmp_path: Path) -> None:
    data_root = tmp_path / "user_data"
    left = tmp_path / "a" / "paper.docx"
    right = tmp_path / "b" / "paper.docx"
    left.parent.mkdir()
    right.parent.mkdir()
    left.write_bytes(b"left")
    right.write_bytes(b"right")

    archived_left = archive_source_paper(left, data_root=data_root)
    archived_right = archive_source_paper(right, data_root=data_root)

    assert archived_left.physical_path != archived_right.physical_path
    assert archived_left.physical_path.read_bytes() == b"left"
    assert archived_right.physical_path.read_bytes() == b"right"


def test_archive_bytes_uses_the_same_content_addressed_contract(tmp_path: Path) -> None:
    archived = archive_source_bytes(
        filename="upload.docx",
        content=b"uploaded-paper",
        data_root=tmp_path / "user_data",
    )

    assert archived.physical_path.read_bytes() == b"uploaded-paper"
    assert archived.stored_path.endswith(f"_{archived.sha256[:12]}.docx")


def test_custom_raw_papers_dir_infers_its_data_root(tmp_path: Path) -> None:
    raw_papers = tmp_path / "portable_data" / "question_bank" / "raw_papers"
    source = tmp_path / "paper.docx"
    source.write_bytes(b"paper")

    archived = archive_source_paper(source, raw_papers_dir=raw_papers)

    assert archived.physical_path.parent == raw_papers
    assert archived.stored_path.startswith("question_bank/raw_papers/")
