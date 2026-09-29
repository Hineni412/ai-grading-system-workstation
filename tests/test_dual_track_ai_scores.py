"""Dual-track score persistence: AI originals beside teacher finals."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.domain_models import GradingResult, QuestionGradingDetail
from db_manager import DBManager
from backend.repositories.grading_database import open_grading_repositories


def _detail_by_question(db: DBManager, result_id: int) -> dict[str, dict[str, object]]:
    return {str(row["question_id"]): row for row in db.results.get_result_details(result_id)}


def test_hybrid_run_grades_locked_questions_and_teacher_score_wins(
    tmp_path: Path,
) -> None:
    from grading_service import GradingService
    from ai_batch_grading_service import AIBatchRunResult, PaperEntry
    from llm_client import LLMClient
    from scanner import ExamPaperGroup

    exams_dir = tmp_path / "exams"
    exams_dir.mkdir()
    front_image = exams_dir / "front.jpg"
    front_image.touch()
    back_image = exams_dir / "back.jpg"
    back_image.touch()
    from path_manager import get_path_manager

    controlled_root = (
        get_path_manager().data_root / "config" / "uploaded" / "dual-track"
    )
    controlled_root.mkdir(parents=True, exist_ok=True)
    rubric_path = controlled_root / "rubric.json"
    rubric_path.write_text(
        json.dumps(
            {
                "questions": [
                    {"question_id": "Q1", "max_score": 5, "parts": []},
                    {"question_id": "Q2", "max_score": 5, "parts": []},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    answer_key_path = controlled_root / "answer_key.json"
    answer_key_path.write_text("{}", encoding="utf-8")

    db = open_grading_repositories(tmp_path / "hybrid-locks.db")
    db.initialize()
    session_id = db.sessions.create_grading_session(
        "混合双轨", str(rubric_path), str(answer_key_path)
    )

    from PIL import Image

    templates_root = (
        get_path_manager().data_root / "templates" / f"session_{session_id}"
    )
    templates_root.mkdir(parents=True, exist_ok=True)
    front_template = templates_root / "front.png"
    back_template = templates_root / "back.png"
    Image.new("RGB", (2831, 1960), "white").save(front_template)
    Image.new("RGB", (2800, 1900), "white").save(back_template)
    template_id = db.templates.upsert_session_template(
        session_id, str(front_template), str(back_template)
    )
    regions = [
        {
            "region_uuid": f"ru-{index}",
            "page": "front",
            "region_order": index,
            "x": 10,
            "y": 20 + 60 * (index - 1),
            "w": 100,
            "h": 50,
            "detected_question_id": question_id,
            "mapped_question_id": question_id,
            "confidence": 0.9,
            "is_confirmed": True,
            "mapping_status": "manual",
        }
        for index, question_id in enumerate(("Q1", "Q2"), start=1)
    ]
    db.templates.replace_answer_regions_atomic(session_id, template_id, regions, confirmed=True)

    with sqlite3.connect(db.db_path) as conn:
        student_id = int(
            conn.execute(
                "INSERT INTO students (student_code, name) VALUES ('001', 'Alice')"
            ).lastrowid
        )
        paper_id = int(
            conn.execute(
                """
                INSERT INTO exam_papers (
                    session_id, front_image, back_image, ocr_name, student_id,
                    match_status, processing_status
                ) VALUES (?, ?, ?, 'Alice', ?, 'matched', 'failed')
                """,
                (session_id, str(front_image), str(back_image), student_id),
            ).lastrowid
        )
        conn.commit()

    db.reviews.confirm_teacher_score_locks(
        session_id,
        "batch-1",
        [
            {
                "student_id": student_id,
                "question_id": "Q1",
                "score_awarded": 5.0,
                "max_score": 5.0,
                "deduction_reason": "教师认定满分",
                "source_target_type": "exam_paper",
                "source_target_id": paper_id,
                "expected_revision": 0,
            }
        ],
    )

    group = ExamPaperGroup(
        front_image=front_image,
        back_image=back_image,
        student_name="Alice",
        student_id=student_id,
    )
    entry = PaperEntry(
        paper_key="Alice",
        student_id=student_id,
        student_name="Alice",
        group=group,
    )
    ai_result = GradingResult(
        student_name="Alice",
        total_score=10.0,
        student_score=7.0,
        needs_human_review=False,
        grading_details=[
            QuestionGradingDetail(
                question_id="Q1",
                score_awarded=4.0,
                deduction_reason="AI 扣分",
                knowledge_id="K1",
                knowledge_ids=["K1"],
                confidence_score=95.0,
            ),
            QuestionGradingDetail(
                question_id="Q2",
                score_awarded=3.0,
                deduction_reason="计算错误",
                knowledge_id="K2",
                knowledge_ids=["K2"],
                confidence_score=92.0,
            ),
        ],
        raw_json={"Q1": {"score": 4.0}, "Q2": {"score": 3.0}},
    )
    batch_result = AIBatchRunResult(
        paper_entries=[entry],
        results_by_paper_key={"Alice": ai_result},
        fallback_items=[],
        usage_records=[],
        usage_summary={},
    )

    service = GradingService(db, MagicMock(spec=LLMClient))
    with patch(
        "grading_service.run_ai_batch_grading", return_value=batch_result
    ) as run:
        events = list(
            service.run_session_grading(
                session_id,
                exams_dir,
                rubric_path,
                answer_key_path,
                grading_mode="ai",
                scan_batch_id="batch-1",
                failed_only=True,
            )
        )

    # Locked questions are no longer excluded from the AI run.
    assert run.call_args.kwargs["skipped_questions_by_student"] == {}
    assert any(event.get("event") == "graded" for event in events)

    saved = db.results.get_session_results(session_id)[0]
    assert saved["student_score"] == 8.0
    assert saved["ai_student_score"] == 7.0
    details = _detail_by_question(db, int(saved["result_id"]))
    assert details["Q1"]["score_awarded"] == 5.0
    assert details["Q1"]["ai_score_awarded"] == 4.0
    assert details["Q2"]["score_awarded"] == 3.0
    assert details["Q2"]["ai_score_awarded"] == 3.0
