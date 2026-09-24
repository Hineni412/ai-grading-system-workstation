"""错因体系 P2–P4：选项诊断、填空错法库、题库确认入库。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from backend.class_analysis import ClassAnalysisStateStore
from backend.error_patterns import (
    OPTION_ANALYSIS_VERSION,
    additions_from_v3_result,
    extract_canonical_option,
    find_answer_patterns,
    find_option_analysis,
    normalize_option_analysis,
    normalize_option_answer,
    normalize_wrong_answer,
    parse_option_letters,
    record_answer_patterns,
    save_option_analysis,
    session_bank_context,
    session_bank_map,
    synthesize_answer_result,
    synthesize_option_result,
)


def _make_bank_db(path: Path) -> sqlite3.Connection:
    """最小题库：questions + grading_question_links + question_duplicate_links。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question_text TEXT,
            answer_text TEXT,
            question_type TEXT
        );
        CREATE TABLE grading_question_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            grading_session_id TEXT NOT NULL,
            source_question_id TEXT NOT NULL,
            bank_question_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'confirmed'
        );
        CREATE TABLE question_duplicate_links (
            question_id INTEGER NOT NULL,
            duplicate_of_question_id INTEGER NOT NULL
        );
        CREATE TABLE question_tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question_id INTEGER NOT NULL,
            tag_type TEXT NOT NULL,
            tag_value TEXT NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT INTO questions (id, question_text, answer_text, question_type)"
        " VALUES (101, '下列各数中是无理数的是 A. 3.14 B. sqrt2 C. 0.5 D. 22/7', '【答案】B', '选择题')"
    )
    conn.execute(
        "INSERT INTO questions (id, question_text, answer_text, question_type)"
        " VALUES (202, '计算：sqrt8 化简为 A. 4 B. 2sqrt2 C. sqrt4 D. 2', '【答案】B', '选择题')"
    )
    conn.execute(
        "INSERT INTO grading_question_links (grading_session_id, source_question_id, bank_question_id, status)"
        " VALUES ('7', 'Q3', 101, 'confirmed'), ('9', 'Q1', 202, 'confirmed')"
    )
    conn.execute(
        "INSERT INTO question_duplicate_links (question_id, duplicate_of_question_id)"
        " VALUES (202, 101)"
    )
    conn.commit()
    return conn


def test_parse_option_letters_variants():
    assert parse_option_letters("题目 A. 甲 B. 乙 C. 丙 D. 丁") == ["A", "B", "C", "D"]
    assert parse_option_letters("A、x B、y C、z") == ["A", "B", "C"]
    assert parse_option_letters("A) 1 B) 2") == ["A", "B"]
    assert parse_option_letters("A．正 B．误") == ["A", "B"]
    assert parse_option_letters("没有选项的解答题题干") == []
    assert parse_option_letters(None) == []


def test_normalize_option_answer():
    assert normalize_option_answer("b") == "B"
    assert normalize_option_answer("CA") == "AC"  # 去重排序
    assert normalize_option_answer("选B") == "B"
    assert normalize_option_answer("答案是D") == "D"
    assert normalize_option_answer("") == ""
    assert normalize_option_answer("乱写的文字") == ""
    assert normalize_option_answer("12") == ""


def test_extract_canonical_option():
    assert extract_canonical_option("【答案】C") == "C"
    assert extract_canonical_option("【答案】B 解析略") == "B"
    assert extract_canonical_option("答案：A") == "A"
    assert extract_canonical_option("没有答案标注") == ""


def test_normalize_option_analysis_strict_validation():
    payload = {
        "options": [
            {"option": "A", "category": "概念理解", "pattern": "混淆无理数与循环小数", "explanation": "把3.14当成无理数"},
            {"option": "B", "category": "概念理解", "pattern": "不该判正确项", "explanation": "正确选项"},
            {"option": "Z", "category": "计算与化简", "pattern": "不存在的选项", "explanation": ""},
            {"option": "C", "category": "粗心大意", "pattern": "非法大类", "explanation": ""},
            {"option": "D", "category": "审题与条件", "pattern": "", "explanation": "空名称"},
        ]
    }
    result = normalize_option_analysis(
        payload, option_letters=["A", "B", "C", "D"], correct_option="B")
    assert list(result) == ["A"]
    assert result["A"]["pattern"] == "混淆无理数与循环小数"
    with pytest.raises(ValueError):
        normalize_option_analysis({"options": []}, option_letters=["A"], correct_option="B")
    with pytest.raises(ValueError):
        normalize_option_analysis({"options": "not-a-list"}, option_letters=["A"], correct_option="B")


def test_synthesize_option_result_maps_letters_and_marks_unknown():
    source = {
        "question_id": "Q3",
        "evidence": [
            {"id": "E1", "student_answer": "C"},
            {"id": "E2", "student_answer": "c"},
            {"id": "E3", "student_answer": "D"},   # 没有诊断条目 → uncertain
            {"id": "E4", "student_answer": ""},     # 空白 → 未作答
            {"id": "E5", "student_answer": "看图作答"},  # 无法解析 → uncertain
        ],
    }
    patterns = {"C": {"category": "概念理解", "pattern": "混淆平方根与算术平方根", "explanation": "取错根"}}
    payload = synthesize_option_result(source, patterns)
    groups = payload["groups"]
    error_group = next(g for g in groups if g["kind"] == "error")
    assert error_group["category"] == "概念理解"
    assert error_group["reason"] == "混淆平方根与算术平方根"
    assert sorted(error_group["evidence_ids"]) == ["E1", "E2"]
    unanswered = next(g for g in groups if g["kind"] == "response_state")
    assert unanswered["category"] == "未作答" and unanswered["evidence_ids"] == ["E4"]
    assert payload["uncertain_ids"] == ["E3", "E5"]  # 原因未明，不猜


def test_synthesize_answer_result_full_and_partial_coverage():
    source = {
        "question_id": "Q7",
        "canonical_answer": "±8",
        "evidence": [
            {"id": "E1", "student_answer": "8"},
            {"id": "E2", "student_answer": " 8 "},   # 等价写法归同组
            {"id": "E3", "student_answer": ""},
        ],
    }
    library = {"8": {"category": "审题与条件", "pattern": "漏写负根", "explanation": "只写正值"}}
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
    result = {"groups": [
        {"kind": "error", "category": "审题与条件", "reason": "漏写负根",
         "manifestations": [{"description": "只写正平方根", "source_question_id": None,
                             "evidence_ids": ["E1"]}],
         "evidence_ids": ["E1"]},
        {"kind": "response_state", "category": "未作答", "reason": "未作答或无法辨认",
         "manifestations": [{"description": "空白", "source_question_id": None,
                             "evidence_ids": ["E2"]}],
         "evidence_ids": ["E2"]},
    ]}
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


def test_session_bank_context_links_and_families(tmp_path):
    qb_path = tmp_path / "databases" / "question_bank.db"
    conn = _make_bank_db(qb_path)
    conn.close()
    context = session_bank_context(qb_path, 7)
    assert context["Q3"]["bank_id"] == 101
    assert context["Q3"]["bank_ids"] == {101, 202}          # 判重家族
    assert (9, "Q1") in context["Q3"]["linked"]             # 同题另一场次
    assert session_bank_map(qb_path, 7) == {"Q3": 101}
    assert session_bank_context(qb_path, 9) == {"Q1": {"bank_id": 202, "bank_ids": {101, 202}, "linked": {(7, "Q3")}}}
    assert session_bank_map(None, 7) == {}


def test_option_analysis_reuse_across_sessions(tmp_path):
    store = ClassAnalysisStateStore(tmp_path / "reports")
    entry = {
        "version": OPTION_ANALYSIS_VERSION,
        "input_fingerprint": "fp-abc",
        "analysis": {"C": {"category": "概念理解", "pattern": "漏负根", "explanation": ""}},
        "failed": False,
    }
    save_option_analysis(store, 9, "Q1", entry)
    # 关联场次的同题分析可复用：同一题库题在场次9叫Q1、场次7叫Q3
    linked = {(9, "Q1")}
    found = find_option_analysis(store, 7, "Q3", "fp-abc", linked)
    assert found is not None and found["analysis"]["C"]["pattern"] == "漏负根"
    assert find_option_analysis(store, 7, "Q3", "fp-other", linked) is None


def test_confirm_pattern_idempotent_and_occurrences(tmp_path):
    from question_bank.database.schema import initialize_database
    from question_bank.services.error_pattern_service import (
        confirm_pattern, list_patterns, reject_pattern,
    )

    qb_path = tmp_path / "databases" / "question_bank.db"
    initialize_database(qb_path)
    from question_bank.database.schema import connect

    with connect(qb_path) as conn:
        conn.execute(
            "INSERT INTO questions (question_number, question_text, answer_text, question_type)"
            " VALUES ('3', '题干', '【答案】B', '选择题')"
        )
        question_id = int(conn.execute("SELECT id FROM questions").fetchone()[0])
    occurrence = {"session_id": 7, "question_id": "Q3", "kind": "error"}
    first = confirm_pattern(
        qb_path, question_id=question_id, category="概念理解",
        pattern="混淆平方根与算术平方根", explanation="取错根",
        trigger_kind="option", trigger_value="C",
        source="teacher_confirm", occurrence=occurrence,
        confirm_token="tok-1", confirmed_by="local_teacher",
    )
    second = confirm_pattern(
        qb_path, question_id=question_id, category="概念理解",
        pattern="混淆平方根与算术平方根", explanation="取错根",
        trigger_kind="option", trigger_value="C",
        source="teacher_confirm", occurrence={**occurrence, "session_id": 9},
        confirm_token="tok-2", confirmed_by="local_teacher",
    )
    assert second["id"] == first["id"]                      # 幂等：不重复建行
    assert len(second["occurrences"]) == 2                  # 来源快照追加
    with connect(qb_path) as conn:
        listed = list_patterns(conn, [question_id])
    assert len(listed[question_id]) == 1
    assert listed[question_id][0]["status"] == "confirmed"
    assert reject_pattern(qb_path, pattern_id=first["id"]) is True
    with connect(qb_path) as conn:
        assert list_patterns(conn, [question_id])[question_id] == []


def test_list_patterns_legacy_tag_conversion(tmp_path):
    from question_bank.database.schema import connect, initialize_database
    from question_bank.services.error_pattern_service import list_patterns

    qb_path = tmp_path / "databases" / "question_bank.db"
    initialize_database(qb_path)
    with connect(qb_path) as conn:
        conn.execute(
            "INSERT INTO questions (question_number, question_text) VALUES ('1', '题干')")
        question_id = int(conn.execute("SELECT id FROM questions").fetchone()[0])
        conn.execute(
            "INSERT INTO question_tags (question_id, tag_type, tag_value)"
            " VALUES (?, 'error_type', '运算化简错误'), (?, 'error_type', '自创自由文本')",
            (question_id, question_id),
        )
        conn.execute(
            "INSERT INTO question_tags (question_id, tag_type, tag_value)"
            " VALUES (?, 'knowledge_point', '平方根')",
            (question_id,),
        )
    with connect(qb_path) as conn:
        plain = list_patterns(conn, [question_id])
        with_predicted = list_patterns(conn, [question_id], include_predicted=True)
    assert plain[question_id] == []                         # 无确认行时为空
    items = {item["pattern"]: item for item in with_predicted[question_id]}
    assert items["运算化简错误"]["category"] == "计算与化简"   # 词表换算大类
    assert items["运算化简错误"]["status"] == "predicted"
    assert items["自创自由文本"]["category"] is None          # 未登记词不猜


def test_plan_cause_question_scope_and_paths(tmp_path):
    """八上+已关联题库的选择题走 option；其他选择/填空走各自路径。"""
    from backend.class_analysis import plan_cause_question

    qb_path = tmp_path / "databases" / "question_bank.db"
    _make_bank_db(qb_path).close()
    store = ClassAnalysisStateStore(tmp_path / "reports")
    choice_source = {
        "question_id": "Q3",
        "question_text": "下列各数中是无理数的是 A. 3.14 B. sqrt2 C. 0.5 D. 22/7",
        "canonical_answer": "B",
        "evidence": [{"id": "E1", "student_answer": "C"}],
    }
    ctx = session_bank_context(qb_path, 7)["Q3"]
    confirmed = {}
    in_scope = plan_cause_question(
        store, 7, choice_source, qtype="choice", option_scope=True,
        ctx=ctx, confirmed_by_bank=confirmed, question_bank_path=qb_path,
        retry_failed=False,
    )
    assert in_scope["path"] == "option" and in_scope["needs_call"] is True
    out_of_scope = plan_cause_question(
        store, 7, choice_source, qtype="choice", option_scope=False,
        ctx=ctx, confirmed_by_bank=confirmed, question_bank_path=qb_path,
        retry_failed=False,
    )
    assert out_of_scope["path"] == "v3"                       # 非八上退回整题整理
    unlinked = plan_cause_question(
        store, 7, choice_source, qtype="choice", option_scope=True,
        ctx={}, confirmed_by_bank=confirmed, question_bank_path=qb_path,
        retry_failed=False,
    )
    assert unlinked["path"] == "v3"                           # 未关联题库退回 v3
    proof = plan_cause_question(
        store, 7, {"question_id": "Q2", "evidence": []}, qtype="proof",
        option_scope=True, ctx={}, confirmed_by_bank=confirmed,
        question_bank_path=qb_path, retry_failed=False,
    )
    assert proof["path"] == "v3"

    # 填空题：答案库未覆盖 → fill_v3；库覆盖全部错误答案 → fill_covered 零调用。
    fill_source = {
        "question_id": "Q9",
        "canonical_answer": "±8",
        "evidence": [{"id": "E1", "student_answer": "8"}],
    }
    fill = plan_cause_question(
        store, 7, fill_source, qtype="fill_blank",
        option_scope=True, ctx={}, confirmed_by_bank=confirmed,
        question_bank_path=qb_path, retry_failed=False,
    )
    assert fill["path"] == "fill_v3" and fill["needs_call"] is True
    record_answer_patterns(store, 7, "Q9", [{
        "answer": "8", "category": "审题与条件", "pattern": "漏写负根",
    }])
    covered = plan_cause_question(
        store, 7, fill_source, qtype="fill_blank",
        option_scope=True, ctx={}, confirmed_by_bank=confirmed,
        question_bank_path=qb_path, retry_failed=False,
    )
    assert covered["path"] == "fill_covered" and covered["needs_call"] is False
    assert covered["payload"]["groups"][0]["reason"] == "漏写负根"


def test_recommendation_error_dimensions(tmp_path):
    """薄弱点新错因字段进入匹配维度；题库确认错法与旧标签都参与候选匹配。"""
    from question_bank.database.schema import connect, initialize_database
    from question_bank.recommendation.practice_plan_service import (
        _merge_error_pattern_tags,
        _question_tag_targets,
    )
    from question_bank.services.error_pattern_service import confirm_pattern

    profile = {
        "students": [{
            "student_id": "1",
            "weak_points": [{
                "knowledge_point": "平方根",
                "error_categories": ["概念理解"],
                "error_patterns": ["漏写负根"],
                "error_types": ["运算化简错误", "未登记旧词"],
                "tag_context": {"sub_skill": ["估算"]},
            }],
        }],
    }
    targets = _question_tag_targets(profile)
    ctx = targets["平方根"]
    assert ctx["error_pattern"] == {"漏写负根", "未登记旧词"}
    assert ctx["error_category"] == {"概念理解", "计算与化简"}  # 旧词换算
    assert ctx["sub_skill"] == {"估算"}

    qb_path = tmp_path / "qb.db"
    initialize_database(qb_path)
    with connect(qb_path) as conn:
        conn.execute(
            "INSERT INTO questions (question_number, question_text) VALUES ('1', '题干')")
        qid = int(conn.execute("SELECT id FROM questions").fetchone()[0])
        conn.execute(
            "INSERT INTO question_tags (question_id, tag_type, tag_value)"
            " VALUES (?, 'error_type', '概念理解不清')",
            (qid,),
        )
    confirm_pattern(
        qb_path, question_id=qid, category="审题与条件",
        pattern="漏写负根", trigger_kind="wrong_answer", trigger_value="8",
        source="teacher_confirm",
    )
    tags = {qid: {"error_type": ["概念理解不清"]}}
    with connect(qb_path) as conn:
        _merge_error_pattern_tags(conn, tags)
    merged = tags[qid]
    assert "漏写负根" in merged["error_pattern"]              # 确认错法
    assert "审题与条件" in merged["error_category"]
    assert "概念理解" in merged["error_category"]             # 旧标签换算
