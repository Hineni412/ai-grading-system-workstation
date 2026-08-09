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
from question_bank.services.question_write_service import (
    PaperPermanentDeleteDependencyConflict,
    QuestionBankWriteService,
)


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


def test_legacy_paper_trash_api_hides_then_restores_without_permanent_deletion(
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


def test_active_paper_can_be_permanently_deleted_after_impact_confirmation(
    tmp_path: Path,
) -> None:
    client, _reader, paper_id, version = _client_with_paper(tmp_path)
    selections = [{
        "id": paper_id,
        "expected_updated_at": version,
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
    assert impact.json()["analysis_record_count"] == 0
    assert impact.json()["permanent_delete_phrase"] == "彻底删除 1 份试卷"
    assert rejected.status_code == 422
    assert deleted.status_code == 200
    assert deleted.json()["deleted_paper_ids"] == [paper_id]
    assert deleted.json()["deleted_analysis_record_count"] == 0
    assert repeated.status_code == 200
    assert repeated.json() == deleted.json()
    assert client.get("/api/question-bank/papers").json()["items"] == []


def test_dependency_conflict_returns_an_actionable_api_error() -> None:
    class BlockedDeleteService:
        def preview_paper_permanent_delete(self, _selections):
            raise PaperPermanentDeleteDependencyConflict(
                "Paper deletion is blocked by dependent question-bank data"
            )

    app = create_app()
    app.dependency_overrides[get_question_bank_write_service] = (
        lambda: BlockedDeleteService()
    )
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/api/question-bank/papers/permanent-deletion-impact",
        json={
            "selections": [{
                "id": 1,
                "expected_updated_at": "2026-08-09 10:00:00.000000",
            }]
        },
    )

    assert response.status_code == 409
    assert response.json()["error"] == {
        "code": "paper_permanent_delete_dependency_conflict",
        "message": "题库中存在当前版本无法安全处理的关联数据。请先更新应用，再重新删除；本次没有删除任何内容。",
        "details": {},
        "request_id": response.headers["x-request-id"],
    }
