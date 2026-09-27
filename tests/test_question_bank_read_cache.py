from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from question_bank.database.schema import initialize_database
from question_bank.services import question_read_service as read_module
from question_bank.services.question_read_service import (
    QuestionBankReadService,
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
