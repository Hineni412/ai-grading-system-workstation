from __future__ import annotations

from ai_grader import GradingResult, QuestionGradingDetail, SecondaryError
from db_manager import DBManager
from grading_service import _detail_from_row


def _seed_result(db: DBManager) -> tuple[int, int]:
    session_id = db.create_grading_session("错因持久化", "rubric.json", "answer.json")
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
    return session_id, db.save_session_result(session_id, student_id, paper_id, result)


def test_session_detail_schema_and_domain_round_trip_secondary_errors(tmp_path) -> None:
    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    _session_id, result_id = _seed_result(db)

    with db._connect() as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(session_details)")}
    assert "secondary_errors_json" in columns

    rows = db.get_result_details(result_id)
    q2 = next(row for row in rows if row["question_id"] == "Q2")
    assert q2["secondary_errors"] == [
        {"category": "审题错误", "summary": "条件识别不完整", "evidence": "漏读已知"},
        {"category": "其他", "summary": "符号抄错", "evidence": "第二行"},
    ]
    detail = _detail_from_row(q2)
    assert [(item.category, item.summary) for item in detail.secondary_errors] == [
        ("审题错误", "条件识别不完整"),
        ("其他", "符号抄错"),
    ]


def test_atomic_retry_replaces_only_target_secondary_errors(tmp_path) -> None:
    db = DBManager(tmp_path / "grading.db")
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

    db.replace_result_details_atomic(
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

    rows = {row["question_id"]: row for row in db.get_result_details(result_id)}
    assert rows["Q1"]["secondary_errors"] == []
    assert rows["Q2"]["secondary_errors"] == [
        {"category": "其他", "summary": "单位遗漏", "evidence": "末行"}
    ]


def test_malformed_historical_secondary_error_json_reads_as_empty_list(tmp_path) -> None:
    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    _session_id, result_id = _seed_result(db)
    with db._connect() as conn:
        conn.execute(
            "UPDATE session_details SET secondary_errors_json = '{bad json' "
            "WHERE result_id = ? AND question_id = 'Q2'",
            (result_id,),
        )

    q2 = next(row for row in db.get_result_details(result_id) if row["question_id"] == "Q2")
    assert q2["secondary_errors"] == []
    assert _detail_from_row(q2).secondary_errors == []
