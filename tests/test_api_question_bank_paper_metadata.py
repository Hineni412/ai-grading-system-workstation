from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.dependencies import get_question_bank_write_service
from question_bank.database.schema import connect, initialize_database
from question_bank.services.question_write_service import QuestionBankWriteService


def _client_with_paper(
    tmp_path: Path,
) -> tuple[TestClient, int, str]:
    data_root = tmp_path / "data"
    db_path = data_root / "databases" / "question_bank.db"
    initialize_database(db_path)
    with connect(db_path) as conn:
        paper_id = int(
            conn.execute(
                """
                INSERT INTO papers (
                    title, source_file, year, exam_type, import_status
                ) VALUES (
                    'source', 'question_bank/raw_papers/0526test2.docx',
                    NULL, NULL, 'success'
                )
                """
            ).lastrowid
        )
        updated_at = str(
            conn.execute(
                "SELECT updated_at FROM papers WHERE id = ?",
                (paper_id,),
            ).fetchone()["updated_at"]
        )
    service = QuestionBankWriteService(db_path, data_root=data_root)
    app = create_app()
    app.dependency_overrides[get_question_bank_write_service] = lambda: service
    return TestClient(app), paper_id, updated_at


def _payload(updated_at: str, *, title: str) -> dict[str, object]:
    return {
        "expected_updated_at": updated_at,
        "metadata": {
            "title": title,
            "year": "2026",
            "province": "广东省",
            "city": "深圳市",
            "district": None,
            "exam_type": "阶段练习",
            "grade": "七年级",
            "semester": "下学期",
            "textbook_version": "北师大版 2024",
        },
    }


def test_paper_metadata_patch_returns_the_server_confirmed_card_values(
    tmp_path: Path,
) -> None:
    client, paper_id, updated_at = _client_with_paper(tmp_path)

    response = client.patch(
        f"/api/question-bank/papers/{paper_id}",
        json=_payload(updated_at, title="0526test2"),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == paper_id
    assert body["title"] == "0526test2"
    assert body["year"] == "2026"
    assert body["exam_type"] == "阶段练习"
    assert body["updated_at"] != updated_at
    assert "source_file" not in body


def test_paper_metadata_patch_reports_a_stale_edit_without_overwriting_it(
    tmp_path: Path,
) -> None:
    client, paper_id, updated_at = _client_with_paper(tmp_path)
    first = client.patch(
        f"/api/question-bank/papers/{paper_id}",
        json=_payload(updated_at, title="先保存的名称"),
    )
    assert first.status_code == 200

    stale = client.patch(
        f"/api/question-bank/papers/{paper_id}",
        json=_payload(updated_at, title="旧窗口覆盖"),
    )

    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "paper_metadata_conflict"
    assert stale.json()["error"]["details"]["current_updated_at"] == (
        first.json()["updated_at"]
    )


def test_paper_metadata_patch_rejects_a_blank_title(
    tmp_path: Path,
) -> None:
    client, paper_id, updated_at = _client_with_paper(tmp_path)

    response = client.patch(
        f"/api/question-bank/papers/{paper_id}",
        json=_payload(updated_at, title="   "),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "paper_metadata_invalid"
