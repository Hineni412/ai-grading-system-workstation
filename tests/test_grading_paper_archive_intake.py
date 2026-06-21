from __future__ import annotations

from pathlib import Path

from question_bank.services.grading_paper_intake_service import (
    copy_existing_grading_paper_to_raw_dir,
    save_uploaded_grading_paper,
)


def test_uploaded_grading_paper_uses_hash_archive(tmp_path: Path) -> None:
    raw = tmp_path / "user_data" / "question_bank" / "raw_papers"
    saved = save_uploaded_grading_paper(
        filename="paper.docx",
        content=b"paper-content",
        raw_papers_dir=raw,
    )
    reused = save_uploaded_grading_paper(
        filename="renamed.docx",
        content=b"paper-content",
        raw_papers_dir=raw,
    )

    assert saved.name.startswith("paper_")
    assert saved.name.endswith(".docx")
    assert saved.read_bytes() == b"paper-content"
    assert reused == saved
    assert len(list(raw.glob("*.docx"))) == 1


def test_copy_existing_grading_paper_reuses_identical_archive(tmp_path: Path) -> None:
    source = tmp_path / "outside.docx"
    source.write_bytes(b"paper-content")
    raw = tmp_path / "user_data" / "question_bank" / "raw_papers"

    first = copy_existing_grading_paper_to_raw_dir(source, raw_papers_dir=raw)
    second = copy_existing_grading_paper_to_raw_dir(source, raw_papers_dir=raw)

    assert first == second
    assert len(list(raw.glob("*.docx"))) == 1
    assert source.exists()
