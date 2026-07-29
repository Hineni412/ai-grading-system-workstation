from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.dependencies import get_question_bank_read_service
from question_bank.database.schema import connect, initialize_database
from question_bank.services.question_read_service import QuestionBankReadService


def test_paper_order_sorts_numeric_question_numbers_before_import_order(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    db_path = data_root / "databases" / "question_bank.db"
    initialize_database(db_path)
    with connect(db_path) as conn:
        paper_id = int(
            conn.execute(
                """
                INSERT INTO papers (title, source_file, import_status)
                VALUES ('顺序测试卷', 'raw/order.docx', 'completed')
                """
            ).lastrowid
        )
        for number in ("10", "2", "1", "附加题", ""):
            conn.execute(
                """
                INSERT INTO questions (
                    paper_id, question_number, question_text
                ) VALUES (?, ?, ?)
                """,
                (paper_id, number, f"题目 {number or '空号'}"),
            )
    reader = QuestionBankReadService(db_path, data_root=data_root)
    app = create_app()
    app.dependency_overrides[get_question_bank_read_service] = lambda: reader
    client = TestClient(app)

    first_page = client.get(
        "/api/question-bank/questions",
        params={
            "paper_ids": paper_id,
            "sort": "paper_order",
            "page": 1,
            "page_size": 3,
        },
    )
    second_page = client.get(
        "/api/question-bank/questions",
        params={
            "paper_ids": paper_id,
            "sort": "paper_order",
            "page": 2,
            "page_size": 3,
        },
    )

    assert first_page.status_code == 200
    assert second_page.status_code == 200
    assert [
        item["question_number"] for item in first_page.json()["items"]
    ] == ["1", "2", "10"]
    assert [
        item["question_number"] for item in second_page.json()["items"]
    ] == ["附加题", ""]
