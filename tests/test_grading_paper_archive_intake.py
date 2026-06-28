from __future__ import annotations

from pathlib import Path

import pytest

from question_bank.database.schema import initialize_database
from question_bank.importers.batch_importer import BatchImportResult, PaperImportFileResult
from question_bank.services import grading_paper_intake_service
from question_bank.services.grading_paper_intake_service import (
    archive_uploaded_grading_paper,
    copy_existing_grading_paper_to_raw_dir,
    intake_grading_paper_to_question_bank,
    save_uploaded_grading_paper,
)


def test_archive_uploaded_grading_paper_returns_portable_hash_evidence(tmp_path: Path) -> None:
    data_root = tmp_path / "user_data"
    raw = data_root / "question_bank" / "raw_papers"

    first = archive_uploaded_grading_paper(
        filename="paper.docx",
        content=b"paper-content",
        data_root=data_root,
        raw_papers_dir=raw,
    )
    reused = archive_uploaded_grading_paper(
        filename="renamed.docx",
        content=b"paper-content",
        data_root=data_root,
        raw_papers_dir=raw,
    )

    assert first.stored_path.startswith("question_bank/raw_papers/")
    assert len(first.sha256) == 64
    assert reused.physical_path == first.physical_path
    assert reused.sha256 == first.sha256
    assert reused.reused is True


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


def test_duplicate_intake_returns_the_archived_portable_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "user_data"
    archived = data_root / "question_bank" / "raw_papers" / "paper_hash.docx"
    archived.parent.mkdir(parents=True)
    archived.write_bytes(b"paper-content")
    source = tmp_path / "outside.docx"
    source.write_bytes(b"paper-content")
    stored_source = "question_bank/raw_papers/paper_hash.docx"
    db_path = data_root / "databases" / "question_bank.db"
    initialize_database(db_path)

    monkeypatch.setattr(
        grading_paper_intake_service,
        "import_scanned_papers",
        lambda *_args, **_kwargs: BatchImportResult(
            files=[PaperImportFileResult(source_file=stored_source, status="duplicate")],
            imported_papers=0,
            question_count=0,
            answer_match_count=0,
            review_count=0,
            skipped_duplicate_files=1,
            failed_files=0,
        ),
    )

    result = intake_grading_paper_to_question_bank(
        source_file=source,
        db_path=db_path,
        data_root=data_root,
        run_ai_tagging=False,
    )

    assert result.saved_file == archived


def test_intake_source_wires_contextual_skill_resolver() -> None:
    source = Path("question_bank/services/grading_paper_intake_service.py").read_text(encoding="utf-8")

    assert "build_skill_context_ranker" in source
    assert "skill_resolver=skill_resolver" in source
