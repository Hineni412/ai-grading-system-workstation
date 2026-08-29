from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from question_bank.database.schema import initialize_database
from question_bank.services import question_read_service as read_module
from question_bank.services.question_read_service import (
    QuestionBankReadService,
    QuestionBankSnapshotBusy,
    QuestionBankSnapshotUnavailable,
    QuestionReadFilters,
)


@pytest.fixture(autouse=True)
def _clear_read_result_cache():
    read_module._READ_RESULT_CACHE.clear()
    yield
    read_module._READ_RESULT_CACHE.clear()


def _seed_paper(db_path: Path, paper_id: int = 1, title: str = "Paper") -> None:
    initialize_database(db_path)
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            "INSERT INTO papers (id, title, import_status) VALUES (?, ?, 'success')",
            (paper_id, title),
        )
        connection.commit()
        # Fold the WAL into the main file up front so a later read-only
        # connection close cannot checkpoint and shift the generation token
        # mid-test.
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        connection.close()


def test_result_cache_reuses_read_within_same_generation(tmp_path: Path) -> None:
    db_path = tmp_path / "question_bank.db"
    _seed_paper(db_path)
    service = QuestionBankReadService(db_path)

    # Warm-up: the first read may checkpoint the WAL on close, which changes
    # the source generation once; steady-state repeats must hit the cache.
    assert [paper["title"] for paper in service.list_papers()] == ["Paper"]

    underlying_calls = 0
    original = service._list_papers

    def counted(*args: Any, **kwargs: Any):
        nonlocal underlying_calls
        underlying_calls += 1
        return original(*args, **kwargs)

    service._list_papers = counted  # type: ignore[method-assign]
    try:
        assert [paper["title"] for paper in service.list_papers()] == ["Paper"]
        assert [paper["title"] for paper in service.list_papers()] == ["Paper"]
    finally:
        service._list_papers = original  # type: ignore[method-assign]

    # Both repeated reads hit the result cache: the seeded generation stays
    # stable, so no underlying query runs at all.
    assert underlying_calls == 0


def test_write_invalidates_result_cache_and_new_row_becomes_visible(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "question_bank.db"
    _seed_paper(db_path)
    service = QuestionBankReadService(db_path)

    assert [paper["title"] for paper in service.list_papers()] == ["Paper"]

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO papers (id, title, import_status) VALUES (2, 'Second', 'success')"
        )

    titles = {paper["title"] for paper in service.list_papers()}
    assert titles == {"Paper", "Second"}


def test_read_connection_runs_inside_one_deferred_transaction(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "question_bank.db"
    _seed_paper(db_path)

    with read_module._read_connection(db_path) as connection:
        assert connection.row_factory is sqlite3.Row
        assert connection.in_transaction is True
        row = connection.execute("SELECT COUNT(*) AS n FROM papers").fetchone()
        assert row["n"] == 1


def test_uncommitted_writer_does_not_block_or_leak_into_reads(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "question_bank.db"
    _seed_paper(db_path)
    writer = sqlite3.connect(db_path, timeout=5.0)
    try:
        writer.execute("PRAGMA busy_timeout = 5000")
        writer.execute("BEGIN IMMEDIATE")
        writer.execute(
            "INSERT INTO papers (id, title, import_status) VALUES (9, 'Uncommitted', 'success')"
        )

        def read_titles() -> list[str]:
            return [
                paper["title"]
                for paper in QuestionBankReadService(db_path).list_papers()
            ]

        with ThreadPoolExecutor(max_workers=2) as executor:
            reader = executor.submit(read_titles)
            assert set(reader.result(timeout=10)) == {"Paper"}

        writer.commit()
        assert set(read_titles()) == {"Paper", "Uncommitted"}
    finally:
        writer.close()


def test_hot_rollback_journal_maps_to_unavailable(tmp_path: Path) -> None:
    db_path = tmp_path / "question_bank.db"
    _seed_paper(db_path)
    Path(f"{db_path}-journal").write_bytes(b"hot rollback journal")

    # A hot journal needs a writable recovery pass; a read-only connection
    # fails closed instead of returning uncommitted data.
    with pytest.raises(QuestionBankSnapshotUnavailable):
        with read_module._read_connection(db_path) as connection:
            connection.execute("SELECT COUNT(*) FROM papers")


def test_missing_database_maps_to_unavailable(tmp_path: Path) -> None:
    with pytest.raises(QuestionBankSnapshotUnavailable):
        with read_module._read_connection(tmp_path / "missing.db"):
            pass  # pragma: no cover - the context manager raises on entry


def test_corrupt_database_maps_to_unavailable(tmp_path: Path) -> None:
    db_path = tmp_path / "corrupt.db"
    db_path.write_bytes(b"not-a-sqlite-database")

    with pytest.raises(QuestionBankSnapshotUnavailable):
        with read_module._read_connection(db_path) as connection:
            connection.execute("SELECT COUNT(*) FROM papers")


def test_busy_error_mapping() -> None:
    busy = read_module._direct_read_error(
        sqlite3.OperationalError("database is locked")
    )
    unavailable = read_module._direct_read_error(
        sqlite3.OperationalError("file is not a database")
    )

    assert isinstance(busy, QuestionBankSnapshotBusy)
    assert isinstance(unavailable, QuestionBankSnapshotUnavailable)


def test_question_cache_is_scoped_to_the_service_data_root(tmp_path: Path) -> None:
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO papers (id, title, import_status) VALUES (1, 'Paper', 'success')"
        )
        connection.execute(
            """
            INSERT INTO questions (id, paper_id, question_number, question_text)
            VALUES (1, 1, '1', 'Question')
            """
        )

    rich_root = tmp_path / "with-rich-content" / "question_bank" / "rich_content"
    rich_root.mkdir(parents=True)
    rich_root.joinpath("question_1.json").write_text(
        json.dumps(
            {
                "version": 3,
                "question_id": 1,
                "content_revision": "a" * 64,
                "question_blocks": [],
                "answer_blocks": [],
            }
        ),
        encoding="utf-8",
    )

    filters = QuestionReadFilters()
    with_rich_content = QuestionBankReadService(
        db_path,
        data_root=tmp_path / "with-rich-content",
    ).list_questions(filters)
    without_rich_content = QuestionBankReadService(
        db_path,
        data_root=tmp_path / "without-rich-content",
    ).list_questions(filters)

    assert with_rich_content.items[0]["rich_content"]["available"] is True
    assert without_rich_content.items[0]["rich_content"]["available"] is False


def test_question_read_hides_legacy_next_section_heading_blocks(tmp_path: Path) -> None:
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO papers (id, title, import_status) VALUES (1, 'Paper', 'success')"
        )
        connection.execute(
            """
            INSERT INTO questions (id, paper_id, question_number, question_text)
            VALUES (1, 1, '1', '优美比为（ ）。')
            """
        )

    data_root = tmp_path / "data"
    rich_root = data_root / "question_bank" / "rich_content"
    rich_root.mkdir(parents=True)
    rich_root.joinpath("question_1.json").write_text(
        json.dumps(
            {
                "version": 3,
                "question_id": 1,
                "question_blocks": [
                    {"text": "优美比为（ ）。", "image_relationships": {}},
                    {
                        "text": "二．填空题（共5小题）\n[[IMAGE:D:/next.png]]",
                        "image_relationships": {},
                    },
                ],
                "answer_blocks": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    question = QuestionBankReadService(db_path, data_root=data_root).get_question(1)

    assert question is not None
    assert [
        block["text"] for block in question["rich_content"]["question_blocks"]
    ] == ["优美比为（ ）。"]
