from __future__ import annotations

import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

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


def test_paged_duplicate_groups_refresh_after_relabelling_and_keep_occurrence_numbers(tmp_path):
    from question_bank.database.schema import connect
    from question_bank.services.duplicate_analysis_copy_service import ensure_content_index

    db = tmp_path / "question_bank.db"
    _seed_paper(db)
    with connect(db) as conn:
        conn.execute("INSERT INTO papers(id,title) VALUES(2,'Reused paper')")
        conn.executemany("INSERT INTO questions(id,paper_id,question_number,question_text) VALUES(?,1,?,?)", [
            (1, "1", "计算 3+2"), (2, "2", "计算 3+2"), (3, "3", "计算 8+9"),
        ])
        ensure_content_index(conn, data_root=tmp_path)
        conn.execute("INSERT INTO paper_question_occurrences(paper_id,question_id,question_number) VALUES(2,1,'9')")
    service = QuestionBankReadService(db, data_root=tmp_path)
    first = service.list_questions(QuestionReadFilters(collapse_duplicates=True, page_size=1))
    second = service.list_questions(QuestionReadFilters(collapse_duplicates=True, page_size=1, page=2))
    assert first.total == second.total == 2
    assert [first.items[0]["id"], second.items[0]["id"]] == [3, 1]
    with connect(db) as conn:
        conn.execute("INSERT INTO question_tags(question_id,tag_type,tag_value,source) VALUES(2,'method','合成标注','manual')")
    refreshed = service.list_questions(QuestionReadFilters(collapse_duplicates=True, page_size=1, page=2))
    assert refreshed.items[0]["id"] == 2
    reused = service.list_questions(QuestionReadFilters(paper_ids=(2,), sort="paper_order"))
    assert [(item["id"], item["question_number"]) for item in reused.items] == [(1, "9")]
    counts = {paper["id"]: paper["question_count"] for paper in service.list_papers()}
    assert counts == {1: 3, 2: 1}


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


def test_empty_wal_read_version_noise_keeps_cache_and_real_commits_refresh(tmp_path, monkeypatch):
    from integration import data_generation

    database = tmp_path / "TEST-cache-generation.db"
    _seed_paper(database)
    original_open = data_generation._GenerationMonitor._open

    class ReadVersionNoise:
        """Reproduce repeated read-only data_version bumps seen on this PC."""
        def __init__(self, connection):
            self.connection = connection
            self.reads = 0

        def execute(self, statement, *args):
            result = self.connection.execute(statement, *args)
            wal = Path(f"{database}-wal")
            if statement == "PRAGMA data_version" and (not wal.exists() or wal.stat().st_size == 0):
                value = result.fetchone()[0]
                self.reads += 1
                return SimpleNamespace(fetchone=lambda: (value + self.reads,))
            return result

        def close(self):
            self.connection.close()

    monkeypatch.setattr(data_generation._GenerationMonitor, "_open",
                        lambda monitor: ReadVersionNoise(original_open(monitor)))
    first = data_generation.commit_generation(database)
    assert [data_generation.commit_generation(database) for _ in range(6)] == [first] * 6
    service = QuestionBankReadService(database)
    assert [paper["title"] for paper in service.list_papers()] == ["Paper"]
    cache_keys = tuple(read_module._READ_RESULT_CACHE)
    assert service.list_papers()[0]["title"] == "Paper"
    assert tuple(read_module._READ_RESULT_CACHE) == cache_keys

    writer = sqlite3.connect(database)
    try:
        writer.execute("UPDATE papers SET title='TEST-committed' WHERE id=1")
        assert service.list_papers()[0]["title"] == "Paper"
        writer.commit()
        assert data_generation.commit_generation(database) > first
        assert service.list_papers()[0]["title"] == "TEST-committed"
        writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        settled = data_generation.commit_generation(database)
        assert [data_generation.commit_generation(database) for _ in range(6)] == [settled] * 6

        # A whole commit/checkpoint cycle between polls must also refresh,
        # including when the filesystem timestamp is kept unchanged.
        state = database.stat()
        writer.execute("UPDATE papers SET title='TEST-second' WHERE id=1")
        writer.commit()
        writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        os.utime(database, ns=(state.st_atime_ns, state.st_mtime_ns))
        assert data_generation.commit_generation(database) > settled
        assert service.list_papers()[0]["title"] == "TEST-second"
    finally:
        writer.close()
