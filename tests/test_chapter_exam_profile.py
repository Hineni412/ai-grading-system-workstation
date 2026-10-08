"""章节考情统计：同源卷合并、归属、技能与典型题的口径测试。"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from question_bank.services.chapter_exam_profile import (
    build_chapter_exam_profile,
    build_exam_frequency,
)
from question_bank.taxonomy.curriculum_catalog import curriculum_volume

VOLUME_ID = "bnu24-math-g8-upper"
CH1 = "kp_bnu24_math_g8_upper_1"
CH2 = "kp_bnu24_math_g8_upper_2"
S11 = "kp_bnu24_math_g8_upper_1_1"
S21 = "kp_bnu24_math_g8_upper_2_1"
S22 = "kp_bnu24_math_g8_upper_2_2"

# 文本相同 → 三元片段 Dice = 1，足以触发“同一道题/同源卷”判断。
T1 = "下列各数中无理数有几个，请写出判断依据"
T2 = "填空：平方根等于它本身的数是多少"
T3 = "解答：比较两个二次根式的大小并说明理由"
TX = "下列各式中哪些是有理数哪些是无理数"
TU = "选择题：实数范围内绝对值最小的数"
TS = "解答题：本章综合应用实数与二次根式"
TC = "选择题：三角形内角和的另一种问法"
TY = "选择题：立方根与平方根的综合判断"
TR = "填空题：勾股定理的简单应用"
TZ = "选择题：阶段练习里的普通题"
TW = "选择题：已删除试卷里的题"


def _node(key: str, name: str, anchors: list[str]) -> dict:
    return {
        "stable_key": key,
        "display_name": name,
        "observable_evidence": f"定义-{key}",
        "curriculum_anchors_json": json.dumps(anchors),
    }


@pytest.fixture
def profile_db(tmp_path: Path, question_bank_database):
    db_path = tmp_path / "question_bank.db"
    question_bank_database(db_path, taxonomy_revision=3)
    papers = [
        (1, "期中A", "期中", None),
        (2, "期中B", "期中", None),
        (3, "期末A", "期末", None),
        (4, "期末B", "期末", None),
        (5, "阶段练习卷", "阶段练习", None),
        (6, "已删卷", "期中", "2026-01-01 00:00:00"),
    ]
    questions = [
        # 期中A
        (1, 1, "1", "选择题", 2, T1),
        (2, 1, "2", "填空题", 5, T2),
        (3, 1, "3", "解答题", 8, T3),
        (7, 1, "4", "选择题", 2, TX),
        # 期中B：前四题与期中A逐题近乎相同，只多一道新题
        (101, 2, "1", "选择题", 2, T1),
        (102, 2, "2", "填空题", 5, T2),
        (103, 2, "3", "解答题", 8, T3),
        (104, 2, "4", "选择题", 2, TX),
        (105, 2, "9", "选择题", 9, TU),
        # 期末A
        (8, 3, "5", "选择题", 4, TC),
        (9, 3, "20", "解答题", 6, TS),
        (10, 3, "12", "选择题", 5.5, TX),
        (11, 3, "13", "选择题", 5, TY),
        # 期末B
        (12, 4, "6", "填空题", 3, TR),
        # 不计入
        (13, 5, "1", "选择题", 2, TZ),
        (15, 5, "2", "选择题", None, "选择题：阶段练习里的缺难度题"),
        (14, 6, "1", "选择题", 2, TW),
    ]
    scope = [
        (1, S21, [S21]),
        (2, S21, [S21]),
        (3, S21, [S21]),
        (7, S21, [S21]),
        (101, S21, [S21]),
        (102, S21, [S21]),
        (103, S21, [S21]),
        (104, S21, [S21]),
        (105, S21, [S21]),
        (8, S11, [S11, S21]),
        (9, CH2, [CH2]),
        (10, S21, [S21]),
        (11, S21, [S21]),
        (12, S11, [S11]),
        (13, S21, [S21]),
        (15, S21, [S21]),
        (14, S21, [S21]),
    ]
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            "INSERT INTO papers (id, title, exam_type, import_status, deleted_at) "
            "VALUES (?, ?, ?, 'success', ?)",
            papers,
        )
        conn.executemany(
            "INSERT INTO questions (id, paper_id, question_number, question_type, "
            "difficulty, question_text, is_deleted) VALUES (?, ?, ?, ?, ?, ?, 0)",
            questions,
        )
        conn.executemany(
            "INSERT INTO question_scope_summary (question_id, primary_section_id, "
            "direct_section_ids_json) VALUES (?, ?, ?)",
            [(qid, primary, json.dumps(direct)) for qid, primary, direct in scope],
        )
        conn.commit()
    return db_path


def _snapshot() -> dict:
    hits = {
        1: {"sk_a": [{"point_id": "a1"}], "sk_b": [{"point_id": "b1"}, {"point_id": "b2"}]},
        2: {"sk_b": [{"point_id": "b1"}]},
        7: {"sk_a": [{"point_id": "a1"}]},
        8: {"sk_b": [{"point_id": "b1"}]},
        9: {"sk_a": [{"point_id": "a1"}]},
        10: {"sk_a": [{"point_id": "a1"}]},
        11: {"sk_a": [{"point_id": "a1"}]},
        101: {"sk_a": [{"point_id": "a1"}]},
        102: {"sk_b": [{"point_id": "b1"}]},
        104: {"sk_a": [{"point_id": "a1"}]},
        105: {"sk_a": [{"point_id": "a1"}]},
        13: {"sk_a": [{"point_id": "a13"}]},   # 阶段练习的题也有技能链接
        14: {"sk_a": [{"point_id": "a14"}]},   # 宿主卷已删，题本身未删
        15: {"sk_b": [{"point_id": "b15"}]},   # 缺难度
    }
    return {
        "release": "rel-test",
        "nodes": {
            "sk_a": _node("sk_a", "技能·区分有理数无理数", [S21]),
            "sk_b": _node("sk_b", "技能·平方根求法", [S22]),
        },
        "volumes": {VOLUME_ID: {1, 2, 3, 7, 8, 9, 10, 11, 12, 13, 14, 15, 101, 102, 103, 104, 105}},
        "members": {
            1: {1, 2, 3, 7},
            2: {101, 102, 103, 104, 105},
            3: {8, 9, 10, 11},
            4: {12},
            5: {13, 15},
            6: {14},
        },
        "by_question": hits,
    }


def _build(db_path: Path) -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return build_chapter_exam_profile(conn, _snapshot(), curriculum_volume(volume_id=VOLUME_ID))
    finally:
        conn.close()


def _chapter(result: dict, chapter_id: str) -> dict:
    return next(chapter for chapter in result["chapters"] if chapter["id"] == chapter_id)


def _section(chapter: dict, section_id: str) -> dict:
    return next(section for section in chapter["sections"] if section["id"] == section_id)


def test_same_source_merge_and_exclusions(profile_db):
    result = _build(profile_db)
    assert result["model_calls"] == 0
    assert result["counted_question_count"] == 10
    stages = {row["stage"]: row for row in result["stages"]}
    assert stages["midterm"] == {
        "stage": "midterm", "label": "期中", "paper_count": 2, "group_count": 1, "unit": "组",
    }
    assert stages["final"] == {
        "stage": "final", "label": "期末", "paper_count": 2, "group_count": 2, "unit": "份",
    }
    assert result["merged_groups"] == [{
        "stage": "midterm",
        "papers": [{"id": 1, "title": "期中A"}, {"id": 2, "title": "期中B"}],
    }]
    chapter = _chapter(result, CH2)
    assert chapter["totals"] == {
        "midterm": {"main": 5, "cross": 0},
        "final": {"main": 3, "cross": 1},
    }
    # 阶段练习与已删卷的题没有进入任何统计。
    all_ids = {
        qid
        for section in _chapter(result, CH2)["sections"]
        for skill in section["skills"]
        for stage_cells in skill["cells"].values()
        for ids in stage_cells.values()
        for qid in ids
    }
    assert 13 not in all_ids and 14 not in all_ids


def test_main_cross_attribution_and_synthesis(profile_db):
    result = _build(profile_db)
    chapter = _chapter(result, CH2)
    assert chapter["cross_question_ids"]["final"] == [8]
    synthesis = _section(chapter, f"{CH2}#synthesis")
    assert synthesis["synthesis"] is True
    assert synthesis["label"] == "章内综合"
    assert synthesis["main_count"] == 1
    # 另一章的题不因跨章判定点重复算进本章主考。
    chapter_one = _chapter(result, CH1)
    assert chapter_one["totals"]["final"]["main"] == 2
    assert _section(chapter_one, S11)["main_count"] == 2


def test_coverage_tiers_columns_and_skills(profile_db):
    result = _build(profile_db)
    chapter = _chapter(result, CH2)
    section = _section(chapter, S21)
    assert section["label"] == "2.1 认识实数"
    assert section["main_count"] == 7
    assert section["coverage"]["midterm"] == {"groups": 1, "of": 1, "percent": 100, "questions": 5}
    assert section["coverage"]["final"] == {"groups": 1, "of": 2, "percent": 50, "questions": 2}
    assert chapter["difficulty"]["midterm"] == {
        "total": 5, "basic": 2, "mid": 1, "hard": 2, "choice": 3, "fill": 1, "written": 1,
    }
    skills = {row["name"]: row for row in section["skills"]}
    own = skills["区分有理数无理数"]
    assert own["key"] == "sk_a" and own["total"] == 5
    assert own["home_section_label"] == ""
    assert own["cells"]["midterm"]["choice_basic"] == [1, 7]
    assert own["cells"]["midterm"]["choice_advanced"] == [105]
    assert own["cells"]["final"]["choice_advanced"] == [10, 11]
    foreign = skills["平方根求法"]
    assert foreign["total"] == 1
    assert foreign["home_section_label"] == "2.2 平方根与立方根"
    unlinked = section["skills"][-1]
    assert unlinked["unlinked"] is True and unlinked["key"] is None
    assert unlinked["cells"]["midterm"]["written"] == [3]
    # 位置按试卷内题号入桶，20 题及以后合并。
    assert own["positions"]["final"]["12"] == [10]
    synthesis_skill = _section(chapter, f"{CH2}#synthesis")["skills"][0]
    assert synthesis_skill["positions"]["final"]["20+"] == [9]


def test_typical_needs_two_questions_from_two_groups(profile_db):
    # S21 每个技能×难度档单元都不满足“≥2 题且来自 ≥2 个卷组”，不选典型题。
    result = _build(profile_db)
    skills = {
        row["key"]: row
        for row in _section(_chapter(result, CH2), S21)["skills"]
    }
    assert skills["sk_a"]["typical"] == []
    assert skills["sk_b"]["typical"] == []


def _frequency(db_path: Path) -> dict[int, tuple[float, float]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return build_exam_frequency(
            conn, _snapshot(), curriculum_volume(volume_id=VOLUME_ID)
        )
    finally:
        conn.close()


def test_exam_frequency_coverage_and_exclusions(profile_db):
    """考频＝同技能同档计入题覆盖的卷组 ÷ 卷组数，自有卷组分子分母同除。

    本夹具：期中 A/B 同源合并为 1 个卷组，期末 A、B 各 1 组。"""
    freq = _frequency(profile_db)
    # 期中只有一个卷组且是 q1 自有组 → 剔除后无剩余组。
    assert freq[1] == (0.0, 0.0)
    assert freq[105] == (0.0, 0.0)
    # q2（sk_b 中档，期中组）：期末两个组中有一组（q8 所在）覆盖同技能同档。
    assert freq[2] == (0.0, 0.5)
    # q8（sk_b 中档，期末A组）：期中组覆盖 → 1/1；期末剔除自有组后无覆盖 → 0/1。
    assert freq[8] == (1.0, 0.0)
    # 同技能不同档不计：q9（sk_a 中档）期中只有基础/难档同技能题 → 0。
    assert freq[9] == (0.0, 0.0)
    assert freq[10] == (0.0, 0.0)
    assert freq[11] == (0.0, 0.0)
    # 同源合并组内被去重的 101/102/104 也按同一口径取值（自有组仍被剔除）。
    assert freq[101] == (0.0, 0.0)
    assert freq[102] == (0.0, 0.5)
    assert freq[104] == (0.0, 0.0)
    # 阶段练习的题没有自有卷组可剔除 → 期中 1/1。
    assert freq[13] == (1.0, 0.0)
    # 宿主卷已删但题未删，同样按无自有组计。
    assert freq[14] == (1.0, 0.0)


def test_exam_frequency_missing_skill_or_difficulty(profile_db):
    """无技能链接或无难度档的题不出现；正式卷内未链接题也不出现。"""
    freq = _frequency(profile_db)
    assert set(freq) == {1, 2, 7, 8, 9, 10, 11, 13, 14, 101, 102, 104, 105}
    assert 3 not in freq and 12 not in freq     # 计入题但无技能链接
    assert 103 not in freq                       # 去重副本且无技能链接
    assert 15 not in freq                        # 阶段练习且缺难度
    # 无期中/期末卷的本册返回空表。
    empty_snapshot = _snapshot()
    empty_snapshot["volumes"] = {VOLUME_ID: set()}
    conn = sqlite3.connect(profile_db)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            conn.execute("UPDATE papers SET exam_type = '阶段练习'")
        other_volume = curriculum_volume(volume_id=VOLUME_ID)
        freq_empty = build_exam_frequency(conn, empty_snapshot, other_volume)
    finally:
        conn.close()
    assert freq_empty == {}


# ---- 典型题选取：按教师报告口径的专用数据 ----

TA1 = "求16的算术平方根并化简结果"
TA2 = "求81的算术平方根并化简结果"          # 与 TA1 同模板
TA3 = "求144的算术平方根并化简结果"         # 与 TA1 同模板
TB1 = "比较两个实数的大小关系"
TC1 = "判断下列各数是否为无理数"
TD1 = "已知一个正数的平方根求这个数"
TD2 = "已知一个正数的平方根求原数"          # 与 TD1 同模板
TE = ["已知直角三角形两直角边为3和4求斜边长",
      "已知直角三角形两直角边为5和12求斜边长",
      "已知直角三角形两直角边为7和24求斜边长",
      "已知直角三角形两直角边为9和40求斜边长"]
TF = ["估算无理数的取值范围", "用数轴表示无理数的位置", "化简二次根式",
      "比较二次根式与整数的大小"]


def _typical_row(qid: int, paper_id: int, qn: str, qtype: str,
                 difficulty: float, text: str):
    return (qid, paper_id, qn, qtype, difficulty, text)


@pytest.fixture
def typical_db(tmp_path: Path, question_bank_database):
    """8 份互不相同的期中卷（8 个卷组），S21 内五个技能覆盖不同单元形态。"""
    db_path = tmp_path / "typical_bank.db"
    question_bank_database(db_path, taxonomy_revision=3)
    papers = [(i, f"期中卷{i}", "期中", None, str(2018 + i)) for i in range(1, 9)]
    questions = [
        # sk_A：基础 4 题 4 组；中档 3 题 2 组；难 4 题 2 组（难档不足 3 组不入选）→ 8 组 11 题
        (1001, 1, "1", "选择题", 2, TA1), (1002, 2, "1", "选择题", 2, TA2),
        (1003, 3, "1", "选择题", 3, TA1.replace("16", "25") + TB1[-4:]),
        (1004, 4, "1", "选择题", 2, TC1),
        (1011, 5, "2", "填空题", 5, TA3), (1012, 5, "3", "填空题", 5, TD1),
        (1013, 6, "2", "填空题", 5, TD2),
        (1021, 7, "4", "解答题", 8, TF[0]), (1022, 7, "5", "解答题", 8, TF[1]),
        (1023, 8, "4", "解答题", 8, TF[2]), (1024, 8, "5", "解答题", 8, TF[3]),
        # sk_B：基础 3 题 3 组；中档 3 题 3 组；难 3 题 2 组 → 7 组 9 题
        (2001, 1, "2", "选择题", 3, "写出平方根等于它本身的数"),
        (2002, 2, "2", "选择题", 3, "写出立方根等于它本身的数"),
        (2003, 3, "2", "选择题", 4, "求立方根为负数的实数"),
        (2011, 4, "3", "填空题", 5, "已知一个数的平方根为3求这个数"),
        (2012, 5, "4", "填空题", 5, "已知一个数的平方根为7求这个数"),
        (2013, 6, "3", "填空题", 6.7, "已知一个数的平方根为7求这个数"),
        (2021, 6, "4", "解答题", 8, "利用平方根性质解含参数方程"),
        (2022, 7, "6", "解答题", 8, "利用立方根性质求参数范围"),
        (2023, 7, "7", "解答题", 8, "平方根与立方根混合求参数"),
        # sk_C：难档 4 题 4 组；3021 难度高于中位数且对两个成员同模板（居中），
        # 难度窗口应把它排除 → 4 组 4 题
        (3021, 3, "3", "解答题", 9, "探究动点问题的第一部分证明综合讨论第二部分的结论与依据"),
        (3022, 5, "6", "解答题", 8, "探究动点问题的第一部分证明"),
        (3023, 8, "6", "解答题", 8, "综合讨论第二部分的结论与依据"),
        (3024, 1, "4", "解答题", 8, "数轴折叠与实数对应关系"),
        # sk_D：基础 4 题 3 组（同年份两道）；中档 2 题 1 组（不典型）；难 3 题 2 组
        (4001, 1, "3", "填空题", 3, TE[0]), (4002, 2, "3", "填空题", 3, TE[1]),
        (4003, 4, "4", "填空题", 3, TE[2]), (4004, 4, "5", "填空题", 3, TE[3]),
        (4011, 5, "7", "填空题", 5, "勾股定理逆定理判断直角三角形"),
        (4012, 5, "8", "填空题", 5, "用勾股数构造直角三角形"),
        (4021, 6, "6", "解答题", 8, "勾股定理与面积综合问题一"),
        (4022, 7, "8", "解答题", 8, "勾股定理与面积综合问题二"),
        (4023, 7, "9", "解答题", 8, "勾股定理求折叠图形边长"),
        # sk_E：基础 2 题只来自 1 个卷组 → 永不入选
        (5001, 8, "7", "选择题", 2, "识别常见的无理数其一"),
        (5002, 8, "8", "选择题", 2, "区分有理数与无理数例子"),
        # sk_G：只有中档典型单元 3 题 2 组；6001 难度高于中位数且对同模板成员
        # 居中（6001↔6002 同模板），难度窗口应排除它 → 2 组 3 题
        (6001, 1, "6", "填空题", 6.9, "二次根式混合运算题11"),
        (6002, 4, "6", "填空题", 5, "二次根式混合运算题22"),
        (6003, 6, "8", "填空题", 5, "根式加减与数轴表示"),
        # sk_F：难档 3 题 3 组 [8,9,9]，中位数 9 使低难度窗口不生效，
        # 但 ≤8.5 绝对上限只留下 7002 → 3 组 3 题
        (7001, 1, "5", "解答题", 9, "无理数估算综合应用题11"),
        (7002, 3, "4", "解答题", 8, "无理数估算综合应用题22"),
        (7003, 6, "7", "解答题", 9, "无理数估算综合应用题33"),
        # sk_H：难档 3 题 3 组，因提高名额已满不入选 → 3 组 3 题
        (8001, 2, "5", "解答题", 8, "数轴几何综合压轴题11"),
        (8002, 4, "7", "解答题", 8, "数轴几何综合压轴题22"),
        (8003, 8, "9", "解答题", 9, "数轴几何综合压轴题33"),
    ]
    fillers = [
        (9000 + i, (i % 8) + 1, str(10 + i), "解答题", 5,
         f"常规题{i}：计算{10 + i}与{3 + i}的算术组合")
        for i in range(1, 39)
    ]
    questions += fillers
    scope = [(qid, S21, [S21]) for qid, *_ in questions]
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            "INSERT INTO papers (id, title, exam_type, import_status, deleted_at, year) "
            "VALUES (?, ?, ?, 'success', ?, ?)",
            papers,
        )
        conn.executemany(
            "INSERT INTO questions (id, paper_id, question_number, question_type, "
            "difficulty, question_text, is_deleted) VALUES (?, ?, ?, ?, ?, ?, 0)",
            questions,
        )
        conn.executemany(
            "INSERT INTO question_scope_summary (question_id, primary_section_id, "
            "direct_section_ids_json) VALUES (?, ?, ?)",
            [(qid, primary, json.dumps(direct)) for qid, primary, direct in scope],
        )
        conn.commit()
    return db_path


def _typical_snapshot() -> dict:
    def hit(n: int, prefix: str) -> list[dict]:
        return [{"point_id": f"{prefix}{i}"} for i in range(n)]

    hits: dict[int, dict] = {}
    point_counts: dict[int, int] = {}
    for qid in [1001, 1004, 1011, 1012, 1013, 1021, 1022, 1023, 1024]:
        hits[qid] = {"sk_A": hit(1, f"a{qid}")}
        point_counts[qid] = 1
    hits[1002] = {"sk_A": hit(6, "a1002")}          # 纯但判定点多（非独立小题）
    point_counts[1002] = 6
    hits[1003] = {"sk_A": hit(1, "a1003"), "sk_X": hit(3, "x1003")}  # 占比不足半
    point_counts[1003] = 4
    for qid in [2001, 2002, 2003, 2011, 2012, 2013, 2021, 2022, 2023]:
        hits[qid] = {"sk_B": hit(1, f"b{qid}")}
        point_counts[qid] = 1
    for qid in [3021, 3022, 3023, 3024]:
        hits[qid] = {"sk_C": hit(1, f"c{qid}")}
        point_counts[qid] = 1
    for qid in [4001, 4002, 4003, 4004, 4011, 4012, 4021, 4022, 4023]:
        hits[qid] = {"sk_D": hit(1, f"d{qid}")}
        point_counts[qid] = 1
    for qid in [5001, 5002]:
        hits[qid] = {"sk_E": hit(1, f"e{qid}")}
        point_counts[qid] = 1
    for qid in [6001, 6002, 6003]:
        hits[qid] = {"sk_G": hit(1, f"g{qid}")}
        point_counts[qid] = 1
    for qid in [7001, 7002, 7003]:
        hits[qid] = {"sk_F": hit(1, f"f{qid}")}
        point_counts[qid] = 1
    for qid in [8001, 8002, 8003]:
        hits[qid] = {"sk_H": hit(1, f"h{qid}")}
        point_counts[qid] = 1
    return {
        "release": "rel-test",
        "nodes": {
            "sk_A": _node("sk_A", "技能·算术平方根求值", [S21]),
            "sk_B": _node("sk_B", "技能·平方根立方根概念", [S21]),
            "sk_C": _node("sk_C", "技能·实数数轴综合", [S21]),
            "sk_D": _node("sk_D", "技能·勾股定理求边", [S21]),
            "sk_E": _node("sk_E", "技能·无理数识别", [S21]),
            "sk_F": _node("sk_F", "技能·根式综合应用", [S21]),
            "sk_G": _node("sk_G", "技能·根式混合运算", [S21]),
            "sk_H": _node("sk_H", "技能·数轴综合压轴", [S21]),
            "sk_X": _node("sk_X", "技能·他节方法", [S22]),
        },
        "volumes": {VOLUME_ID: set(range(1001, 8004)) | set(range(9001, 9039))},
        "members": {i: set() for i in range(1, 9)},
        "by_question": hits,
        "point_counts": point_counts,
    }


def _build_typical(db_path: Path) -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    snapshot = _typical_snapshot()
    # members 按题的实际 paper_id 归卷。
    for row in conn.execute("SELECT id, paper_id FROM questions"):
        snapshot["members"][int(row["paper_id"])].add(int(row["id"]))
    try:
        return build_chapter_exam_profile(conn, snapshot, curriculum_volume(volume_id=VOLUME_ID))
    finally:
        conn.close()


def _typical_map(result: dict) -> dict:
    return {
        row["key"]: row["typical"]
        for row in _section(_chapter(result, CH2), S21)["skills"]
    }


def test_typical_budget_cap_and_pass_order(typical_db):
    result = _build_typical(typical_db)
    section = _section(_chapter(result, CH2), S21)
    assert section["main_count"] == 82          # B = clamp(round(82/9),2,10) = 9
    typical = _typical_map(result)
    picks = [pick for row in typical.values() for pick in row]
    # 4 个入口 + 2 个变式 + 限额 2 个提高（C/F/H 三个合格难档单元只取 2）= 8 题。
    assert len(picks) == 8
    hard = [pick for pick in picks if pick["role"] == "提高变化"]
    assert len(hard) == 2                       # H = max(1, 9//4) = 2
    # 发出的技能次序 = 卷组覆盖排序：A(8) B(7) D(6) C(4) G(3,名) F(3,名)。
    emitted = [key for key, row in typical.items() if row]
    assert emitted == ["sk_A", "sk_B", "sk_D", "sk_C", "sk_G", "sk_F"]
    flat = [pick["role"] for row in typical.values() for pick in row]
    assert flat == ["基础入口", "常见变式", "基础入口", "常见变式",
                    "基础入口", "提高变化", "常见变式", "提高变化"]
    assert all(pick["same_tier_count"] >= 2 and pick["group_count"] >= 2
               for pick in picks)
    assert all(pick["group_count"] >= 3 for pick in hard)


def test_typical_filters_windows_and_tiebreaks(typical_db):
    result = _build_typical(typical_db)
    typical = _typical_map(result)
    entry_a = typical["sk_A"][0]
    # 占比不足半的 1003 被剔除；判定点 >4 的 1002 不作基础入口；
    # 1001 与同模板的 1002 互相抬高居中度后胜出。
    assert entry_a["question_id"] == 1001 and entry_a["tier"] == "basic"
    assert entry_a["role"] == "基础入口"
    # 变式跳过与已选入口同模板的 1011；并列时取卷年新者 → 1013。
    assert typical["sk_A"][1]["question_id"] == 1013
    assert typical["sk_A"][1]["role"] == "常见变式"
    # 入口难度窗口：sk_B 基础档中位数 3，2003（难度4、卷年最新）被窗口排除 → 2002。
    assert typical["sk_B"][0]["question_id"] == 2002
    # 变式难度窗口 ±1：sk_B 中档中位数 5，2013（6.7）被排除 → 卷年新者 2012。
    assert typical["sk_B"][1]["question_id"] == 2012
    # 提高难度窗口：sk_C 难档中位数 8，3021（难度9、居中最高）被排除 → 3023。
    assert typical["sk_C"][0]["question_id"] == 3023
    # 提高绝对上限 ≤8.5：sk_F 难档 [8,9,9] 中位数 9 让窗口失效，上限只留 7002。
    assert typical["sk_F"][0]["question_id"] == 7002
    # 中档单元作入口也用下窗口：sk_G 中位数 5，6001（6.9）被排除 → 6002。
    assert typical["sk_G"][0]["question_id"] == 6002
    assert typical["sk_G"][0]["role"] == "常见变式"
    # 同年份并列取题号小者：sk_D 基础入口 4003（早于同年的 4004）。
    assert typical["sk_D"][0]["question_id"] == 4003


def test_typical_reason_and_single_group_excluded(typical_db):
    result = _build_typical(typical_db)
    typical = _typical_map(result)
    assert typical["sk_E"] == []                # 只有一个卷组，永不入选
    assert typical["sk_H"] == []                # 提高名额已满
    assert all(pick["tier"] != "mid" for pick in typical["sk_D"])  # 单组中档不选
    entry = typical["sk_A"][0]
    assert "第1常考技能" in entry["reason"] and "8套试卷" in entry["reason"]
    assert typical["sk_A"][1]["reason"].startswith("同技能中档变式")
    assert typical["sk_C"][0]["reason"].startswith("出现在")
    # 卡片已显示角色，理由文本不得重复角色名。
    assert all(
        pick["role"] not in pick["reason"]
        for row in typical.values() for pick in row
    )


def test_typical_deterministic(typical_db):
    first = _build_typical(typical_db)
    second = _build_typical(typical_db)
    assert _typical_map(first) == _typical_map(second)


def test_primary_type_statistics_keep_paper_groups_rates_and_difficulty(profile_db):
    from question_bank.services.chapter_exam_profile import _prepare, UNLINKED
    conn = sqlite3.connect(profile_db)
    conn.row_factory = sqlite3.Row
    volume = curriculum_volume(volume_id=VOLUME_ID)
    baseline = build_chapter_exam_profile(conn, _snapshot(), volume)
    baseline_frequency = build_exam_frequency(conn, _snapshot(), volume)
    prep = _prepare(conn, _snapshot(), volume)
    types = {'sk_a': S21 + '_t01', 'sk_b': S22 + '_t01'}
    snapshot = _snapshot()
    snapshot['nodes'].update({types[key]: _node(types[key], '题型' + key, [S21 if key == 'sk_a' else S22]) for key in types})
    snapshot['primary_types'] = {qid: types[rec['skill_key']] for qid, rec in prep['recs'].items() if rec['skill_key'] != UNLINKED}
    snapshot['primary_types'].update({qid: types[next(iter(hits))] for qid, hits in snapshot['by_question'].items() if qid not in snapshot['primary_types']})
    snapshot['type_hits'] = {qid: {types[key]: hits for key, hits in values.items()} for qid, values in snapshot['by_question'].items()}
    result = build_chapter_exam_profile(conn, snapshot, volume)
    assert result['stages'] == baseline['stages']
    assert result['merged_groups'] == baseline['merged_groups']
    for chapter, old_chapter in zip(result['chapters'], baseline['chapters']):
        assert chapter['totals'] == old_chapter['totals']
        assert chapter['difficulty'] == old_chapter['difficulty']
        for section, old_section in zip(chapter['sections'], old_chapter['sections']):
            assert section['coverage'] == old_section['coverage']
            assert section['overview'] == old_section['overview']
            assert all(row['target_kind'] == 'type' for row in section['skills'])
    assert build_exam_frequency(conn, snapshot, volume) == baseline_frequency
    snapshot['primary_types'][1] = types['sk_b']
    changed = build_chapter_exam_profile(conn, snapshot, volume)
    section = _section(_chapter(changed, CH2), S21)
    assert 1 in next(row for row in section['skills'] if row['key'] == types['sk_b'])['cells']['midterm']['choice_basic']
    assert 1 not in next(row for row in section['skills'] if row['key'] == types['sk_a'])['cells']['midterm']['choice_basic']
    conn.close()
