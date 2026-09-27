from __future__ import annotations

import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from question_bank.database.schema import initialize_database
from question_bank.services.question_write_service import (
    ConfirmedQuestionTag,
    QuestionBankWriteService,
    QuestionWriteConflict,
    QuestionImportTooLarge,
)


@pytest.fixture
def write_seed(
    tmp_path: Path,
) -> tuple[QuestionBankWriteService, int, str]:
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO papers (id, title, import_status)
            VALUES (1, 'Paper', 'success')
            """
        )
        conn.execute(
            """
            INSERT INTO questions (
                id, paper_id, question_number, question_text
            ) VALUES (1, 1, '1', 'Question')
            """
        )
        conn.execute(
            """
            INSERT INTO question_tags (
                question_id, tag_type, tag_value, confidence, source, model_name
            ) VALUES (1, 'knowledge_point', '旧标签', 0.7, 'ai', 'model-x')
            """
        )
        conn.commit()
    service = QuestionBankWriteService(
        db_path,
        data_root=tmp_path / "data",
    )
    return service, 1, service.get_revision(1)


def _load_tags(db_path: Path, question_id: int) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            """
            SELECT tag_type, tag_value, confidence, source, model_name
            FROM question_tags
            WHERE question_id = ?
            ORDER BY id
            """,
            (question_id,),
        ).fetchall()
    finally:
        conn.close()


def test_replace_tags_conflicts_instead_of_overwriting_newer_state(
    write_seed,
) -> None:
    service, question_id, revision = write_seed
    service.replace_tags(
        question_id,
        expected_revision=revision,
        tags=[ConfirmedQuestionTag("knowledge_point", "一次函数", 1.0)],
    )

    with pytest.raises(QuestionWriteConflict) as caught:
        service.replace_tags(
            question_id,
            expected_revision=revision,
            tags=[ConfirmedQuestionTag("knowledge_point", "二次函数", 1.0)],
        )

    assert len(caught.value.current_revision) == 64
    assert _load_tags(service.db_path, question_id)[0]["tag_value"] == "一次函数"


def _write_client(service: QuestionBankWriteService) -> TestClient:
    from backend.api.app import create_app
    from backend.api.dependencies import get_question_bank_write_service

    app = create_app()
    app.dependency_overrides[get_question_bank_write_service] = lambda: service
    return TestClient(app)


def test_question_delete_and_restore_routes(write_seed) -> None:
    service, question_id, revision = write_seed
    client = _write_client(service)

    deleted = client.request(
        "DELETE",
        f"/api/question-bank/questions/{question_id}",
        json={"expected_revision": revision},
    )
    restored = client.post(
        f"/api/question-bank/questions/{question_id}/restore",
        json={"expected_revision": deleted.json()["revision"]},
    )

    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert restored.status_code == 200
    assert restored.json()["deleted"] is False


def test_question_import_upload_and_request_routes(write_seed) -> None:
    service, _, _ = write_seed
    client = _write_client(service)

    uploaded = client.post(
        "/api/question-bank/import-uploads",
        params={"filename": "paper.pdf"},
        content=b"%PDF-api",
        headers={"content-type": "application/octet-stream"},
    )
    requested = client.post(
        "/api/question-bank/import-requests",
        json={"upload_id": uploaded.json()["upload_id"]},
    )

    assert uploaded.status_code == 201
    assert "path" not in uploaded.text.casefold()
    assert requested.status_code == 201
    assert requested.json()["status"] == "pending"
