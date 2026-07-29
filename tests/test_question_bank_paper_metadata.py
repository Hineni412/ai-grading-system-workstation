from __future__ import annotations

from pathlib import Path

import pytest

from question_bank.database.schema import connect, initialize_database
from question_bank.services.question_write_service import (
    PaperMetadataConflict,
    PaperMetadataUpdate,
    QuestionBankWriteService,
)


def _service_with_paper(tmp_path: Path) -> tuple[QuestionBankWriteService, int, str]:
    db_path = tmp_path / "data" / "databases" / "question_bank.db"
    initialize_database(db_path)
    with connect(db_path) as conn:
        paper_id = int(
            conn.execute(
                """
                INSERT INTO papers (
                    title, source_file, year, exam_type, import_status
                ) VALUES (
                    'source', 'question_bank/raw_papers/0526test2.docx',
                    NULL, NULL, 'imported'
                )
                """
            ).lastrowid
        )
        updated_at = str(
            conn.execute(
                "SELECT updated_at FROM papers WHERE id = ?",
                (paper_id,),
            ).fetchone()["updated_at"]
        )
    return (
        QuestionBankWriteService(db_path, data_root=tmp_path / "data"),
        paper_id,
        updated_at,
    )


def _metadata(title: str) -> PaperMetadataUpdate:
    return PaperMetadataUpdate(
        title=title,
        year="2026",
        province="广东省",
        city="深圳市",
        district="",
        exam_type="阶段练习",
        grade="七年级",
        semester="下学期",
        textbook_version="北师大版 2024",
    )


def test_paper_metadata_update_cleans_optional_values_and_changes_version(
    tmp_path: Path,
) -> None:
    service, paper_id, updated_at = _service_with_paper(tmp_path)

    result = service.update_paper_metadata(
        paper_id,
        expected_updated_at=updated_at,
        metadata=_metadata("0526test2"),
    )

    assert result.id == paper_id
    assert result.title == "0526test2"
    assert result.year == "2026"
    assert result.exam_type == "阶段练习"
    assert result.district is None
    assert result.updated_at != updated_at


def test_stale_paper_metadata_update_cannot_overwrite_newer_teacher_edit(
    tmp_path: Path,
) -> None:
    service, paper_id, original_updated_at = _service_with_paper(tmp_path)
    first = service.update_paper_metadata(
        paper_id,
        expected_updated_at=original_updated_at,
        metadata=_metadata("第一次修改"),
    )

    with pytest.raises(PaperMetadataConflict) as caught:
        service.update_paper_metadata(
            paper_id,
            expected_updated_at=original_updated_at,
            metadata=_metadata("过期窗口修改"),
        )

    assert caught.value.current_updated_at == first.updated_at
    current = service.update_paper_metadata(
        paper_id,
        expected_updated_at=first.updated_at,
        metadata=_metadata("第一次修改"),
    )
    assert current.title == "第一次修改"
    assert current.updated_at == first.updated_at
