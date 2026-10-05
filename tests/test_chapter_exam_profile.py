"""章节考情统计：同源卷合并、归属、技能与典型题的口径测试。"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from question_bank.services.chapter_exam_profile import build_chapter_exam_profile
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
        conn.execute(
            "INSERT INTO question_frequency_cache (question_id, score_midterm, "
            "score_final) VALUES (7, 5.0, 0.0)"
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
    }
    return {
        "release": "rel-test",
        "nodes": {
            "sk_a": _node("sk_a", "技能·区分有理数无理数", [S21]),
            "sk_b": _node("sk_b", "技能·平方根求法", [S22]),
        },
        "volumes": {VOLUME_ID: {1, 2, 3, 7, 8, 9, 10, 11, 12, 13, 14, 101, 102, 103, 104, 105}},
        "members": {
            1: {1, 2, 3, 7},
            2: {101, 102, 103, 104, 105},
            3: {8, 9, 10, 11},
            4: {12},
            5: {13},
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


def test_typical_picks_medoid_and_near_duplicate_skip(profile_db):
    result = _build(profile_db)
    skills = {
        row["key"]: row
        for row in _section(_chapter(result, CH2), S21)["skills"]
    }
    picks = skills["sk_a"]["typical"]
    # 基础档两题相似度并列，题频更高者胜出；中档题与已选的 7 近乎相同被跳过。
    assert [(pick["tier"], pick["question_id"]) for pick in picks] == [
        ("basic", 7), ("mid", 11), ("hard", 105),
    ]
    assert [(pick["same_tier_count"], pick["group_count"]) for pick in picks] == [
        (2, 1), (2, 1), (1, 1),
    ]
