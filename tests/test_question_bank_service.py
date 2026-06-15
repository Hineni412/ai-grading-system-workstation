from __future__ import annotations

import sqlite3
from pathlib import Path

from question_bank.models.question import QuestionCreate
from question_bank.models.tag_schema import TagAnalysis
from question_bank.services.question_service import QuestionService


def test_question_schema_has_reason_column_and_migration_file(tmp_path: Path) -> None:
    db_path = tmp_path / "question_bank.db"
    service = QuestionService(db_path)
    service.initialize_database()

    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(questions)").fetchall()}

    assert "reason" in columns
    assert Path("migrations/question_bank/003_add_question_reason.sql").exists()


def test_initialize_database_backfills_semester_from_paper_title(tmp_path: Path) -> None:
    db_path = tmp_path / "question_bank.db"
    service = QuestionService(db_path)
    service.initialize_database()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO papers (title, exam_type, grade) VALUES (?, ?, ?)",
            ("深圳市七年级（下）期末数学试卷", "期末", "七年级"),
        )
        conn.commit()

    service.initialize_database()

    with sqlite3.connect(db_path) as conn:
        semester = conn.execute("SELECT semester FROM papers").fetchone()[0]
    assert semester == "下学期"


def test_save_tag_analysis_persists_reason(tmp_path: Path) -> None:
    service = QuestionService(tmp_path / "question_bank.db")
    question_id = service.add_question(
        QuestionCreate(
            question_number="1",
            question_text="已知一次函数，求参数。",
            answer_text="参考答案",
        )
    )
    analysis = TagAnalysis.from_dict(
        {
            "knowledge_points": ["一次函数"],
            "method_tags": ["待定系数法"],
            "ability_tags": ["运算求解"],
            "math_model_tags": ["函数模型"],
            "difficulty": 6,
            "error_prone_points": ["条件识别不完整"],
            "prerequisite_points": ["代数式"],
            "textbook_chapter": "函数",
            "teaching_stage": "同步巩固",
            "suitable_student_level": "中档提升",
            "reason": "学生容易漏用截距条件。",
        }
    )

    assert service.save_tag_analysis(question_id, analysis)

    saved = service.get_question(question_id)
    assert saved is not None
    assert saved["reason"] == "学生容易漏用截距条件。"


def test_query_questions_sorts_before_pagination(tmp_path: Path) -> None:
    service = QuestionService(tmp_path / "question_bank.db")
    for number, difficulty in [("1", "1"), ("2", "9"), ("3", "5"), ("4", "7")]:
        service.add_question(
            QuestionCreate(
                question_number=number,
                question_text=f"第 {number} 题",
                difficulty=difficulty,
            )
        )

    first_page = service.query_questions(sort_mode="试题难度", limit=2, offset=0)
    second_page = service.query_questions(sort_mode="试题难度", limit=2, offset=2)

    assert [item["question_number"] for item in first_page] == ["2", "4"]
    assert [item["question_number"] for item in second_page] == ["3", "1"]


def test_query_questions_sort_by_frequency(tmp_path: Path) -> None:
    db_path = tmp_path / "question_bank.db"
    from question_bank.database.schema import initialize_database, connect
    initialize_database(db_path)
    
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO papers (id, title, grade, exam_type, semester, import_status)
            VALUES (101, 'Mock Paper', '九年级', '中考', '全学年', 'success')
            """
        )
    
    service = QuestionService(db_path)
    q1 = service.add_question(QuestionCreate(question_number="1", question_text="Q1", question_type="选择题", paper_id=101))
    q2 = service.add_question(QuestionCreate(question_number="2", question_text="Q2", question_type="选择题", paper_id=101))
    q3 = service.add_question(QuestionCreate(question_number="3", question_text="Q3", question_type="选择题", paper_id=101))

    # To make the mock paper eligible (>=90% tagging rate), we must complete all 4 core tags for each question
    service.save_tag_analysis(q1, TagAnalysis.from_dict({
        "knowledge_points": ["KP_A"],
        "ability_tags": ["Ability_A"],
        "textbook_chapter": "Chapter_A",
        "suitable_student_level": "Level_A"
    }))
    service.save_tag_analysis(q2, TagAnalysis.from_dict({
        "knowledge_points": ["KP_A"],
        "ability_tags": ["Ability_A"],
        "textbook_chapter": "Chapter_A",
        "suitable_student_level": "Level_A"
    }))
    service.save_tag_analysis(q3, TagAnalysis.from_dict({
        "knowledge_points": ["KP_B"],
        "ability_tags": ["Ability_A"],
        "textbook_chapter": "Chapter_A",
        "suitable_student_level": "Level_A"
    }))

    res = service.query_questions(sort_mode="考频排序")
    assert {res[0]["question_number"], res[1]["question_number"]} == {"1", "2"}
    assert res[2]["question_number"] == "3"


def test_query_questions_sort_by_frequency_applies_difficulty_isolation(tmp_path: Path) -> None:
    db_path = tmp_path / "question_bank.db"
    from question_bank.database.schema import initialize_database, connect
    initialize_database(db_path)
    
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO papers (id, title, grade, exam_type, semester, import_status)
            VALUES (101, 'Mock Paper', '九年级', '中考', '全学年', 'success')
            """
        )
    
    service = QuestionService(db_path)
    
    # We will create two main questions:
    # q1: difficulty 4
    # q2: difficulty 9
    # Plus mock questions with difficulty 4 to boost q1's frequency.
    # Because of difficulty isolation (difference 5 >= 3), q2 won't match the difficulty 4 candidates.
    q1 = service.add_question(QuestionCreate(question_number="1", question_text="Q1", question_type="选择题", paper_id=101, difficulty="4"))
    for i in range(4):
        service.add_question(QuestionCreate(question_number=f"1_mock_{i}", question_text="MockQ", question_type="选择题", paper_id=101, difficulty="4"))
        
    q2 = service.add_question(QuestionCreate(question_number="2", question_text="Q2", question_type="选择题", paper_id=101, difficulty="9"))
    
    # Tag all of them completely so the paper is eligible.
    service.save_tag_analysis(q1, TagAnalysis.from_dict({
        "knowledge_points": ["KP_A"],
        "ability_tags": ["Ability_A"],
        "textbook_chapter": "Chapter_A",
        "suitable_student_level": "Level_A",
        "difficulty": 4
    }))
    for i in range(4):
        qid = q1 + 1 + i
        service.save_tag_analysis(qid, TagAnalysis.from_dict({
            "knowledge_points": ["KP_A"],
            "ability_tags": ["Ability_A"],
            "textbook_chapter": "Chapter_A",
            "suitable_student_level": "Level_A",
            "difficulty": 4
        }))
        
    service.save_tag_analysis(q2, TagAnalysis.from_dict({
        "knowledge_points": ["KP_A"],
        "ability_tags": ["Ability_A"],
        "textbook_chapter": "Chapter_A",
        "suitable_student_level": "Level_A",
        "difficulty": 9
    }))
    
    res = service.query_questions(sort_mode="考频排序")
    
    q1_idx = next(idx for idx, item in enumerate(res) if item["question_number"] == "1")
    q2_idx = next(idx for idx, item in enumerate(res) if item["question_number"] == "2")
    
    assert q1_idx < q2_idx



