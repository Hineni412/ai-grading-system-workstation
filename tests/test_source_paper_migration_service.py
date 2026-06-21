from __future__ import annotations

from pathlib import Path

import pytest

from question_bank.database.schema import connect, initialize_database
from question_bank.services import source_paper_migration_service as migration_service
from question_bank.services.source_paper_migration_service import (
    apply_source_paper_migration,
    plan_source_paper_migration,
)


def _seed_question_bank(db_path: Path, *, source_file: str, tag_rows: int) -> None:
    initialize_database(db_path)
    with connect(db_path) as conn:
        paper_id = conn.execute(
            "INSERT INTO papers (title, source_file) VALUES ('paper', ?)",
            (source_file,),
        ).lastrowid
        question_id = conn.execute(
            """
            INSERT INTO questions (paper_id, question_number, question_text, source_file)
            VALUES (?, '1', '测试题目正文', ?)
            """,
            (paper_id, source_file),
        ).lastrowid
        conn.execute(
            """
            INSERT INTO question_previews (question_id, preview_type, source_file)
            VALUES (?, 'question', ?)
            """,
            (question_id, source_file),
        )
        conn.executemany(
            """
            INSERT INTO question_tags (
                question_id, tag_type, tag_value, confidence, source, model_name
            ) VALUES (?, 'knowledge_point', ?, 0.9, 'ai', 'test-model')
            """,
            [(question_id, f"tag-{index}") for index in range(tag_rows)],
        )
        conn.commit()


def _seed_single_source_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    data_root = tmp_path / "user_data"
    db_path = data_root / "databases" / "question_bank.db"
    source = tmp_path / "outside" / "paper.docx"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"paper")
    _seed_question_bank(db_path, source_file=str(source), tag_rows=3)
    return data_root, db_path, source


def test_apply_migration_updates_all_source_columns_and_preserves_tags(
    tmp_path: Path,
) -> None:
    data_root, db_path, source = _seed_single_source_fixture(tmp_path)

    report = apply_source_paper_migration(db_path=db_path, data_root=data_root)

    with connect(db_path) as conn:
        paper_source = conn.execute("SELECT source_file FROM papers").fetchone()[0]
        question_source = conn.execute("SELECT source_file FROM questions").fetchone()[0]
        preview_source = conn.execute("SELECT source_file FROM question_previews").fetchone()[0]
        tag_count = conn.execute("SELECT COUNT(*) FROM question_tags").fetchone()[0]
    assert paper_source == question_source == preview_source
    assert paper_source.startswith("question_bank/raw_papers/")
    assert tag_count == 3
    assert source.exists()
    assert report.migrated_sources == 1
    assert report.backup_path is not None and report.backup_path.is_file()


def test_missing_source_is_reported_without_database_update(tmp_path: Path) -> None:
    data_root = tmp_path / "user_data"
    db_path = data_root / "databases" / "question_bank.db"
    missing = tmp_path / "missing.docx"
    _seed_question_bank(db_path, source_file=str(missing), tag_rows=1)

    report = apply_source_paper_migration(db_path=db_path, data_root=data_root)

    with connect(db_path) as conn:
        stored = conn.execute("SELECT source_file FROM questions").fetchone()[0]
    assert stored == str(missing)
    assert report.missing_sources == 1
    assert report.migrated_sources == 0


def test_dry_run_does_not_copy_or_update(tmp_path: Path) -> None:
    data_root, db_path, source = _seed_single_source_fixture(tmp_path)

    report = plan_source_paper_migration(db_path=db_path, data_root=data_root)

    assert report.scanned_sources == 1
    assert report.migrated_sources == 0
    assert not (data_root / "question_bank" / "raw_papers").exists()
    with connect(db_path) as conn:
        assert conn.execute("SELECT source_file FROM questions").fetchone()[0] == str(source)


def test_transaction_rolls_back_when_invariant_check_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root, db_path, source = _seed_single_source_fixture(tmp_path)
    monkeypatch.setattr(migration_service, "_invariants_match", lambda *_: False)

    with pytest.raises(RuntimeError, match="invariant"):
        apply_source_paper_migration(db_path=db_path, data_root=data_root)

    with connect(db_path) as conn:
        assert conn.execute("SELECT source_file FROM questions").fetchone()[0] == str(source)


def test_invariant_snapshot_covers_every_question_tag_column(tmp_path: Path) -> None:
    _data_root, db_path, _source = _seed_single_source_fixture(tmp_path)

    with connect(db_path) as conn:
        before = migration_service._snapshot_invariants(conn)
        conn.execute(
            "UPDATE question_tags SET created_at = '2099-01-01 00:00:00'"
        )
        after = migration_service._snapshot_invariants(conn)

    assert not migration_service._invariants_match(before, after)


def test_invariant_snapshot_covers_question_content_except_source_path(
    tmp_path: Path,
) -> None:
    _data_root, db_path, _source = _seed_single_source_fixture(tmp_path)

    with connect(db_path) as conn:
        before = migration_service._snapshot_invariants(conn)
        conn.execute("UPDATE questions SET question_text = 'changed'")
        after = migration_service._snapshot_invariants(conn)

    assert not migration_service._invariants_match(before, after)
