from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _insert_result(
    conn: sqlite3.Connection,
    *,
    result_id: int,
    session_id: int,
    student_id: int,
    paper_id: int,
    graded_at: str,
    student_score: float,
    total_score: float,
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
            needs_human_review, raw_json, graded_at
        ) VALUES (?, ?, ?, ?, ?, ?, 0, '{}', ?)
        """,
        (
            result_id,
            session_id,
            student_id,
            paper_id,
            total_score,
            student_score,
            graded_at,
        ),
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
def exam_results_client(tmp_path: Path):
    from backend.api.app import create_app
    from backend.api.dependencies import (
        get_grading_db,
        get_question_bank_db_path,
    )
    from db_manager import DBManager

    db_dir = tmp_path / "databases"
    db_dir.mkdir()
    db_path = db_dir / "grading.db"
    question_bank_db_path = db_dir / "question_bank.db"
    rubric_path = tmp_path / "rubric.json"
    rubric_path.write_text(
        json.dumps(
            {
                "questions": [
                    {"question_id": "Q1", "max_score": 10, "parts": []},
                    {"question_id": "Q2", "max_score": 10, "parts": []},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    db = DBManager(db_path)
    db.initialize()
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO grading_sessions (
                id, session_name, rubric_path, answer_key_path, created_at
            ) VALUES (?, ?, ?, '', ?)
            """,
            [
                (1, "上学期期末", str(rubric_path), "2025-01-10 09:00:00"),
                (2, "本月月考", str(rubric_path), "2025-03-05 09:00:00"),
            ],
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
            graded_at="2025-01-12 10:00:00",
            student_score=16,
            total_score=20,
            details=[(1, "Q1", 10, None), (2, "Q2", 6, "计算错误")],
        )
        _insert_result(
            conn,
            result_id=2,
            session_id=2,
            student_id=1,
            paper_id=2,
            graded_at="2025-03-06 10:00:00",
            student_score=17,
            total_score=20,
            details=[(3, "Q1", 7, "漏答"), (4, "Q2", 10, None)],
        )
        _insert_result(
            conn,
            result_id=3,
            session_id=2,
            student_id=2,
            paper_id=3,
            graded_at="2025-03-06 10:00:00",
            student_score=8,
            total_score=20,
            details=[(5, "Q1", 8, "步骤缺失")],
        )
        conn.commit()

    app = create_app()
    app.dependency_overrides[get_grading_db] = lambda: db
    app.dependency_overrides[get_question_bank_db_path] = (
        lambda: question_bank_db_path
    )
    client = TestClient(app)
    try:
        yield client
    finally:
        client.close()


def _seed_question_bank(question_bank_db_path: Path) -> int:
    from question_bank.database.schema import connect, initialize_database

    initialize_database(question_bank_db_path)
    with connect(question_bank_db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO questions (
                question_number, question_type, question_text, answer_text
            ) VALUES ('1', 'choice', '1 + 1 = ?', 'B')
            """
        )
    return int(cursor.lastrowid)


def test_exam_results_default_only_deducted_sorted_desc(exam_results_client) -> None:
    response = exam_results_client.get("/api/students/1/exam-results")

    assert response.status_code == 200
    payload = response.json()
    assert payload["student"]["id"] == 1
    assert payload["student"]["student_code"] == "S-ONE"
    assert payload["student"]["name"] == "甲"
    assert payload["student"]["class_name"] == "七年级一班"
    assert payload["total_sessions"] == 2
    assert payload["page"] == 1
    assert payload["page_size"] == 10
    assert payload["total_pages"] == 1

    sessions = payload["sessions"]
    assert [item["session_id"] for item in sessions] == [2, 1]
    assert sessions[0]["session_name"] == "本月月考"
    assert sessions[0]["graded_at"] == "2025-03-06 10:00:00"
    assert sessions[0]["exam_created_at"] == "2025-03-05 09:00:00"
    assert sessions[0]["result_id"] == 2
    assert sessions[0]["student_score"] == 17
    assert sessions[0]["total_score"] == 20

    items = sessions[0]["items"]
    assert len(items) == 1
    assert items[0]["question_id"] == "Q1"
    assert items[0]["score_awarded"] == 7
    assert items[0]["max_score"] == 10
    assert items[0]["deduction_amount"] == 3
    assert items[0]["deduction_reason"] == "漏答"
    assert items[0]["evidence_url"] == (
        f"/api/sessions/2/results/2/details/{items[0]['detail_id']}/crop"
    )
    assert sessions[1]["items"][0]["question_id"] == "Q2"
    assert "C:/private" not in response.text


def test_exam_results_semester_filter_excludes_old_and_unassigned(exam_results_client, tmp_path):
    with sqlite3.connect(tmp_path / "databases" / "grading.db") as conn:
        conn.execute("UPDATE grading_sessions SET curriculum_volume_id = 'current' WHERE id = 2")
        conn.execute("UPDATE grading_sessions SET curriculum_volume_id = 'old' WHERE id = 1")
    for volume, expected in [("current", [2]), ("old", [1]), ("empty", []), ("", [])]:
        response = exam_results_client.get(
            "/api/students/1/exam-results", params={"curriculum_volume_id": volume}
        )
        assert response.status_code == 200
        assert [item["session_id"] for item in response.json()["sessions"]] == expected
        assert response.json()["total_sessions"] == len(expected)
    with sqlite3.connect(tmp_path / "databases" / "grading.db") as conn:
        conn.execute("UPDATE grading_sessions SET curriculum_volume_id = NULL WHERE id = 2")
    assert exam_results_client.get(
        "/api/students/1/exam-results", params={"curriculum_volume_id": "current"}
    ).json()["sessions"] == []
    assert exam_results_client.get("/api/students/1/exam-results").json()["total_sessions"] == 2


def test_exam_results_only_deducted_false_returns_all_items(
    exam_results_client,
) -> None:
    response = exam_results_client.get(
        "/api/students/1/exam-results",
        params={"only_deducted": "false"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_sessions"] == 2
    latest = payload["sessions"][0]
    assert latest["session_id"] == 2
    assert [item["question_id"] for item in latest["items"]] == ["Q1", "Q2"]


def test_exam_results_paginates_by_session(exam_results_client) -> None:
    first = exam_results_client.get(
        "/api/students/1/exam-results",
        params={"page": 1, "page_size": 1},
    ).json()
    second = exam_results_client.get(
        "/api/students/1/exam-results",
        params={"page": 2, "page_size": 1},
    ).json()

    assert first["total_sessions"] == 2
    assert first["total_pages"] == 2
    assert [item["session_id"] for item in first["sessions"]] == [2]
    assert [item["session_id"] for item in second["sessions"]] == [1]


def test_exam_results_excludes_other_students(exam_results_client) -> None:
    payload = exam_results_client.get("/api/students/2/exam-results").json()

    assert payload["total_sessions"] == 1
    assert payload["sessions"][0]["result_id"] == 3
    assert payload["sessions"][0]["items"][0]["detail_id"] == 5


def test_exam_results_returns_empty_for_student_without_history(
    exam_results_client,
) -> None:
    response = exam_results_client.get("/api/students/3/exam-results")

    assert response.status_code == 200
    payload = response.json()
    assert payload["student"]["id"] == 3
    assert payload["sessions"] == []
    assert payload["total_sessions"] == 0
    assert payload["total_pages"] == 0


def test_exam_results_returns_404_for_missing_student(exam_results_client) -> None:
    response = exam_results_client.get("/api/students/9999/exam-results")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "student_not_found"


def test_exam_results_includes_bank_question_id_for_confirmed_links(
    exam_results_client,
    tmp_path: Path,
) -> None:
    from question_bank.services.source_question_link_service import (
        SourceQuestionLinkService,
    )

    question_bank_db_path = tmp_path / "databases" / "question_bank.db"
    bank_question_id = _seed_question_bank(question_bank_db_path)
    links = SourceQuestionLinkService(question_bank_db_path)
    links.confirm_link(
        grading_session_id=2,
        source_question_id="Q1",
        bank_question_id=bank_question_id,
        link_method="manual",
    )
    links.suggest_link(
        grading_session_id=1,
        source_question_id="Q2",
        bank_question_id=bank_question_id,
        confidence=0.9,
        link_method="text_similarity",
    )

    response = exam_results_client.get("/api/students/1/exam-results")

    assert response.status_code == 200
    payload = response.json()
    latest = payload["sessions"][0]
    assert latest["session_id"] == 2
    assert latest["items"][0]["question_id"] == "Q1"
    assert latest["items"][0]["bank_question_id"] == bank_question_id
    earlier = payload["sessions"][1]
    assert earlier["session_id"] == 1
    assert earlier["items"][0]["question_id"] == "Q2"
    # 只采纳 status=confirmed 的关联，suggested 不出现在结果里
    assert earlier["items"][0]["bank_question_id"] is None


def test_exam_results_bank_question_id_null_without_question_bank(
    exam_results_client,
) -> None:
    response = exam_results_client.get(
        "/api/students/1/exam-results",
        params={"only_deducted": "false"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_sessions"] == 2
    for session in payload["sessions"]:
        for item in session["items"]:
            assert item["bank_question_id"] is None


def test_exam_results_sub_question_falls_back_to_parent_link(
    exam_results_client,
    tmp_path: Path,
) -> None:
    """Sub-question detail ids (``Q11(P1)``) reuse the parent link (``Q11``).

    题库同步按 rubric 母题（Q11）建立 confirmed 关联，而主观题明细行的
    question_id 是子题号（Q11(P1)）。精确匹配失败时必须回退到母题号，
    否则学生证据页主观题永远没有“原题”按钮。
    """
    from question_bank.services.source_question_link_service import (
        SourceQuestionLinkService,
    )

    grading_db_path = tmp_path / "databases" / "grading.db"
    with sqlite3.connect(grading_db_path) as conn:
        conn.execute(
            """
            INSERT INTO grading_sessions (
                id, session_name, rubric_path, answer_key_path, created_at
            ) VALUES (3, '期中考试', '', '', '2025-04-01 09:00:00')
            """
        )
        _insert_result(
            conn,
            result_id=10,
            session_id=3,
            student_id=1,
            paper_id=10,
            graded_at="2025-04-02 10:00:00",
            student_score=12,
            total_score=20,
            details=[
                (10, "Q11(P1)", 6, "步骤缺失"),
                (11, "Q11(P2)", 6, None),
                (12, "Q12(P1)", 0, "未作答"),
            ],
        )
        conn.commit()

    question_bank_db_path = tmp_path / "databases" / "question_bank.db"
    bank_question_id = _seed_question_bank(question_bank_db_path)
    links = SourceQuestionLinkService(question_bank_db_path)
    links.confirm_link(
        grading_session_id=3,
        source_question_id="Q11",
        bank_question_id=bank_question_id,
        link_method="manual",
    )

    response = exam_results_client.get(
        "/api/students/1/exam-results",
        params={"only_deducted": "false"},
    )

    assert response.status_code == 200
    payload = response.json()
    latest = payload["sessions"][0]
    assert latest["session_id"] == 3
    items = {item["question_id"]: item for item in latest["items"]}
    # 子题精确匹配不到 link，回退母题 Q11 命中
    assert items["Q11(P1)"]["bank_question_id"] == bank_question_id
    assert items["Q11(P2)"]["bank_question_id"] == bank_question_id
    # 母题也没有 link 的子题仍然为 None
    assert items["Q12(P1)"]["bank_question_id"] is None
