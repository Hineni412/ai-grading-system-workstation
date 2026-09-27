from __future__ import annotations

from pathlib import Path


from question_bank.database.schema import connect, initialize_database
from question_bank.services.question_frequency_service import (
    QuestionFrequencyService,
)
from tests.current_knowledge_support import install_current_knowledge


def _insert_paper(
    db_path: Path,
    *,
    title: str,
    exam_type: str,
    grade: str = "七年级",
    semester: str = "下学期",
    province: str = "广东省",
    city: str = "深圳市",
) -> int:
    with connect(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO papers (
                title, exam_type, grade, semester, province, city, import_status
            ) VALUES (?, ?, ?, ?, ?, ?, 'ready')
            """,
            (title, exam_type, grade, semester, province, city),
        )
        return int(cursor.lastrowid)


def _insert_question(
    db_path: Path,
    paper_id: int,
    *,
    number: str,
    question_type: str = "选择题",
    knowledge: str = "科学记数法—表示较大的数",
    method: str = "数形结合",
    ability: str = "运算能力",
    model: str = "",
    difficulty: str = "4",
) -> int:
    with connect(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO questions (
                paper_id, question_number, question_type, question_text, difficulty
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (paper_id, number, question_type, f"{knowledge}-{number}", difficulty),
        )
        question_id = int(cursor.lastrowid)
        rows = [
            (question_id, "knowledge_point", knowledge),
            (question_id, "ability", ability),
            (question_id, "exam_scope", "七年级下册"),
            (question_id, "student_level", "基础巩固"),
        ]
        if method:
            rows.append((question_id, "method", method))
        if model:
            rows.append((question_id, "model", model))
        conn.executemany(
            "INSERT INTO question_tags (question_id, tag_type, tag_value) VALUES (?, ?, ?)",
            rows,
        )
        return question_id


def test_frequency_counts_at_most_one_best_match_per_paper(tmp_path: Path) -> None:
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    install_current_knowledge(db_path)
    service = QuestionFrequencyService(db_path)
    first_paper = _insert_paper(db_path, title="A", exam_type="期末")
    second_paper = _insert_paper(db_path, title="B", exam_type="期末")
    target_id = _insert_question(db_path, first_paper, number="1")
    _insert_question(db_path, first_paper, number="2")
    _insert_question(db_path, second_paper, number="1")

    metrics = service.metrics_for_question(target_id)

    assert metrics.available
    assert metrics.matched_question_count == 2
    assert metrics.eligible_paper_count == 2
    assert metrics.questions_per_paper == 1.0


def test_frequency_excludes_practice_and_other_semesters(tmp_path: Path) -> None:
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    install_current_knowledge(db_path)
    service = QuestionFrequencyService(db_path)
    target_paper = _insert_paper(db_path, title="期末A", exam_type="期末")
    same_scope = _insert_paper(db_path, title="期末B", exam_type="期末")
    other_semester = _insert_paper(
        db_path, title="上学期期末", exam_type="期末", semester="上学期"
    )
    other_grade = _insert_paper(
        db_path, title="八年级期末", exam_type="期末", grade="八年级"
    )
    other_exam = _insert_paper(db_path, title="七年级期中", exam_type="期中")
    practice = _insert_paper(db_path, title="同步练习", exam_type="同步练习")
    disguised_practice = _insert_paper(
        db_path,
        title="期末同步练习",
        exam_type="期末同步练习",
    )
    target_id = _insert_question(db_path, target_paper, number="1")
    _insert_question(db_path, same_scope, number="1")
    _insert_question(db_path, other_semester, number="1")
    _insert_question(db_path, other_grade, number="1")
    _insert_question(db_path, other_exam, number="1")
    _insert_question(db_path, practice, number="1")
    _insert_question(db_path, disguised_practice, number="1")

    metrics = service.metrics_for_question(target_id)
    practice_metrics = service.metrics_for_question(
        _insert_question(db_path, practice, number="2")
    )

    assert metrics.matched_question_count == 2
    assert metrics.eligible_paper_count == 2
    assert not practice_metrics.available
