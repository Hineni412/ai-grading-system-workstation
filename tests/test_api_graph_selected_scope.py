from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.dependencies import get_request_diagnosis_profile_service
from backend.api.routers.graph import get_current_graph_query_service
from db_manager import DBManager
from integration.diagnosis_profile_service import DiagnosisProfileService
from question_bank.database.schema import connect, initialize_database
from question_bank.relations.query_service import CurrentKnowledgeGraphQueryService
from question_bank.services.source_question_link_service import (
    SourceQuestionLinkService,
)
from tests.current_knowledge_support import install_current_knowledge


def _seed_grading_db(tmp_path: Path) -> Path:
    """4 名学生 + 两场考试：乙在本次考试有证据，丙只有历史考试证据。"""
    import path_manager

    database = tmp_path / "grading.db"
    DBManager(database).initialize()
    # rubric 必须落在受控数据根目录内，否则 _load_session_rubric 拒绝读取
    rubric_dir = path_manager.get_path_manager().data_root / "rubrics"
    rubric_dir.mkdir(parents=True, exist_ok=True)
    rubric_path = rubric_dir / f"rubric-{tmp_path.name}.json"
    rubric_path.write_text(
        json.dumps(
            {
                "questions": [
                    {"question_id": "Q1", "max_score": 10, "parts": []},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with sqlite3.connect(database) as conn:
        conn.executemany(
            """
            INSERT INTO students (id, student_code, name, class_name)
            VALUES (?, ?, ?, ?)
            """,
            [
                (1, "S1", "甲", "一班"),
                (2, "S2", "乙", "一班"),
                (3, "S3", "丙", "一班"),
                (4, "S4", "丁", "一班"),
            ],
        )
        conn.executemany(
            """
            INSERT INTO grading_sessions (
                id, session_name, rubric_path, answer_key_path, created_at
            ) VALUES (?, ?, ?, '', ?)
            """,
            [
                (1, "历史考试", str(rubric_path), "2026-01-01 09:00:00"),
                (2, "当前考试", str(rubric_path), "2026-02-01 09:00:00"),
            ],
        )
        # 丙只在历史考试有成绩（触发 historical fallback）
        # 乙在当前考试有成绩；甲、丁两场都没有证据
        for result_id, session_id, student_id, paper_id, graded_at in (
            (1, 1, 3, 1, "2026-01-02 10:00:00"),
            (2, 2, 2, 2, "2026-02-02 10:00:00"),
        ):
            conn.execute(
                """
                INSERT INTO exam_papers (
                    id, session_id, front_image, back_image, student_id,
                    match_status, processing_status
                ) VALUES (?, ?, 'f.png', 'b.png', ?, 'matched', 'graded')
                """,
                (paper_id, session_id, student_id),
            )
            conn.execute(
                """
                INSERT INTO session_results (
                    id, session_id, student_id, paper_id, total_score,
                    student_score, needs_human_review, raw_json, graded_at
                ) VALUES (?, ?, ?, ?, 10, 6, 0, '{}', ?)
                """,
                (result_id, session_id, student_id, paper_id, graded_at),
            )
            conn.execute(
                """
                INSERT INTO session_details (
                    id, result_id, question_id, score_awarded,
                    deduction_reason, knowledge_ids
                ) VALUES (?, ?, 'Q1', 6, '漏写单位', '["K"]')
                """,
                (result_id, result_id),
            )
        conn.commit()
    return database


def _seed_question_bank_db(tmp_path: Path) -> Path:
    database = tmp_path / "question-bank.db"
    initialize_database(database)
    install_current_knowledge(database)
    with connect(database) as conn:
        cursor = conn.execute(
            """
            INSERT INTO questions (
                question_number, question_type, question_text, answer_text
            ) VALUES ('1', 'choice', '2x = 4, x = ?', '2')
            """
        )
        bank_question_id = int(cursor.lastrowid)
        conn.execute(
            """
            INSERT INTO question_tags (question_id, tag_type, tag_value)
            VALUES (?, 'knowledge_point', '一元一次方程')
            """,
            (bank_question_id,),
        )
    links = SourceQuestionLinkService(database)
    for session_id in (1, 2):
        links.confirm_link(
            grading_session_id=session_id,
            source_question_id="Q1",
            bank_question_id=bank_question_id,
            link_method="manual",
        )
    return database


def _client(grading_db: Path, question_bank_db: Path) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_request_diagnosis_profile_service] = lambda: (
        DiagnosisProfileService(grading_db, question_bank_db)
    )
    app.dependency_overrides[get_current_graph_query_service] = lambda: (
        CurrentKnowledgeGraphQueryService(question_bank_db)
    )
    return TestClient(app)


def test_semester_graph_and_evidence_exclude_other_terms_and_empty_scope(tmp_path):
    grading_db = _seed_grading_db(tmp_path)
    question_bank_db = _seed_question_bank_db(tmp_path)
    with sqlite3.connect(grading_db) as conn:
        conn.execute(
            "UPDATE grading_sessions SET curriculum_volume_id=CASE WHEN id=2 THEN 'bnu24-math-g8-upper' ELSE 'bnu24-math-g7-lower' END"
        )
    client = _client(grading_db, question_bank_db)
    query = {
        "scope": {"mode": "all", "use_historical_fallback": True},
        "exam_scope": {
            "mode": "semester",
            "curriculum_volume_id": "bnu24-math-g8-upper",
            "session_ids": [1],
        },
    }
    response = client.post("/api/graph/query", json=query)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["exam_scope"]["session_ids"] == [2]
    assert payload["scope"]["student_score_profiles"]["3"]["score_rate"] is None
    assert payload["scope"]["use_historical_fallback"] is False
    assert payload["nodes"]
    key = "kp_alg_linear_equation"
    for volume, expected in [
        ("bnu24-math-g8-upper", {2}),
        ("term-without-exams", set()),
        ("", set()),
    ]:
        query["exam_scope"]["curriculum_volume_id"] = volume
        evidence = client.post("/api/graph/evidence", json={**query, "stable_key": key})
        assert evidence.status_code == 200, evidence.text
        assert {row["session_id"] for row in evidence.json()["items"]} == expected
