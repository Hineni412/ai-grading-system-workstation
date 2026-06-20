import pytest
from pathlib import Path
from db_manager import DBManager

def test_list_failed_papers(tmp_path):
    db = DBManager(tmp_path / "test.db")
    db.initialize()
    
    session_id = db.create_grading_session("Test Session", "rubric.json", "answer.json")
    
    # insert some students
    with db._connect() as conn:
        s1_id = conn.execute("INSERT INTO students (student_code, name) VALUES ('001', 'Alice')").lastrowid
        s2_id = conn.execute("INSERT INTO students (student_code, name) VALUES ('002', 'Bob')").lastrowid
        s3_id = conn.execute("INSERT INTO students (student_code, name) VALUES ('003', 'Charlie')").lastrowid
        s4_id = conn.execute("INSERT INTO students (student_code, name) VALUES ('004', 'David')").lastrowid
        
        # insert a failed paper, a successful paper, a partially failed paper, and a stuck grading paper
        conn.execute(
            "INSERT INTO exam_papers (session_id, front_image, back_image, ocr_name, student_id, match_status, processing_status, error_message) "
            "VALUES (?, 'f1.jpg', 'b1.jpg', 'Alice', ?, 'matched', 'failed', 'Connection error')",
            (session_id, s1_id)
        )
        conn.execute(
            "INSERT INTO exam_papers (session_id, front_image, back_image, ocr_name, student_id, match_status, processing_status) "
            "VALUES (?, 'f2.jpg', 'b2.jpg', 'Bob', ?, 'matched', 'graded')",
            (session_id, s2_id)
        )
        p3_id = conn.execute(
            "INSERT INTO exam_papers (session_id, front_image, back_image, ocr_name, student_id, match_status, processing_status) "
            "VALUES (?, 'f3.jpg', 'b3.jpg', 'Charlie', ?, 'matched', 'graded')",
            (session_id, s3_id)
        ).lastrowid
        conn.execute(
            "INSERT INTO exam_papers (session_id, front_image, back_image, ocr_name, student_id, match_status, processing_status) "
            "VALUES (?, 'f4.jpg', 'b4.jpg', 'David', ?, 'matched', 'grading')",
            (session_id, s4_id)
        )
        
        # Insert session result for Charlie with hybrid_batch_fallback
        import json
        conn.execute(
            "INSERT INTO session_results (session_id, paper_id, student_id, total_score, student_score, needs_human_review, raw_json) "
            "VALUES (?, ?, ?, 100.0, 80.0, 1, ?)",
            (session_id, p3_id, s3_id, json.dumps({"hybrid_batch_fallback": "some_fallback"}))
        )
        conn.commit()
        
    # Set session status to 'failed' via raw SQL to test dynamic query behavior without triggering database reset
    with db._connect() as conn:
        conn.execute("UPDATE grading_sessions SET status = 'failed' WHERE id = ?", (session_id,))
        conn.commit()
        
    failed = db.list_failed_papers(session_id)
    assert len(failed) == 3
    # Sort by student_name/ocr_name to make assertion order deterministic
    failed = sorted(failed, key=lambda x: x["student_name"])
    assert failed[0]["student_name"] == "Alice"
    assert failed[0]["error_message"] == "Connection error"
    assert failed[1]["student_name"] == "Charlie"
    assert failed[1]["error_message"] == "AI批改部分大题缺失，需重新发AI批改"
    assert failed[2]["student_name"] == "David"
    assert failed[2]["error_message"] == "批改任务异常中断，需重新批改"
    
    # If session status is updated to 'running', the 'grading' paper is excluded (since it's actively processing)
    with db._connect() as conn:
        conn.execute("UPDATE grading_sessions SET status = 'running' WHERE id = ?", (session_id,))
        conn.commit()
    failed = db.list_failed_papers(session_id)
    assert len(failed) == 2
    
    # Test update_session_status database reset logic
    with db._connect() as conn:
        conn.execute("UPDATE grading_sessions SET status = 'running' WHERE id = ?", (session_id,))
        conn.execute("UPDATE exam_papers SET processing_status = 'grading' WHERE session_id = ? AND ocr_name = 'David'", (session_id,))
        conn.commit()
        
    db.update_session_status(session_id, "failed")
    with db._connect() as conn:
        row = conn.execute("SELECT processing_status, error_message FROM exam_papers WHERE session_id = ? AND ocr_name = 'David'", (session_id,)).fetchone()
        assert row["processing_status"] == "failed"
        assert row["error_message"] == "批改中途被终止或强制重置"
        
    # Restoring status to 'failed' and running try_start_session_run
    with db._connect() as conn:
        conn.execute("UPDATE grading_sessions SET status = 'failed' WHERE id = ?", (session_id,))
        conn.execute("UPDATE exam_papers SET processing_status = 'grading' WHERE session_id = ? AND ocr_name = 'David'", (session_id,))
        conn.commit()
        
    # try_start_session_run should succeed and reset David to 'failed'
    assert db.try_start_session_run(session_id) is True
    
    # Verify David's status is now 'failed' in the database
    with db._connect() as conn:
        row = conn.execute("SELECT processing_status, error_message FROM exam_papers WHERE session_id = ? AND ocr_name = 'David'", (session_id,)).fetchone()
        assert row["processing_status"] == "failed"
        assert row["error_message"] == "批改中途被异常中断，请重试"
        
    failed_detailed = db.list_failed_papers_detailed(session_id)
    assert len(failed_detailed) == 3
    failed_detailed = sorted(failed_detailed, key=lambda x: x["ocr_name"])
    assert failed_detailed[0]["ocr_name"] == "Alice"
    assert failed_detailed[0]["front_image"] == "f1.jpg"
    assert failed_detailed[1]["ocr_name"] == "Charlie"
    assert failed_detailed[1]["front_image"] == "f3.jpg"
    assert failed_detailed[2]["ocr_name"] == "David"
    assert failed_detailed[2]["front_image"] == "f4.jpg"


@pytest.mark.parametrize("completeness_status", ["incomplete", "invalid"])
def test_graded_incomplete_or_invalid_result_is_retry_eligible(tmp_path, completeness_status):
    import json

    db = DBManager(tmp_path / f"{completeness_status}.db")
    db.initialize()
    session_id = db.create_grading_session("Retry", "rubric.json", "answer.json")
    with db._connect() as conn:
        student_id = conn.execute(
            "INSERT INTO students (student_code, name) VALUES ('001', 'Alice')"
        ).lastrowid
        paper_id = conn.execute(
            "INSERT INTO exam_papers (session_id, front_image, back_image, ocr_name, student_id, match_status, processing_status) "
            "VALUES (?, 'front.jpg', 'back.jpg', 'Alice', ?, 'matched', 'graded')",
            (session_id, student_id),
        ).lastrowid
        conn.execute(
            "INSERT INTO session_results (session_id, student_id, paper_id, total_score, student_score, needs_human_review, raw_json) "
            "VALUES (?, ?, ?, 10, 5, 1, ?)",
            (
                session_id,
                student_id,
                paper_id,
                json.dumps({"grading_completeness": {"status": completeness_status}}),
            ),
        )
        conn.commit()

    assert [item["paper_id"] for item in db.list_failed_papers(session_id)] == [paper_id]
    assert [item["paper_id"] for item in db.list_failed_papers_detailed(session_id)] == [paper_id]


def test_grading_service_incremental_retry_merge(tmp_path):
    import json
    from unittest.mock import patch, MagicMock
    from hybrid_batch_grading_service import HybridBatchRunResult, PaperEntry
    from scanner import ExamPaperGroup
    from ai_grader import GradingResult, QuestionGradingDetail
    from grading_service import GradingService
    from llm_client import LLMClient

    # Setup files
    exams_dir = tmp_path / "exams"
    exams_dir.mkdir()
    front_image = exams_dir / "front.jpg"
    front_image.touch()
    back_image = exams_dir / "back.jpg"
    back_image.touch()

    rubric_path = tmp_path / "rubric.json"
    rubric_path.write_text('{"title": "Test"}', encoding="utf-8")
    answer_key_path = tmp_path / "answer_key.json"
    answer_key_path.write_text('{}', encoding="utf-8")

    db = DBManager(tmp_path / "test_merge.db")
    db.initialize()

    # Create session & template & regions
    session_id = db.create_grading_session("Test", "rubric.json", "answer.json")
    front_template = tmp_path / "front.png"
    back_template = tmp_path / "back.png"
    from PIL import Image
    Image.new("RGB", (2831, 1960), "white").save(front_template)
    Image.new("RGB", (2800, 1900), "white").save(back_template)
    template_id = db.upsert_session_template(session_id, str(front_template), str(back_template))
    
    # 2 questions: Q1 and Q2.
    regions = [
        {
            "region_uuid": "ru-1",
            "page": "front",
            "region_order": 1,
            "x": 10, "y": 20, "w": 100, "h": 50,
            "detected_question_id": "Q1",
            "mapped_question_id": "Q1",
            "confidence": 0.9,
            "is_confirmed": True,
            "mapping_status": "manual",
        },
        {
            "region_uuid": "ru-2",
            "page": "front",
            "region_order": 2,
            "x": 10, "y": 80, "w": 100, "h": 50,
            "detected_question_id": "Q2",
            "mapped_question_id": "Q2",
            "confidence": 0.9,
            "is_confirmed": True,
            "mapping_status": "manual",
        }
    ]
    db.replace_answer_regions_atomic(session_id, template_id, regions, confirmed=True)

    # Add Alice
    with db._connect() as conn:
        s1_id = conn.execute("INSERT INTO students (student_code, name) VALUES ('001', 'Alice')").lastrowid
        # Alice's exam paper
        paper_id = conn.execute(
            "INSERT INTO exam_papers (session_id, front_image, back_image, ocr_name, student_id, match_status, processing_status) "
            "VALUES (?, ?, ?, 'Alice', ?, 'matched', 'failed')",
            (session_id, str(front_image), str(back_image), s1_id)
        ).lastrowid
        
        # Suppose Alice has an existing score for Q1 = 5.0 (total = 10.0), but Q2 is missing
        # Insert Alice's session_results
        result_id = conn.execute(
            "INSERT INTO session_results (session_id, paper_id, student_id, total_score, student_score, needs_human_review, raw_json) "
            "VALUES (?, ?, ?, 10.0, 5.0, 0, ?)",
            (session_id, paper_id, s1_id, json.dumps({"Q1": {"score": 5.0}}))
        ).lastrowid
        
        # Insert details
        conn.execute(
            "INSERT INTO session_details (result_id, question_id, score_awarded, deduction_reason, knowledge_id, error_category, confidence_score) "
            "VALUES (?, 'Q1', 5.0, '', 'K-1', '', 99.0)",
            (result_id,)
        )
        conn.commit()

    mock_entry = PaperEntry(
        paper_key="Alice",
        student_id=s1_id,
        student_name="Alice",
        group=ExamPaperGroup(front_image=front_image, back_image=back_image, student_name="Alice", student_id=s1_id)
    )
    
    mock_q2_detail = QuestionGradingDetail(
        question_id="Q2",
        score_awarded=3.0,
        deduction_reason="Minor mistake",
        knowledge_id="K-2",
        error_category="calculation",
        confidence_score=95.0
    )
    
    mock_grading_result = GradingResult(
        student_name="Alice",
        total_score=10.0,
        student_score=3.0,
        needs_human_review=False,
        grading_details=[mock_q2_detail],
        raw_json={"Q2": {"score": 3.0}}
    )
    
    mock_run_result = HybridBatchRunResult(
        paper_entries=[mock_entry],
        results_by_paper_key={"Alice": mock_grading_result},
        fallback_items=[],
        usage_records=[],
        usage_summary={}
    )

    llm_client = MagicMock(spec=LLMClient)
    service = GradingService(db, llm_client)
    
    with patch("grading_service.run_hybrid_batch_grading") as mock_hybrid:
        mock_hybrid.return_value = mock_run_result
        
        events = list(service.run_session_grading(
            session_id=session_id,
            exams_dir=exams_dir,
            rubric_path=rubric_path,
            answer_key_path=answer_key_path,
            grading_mode="hybrid_batch",
            failed_only=True
        ))
        
        mock_hybrid.assert_called_once()
        kwargs = mock_hybrid.call_args.kwargs
        assert kwargs["skipped_questions_by_student"] == {s1_id: {"Q1"}}
        regions_passed = kwargs["answer_regions"]
        assert len(regions_passed) == 2
        assert regions_passed[0]["source_image_width"] == 2831
        assert regions_passed[0]["source_image_height"] == 1960
        
    with db._connect() as conn:
        paper = conn.execute("SELECT * FROM exam_papers WHERE id = ?", (paper_id,)).fetchone()
    assert paper["processing_status"] == "graded"
    
    res = db.get_session_results(session_id)[0]
    assert res["student_score"] == 8.0
    
    details = db.get_result_details(res["result_id"])
    assert len(details) == 2
    detail_qids = {d["question_id"] for d in details}
    assert detail_qids == {"Q1", "Q2"}
    
    q1_detail = next(d for d in details if d["question_id"] == "Q1")
    q2_detail = next(d for d in details if d["question_id"] == "Q2")
    assert q1_detail["score_awarded"] == 5.0
    assert q2_detail["score_awarded"] == 3.0


def test_annotated_results_foreign_key_cleanup(tmp_path):
    db = DBManager(tmp_path / "test_fk.db")
    db.initialize()
    
    session_id = db.create_grading_session("Test FK", "rubric.json", "answer.json")
    
    with db._connect() as conn:
        s1_id = conn.execute("INSERT INTO students (student_code, name) VALUES ('001', 'Alice')").lastrowid
        paper_id = conn.execute(
            "INSERT INTO exam_papers (session_id, front_image, back_image, ocr_name, student_id, match_status, processing_status) "
            "VALUES (?, 'f1.jpg', 'b1.jpg', 'Alice', ?, 'matched', 'graded')",
            (session_id, s1_id)
        ).lastrowid
        conn.commit()
        
    from ai_grader import GradingResult, QuestionGradingDetail
    
    res = GradingResult(
        student_name="Alice",
        total_score=100.0,
        student_score=85.0,
        needs_human_review=False,
        grading_details=[
            QuestionGradingDetail("Q1", 85.0, "", "K-1", confidence_score=1.0)
        ],
        raw_json={}
    )
    
    db.save_session_result(session_id, paper_id, s1_id, res)
    
    with db._connect() as conn:
        row = conn.execute("SELECT id FROM session_results WHERE session_id = ? AND student_id = ?", (session_id, s1_id)).fetchone()
        result_id = row[0]
        
        conn.execute(
            "INSERT INTO annotated_results (session_id, result_id, annotated_front_path, annotated_back_path) "
            "VALUES (?, ?, 'af.png', 'ab.png')",
            (session_id, result_id)
        )
        conn.commit()
        
    db.save_session_result(session_id, paper_id, s1_id, res)
    
    with db._connect() as conn:
        row = conn.execute("SELECT COUNT(*) FROM annotated_results WHERE result_id = ?", (result_id,)).fetchone()
        assert row[0] == 0


def test_bbox_from_regions_preserves_source_image_sizes():
    from hybrid_batch_grading_service import _bbox_from_regions
    regions = [
        {
            "page": "front",
            "x": 10, "y": 20, "w": 100, "h": 50,
            "source_image_width": 2831,
            "source_image_height": 1960,
            "extra_key": "some_value"
        },
        {
            "page": "front",
            "x": 20, "y": 30, "w": 150, "h": 80,
            "source_image_width": 2831,
            "source_image_height": 1960
        }
    ]
    merged = _bbox_from_regions(regions)
    assert merged["x"] == 10
    assert merged["y"] == 20
    assert merged["w"] == 160
    assert merged["h"] == 90
    assert merged["source_image_width"] == 2831
    assert merged["source_image_height"] == 1960
    assert merged["extra_key"] == "some_value"

