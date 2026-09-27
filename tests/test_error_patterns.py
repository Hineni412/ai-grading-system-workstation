"""错因体系 P2–P4：选项诊断、填空错法库、题库确认入库。"""

from __future__ import annotations

import sqlite3
from pathlib import Path


from backend.class_analysis import ClassAnalysisStateStore
from backend.error_patterns import (
    OPTION_ANALYSIS_VERSION,
    additions_from_v3_result,
    find_answer_patterns,
    find_option_analysis,
    record_answer_patterns,
    save_option_analysis,
    synthesize_answer_result,
)


def test_synthesize_answer_result_full_and_partial_coverage():
    source = {
        "question_id": "Q7",
        "canonical_answer": "±8",
        "evidence": [
            {"id": "E1", "student_answer": "8"},
            {"id": "E2", "student_answer": " 8 "},  # 等价写法归同组
            {"id": "E3", "student_answer": ""},
        ],
    }
    library = {
        "8": {
            "category": "审题与条件",
            "pattern": "漏写负根",
            "explanation": "只写正值",
        }
    }
    payload = synthesize_answer_result(source, library, "±8")
    assert payload is not None
    group = next(g for g in payload["groups"] if g["kind"] == "error")
    assert sorted(group["evidence_ids"]) == ["E1", "E2"]
    assert group["reason"] == "漏写负根"
    blank = next(g for g in payload["groups"] if g["kind"] == "response_state")
    assert blank["evidence_ids"] == ["E3"]

    # 库外新答案 → 需要 v3 整理
    source["evidence"].append({"id": "E4", "student_answer": "9"})
    assert synthesize_answer_result(source, library, "±8") is None
    # 与正确答案等价的失分记录不直接归入错法库 → 需要 v3 判断
    source["evidence"] = [{"id": "E5", "student_answer": "±8"}]
    assert synthesize_answer_result(source, library, "±8") is None


def test_answer_pattern_library_roundtrip_and_absorb(tmp_path):
    store = ClassAnalysisStateStore(tmp_path / "reports")
    source = {
        "question_id": "Q7(P1)",
        "canonical_answer": "±8",
        "evidence": [
            {"id": "E1", "student_answer": "8"},
            {"id": "E2", "student_answer": "64"},
        ],
    }
    result = {
        "groups": [
            {
                "kind": "error",
                "category": "审题与条件",
                "reason": "漏写负根",
                "manifestations": [
                    {
                        "description": "只写正平方根",
                        "source_question_id": None,
                        "evidence_ids": ["E1"],
                    }
                ],
                "evidence_ids": ["E1"],
            },
            {
                "kind": "response_state",
                "category": "未作答",
                "reason": "未作答或无法辨认",
                "manifestations": [
                    {
                        "description": "空白",
                        "source_question_id": None,
                        "evidence_ids": ["E2"],
                    }
                ],
                "evidence_ids": ["E2"],
            },
        ]
    }
    additions = additions_from_v3_result(source, result, "±8")
    assert len(additions) == 1  # 未作答组不入库
    assert additions[0]["answer"] == "8"
    assert additions[0]["pattern"] == "漏写负根"
    added = record_answer_patterns(store, 7, "Q7", additions, bank_question_id=101)
    assert added == 1
    # 幂等：同一规范化答案不重复新增
    assert record_answer_patterns(store, 7, "Q7", additions) == 0
    merged = find_answer_patterns(store, 7, "Q7", [])
    assert merged["8"]["pattern"] == "漏写负根"
    assert merged["8"]["category"] == "审题与条件"


def test_option_analysis_reuse_across_sessions(tmp_path):
    store = ClassAnalysisStateStore(tmp_path / "reports")
    entry = {
        "version": OPTION_ANALYSIS_VERSION,
        "input_fingerprint": "fp-abc",
        "analysis": {
            "C": {"category": "概念理解", "pattern": "漏负根", "explanation": ""}
        },
        "failed": False,
    }
    save_option_analysis(store, 9, "Q1", entry)
    # 关联场次的同题分析可复用：同一题库题在场次9叫Q1、场次7叫Q3
    linked = {(9, "Q1")}
    found = find_option_analysis(store, 7, "Q3", "fp-abc", linked)
    assert found is not None and found["analysis"]["C"]["pattern"] == "漏负根"
    assert find_option_analysis(store, 7, "Q3", "fp-other", linked) is None


def _seed_pattern_question(qb_path: Path) -> int:
    from question_bank.database.schema import connect, initialize_database

    initialize_database(qb_path)
    with connect(qb_path) as conn:
        conn.execute(
            "INSERT INTO questions (question_number, question_text, answer_text, question_type)"
            " VALUES ('3', '题干', '【答案】B', '选择题')"
        )
        return int(conn.execute("SELECT id FROM questions").fetchone()[0])


def test_record_auto_patterns_insert_rerun_and_occurrence(tmp_path):
    from question_bank.database.schema import connect
    from question_bank.services.error_pattern_service import (
        list_patterns,
        record_auto_patterns,
    )

    qb_path = tmp_path / "databases" / "question_bank.db"
    question_id = _seed_pattern_question(qb_path)
    occurrence = {"session_id": 7, "question_id": "Q3"}
    rows = [
        {
            "question_id": question_id,
            "category": "概念理解",
            "pattern": "混淆平方根与算术平方根",
            "explanation": "取错根",
            "trigger_kind": "option",
            "trigger_value": "C",
            "source": "ai_auto",
            "occurrence": occurrence,
        }
    ]
    assert record_auto_patterns(qb_path, rows) == 1
    # 重跑同一快照：不新增、不重复追加 occurrence。
    assert record_auto_patterns(qb_path, rows) == 0
    # 新场次的出现快照追加到同一行。
    assert (
        record_auto_patterns(
            qb_path,
            [
                {**rows[0], "occurrence": {"session_id": 9, "question_id": "Q1"}},
            ],
        )
        == 0
    )
    with connect(qb_path) as conn:
        listed = list_patterns(conn, [question_id])[question_id]
    assert len(listed) == 1
    assert listed[0]["source"] == "ai_auto"
    assert listed[0]["status"] == "confirmed"
    assert len(listed[0]["occurrences"]) == 2


def test_rename_patterns_merges_old_and_writes_teacher_edit(tmp_path):
    from question_bank.database.schema import connect
    from question_bank.services.error_pattern_service import (
        list_patterns,
        record_auto_patterns,
        rename_patterns,
    )

    qb_path = tmp_path / "databases" / "question_bank.db"
    question_id = _seed_pattern_question(qb_path)
    record_auto_patterns(
        qb_path,
        [
            {
                "question_id": question_id,
                "category": "概念理解",
                "pattern": "混淆平方根与算术平方根",
                "explanation": "取错根",
                "trigger_kind": "option",
                "trigger_value": "C",
                "source": "ai_auto",
                "occurrence": {"session_id": 7, "question_id": "Q3"},
            }
        ],
    )
    # 改名：旧行 merged，新名以 teacher_edit 生效，触发条件保留。
    assert (
        rename_patterns(
            qb_path,
            question_ids=[question_id],
            old_pattern="混淆平方根与算术平方根",
            new_pattern="误认梯形为轴对称",
            category="审题与条件",
        )
        == 1
    )
    with connect(qb_path) as conn:
        listed = list_patterns(conn, [question_id])[question_id]
        all_rows = conn.execute(
            "SELECT pattern, status, source, trigger_kind, trigger_value, category"
            " FROM question_error_patterns ORDER BY id"
        ).fetchall()
    assert len(all_rows) == 2
    assert all_rows[0][1] == "merged"
    assert tuple(all_rows[1]) == (
        "误认梯形为轴对称",
        "confirmed",
        "teacher_edit",
        "option",
        "C",
        "审题与条件",
    )
    assert len(listed) == 1 and listed[0]["pattern"] == "误认梯形为轴对称"

    # 只改大类：原行就地更新，不新建行。
    assert (
        rename_patterns(
            qb_path,
            question_ids=[question_id],
            old_pattern="误认梯形为轴对称",
            new_pattern="误认梯形为轴对称",
            category="概念理解",
        )
        == 1
    )
    with connect(qb_path) as conn:
        listed = list_patterns(conn, [question_id])[question_id]
        total = conn.execute("SELECT COUNT(*) FROM question_error_patterns").fetchone()[
            0
        ]
    assert total == 2
    assert listed[0]["category"] == "概念理解"
    assert listed[0]["source"] == "teacher_edit"
