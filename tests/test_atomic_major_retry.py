from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from backend.domain_models import GradingResult, QuestionGradingDetail
from db_manager import DBManager
from grading_service import GradingService
from llm_client import LLMClient
from scanner import ExamPaperGroup


RUBRIC = {
    "total_score": 15,
    "questions": [
        {"question_id": "Q1", "max_score": 5, "knowledge_id": "K1"},
        {
            "question_id": "Q10",
            "max_score": 10,
            "knowledge_id": "K10",
            "parts": [
                {"part_id": "10-1", "part_score": 4, "knowledge_id": "K10-1"},
                {"part_id": "10-2", "part_score": 6, "knowledge_id": "K10-2"},
            ],
        },
    ],
}


def _detail(
    question_id: str, score: float, knowledge_id: str = "K"
) -> QuestionGradingDetail:
    return QuestionGradingDetail(
        question_id, score, "", knowledge_id, confidence_score=99.0
    )


def _seed_retry_case(
    tmp_path: Path,
) -> tuple[DBManager, int, int, int, Path, Path, Path]:
    db_dir = tmp_path / "databases"
    db_dir.mkdir()
    db = DBManager(db_dir / "retry.db")
    db.initialize()
    session_id = db.create_grading_session("Retry", "rubric.json", "answer.json")
    exams_dir = tmp_path / "exams"
    exams_dir.mkdir()
    front_image = exams_dir / "front.jpg"
    back_image = exams_dir / "back.jpg"
    Image.new("RGB", (100, 100), "white").save(front_image)
    Image.new("RGB", (100, 100), "white").save(back_image)
    front_template = tmp_path / "front-template.png"
    back_template = tmp_path / "back-template.png"
    Image.new("RGB", (100, 100), "white").save(front_template)
    Image.new("RGB", (100, 100), "white").save(back_template)
    template_id = db.upsert_session_template(
        session_id, str(front_template), str(back_template)
    )
    regions = []
    for index, question_id in enumerate(("Q1", "10-1", "10-2"), start=1):
        regions.append(
            {
                "region_uuid": f"r-{index}",
                "page": "front",
                "region_order": index,
                "x": 0,
                "y": index * 10,
                "w": 50,
                "h": 10,
                "detected_question_id": question_id,
                "mapped_question_id": question_id,
                "confidence": 1.0,
                "is_confirmed": True,
                "mapping_status": "manual",
            }
        )
    db.replace_answer_regions_atomic(session_id, template_id, regions, confirmed=True)

    rubric_path = tmp_path / "rubric.json"
    answer_path = tmp_path / "answer.json"
    rubric_path.write_text(json.dumps(RUBRIC), encoding="utf-8")
    answer_path.write_text("{}", encoding="utf-8")
    incomplete = {
        "grading_completeness": {
            "status": "incomplete",
            "missing_question_ids": ["10-2"],
            "duplicate_question_ids": [],
            "unexpected_question_ids": [],
            "score_out_of_range": [],
            "affected_major_question_ids": ["Q10"],
        }
    }
    with db._connect() as conn:
        student_id = conn.execute(
            "INSERT INTO students (student_code, name) VALUES ('001', 'Alice')"
        ).lastrowid
        paper_id = conn.execute(
            "INSERT INTO exam_papers (session_id, front_image, back_image, ocr_name, student_id, match_status, processing_status) "
            "VALUES (?, ?, ?, 'Alice', ?, 'matched', 'graded')",
            (session_id, str(front_image), str(back_image), student_id),
        ).lastrowid
        result_id = conn.execute(
            "INSERT INTO session_results (session_id, student_id, paper_id, total_score, student_score, needs_human_review, raw_json) "
            "VALUES (?, ?, ?, 15, 8, 1, ?)",
            (session_id, student_id, paper_id, json.dumps(incomplete)),
        ).lastrowid
        conn.executemany(
            "INSERT INTO session_details (result_id, question_id, score_awarded, deduction_reason, knowledge_ids, confidence_score) "
            "VALUES (?, ?, ?, '', ?, 99)",
            [(result_id, "Q1", 5, '["K1"]'), (result_id, "10-1", 3, '["old-K10"]')],
        )
        conn.execute(
            "INSERT INTO annotated_results (session_id, result_id, annotated_front_path) VALUES (?, ?, 'annotated.png')",
            (session_id, result_id),
        )
        conn.commit()
    return db, session_id, student_id, paper_id, rubric_path, answer_path, exams_dir


def test_replace_result_details_atomic_rolls_back_delete_and_result_update(
    tmp_path: Path,
) -> None:
    db, session_id, student_id, paper_id, _, _, _ = _seed_retry_case(tmp_path)
    original = db.get_session_results(session_id)[0]
    result_id = original["result_id"]
    invalid_detail = _detail("10-2", 6)
    invalid_detail.question_id = None  # type: ignore[assignment]

    with pytest.raises(sqlite3.IntegrityError):
        db.replace_result_details_atomic(
            result_id,
            ["10-1", "10-2"],
            [_detail("10-1", 4), invalid_detail],
            rubric=RUBRIC,
            student_score=15,
            needs_human_review=False,
            raw_json={"grading_completeness": {"status": "complete"}},
        )

    preserved = db.get_session_results(session_id)[0]
    assert preserved["result_id"] == result_id
    assert preserved["student_score"] == original["student_score"]
    assert preserved["raw_json"] == original["raw_json"]
    assert [
        (d["question_id"], d["score_awarded"]) for d in db.get_result_details(result_id)
    ] == [
        ("Q1", 5.0),
        ("10-1", 3.0),
    ]


def test_retry_retains_other_question_evidence_and_recomputes_review_after_reload(
    tmp_path: Path,
) -> None:
    db, session_id, _, _, _, _, _ = _seed_retry_case(tmp_path)
    result_id = db.get_session_results(session_id)[0]["result_id"]
    previous = {
        "detail_metadata": {
            "Q1": {"recognized_answer": "A", "need_review": False},
            "10-1": {"observed_answer": "obsolete", "needs_human_review": True},
        }
    }
    with db._connect() as conn:
        conn.execute(
            "UPDATE session_results SET raw_json=? WHERE id=?",
            (json.dumps(previous), result_id),
        )
        conn.commit()
    db.replace_result_details_atomic(
        result_id,
        ["10-1", "10-2"],
        [_detail("10-1", 4), _detail("10-2", 6)],
        rubric=RUBRIC,
        student_score=15,
        needs_human_review=True,
        raw_json={
            "detail_metadata": {
                "10-1": {
                    "observed_answer": "new evidence",
                    "needs_human_review": False,
                },
                "10-2": {"needs_human_review": False},
            }
        },
    )
    stored = db.get_session_results(session_id)[0]
    raw = stored["raw_json"]
    if isinstance(raw, str):
        raw = json.loads(raw)
    assert raw["detail_metadata"]["Q1"] == previous["detail_metadata"]["Q1"]
    assert raw["detail_metadata"]["10-1"]["observed_answer"] == "new evidence"
    assert raw["grading_completeness"]["status"] == "complete"
    assert not stored["needs_human_review"]
