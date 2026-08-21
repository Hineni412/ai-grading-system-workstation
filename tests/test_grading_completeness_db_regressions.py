from __future__ import annotations

"""Database regressions for incomplete and failed grading results."""

import json
import sqlite3
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


def _seed_session(tmp_path: Path, raw_json: dict | None = None) -> tuple[DBManager, int, int]:
    db_path = tmp_path / "databases" / "grading.db"
    rubric_path = tmp_path / "rubric.json"
    rubric_path.write_text(json.dumps(RUBRIC, ensure_ascii=False), encoding="utf-8")
    db = DBManager(db_path)
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


def test_list_incomplete_results_audits_historical_graded_rows_without_payload(tmp_path: Path) -> None:
    db, session_id, paper_id = _seed_session(tmp_path, raw_json={})

    rows = db.list_incomplete_results(session_id)

    assert len(rows) == 1
    assert rows[0]["paper_id"] == paper_id
    assert rows[0]["status"] == "incomplete"
    assert rows[0]["missing_question_ids"] == ["Q11(P1)"]
    assert rows[0]["affected_major_question_ids"] == ["Q11"]
    assert rows[0]["retry_attempt_count"] == 0
    assert [item["paper_id"] for item in db.list_failed_papers(session_id)] == [paper_id]
    assert [item["paper_id"] for item in db.list_failed_papers_detailed(session_id)] == [paper_id]


def test_incomplete_result_stays_visible_after_another_failed_retry(tmp_path: Path) -> None:
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

    db.record_result_retry_failure(
        1,
        {
            "status": "failed",
            "error": "second failure",
            "affected_major_question_ids": ["Q11"],
            "attempted_at": "2026-06-20T10:00:00+0800",
        },
    )

    rows = db.list_incomplete_results(session_id)

    assert len(rows) == 1
    assert rows[0]["status"] == "incomplete"
    assert rows[0]["retry_attempt_count"] == 2
    assert rows[0]["last_failure_reason"] == "second failure"


def test_incomplete_failure_reason_is_safe_at_db_and_render_boundaries(tmp_path: Path) -> None:
    raw_reason = (
        "模型重试失败 data:image/jpeg;base64,"
        + ("A" * 600)
        + " Bearer secret-token Authorization: Basic auth-secret api_key=secret "
        + ("oversized-payload " * 80)
    )
    db, session_id, _paper_id = _seed_session(
        tmp_path,
        raw_json={
            "grading_retry_attempts": [
                {
                    "status": "failed",
                    "error": raw_reason,
                    "affected_major_question_ids": ["Q11"],
                }
            ]
        },
    )

    incomplete_rows = db.list_incomplete_results(session_id)
    legacy_rows = db.list_failed_papers(session_id)

    assert len(incomplete_rows) == 1
    safe_summary = incomplete_rows[0]["last_failure_reason"]
    legacy_summary = legacy_rows[0]["error_message"]
    for exposed_text in (safe_summary, legacy_summary):
        lowered = exposed_text.lower()
        assert "data:image" not in lowered
        assert "base64" not in lowered
        assert "secret-token" not in lowered
        assert "auth-secret" not in lowered
        assert "api_key=secret" not in lowered
        assert "a" * 100 not in lowered
    assert len(safe_summary) <= 240
    assert "模型重试失败" in safe_summary
    assert legacy_summary == "批改结果不完整，需补跑受影响大题"


def test_failed_paper_error_is_safe_in_db_list_and_ui_row(tmp_path: Path) -> None:
    raw_error = (
        "模型请求失败 data:image/jpeg;base64,"
        + ("A" * 500)
        + " Bearer bearer-secret Authorization: Basic auth-secret api_key=api-secret"
    )
    db, session_id, _paper_id = _seed_session(tmp_path, raw_json={})
    with db._connect() as conn:
        conn.execute(
            "UPDATE exam_papers SET processing_status = 'failed', error_message = ? WHERE session_id = ?",
            (raw_error, session_id),
        )
        conn.commit()

    failed_rows = db.list_failed_papers(session_id)

    assert len(failed_rows) == 1
    list_summary = failed_rows[0]["error_message"]
    for forbidden in (
        "data:image",
        "base64",
        "bearer-secret",
        "auth-secret",
        "api_key=api-secret",
        "A" * 100,
    ):
        assert forbidden.lower() not in list_summary.lower()
    assert "模型请求失败" in list_summary
    assert len(list_summary) <= 240
