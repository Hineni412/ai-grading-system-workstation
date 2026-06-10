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
        
        # insert a failed paper and a successful paper
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
        conn.commit()
        
    failed = db.list_failed_papers(session_id)
    assert len(failed) == 1
    assert failed[0]["student_name"] == "Alice"
    assert failed[0]["error_message"] == "Connection error"
    
    failed_detailed = db.list_failed_papers_detailed(session_id)
    assert len(failed_detailed) == 1
    assert failed_detailed[0]["ocr_name"] == "Alice"
    assert failed_detailed[0]["front_image"] == "f1.jpg"
    assert failed_detailed[0]["back_image"] == "b1.jpg"
