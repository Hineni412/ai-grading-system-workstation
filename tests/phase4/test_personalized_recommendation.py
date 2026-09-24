from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest

from question_bank.database.schema import connect, initialize_database
from question_bank.knowledge_graph_release import (
    bootstrap_release,
    load_release_for_taxonomy_revision,
)
from question_bank.knowledge_graph_release.contracts import (
    KnowledgeGraphRelease,
)
from question_bank.recommendation.personalized import (
    PersonalizedRecommendationConfig,
    PersonalizedRecommendationModule,
    RecommendationEditCommand,
    RecommendationEditInvalid,
    RecommendationRequestConflict,
    RecommendationRevisionConflict,
    RecommendationSourceChanged,
    _allowed_keys_for_volume,
    _group_needs,
    _chapter_group_members,
)
from question_bank.training_criteria import QuestionAnalysisInputLoader
from tests.current_knowledge_support import install_current_knowledge


NOW = datetime(2026, 7, 30, 8, 0, tzinfo=UTC)
LOCAL_ONE = "ki_00000000000000000000000000000001"
LOCAL_TWO = "ki_00000000000000000000000000000002"
BNU_CHAPTER4 = "kp_bnu24_math_g7_lower_4"
BNU_TARGET = f"{BNU_CHAPTER4}_2_2"
BNU_PREREQ_NEAR = f"{BNU_CHAPTER4}_2_1"
BNU_PREREQ_EARLIER = f"{BNU_CHAPTER4}_1_17"
BNU_TRANSFER_SIBLING = f"{BNU_CHAPTER4}_2_3"
BNU_TRANSFER_OTHER = f"{BNU_CHAPTER4}_1_1"
BNU_OTHER_CHAPTER = "kp_bnu24_math_g7_lower_5_1_1"
BNU_FIRST_LEAF = "kp_bnu24_math_g7_upper_1_1_1"
BNU_G8_LEAF = "kp_bnu24_math_g8_upper_1_1_1"
BNU_G8_QUESTION = 17


def test_direct_targets_do_not_depend_on_difficulty_profile(bnu24_difficulty_module):
    module = bnu24_difficulty_module
    with connect(module.db_path) as connection:
        row = connection.execute("SELECT question_id, version_id, criteria_json FROM training_criterion_versions ORDER BY question_id LIMIT 1").fetchone()
        question_id = row["question_id"]
        criteria = json.loads(row["criteria_json"])
        criteria["solution_evidence"] = {"parts": [{"part_id": "p1", "response_mode": "process_required",
            "evidence_points": [{"fine_term_links": [{"role": "direct", "core_resolution": {
                "status": "resolved", "stable_keys": [BNU_OTHER_CHAPTER]}}]}]}]}
        connection.execute("UPDATE training_criterion_versions SET criteria_json = ? WHERE version_id = ?",
                           (json.dumps(criteria), row["version_id"]))
    scoped, _, _ = module._source_snapshot(knowledge_keys=(BNU_OTHER_CHAPTER,))
    candidate = next(item for item in scoped if item["question_id"] == question_id)
    assert candidate["stable_keys"] == [BNU_OTHER_CHAPTER]
    assert BNU_OTHER_CHAPTER in candidate["required_keys"]
    assert candidate["response_modes_by_key"][BNU_OTHER_CHAPTER] == ["process_required"]


def _set_practice_parts(module, question_id, parts):
    with connect(module.db_path) as connection:
        row = connection.execute("SELECT version_id, criteria_json FROM training_criterion_versions WHERE question_id = ?", (question_id,)).fetchone()
        criteria = json.loads(row["criteria_json"])
        criteria["solution_evidence"] = {"parts": parts}
        connection.execute("UPDATE training_criterion_versions SET criteria_json = ? WHERE version_id = ?",
                           (json.dumps(criteria), row["version_id"]))


def _practice_part(key, mode, observable, part_id="part1", supporting=()):
    return {"part_id": part_id, "response_mode": mode, "evidence_points": [{"target": observable,
        "observable_evidence": observable, "fine_term_links": [
            {"role": role, "fine_term_id": target, "core_resolution": {"status": "resolved", "stable_keys": [target]}}
            for role, target in [("direct", key), *(("supporting_prerequisite", target) for target in supporting)]]}]}


def test_blank_or_ineligible_evidence_does_not_invent_an_error():
    from question_bank.recommendation.personalized import _training_tasks
    source = {"full_score": 5, "score_awarded": 0, "source_kind": "current_exam",
              "deduction_reason": "未作答，空白", "assessment": {"granularity": "part", "eligible": True}}
    point = {"source_question_refs": [source], "actionable_reasons": ["计算错误"]}
    assert [task["code"] for task in _training_tasks(point)] == ["diagnostic_check"]
    for reason in ("未作答，未见计算过程", "本问空白，未提供推理依据", "未答，无法体现数量关系"):
        source["deduction_reason"] = reason
        assert [task["code"] for task in _training_tasks(point)] == ["diagnostic_check"]
    source["deduction_reason"] = "已写出正确计算过程，但没有写出依据"
    assert [task["code"] for task in _training_tasks(point)] == ["written_reasoning"]
    source["deduction_reason"] = "数量关系正确，推理依据完整，但计算错误"
    assert [task["code"] for task in _training_tasks(point)] == ["calculation_check"]
    source.update(score_awarded=2, deduction_reason="第一步移项错误，后续步骤空白")
    assert [task["code"] for task in _training_tasks(point)] == ["calculation_check"]
    source["score_awarded"] = 0
    assert [task["code"] for task in _training_tasks(point)] == ["calculation_check"]
    source["assessment"]["eligible"] = False
    assert _training_tasks(point) == []
    source["assessment"]["eligible"] = True
    source["deduction_reason"] = ""
    assert _training_tasks(point) == []
    source.update(source_kind="historical_exam", deduction_reason="计算错误")
    point["source_question_refs"].append({**source, "source_kind": "current_exam", "score_awarded": 5})
    assert _training_tasks(point) == []


def test_progress_checks_later_small_parts_support_and_unknowns(bnu24_difficulty_module):
    from question_bank.recommendation.personalized import _allowed_keys_for_config, _question_evidence_metadata
    module = bnu24_difficulty_module
    candidates, _, _ = module._source_snapshot()
    base = next(item for item in candidates if BNU_TARGET in item["stable_keys"])
    parts = [_practice_part(BNU_TARGET, "process_required", "写出理由", supporting=(BNU_PREREQ_NEAR,)),
             _practice_part(BNU_OTHER_CHAPTER, "process_required", "写出理由", "part2")]
    item = {**base, **_question_evidence_metadata({"parts": parts}, module.current_knowledge)}
    def choose(config):
        return module._eligible_candidates([item], stage="direct", target_keys=(BNU_TARGET,), maintenance=False,
            used=set(), recent=set(), excluded=set(), config=config, allowed_keys=_allowed_keys_for_config(config))
    config = PersonalizedRecommendationConfig(target_keys=(BNU_TARGET,))
    assert not choose(config)  # 推导进度只到第4章，第2问第5章还不能放入。
    assert choose(replace(config, teaching_progress_chapter_id="bnu24-math-g7-lower-c05"))
    parts[1]["evidence_points"][0]["fine_term_links"][0]["core_resolution"] = {"status": "unresolved", "stable_keys": []}
    item.update(_question_evidence_metadata({"parts": parts}, module.current_knowledge))
    assert not choose(replace(config, teaching_progress_chapter_id="bnu24-math-g7-lower-c05"))
    with pytest.raises(ValueError, match="teaching_progress"):
        replace(config, teaching_progress_chapter_id="unknown-chapter")


def _grouping_diagnosis() -> dict:
    def point(key, mastery, *, difficulty=3, coarse=False):
        return {"knowledge_key": key, "knowledge_point": key, "mastery": mastery,
                "evidence_count": 1, "effective_weight": 1, "direct_evidence_count": 1,
                "source_question_refs": [{"session_id": 1, "session_name": "合成考试", "question_id": key,
                    "bank_question_id": 10000, "score_awarded": 0, "full_score": 5,
                    "assessment": {"granularity": "whole_question" if coarse else "part",
                        "eligible": True, "part_difficulty": difficulty, "evidence_weight": 1}}]}
    values = [
        ("A", "一班", .5, [point(BNU_TARGET, .42)]),
        ("B", "二班", .9, [point(BNU_TARGET, .46)]),
        ("C", "一班", .5, [point(BNU_PREREQ_NEAR, .43)]),
        ("D", "三班", .7, [point(BNU_PREREQ_NEAR, .47)]),
        ("E", "二班", None, []),
        ("F", "一班", .5, [point(BNU_TARGET, .45, coarse=True)]),
        ("G", "二班", .8, [point(BNU_TARGET, .46, difficulty=9)]),
    ]
    return {"students": [{"student_id": sid, "student_name": "同名学生" if sid in {"A", "B"} else f"合成{sid}",
                         "student_code": f"S{sid}", "class_id": cls, "score_rate": score,
                         "weak_points": points} for sid, cls, score, points in values],
            "exam_scope": {"mode": "current", "session_ids": [1]}}


@pytest.fixture()
def recommendation_module(tmp_path: Path) -> PersonalizedRecommendationModule:
    db_path = tmp_path / "question_bank.db"
    data_root = tmp_path / "data"
    initialize_database(db_path)
    install_current_knowledge(db_path)
    _seed_recommendation_sources(db_path, data_root)
    return PersonalizedRecommendationModule(
        db_path=db_path,
        data_root=data_root,
        clock=lambda: NOW,
    )


@pytest.fixture()
def bnu24_recommendation_module(
    tmp_path: Path,
) -> PersonalizedRecommendationModule:
    db_path = tmp_path / "question_bank.db"
    data_root = tmp_path / "data"
    initialize_database(db_path)
    install_current_knowledge(db_path, taxonomy_revision=4)
    _seed_bnu24_recommendation_sources(db_path, data_root)
    return PersonalizedRecommendationModule(
        db_path=db_path,
        data_root=data_root,
        clock=lambda: NOW,
    )


@pytest.fixture()
def bnu24_difficulty_module(
    tmp_path: Path,
) -> PersonalizedRecommendationModule:
    db_path = tmp_path / "question_bank.db"
    data_root = tmp_path / "data"
    initialize_database(db_path)
    install_current_knowledge(db_path, taxonomy_revision=4)
    _seed_bnu24_difficulty_sources(db_path, data_root)
    return PersonalizedRecommendationModule(
        db_path=db_path,
        data_root=data_root,
        clock=lambda: NOW,
    )


@pytest.fixture()
def bnu24_expansion_module(
    tmp_path: Path,
) -> PersonalizedRecommendationModule:
    db_path = tmp_path / "question_bank.db"
    data_root = tmp_path / "data"
    initialize_database(db_path)
    _install_bnu24_release_with_mainline_relations(db_path)
    _seed_bnu24_expansion_sources(db_path, data_root)
    return PersonalizedRecommendationModule(
        db_path=db_path,
        data_root=data_root,
        clock=lambda: NOW,
    )


def test_allowed_keys_for_volume_covers_current_and_earlier_volumes_only() -> (
    None
):
    assert _allowed_keys_for_volume("") is None
    keys = _allowed_keys_for_volume("bnu24-math-g7-lower")
    assert keys is not None
    assert BNU_TARGET in keys
    assert BNU_FIRST_LEAF in keys
    # 章级 key 同样在边界内，供范围展开比对。
    assert BNU_CHAPTER4 in keys
    assert BNU_G8_LEAF not in keys
    g7_upper_only = _allowed_keys_for_volume("bnu24-math-g7-upper")
    assert g7_upper_only is not None
    assert BNU_FIRST_LEAF in g7_upper_only
    assert BNU_TARGET not in g7_upper_only


def test_config_rejects_unknown_curriculum_volume() -> None:
    with pytest.raises(ValueError, match="curriculum_volume_id"):
        PersonalizedRecommendationConfig(
            curriculum_volume_id="bnu24-math-g6-lower"
        )


def _seed_recommendation_sources(db_path: Path, data_root: Path) -> None:
    questions = (
        (1, "1", "单项选择题", "坐标基础选择题", "4"),
        (2, "2", "填空题", "代数基础填空题", "3"),
        (3, "3", "计算题", "解一元一次方程", "5"),
        (4, "4", "证明题", "证明两个三角形全等", "6"),
        (5, "5", "作图题", "尺规作图", "5"),
        (6, "6", "计算题", "解另一道一元一次方程", "7"),
        (7, "7", "计算题", "解第三道一元一次方程", "8"),
        # 难度天花板下的保底简单题：薄弱学生也应配到合适难度。
        # 题型错开：同一细点同一题型一卷只出一道。
        (8, "8", "计算题", "解基础一元一次方程", "4"),
        (9, "9", "填空题", "再解一道基础一元一次方程", "4"),
        (10, "10", "证明题", "基础全等三角形证明", "4"),
    )
    stable_keys = {
        1: LOCAL_ONE,
        2: LOCAL_TWO,
        3: "kp_alg_linear_equation",
        4: "kp_geo_triangle_congruence",
        5: "kp_geo_construction",
        6: "kp_alg_linear_equation",
        7: "kp_alg_linear_equation",
        8: "kp_alg_linear_equation",
        9: "kp_alg_linear_equation",
        10: "kp_geo_triangle_congruence",
    }
    with connect(db_path) as connection:
        connection.execute(
            "INSERT INTO papers (id, title, import_status) VALUES (1, '合成题源', 'ready')"
        )
        for stable_key, name in (
            (LOCAL_ONE, "合成基础一"),
            (LOCAL_TWO, "合成基础二"),
        ):
            connection.execute(
                """
                INSERT INTO knowledge_tag_identities (
                    stable_key, display_name, origin, status
                ) VALUES (?, ?, 'local', 'active')
                """,
                (stable_key, name),
            )
        for question_id, number, question_type, text, difficulty in questions:
            connection.execute(
                """
                INSERT INTO questions (
                    id, paper_id, question_number, question_type,
                    question_text, answer_text, difficulty
                ) VALUES (?, 1, ?, ?, ?, '合成答案', ?)
                """,
                (
                    question_id,
                    number,
                    question_type,
                    text,
                    difficulty,
                ),
            )
            tag = connection.execute(
                """
                INSERT INTO question_tags (
                    question_id, tag_type, tag_value, source
                ) VALUES (?, 'knowledge_point', ?, 'synthetic')
                """,
                (question_id, stable_keys[question_id]),
            )
            connection.execute(
                """
                INSERT INTO knowledge_tag_identity_mappings (
                    question_tag_id, stable_key, source_value_snapshot,
                    mapping_source
                ) VALUES (?, ?, ?, 'teacher')
                """,
                (
                    int(tag.lastrowid),
                    stable_keys[question_id],
                    stable_keys[question_id],
                ),
            )
        _insert_relation(
            connection,
            "rel-linear-prerequisite",
            "kp_alg_linear_equation",
            LOCAL_TWO,
            "prerequisite",
        )
        _insert_relation(
            connection,
            "rel-congruence-prerequisite",
            "kp_geo_triangle_congruence",
            LOCAL_ONE,
            "prerequisite",
        )
        _insert_relation(
            connection,
            "rel-construction-related",
            "kp_geo_construction",
            "kp_geo_triangle_congruence",
            "related",
        )
        _insert_relation(
            connection,
            "rel-function-prerequisite",
            "kp_fun_linear",
            "kp_alg_linear_equation",
            "prerequisite",
        )
        _insert_relation(
            connection,
            "rel-function-related",
            "kp_fun_linear",
            "kp_geo_construction",
            "related",
        )

    _approve_synthetic_criteria(
        db_path,
        data_root,
        tuple(item[0] for item in questions),
    )


def _insert_bnu24_questions(
    connection,
    questions: tuple[tuple[int, str, str, str, str, str], ...],
) -> None:
    connection.execute(
        "INSERT OR IGNORE INTO papers (id, title, import_status)"
        " VALUES (1, 'BNU24合成题源', 'ready')"
    )
    for (
        question_id,
        number,
        question_type,
        text,
        difficulty,
        stable_key,
    ) in questions:
        connection.execute(
            """
            INSERT INTO questions (
                id, paper_id, question_number, question_type,
                question_text, answer_text, difficulty
            ) VALUES (?, 1, ?, ?, ?, '合成答案', ?)
            """,
            (question_id, number, question_type, text, difficulty),
        )
        connection.execute(
            """
            INSERT INTO question_tags (
                question_id, tag_type, tag_value, source
            ) VALUES (?, 'knowledge_point', ?, 'synthetic')
            """,
            (question_id, stable_key),
        )


def _insert_bnu24_questions_with_skill_tags(
    db_path: Path,
    questions: tuple[
        tuple[int, str, str, str, str, str, tuple[str, ...]], ...
    ],
) -> None:
    """带方法标签的 bnu24 合成题：相似题判重测试用。"""
    with connect(db_path) as connection:
        for (
            question_id,
            number,
            question_type,
            text,
            difficulty,
            stable_key,
            methods,
        ) in questions:
            connection.execute(
                """
                INSERT INTO questions (
                    id, paper_id, question_number, question_type,
                    question_text, answer_text, difficulty
                ) VALUES (?, 1, ?, ?, ?, '合成答案', ?)
                """,
                (question_id, number, question_type, text, difficulty),
            )
            connection.execute(
                """
                INSERT INTO question_tags (
                    question_id, tag_type, tag_value, source
                ) VALUES (?, 'knowledge_point', ?, 'synthetic')
                """,
                (question_id, stable_key),
            )
            for method in methods:
                connection.execute(
                    """
                    INSERT INTO question_tags (
                        question_id, tag_type, tag_value, source
                    ) VALUES (?, 'method', ?, 'synthetic')
                    """,
                    (question_id, method),
                )


def _seed_bnu24_recommendation_sources(db_path: Path, data_root: Path) -> None:
    questions = (
        (11, "11", "计算题", "全等三角形性质计算", "5", BNU_TARGET),
        (12, "12", "计算题", "全等三角形概念计算", "4", BNU_PREREQ_NEAR),
        (13, "13", "填空题", "重心性质填空", "3", BNU_PREREQ_EARLIER),
        (14, "14", "计算题", "分割全等图形计算", "5", BNU_TRANSFER_SIBLING),
        (15, "15", "单项选择题", "三角形识别选择", "3", BNU_TRANSFER_OTHER),
        (16, "16", "计算题", "轴对称图形识别计算", "4", BNU_OTHER_CHAPTER),
        (21, "21", "单项选择题", "常见几何体选择", "3", BNU_FIRST_LEAF),
    )
    with connect(db_path) as connection:
        _insert_bnu24_questions(connection, questions)
    _approve_synthetic_criteria(
        db_path,
        data_root,
        tuple(item[0] for item in questions),
    )


def _seed_bnu24_difficulty_sources(db_path: Path, data_root: Path) -> None:
    questions = (
        (31, "31", "填空题", "全等三角形性质基础填空", "2", BNU_TARGET),
        (32, "32", "填空题", "全等三角形性质中档填空", "5", BNU_TARGET),
        (33, "33", "填空题", "全等三角形性质拔高填空", "8", BNU_TARGET),
        (41, "41", "填空题", "分割全等图形填空一", "4", BNU_TRANSFER_SIBLING),
        (42, "42", "填空题", "分割全等图形填空二", "5", BNU_TRANSFER_SIBLING),
        (43, "43", "填空题", "分割全等图形填空三", "6", BNU_TRANSFER_SIBLING),
        (44, "44", "填空题", "分割全等图形填空四", "7", BNU_TRANSFER_SIBLING),
        (45, "45", "填空题", "分割全等图形填空五", "8", BNU_TRANSFER_SIBLING),
        (46, "46", "填空题", "分割全等图形填空六", "9", BNU_TRANSFER_SIBLING),
        (51, "51", "填空题", "全等三角形概念基础填空", "3", BNU_PREREQ_NEAR),
        (52, "52", "填空题", "全等三角形概念中档填空", "6", BNU_PREREQ_NEAR),
    )
    with connect(db_path) as connection:
        _insert_bnu24_questions(connection, questions)
    _approve_synthetic_criteria(
        db_path,
        data_root,
        tuple(item[0] for item in questions),
    )


def _seed_bnu24_g8_question(db_path: Path, data_root: Path) -> None:
    """Seed one Grade 8A candidate on top of the bnu24 fixture's paper."""
    with connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO questions (
                id, paper_id, question_number, question_type,
                question_text, answer_text, difficulty
            ) VALUES (?, 1, '17', '计算题', '八上合成计算题', '合成答案', '4')
            """,
            (BNU_G8_QUESTION,),
        )
        connection.execute(
            """
            INSERT INTO question_tags (
                question_id, tag_type, tag_value, source
            ) VALUES (?, 'knowledge_point', ?, 'synthetic')
            """,
            (BNU_G8_QUESTION, BNU_G8_LEAF),
        )
    _approve_synthetic_criteria(db_path, data_root, (BNU_G8_QUESTION,))


def _seed_g8_paper_with_g7_question(db_path: Path, data_root: Path) -> None:
    """一道八上卷里的七下知识点题：知识点合规但来源卷越界。"""
    with connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO papers (id, title, import_status, grade, semester)
            VALUES (2, '八上合成题源', 'ready', '八年级', '上学期')
            """
        )
        connection.execute(
            """
            INSERT INTO questions (
                id, paper_id, question_number, question_type,
                question_text, answer_text, difficulty
            ) VALUES (18, 2, '18', '证明题', '八上卷里的全等证明题', '合成答案', '3')
            """
        )
        connection.execute(
            """
            INSERT INTO question_tags (
                question_id, tag_type, tag_value, source
            ) VALUES (18, 'knowledge_point', ?, 'synthetic')
            """,
            (BNU_TARGET,),
        )
    _approve_synthetic_criteria(db_path, data_root, (18,))


def _install_bnu24_release_with_mainline_relations(db_path: Path) -> None:
    """Install a rev4 release plus two synthetic ancestor-level relations."""
    release = load_release_for_taxonomy_revision(4)
    payload = release.to_dict()
    payload.pop("content_hash", None)
    source_id = str(payload["sources"][0]["source_id"])
    payload["release_id"] = f"{payload['release_id']}-mainline-test"
    payload["relations"] = [
        *payload["relations"],
        {
            "relation_key": hashlib.sha256(
                "rel-section-prerequisite".encode()
            ).hexdigest(),
            "source_key": f"{BNU_CHAPTER4}_2",
            "target_key": "kp_bnu24_math_g7_lower_1_1",
            "relation_type": "prerequisite",
            "rationale": "合成节级先修关系",
            "basis_kind": "curriculum_structure",
            "strength": "recommended",
            "evidence_source_ids": [source_id],
            "source_locator": "合成来源定位",
        },
        {
            "relation_key": hashlib.sha256(
                "rel-chapter-related".encode()
            ).hexdigest(),
            "source_key": BNU_CHAPTER4,
            "target_key": "kp_bnu24_math_g7_lower_5",
            "relation_type": "related",
            "rationale": "合成章级相关关系",
            "basis_kind": "curriculum_structure",
            "strength": "contextual",
            "evidence_source_ids": [source_id],
            "source_locator": "合成来源定位",
        },
    ]
    bootstrap_release(
        Path(db_path),
        KnowledgeGraphRelease.from_mapping(payload),
        actor_ref="test-suite",
        source_reference="synthetic-mainline-test-release",
        reason="install mainline-relation test release",
    )


def _seed_bnu24_expansion_sources(db_path: Path, data_root: Path) -> None:
    questions = (
        (61, "61", "计算题", "整式乘法计算一", "4", "kp_bnu24_math_g7_lower_1_1_1"),
        (62, "62", "计算题", "整式乘法计算二", "5", "kp_bnu24_math_g7_lower_1_1_2"),
        (63, "63", "计算题", "轴对称图形识别计算", "5", BNU_OTHER_CHAPTER),
        (64, "64", "计算题", "全等三角形性质计算", "5", BNU_TARGET),
    )
    with connect(db_path) as connection:
        _insert_bnu24_questions(connection, questions)
    _approve_synthetic_criteria(
        db_path,
        data_root,
        tuple(item[0] for item in questions),
    )


def _bnu24_mastery_diagnosis(
    entries: tuple[tuple[str, float | None], ...],
) -> dict[str, object]:
    students = []
    for index, (student_id, mastery) in enumerate(entries, start=1):
        weak_points = []
        if mastery is not None:
            weak_points.append(
                {
                    "knowledge_point": BNU_TARGET,
                    "mastery": mastery,
                    "evidence_count": 2,
                    "source_question_refs": [],
                }
            )
        students.append(
            {
                "student_id": student_id,
                "student_code": f"D{index:02d}",
                "student_name": f"合成学生D{index}",
                "class_id": "SYN-C01",
                "weak_points": weak_points,
            }
        )
    return {
        "students": students,
        "exam_scope": {"mode": "current", "session_ids": [1]},
    }


def _bnu24_diagnosis() -> dict[str, object]:
    return {
        "students": [
            {
                "student_id": "SYN-B01",
                "student_code": "B01",
                "student_name": "合成学生B1",
                "class_id": "SYN-C01",
                "weak_points": [],
            }
        ],
        "exam_scope": {"mode": "current", "session_ids": [1]},
    }


def _approve_synthetic_criteria(
    db_path: Path,
    data_root: Path,
    question_ids: tuple[int, ...],
) -> None:
    loader = QuestionAnalysisInputLoader(
        db_path=db_path,
        data_root=data_root,
    )
    loaded = loader.load(question_ids)
    from question_bank.current_knowledge import CurrentKnowledgeResolver
    resolver = CurrentKnowledgeResolver.from_active_database(db_path)
    with connect(db_path) as connection:
        for question in loaded:
            keys = sorted({identity.stable_key for tag in connection.execute(
                "SELECT tag_value FROM question_tags WHERE question_id = ? AND tag_type IN ('knowledge_point', 'canonical_knowledge_id')",
                (question.question_id,)).fetchall() for identity in resolver.resolve(tag["tag_value"])})
            kind = connection.execute("SELECT question_type FROM questions WHERE id = ?", (question.question_id,)).fetchone()[0]
            version_id = hashlib.sha256(
                f"criterion:{question.question_id}".encode()
            ).hexdigest()
            criteria = {
                "schema_version": "training-criteria-draft-v1",
                "question_id": question.question_id,
                "source_content_hash": question.criterion_source_content_hash,
                "question_type": question.question_type_group,
                "points": [
                    {
                        "point_id": "p1",
                        "target": "完成题目要求",
                        "observable_evidence": "答案中有可核对结果",
                        "equivalent_rules": [],
                        "counterexamples": [],
                    }
                ],
                "auxiliary_rules": [],
                "rationale": "合成测试判定点",
                "confidence": 1.0,
                "source_kind": "confirmed_rubric_adapter",
                "solution_evidence": {"parts": [{"part_id": "part1",
                    "response_mode": "exact_objective" if any(word in kind for word in ("选择", "填空")) else "process_required",
                    "evidence_points": [{"target": "计算并说明数量关系", "observable_evidence": "写出等式、依据和单位",
                        "fine_term_links": [{"fine_term_id": key, "role": "direct",
                                             "core_resolution": {"status": "resolved", "stable_keys": [key]}} for key in keys]}]}]},
            }
            criteria_json = json.dumps(
                criteria,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            connection.execute(
                """
                INSERT INTO training_criterion_versions (
                    version_id, question_id, version_number,
                    source_content_hash, schema_version, status,
                    source_kind, source_reference, criteria_json,
                    criteria_hash, quality_status, created_by,
                    decision_by, decision_note, decided_at
                ) VALUES (?, ?, 1, ?, 'training-criteria-draft-v1',
                          'approved', 'teacher_manual', ?, ?, ?,
                          'passed', 'synthetic', 'teacher-1',
                          'synthetic approval', datetime('now','localtime'))
                """,
                (
                    version_id,
                    question.question_id,
                    question.criterion_source_content_hash,
                    f"synthetic:{question.question_id}",
                    criteria_json,
                    hashlib.sha256(criteria_json.encode()).hexdigest(),
                ),
            )
            connection.execute(
                """
                INSERT INTO training_criterion_heads (
                    question_id, current_version_id, approved_version_id,
                    current_source_hash, revision
                ) VALUES (?, ?, ?, ?, 1)
                """,
                (
                    question.question_id,
                    version_id,
                    version_id,
                    question.criterion_source_content_hash,
                ),
            )


def _insert_relation(
    connection,
    relation_id: str,
    source_key: str,
    target_key: str,
    relation_type: str,
) -> None:
    if relation_type == "related" and source_key > target_key:
        source_key, target_key = target_key, source_key
    connection.execute(
        """
        INSERT INTO knowledge_relations (
            relation_id, source_key, target_key, relation_type,
            status, source_kind, rationale, decision_by,
            decision_note, decided_at
        ) VALUES (?, ?, ?, ?, 'confirmed', 'teacher',
                  '合成已确认关系', 'teacher-1',
                  '合成确认', datetime('now','localtime'))
        """,
        (relation_id, source_key, target_key, relation_type),
    )


def _diagnosis(
    *,
    student_ids: tuple[str, ...] = (
        "SYN-S01",
        "SYN-S02",
        "SYN-S03",
        "SYN-S04",
        "SYN-S05",
    ),
) -> dict[str, object]:
    weaknesses = {
        "SYN-S01": "一元一次方程",
        "SYN-S02": "三角形全等",
        "SYN-S03": "尺规作图",
        "SYN-S04": "一次函数",
    }
    students = []
    for index, student_id in enumerate(student_ids, start=1):
        weak = weaknesses.get(student_id)
        students.append(
            {
                "student_id": student_id,
                "student_code": f"S{index:02d}",
                "student_name": f"合成学生{index}",
                "class_id": "SYN-C01",
                "weak_points": (
                    []
                    if weak is None
                    else [
                        {
                            "knowledge_point": weak,
                            "mastery": 0.35 + index / 100,
                            "evidence_count": 2,
                            "source_question_refs": [
                                {
                                    "session_id": 1,
                                    "question_id": f"EX-{index}",
                                    "score_awarded": 4,
                                    "full_score": 10,
                                    "source_kind": "current_exam",
                                    "question_difficulty": 6 if student_id == "SYN-S02" else 5,
                                    "direct_fine_terms": [{"一元一次方程": "kp_alg_linear_equation", "三角形全等": "kp_geo_triangle_congruence", "尺规作图": "kp_geo_construction", "一次函数": "kp_fun_linear"}[weak]],
                                }
                            ],
                            "actionable_reasons": ["合成掌握证据偏弱"],
                        }
                    ]
                ),
            }
        )
    return {
        "students": students,
        "exam_scope": {"mode": "current", "session_ids": [1]},
        "_mastery_session_times": {"1": "2026-07-20T08:00:00+08:00"},
    }


def _mark_question_recent(
    db_path: Path,
    *,
    student_id: str,
    question_id: int,
) -> None:
    with connect(db_path) as connection:
        task_id = int(
            connection.execute(
                """
                INSERT INTO training_tasks (
                    task_code, status, created_at
                ) VALUES ('SYN-RECENT', 'ready', '2026-07-29 08:00:00')
                """
            ).lastrowid
        )
        variant_id = int(
            connection.execute(
                """
                INSERT INTO training_variants (
                    task_id, variant_key, variant_type
                ) VALUES (?, 'SYN-V1', 'individual')
                """,
                (task_id,),
            ).lastrowid
        )
        connection.execute(
            """
            INSERT INTO variant_students (variant_id, student_id)
            VALUES (?, ?)
            """,
            (variant_id, student_id),
        )
        connection.execute(
            """
            INSERT INTO training_task_items (
                variant_id, task_item_code, bank_question_id,
                item_order, stage
            ) VALUES (?, 'SYN-RECENT-I1', ?, 1, 'direct')
            """,
            (variant_id, question_id),
        )


@pytest.fixture()
def direct_module(bnu24_difficulty_module):
    module = bnu24_difficulty_module
    rows = [(100 + i, str(100 + i), "填空题", f"合成直角三角形长度应用：已知一边{i+20}，列式求另一边", str(7 + i % 2), BNU_TARGET) for i in range(14)]
    rows += [(200 + i, str(200 + i), "解答题", f"合成全等对应边长度：已知一边{i+30}，求对应边", "5", BNU_PREREQ_NEAR) for i in range(4)]
    rows += [(300, "300", "填空题", "判定三角形是否为直角三角形", "7", BNU_TARGET),
             (301, "301", "填空题", "利用面积差求阴影面积", "7", BNU_TARGET)]
    rows += [(400+i, str(400+i), "填空题", text, "8", BNU_TARGET) for i, text in enumerate([
        "由折叠纸片后的重合位置推算动点范围", "在坐标系中分析路径的最短长度", "利用池塘里的芦苇弯折建立方程", "根据斜坡倾斜角分析道路之间的距离"])]
    with connect(module.db_path) as conn:
        _insert_bnu24_questions(conn, tuple(rows + [
            (900, "17", "解答题", "合成错题：折断树高，列直角三角形方程求长度", "8", BNU_TARGET),
            (901, "18", "解答题", "合成错题：全等三角形对应边求值", "5", BNU_PREREQ_NEAR)]))
        for qid, *_ in rows:
            method = "直角三角形列式求长度" if qid < 200 else "全等对应边" if qid < 300 else "逆定理判定" if qid == 300 else "面积计算"
            conn.execute("INSERT INTO question_tags(question_id,tag_type,tag_value,source) VALUES (?,'method',?,'synthetic')", (qid, method))
        for qid, method in ((900, "直角三角形列式求长度"), (901, "全等对应边")):
            conn.execute("INSERT INTO question_tags(question_id,tag_type,tag_value,source) VALUES (?,'method',?,'synthetic')", (qid,method))
    _approve_synthetic_criteria(module.db_path, module.data_root, tuple(row[0] for row in rows))
    return module


def _direct_diagnosis(entries=(("A", .9, 900, BNU_TARGET),)):
    return {"exam_scope": {"mode": "current", "session_ids": [1]}, "students": [
        {"student_id": sid, "student_name": f"合成学生{sid}", "class_id": "synthetic",
         "score_rate": rate, "weak_points": [{"knowledge_key": key, "knowledge_point": key,
            "mastery": .8 if rate is None else rate, "evidence_count": 1,
            "source_question_refs": [{"session_id": 1, "question_id": f"Q{source}", "bank_question_id": source,
                "full_score": 5, "score_awarded": 4, "score_rate": .8, "source_kind": "current_exam",
                "assessment": {"granularity": "part", "part_id": "part1", "eligible": True, "evidence_weight": 1}}]}]}
        for sid, rate, source, key in entries]}


def _make_direct(module, *, diagnosis=None, token="a", **settings):
    return module.create(request_token=token * 32, diagnosis=diagnosis or _direct_diagnosis(),
        config=PersonalizedRecommendationConfig(question_count=8, scope_keys=(BNU_CHAPTER4,), **settings), actor_ref="synthetic")


def test_same_hard_loss_varies_by_overall_score_and_obeys_cap(direct_module):
    diagnosis = _direct_diagnosis((("strong", .95, 900, BNU_TARGET), ("weak", .3, 900, BNU_TARGET)))
    draft = _make_direct(direct_module, diagnosis=diagnosis, difficulty_max=10)
    by_id = {s["student_id"]: s for s in draft["students"]}
    # The weak student's pool no longer admits the old 7-8 wall. With no
    # suitable same-skill material in this fixture it must report a shortage.
    assert all(q["difficulty"] <= 6 for q in by_id["weak"]["items"])
    assert by_id["weak"]["shortages"]
    for student in [by_id["strong"]]:
        assert student["items"] and all(7 <= q["difficulty"] <= 8 for q in student["items"])
        assert all(q["stage"] == "direct" for q in student["items"])
        assert all(q["matched_key"] == BNU_TARGET for q in student["items"] if q["selection_kind"] == "direct")
        assert [q["difficulty"] for q in student["items"]] == sorted(q["difficulty"] for q in student["items"])
        assert len([q for q in student["items"] if q["question_id"] in range(100, 114)]) == 1
    capped = _make_direct(direct_module, diagnosis=diagnosis, token="b")
    assert all(q["difficulty"] <= 7 for s in capped["students"] for q in s["items"])
    assert all(q["target"]["target_difficulty"] == (7 if s["student_id"] == "strong" else 6)
               for s in capped["students"] for q in s["items"])


def test_part_difficulty_overrides_whole_and_low_cap_remains_authoritative(direct_module):
    diagnosis = _direct_diagnosis((("A", .95, 900, BNU_TARGET),))
    diagnosis["students"][0]["weak_points"][0]["source_question_refs"][0]["assessment"]["part_difficulty"] = 3
    draft = _make_direct(direct_module, diagnosis=diagnosis, difficulty_max=10)
    assert draft["students"][0]["items"]
    assert all(2 <= q["difficulty"] <= 3 for q in draft["students"][0]["items"])
    assert all(q["question_id"] not in range(100, 114) for q in draft["students"][0]["items"])
    from question_bank.recommendation.personalized import _loss_difficulty
    ref = diagnosis["students"][0]["weak_points"][0]["source_question_refs"][0]
    assert _loss_difficulty(ref,.95,10) == 3
    assert _loss_difficulty(ref,.95,1) == 1
    assert PersonalizedRecommendationConfig(difficulty_min=2,difficulty_max=1).difficulty_min == 1


@pytest.mark.parametrize("change", ["full_score", "missing_difficulty", "ineligible"])
def test_insufficient_sources_do_not_invent_recommendations(direct_module, change):
    diagnosis = _direct_diagnosis()
    ref = diagnosis["students"][0]["weak_points"][0]["source_question_refs"][0]
    if change == "full_score": ref["score_awarded"] = 5
    if change == "ineligible": ref["assessment"]["eligible"] = False
    with connect(direct_module.db_path) as conn:
        if change == "missing_difficulty": conn.execute("UPDATE questions SET difficulty=NULL WHERE id=900")
    draft = _make_direct(direct_module, diagnosis=diagnosis)
    assert draft["students"][0]["items"] == []
    assert draft["students"][0]["shortages"][0]["missing_count"] == 8
    assert draft["students"][0]["warnings"]


def test_current_full_score_does_not_resurrect_historical_loss(direct_module):
    diagnosis = _direct_diagnosis()
    point = diagnosis["students"][0]["weak_points"][0]
    old = deepcopy(point["source_question_refs"][0]); old["source_kind"] = "history_exam"; old["session_id"] = 2
    point["source_question_refs"][0]["score_awarded"] = 5
    point["source_question_refs"].append(old)
    assert not _make_direct(direct_module, diagnosis=diagnosis)["students"][0]["items"]
    assert not _group_needs(diagnosis, (BNU_TARGET,))["A"]


def test_missing_overall_score_is_explained_and_time_is_not_used(direct_module):
    diagnosis = _direct_diagnosis((("A",None,900,BNU_TARGET),))
    short = _make_direct(direct_module, diagnosis=diagnosis, expected_minutes=1)
    long = _make_direct(direct_module, diagnosis=diagnosis, expected_minutes=999, token="b")
    assert short["students"] == long["students"]
    assert "整体成绩缺失" in short["students"][0]["items"][0]["reason"]
    assert "expected_minutes" not in short["config"]
    assert "stage_ratios" not in short["config"]
    assert all("estimated_minutes" not in q for q in short["students"][0]["items"])


def test_shared_paper_rejects_disjoint_weaknesses(direct_module):
    diagnosis = _direct_diagnosis((("A",.8,900,BNU_TARGET),("B",.79,901,BNU_PREREQ_NEAR)))
    with pytest.raises(ValueError, match="common weak knowledge"):
        _make_direct(direct_module, diagnosis=diagnosis, paper_mode="shared")


def test_shared_paper_retains_only_core_exercises_suitable_for_every_member(direct_module):
    diagnosis = _direct_diagnosis((("A",.8,900,BNU_TARGET),("B",.79,900,BNU_TARGET)))
    extra = _direct_diagnosis((("B",.79,901,BNU_PREREQ_NEAR),))["students"][0]["weak_points"][0]
    diagnosis["students"][1]["weak_points"].append(extra)
    first = _make_direct(direct_module, diagnosis=diagnosis, paper_mode="shared")
    items = first["students"][0]["items"]
    assert {q["matched_key"] for q in items if q["selection_kind"] == "direct"} == {BNU_TARGET}
    assert {sid for q in items for sid in q["beneficiary_student_ids"]} == {"A","B"}
    assert any(set(q["beneficiary_student_ids"]) == {"A", "B"} for q in items)
    assert all(q["beneficiary_student_ids"] == ["A", "B"] for q in items if q["selection_kind"] != "supplement")
    ids = [q["question_id"] for q in items]
    assert ids == [q["question_id"] for q in first["students"][1]["items"]]
    diagnosis["students"].reverse()
    second = _make_direct(direct_module, diagnosis=diagnosis, token="b", paper_mode="shared")
    assert ids == [q["question_id"] for q in second["students"][0]["items"]]


def test_chapter_groups_require_both_score_and_common_weakness(direct_module):
    diagnosis = _direct_diagnosis((("A",.8,900,BNU_TARGET),("B",.79,900,BNU_TARGET),
                                 ("C",.2,900,BNU_TARGET),("D",.8,901,BNU_PREREQ_NEAR)))
    config = PersonalizedRecommendationConfig(paper_mode="shared", scope_keys=(BNU_CHAPTER4,), group_scope_keys=(BNU_CHAPTER4,))
    preview = direct_module.chapter_groups(diagnosis=diagnosis, config=config)
    assert len(preview["groups"]) == 1
    group = preview["groups"][0]
    assert group["ready"]
    assert {m["student_id"] for m in group["members"]} == {"A","B"}
    assert {t["knowledge_key"] for t in group["targets"]} == {BNU_TARGET}
    assert all(t["affected_student_count"] == 2 for t in group["targets"])
    assert {member["student_id"] for member in preview["unassigned"]} == {"C", "D"}
    assert preview["summary"] == {"student_count": 4, "students_with_needs": 4,
                                "grouped_student_count": 2, "group_count": 1}
    assert {member["reason_kind"] for member in preview["unassigned"]} == {"no_group_fit"}
    rejected = direct_module.chapter_groups(diagnosis=diagnosis, config=config, member_ids=("A", "D"))["selection"]
    assert not rejected["ready"]
    assert any("共同薄弱" in issue for issue in rejected["issues"])
    selected = deepcopy(diagnosis); selected["students"] = selected["students"][:2]
    adopted = replace(config, scope_keys=(), target_keys=tuple(t["knowledge_key"] for t in group["targets"]), group_source_version=group["source_version"])
    draft = direct_module.create(request_token="a"*32,diagnosis=selected,config=adopted,actor_ref="synthetic")
    assert direct_module.get(draft["draft_id"]) == draft
    selected["students"][0]["weak_points"][0]["source_question_refs"][0]["score_awarded"] = 2
    with pytest.raises(RecommendationSourceChanged):
        direct_module.create(request_token="b"*32,diagnosis=selected,config=adopted,actor_ref="synthetic")


def test_semester_group_of_seventeen_creates_one_shared_paper_through_api(direct_module):
    from backend.api.app import create_app
    from backend.api.dependencies import get_diagnosis_profile_service, get_request_diagnosis_profile_service
    from backend.api.dependencies import get_personalized_recommendation_module
    from backend.api.routers.training import _grouping_module
    from fastapi.testclient import TestClient

    members = [f"SYN-{index:02}" for index in range(17)]
    source = _direct_diagnosis(tuple((sid, .8, 900, BNU_TARGET) for sid in members)
                              + tuple((f"OTHER-{index:02}", .2, 901, BNU_PREREQ_NEAR) for index in range(56)))
    source.update(knowledge_catalog=[], coverage={"covered_items": 2, "total_items": 2, "missing_items": {}},
                  diagnosis_identity="question_tag", warnings=[], confirmed_concept_ids=[], suggested_terms=[], unmapped_terms=[])
    for student in source["students"]:
        student["student_code"] = student["student_id"]
        for point in student["weak_points"]:
            point.update(score_sum=4, full_score_sum=5, deduction_count=1, exam_count=1,
                         actionable_reasons=[], tag_context={}, error_counts={})
            for ref in point["source_question_refs"]:
                ref["session_name"] = "合成学期考试"

    class Diagnosis:
        def build_profiles(self, *, scope, exam_scope):
            result = deepcopy(source)
            result["scope"] = scope
            result["exam_scope"] = {**exam_scope, "session_ids": [1, 2], "sessions": []}
            if scope["mode"] == "selected":
                result["students"] = [student for student in result["students"] if student["student_id"] in scope["student_ids"]]
            return result

    app = create_app()
    app.dependency_overrides[get_diagnosis_profile_service] = Diagnosis
    app.dependency_overrides[get_request_diagnosis_profile_service] = Diagnosis
    app.dependency_overrides[get_personalized_recommendation_module] = lambda: direct_module
    app.dependency_overrides[_grouping_module] = lambda: direct_module
    client = TestClient(app)
    exams = {"mode": "semester", "session_ids": [], "curriculum_volume_id": "bnu24-math-g8-upper"}
    settings = {"scope_keys": [BNU_CHAPTER4], "question_count": 10, "difficulty_max": 7}
    response = client.post("/api/training/diagnosis", json={
        "scope": {"mode": "all"}, "exam_scope": exams, "grouping": settings})
    assert response.status_code == 200, response.text
    group = next(group for group in response.json()["grouping"]["groups"] if len(group["members"]) == 17)
    assert group["ready"]
    targets = [target["knowledge_key"] for target in group["targets"]]
    checked = client.post("/api/training/diagnosis", json={
        "scope": {"mode": "all"}, "exam_scope": exams,
        "grouping": {**settings, "member_ids": members, "target_keys": targets}})
    assert checked.status_code == 200, checked.text
    request_url = "/api/training/personalized-drafts/by-request/" + "8" * 32
    assert client.get(request_url).status_code == 404
    created = client.post("/api/training/personalized-drafts", json={
        "request_token": "8" * 32, "scope": {"mode": "selected", "student_ids": members}, "exam_scope": exams,
        "paper_mode": "shared", "question_count": 10, "difficulty_max": 7, "target_keys": targets,
        "group_scope_keys": [BNU_CHAPTER4], "group_source_version": checked.json()["grouping"]["selection"]["source_version"]})
    assert created.status_code == 200, created.text
    draft = created.json()
    assert {student["student_id"] for student in draft["students"]} == set(members)
    question_sets = {tuple(item["question_id"] for item in student["items"]) for student in draft["students"]}
    assert len(question_sets) == 1
    assert next(iter(question_sets))
    assert client.get(f"/api/training/personalized-drafts/{draft['draft_id']}").json() == draft
    recovered = client.get(request_url)
    assert recovered.status_code == 200
    assert recovered.json() == draft
    assert "_input_fingerprint" not in recovered.json()
    with connect(direct_module.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM personalized_recommendation_drafts").fetchone()[0] == 1


def test_group_common_core_must_cover_half_of_every_member_and_whole_group():
    def need(*keys, score=.8):
        return {key: {"score_rate": score, "mastery": .4} for key in keys}
    # One shared target out of four is insufficient for the broader need.
    assert _chapter_group_members({"A": need("a"), "B": need("a", "b", "c", "d")}) == []
    assert _chapter_group_members({"A": need("a"), "B": need("a", "b")}) == [("A", "B")]
    # Every pair overlaps by half, but the three members share no common core.
    needs = {"A": need("a", "b"), "B": need("a", "c"), "C": need("b", "c")}
    assert _chapter_group_members(needs) == [("A", "B")]
    assert _chapter_group_members(dict(reversed(list(needs.items())))) == [("A", "B")]
    assert _chapter_group_members({"A": need("a", score=.8), "B": need("a", score=.55)}) == [("A", "B")]
    assert _chapter_group_members({"A": need("a", score=.8), "B": need("a", score=.549)}) == []


def test_singleton_can_pair_with_a_compatible_member_without_breaking_original_group():
    def need(*keys):
        return {key: {"score_rate": .8, "mastery": .4} for key in keys}
    needs = {"A": need("a", "b"), "B": need("a"), "C": need("a"), "D": need("b")}
    assert _chapter_group_members(needs) == [("A", "D"), ("B", "C")]
    assert _chapter_group_members(dict(reversed(list(needs.items())))) == [("A", "D"), ("B", "C")]


def test_saved_lock_replace_exclude_and_idempotent_retry(direct_module):
    draft = _make_direct(direct_module, difficulty_max=10)
    item = next(q for q in draft["students"][0]["items"] if q["question_id"] in range(100, 114))
    def command(action, token, revision, replacement=None):
        return RecommendationEditCommand(request_token=token*32,expected_revision=revision,action=action,
            student_id="A",item_id=item["item_id"],actor_ref="synthetic",reason="合成验收",replacement_question_id=replacement)
    lock = command("lock","b",1)
    locked = direct_module.edit(draft["draft_id"],lock)
    assert direct_module.edit(draft["draft_id"],lock) == locked
    with pytest.raises(RecommendationEditInvalid): direct_module.edit(draft["draft_id"],command("replace","c",2))
    direct_module.edit(draft["draft_id"],command("unlock","d",2))
    with pytest.raises(RecommendationEditInvalid): direct_module.edit(draft["draft_id"],command("replace","e",3,46))
    other = next(q for q in draft["students"][0]["items"] if q["question_id"] == 300)
    with pytest.raises(RecommendationEditInvalid):
        direct_module.edit(draft["draft_id"], RecommendationEditCommand(
            request_token="9"*32, expected_revision=3, action="replace", student_id="A",
            item_id=other["item_id"], actor_ref="synthetic", reason="不能换入已选题的数字变式", replacement_question_id=102))
    replaced = direct_module.edit(draft["draft_id"],command("replace","f",3))
    replacement = next(q for q in replaced["students"][0]["items"] if q["item_id"] == item["item_id"])
    assert replacement["question_id"] != item["question_id"]
    assert replacement["target"]["target_difficulty"] == item["target"]["target_difficulty"]
    assert replacement["replacement_history"] and replacement["difficulty"] in (7,8)
    items = replaced["students"][0]["items"]
    assert [q["difficulty"] for q in items] == sorted(q["difficulty"] for q in items)
    assert [q["item_order"] for q in items] == list(range(1, len(items) + 1))
    assert direct_module.get(draft["draft_id"]) == replaced
    removed = direct_module.edit(draft["draft_id"],command("exclude","1",4))
    assert len(removed["students"][0]["items"]) == len(draft["students"][0]["items"]) - 1
    assert direct_module.get(draft["draft_id"]) == removed
    assert [q["item_order"] for q in removed["students"][0]["items"]] == list(range(1, len(items)))

    remaining = removed["students"][0]["items"][0]
    updated = direct_module.edit(draft["draft_id"], RecommendationEditCommand(
        request_token="8"*32, expected_revision=5, action="exclude", student_id="A", item_id=remaining["item_id"],
        actor_ref="synthetic", reason="移除题目后更新题量说明"))
    assert len(updated["students"][0]["items"]) == len(removed["students"][0]["items"])-1
    assert direct_module.get(draft["draft_id"]) == updated


def test_source_difficulty_change_invalidates_draft(direct_module):
    draft = _make_direct(direct_module)
    assert direct_module.ensure_current(draft["draft_id"]) == draft
    with connect(direct_module.db_path) as conn: conn.execute("UPDATE questions SET difficulty='4' WHERE id=900")
    with pytest.raises(RecommendationSourceChanged): direct_module.ensure_current(draft["draft_id"])
    with pytest.raises(RecommendationRequestConflict): _make_direct(direct_module, diagnosis=_direct_diagnosis((("B",.8,900,BNU_TARGET),)))


def test_concurrent_teacher_edits_allow_only_one_revision(direct_module):
    draft = _make_direct(direct_module)
    item = draft["students"][0]["items"][0]
    def apply(token):
        try:
            direct_module.edit(draft["draft_id"],RecommendationEditCommand(request_token=token*32,expected_revision=1,
                action="lock",student_id="A",item_id=item["item_id"],actor_ref="synthetic",reason="并发验收"))
            return "applied"
        except RecommendationRevisionConflict: return "conflict"
    with ThreadPoolExecutor(max_workers=2) as pool: results = list(pool.map(apply,("b","c")))
    assert sorted(results) == ["applied","conflict"]
    assert direct_module.get(draft["draft_id"])["revision"] == 2


def test_recent_exact_duplicate_is_excluded_for_the_student(direct_module):
    _mark_question_recent(direct_module.db_path,student_id="A",question_id=100)
    draft = _make_direct(direct_module)
    assert 100 not in [q["question_id"] for q in draft["students"][0]["items"]]


def test_direct_scope_does_not_expand_confirmed_or_textbook_neighbours(direct_module):
    draft = _make_direct(direct_module, direct_ratio=0, prerequisite_ratio=.5, transfer_ratio=.5)
    assert draft["students"][0]["items"]
    assert all(q["stage"] == "direct" for q in draft["students"][0]["items"])
    assert all(q["matched_key"] == BNU_TARGET for q in draft["students"][0]["items"] if q["selection_kind"] == "direct")
    assert all(q["matched_key"].startswith(BNU_CHAPTER4 + "_") for q in draft["students"][0]["items"])


def _selection_candidate(qid, text, key=BNU_TARGET, difficulty=5, **extra):
    return {"question_id": qid, "question_number": str(qid), "question_type": "选择题",
            "question_text": text, "difficulty": difficulty, "stable_keys": [key],
            "required_keys": [key], "scope_complete": True, "stable_names": {key: key},
            "criterion_version_id": "c" * 64, "criterion_point_count": 1,
            "source_paper": "合成题源", "similarity_profile": {"tags": []}, **extra}


def _selection_draft(monkeypatch, candidates, diagnosis=None, shared=False, question_count=10, **settings):
    module = object.__new__(PersonalizedRecommendationModule)
    from question_bank.current_knowledge import CurrentKnowledgeResolver
    from question_bank.knowledge_graph_release.loader import load_release_for_taxonomy_revision, load_taxonomy_catalog_for_release
    release = load_release_for_taxonomy_revision(4)
    module.current_knowledge = CurrentKnowledgeResolver(release, load_taxonomy_catalog_for_release(release))
    monkeypatch.setattr(module, "_source_practice_metadata", lambda _: {})
    diagnosis = deepcopy(diagnosis or _direct_diagnosis())
    mastery = {}
    for student in diagnosis["students"]:
        for point in student["weak_points"]:
            for ref in point["source_question_refs"]:
                ref.setdefault("question_difficulty", 5)
            key = point["knowledge_key"]
            mastery[(student["student_id"], key)] = {**point, "stable_key": key, "display_name": key}
    return module._build_draft(
        diagnosis=diagnosis, config=PersonalizedRecommendationConfig(
            question_count=question_count, paper_mode="shared" if shared else "individual", scope_keys=(BNU_CHAPTER4,), **settings),
        candidates=tuple(candidates), relations=tuple(
            {"relation_type": "parent", "source_key": key, "target_key": BNU_CHAPTER4}
            for key in (BNU_TARGET, BNU_PREREQ_NEAR)), mastery=mastery, recent={}, excluded_question_ids=set())


def test_ability_changes_admission_and_whole_paper_distribution(monkeypatch):
    themes = ["折叠纸片寻找重合位置", "操场跑步比较路径", "池塘芦苇弯折测量", "飞机航行计算距离",
              "木框能否通过门洞", "梯子沿墙滑动变化", "正方形对角线的关系", "斜坡道路长度计算",
              "风筝离地高度估计", "菱形边长推算", "绳子围成三角形", "坐标网格中两点距离"]
    candidates = [_selection_candidate(level*100+i, theme+f"：求第{level}组条件下的结果", difficulty=level,
                    question_type="解答题" if i in (0, 2, 4, 6) else "填空题")
                  for level in (2, 3, 4, 5) for i, theme in enumerate(themes)]
    diagnosis = _direct_diagnosis((("weak", .2, 900, BNU_TARGET), ("middle", .6, 900, BNU_TARGET), ("strong", .9, 900, BNU_TARGET)))
    for profile, earned in zip(diagnosis["students"], (1, 3, 4), strict=True):
        ref = profile["weak_points"][0]["source_question_refs"][0]
        ref.update(question_difficulty=4, score_awarded=earned, score_rate=earned/5)
    students = _selection_draft(monkeypatch, candidates, diagnosis)["students"]
    for student, expected in zip(students, ({2:7, 3:3}, {2:3, 3:6, 4:1}, {3:2, 4:7, 5:1}), strict=True):
        from collections import Counter
        assert Counter(q["difficulty"] for q in student["items"]) == expected
        assert student["structure"]["written_count"] <= 2
    assert not set(q["question_id"] for q in students[0]["items"] if q["difficulty"]>3)


def test_hard_source_does_not_set_floor_and_local_success_beats_overall_label():
    from question_bank.recommendation.personalized import _difficulty_plan
    ref = {"question_difficulty": 4, "full_score": 10, "score_awarded": 1}
    assert _difficulty_plan(ref, .3, 7)["maximum"] == 3
    # An overall high scorer still starts conservatively on an unmastered goal.
    assert _difficulty_plan(ref, .9, 7)["level"] == "foundation"
    # Strong local evidence matters even when the overall exam mark is low.
    assert _difficulty_plan({**ref, "score_awarded":9}, .3, 7)["level"] == "developing"


def test_two_written_questions_limit_applies_to_whole_multipart_questions():
    from question_bank.recommendation.personalized import _paper_diversity_allowed
    printed = [_selection_candidate(1, "第一道完整证明题", question_type="解答题"),
               _selection_candidate(2, "第二道完整应用题", question_type="解答题")]
    assert not _paper_diversity_allowed(_selection_candidate(3, "第三道含两个小问的题", question_type="解答题"), printed)
    assert _paper_diversity_allowed(_selection_candidate(4, "选择正确的数量关系", question_type="选择题"), printed)


def test_sparse_pool_never_fills_low_group_with_hard_questions(monkeypatch):
    diagnosis = _direct_diagnosis((("A", .25, 900, BNU_TARGET),))
    ref = diagnosis["students"][0]["weak_points"][0]["source_question_refs"][0]
    ref.update(question_difficulty=4, score_awarded=0, score_rate=0)
    candidates = [_selection_candidate(1,"识别图中已知边对应的位置",difficulty=2),
                  _selection_candidate(2,"独立建立多组关系完成综合应用",difficulty=4)]
    student = _selection_draft(monkeypatch, candidates, diagnosis)["students"][0]
    assert [q["question_id"] for q in student["items"]] == [1]
    assert student["shortages"][0]["missing_count"] == 9


def test_same_skill_short_practice_is_core_without_claiming_written_reasoning(monkeypatch):
    diagnosis = _direct_diagnosis()
    ref = diagnosis["students"][0]["weak_points"][0]["source_question_refs"][0]
    ref.update(direct_keys=[BNU_TARGET, BNU_PREREQ_NEAR], direct_fine_terms=["原题细项"],
               practice_tags={"method": ["公式计算"], "model": ["直角三角形"]},
               question_type="解答题", deduction_reason="缺少依据和推理步骤")
    candidate = _selection_candidate(1, "从正方形面积关系中选择满足条件的边长")
    candidate["practice_observations_by_key"] = {BNU_TARGET: [{"response_mode": "answer_only", "fine_terms": ["另一细项"]}]}
    student = _selection_draft(monkeypatch, [candidate], diagnosis)["students"][0]
    assert student["items"][0]["selection_kind"] == "direct"
    assert student["items"][0]["practice_role"] == "step_practice"
    assert student["items"][0]["practice_tasks"] == []
    assert "不等同于" in student["items"][0]["reason"]
    candidate["practice_observations_by_key"][BNU_TARGET][0].update(
        response_mode="process_required", observable="依据定理写出证明过程")
    item = _selection_draft(monkeypatch, [candidate], diagnosis)["students"][0]["items"][0]
    assert item["selection_kind"] == "direct"
    assert item["question_id"] == 1
    assert [t["code"] for t in item["practice_tasks"]] == ["written_reasoning"]
    assert "相近解题要求与方法" not in item["reason"]
    ref.pop("practice_tags")
    ref.pop("direct_fine_terms")
    assert _selection_draft(monkeypatch, [candidate], diagnosis)["students"][0]["items"]


def test_range_supplements_stop_at_twenty_percent_of_actual_paper(monkeypatch):
    texts = ["用拼图面积说明一个边长等式", "从坐标计算两个标记之间的距离", "分析折断树木触地点与树根间距",
             "研究梯子沿墙滑动后底端位置", "在长方体表面规划蚂蚁的最短行程", "水池中央芦苇弯曲时求水深",
             "利用菱形对角线计算四条边的总长", "测量河宽时设置岸上的垂直标杆", "围绕等边三角形中线建立关系",
             "观察纸片折叠后重合点的位置", "利用风筝斜线长度求离地高度", "在扇形内部确定弦与半径的位置"]
    candidates = [_selection_candidate(i + 1, text, BNU_TARGET if i < 4 else BNU_PREREQ_NEAR,
                                       5 if i % 2 == 0 else 4) for i, text in enumerate(texts)]
    candidates += [_selection_candidate(90, "范围外的概率题", BNU_OTHER_CHAPTER, 5),
                   _selection_candidate(91, "范围内过难的综合题", BNU_PREREQ_NEAR, 8),
                   _selection_candidate(92, "范围内太简单的填空题", BNU_PREREQ_NEAR, 1)]
    before = deepcopy(candidates)
    student = _selection_draft(monkeypatch, candidates)["students"][0]
    assert len(student["items"]) == 5
    assert sum(item["selection_kind"] == "direct" for item in student["items"]) == 4
    assert sum(item["selection_kind"] == "supplement" for item in student["items"]) == 1
    assert [item["difficulty"] for item in student["items"]] == sorted(item["difficulty"] for item in student["items"])
    assert all(item["question_id"] < 90 and item["difficulty"] in (4, 5) for item in student["items"])
    for item in (q for q in student["items"] if q["selection_kind"] == "supplement"):
        assert item["matched_key"] == BNU_PREREQ_NEAR
        assert item["target"]["stable_key"] == BNU_TARGET  # Origin is retained only as the difficulty basis.
        assert "补充练习" in item["reason"] and "对应错题" not in item["reason"]
        assert not item["practice_tasks"]
    assert student["shortages"][0]["missing_count"] == 5
    assert student["structure"]["within_supplement_limit"]
    assert candidates == before  # Selection never rewrites tags or criteria.


def test_public_paper_does_not_assign_minority_only_need_to_everyone(monkeypatch):
    diagnosis = _direct_diagnosis(tuple((sid, .8, 900, BNU_TARGET) for sid in "ABCDE") + (("F", .79, 901, BNU_PREREQ_NEAR),))
    diagnosis["students"][-1]["weak_points"].append(deepcopy(diagnosis["students"][0]["weak_points"][0]))
    candidates = [_selection_candidate(1, "已知两直角边求三角形周长"),
                  _selection_candidate(2, "根据图形中的面积差推算线段长度"),
                  _selection_candidate(3, "在网格纸中观察对称点之间的距离"),
                  _selection_candidate(4, "测绘队沿两个方向行走后测算直线距离"),
                  _selection_candidate(9, "识别两个全等图形的对应角", BNU_PREREQ_NEAR)]
    first = _selection_draft(monkeypatch, candidates, diagnosis, shared=True)
    items = first["students"][0]["items"]
    assert items[0]["beneficiary_student_ids"] == list("ABCDEF")
    assert all(q["question_id"] != 9 for q in items)
    assert all(q["beneficiary_student_ids"] == list("ABCDEF") for q in items)
    diagnosis["students"].reverse()
    second = _selection_draft(monkeypatch, list(reversed(candidates)), diagnosis, shared=True)
    assert {tuple(item["question_id"] for item in student["items"]) for student in second["students"]} == {
        tuple(item["question_id"] for item in items)}


def test_each_loss_difficulty_is_covered_before_extra_practice(monkeypatch):
    diagnosis = _direct_diagnosis()
    refs = diagnosis["students"][0]["weak_points"][0]["source_question_refs"]
    refs[0]["question_difficulty"] = 3
    refs.append({**deepcopy(refs[0]), "question_id": "Q901", "bank_question_id": 901, "question_difficulty": 6})
    texts = ["观察网格中的线段关系并填空", "已知等式求出图形中的未知边", "从正方形面积推算边长",
             "比较两条道路的长度", "利用绳子测量井深", "测算梯子顶部离地高度", "判断木框能否通过门洞",
             "用拼图展示面积之间的关系", "根据航海路线确定距离"]
    candidates = [_selection_candidate(index, text, difficulty=3) for index, text in enumerate(texts, 1)]
    candidates.append(_selection_candidate(20, "折叠纸片并由多组条件求重合点位置", difficulty=6))
    items = _selection_draft(monkeypatch, candidates, diagnosis, question_count=8)["students"][0]["items"]
    assert [item["difficulty"] for item in items] == [3] * 7 + [6]
    assert {q["target"]["source_question_refs"][0]["question_id"] for q in items} == {"Q900", "Q901"}


def test_numeric_variants_and_basic_judgements_do_not_fill_the_paper(monkeypatch):
    from question_bank.recommendation.personalized import _paper_diversity_allowed
    diagnosis = _direct_diagnosis()
    diagnosis["students"][0]["weak_points"][0]["source_question_refs"][0]["question_difficulty"] = 3
    candidates = [_selection_candidate(i + 1, f"已知一个直角三角形的两条直角边长分别为 {i+3} 和 {i+4}，求斜边长。", difficulty=3)
                  for i in range(12)]
    candidates += [_selection_candidate(20, "下列给出的数组中，哪些是勾股数？", difficulty=3),
                   _selection_candidate(21, "一根绳子分成三段，能否构成直角三角形？", difficulty=3),
                   _selection_candidate(22, "小明列出几种三边长度，请判断其中的直角三角形。", difficulty=3)]
    items = _selection_draft(monkeypatch, candidates, diagnosis)["students"][0]["items"]
    assert len([item for item in items if item["question_id"] <= 12]) == 1
    assert len([item for item in items if item["question_id"] >= 20]) == 2
    assert len(items) == 3
    other_image = {**candidates[0], "question_id": 99, "image_identity": ("different-drawing",)}
    assert _paper_diversity_allowed(other_image, [candidates[0]])


def test_supplements_do_not_hide_an_uncovered_loss(monkeypatch):
    student = _selection_draft(monkeypatch, [
        _selection_candidate(1, "由对称图形的对应边读出长度", BNU_PREREQ_NEAR)
    ])["students"][0]
    assert student["items"] == []
    assert any("尚未获得直接练习" in warning for warning in student["warnings"])


def test_reprinted_diagrams_require_identical_full_question_and_answer_for_deduplication():
    from question_bank.recommendation.personalized import _paper_diversity_allowed
    stem = '某数学家用纸片剪拼形成两个面积相等的空洞，图中给出了完整的剪拼过程。根据直角三角形边长与正方形面积的对应关系，判断下列等式中不正确的一项。A．面积等于平方和 B．面积等于边长积 C．两个空洞面积相同 D．符合勾股定理'
    original = _selection_candidate(1, stem)
    original.update(image_identity=('original-image',), solution_observable='由勾股定理得到两直角边平方和等于斜边平方。分别计算剪拼前后的面积，可以得到两个空洞的面积相等，并逐一核对四个选项所描述的数量关系，因此选择A。')
    reprint = {**original, 'question_id':2, 'question_text':'（3分）'+stem, 'image_identity':('different-encoding',)}
    assert not _paper_diversity_allowed(reprint, [original])
    assert _paper_diversity_allowed({**reprint, 'solution_observable':original['solution_observable'].replace('选择A', '选择B')}, [original])
    assert _paper_diversity_allowed({**reprint, 'solution_observable':''}, [original])


def test_process_task_uses_existing_steps_without_error_consolidation():
    from question_bank.recommendation.personalized import _training_tasks
    ref = {"source_kind": "current_exam", "full_score": 6, "score_awarded": 3,
           "assessment": {"granularity": "part", "eligible": True},
           "practice_observations_by_key": {BNU_TARGET: [{"part_id": "p1", "response_mode": "process_required",
               "evidence_points": [{"evidence_point_id": "step1", "target": "计算平方和"},
                                   {"evidence_point_id": "step2", "target": "写出推理依据"}]}]}}
    source = {"stable_key": BNU_TARGET, "source_question_refs": [ref]}
    assert [t["code"] for t in _training_tasks(source)] == ["process_practice"]
    ref["task_evidence_version_matches"] = True
    ref["assessment"]["point_observations"] = [{"point_id": "step1", "achieved": 0}, {"point_id": "step2", "achieved": 1}]
    ref["deduction_reason"] = "缺少依据"  # Reliable observations supersede this coarse note.
    tasks = _training_tasks(source)
    assert [t["code"] for t in tasks] == ["calculation_check", "process_practice"]
    assert tasks[0]["basis"] == "observed_step"
    ref["task_evidence_version_matches"] = False
    assert all(t["basis"] != "observed_step" for t in _training_tasks(source))
    ref["deduction_reason"] = "未作答，未见计算过程"
    assert [t["code"] for t in _training_tasks(source)] == ["diagnostic_check", "process_practice"]
    ref["assessment"]["eligible"] = False
    assert _training_tasks(source) == []


def test_practice_requirements_cannot_be_pooled_across_parts():
    from question_bank.recommendation.personalized import _practice_matches
    candidate = {"practice_observations_by_key": {BNU_TARGET: [
        {"part_id": "p1", "response_mode": "process_required", "observable": "写出推理依据"},
        {"part_id": "p2", "response_mode": "process_required", "observable": "代入计算"}]}}
    tasks = [{"code": "written_reasoning"}, {"code": "calculation_check"}]
    assert not _practice_matches(candidate, BNU_TARGET, tasks)
    candidate["practice_observations_by_key"][BNU_TARGET][0]["observable"] += "，代入计算"
    assert _practice_matches(candidate, BNU_TARGET, tasks)


def test_explicit_task_operations_accept_existing_formula_serializations():
    from question_bank.recommendation.personalized import _observable_operations
    for expression in ("AB²+BC²=AC²", "AB^2+BC^2=AC^2", "AB^{2}+BC^{2}=AC^{2}", "AB<sup>2</sup>+BC<sup>2</sup>=AC<sup>2</sup>"):
        assert _observable_operations("由勾股定理写出" + expression) == {"pythagorean_equation"}
    assert _observable_operations("由勾股逆定理判定直角三角形") == {"pythagorean_converse"}
    assert _observable_operations("利用公式求面积并证明") == set()


def test_numeric_variants_with_different_images_require_solution_evidence():
    from question_bank.recommendation.personalized import _paper_diversity_allowed
    base = _selection_candidate(1, "如图，分别以直角三角形的三边为边长向外作正方形，三个面积满足已知关系，面积差为18，求图中阴影部分的面积。",
        image_identity=("drawing-a",), similarity_profile={"tags": [{"tag_type": "model", "tag_value": "弦图模型"}]},
        solution_template="由勾股定理及正方形面积关系，先列出三个正方形的面积关系，再把已知面积差代入求出直角边上正方形的面积，阴影面积等于该正方形面积的一半。")
    variant = {**base, "question_id": 2, "question_text": base["question_text"].replace("18", "20"), "image_identity": ("drawing-b",)}
    assert not _paper_diversity_allowed(variant, [base])
    variant["solution_template"] = ""
    assert _paper_diversity_allowed(variant, [base])
    variant["solution_template"] = base["solution_template"]
    variant["question_text"] = base["question_text"]
    assert _paper_diversity_allowed(variant, [base])  # A drawing-only change remains meaningful.


def test_whole_paper_repair_recovers_uncovered_need_without_losing_coverage():
    from question_bank.recommendation.personalized import _choose_practice_entries
    def entry(qid, key, text, identity):
        return {"candidate": _selection_candidate(qid, text, key, practice_identity=identity),
                "selection_kind": "direct", "student_id": "A", "key": key, "matched_key": key,
                "distance": 0, "preference": 0, "loss": .5, "match_level": 2,
                "target": {"source_question_refs": [{"session_id": 1, "question_id": key, "bank_question_id": 99,
                                                     "full_score": 4, "score_awarded": 2}]}}
    entries = [entry(1, "need-A", "根据已知条件求线段长", "shared"),
               entry(2, "need-A", "证明两个全等三角形的对应关系", "other"),
               entry(3, "need-B", "根据已知条件求线段长", "shared")]
    chosen = _choose_practice_entries(entries, 2)
    assert {e["candidate"]["question_id"] for e, _ in chosen} == {2, 3}
    assert {e["key"] for _, group in chosen for e in group} == {"need-A", "need-B"}


@pytest.mark.parametrize("shared", [False, True])
def test_easy_first_presentation_keeps_tied_order_and_all_loss_sources(monkeypatch, shared):
    diagnosis = _direct_diagnosis((("A", .9, 900, BNU_TARGET), ("B", .9, 900, BNU_TARGET)))
    for student in diagnosis["students"]:
        refs = student["weak_points"][0]["source_question_refs"]
        refs[0]["question_difficulty"] = 6
        refs.append({**deepcopy(refs[0]), "question_id": "Q901", "bank_question_id": 901, "question_difficulty": 3})
    parts = {"parts": [{"label": "(1)", "difficulty": 2}, {"label": "(2)", "difficulty": 6}]}
    candidates = [
        _selection_candidate(1, "通过辅助线构造全等三角形证明结论", difficulty=6, part_assessment=parts),
        _selection_candidate(2, "从已知面积推算正方形的边长", difficulty=3),
        _selection_candidate(3, "计算小船沿河航行后与码头的直线距离", difficulty=3),
    ]
    draft = _selection_draft(monkeypatch, candidates, diagnosis, shared=shared)
    for student in draft["students"]:
        items = student["items"]
        assert [q["question_id"] for q in items] == [2, 3, 1]
        assert [q["difficulty"] for q in items] == [3, 3, 6]
        assert [q["item_order"] for q in items] == [1, 2, 3]
        assert items[-1]["part_assessment"] == parts
        assert {q["target"]["source_question_refs"][0]["question_id"] for q in items} == {"Q900", "Q901"}
        assert all(student["student_id"] in q["beneficiary_student_ids"] for q in items)


def test_extra_in_scope_knowledge_does_not_hide_better_method_and_model_matches(monkeypatch):
    diagnosis = _direct_diagnosis()
    ref = diagnosis["students"][0]["weak_points"][0]["source_question_refs"][0]
    ref.update(direct_keys=[BNU_TARGET], question_type="解答题",
               practice_tags={"method": ["列方程"], "model": ["直角三角形"]})
    choice = _selection_candidate(1, "观察三角形中的已知数据选择线段长度",
        similarity_profile={"tags": [{"tag_type": "model", "tag_value": "直角三角形"}]})
    written = _selection_candidate(2, "结合全等关系列方程计算未知边长", question_type="解答题",
        stable_keys=[BNU_TARGET, BNU_PREREQ_NEAR], required_keys=[BNU_TARGET, BNU_PREREQ_NEAR],
        similarity_profile={"tags": [{"tag_type": "method", "tag_value": "列方程"},
                                     {"tag_type": "model", "tag_value": "直角三角形"}]})
    items = _selection_draft(monkeypatch, [choice, written], diagnosis)["students"][0]["items"]
    assert items[0]["question_id"] == 2
    assert items[0]["difficulty"] == 5
    # The same source still admits a single-knowledge question when it is all we have.
    assert _selection_draft(monkeypatch, [choice], diagnosis)["students"][0]["items"][0]["question_id"] == 1


def test_thought_match_prefers_candidate_without_becoming_a_gate(monkeypatch):
    diagnosis = _direct_diagnosis()
    ref = diagnosis["students"][0]["weak_points"][0]["source_question_refs"][0]
    ref["practice_tags"] = {"thought": ["分类讨论"]}
    plain = _selection_candidate(1, "由已知边长计算一个三角形的面积")
    matching = _selection_candidate(2, "讨论顶点不同位置下三角形的边长",
        similarity_profile={"tags": [{"tag_type": "thought", "tag_value": "分类讨论"}]})
    items = _selection_draft(monkeypatch, [plain, matching], diagnosis)["students"][0]["items"]
    assert items[0]["question_id"] == 2
    assert _selection_draft(monkeypatch, [plain], diagnosis)["students"][0]["items"]
    ref.pop("practice_tags")
    assert len(_selection_draft(monkeypatch, [plain, matching], diagnosis)["students"][0]["items"]) == 2


def test_thought_tags_are_read_from_bank_and_invalidate_saved_source(direct_module):
    with connect(direct_module.db_path) as conn:
        for qid in (900, 301):
            conn.execute("INSERT INTO question_tags(question_id,tag_type,tag_value,source) VALUES (?,'thought','分类讨论','synthetic')", (qid,))
    metadata = direct_module._source_practice_metadata(_direct_diagnosis())
    assert metadata[900]["practice_tags"]["thought"] == ["分类讨论"]
    candidates, _, _ = direct_module._source_snapshot()
    assert {"tag_type": "thought", "tag_value": "分类讨论"} in next(q for q in candidates if q["question_id"] == 301)["similarity_profile"]["tags"]
    draft = _make_direct(direct_module)
    assert direct_module.get(draft["draft_id"]) == draft
    with connect(direct_module.db_path) as conn:
        conn.execute("UPDATE question_tags SET tag_value='数形结合' WHERE question_id=301 AND tag_type='thought'")
    with pytest.raises(RecommendationSourceChanged):
        direct_module.ensure_current(draft["draft_id"])


def _printed_original_variants(data_root):
    from PIL import Image, ImageChops, ImageDraw
    import numpy as np
    folder = data_root / "question_bank" / "extracted_images"
    folder.mkdir(parents=True, exist_ok=True)
    drawing = Image.new("RGB", (160, 100), "white")
    pen = ImageDraw.Draw(drawing)
    pen.line([(20, 15), (20, 80), (130, 80), (20, 15)], fill="black", width=2)
    pen.text((3, 10), "A", fill="black")
    pen.text((65, 83), "12", fill="black")
    background = Image.new("RGB", drawing.size, "white")
    ImageDraw.Draw(background).text((35, 32), "SYNTHETIC", fill=(205, 220, 235))
    # Same contours with channel-rounding noise across the entire picture.
    # A few dark pixels also have the small tint produced by image encoders.
    encoded_pixels = np.array(drawing)
    encoded_pixels[:, :, 0] = np.maximum(encoded_pixels[:, :, 0].astype(int) - 1, 0)
    encoded_pixels[16, 20] = (11, 20, 8)
    encoded = Image.fromarray(encoded_pixels)
    pale = Image.new("RGB", drawing.size, "white")
    ImageDraw.Draw(pale).text((40, 55), "PRINT", fill=(249, 252, 255))
    encoded = ImageChops.darker(encoded, pale)
    changed = drawing.copy()
    ImageDraw.Draw(changed).line((20, 80, 100, 20), fill="black", width=2)
    label = drawing.copy()
    ImageDraw.Draw(label).rectangle((62, 82, 90, 99), fill="white")
    ImageDraw.Draw(label).text((65, 83), "13", fill="black")
    for name, picture in (("original", drawing), ("printed", ImageChops.darker(drawing, background)), ("encoded", encoded),
                          ("changed", changed), ("label", label)):
        picture.save(folder / f"{name}.png")
    stem = "如图，两根竖直杆高分别为9米、4米，杆底相距12米，求两杆顶端的距离。"
    def row(number, name, text=stem):
        path = f"question_bank/extracted_images/{name}.png"
        return {"question_number": str(number), "question_text": f"{number}. {text}[[IMAGE:{path}]]",
                "has_images": True, "image_paths": [path]}
    return {
        "original": row(17, "original"),
        "printed": row(39, "printed", "（3分）" + stem),
        "encoded": row(44, "encoded", "（6分）" + stem),
        "numbers": row(40, "printed", stem.replace("12米", "13米")),
        "drawing": row(41, "changed"),
        "label": row(42, "label"),
        "missing": row(43, "missing"),
    }


def test_original_comparison_ignores_printing_but_preserves_question_conditions(tmp_path):
    from question_bank.services.duplicate_analysis_copy_service import exact_question_key, exam_original_key
    rows = _printed_original_variants(tmp_path)
    keys = {name: exam_original_key(row, data_root=tmp_path) for name, row in rows.items()}
    assert keys["original"] and keys["original"] == keys["printed"] == keys["encoded"]
    assert all(keys["original"] != keys[name] for name in ("numbers", "drawing", "label"))
    assert not keys["missing"]
    # Exclusion tolerance must not change import-time copying or bank identities.
    assert exact_question_key(rows["original"], data_root=tmp_path) != exact_question_key(rows["printed"], data_root=tmp_path)
    assert exact_question_key(rows["original"], data_root=tmp_path) != exact_question_key(rows["encoded"], data_root=tmp_path)
    def key(text, **kwargs):
        return exam_original_key({"question_text": text, "question_number": "3", **kwargs}, data_root=tmp_path)
    assert key("3.5的平方") != key("5的平方")
    assert key("选出正确结果", options=["A.3", "B.4"]) != key("选出正确结果", options=["A.4", "B.3"])
    assert exam_original_key({"question_text": "求下式的值"}, data_root=tmp_path,
        rich_content={"math_expressions": [{"restricted_latex": "x^2+1"}]}) != exam_original_key(
        {"question_text": "求下式的值"}, data_root=tmp_path,
        rich_content={"math_expressions": [{"restricted_latex": "x^2-1"}]})


@pytest.mark.parametrize("shade", [(180, 180, 180), (205, 220, 235), (0, 120, 200)])
def test_original_comparison_preserves_gray_lines_and_shaded_regions(tmp_path, shade):
    from PIL import Image, ImageDraw
    from question_bank.services.duplicate_analysis_copy_service import exam_original_key
    rows = _printed_original_variants(tmp_path)
    folder = tmp_path / "question_bank" / "extracted_images"
    picture = Image.open(folder / "original.png").convert("RGB")
    drawing = ImageDraw.Draw(picture)
    drawing.rectangle((28, 45, 45, 68), fill=shade)
    drawing.line((20, 80, 100, 20), fill=(180, 180, 180), width=1)
    picture.save(folder / "shaded.png")
    row = {**rows["original"], "image_paths": ["question_bank/extracted_images/shaded.png"],
           "question_text": rows["original"]["question_text"].replace("original.png", "shaded.png")}
    assert exam_original_key(rows["original"], data_root=tmp_path) != exam_original_key(row, data_root=tmp_path)


@pytest.mark.parametrize("shade", [(180, 180, 180), (230, 230, 230), (245, 245, 245), (0, 100, 200)])
def test_printed_identity_keeps_an_added_auxiliary_line_without_a_large_region(tmp_path, shade):
    from PIL import Image, ImageDraw
    from question_bank.services.duplicate_analysis_copy_service import exam_original_key
    rows = _printed_original_variants(tmp_path)
    folder = tmp_path / "question_bank" / "extracted_images"
    with Image.open(folder / "original.png") as source:
        picture = source.convert("RGB")
    ImageDraw.Draw(picture).line((30, 65, 60, 65), fill=shade, width=1)
    picture.save(folder / "auxiliary.png")
    row = {**rows["original"], "image_paths": ["question_bank/extracted_images/auxiliary.png"],
           "question_text": rows["original"]["question_text"].replace("original.png", "auxiliary.png")}
    assert exam_original_key(rows["original"], data_root=tmp_path) != exam_original_key(row, data_root=tmp_path)


def test_exam_duplicate_is_excluded_despite_printing_and_tag_differences(direct_module, monkeypatch):
    variants = _printed_original_variants(direct_module.data_root)
    rows = [(900, "original", BNU_TARGET), (902, "printed", BNU_PREREQ_NEAR),
            (903, "numbers", BNU_TARGET), (904, "drawing", BNU_TARGET), (905, "encoded", BNU_TARGET)]
    with connect(direct_module.db_path) as conn:
        _insert_bnu24_questions(conn, tuple((qid, str(qid), "填空题", variants[name]["question_text"], "7", key)
            for qid, name, key in rows if qid != 900))
        for qid, name, _ in rows:
            row = variants[name]
            conn.execute("UPDATE questions SET question_number=?,question_text=?,has_images=1,image_paths=? WHERE id=?",
                (row["question_number"], row["question_text"], json.dumps(row["image_paths"]), qid))
        conn.execute("INSERT INTO grading_question_links(grading_session_id,source_question_id,bank_question_id,link_method,status) VALUES ('1','Q900',900,'manual','confirmed')")
    _approve_synthetic_criteria(direct_module.db_path, direct_module.data_root, (902, 903, 904, 905))
    from question_bank.recommendation import personalized
    original_key = personalized.exam_original_key
    examined = []
    def track_key(row, **kwargs):
        examined.append(row["id"])
        return original_key(row, **kwargs)
    with monkeypatch.context() as guarded:
        guarded.setattr(personalized, "exam_original_key", track_key)
        assert direct_module.current_exam_question_ids(_direct_diagnosis()) == {900, 902, 905}
    # Different text cannot be an original copy: do not decode its figure.
    assert set(examined) == {900, 902, 904, 905}
    draft = _make_direct(direct_module, exclude_current_exam_originals=True)
    assert draft["students"][0]["items"]
    assert not {900, 902, 905}.intersection(q["question_id"] for q in draft["students"][0]["items"])
    assert direct_module.get(draft["draft_id"]) == draft
    allowed = _make_direct(direct_module, token="b", exclude_current_exam_originals=False)
    assert len({902, 905} & {q["question_id"] for q in allowed["students"][0]["items"]}) == 1


@pytest.mark.parametrize("shared", [False, True])
def test_printed_duplicates_are_excluded_from_generation_and_replacement(direct_module, shared):
    from question_bank.recommendation.personalized import _paper_diversity_allowed
    variants = _printed_original_variants(direct_module.data_root)
    rows = [(910, "original"), (911, "encoded"), (912, "drawing")]
    with connect(direct_module.db_path) as conn:
        _insert_bnu24_questions(conn, tuple((qid, str(qid), "解答题", variants[name]["question_text"], "7", BNU_TARGET)
                                           for qid, name in rows))
        for qid, name in rows:
            variant = variants[name]
            conn.execute("UPDATE questions SET question_number=?,has_images=1,image_paths=? WHERE id=?",
                         (variant["question_number"], json.dumps(variant["image_paths"]), qid))
    _approve_synthetic_criteria(direct_module.db_path, direct_module.data_root, (910, 911, 912))
    candidates, _, _ = direct_module._source_snapshot()
    by_id = {q["question_id"]: q for q in candidates}
    assert by_id[910]["duplicate_identity"] != by_id[911]["duplicate_identity"]
    assert by_id[910]["practice_identity"] == by_id[911]["practice_identity"]
    assert not _paper_diversity_allowed(by_id[911], [by_id[910]])
    assert _paper_diversity_allowed(by_id[912], [by_id[910]])
    diagnosis = _direct_diagnosis((("A", .9, 900, BNU_TARGET), ("B", .9, 900, BNU_TARGET)))
    draft = _make_direct(direct_module, diagnosis=diagnosis, paper_mode="shared" if shared else "individual")
    for student in draft["students"]:
        chosen = {q["question_id"] for q in student["items"]}
        assert len(chosen & {910, 911}) == 1
        assert 912 in chosen  # The extra line encodes a genuinely different drawing.
    student = draft["students"][0]
    chosen = {q["question_id"] for q in student["items"]}
    duplicate = ({910, 911} - chosen).pop()
    other = next(q for q in student["items"] if q["question_id"] not in {910, 911})
    message = "shared paper questions cannot be edited" if shared else "no approved replacement"
    with pytest.raises(RecommendationEditInvalid, match=message):
        direct_module.edit(draft["draft_id"], RecommendationEditCommand(request_token="c" * 32,
            expected_revision=1, action="replace", student_id=student["student_id"], item_id=other["item_id"],
            actor_ref="synthetic", reason="合成重复题换题验证", replacement_question_id=duplicate))
    assert direct_module.get(draft["draft_id"]) == draft


def _ordered_paper_workspace(tmp_path):
    from html import escape
    from question_bank.services.rich_content_service import save_question_rich_content
    db_path, data_root = tmp_path / "question_bank.db", tmp_path / "data"
    initialize_database(db_path)
    install_current_knowledge(db_path, taxonomy_revision=4)
    questions = (
        (900, "17", "选择题", "合成错题：根据直角三角形边长关系求值", "5", BNU_TARGET),
        (201, "21", "解答题", "直角三角形的两条直角边分别为6和8，求斜边长并写出计算过程。", "5", BNU_TARGET),
        (202, "22", "填空题", "边长为5的正方形，其面积是____。", "4", BNU_TARGET),
        (203, "23", "选择题", "长方形对角线将图形分成的两个三角形是否全等？A.是 B.否", "5", BNU_TARGET),
        (204, "24", "解答题", "直角三角形的两条直角边分别为9和12，求斜边长并写出计算过程。", "4", BNU_TARGET),
    )
    with connect(db_path) as conn:
        _insert_bnu24_questions(conn, questions)
    for qid, _, _, text, _, _ in questions:
        save_question_rich_content(qid, root=data_root / "question_bank" / "rich_content", question_blocks=[{
            "text": text, "xml": '<w:p xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:r><w:t>'
            + escape(text) + '</w:t></w:r></w:p>',
        }])
    _approve_synthetic_criteria(db_path, data_root, (201, 202, 203, 204))
    module = PersonalizedRecommendationModule(db_path=db_path, data_root=data_root, clock=lambda: NOW)
    return module, _make_direct(module)


def test_replacement_reorders_saved_draft_and_paper_uses_the_same_question_numbers(tmp_path):
    from docx import Document
    from question_bank.personalized_papers import CreatePaperCommand, PersonalizedPaperModule
    from question_bank.personalized_papers.latex_render import render_training_tex
    module, draft = _ordered_paper_workspace(tmp_path)
    assert [q["question_id"] for q in draft["students"][0]["items"]] == [202, 203, 201]
    item = draft["students"][0]["items"][-1]
    updated = module.edit(draft["draft_id"], RecommendationEditCommand(
        request_token="b" * 32, expected_revision=1, action="replace", student_id="A", item_id=item["item_id"],
        actor_ref="synthetic", reason="换为较简单的合成题", replacement_question_id=204))
    reentered = module.get(draft["draft_id"])
    assert reentered == updated
    expected = [202, 204, 203]
    items = reentered["students"][0]["items"]
    assert [q["question_id"] for q in items] == expected
    assert [q["difficulty"] for q in items] == [4, 4, 5]
    assert items[1]["item_id"] == item["item_id"]
    paper_module = PersonalizedPaperModule(db_path=module.db_path, data_root=module.data_root, clock=lambda: NOW)
    paper = paper_module.create_review_instance(draft["draft_id"], CreatePaperCommand(
        operation_token="c" * 32, expected_draft_revision=2, student_id="A", actor_ref="synthetic"))
    with connect(module.db_path) as conn:
        snapshot = json.loads(conn.execute("SELECT snapshot_json FROM personalized_paper_instances WHERE paper_instance_id=?",
            (paper["paper_instance_id"],)).fetchone()[0])
        saved = conn.execute("SELECT bank_question_id,item_order,task_item_code FROM personalized_paper_items WHERE paper_instance_id=? ORDER BY item_order",
            (paper["paper_instance_id"],)).fetchall()
    assert [q["bank_question_id"] for q in saved] == expected
    assert [q["item_order"] for q in saved] == [1, 2, 3]
    assert all(q["task_item_code"].endswith(f"Q{index:02d}") for index, q in enumerate(saved, 1))
    review_path, _ = paper_module.artifact_path(paper["paper_instance_id"], "review-docx")
    document_text = "\n".join(p.text for p in Document(review_path).paragraphs)
    tex = render_training_tex(snapshot, data_root=module.data_root)
    markers = ["边长为5的正方形", "直角三角形的两条直角边分别为9和12", "长方形对角线"]
    for content in (document_text, tex):
        assert [content.index(marker) for marker in markers] == sorted(content.index(marker) for marker in markers)
