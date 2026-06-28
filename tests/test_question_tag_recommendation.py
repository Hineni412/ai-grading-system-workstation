from __future__ import annotations

from pathlib import Path

from question_bank.database.schema import connect, initialize_database
from question_bank.recommendation.practice_plan_service import PracticePlanService
from question_bank.services.source_question_link_service import SourceQuestionLinkService


_QUESTION_TEXTS = (
    "已知两边及夹角，证明两个三角形全等。",
    "利用角平分线构造辅助线并说明线段关系。",
    "在平行线背景下推导对应角相等。",
    "由中点条件连接线段完成几何证明。",
    "根据垂直条件判断直角三角形的对应关系。",
    "在折叠情境中求重合边与角的数量关系。",
    "结合等腰三角形性质证明底角相等。",
    "由公共边和两组等角完成全等论证。",
    "在网格图中补充条件使两个图形全等。",
    "利用旋转前后的对应点证明线段相等。",
    "根据尺规作图痕迹还原三角形证明过程。",
    "在多边形分割图中寻找一对全等三角形。",
)


def _insert_candidate(
    conn,
    *,
    question_id: int,
    knowledge_point: str,
    method: str = "",
    difficulty: int = 5,
) -> None:
    conn.execute(
        """
        INSERT INTO papers (id, title, source_file, grade, import_status)
        VALUES (?, ?, ?, '八年级', 'ready')
        """,
        (question_id, f"来源{question_id}", f"paper-{question_id}.docx"),
    )
    conn.execute(
        """
        INSERT INTO questions (
            id, paper_id, question_number, question_type, question_text,
            answer_text, difficulty
        ) VALUES (?, ?, '1', '解答题', ?, '答案', ?)
        """,
        (
            question_id,
            question_id,
            _QUESTION_TEXTS[question_id % len(_QUESTION_TEXTS)],
            str(difficulty),
        ),
    )
    conn.execute(
        "INSERT INTO question_tags (question_id, tag_type, tag_value) "
        "VALUES (?, 'knowledge_point', ?)",
        (question_id, knowledge_point),
    )
    if method:
        conn.execute(
            "INSERT INTO question_tags (question_id, tag_type, tag_value) "
            "VALUES (?, 'method', ?)",
            (question_id, method),
        )


def _profile() -> dict:
    return {
        "diagnosis_identity": "question_tag",
        "scope": {"mode": "student", "student_ids": ["12"]},
        "exam_scope": {"mode": "current", "session_ids": [14]},
        "students": [
            {
                "student_id": "12",
                "student_name": "学生甲",
                "score_rate": 0.5,
                "weak_points": [
                    {
                        "knowledge_key": "knowledge_point:三角形全等",
                        "knowledge_point": "三角形全等",
                        "mastery": 0.4,
                        "tag_context": {
                            "method": ["构造辅助线"],
                            "sub_skill": ["角平分线模型"],
                        },
                    }
                ],
            }
        ],
    }


def test_exact_knowledge_is_required_tag_overlap_boosts_and_original_is_excluded(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    with connect(db_path) as conn:
        _insert_candidate(
            conn,
            question_id=101,
            knowledge_point="三角形全等",
            method="构造辅助线",
        )
        _insert_candidate(conn, question_id=102, knowledge_point="三角形全等")
        _insert_candidate(conn, question_id=103, knowledge_point="三角形全等判定")
        _insert_candidate(conn, question_id=104, knowledge_point="三角形全等")
        for question_id in range(105, 112):
            _insert_candidate(
                conn,
                question_id=question_id,
                knowledge_point="三角形全等",
                difficulty=2 + (question_id % 7),
            )
    SourceQuestionLinkService(db_path).confirm_link(
        grading_session_id=14,
        source_question_id="Q1",
        bank_question_id=104,
        link_method="paper_question_number",
    )

    plan = PracticePlanService(db_path).generate_variant(_profile(), question_count=8)

    selected_ids = {item["question_id"] for item in plan["items"]}
    assert 103 not in selected_ids
    assert 104 not in selected_ids
    assert all(
        "三角形全等" in item["tags"]["knowledge_point"]
        for item in plan["items"]
    )
    assert plan["items"][0]["question_id"] == 101
    assert plan["items"][0]["score_components"]["tag_overlap"] > 0
    assert all(item["match_kind"] == "exact" for item in plan["items"])
    assert all("target_skill_id" not in item for item in plan["items"])


def test_related_fill_policy_never_adds_non_exact_knowledge_candidates(tmp_path: Path) -> None:
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    with connect(db_path) as conn:
        _insert_candidate(conn, question_id=201, knowledge_point="三角形全等")
        _insert_candidate(conn, question_id=202, knowledge_point="三角形全等")
        for question_id in range(203, 212):
            _insert_candidate(conn, question_id=question_id, knowledge_point="三角形全等判定")

    plan = PracticePlanService(db_path).generate_variant(
        _profile(),
        question_count=8,
        exclude_question_ids=set(),
        related_fill_policy="allow_neighbors",
    )

    assert {item["question_id"] for item in plan["items"]} == {201, 202}
    assert plan["shortages"]
    assert all(shortage["decision_required"] is False for shortage in plan["shortages"])
