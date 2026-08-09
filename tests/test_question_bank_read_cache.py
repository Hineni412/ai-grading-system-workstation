from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event, Lock

import pytest

from question_bank.current_knowledge import CurrentKnowledgeResolver
from question_bank.database.schema import initialize_database
from question_bank.services import question_read_service as read_module
from question_bank.services.question_read_service import (
    QuestionBankReadService,
    QuestionBankSnapshotBusy,
    QuestionBankSnapshotUnavailable,
    QuestionReadFilters,
)


@pytest.fixture(autouse=True)
def _clear_question_snapshot_cache():
    read_module._clear_question_read_snapshot_cache_for_tests()
    yield
    read_module._clear_question_read_snapshot_cache_for_tests()


def test_repeated_paper_refresh_reuses_stable_snapshot_and_invalidates_on_write(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO papers (id, title, import_status) VALUES (1, 'First', 'success')"
        )

    captures = 0
    original_capture = read_module._capture_snapshot_attempt

    def tracked_capture(*args, **kwargs):
        nonlocal captures
        captures += 1
        return original_capture(*args, **kwargs)

    monkeypatch.setattr(read_module, "_capture_snapshot_attempt", tracked_capture)
    service = QuestionBankReadService(db_path)

    assert [item["title"] for item in service.list_papers()] == ["First"]
    assert [item["title"] for item in service.list_papers()] == ["First"]
    assert captures == 1

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO papers (id, title, import_status) VALUES (2, 'Second', 'success')"
        )

    assert {item["title"] for item in service.list_papers()} == {"First", "Second"}
    assert captures == 2


def test_distinct_question_reads_share_one_validated_database_generation(
    tmp_path: Path,
    monkeypatch,
) -> None:
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

    captures = 0
    validations: list[bool] = []
    original_capture = read_module._capture_snapshot_attempt
    original_open = read_module._open_snapshot_connection

    def tracked_capture(*args, **kwargs):
        nonlocal captures
        captures += 1
        return original_capture(*args, **kwargs)

    def tracked_open(*args, **kwargs):
        validations.append(bool(kwargs.get("validate_snapshot", True)))
        return original_open(*args, **kwargs)

    monkeypatch.setattr(read_module, "_capture_snapshot_attempt", tracked_capture)
    monkeypatch.setattr(read_module, "_open_snapshot_connection", tracked_open)
    service = QuestionBankReadService(db_path)

    assert service.list_papers()[0]["title"] == "Paper"
    assert service.list_questions(QuestionReadFilters()).total == 1
    assert service.list_facets(QuestionReadFilters())["question_types"] == []

    assert captures == 1
    assert validations.count(True) == 1
    assert validations.count(False) == 2


def test_non_knowledge_reads_do_not_load_current_knowledge(
    tmp_path: Path,
    monkeypatch,
) -> None:
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

    calls = 0
    original_loader = CurrentKnowledgeResolver.from_connection.__func__

    def counted_loader(cls, connection, *, taxonomy_catalog=None):
        nonlocal calls
        calls += 1
        return original_loader(
            cls,
            connection,
            taxonomy_catalog=taxonomy_catalog,
        )

    monkeypatch.setattr(
        CurrentKnowledgeResolver,
        "from_connection",
        classmethod(counted_loader),
    )
    service = QuestionBankReadService(db_path)

    assert service.list_papers()[0]["title"] == "Paper"
    assert service.tag_value_counts("method", ["not-present"]) == {
        "not-present": 0
    }
    assert calls == 0

    assert service.list_questions(QuestionReadFilters()).total == 1
    assert calls == 1


def test_concurrent_cold_reads_publish_one_snapshot(
    tmp_path: Path,
    monkeypatch,
) -> None:
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

    capture_started = Event()
    allow_capture = Event()
    second_started = Event()
    count_lock = Lock()
    captures = 0
    original_capture = read_module._capture_snapshot_attempt

    def blocked_capture(*args, **kwargs):
        nonlocal captures
        with count_lock:
            captures += 1
        capture_started.set()
        assert allow_capture.wait(timeout=5)
        return original_capture(*args, **kwargs)

    def read_questions():
        second_started.set()
        return QuestionBankReadService(db_path).list_questions(
            QuestionReadFilters()
        )

    monkeypatch.setattr(read_module, "_capture_snapshot_attempt", blocked_capture)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            papers_future = executor.submit(
                QuestionBankReadService(db_path).list_papers
            )
            assert capture_started.wait(timeout=5)
            questions_future = executor.submit(read_questions)
            assert second_started.wait(timeout=5)
            allow_capture.set()
            assert papers_future.result(timeout=10)[0]["title"] == "Paper"
            assert questions_future.result(timeout=10).total == 1
    finally:
        allow_capture.set()

    assert captures == 1


def test_reused_snapshot_opens_independent_read_only_transactions(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    barrier = Barrier(2)

    def borrow_connection() -> tuple[int, Path, int, bool]:
        with read_module._read_connection(db_path) as connection:
            candidate = next(
                Path(str(row[2]))
                for row in connection.execute("PRAGMA database_list").fetchall()
                if str(row[1]) == "main"
            )
            result = (
                id(connection),
                candidate,
                int(connection.execute("PRAGMA query_only").fetchone()[0]),
                bool(connection.in_transaction),
            )
            barrier.wait(timeout=5)
            return result

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(lambda _index: borrow_connection(), range(2)))

    assert first[0] != second[0]
    assert first[1] == second[1]
    assert first[2:] == second[2:] == (1, True)


def test_old_snapshot_is_cleaned_after_its_last_reader_exits(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO papers (id, title, import_status) VALUES (1, 'First', 'success')"
        )

    with read_module._read_connection(db_path) as old_connection:
        old_candidate = next(
            Path(str(row[2]))
            for row in old_connection.execute("PRAGMA database_list").fetchall()
            if str(row[1]) == "main"
        )
        assert old_connection.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 1
        with sqlite3.connect(db_path) as writer:
            writer.execute(
                "INSERT INTO papers (id, title, import_status) VALUES (2, 'Second', 'success')"
            )

        def read_new_generation() -> tuple[Path, int]:
            with read_module._read_connection(db_path) as new_connection:
                candidate = next(
                    Path(str(row[2]))
                    for row in new_connection.execute("PRAGMA database_list").fetchall()
                    if str(row[1]) == "main"
                )
                count = int(
                    new_connection.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
                )
                return candidate, count

        with ThreadPoolExecutor(max_workers=1) as executor:
            new_candidate, new_count = executor.submit(
                read_new_generation
            ).result(timeout=10)

        assert new_count == 2
        assert new_candidate != old_candidate
        assert new_candidate.exists()
        assert old_candidate.exists()
        assert old_connection.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 1

    assert not old_candidate.exists()
    assert new_candidate.exists()
    read_module._clear_question_read_snapshot_cache_for_tests()
    assert not new_candidate.exists()


def test_snapshot_validation_failure_is_not_cached(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO papers (id, title, import_status) VALUES (1, 'Paper', 'success')"
        )

    original_open = read_module._open_snapshot_connection
    validation_attempts = 0
    failed_candidate: Path | None = None

    def flaky_open(candidate, *args, **kwargs):
        nonlocal validation_attempts, failed_candidate
        if kwargs.get("validate_snapshot", True):
            validation_attempts += 1
            if validation_attempts == 1:
                failed_candidate = Path(candidate)
                raise QuestionBankSnapshotUnavailable(
                    "synthetic snapshot validation failure"
                )
        return original_open(candidate, *args, **kwargs)

    monkeypatch.setattr(read_module, "_open_snapshot_connection", flaky_open)
    service = QuestionBankReadService(db_path)

    with pytest.raises(QuestionBankSnapshotUnavailable):
        service.list_papers()
    assert failed_candidate is not None
    assert not failed_candidate.exists()

    assert service.list_papers()[0]["title"] == "Paper"
    assert validation_attempts == 2


def test_wal_growth_and_checkpoint_invalidate_cached_snapshot(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    writer = sqlite3.connect(db_path)
    assert writer.execute("PRAGMA journal_mode = WAL").fetchone()[0] == "wal"
    writer.execute("PRAGMA wal_autocheckpoint = 0")
    checkpoint = writer.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    assert checkpoint is not None and checkpoint[0] == 0
    writer.execute(
        "INSERT INTO papers (id, title, import_status) VALUES (1, 'First', 'success')"
    )
    writer.commit()

    captures = 0
    original_capture = read_module._capture_snapshot_attempt

    def tracked_capture(*args, **kwargs):
        nonlocal captures
        captures += 1
        return original_capture(*args, **kwargs)

    monkeypatch.setattr(read_module, "_capture_snapshot_attempt", tracked_capture)
    service = QuestionBankReadService(db_path)
    try:
        assert [item["title"] for item in service.list_papers()] == ["First"]
        assert captures == 1

        writer.execute(
            "INSERT INTO papers (id, title, import_status) VALUES (2, 'Second', 'success')"
        )
        writer.commit()
        assert {item["title"] for item in service.list_papers()} == {
            "First",
            "Second",
        }
        assert captures == 2

        checkpoint = writer.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        assert checkpoint is not None and checkpoint[0] == 0
        assert {item["title"] for item in service.list_papers()} == {
            "First",
            "Second",
        }
        assert captures == 3
    finally:
        writer.close()


def test_visible_rollback_journal_never_reuses_cached_snapshot(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO papers (id, title, import_status) VALUES (1, 'Paper', 'success')"
        )

    service = QuestionBankReadService(db_path)
    assert service.list_papers()[0]["title"] == "Paper"
    journal = Path(f"{db_path}-journal")
    journal.write_bytes(b"synthetic visible rollback journal")
    try:
        with pytest.raises(QuestionBankSnapshotBusy):
            service.list_papers()
    finally:
        journal.unlink()

    assert service.tag_value_counts("method", ["not-present"]) == {
        "not-present": 0
    }


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
