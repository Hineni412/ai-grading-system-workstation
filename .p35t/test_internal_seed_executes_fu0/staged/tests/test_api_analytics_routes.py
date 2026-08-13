from __future__ import annotations

import hashlib
import json
import sqlite3
import warnings
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient


warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
)


def _insert_result(
    conn: sqlite3.Connection,
    *,
    result_id: int,
    session_id: int,
    student_id: int,
    paper_id: int,
    details: list[tuple[int, str, float, str | None]],
) -> None:
    conn.execute(
        """
        INSERT INTO exam_papers (
            id, session_id, front_image, back_image, student_id,
            match_status, processing_status
        ) VALUES (?, ?, ?, ?, ?, 'matched', 'graded')
        """,
        (
            paper_id,
            session_id,
            f"C:/private/front-{paper_id}.jpg",
            f"C:/private/back-{paper_id}.jpg",
            student_id,
        ),
    )
    conn.execute(
        """
        INSERT INTO session_results (
            id, session_id, student_id, paper_id, total_score, student_score,
            needs_human_review, raw_json
        ) VALUES (?, ?, ?, ?, 20, 0, 0, '{}')
        """,
        (result_id, session_id, student_id, paper_id),
    )
    conn.executemany(
        """
        INSERT INTO session_details (
            id, result_id, question_id, score_awarded, deduction_reason,
            knowledge_ids
        ) VALUES (?, ?, ?, ?, ?, '["K"]')
        """,
        [
            (detail_id, result_id, question_id, score, reason)
            for detail_id, question_id, score, reason in details
        ],
    )


@pytest.fixture
def analysis_client(tmp_path: Path):
    from backend.api.app import create_app
    from backend.api.dependencies import get_grading_db
    from db_manager import DBManager

    db_path = tmp_path / "grading.db"
    rubric_path = tmp_path / "rubric.json"
    rubric_path.write_text(
        json.dumps(
            {
                "questions": [
                    {"question_id": "Q1", "max_score": 10, "parts": []},
                    {
                        "question_id": "Q2",
                        "max_score": 10,
                        "parts": [
                            {"part_id": "Q2(1)", "part_score": 4},
                            {"part_id": "Q2(2)", "part_score": 6},
                        ],
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    db = DBManager(db_path)
    db.initialize()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO grading_sessions (
                id, session_name, rubric_path, answer_key_path
            ) VALUES (1, '期末测试', ?, '')
            """,
            (str(rubric_path),),
        )
        conn.executemany(
            """
            INSERT INTO students (id, student_code, name, class_name)
            VALUES (?, ?, ?, ?)
            """,
            [
                (1, "S-ONE", "甲", "七年级一班"),
                (2, "S-TWO", "乙", "七年级一班"),
                (3, "S-THREE", "丙", "七年级二班"),
            ],
        )
        _insert_result(
            conn,
            result_id=1,
            session_id=1,
            student_id=1,
            paper_id=1,
            details=[(1, "Q1", 10, None), (2, "Q2(1)", 4, None)],
        )
        _insert_result(
            conn,
            result_id=2,
            session_id=1,
            student_id=2,
            paper_id=2,
            details=[(3, "Q1", 8, "计算错误"), (4, "Q2(1)", 2, "步骤缺失")],
        )
        _insert_result(
            conn,
            result_id=3,
            session_id=1,
            student_id=3,
            paper_id=3,
            details=[(5, "Q1", 7, "漏答")],
        )
        conn.commit()

    app = create_app()
    app.dependency_overrides[get_grading_db] = lambda: db
    client = TestClient(app)
    try:
        yield client, 1, db_path
    finally:
        client.close()


@pytest.fixture
def missing_max_client(tmp_path: Path):
    from backend.api.app import create_app
    from backend.api.dependencies import get_grading_db
    from db_manager import DBManager

    db_path = tmp_path / "missing-max.db"
    rubric_path = tmp_path / "rubric.json"
    rubric_path.write_text('{"questions": []}', encoding="utf-8")
    db = DBManager(db_path)
    db.initialize()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO grading_sessions (
                id, session_name, rubric_path, answer_key_path
            ) VALUES (1, '缺满分', ?, '')
            """,
            (str(rubric_path),),
        )
        conn.execute(
            """
            INSERT INTO students (id, student_code, name, class_name)
            VALUES (1, 'S1', '甲', '一班')
            """
        )
        _insert_result(
            conn,
            result_id=1,
            session_id=1,
            student_id=1,
            paper_id=1,
            details=[(1, "QX", 3, None)],
        )
        conn.commit()

    app = create_app()
    app.dependency_overrides[get_grading_db] = lambda: db
    client = TestClient(app)
    try:
        yield client, 1
    finally:
        client.close()


def test_question_analysis_filters_class_and_pages(analysis_client) -> None:
    client, session_id, _db_path = analysis_client
    response = client.get(
        f"/api/sessions/{session_id}/analysis/questions",
        params={"class_name": " 七年级一班 ", "page": 1, "page_size": 1},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["scope"]["class_name"] == "七年级一班"
    assert payload["classes"] == ["七年级一班", "七年级二班"]
    assert payload["items"][0]["attempt_count"] > 0
    assert payload["total"] == 2
    assert payload["total_pages"] == 2


def test_question_analysis_filters_question_and_empty_page(analysis_client) -> None:
    client, session_id, _db_path = analysis_client
    response = client.get(
        f"/api/sessions/{session_id}/analysis/questions",
        params={"question_id": " Q1 ", "page": 2, "page_size": 20},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["scope"]["question_id"] == "Q1"
    assert payload["total"] == 1
    assert payload["items"] == []
    assert payload["total_pages"] == 1


def test_student_analysis_encodes_evidence_url_and_question_path(analysis_client) -> None:
    client, session_id, _db_path = analysis_client
    question_id = "Q2(1)"
    response = client.get(
        f"/api/sessions/{session_id}/analysis/questions/"
        f"{quote(question_id, safe='')}/students"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["scope"]["question_id"] == question_id
    row = payload["items"][0]
    assert row["evidence_url"] == (
        f"/api/sessions/{session_id}/results/{row['result_id']}"
        f"/details/{row['detail_id']}/crop"
    )
    assert "C:/private" not in response.text


def test_student_analysis_filters_class_and_pages(analysis_client) -> None:
    client, session_id, _db_path = analysis_client
    response = client.get(
        f"/api/sessions/{session_id}/analysis/questions/Q1/students",
        params={"class_name": " 七年级二班 ", "page": 1, "page_size": 1},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["scope"]["class_name"] == "七年级二班"
    assert payload["total"] == 1
    assert payload["total_pages"] == 1
    assert payload["items"][0]["student_code"] == "S-THREE"


def test_missing_max_score_and_single_sample_remain_explicit(missing_max_client) -> None:
    client, session_id = missing_max_client
    questions = client.get(
        f"/api/sessions/{session_id}/analysis/questions"
    ).json()
    students = client.get(
        f"/api/sessions/{session_id}/analysis/questions/QX/students"
    ).json()

    assert questions["items"][0]["attempt_count"] == 1
    assert questions["items"][0]["max_score"] is None
    assert questions["items"][0]["score_rate"] is None
    assert questions["items"][0]["metric_status"] == "missing_max_score"
    assert students["items"][0]["max_score"] is None
    assert students["items"][0]["deduction_amount"] is None


def test_student_analysis_without_details_returns_empty_items(analysis_client) -> None:
    client, session_id, _db_path = analysis_client
    response = client.get(
        f"/api/sessions/{session_id}/analysis/questions/Q9/students"
    )

    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["total"] == 0


@pytest.mark.parametrize(
    "path, params",
    [
        ("questions", {"class_name": " "}),
        ("questions", {"question_id": "\t"}),
        ("questions/Q1/students", {"class_name": " "}),
    ],
)
def test_analysis_rejects_blank_text_filters(analysis_client, path, params) -> None:
    client, session_id, _db_path = analysis_client
    response = client.get(
        f"/api/sessions/{session_id}/analysis/{path}",
        params=params,
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    "path",
    [
        "/api/sessions/9999/analysis/questions",
        "/api/sessions/9999/analysis/questions/Q1/students",
    ],
)
def test_analysis_returns_404_for_missing_session(analysis_client, path) -> None:
    client, _session_id, _db_path = analysis_client
    response = client.get(path)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "session_not_found"


def test_analysis_gets_do_not_change_database_fingerprint(analysis_client) -> None:
    client, session_id, db_path = analysis_client
    before = hashlib.sha256(db_path.read_bytes()).hexdigest()

    questions = client.get(f"/api/sessions/{session_id}/analysis/questions")
    students = client.get(
        f"/api/sessions/{session_id}/analysis/questions/Q1/students"
    )

    after = hashlib.sha256(db_path.read_bytes()).hexdigest()
    assert questions.status_code == 200
    assert students.status_code == 200
    assert after == before
