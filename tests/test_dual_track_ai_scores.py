"""Dual-track score persistence: AI originals beside teacher finals."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.domain_models import GradingResult, QuestionGradingDetail
from db_manager import DBManager


def _seed_session(tmp_path: Path) -> tuple[DBManager, int, int, int]:
    db = DBManager(tmp_path / "dual-track.db")
    db.initialize()
    session_id = db.create_grading_session("双轨测试", "rubric.json", "answer.json")
    with sqlite3.connect(db.db_path) as conn:
        student_id = int(
            conn.execute(
                "INSERT INTO students (student_code, name, class_name) "
                "VALUES ('001', '张三', '一班')"
            ).lastrowid
        )
        paper_id = int(
            conn.execute(
                """
                INSERT INTO exam_papers (
                    session_id, front_image, back_image, ocr_name, student_id,
                    match_status, processing_status
                ) VALUES (?, 'front.jpg', 'back.jpg', '张三', ?, 'matched', 'graded')
                """,
                (session_id, student_id),
            ).lastrowid
        )
        conn.commit()
    return db, session_id, student_id, paper_id


def _ai_result() -> GradingResult:
    return GradingResult(
        student_name="张三",
        total_score=10.0,
        student_score=7.0,
        needs_human_review=False,
        grading_details=[
            QuestionGradingDetail(
                question_id="Q1",
                score_awarded=4.0,
                deduction_reason="过程不完整",
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


def _detail_by_question(
    db: DBManager, result_id: int
) -> dict[str, dict[str, object]]:
    return {
        str(row["question_id"]): row
        for row in db.get_result_details(result_id)
    }


def _result_row(db: DBManager, result_id: int) -> sqlite3.Row:
    with sqlite3.connect(db.db_path) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            "SELECT * FROM session_results WHERE id = ?",
            (result_id,),
        ).fetchone()


def test_migration_adds_dual_track_columns(tmp_path: Path) -> None:
    db = DBManager(tmp_path / "columns.db")
    db.initialize()
    with sqlite3.connect(db.db_path) as conn:
        detail_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(session_details)")
        }
        result_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(session_results)")
        }
    assert "ai_score_awarded" in detail_columns
    assert "ai_student_score" in result_columns


def test_save_session_result_records_ai_track(tmp_path: Path) -> None:
    db, session_id, student_id, paper_id = _seed_session(tmp_path)

    result_id = db.result_repository.save_session_result(
        session_id, student_id, paper_id, _ai_result()
    )

    details = _detail_by_question(db, result_id)
    assert details["Q1"]["score_awarded"] == 4.0
    assert details["Q1"]["ai_score_awarded"] == 4.0
    assert details["Q2"]["score_awarded"] == 3.0
    assert details["Q2"]["ai_score_awarded"] == 3.0
    row = _result_row(db, result_id)
    assert row["student_score"] == 7.0
    assert row["ai_student_score"] == 7.0


def test_teacher_lock_merge_preserves_ai_score(tmp_path: Path) -> None:
    db, session_id, student_id, paper_id = _seed_session(tmp_path)
    db.confirm_teacher_score_locks(
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

    result_id = db.result_repository.save_session_result(
        session_id, student_id, paper_id, _ai_result(), scan_batch_id="batch-1"
    )

    details = _detail_by_question(db, result_id)
    # Teacher score wins the final column; the AI original survives beside it.
    assert details["Q1"]["score_awarded"] == 5.0
    assert details["Q1"]["ai_score_awarded"] == 4.0
    assert details["Q2"]["score_awarded"] == 3.0
    assert details["Q2"]["ai_score_awarded"] == 3.0
    row = _result_row(db, result_id)
    assert row["student_score"] == 8.0
    assert row["ai_student_score"] == 7.0


def test_teacher_lock_without_ai_detail_has_null_ai_score(tmp_path: Path) -> None:
    db, session_id, student_id, paper_id = _seed_session(tmp_path)
    db.confirm_teacher_score_locks(
        session_id,
        "batch-1",
        [
            {
                "student_id": student_id,
                "question_id": "Q3",
                "score_awarded": 2.0,
                "max_score": 5.0,
                "deduction_reason": None,
                "source_target_type": "exam_paper",
                "source_target_id": paper_id,
                "expected_revision": 0,
            }
        ],
    )

    result_id = db.result_repository.save_session_result(
        session_id, student_id, paper_id, _ai_result(), scan_batch_id="batch-1"
    )

    details = _detail_by_question(db, result_id)
    assert details["Q3"]["score_awarded"] == 2.0
    assert details["Q3"]["ai_score_awarded"] is None
    row = _result_row(db, result_id)
    assert row["student_score"] == 9.0
    # The AI run only produced Q1/Q2 scores.
    assert row["ai_student_score"] == 7.0


def test_confirm_teacher_score_lock_keeps_ai_columns(tmp_path: Path) -> None:
    db, session_id, student_id, paper_id = _seed_session(tmp_path)
    result_id = db.result_repository.save_session_result(
        session_id, student_id, paper_id, _ai_result(), scan_batch_id="batch-1"
    )
    detail_id = int(_detail_by_question(db, result_id)["Q1"]["detail_id"])

    db.confirm_teacher_score_locks(
        session_id,
        "batch-1",
        [
            {
                "student_id": student_id,
                "question_id": "Q1",
                "score_awarded": 5.0,
                "max_score": 5.0,
                "deduction_reason": "教师改判满分",
                "source_target_type": "session_detail",
                "source_target_id": detail_id,
                "expected_revision": 0,
                "result_id": result_id,
                "detail_id": detail_id,
            }
        ],
    )

    details = _detail_by_question(db, result_id)
    assert details["Q1"]["score_awarded"] == 5.0
    assert details["Q1"]["deduction_reason"] == "教师改判满分"
    assert details["Q1"]["ai_score_awarded"] == 4.0
    row = _result_row(db, result_id)
    assert row["student_score"] == 8.0
    assert row["ai_student_score"] == 7.0


def _seed_legacy_result_without_ai(
    db: DBManager, session_id: int, student_id: int, paper_id: int
) -> tuple[int, int, int]:
    """Simulate pre-migration rows: ai columns are NULL."""
    with sqlite3.connect(db.db_path) as conn:
        result_id = int(
            conn.execute(
                """
                INSERT INTO session_results (
                    session_id, student_id, paper_id, total_score, student_score,
                    needs_human_review, raw_json
                ) VALUES (?, ?, ?, 10, 7, 0, '{}')
                """,
                (session_id, student_id, paper_id),
            ).lastrowid
        )
        ai_detail_id = int(
            conn.execute(
                """
                INSERT INTO session_details (
                    result_id, question_id, score_awarded, deduction_reason,
                    knowledge_ids, confidence_score
                ) VALUES (?, 'Q1', 4.0, '过程不完整', '["K1"]', 95.0)
                """,
                (result_id,),
            ).lastrowid
        )
        manual_detail_id = int(
            conn.execute(
                """
                INSERT INTO session_details (
                    result_id, question_id, score_awarded, deduction_reason,
                    knowledge_ids
                ) VALUES (?, 'Q2', 3.0, NULL, '["K2"]')
                """,
                (result_id,),
            ).lastrowid
        )
        conn.commit()
    return result_id, ai_detail_id, manual_detail_id


def test_review_adjustments_backfill_ai_score_once(tmp_path: Path) -> None:
    db, session_id, student_id, paper_id = _seed_session(tmp_path)
    result_id, ai_detail_id, manual_detail_id = _seed_legacy_result_without_ai(
        db, session_id, student_id, paper_id
    )

    outcome = db.apply_session_review_adjustments(
        session_id,
        [
            {
                "session_id": session_id,
                "result_id": result_id,
                "detail_id": ai_detail_id,
                "question_id": "Q1",
                "score_awarded": 5.0,
                "deduction_reason": "教师复核通过",
            },
            {
                "session_id": session_id,
                "result_id": result_id,
                "detail_id": manual_detail_id,
                "question_id": "Q2",
                "score_awarded": 2.0,
                "deduction_reason": "教师扣分",
            },
        ],
    )

    assert outcome == {"updated_details": 2, "updated_results": 1}
    details = _detail_by_question(db, result_id)
    # AI-produced row: the old score is preserved as the AI original.
    assert details["Q1"]["score_awarded"] == 5.0
    assert details["Q1"]["ai_score_awarded"] == 4.0
    # Manual-looking row (no AI traces): nothing to preserve.
    assert details["Q2"]["score_awarded"] == 2.0
    assert details["Q2"]["ai_score_awarded"] is None

    # A second adjustment must not overwrite the preserved AI original.
    db.apply_session_review_adjustments(
        session_id,
        [
            {
                "session_id": session_id,
                "result_id": result_id,
                "detail_id": ai_detail_id,
                "question_id": "Q1",
                "score_awarded": 4.5,
                "deduction_reason": "再次复核",
            }
        ],
    )
    details = _detail_by_question(db, result_id)
    assert details["Q1"]["score_awarded"] == 4.5
    assert details["Q1"]["ai_score_awarded"] == 4.0


def test_batch_score_updates_backfill_ai_score(tmp_path: Path) -> None:
    db, session_id, student_id, paper_id = _seed_session(tmp_path)
    result_id, ai_detail_id, _manual_detail_id = _seed_legacy_result_without_ai(
        db, session_id, student_id, paper_id
    )

    outcome = db.review_repository.update_session_detail_scores(
        session_id,
        [{"detail_id": ai_detail_id, "score_awarded": 5.0}],
    )

    assert outcome == {"updated_details": 1, "updated_results": 1}
    details = _detail_by_question(db, result_id)
    assert details["Q1"]["score_awarded"] == 5.0
    assert details["Q1"]["ai_score_awarded"] == 4.0


def test_hybrid_run_grades_locked_questions_and_teacher_score_wins(
    tmp_path: Path,
) -> None:
    from grading_service import GradingService
    from hybrid_batch_grading_service import HybridBatchRunResult, PaperEntry
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

    db = DBManager(tmp_path / "hybrid-locks.db")
    db.initialize()
    session_id = db.create_grading_session(
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
    template_id = db.upsert_session_template(
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
    db.replace_answer_regions_atomic(session_id, template_id, regions, confirmed=True)

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

    db.confirm_teacher_score_locks(
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
    batch_result = HybridBatchRunResult(
        paper_entries=[entry],
        results_by_paper_key={"Alice": ai_result},
        fallback_items=[],
        usage_records=[],
        usage_summary={},
    )

    service = GradingService(db, MagicMock(spec=LLMClient))
    with patch(
        "grading_service.run_hybrid_batch_grading", return_value=batch_result
    ) as run:
        events = list(
            service.run_session_grading(
                session_id,
                exams_dir,
                rubric_path,
                answer_key_path,
                grading_mode="hybrid_batch",
                scan_batch_id="batch-1",
                failed_only=True,
            )
        )

    # Locked questions are no longer excluded from the AI run.
    assert run.call_args.kwargs["skipped_questions_by_student"] == {}
    assert any(event.get("event") == "graded" for event in events)

    saved = db.get_session_results(session_id)[0]
    assert saved["student_score"] == 8.0
    assert saved["ai_student_score"] == 7.0
    details = _detail_by_question(db, int(saved["result_id"]))
    assert details["Q1"]["score_awarded"] == 5.0
    assert details["Q1"]["ai_score_awarded"] == 4.0
    assert details["Q2"]["score_awarded"] == 3.0
    assert details["Q2"]["ai_score_awarded"] == 3.0
