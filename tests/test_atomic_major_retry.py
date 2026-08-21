from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from ai_grader import GradingResult, QuestionGradingDetail
from db_manager import DBManager
from grading_service import GradingService
from hybrid_batch_grading_service import HybridBatchRunResult, PaperEntry
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


def _detail(question_id: str, score: float, knowledge_id: str = "K") -> QuestionGradingDetail:
    return QuestionGradingDetail(question_id, score, "", knowledge_id, confidence_score=99.0)


def _seed_retry_case(tmp_path: Path) -> tuple[DBManager, int, int, int, Path, Path, Path]:
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
    template_id = db.upsert_session_template(session_id, str(front_template), str(back_template))
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


def test_replace_result_details_atomic_rolls_back_delete_and_result_update(tmp_path: Path) -> None:
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
    assert [(d["question_id"], d["score_awarded"]) for d in db.get_result_details(result_id)] == [
        ("Q1", 5.0),
        ("10-1", 3.0),
    ]


def test_atomic_replace_recomputes_stale_score_and_completeness_inside_transaction(tmp_path: Path) -> None:
    db, session_id, _, _, _, _, _ = _seed_retry_case(tmp_path)
    result_id = db.get_session_results(session_id)[0]["result_id"]

    db.replace_result_details_atomic(
        result_id,
        ["10-1", "10-2"],
        [_detail("10-1", 4), _detail("10-2", 5)],
        rubric=RUBRIC,
        student_score=-999,
        needs_human_review=False,
        raw_json={"grading_completeness": {"status": "invalid"}, "caller": "stale"},
    )

    saved = db.get_session_results(session_id)[0]
    assert saved["student_score"] == 14
    assert saved["raw_json"]["grading_completeness"]["status"] == "complete"
    assert saved["raw_json"]["caller"] == "stale"


def test_atomic_replace_original_signature_without_rubric_remains_compatible(tmp_path: Path) -> None:
    db, session_id, _, _, _, _, _ = _seed_retry_case(tmp_path)
    result_id = db.get_session_results(session_id)[0]["result_id"]

    db.replace_result_details_atomic(
        result_id,
        ["10-1"],
        [_detail("10-1", 4, "new-K10")],
        student_score=-999,
        needs_human_review=False,
        raw_json={"legacy_retry": True},
    )

    saved = db.get_session_results(session_id)[0]
    assert saved["result_id"] == result_id
    assert saved["student_score"] == 9
    assert saved["raw_json"] == {"legacy_retry": True}
    assert [(detail["question_id"], detail["score_awarded"]) for detail in db.get_result_details(result_id)] == [
        ("Q1", 5.0),
        ("10-1", 4.0),
    ]


@pytest.mark.parametrize(
    "replacement_details",
    [
        [_detail("10-1", 4), _detail("10-1", 4), _detail("10-2", 5)],
        [_detail("10-1", 5), _detail("10-2", 5)],
        [_detail("10-1", 4)],
    ],
    ids=["duplicate", "out-of-range", "incomplete"],
)
def test_atomic_replace_rolls_back_non_complete_stored_replacement(
    tmp_path: Path, replacement_details: list[QuestionGradingDetail]
) -> None:
    db, session_id, _, _, _, _, _ = _seed_retry_case(tmp_path)
    original = db.get_session_results(session_id)[0]
    result_id = original["result_id"]
    original_details = db.get_result_details(result_id)

    with pytest.raises(ValueError, match="complete"):
        db.replace_result_details_atomic(
            result_id,
            ["10-1", "10-2"],
            replacement_details,
            rubric=RUBRIC,
            student_score=15,
            needs_human_review=False,
            raw_json={"grading_completeness": {"status": "complete"}},
        )

    saved = db.get_session_results(session_id)[0]
    assert saved["student_score"] == original["student_score"]
    assert saved["raw_json"] == original["raw_json"]
    assert db.get_result_details(result_id) == original_details


def test_missing_one_part_retries_and_atomically_replaces_the_whole_major(tmp_path: Path) -> None:
    db, session_id, student_id, paper_id, rubric_path, answer_path, exams_dir = _seed_retry_case(tmp_path)
    result_id = db.get_session_results(session_id)[0]["result_id"]
    group = ExamPaperGroup(
        front_image=exams_dir / "front.jpg",
        back_image=exams_dir / "back.jpg",
        student_name="Alice",
        student_id=student_id,
    )
    entry = PaperEntry("Alice", student_id, "Alice", group)
    retry_result = GradingResult(
        "Alice",
        15,
        9,
        False,
        [_detail("10-1", 4, "new-K10-1"), _detail("10-2", 5, "new-K10-2")],
        {"retry_payload": True},
    )
    batch_result = HybridBatchRunResult(
        paper_entries=[entry],
        results_by_paper_key={"Alice": retry_result},
        fallback_items=[],
        usage_records=[],
        usage_summary={},
    )
    service = GradingService(db, MagicMock(spec=LLMClient))

    with patch("grading_service.run_hybrid_batch_grading", return_value=batch_result) as run:
        events = list(
            service.run_session_grading(
                session_id,
                exams_dir,
                rubric_path,
                answer_path,
                grading_mode="hybrid_batch",
                failed_only=True,
            )
        )

    assert run.call_args.kwargs["skipped_questions_by_student"] == {student_id: {"Q1"}}
    saved = db.get_session_results(session_id)[0]
    assert saved["result_id"] == result_id
    assert saved["student_score"] == 14
    assert saved["raw_json"]["grading_completeness"]["status"] == "complete"
    details = db.get_result_details(result_id)
    assert [(d["question_id"], d["score_awarded"]) for d in details] == [
        ("Q1", 5.0),
        ("10-1", 4.0),
        ("10-2", 5.0),
    ]
    assert next(d for d in details if d["question_id"] == "Q1")["knowledge_id"] == "K1"
    with db._connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM annotated_results WHERE result_id = ?", (result_id,)
        ).fetchone()[0] == 1
    assert any(event.get("event") == "graded" and event.get("result_id") == result_id for event in events)


def test_incomplete_retry_records_attempt_and_remains_eligible_repeatedly(tmp_path: Path) -> None:
    db, session_id, student_id, paper_id, rubric_path, answer_path, exams_dir = _seed_retry_case(tmp_path)
    group = ExamPaperGroup(
        front_image=exams_dir / "front.jpg",
        back_image=exams_dir / "back.jpg",
        student_name="Alice",
        student_id=student_id,
    )
    entry = PaperEntry("Alice", student_id, "Alice", group)
    incomplete_retry = GradingResult("Alice", 15, 4, False, [_detail("10-1", 4)], {})
    batch_result = HybridBatchRunResult(
        paper_entries=[entry],
        results_by_paper_key={"Alice": incomplete_retry},
        fallback_items=[],
        usage_records=[],
        usage_summary={},
    )
    service = GradingService(db, MagicMock(spec=LLMClient))

    for expected_attempts in (1, 2):
        with patch("grading_service.run_hybrid_batch_grading", return_value=batch_result):
            list(
                service.run_session_grading(
                    session_id,
                    exams_dir,
                    rubric_path,
                    answer_path,
                    grading_mode="hybrid_batch",
                    failed_only=True,
                )
            )
        raw_json = db.get_session_results(session_id)[0]["raw_json"]
        assert raw_json["grading_completeness"]["status"] == "incomplete"
        assert len(raw_json["grading_retry_attempts"]) == expected_attempts
        assert raw_json["grading_retry_attempts"][-1]["status"] == "failed"
        assert [item["paper_id"] for item in db.list_failed_papers(session_id)] == [paper_id]


def test_batch_retry_exception_records_attempt_without_changing_scores(tmp_path: Path) -> None:
    db, session_id, _, paper_id, rubric_path, answer_path, exams_dir = _seed_retry_case(tmp_path)
    original = db.get_session_results(session_id)[0]
    original_details = db.get_result_details(original["result_id"])
    service = GradingService(db, MagicMock(spec=LLMClient))

    with patch("grading_service.run_hybrid_batch_grading", side_effect=RuntimeError("network down")):
        list(
            service.run_session_grading(
                session_id,
                exams_dir,
                rubric_path,
                answer_path,
                grading_mode="hybrid_batch",
                failed_only=True,
            )
        )

    saved = db.get_session_results(session_id)[0]
    assert saved["result_id"] == original["result_id"]
    assert saved["student_score"] == original["student_score"]
    assert db.get_result_details(saved["result_id"]) == original_details
    assert saved["raw_json"]["grading_completeness"]["status"] == "incomplete"
    assert saved["raw_json"]["grading_retry_attempts"][-1]["error"] == "network down"
    assert [item["paper_id"] for item in db.list_failed_papers(session_id)] == [paper_id]


def test_unmapped_structured_invalid_retries_all_majors_without_legacy_save(tmp_path: Path) -> None:
    db, session_id, student_id, _, rubric_path, answer_path, exams_dir = _seed_retry_case(tmp_path)
    result_id = db.get_session_results(session_id)[0]["result_id"]
    invalid_audit = {
        "status": "invalid",
        "missing_question_ids": [],
        "duplicate_question_ids": [],
        "unexpected_question_ids": ["Q99"],
        "score_out_of_range": [],
        "affected_major_question_ids": [],
    }
    with db._connect() as conn:
        conn.execute("DELETE FROM session_details WHERE result_id = ?", (result_id,))
        conn.executemany(
            "INSERT INTO session_details (result_id, question_id, score_awarded, deduction_reason, knowledge_ids, confidence_score) "
            "VALUES (?, ?, ?, '', ?, 99)",
            [
                (result_id, "Q1", 5, '["K1"]'),
                (result_id, "10-1", 4, '["K10-1"]'),
                (result_id, "10-2", 6, '["K10-2"]'),
                (result_id, "Q99", 1, '["K99"]'),
            ],
        )
        conn.execute(
            "UPDATE session_results SET student_score = 16, raw_json = ? WHERE id = ?",
            (json.dumps({"grading_completeness": invalid_audit}), result_id),
        )
        conn.commit()

    group = ExamPaperGroup(
        front_image=exams_dir / "front.jpg",
        back_image=exams_dir / "back.jpg",
        student_name="Alice",
        student_id=student_id,
    )
    batch_result = HybridBatchRunResult(
        paper_entries=[PaperEntry("Alice", student_id, "Alice", group)],
        results_by_paper_key={
            "Alice": GradingResult(
                "Alice",
                15,
                15,
                False,
                [_detail("Q1", 5), _detail("10-1", 4), _detail("10-2", 6)],
                {},
            )
        },
        fallback_items=[],
        usage_records=[],
        usage_summary={},
    )
    service = GradingService(db, MagicMock(spec=LLMClient))

    with (
        patch("grading_service.run_hybrid_batch_grading", return_value=batch_result) as run,
        patch.object(db, "save_session_result", side_effect=AssertionError("legacy save called")) as legacy_save,
    ):
        events = list(
            service.run_session_grading(
                session_id,
                exams_dir,
                rubric_path,
                answer_path,
                grading_mode="hybrid_batch",
                failed_only=True,
            )
        )

    legacy_save.assert_not_called()
    assert run.call_args.kwargs["skipped_questions_by_student"] == {student_id: set()}
    saved = db.get_session_results(session_id)[0]
    assert saved["result_id"] == result_id
    assert saved["student_score"] == 15
    assert saved["raw_json"]["grading_completeness"]["status"] == "complete"
    assert [detail["question_id"] for detail in db.get_result_details(result_id)] == ["Q1", "10-1", "10-2"]
    with db._connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM annotated_results WHERE result_id = ?", (result_id,)
        ).fetchone()[0] == 1
    assert any(event.get("event") == "graded" and event.get("result_id") == result_id for event in events)


def test_mixed_mappable_and_unmapped_invalid_retries_all_majors_and_removes_residue(tmp_path: Path) -> None:
    db, session_id, student_id, _, rubric_path, answer_path, exams_dir = _seed_retry_case(tmp_path)
    result_id = db.get_session_results(session_id)[0]["result_id"]
    with db._connect() as conn:
        conn.execute(
            "INSERT INTO session_details (result_id, question_id, score_awarded, deduction_reason, knowledge_ids, confidence_score) "
            "VALUES (?, 'Q99', 1, '', '[\"K99\"]', 99)",
            (result_id,),
        )
        conn.execute(
            "UPDATE session_results SET student_score = 9, raw_json = ? WHERE id = ?",
            (
                json.dumps(
                    {
                        "grading_completeness": {
                            "status": "invalid",
                            "missing_question_ids": ["10-2"],
                            "duplicate_question_ids": [],
                            "unexpected_question_ids": ["Q99"],
                            "score_out_of_range": [],
                            "affected_major_question_ids": ["Q10"],
                        }
                    }
                ),
                result_id,
            ),
        )
        conn.commit()

    group = ExamPaperGroup(
        front_image=exams_dir / "front.jpg",
        back_image=exams_dir / "back.jpg",
        student_name="Alice",
        student_id=student_id,
    )
    batch_result = HybridBatchRunResult(
        paper_entries=[PaperEntry("Alice", student_id, "Alice", group)],
        results_by_paper_key={
            "Alice": GradingResult(
                "Alice",
                15,
                15,
                False,
                [_detail("Q1", 5), _detail("10-1", 4), _detail("10-2", 6)],
                {},
            )
        },
        fallback_items=[],
        usage_records=[],
        usage_summary={},
    )
    service = GradingService(db, MagicMock(spec=LLMClient))

    with (
        patch("grading_service.run_hybrid_batch_grading", return_value=batch_result) as run,
        patch.object(
            db.result_repository,
            "replace_result_details_atomic",
            wraps=db.result_repository.replace_result_details_atomic,
        ) as atomic_replace,
    ):
        events = list(
            service.run_session_grading(
                session_id,
                exams_dir,
                rubric_path,
                answer_path,
                grading_mode="hybrid_batch",
                failed_only=True,
            )
        )

    assert run.call_args.kwargs["skipped_questions_by_student"] == {student_id: set()}
    assert set(atomic_replace.call_args.args[1]) == {"Q1", "10-1", "Q99"}
    saved = db.get_session_results(session_id)[0]
    assert saved["result_id"] == result_id
    assert saved["student_score"] == 15
    assert saved["raw_json"]["grading_completeness"]["status"] == "complete"
    assert [detail["question_id"] for detail in db.get_result_details(result_id)] == ["Q1", "10-1", "10-2"]
    with db._connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM annotated_results WHERE result_id = ?", (result_id,)
        ).fetchone()[0] == 1
    assert any(event.get("event") == "graded" and event.get("result_id") == result_id for event in events)


def test_fallback_retry_preserves_old_major_and_incomplete_marker(tmp_path: Path) -> None:
    db, session_id, student_id, paper_id, rubric_path, answer_path, exams_dir = _seed_retry_case(tmp_path)
    original = db.get_session_results(session_id)[0]
    original_details = db.get_result_details(original["result_id"])
    group = ExamPaperGroup(
        front_image=exams_dir / "front.jpg",
        back_image=exams_dir / "back.jpg",
        student_name="Alice",
        student_id=student_id,
    )
    batch_result = HybridBatchRunResult(
        paper_entries=[PaperEntry("Alice", student_id, "Alice", group)],
        results_by_paper_key={
            "Alice": GradingResult(
                "Alice",
                15,
                10,
                False,
                [_detail("10-1", 4), _detail("10-2", 6)],
                {},
            )
        },
        fallback_items=[{"paper_key": "Alice", "question_id": "Q10", "reason": "partial"}],
        usage_records=[],
        usage_summary={},
    )

    with patch("grading_service.run_hybrid_batch_grading", return_value=batch_result):
        list(
            GradingService(db, MagicMock(spec=LLMClient)).run_session_grading(
                session_id,
                exams_dir,
                rubric_path,
                answer_path,
                grading_mode="hybrid_batch",
                failed_only=True,
            )
        )

    saved = db.get_session_results(session_id)[0]
    assert saved["result_id"] == original["result_id"]
    assert saved["student_score"] == original["student_score"]
    assert saved["raw_json"]["grading_completeness"]["status"] == "incomplete"
    assert db.get_result_details(saved["result_id"]) == original_details
    assert [item["paper_id"] for item in db.list_failed_papers(session_id)] == [paper_id]
