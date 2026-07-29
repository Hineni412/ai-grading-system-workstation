from __future__ import annotations

from pathlib import Path

import pytest

from question_bank.database.schema import connect, initialize_database
from question_bank.services.question_read_service import QuestionBankReadService
from question_bank.services.question_write_service import (
    PaperPermanentDeleteConflict,
    PaperPermanentDeleteSelection,
    PaperStateConflict,
    QuestionBankWriteService,
)


def _seed_paper(
    tmp_path: Path,
) -> tuple[QuestionBankWriteService, QuestionBankReadService, int, str, Path]:
    data_root = tmp_path / "data"
    db_path = data_root / "databases" / "question_bank.db"
    initialize_database(db_path)
    source_path = data_root / "question_bank" / "raw_papers" / "source.docx"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"source-stays")
    with connect(db_path) as conn:
        paper_id = int(
            conn.execute(
                """
                INSERT INTO papers (
                    title, source_file, import_status, updated_at
                ) VALUES (?, ?, 'completed', ?)
                """,
                ("测试卷", str(source_path), "2026-07-29 12:00:00.000000"),
            ).lastrowid
        )
        first_id = int(
            conn.execute(
                """
                INSERT INTO questions (
                    paper_id, question_number, question_text, is_deleted
                ) VALUES (?, '1', '第一题', 0)
                """,
                (paper_id,),
            ).lastrowid
        )
        conn.execute(
            """
            INSERT INTO questions (
                paper_id, question_number, question_text, is_deleted
            ) VALUES (?, '2', '第二题', 0)
            """,
            (paper_id,),
        )
        previously_deleted_id = int(
            conn.execute(
                """
                INSERT INTO questions (
                    paper_id, question_number, question_text,
                    is_deleted, deleted_at
                ) VALUES (?, '3', '此前单独删除的题', 1, ?)
                """,
                (paper_id, "2026-07-28 09:00:00"),
            ).lastrowid
        )
        conn.execute(
            """
            INSERT INTO question_tags (
                question_id, tag_type, tag_value, source
            ) VALUES (?, 'knowledge_point', '全等三角形', 'manual')
            """,
            (first_id,),
        )
    return (
        QuestionBankWriteService(db_path, data_root=data_root),
        QuestionBankReadService(db_path, data_root=data_root),
        paper_id,
        "2026-07-29 12:00:00.000000",
        source_path,
    )


def test_restoring_a_trashed_paper_only_restores_questions_moved_with_it(
    tmp_path: Path,
) -> None:
    writer, reader, paper_id, version, source_path = _seed_paper(tmp_path)

    trashed = writer.set_paper_deleted(
        paper_id,
        expected_updated_at=version,
        deleted=True,
    )
    restored = writer.set_paper_deleted(
        paper_id,
        expected_updated_at=trashed.updated_at,
        deleted=False,
    )

    assert trashed.deleted is True
    assert trashed.affected_question_count == 2
    assert restored.deleted is False
    assert restored.affected_question_count == 2
    assert reader.get_question(1)["question_text"] == "第一题"
    assert reader.get_question(1)["tags"][0]["tag_value"] == "全等三角形"
    assert reader.get_question(3) is None
    assert source_path.read_bytes() == b"source-stays"


def test_permanent_delete_removes_owned_file_tags_and_statistic_links(
    tmp_path: Path,
) -> None:
    writer, _reader, paper_id, version, source_path = _seed_paper(tmp_path)
    trashed = writer.set_paper_deleted(
        paper_id,
        expected_updated_at=version,
        deleted=True,
    )
    with connect(writer.db_path) as conn:
        question_id = int(conn.execute(
            "SELECT id FROM questions WHERE paper_id = ? ORDER BY id LIMIT 1",
            (paper_id,),
        ).fetchone()[0])
        conn.execute(
            """
            INSERT INTO grading_question_links (
                grading_session_id, source_question_id, bank_question_id,
                link_method, status
            ) VALUES ('7', 'Q1', ?, 'manual', 'confirmed')
            """,
            (question_id,),
        )
        training_set_id = int(conn.execute(
            "INSERT INTO training_sets (name) VALUES ('临时训练')"
        ).lastrowid)
        conn.execute(
            """
            INSERT INTO training_set_items (training_set_id, question_id)
            VALUES (?, ?)
            """,
            (training_set_id, question_id),
        )

    impact = writer.preview_paper_permanent_delete([
        PaperPermanentDeleteSelection(paper_id, trashed.updated_at)
    ])
    result = writer.permanently_delete_papers(
        [PaperPermanentDeleteSelection(paper_id, trashed.updated_at)],
        confirmation_phrase="彻底删除 1 份试卷",
        request_token="a" * 32,
    )
    repeated = writer.permanently_delete_papers(
        [PaperPermanentDeleteSelection(paper_id, trashed.updated_at)],
        confirmation_phrase="彻底删除 1 份试卷",
        request_token="a" * 32,
    )

    assert impact.tag_count == 1
    assert impact.training_link_count == 1
    assert impact.knowledge_graph_link_count == 1
    assert result.deleted_paper_ids == (paper_id,)
    assert repeated == result
    assert not source_path.exists()
    with connect(writer.db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM papers WHERE id = ?", (paper_id,)
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM grading_question_links"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM training_set_items"
        ).fetchone()[0] == 0

    with pytest.raises(PaperPermanentDeleteConflict):
        writer.permanently_delete_papers(
            [
                PaperPermanentDeleteSelection(
                    paper_id,
                    trashed.updated_at + "-changed",
                )
            ],
            confirmation_phrase="彻底删除 1 份试卷",
            request_token="a" * 32,
        )


def test_paper_state_change_rejects_the_opposite_action_from_a_stale_card(
    tmp_path: Path,
) -> None:
    writer, _, paper_id, version, _ = _seed_paper(tmp_path)
    trashed = writer.set_paper_deleted(
        paper_id,
        expected_updated_at=version,
        deleted=True,
    )

    with pytest.raises(PaperStateConflict) as caught:
        writer.set_paper_deleted(
            paper_id,
            expected_updated_at=version,
            deleted=False,
        )

    assert caught.value.current_updated_at == trashed.updated_at
    assert caught.value.deleted is True


def test_repeated_paper_state_request_is_idempotent_and_returns_real_status(
    tmp_path: Path,
) -> None:
    writer, _, paper_id, version, _ = _seed_paper(tmp_path)

    trashed = writer.set_paper_deleted(
        paper_id,
        expected_updated_at=version,
        deleted=True,
    )
    repeated_trash = writer.set_paper_deleted(
        paper_id,
        expected_updated_at=version,
        deleted=True,
    )
    restored = writer.set_paper_deleted(
        paper_id,
        expected_updated_at=trashed.updated_at,
        deleted=False,
    )
    repeated_restore = writer.set_paper_deleted(
        paper_id,
        expected_updated_at=trashed.updated_at,
        deleted=False,
    )

    assert trashed.import_status == "deleted"
    assert repeated_trash == trashed
    assert restored.import_status == "completed"
    assert repeated_restore.deleted is False
    assert repeated_restore.import_status == "completed"
    assert repeated_restore.updated_at == restored.updated_at


def test_restore_falls_back_to_needs_review_when_old_status_is_blank(
    tmp_path: Path,
) -> None:
    writer, _, paper_id, version, _ = _seed_paper(tmp_path)
    with connect(writer.db_path) as conn:
        conn.execute(
            "UPDATE papers SET import_status = '' WHERE id = ?",
            (paper_id,),
        )

    trashed = writer.set_paper_deleted(
        paper_id,
        expected_updated_at=version,
        deleted=True,
    )
    restored = writer.set_paper_deleted(
        paper_id,
        expected_updated_at=trashed.updated_at,
        deleted=False,
    )

    assert restored.import_status == "needs_review"
    with connect(writer.db_path) as conn:
        row = conn.execute(
            "SELECT import_status FROM papers WHERE id = ?",
            (paper_id,),
        ).fetchone()
    assert row is not None
    assert row["import_status"] == "needs_review"
