from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from question_bank.database.schema import initialize_database
from question_bank.services import question_read_service as read_module
from question_bank.services.question_read_service import (
    QuestionBankReadService,
    QuestionReadFilters,
)


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
