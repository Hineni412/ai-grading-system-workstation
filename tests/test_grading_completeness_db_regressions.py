from __future__ import annotations

"""Database regressions for incomplete and failed grading results."""

import json
import sqlite3
from pathlib import Path

from db_manager import DBManager


RUBRIC = {
    "questions": [
        {
            "question_id": "Q11",
            "max_score": 10,
            "parts": [
                {"part_id": "Q11(1)", "part_score": 4},
                {"part_id": "Q11(2)", "part_score": 6},
            ],
        },
        {"question_id": "Q12", "max_score": 5},
    ]
}
from backend.repositories.grading_database import open_grading_repositories


def _seed_session(
    tmp_path: Path, raw_json: dict | None = None
) -> tuple[DBManager, int, int]:
    db_path = tmp_path / "databases" / "grading.db"
    rubric_path = tmp_path / "rubric.json"
    rubric_path.write_text(json.dumps(RUBRIC, ensure_ascii=False), encoding="utf-8")
    db = open_grading_repositories(db_path)
    db.initialize()

    payload = raw_json if raw_json is not None else {}

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO grading_sessions (id, session_name, rubric_path, answer_key_path, status)
            VALUES (1, '期末测试', ?, '', 'completed')
            """,
            (str(rubric_path),),
        )
        conn.execute(
            "INSERT INTO students (id, student_code, name, class_name) VALUES (1, '001', '张三', '一班')"
        )
        conn.execute(
            """
            INSERT INTO exam_papers (
                id, session_id, front_image, back_image, ocr_name, student_id, match_status, processing_status
            ) VALUES (1, 1, 'front.jpg', 'back.jpg', '张三', 1, 'matched', 'graded')
            """
        )
        conn.execute(
            """
            INSERT INTO session_results (
                id, session_id, student_id, paper_id, total_score, student_score,
                needs_human_review, raw_json
            ) VALUES (1, 1, 1, 1, 15, 9, 1, ?)
            """,
            (json.dumps(payload, ensure_ascii=False),),
        )
        conn.executemany(
            """
            INSERT INTO session_details (
                result_id, question_id, score_awarded, deduction_reason, knowledge_ids
            ) VALUES (?, ?, ?, '', ?)
            """,
            [
                (1, "Q11(2)", 4, '["K11-2"]'),
                (1, "Q12", 5, '["K12"]'),
            ],
        )
        conn.commit()
    return db, 1, 1


def test_incomplete_result_stays_visible_after_another_failed_retry(
    tmp_path: Path,
) -> None:
    db, session_id, _paper_id = _seed_session(
        tmp_path,
        raw_json={
            "grading_completeness": {
                "status": "incomplete",
                "missing_question_ids": ["Q11(1)"],
                "duplicate_question_ids": [],
                "unexpected_question_ids": [],
                "score_out_of_range": [],
                "affected_major_question_ids": ["Q11"],
            },
            "grading_retry_attempts": [
                {
                    "status": "failed",
                    "error": "first failure",
                    "affected_major_question_ids": ["Q11"],
                    "attempted_at": "2026-06-20T09:00:00+0800",
                }
            ],
        },
    )

    db.results.record_result_retry_failure(
        1,
        {
            "status": "failed",
            "error": "second failure",
            "affected_major_question_ids": ["Q11"],
            "attempted_at": "2026-06-20T10:00:00+0800",
        },
    )

    rows = db.results.list_incomplete_results(session_id)

    assert len(rows) == 1
    assert rows[0]["status"] == "incomplete"
    assert rows[0]["retry_attempt_count"] == 2
    assert rows[0]["last_failure_reason"] == "second failure"


def test_unreadable_configured_rubric_raises_instead_of_empty_stats(tmp_path):
    """评分依据已配置但损坏：薄弱点/完整性统计必须显式报错而不是按空内容继续。"""
    import pytest

    from backend.repositories.results import RubricUnreadableError

    db, session_id, _rid = _seed_session(tmp_path)
    broken = tmp_path / "broken_rubric.json"
    broken.write_text("{not json", encoding="utf-8")
    with sqlite3.connect(db.db_path) as conn:
        conn.execute(
            "UPDATE grading_sessions SET rubric_path = ? WHERE id = ?",
            (str(broken), session_id),
        )
        conn.commit()

    with pytest.raises(RubricUnreadableError, match="broken_rubric.json"):
        db.results.list_incomplete_results(session_id)


def test_missing_configured_rubric_file_raises(tmp_path):
    """配置了评分依据但文件丢失同样显式报错。"""
    import pytest

    from backend.repositories.results import RubricUnreadableError

    db, session_id, _rid = _seed_session(tmp_path)
    with sqlite3.connect(db.db_path) as conn:
        conn.execute(
            "UPDATE grading_sessions SET rubric_path = ? WHERE id = ?",
            ("gone_rubric.json", session_id),
        )
        conn.commit()

    with pytest.raises(RubricUnreadableError, match="gone_rubric.json"):
        db.results.list_incomplete_results(session_id)


def test_missing_configured_rubric_surfaces_as_api_error(tmp_path):
    """统计接口遇缺失评分依据返回 409 rubric_unreadable，报文只含文件名。"""
    from fastapi.testclient import TestClient

    from backend.api.app import create_app
    from backend.api.dependencies import get_grading_db, get_workbench_service
    from backend.workbench.service import WorkbenchService

    db, session_id, _rid = _seed_session(tmp_path)
    with sqlite3.connect(db.db_path) as conn:
        conn.execute(
            "UPDATE grading_sessions SET rubric_path = ? WHERE id = ?",
            ("gone_rubric.json", session_id),
        )
        conn.commit()

    app = create_app()
    app.dependency_overrides[get_grading_db] = lambda: db
    app.dependency_overrides[get_workbench_service] = (
        lambda: WorkbenchService(db, None, None)
    )
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get(f"/api/sessions/{session_id}/anomalies")
    assert response.status_code == 409
    payload = response.json()["error"]
    assert payload["code"] == "rubric_unreadable"
    assert "gone_rubric.json" in payload["message"]
    assert str(tmp_path) not in payload["message"]
