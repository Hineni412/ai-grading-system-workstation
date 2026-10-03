from __future__ import annotations

import json
import sqlite3
import warnings
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
)

from fastapi.testclient import TestClient
from backend.repositories.grading_database import open_grading_repositories


def _seed_review_db(tmp_path: Path, *, result_count: int = 1):
    from backend.api.app import create_app
    from backend.api.dependencies import (
        get_annotated_dir,
        get_grading_db,
        get_job_manager,
    )
    from backend.jobs.manager import JobManager
    from backend.jobs.store import JobStore
    

    db_dir = tmp_path / "databases"
    db_dir.mkdir(parents=True, exist_ok=True)
    db = open_grading_repositories(db_dir / "grading.db")
    db.initialize()
    rubric_path = tmp_path / "rubric.json"
    rubric_path.write_text(
        json.dumps(
            {
                "questions": [
                    {
                        "question_id": "Q1",
                        "question_type": "choice",
                        "max_score": 10,
                    },
                    {
                        "question_id": "Q2",
                        "question_type": "proof",
                        "max_score": 5,
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    session_id = db.sessions.create_grading_session("Exam A", str(rubric_path), "answer.json")
    with sqlite3.connect(db.db_path) as conn:
        conn.row_factory = sqlite3.Row
        result_id = 0
        q1_detail_id = 0
        for index in range(result_count):
            student_id = int(
                conn.execute(
                    "INSERT INTO students (student_code, name, class_name) VALUES (?, ?, 'Class 1')",
                    (
                        f"S{index + 1:03d}",
                        "Alice" if index == 0 else f"Student {index + 1:03d}",
                    ),
                ).lastrowid
            )
            paper_id = int(
                conn.execute(
                    """
                    INSERT INTO exam_papers (
                        session_id, front_image, back_image, ocr_name, student_id,
                        match_status, processing_status
                    ) VALUES (?, ?, ?, ?, ?, 'matched', 'graded')
                    """,
                    (
                        session_id,
                        f"front-{index}.jpg",
                        f"back-{index}.jpg",
                        "Alice" if index == 0 else f"Student {index + 1:03d}",
                        student_id,
                    ),
                ).lastrowid
            )
            current_result_id = int(
                conn.execute(
                    """
                    INSERT INTO session_results (
                        session_id, student_id, paper_id, total_score, student_score,
                        needs_human_review, raw_json
                    ) VALUES (?, ?, ?, 15, 10, 1, ?)
                    """,
                    (
                        session_id,
                        student_id,
                        paper_id,
                        json.dumps(
                            {
                                "detail_metadata": {
                                    "Q1": {
                                        "question_id": "Q1",
                                        "evidence_steps": [
                                            "student step",
                                            str(tmp_path / "private" / "evidence.png"),
                                        ],
                                        "missing_steps": ["final conclusion"],
                                        "candidate_scores": [
                                            {
                                                "score": 8,
                                                "confidence": 0.55,
                                                "reason": "unclear handwriting",
                                                "image_path": str(
                                                    tmp_path
                                                    / "private"
                                                    / "candidate.png"
                                                ),
                                            }
                                        ],
                                        "source": "legacy-import",
                                        "image_path": str(
                                            tmp_path / "private" / "metadata.png"
                                        ),
                                        "configApiKey": "metadata-secret",
                                    }
                                }
                            },
                            ensure_ascii=False,
                        ),
                    ),
                ).lastrowid
            )
            current_detail_id = int(
                conn.execute(
                    """
                    INSERT INTO session_details (
                        result_id, question_id, score_awarded, deduction_reason, knowledge_ids,
                        error_category, error_summary, confidence_score
                    ) VALUES (?, 'Q1', 8, '需复核：字迹不清', '["K1"]', '需复核', 'unclear', 55)
                    """,
                    (current_result_id,),
                ).lastrowid
            )
            conn.execute(
                """
                INSERT INTO session_details (
                    result_id, question_id, score_awarded, deduction_reason, knowledge_ids,
                    error_category, error_summary, confidence_score
                ) VALUES (?, 'Q2', 2, '计算错误', '["K2"]', '计算错误', 'wrong', 92)
                """,
                (current_result_id,),
            )
            if index == 0:
                result_id = current_result_id
                q1_detail_id = current_detail_id
        conn.commit()

    app = create_app()
    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)
    app.dependency_overrides[get_annotated_dir] = lambda: tmp_path / "annotated"
    app.dependency_overrides[get_grading_db] = lambda: db
    app.dependency_overrides[get_job_manager] = lambda: manager
    return TestClient(app), db, session_id, result_id, q1_detail_id


@pytest.mark.parametrize("flag", [True, "true", False])
def test_results_share_only_solution_flag_for_walkthrough(tmp_path: Path, flag) -> None:
    client, db, session_id, result_id, _detail_id = _seed_review_db(tmp_path)
    with sqlite3.connect(db.db_path) as conn:
        conn.execute(
            "UPDATE session_results SET raw_json=? WHERE id=?",
            (json.dumps({"detail_metadata": {
                qid: {"alternative_solution_detected": flag,
                      "alternative_solution_summary": "private solution explanation",
                      "evidence_steps": ["private evidence"]}
                for qid in ("Q1", "Q2")
            }}), result_id),
        )
    response = client.get(f"/api/sessions/{session_id}/results-center")
    assert response.status_code == 200
    items = {item["question_id"]: item for item in response.json()["students"][0]["items"]}
    assert "alternative_solution_detected" not in items["Q1"]  # choice stays excluded
    assert items["Q2"].get("alternative_solution_detected", False) is (flag is True)
    if flag is not True:
        assert "alternative_solution_detected" not in items["Q2"]
    assert response.json()["students"][0]["current_score"] == 10
    assert "private solution" not in response.text and "private evidence" not in response.text
    assert all("metadata" not in item and "media" not in item for item in items.values())


def test_review_confirm_maps_invalid_score_to_stable_domain_error(
    tmp_path: Path,
) -> None:
    # 分数分类由应用服务测试覆盖；这里验证一种非法分的 HTTP 错误映射。
    client, _db, session_id, result_id, detail_id = _seed_review_db(tmp_path)

    response = client.post(
        f"/api/sessions/{session_id}/review/questions/Q1/confirm",
        headers={"x-request-id": "rid-review-score"},
        json={
            "items": [
                {
                    "result_id": result_id,
                    "detail_id": detail_id,
                    "score_awarded": 10.01,
                }
            ]
        },
    )

    assert response.status_code == 422
    assert response.json()["error"] == {
        "code": "review_validation_error",
        "message": "Invalid review confirmation",
        "details": {"session_id": session_id, "question_id": "Q1"},
        "request_id": "rid-review-score",
    }
