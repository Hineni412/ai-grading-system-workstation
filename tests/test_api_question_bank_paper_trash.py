from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.dependencies import (
    get_question_bank_read_service,
    get_question_bank_write_service,
)
from question_bank.database.schema import connect, initialize_database
from question_bank.services.question_read_service import QuestionBankReadService
from question_bank.services.question_write_service import QuestionBankWriteService


def _client_with_paper(
    tmp_path: Path,
) -> tuple[TestClient, QuestionBankReadService, int, str]:
    data_root = tmp_path / "data"
    db_path = data_root / "databases" / "question_bank.db"
    initialize_database(db_path)
    with connect(db_path) as conn:
        paper_id = int(
            conn.execute(
                """
                INSERT INTO papers (
                    title, source_file, import_status, updated_at
                ) VALUES ('测试卷', 'raw/source.docx', 'completed', ?)
                """,
                ("2026-07-29 13:00:00.000000",),
            ).lastrowid
        )
        conn.execute(
            """
            INSERT INTO questions (
                paper_id, question_number, question_text
            ) VALUES (?, '1', '第一题')
            """,
            (paper_id,),
        )
        conn.execute(
            """
            INSERT INTO questions (
                paper_id, question_number, question_text,
                is_deleted, deleted_at
            ) VALUES (?, '2', '此前单独删除', 1, '2026-07-28 08:00:00')
            """,
            (paper_id,),
        )
    reader = QuestionBankReadService(db_path, data_root=data_root)
    writer = QuestionBankWriteService(db_path, data_root=data_root)
    app = create_app()
    app.dependency_overrides[get_question_bank_read_service] = lambda: reader
    app.dependency_overrides[get_question_bank_write_service] = lambda: writer
    return TestClient(app), reader, paper_id, "2026-07-29 13:00:00.000000"


def test_paper_trash_api_lists_and_restores_the_server_confirmed_state(
    tmp_path: Path,
) -> None:
    client, reader, paper_id, version = _client_with_paper(tmp_path)

    trashed = client.post(
        f"/api/question-bank/papers/{paper_id}/trash",
        json={"expected_updated_at": version},
    )

    assert trashed.status_code == 200
    assert trashed.json()["deleted"] is True
    assert trashed.json()["import_status"] == "deleted"
    assert trashed.json()["affected_question_count"] == 1
    assert client.get("/api/question-bank/papers").json()["items"] == []
    trash_items = client.get(
        "/api/question-bank/papers",
        params={"deleted": "true"},
    ).json()["items"]
    assert [(item["id"], item["question_count"]) for item in trash_items] == [
        (paper_id, 1)
    ]

    stale_restore = client.post(
        f"/api/question-bank/papers/{paper_id}/restore",
        json={"expected_updated_at": version},
    )
    assert stale_restore.status_code == 409
    assert stale_restore.json()["error"]["code"] == "paper_state_conflict"
    assert stale_restore.json()["error"]["details"]["deleted"] is True

    restored = client.post(
        f"/api/question-bank/papers/{paper_id}/restore",
        json={"expected_updated_at": trashed.json()["updated_at"]},
    )
    assert restored.status_code == 200
    assert restored.json()["deleted"] is False
    assert restored.json()["import_status"] == "completed"
    assert restored.json()["affected_question_count"] == 1
    assert client.get("/api/question-bank/papers").json()["total"] == 1
    assert reader.get_question(1) is not None
    assert reader.get_question(2) is None


def test_paper_permanent_delete_api_requires_impact_and_exact_phrase(
    tmp_path: Path,
) -> None:
    client, _reader, paper_id, version = _client_with_paper(tmp_path)
    trashed = client.post(
        f"/api/question-bank/papers/{paper_id}/trash",
        json={"expected_updated_at": version},
    ).json()
    selections = [{
        "id": paper_id,
        "expected_updated_at": trashed["updated_at"],
    }]

    impact = client.post(
        "/api/question-bank/papers/permanent-deletion-impact",
        json={"selections": selections},
    )
    rejected = client.post(
        "/api/question-bank/papers/permanent-delete",
        json={
            "selections": selections,
            "confirmation_phrase": "删除",
            "request_token": "b" * 32,
        },
    )
    delete_body = {
        "selections": selections,
        "confirmation_phrase": "彻底删除 1 份试卷",
        "request_token": "c" * 32,
    }
    deleted = client.post(
        "/api/question-bank/papers/permanent-delete",
        json=delete_body,
    )
    repeated = client.post(
        "/api/question-bank/papers/permanent-delete",
        json=delete_body,
    )

    assert impact.status_code == 200
    assert impact.json()["question_count"] == 2
    assert impact.json()["permanent_delete_phrase"] == "彻底删除 1 份试卷"
    assert rejected.status_code == 422
    assert deleted.status_code == 200
    assert deleted.json()["deleted_paper_ids"] == [paper_id]
    assert repeated.status_code == 200
    assert repeated.json() == deleted.json()
    assert client.get(
        "/api/question-bank/papers",
        params={"deleted": "true"},
    ).json()["items"] == []
