from __future__ import annotations

from backend.domain_models import GradingResult, QuestionGradingDetail, SecondaryError
from db_manager import DBManager
from backend.repositories.grading_database import open_grading_repositories


def _seed_result(db: DBManager) -> tuple[int, int]:
    session_id = db.sessions.create_grading_session("错因持久化", "rubric.json", "answer.json")
    with db._connect() as conn:
        student_id = int(
            conn.execute(
                "INSERT INTO students (student_code, name) VALUES ('001', '学生甲')"
            ).lastrowid
        )
        paper_id = int(
            conn.execute(
                """
                INSERT INTO exam_papers (
                    session_id, front_image, back_image, ocr_name, student_id,
                    match_status, processing_status
                ) VALUES (?, 'front.jpg', 'back.jpg', '学生甲', ?, 'matched', 'graded')
                """,
                (session_id, student_id),
            ).lastrowid
        )
    result = GradingResult(
        student_name="学生甲",
        total_score=10,
        student_score=7,
        needs_human_review=False,
        grading_details=[
            QuestionGradingDetail(
                question_id="Q1",
                score_awarded=5,
                deduction_reason=None,
                secondary_errors=[],
            ),
            QuestionGradingDetail(
                question_id="Q2",
                score_awarded=2,
                deduction_reason="作答证据",
                error_category="逻辑断裂",
                error_summary="辅助线思路缺失",
                secondary_errors=[
                    SecondaryError("审题错误", "条件识别不完整", "漏读已知"),
                    SecondaryError("其他", "符号抄错", "第二行"),
                ],
            ),
        ],
        raw_json={"grading_completeness": {"status": "complete"}},
    )
    return session_id, db.results.save_session_result(session_id, student_id, paper_id, result)


def test_atomic_retry_replaces_only_target_secondary_errors(tmp_path) -> None:
    db = open_grading_repositories(tmp_path / "grading.db")
    db.initialize()
    _session_id, result_id = _seed_result(db)
    replacement = QuestionGradingDetail(
        question_id="Q2",
        score_awarded=4,
        deduction_reason="新证据",
        error_category="计算错误",
        error_summary="计算失误",
        secondary_errors=[SecondaryError("其他", "单位遗漏", "末行")],
    )

    db.results.replace_result_details_atomic(
        result_id,
        ["Q2"],
        [replacement],
        rubric={
            "total_score": 10,
            "questions": [
                {"question_id": "Q1", "max_score": 5},
                {"question_id": "Q2", "max_score": 5},
            ],
        },
        student_score=9,
        needs_human_review=False,
        raw_json={"grading_completeness": {"status": "complete"}},
    )

    rows = {row["question_id"]: row for row in db.results.get_result_details(result_id)}
    assert rows["Q1"]["secondary_errors"] == []
    assert rows["Q2"]["secondary_errors"] == [
        {"category": "其他", "summary": "单位遗漏", "evidence": "末行"}
    ]
