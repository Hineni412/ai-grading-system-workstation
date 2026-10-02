from __future__ import annotations

import hashlib
import json
from copy import deepcopy
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
    RecommendationRevisionConflict,
    _group_needs,
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


def test_blank_or_ineligible_evidence_does_not_invent_an_error():
    from question_bank.recommendation.personalized import _training_tasks

    source = {
        "full_score": 5,
        "score_awarded": 0,
        "source_kind": "current_exam",
        "deduction_reason": "未作答，空白",
        "assessment": {"granularity": "part", "eligible": True},
    }
    point = {"source_question_refs": [source], "actionable_reasons": ["计算错误"]}
    assert [task["code"] for task in _training_tasks(point)] == ["diagnostic_check"]
    for reason in (
        "未作答，未见计算过程",
        "本问空白，未提供推理依据",
        "未答，无法体现数量关系",
    ):
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
    point["source_question_refs"].append(
        {**source, "source_kind": "current_exam", "score_awarded": 5}
    )
    assert _training_tasks(point) == []


def test_classified_cause_guides_practice_without_turning_predictions_into_evidence():
    from question_bank.recommendation.personalized import _training_tasks
    ref = {"source_kind": "current_exam", "full_score": 5, "score_awarded": 2,
           "assessment": {"granularity": "part", "eligible": True},
           "deduction_reason": "失分", "causes": [{"kind": "error", "category": "计算与化简",
               "pattern": "计算漏负号", "pattern_status": "candidate"}]}
    tasks = _training_tasks({"source_question_refs": [ref]})
    assert [(task["code"], task["basis"]) for task in tasks] == [("calculation_check", "classified_cause")]
    ref["causes"][0]["pattern_status"] = "rejected"
    assert _training_tasks({"source_question_refs": [ref]}) == []
    ref["causes"][0]["pattern_status"] = "candidate"
    ref["score_awarded"] = 5
    assert _training_tasks({"source_question_refs": [ref]}) == []


@pytest.fixture()
def recommendation_module(
    tmp_path: Path, question_bank_database
) -> PersonalizedRecommendationModule:
    db_path = tmp_path / "question_bank.db"
    data_root = tmp_path / "data"
    question_bank_database(db_path, taxonomy_revision=3)
    _seed_recommendation_sources(db_path, data_root)
    return PersonalizedRecommendationModule(
        db_path=db_path,
        data_root=data_root,
        clock=lambda: NOW,
    )


@pytest.fixture()
def bnu24_recommendation_module(
    tmp_path: Path,
    question_bank_database,
) -> PersonalizedRecommendationModule:
    db_path = tmp_path / "question_bank.db"
    data_root = tmp_path / "data"
    question_bank_database(db_path, taxonomy_revision=4)
    _seed_bnu24_recommendation_sources(db_path, data_root)
    return PersonalizedRecommendationModule(
        db_path=db_path,
        data_root=data_root,
        clock=lambda: NOW,
    )


@pytest.fixture()
def bnu24_difficulty_module(
    tmp_path: Path,
    question_bank_database,
) -> PersonalizedRecommendationModule:
    db_path = tmp_path / "question_bank.db"
    data_root = tmp_path / "data"
    question_bank_database(db_path, taxonomy_revision=4)
    _seed_bnu24_difficulty_sources(db_path, data_root)
    return PersonalizedRecommendationModule(
        db_path=db_path,
        data_root=data_root,
        clock=lambda: NOW,
    )


@pytest.fixture()
def bnu24_expansion_module(
    tmp_path: Path,
    question_bank_database,
) -> PersonalizedRecommendationModule:
    db_path = tmp_path / "question_bank.db"
    data_root = tmp_path / "data"
    question_bank_database(db_path)
    _install_bnu24_release_with_mainline_relations(db_path)
    _seed_bnu24_expansion_sources(db_path, data_root)
    return PersonalizedRecommendationModule(
        db_path=db_path,
        data_root=data_root,
        clock=lambda: NOW,
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
            "relation_key": hashlib.sha256("rel-chapter-related".encode()).hexdigest(),
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
            keys = sorted(
                {
                    identity.stable_key
                    for tag in connection.execute(
                        "SELECT tag_value FROM question_tags WHERE question_id = ? AND tag_type IN ('knowledge_point', 'canonical_knowledge_id')",
                        (question.question_id,),
                    ).fetchall()
                    for identity in resolver.resolve(tag["tag_value"])
                }
            )
            kind = connection.execute(
                "SELECT question_type FROM questions WHERE id = ?",
                (question.question_id,),
            ).fetchone()[0]
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
                "solution_evidence": {
                    "parts": [
                        {
                            "part_id": "part1",
                            "response_mode": "exact_objective"
                            if any(word in kind for word in ("选择", "填空"))
                            else "process_required",
                            "evidence_points": [
                                {
                                    "target": "计算并说明数量关系",
                                    "observable_evidence": "写出等式、依据和单位",
                                    "fine_term_links": [
                                        {
                                            "fine_term_id": key,
                                            "role": "direct",
                                            "core_resolution": {
                                                "status": "resolved",
                                                "stable_keys": [key],
                                            },
                                        }
                                        for key in keys
                                    ],
                                }
                            ],
                        }
                    ]
                },
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
                                    "question_difficulty": 6
                                    if student_id == "SYN-S02"
                                    else 5,
                                    "direct_fine_terms": [
                                        {
                                            "一元一次方程": "kp_alg_linear_equation",
                                            "三角形全等": "kp_geo_triangle_congruence",
                                            "尺规作图": "kp_geo_construction",
                                            "一次函数": "kp_fun_linear",
                                        }[weak]
                                    ],
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


@pytest.fixture()
def direct_module(bnu24_difficulty_module):
    module = bnu24_difficulty_module
    rows = [
        (
            100 + i,
            str(100 + i),
            "填空题",
            f"合成直角三角形长度应用：已知一边{i + 20}，列式求另一边",
            str(7 + i % 2),
            BNU_TARGET,
        )
        for i in range(14)
    ]
    rows += [
        (
            200 + i,
            str(200 + i),
            "解答题",
            f"合成全等对应边长度：已知一边{i + 30}，求对应边",
            "5",
            BNU_PREREQ_NEAR,
        )
        for i in range(4)
    ]
    rows += [
        (300, "300", "填空题", "判定三角形是否为直角三角形", "7", BNU_TARGET),
        (301, "301", "填空题", "利用面积差求阴影面积", "7", BNU_TARGET),
    ]
    rows += [
        (400 + i, str(400 + i), "填空题", text, "8", BNU_TARGET)
        for i, text in enumerate(
            [
                "由折叠纸片后的重合位置推算动点范围",
                "在坐标系中分析路径的最短长度",
                "利用池塘里的芦苇弯折建立方程",
                "根据斜坡倾斜角分析道路之间的距离",
            ]
        )
    ]
    with connect(module.db_path) as conn:
        _insert_bnu24_questions(
            conn,
            tuple(
                rows
                + [
                    (
                        900,
                        "17",
                        "解答题",
                        "合成错题：折断树高，列直角三角形方程求长度",
                        "8",
                        BNU_TARGET,
                    ),
                    (
                        901,
                        "18",
                        "解答题",
                        "合成错题：全等三角形对应边求值",
                        "5",
                        BNU_PREREQ_NEAR,
                    ),
                ]
            ),
        )
        for qid, *_ in rows:
            method = (
                "直角三角形列式求长度"
                if qid < 200
                else "全等对应边"
                if qid < 300
                else "逆定理判定"
                if qid == 300
                else "面积计算"
            )
            conn.execute(
                "INSERT INTO question_tags(question_id,tag_type,tag_value,source) VALUES (?,'method',?,'synthetic')",
                (qid, method),
            )
        for qid, method in ((900, "直角三角形列式求长度"), (901, "全等对应边")):
            conn.execute(
                "INSERT INTO question_tags(question_id,tag_type,tag_value,source) VALUES (?,'method',?,'synthetic')",
                (qid, method),
            )
    _approve_synthetic_criteria(
        module.db_path, module.data_root, tuple(row[0] for row in rows)
    )
    return module


def _direct_diagnosis(entries=(("A", 0.9, 900, BNU_TARGET),)):
    return {
        "exam_scope": {"mode": "current", "session_ids": [1]},
        "students": [
            {
                "student_id": sid,
                "student_name": f"合成学生{sid}",
                "class_id": "synthetic",
                "score_rate": rate,
                "weak_points": [
                    {
                        "knowledge_key": key,
                        "knowledge_point": key,
                        "mastery": 0.8 if rate is None else rate,
                        "evidence_count": 1,
                        "source_question_refs": [
                            {
                                "session_id": 1,
                                "question_id": f"Q{source}",
                                "bank_question_id": source,
                                "full_score": 5,
                                "score_awarded": 4,
                                "score_rate": 0.8,
                                "source_kind": "current_exam",
                                "assessment": {
                                    "granularity": "part",
                                    "part_id": "part1",
                                    "eligible": True,
                                    "evidence_weight": 1,
                                },
                            }
                        ],
                    }
                ],
            }
            for sid, rate, source, key in entries
        ],
    }


def _make_direct(module, *, diagnosis=None, token="a", **settings):
    return module.create(
        request_token=token * 32,
        diagnosis=diagnosis or _direct_diagnosis(),
        config=PersonalizedRecommendationConfig(
            question_count=8, scope_keys=(BNU_CHAPTER4,), **settings
        ),
        actor_ref="synthetic",
    )


def test_current_full_score_does_not_resurrect_historical_loss(direct_module):
    diagnosis = _direct_diagnosis()
    point = diagnosis["students"][0]["weak_points"][0]
    old = deepcopy(point["source_question_refs"][0])
    old["source_kind"] = "history_exam"
    old["session_id"] = 2
    point["source_question_refs"][0]["score_awarded"] = 5
    point["source_question_refs"].append(old)
    items = _make_direct(direct_module, diagnosis=diagnosis)["students"][0]["items"]
    assert items
    assert all(item["practice_purpose"] != "remediation" for item in items)
    assert not _group_needs(diagnosis, (BNU_TARGET,))["A"]


def test_semester_group_of_seventeen_creates_one_shared_paper_through_api(
    direct_module,
    monkeypatch,
):
    from backend.api.app import create_app
    from backend.api.dependencies import (
        get_diagnosis_profile_service,
        get_request_diagnosis_profile_service,
    )
    from backend.api.dependencies import get_personalized_recommendation_module
    from backend.api.routers.training import _grouping_module
    from fastapi.testclient import TestClient

    members = [f"SYN-{index:02}" for index in range(17)]
    source = _direct_diagnosis(
        tuple((sid, 0.8, 900, BNU_TARGET) for sid in members)
        + tuple((f"OTHER-{index:02}", 0.2, 901, BNU_PREREQ_NEAR) for index in range(56))
    )
    source.update(
        knowledge_catalog=[],
        coverage={"covered_items": 2, "total_items": 2, "missing_items": {}},
        diagnosis_identity="question_tag",
        warnings=[],
        confirmed_concept_ids=[],
        suggested_terms=[],
        unmapped_terms=[],
    )
    for student in source["students"]:
        student["student_code"] = student["student_id"]
        for point in student["weak_points"]:
            point.update(
                score_sum=4,
                full_score_sum=5,
                deduction_count=1,
                exam_count=1,
                actionable_reasons=[],
                tag_context={},
                error_counts={},
            )
            for ref in point["source_question_refs"]:
                ref["session_name"] = "合成学期考试"

    class Diagnosis:
        def build_profiles(self, *, scope, exam_scope):
            result = deepcopy(source)
            result["scope"] = scope
            result["exam_scope"] = {**exam_scope, "session_ids": [1, 2], "sessions": []}
            if scope["mode"] == "selected":
                result["students"] = [
                    student
                    for student in result["students"]
                    if student["student_id"] in scope["student_ids"]
                ]
            return result

        def graded_activities(self, student_ids):
            return []

    app = create_app()
    app.dependency_overrides[get_diagnosis_profile_service] = Diagnosis
    app.dependency_overrides[get_request_diagnosis_profile_service] = Diagnosis
    app.dependency_overrides[get_personalized_recommendation_module] = lambda: (
        direct_module
    )
    app.dependency_overrides[_grouping_module] = lambda: direct_module
    client = TestClient(app)
    exams = {
        "mode": "semester",
        "session_ids": [],
        "curriculum_volume_id": "bnu24-math-g8-upper",
    }
    settings = {"scope_keys": [BNU_CHAPTER4], "question_count": 10, "difficulty_max": 7}
    metadata_reads = []
    original_metadata = direct_module._source_practice_metadata

    def read_metadata(diagnosis):
        metadata_reads.append(len(diagnosis["students"]))
        return original_metadata(diagnosis)

    monkeypatch.setattr(direct_module, "_source_practice_metadata", read_metadata)
    eligibility_reads = []
    original_eligible = direct_module._eligible_candidates

    def read_eligible(*args, **kwargs):
        eligibility_reads.append(1)
        return original_eligible(*args, **kwargs)

    monkeypatch.setattr(direct_module, "_eligible_candidates", read_eligible)
    response = client.post(
        "/api/training/diagnosis",
        json={"scope": {"mode": "all"}, "exam_scope": exams, "grouping": settings},
    )
    assert response.status_code == 200, response.text
    # Each group reuses the source snapshot covering the full selected roster.
    assert metadata_reads == [len(source["students"])]
    cached_eligibility_reads = len(eligibility_reads)
    assert cached_eligibility_reads < len(source["students"])
    original_entries = direct_module._candidate_entries

    def entries_without_reuse(**kwargs):
        kwargs.update(source_part_cache=None, target_match_cache=None, source_links=None, eligibility_cache=None)
        return original_entries(**kwargs)

    with monkeypatch.context() as uncached:
        uncached.setattr(direct_module, "_candidate_entries", entries_without_reuse)
        baseline = client.post(
            "/api/training/diagnosis",
            json={"scope": {"mode": "all"}, "exam_scope": exams, "grouping": settings},
        )
    # Reuse must preserve all members, per-student suitability and source versions.
    assert baseline.status_code == 200, baseline.text
    assert baseline.json() == response.json()
    assert len(eligibility_reads) - cached_eligibility_reads > cached_eligibility_reads
    group = next(
        group
        for group in response.json()["grouping"]["groups"]
        if len(group["members"]) == 17
    )
    assert group["ready"]
    targets = [target["knowledge_key"] for target in group["targets"]]
    checked = client.post(
        "/api/training/diagnosis",
        json={
            "scope": {"mode": "all"},
            "exam_scope": exams,
            "grouping": {**settings, "member_ids": members, "target_keys": targets},
        },
    )
    assert checked.status_code == 200, checked.text
    request_url = "/api/training/personalized-drafts/by-request/" + "8" * 32
    assert client.get(request_url).status_code == 404
    created = client.post(
        "/api/training/personalized-drafts",
        json={
            "request_token": "8" * 32,
            "scope": {"mode": "selected", "student_ids": members},
            "exam_scope": exams,
            "paper_mode": "shared",
            "question_count": 10,
            "difficulty_max": 7,
            "target_keys": targets,
            "group_scope_keys": [BNU_CHAPTER4],
            "group_source_version": checked.json()["grouping"]["selection"][
                "source_version"
            ],
        },
    )
    assert created.status_code == 200, created.text
    draft = created.json()
    assert {student["student_id"] for student in draft["students"]} == set(members)
    question_sets = {
        tuple(item["question_id"] for item in student["items"])
        for student in draft["students"]
    }
    assert len(question_sets) == 1
    assert next(iter(question_sets))
    assert (
        client.get(f"/api/training/personalized-drafts/{draft['draft_id']}").json()
        == draft
    )
    recovered = client.get(request_url)
    assert recovered.status_code == 200
    assert recovered.json() == draft
    assert "_input_fingerprint" not in recovered.json()
    with connect(direct_module.db_path) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM personalized_recommendation_drafts"
            ).fetchone()[0]
            == 1
        )


def test_group_adopt_stores_activities_for_all_scope_students(
    direct_module,
):
    from backend.api.app import create_app
    from backend.api.dependencies import (
        get_diagnosis_profile_service,
        get_request_diagnosis_profile_service,
    )
    from backend.api.dependencies import get_personalized_recommendation_module
    from backend.api.routers.training import _grouping_module
    from fastapi.testclient import TestClient

    members = ("SYN-A", "SYN-B", "SYN-C")
    source = _direct_diagnosis(
        tuple((sid, 0.8, 900, BNU_TARGET) for sid in members)
    )
    source["students"].append(
        {
            "student_id": "IDLE-01",
            "student_name": "合成学生IDLE-01",
            "class_id": "synthetic",
            "score_rate": 0.9,
            "weak_points": [],
        }
    )
    source.update(
        knowledge_catalog=[],
        coverage={"covered_items": 2, "total_items": 2, "missing_items": {}},
        diagnosis_identity="question_tag",
        warnings=[],
        confirmed_concept_ids=[],
        suggested_terms=[],
        unmapped_terms=[],
    )
    for student in source["students"]:
        student["student_code"] = student["student_id"]
        for point in student["weak_points"]:
            point.update(
                score_sum=4,
                full_score_sum=5,
                deduction_count=1,
                exam_count=1,
                actionable_reasons=[],
                tag_context={},
                error_counts={},
            )
            for ref in point["source_question_refs"]:
                ref["session_name"] = "合成学期考试"

    class Diagnosis:
        def build_profiles(self, *, scope, exam_scope):
            result = deepcopy(source)
            result["scope"] = scope
            result["exam_scope"] = {**exam_scope, "session_ids": [1, 2], "sessions": []}
            if scope["mode"] == "selected":
                result["students"] = [
                    student
                    for student in result["students"]
                    if student["student_id"] in scope["student_ids"]
                ]
            return result

        def graded_activities(self, student_ids):
            return [
                {
                    "student_id": sid,
                    "activity_id": "exam:1",
                    "session_id": "1",
                    "occurred_at": "2026-09-01T08:00:00",
                }
                for sid in student_ids
            ]

    app = create_app()
    app.dependency_overrides[get_diagnosis_profile_service] = Diagnosis
    app.dependency_overrides[get_request_diagnosis_profile_service] = Diagnosis
    app.dependency_overrides[get_personalized_recommendation_module] = lambda: (
        direct_module
    )
    app.dependency_overrides[_grouping_module] = lambda: direct_module
    client = TestClient(app)
    exams = {
        "mode": "semester",
        "session_ids": [],
        "curriculum_volume_id": "bnu24-math-g8-upper",
    }
    settings = {"scope_keys": [BNU_CHAPTER4], "question_count": 8, "difficulty_max": 7}
    checked = client.post(
        "/api/training/diagnosis",
        json={
            "scope": {"mode": "all"},
            "exam_scope": exams,
            "grouping": {
                **settings,
                "member_ids": list(members),
                "target_keys": [BNU_TARGET],
            },
        },
    )
    assert checked.status_code == 200, checked.text
    created = client.post(
        "/api/training/personalized-drafts",
        json={
            "request_token": "5" * 32,
            # IDLE-01 stays in scope but is filtered out for lack of weak
            # points; the stored activities must still cover all scope students.
            "scope": {"mode": "all"},
            "exam_scope": exams,
            "paper_mode": "shared",
            "question_count": 8,
            "difficulty_max": 7,
            "target_keys": [BNU_TARGET],
            "group_scope_keys": [BNU_CHAPTER4],
            "group_source_version": checked.json()["grouping"]["selection"][
                "source_version"
            ],
        },
    )
    assert created.status_code == 200, created.text
    with connect(direct_module.db_path) as conn:
        stored = json.loads(
            conn.execute(
                "SELECT request_json FROM personalized_recommendation_drafts WHERE draft_id = ?",
                (created.json()["draft_id"],),
            ).fetchone()[0]
        )
    assert {item["student_id"] for item in stored["graded_activities"]} == {
        *members,
        "IDLE-01",
    }
    assert "_graded_activities" not in stored["diagnosis"]


def test_saved_lock_replace_exclude_and_idempotent_retry(direct_module):
    draft = _make_direct(direct_module, difficulty_max=10)
    item = next(
        q for q in draft["students"][0]["items"] if q["question_id"] in range(100, 114)
    )

    def command(action, token, revision, replacement=None):
        return RecommendationEditCommand(
            request_token=token * 32,
            expected_revision=revision,
            action=action,
            student_id="A",
            item_id=item["item_id"],
            actor_ref="synthetic",
            reason="合成验收",
            replacement_question_id=replacement,
        )

    lock = command("lock", "b", 1)
    locked = direct_module.edit(draft["draft_id"], lock)
    assert direct_module.edit(draft["draft_id"], lock) == locked
    with pytest.raises(RecommendationEditInvalid):
        direct_module.edit(draft["draft_id"], command("replace", "c", 2))
    direct_module.edit(draft["draft_id"], command("unlock", "d", 2))
    with pytest.raises(RecommendationEditInvalid):
        direct_module.edit(draft["draft_id"], command("replace", "e", 3, 46))
    other = next(q for q in draft["students"][0]["items"] if q["question_id"] == 300)
    with pytest.raises(RecommendationEditInvalid):
        direct_module.edit(
            draft["draft_id"],
            RecommendationEditCommand(
                request_token="9" * 32,
                expected_revision=3,
                action="replace",
                student_id="A",
                item_id=other["item_id"],
                actor_ref="synthetic",
                reason="不能换入已选题的数字变式",
                replacement_question_id=102,
            ),
        )
    replaced = direct_module.edit(draft["draft_id"], command("replace", "f", 3))
    replacement = next(
        q for q in replaced["students"][0]["items"] if q["item_id"] == item["item_id"]
    )
    assert replacement["question_id"] != item["question_id"]
    assert (
        replacement["target"]["target_difficulty"]
        == item["target"]["target_difficulty"]
    )
    assert replacement["replacement_history"] and replacement["difficulty"] in (7, 8)
    items = replaced["students"][0]["items"]
    assert [q["difficulty"] for q in items] == sorted(q["difficulty"] for q in items)
    assert [q["item_order"] for q in items] == list(range(1, len(items) + 1))
    assert direct_module.get(draft["draft_id"]) == replaced
    removed = direct_module.edit(draft["draft_id"], command("exclude", "1", 4))
    assert (
        len(removed["students"][0]["items"]) == len(draft["students"][0]["items"]) - 1
    )
    assert direct_module.get(draft["draft_id"]) == removed
    assert [q["item_order"] for q in removed["students"][0]["items"]] == list(
        range(1, len(items))
    )

    remaining = removed["students"][0]["items"][0]
    updated = direct_module.edit(
        draft["draft_id"],
        RecommendationEditCommand(
            request_token="8" * 32,
            expected_revision=5,
            action="exclude",
            student_id="A",
            item_id=remaining["item_id"],
            actor_ref="synthetic",
            reason="移除题目后更新题量说明",
        ),
    )
    assert (
        len(updated["students"][0]["items"]) == len(removed["students"][0]["items"]) - 1
    )
    assert direct_module.get(draft["draft_id"]) == updated


def test_legacy_embedded_activities_still_exclude_originals_on_edit(
    direct_module,
):
    from question_bank.services.source_question_link_service import (
        SourceQuestionLinkService,
    )

    links = SourceQuestionLinkService(direct_module.db_path)
    linked_ids = (105, 107, 109, 111)
    for index, qid in enumerate(linked_ids):
        links.confirm_link(
            grading_session_id=9,
            source_question_id=f"LEGACY-{index}",
            bank_question_id=qid,
            link_method="synthetic",
        )
    activities = [
        {"student_id": "A", "session_id": "9", "occurred_at": "2026-09-09"}
    ]
    draft = _make_direct(
        direct_module,
        diagnosis={**_direct_diagnosis(), "_graded_activities": activities},
        difficulty_max=10,
    )
    # Requests stored before the snapshot refactor embedded the activities
    # inside the diagnosis instead of the request-level sibling key.
    with connect(direct_module.db_path) as conn:
        request = json.loads(
            conn.execute(
                "SELECT request_json FROM personalized_recommendation_drafts WHERE draft_id = ?",
                (draft["draft_id"],),
            ).fetchone()[0]
        )
        request["diagnosis"]["_graded_activities"] = request.pop(
            "graded_activities"
        )
        conn.execute(
            "UPDATE personalized_recommendation_drafts SET request_json = ? WHERE draft_id = ?",
            (json.dumps(request, ensure_ascii=False), draft["draft_id"]),
        )
    control = _make_direct(direct_module, token="9", difficulty_max=10)
    control_ids = {
        item["question_id"] for item in control["students"][0]["items"]
    }
    recent_original = next(
        qid for qid in linked_ids if qid not in control_ids
    )
    item = next(
        q
        for q in draft["students"][0]["items"]
        if q["question_id"] in range(100, 114)
    )
    control_item = next(
        q
        for q in control["students"][0]["items"]
        if q["question_id"] in range(100, 114)
        and q["question_id"] != recent_original
    )

    def replace_command(token, target_item):
        return RecommendationEditCommand(
            request_token=token * 32,
            expected_revision=1,
            action="replace",
            student_id="A",
            item_id=target_item["item_id"],
            actor_ref="synthetic",
            reason="合成验收",
            replacement_question_id=recent_original,
        )

    replaced = direct_module.edit(
        control["draft_id"], replace_command("8", control_item)
    )
    assert next(
        q
        for q in replaced["students"][0]["items"]
        if q["item_id"] == control_item["item_id"]
    )["question_id"] == recent_original
    with pytest.raises(RecommendationEditInvalid):
        direct_module.edit(draft["draft_id"], replace_command("7", item))


def test_concurrent_teacher_edits_allow_only_one_revision(direct_module):
    draft = _make_direct(direct_module)
    item = draft["students"][0]["items"][0]

    def apply(token):
        try:
            direct_module.edit(
                draft["draft_id"],
                RecommendationEditCommand(
                    request_token=token * 32,
                    expected_revision=1,
                    action="lock",
                    student_id="A",
                    item_id=item["item_id"],
                    actor_ref="synthetic",
                    reason="并发验收",
                ),
            )
            return "applied"
        except RecommendationRevisionConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(apply, ("b", "c")))
    assert sorted(results) == ["applied", "conflict"]
    assert direct_module.get(draft["draft_id"])["revision"] == 2


def _selection_candidate(qid, text, key=BNU_TARGET, difficulty=5, **extra):
    return {
        "question_id": qid,
        "question_number": str(qid),
        "question_type": "选择题",
        "question_text": text,
        "difficulty": difficulty,
        "stable_keys": [key],
        "required_keys": [key],
        "scope_complete": True,
        "stable_names": {key: key},
        "criterion_version_id": "c" * 64,
        "criterion_point_count": 1,
        "source_paper": "合成题源",
        "similarity_profile": {"tags": []},
        **extra,
    }


def _selection_draft(
    monkeypatch, candidates, diagnosis=None, shared=False, question_count=10, **settings
):
    module = object.__new__(PersonalizedRecommendationModule)
    from question_bank.current_knowledge import CurrentKnowledgeResolver
    from question_bank.knowledge_graph_release.loader import (
        load_release_for_taxonomy_revision,
        load_taxonomy_catalog_for_release,
    )

    release = load_release_for_taxonomy_revision(4)
    module.current_knowledge = CurrentKnowledgeResolver(
        release, load_taxonomy_catalog_for_release(release)
    )
    monkeypatch.setattr(module, "_source_practice_metadata", lambda _: {})
    diagnosis = deepcopy(diagnosis or _direct_diagnosis())
    mastery = {}
    for student in diagnosis["students"]:
        for point in student["weak_points"]:
            for ref in point["source_question_refs"]:
                ref.setdefault("question_difficulty", 5)
            key = point["knowledge_key"]
            mastery[(student["student_id"], key)] = {
                **point,
                "stable_key": key,
                "display_name": key,
            }
    return module._build_draft(
        diagnosis=diagnosis,
        config=PersonalizedRecommendationConfig(
            question_count=question_count,
            paper_mode="shared" if shared else "individual",
            scope_keys=(BNU_CHAPTER4,),
            **settings,
        ),
        candidates=tuple(candidates),
        relations=tuple(
            {"relation_type": "parent", "source_key": key, "target_key": BNU_CHAPTER4}
            for key in (BNU_TARGET, BNU_PREREQ_NEAR)
        ),
        mastery=mastery,
        recent={},
        excluded_question_ids=set(),
    )


def test_sparse_pool_never_fills_low_group_with_hard_questions(monkeypatch):
    diagnosis = _direct_diagnosis((("A", 0.25, 900, BNU_TARGET),))
    ref = diagnosis["students"][0]["weak_points"][0]["source_question_refs"][0]
    ref.update(question_difficulty=4, score_awarded=0, score_rate=0)
    candidates = [
        _selection_candidate(1, "识别图中已知边对应的位置", difficulty=2),
        _selection_candidate(2, "独立建立多组关系完成综合应用", difficulty=4),
    ]
    student = _selection_draft(monkeypatch, candidates, diagnosis)["students"][0]
    assert [q["question_id"] for q in student["items"]] == [1]
    assert student["shortages"][0]["missing_count"] == 9


def test_practice_requirements_cannot_be_pooled_across_parts():
    from question_bank.recommendation.personalized import _practice_matches

    candidate = {
        "practice_observations_by_key": {
            BNU_TARGET: [
                {
                    "part_id": "p1",
                    "response_mode": "process_required",
                    "observable": "写出推理依据",
                },
                {
                    "part_id": "p2",
                    "response_mode": "process_required",
                    "observable": "代入计算",
                },
            ]
        }
    }
    tasks = [{"code": "written_reasoning"}, {"code": "calculation_check"}]
    assert not _practice_matches(candidate, BNU_TARGET, tasks)
    candidate["practice_observations_by_key"][BNU_TARGET][0]["observable"] += (
        "，代入计算"
    )
    assert _practice_matches(candidate, BNU_TARGET, tasks)


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
    for name, picture in (
        ("original", drawing),
        ("printed", ImageChops.darker(drawing, background)),
        ("encoded", encoded),
        ("changed", changed),
        ("label", label),
    ):
        picture.save(folder / f"{name}.png")
    stem = "如图，两根竖直杆高分别为9米、4米，杆底相距12米，求两杆顶端的距离。"

    def row(number, name, text=stem):
        path = f"question_bank/extracted_images/{name}.png"
        return {
            "question_number": str(number),
            "question_text": f"{number}. {text}[[IMAGE:{path}]]",
            "has_images": True,
            "image_paths": [path],
        }

    return {
        "original": row(17, "original"),
        "printed": row(39, "printed", "（3分）" + stem),
        "encoded": row(44, "encoded", "（6分）" + stem),
        "numbers": row(40, "printed", stem.replace("12米", "13米")),
        "drawing": row(41, "changed"),
        "label": row(42, "label"),
        "missing": row(43, "missing"),
    }


@pytest.mark.parametrize("shared", [False, True])
def test_printed_duplicates_are_excluded_from_generation_and_replacement(
    direct_module, shared
):
    from question_bank.recommendation.personalized import _paper_diversity_allowed

    variants = _printed_original_variants(direct_module.data_root)
    rows = [(910, "original"), (911, "encoded"), (912, "drawing")]
    with connect(direct_module.db_path) as conn:
        _insert_bnu24_questions(
            conn,
            tuple(
                (
                    qid,
                    str(qid),
                    "解答题",
                    variants[name]["question_text"],
                    "7",
                    BNU_TARGET,
                )
                for qid, name in rows
            ),
        )
        for qid, name in rows:
            variant = variants[name]
            conn.execute(
                "UPDATE questions SET question_number=?,has_images=1,image_paths=? WHERE id=?",
                (variant["question_number"], json.dumps(variant["image_paths"]), qid),
            )
    _approve_synthetic_criteria(
        direct_module.db_path, direct_module.data_root, (910, 911, 912)
    )
    candidates, _, _ = direct_module._source_snapshot()
    by_id = {q["question_id"]: q for q in candidates}
    assert by_id[910]["duplicate_identity"] != by_id[911]["duplicate_identity"]
    assert by_id[910]["practice_identity"] == by_id[911]["practice_identity"]
    assert not _paper_diversity_allowed(by_id[911], [by_id[910]])
    assert _paper_diversity_allowed(by_id[912], [by_id[910]])
    diagnosis = _direct_diagnosis(
        (("A", 0.9, 900, BNU_TARGET), ("B", 0.9, 900, BNU_TARGET))
    )
    draft = _make_direct(
        direct_module,
        diagnosis=diagnosis,
        paper_mode="shared" if shared else "individual",
    )
    for student in draft["students"]:
        chosen = {q["question_id"] for q in student["items"]}
        assert len(chosen & {910, 911}) == 1
        assert 912 in chosen  # The extra line encodes a genuinely different drawing.
    student = draft["students"][0]
    chosen = {q["question_id"] for q in student["items"]}
    duplicate = ({910, 911} - chosen).pop()
    other = next(q for q in student["items"] if q["question_id"] not in {910, 911})
    message = (
        "shared paper questions cannot be edited"
        if shared
        else "no approved replacement"
    )
    with pytest.raises(RecommendationEditInvalid, match=message):
        direct_module.edit(
            draft["draft_id"],
            RecommendationEditCommand(
                request_token="c" * 32,
                expected_revision=1,
                action="replace",
                student_id=student["student_id"],
                item_id=other["item_id"],
                actor_ref="synthetic",
                reason="合成重复题换题验证",
                replacement_question_id=duplicate,
            ),
        )
    assert direct_module.get(draft["draft_id"]) == draft


def test_adjustable_rules_and_legacy_defaults(direct_module):
    from question_bank.recommendation.personalized import (
        _config_constructor, _difficulty_plan, _paper_diversity_allowed, _hash_payload,
    )
    from backend.api.schemas.training import TrainingGroupingRequest, PersonalizedRecommendationCreateRequest
    from pydantic import ValidationError

    defaults = PersonalizedRecommendationConfig()
    relaxed = PersonalizedRecommendationConfig(max_questions_per_skill=3, max_written_questions=5,
        recent_activity_count=0, difficulty_max=10)
    candidates = [{"question_id": i, "stable_keys": ["sk_synthetic"], "question_type": "解答题",
                   "question_text": f"题{i}"} for i in range(1, 8)]
    assert _paper_diversity_allowed(candidates[1], candidates[:1], relaxed)
    assert not _paper_diversity_allowed(candidates[1], candidates[:1], defaults)
    assert not _paper_diversity_allowed(candidates[3], candidates[:3], relaxed)
    different = [{**c, "stable_keys": [f"sk_{c['question_id']}"]} for c in candidates]
    assert _paper_diversity_allowed(different[4], different[:4], relaxed)
    assert not _paper_diversity_allowed(different[5], different[:5], relaxed)
    assert not _paper_diversity_allowed(different[2], different[:2], defaults)
    target = {"source_question_refs": [{"session_id": i, "question_id": "Q", "full_score": 5,
        "score_awarded": 5, "question_difficulty": 9} for i in range(2)]}
    assert _difficulty_plan({}, .9, 10, target)["maximum"] == 10
    assert _difficulty_plan({}, .9, 8, target)["maximum"] == 8
    assert _difficulty_plan({}, None, 10)["maximum"] == 3
    handout = PersonalizedRecommendationConfig(purpose="handout", question_count=100,
        max_questions_per_skill=20, max_written_questions=20, recent_activity_count=20, difficulty_max=10)
    assert PersonalizedRecommendationConfig(**_config_constructor(handout.to_dict())) == handout
    for cls, extra in ((TrainingGroupingRequest, {"scope_keys": [BNU_CHAPTER4]}),
        (PersonalizedRecommendationCreateRequest, {"request_token": "e" * 32,
         "scope": {"mode": "all"}, "exam_scope": {"mode": "semester"}})):
        body = cls(**extra, purpose="handout", question_count=100, max_questions_per_skill=20,
                   max_written_questions=20, recent_activity_count=20, difficulty_max=10)
        assert body.question_count == 100 and body.recent_activity_count == 20
        with pytest.raises(ValidationError):
            cls(**extra, purpose="training", question_count=100)
        with pytest.raises(ValidationError):
            cls(**extra, purpose="handout", question_count=3, max_written_questions=4)
    legacy = defaults.to_dict()
    for field in ("purpose", "max_questions_per_skill", "max_written_questions", "recent_activity_count"):
        legacy.pop(field)
    assert PersonalizedRecommendationConfig(**_config_constructor(legacy)) == defaults
    _seed_handout_pool(direct_module, count=20)
    observed = {"students": [{"student_id": "A", "weak_points": [{"source_question_refs": [
        {"session_id": 10000+i, "bank_question_id": 1000+i, "occurred_at": f"2026-07-{i+1:02d}T08:00:00+08:00"}
        for i in range(20)]}]}]}
    assert direct_module._recent_question_ids(("A",), diagnosis=observed, recent_activity_count=0) == {"A": set()}
    assert direct_module._recent_question_ids(("A",), diagnosis=observed, recent_activity_count=3) == {"A": {1017, 1018, 1019}}
    assert direct_module._recent_question_ids(("A",), diagnosis=observed, recent_activity_count=20) == {"A": set(range(1000, 1020))}

    # Same student, same source: saved configurable rules are the only difference.
    control = _make_direct(direct_module, token="1")
    relaxed_draft = _make_direct(direct_module, token="2", max_questions_per_skill=3,
        max_written_questions=5, recent_activity_count=0, difficulty_max=10)
    assert relaxed_draft["config"]["difficulty_max"] == 10
    assert relaxed_draft["students"][0]["structure"]["written_limit"] == 5
    assert all(item["difficulty"] <= 10 for item in relaxed_draft["students"][0]["items"])
    with connect(direct_module.db_path) as conn:
        saved = json.loads(conn.execute("SELECT request_json FROM personalized_recommendation_drafts WHERE draft_id=?",
            (relaxed_draft["draft_id"],)).fetchone()[0])
        assert saved["recent_question_ids"] == {"A": []}
        old_request = json.loads(conn.execute("SELECT request_json FROM personalized_recommendation_drafts WHERE draft_id=?",
            (control["draft_id"],)).fetchone()[0])
        old_config = {k: v for k, v in old_request["config"].items()
                      if k not in {"purpose", "max_questions_per_skill", "max_written_questions"}}
        conn.execute("UPDATE personalized_recommendation_drafts SET input_fingerprint=? WHERE draft_id=?",
            (_hash_payload({"diagnosis": old_request["diagnosis"], "config": old_config}), control["draft_id"]))
        for column in ("request_json", "draft_json"):
            value = json.loads(conn.execute(f"SELECT {column} FROM personalized_recommendation_drafts WHERE draft_id=?",
                (control["draft_id"],)).fetchone()[0])
            for field in ("purpose", "max_questions_per_skill", "max_written_questions", "recent_activity_count"):
                value["config"].pop(field)
            value.pop("recent_question_ids", None)
            conn.execute(f"UPDATE personalized_recommendation_drafts SET {column}=? WHERE draft_id=?",
                (json.dumps(value), control["draft_id"]))
    assert _make_direct(direct_module, token="1")["draft_id"] == control["draft_id"]
    # Restored old input and current defaults select the same replacement.
    current = _make_direct(direct_module, token="3")
    item = next(i for i in control["students"][0]["items"] if i["question_id"] in range(100, 114))
    def replace(draft, token):
        return direct_module.edit(draft["draft_id"], RecommendationEditCommand(request_token=token * 32,
            expected_revision=1, action="replace", student_id="A", item_id=item["item_id"],
            actor_ref="synthetic", reason="旧草稿规则对照"))["students"][0]["items"]
    assert [i["question_id"] for i in replace(control, "4")] == [i["question_id"] for i in replace(current, "5")]


def _seed_handout_pool(module, count=120):
    rows = tuple((1000+i, str(1000+i), "解答题" if i % 5 == 0 else "填空题",
                  f"测例{i:03d}", "7", BNU_TARGET) for i in range(count))
    with connect(module.db_path) as conn:
        _insert_bnu24_questions(conn, rows)
    _approve_synthetic_criteria(module.db_path, module.data_root, tuple(row[0] for row in rows))


def test_fixed_class_assembly_preserves_all_members_order_and_idempotency(direct_module):
    """Teacher selection reuses freezing while retaining evidence-free members."""
    _seed_handout_pool(direct_module, count=8)
    diagnosis = _direct_diagnosis((("A", .6, 900, BNU_TARGET),))
    diagnosis['students'].append({'student_id': 'NO-EVIDENCE', 'class_id': '合成班', 'weak_points': []})
    order = [1007, 1001, 1003, 1002, 1006, 1004, 1005, 1000]
    snapshot = {'source': '班级组卷 · 全班', 'revision': 'a'*64, 'class_ids': ['合成班'],
                'session_ids': [7], 'question_ids': order, 'title': 'TEST-固定全班卷'}
    config = PersonalizedRecommendationConfig(paper_mode='shared', question_count=8,
        target_keys=(BNU_TARGET,),
        curriculum_volume_id='bnu24-math-g7-lower', max_questions_per_skill=8,
        max_written_questions=8, recent_activity_count=0)
    create = lambda token: direct_module.create(request_token=token*32, diagnosis=diagnosis,
        config=config, actor_ref='test', assembly_snapshot=snapshot, graded_activities=[])
    first = create('1')
    assert create('1') == create('2') == first
    assert direct_module.get_by_request_token('2'*32) == first
    from question_bank.recommendation.personalized import RecommendationRequestConflict
    with pytest.raises(RecommendationRequestConflict):
        direct_module.create(request_token='2'*32,diagnosis=diagnosis,config=config,actor_ref='test',
            assembly_snapshot={**snapshot,'revision':'b'*64},graded_activities=[])
    assert {s['student_id'] for s in first['students']} == {'A', 'NO-EVIDENCE'}
    assert first['config']['assembly_source'] == snapshot
    for student in first['students']:
        assert [i['question_id'] for i in student['items']] == order
        assert all(i['locked'] for i in student['items'])
    assert direct_module.ensure_current(first['draft_id']) == first
    with connect(direct_module.db_path) as conn:
        assert conn.execute('SELECT COUNT(*) FROM personalized_recommendation_drafts').fetchone()[0] == 1
    from fastapi.testclient import TestClient
    from types import SimpleNamespace
    from backend.api.app import create_app
    from backend.api.dependencies import get_assembly_workspace_service, get_personalized_recommendation_module, get_request_diagnosis_profile_service
    from question_bank.services.assembly_workspace_service import AssemblyWorkspaceService
    workspace = AssemblyWorkspaceService(direct_module.data_root)
    saved = workspace.save_draft(expected_revision=workspace.load_draft().revision,
        draft={'basket_ids':order,'order_ids':order,'title':'TEST-入口卷','practice_rules':{
            key:config.to_dict()[key] for key in ('purpose','question_count','difficulty_max','max_questions_per_skill','max_written_questions','recent_activity_count')},
            'assembly_context':{'class_ids':['合成班'],'session_ids':[7],'curriculum_volume_id':config.curriculum_volume_id,'sources':{}}})
    app = create_app()
    app.dependency_overrides[get_assembly_workspace_service] = lambda: workspace
    app.dependency_overrides[get_personalized_recommendation_module] = lambda: direct_module
    app.dependency_overrides[get_request_diagnosis_profile_service] = lambda: SimpleNamespace(build_profiles=lambda **_:diagnosis, graded_activities=lambda _:[])
    client = TestClient(app)
    body = {'request_token':'3'*32,'class_ids':['合成班'],'draft_revision':saved.revision,'rules':saved.practice_rules}
    response = client.post('/api/training/personalized-drafts/from-assembly', json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert {s['student_id'] for s in result['students']} == {'A','NO-EVIDENCE'}
    assert [i['question_id'] for i in result['students'][0]['items']] == order
    assert client.post('/api/training/personalized-drafts/from-assembly', json={**body,'request_token':'4'*32}).json() == result
    assert client.post('/api/training/personalized-drafts/from-assembly', json={**body,'draft_revision':'0'*64}).status_code == 409
    client.close()


def _record_legacy_training(module, question_ids, *, student_id="A", name="SYN-TRAINING",
                            occurred_at="2026-07-30T08:00:00+08:00", scored=True):
    with connect(module.db_path) as conn:
        task_id = conn.execute("INSERT INTO training_tasks(task_code) VALUES (?)", (name,)).lastrowid
        variant_id = conn.execute(
            "INSERT INTO training_variants(task_id,variant_key,variant_type) VALUES (?,'synthetic','individual')",
            (task_id,),
        ).lastrowid
        for order, qid in enumerate(question_ids, 1):
            code = f"{name}-Q{order}"
            conn.execute(
                "INSERT INTO training_task_items(variant_id,task_item_code,bank_question_id,item_order,stage) VALUES (?,?,?,?,'direct')",
                (variant_id, code, qid, order),
            )
            if scored:
                conn.execute(
                    "INSERT INTO training_attempts(task_item_code,student_id,score_awarded,full_score,created_at) VALUES (?,?,3,5,?)",
                    (code, student_id, occurred_at),
                )


@pytest.mark.parametrize("paper_mode", ["individual", "shared"])
def test_recent_originals_follow_purpose_in_generation_preview_and_edit(direct_module, paper_mode):
    from question_bank.services.source_question_link_service import SourceQuestionLinkService

    _seed_handout_pool(direct_module, count=40)
    diagnosis = _direct_diagnosis((("A", .9, 900, BNU_TARGET), ("B", .9, 900, BNU_TARGET)))
    settings = dict(paper_mode=paper_mode, max_questions_per_skill=8, max_written_questions=8)
    control = _make_direct(direct_module, diagnosis=diagnosis, recent_activity_count=0, **settings)
    control_ids = [item["question_id"] for item in control["students"][0]["items"]]
    assert len(control_ids) == len(set(control_ids)) == 8
    exam_id, *training_ids = control_ids
    SourceQuestionLinkService(direct_module.db_path).confirm_link(
        grading_session_id=9, source_question_id="SYN-EXAM", bank_question_id=exam_id, link_method="synthetic")
    # Only A has this history; the shared paper must apply the members' union.
    diagnosis["_graded_activities"] = [{"student_id": "A", "session_id": "9", "occurred_at": "2026-07-29"}]
    _record_legacy_training(direct_module, training_ids)
    _record_legacy_training(direct_module, (1039,), name="SYN-UNMARKED", scored=False)
    assert direct_module._recent_question_ids(("A",), diagnosis=diagnosis, purpose="handout",
                                              recent_activity_count=1) == {"A": {exam_id}}
    assert direct_module._recent_question_ids(("A",), diagnosis=diagnosis, purpose="handout",
                                              recent_activity_count=0) == {"A": set()}
    requests = {}
    drafts = {}
    for purpose, token in (("training", "b"), ("handout", "c")):
        draft = _make_direct(direct_module, diagnosis=diagnosis, token=token, purpose=purpose, **settings)
        drafts[purpose] = draft
        ids = [item["question_id"] for item in draft["students"][0]["items"]]
        assert len(ids) == len(set(ids)) == 8
        assert exam_id not in ids
        if purpose == "training":
            assert set(ids).isdisjoint(training_ids)
        else:
            assert set(ids).intersection(training_ids)
        with connect(direct_module.db_path) as conn:
            request = json.loads(conn.execute(
                "SELECT request_json FROM personalized_recommendation_drafts WHERE draft_id=?",
                (draft["draft_id"],),
            ).fetchone()[0])
        requests[purpose] = request
        assert set(request["recent_question_ids"]["A"]) == (
            {exam_id, *training_ids} if purpose == "training" else {exam_id})
        assert request["recent_question_ids"]["B"] == []
        # Read-only evaluation, also used by the fixed class assembly helper.
        config = PersonalizedRecommendationConfig(question_count=8, scope_keys=(BNU_CHAPTER4,), purpose=purpose, **settings)
        evaluated = direct_module.evaluate_candidates(diagnosis=diagnosis, config=config)
        pool_ids = {entry["candidate"]["question_id"] for entry in evaluated["pools"]["A"]}
        assert exam_id not in pool_ids
        assert bool(pool_ids.intersection(training_ids)) == (purpose == "handout")
        if paper_mode == "shared":
            preview = direct_module.chapter_groups(diagnosis=diagnosis,
                config=PersonalizedRecommendationConfig(question_count=8, group_scope_keys=(BNU_CHAPTER4,),
                    target_keys=(BNU_TARGET,), purpose=purpose, **settings), member_ids=("A", "B"))["selection"]
            assert preview["available_question_count"] == 8
            assert preview["source_version"]

    # A later marked activity cannot change either stored exclusion snapshot.
    _record_legacy_training(direct_module, (1001,), name="SYN-LATER", occurred_at="2026-07-31")
    for purpose, request in requests.items():
        assert direct_module._request_recent(request, ("A", "B")) == {
            sid: set(ids) for sid, ids in request["recent_question_ids"].items()}
        assert direct_module.get(drafts[purpose]["draft_id"]) == drafts[purpose]
        assert direct_module.ensure_current(drafts[purpose]["draft_id"]) == drafts[purpose]

    # For legacy requests without a frozen set, the saved purpose still applies.
    for purpose, request in requests.items():
        legacy = deepcopy(request)
        legacy.pop("recent_question_ids")
        recent = direct_module._request_recent(legacy, ("A", "B"))
        assert recent["A"] == ({exam_id, *training_ids, 1001} if purpose == "training" else {exam_id})

    # Shared drafts have no per-student edit operation in the existing flow.
    if paper_mode == "shared":
        return

    # Choose an unselected historical training original through the real edit.
    handout = drafts["handout"]
    used = {item["question_id"] for item in handout["students"][0]["items"]}
    candidate = next(iter(set(training_ids) - used), None)
    if candidate is None:
        candidate = training_ids[0]
        item = next(item for item in handout["students"][0]["items"] if item["question_id"] == candidate)
        handout = direct_module.edit(handout["draft_id"], RecommendationEditCommand(
            request_token="d" * 32, expected_revision=1, action="exclude", student_id="A",
            item_id=item["item_id"], actor_ref="synthetic", reason="合成讲义换题验收"))
    item = handout["students"][0]["items"][0]
    replaced = direct_module.edit(handout["draft_id"], RecommendationEditCommand(
        request_token="e" * 32, expected_revision=handout["revision"], action="replace", student_id="A",
        item_id=item["item_id"], replacement_question_id=candidate, actor_ref="synthetic", reason="复用历史训练题"))
    assert any(item["question_id"] == candidate for item in replaced["students"][0]["items"])
    with pytest.raises(RecommendationEditInvalid):
        direct_module.edit(replaced["draft_id"], RecommendationEditCommand(
            request_token="f" * 32, expected_revision=replaced["revision"], action="replace", student_id="A",
            item_id=item["item_id"], replacement_question_id=exam_id, actor_ref="synthetic", reason="考试原题仍排除"))


@pytest.mark.parametrize("recent_count", [0, 1, 3])
def test_next_round_excludes_just_marked_paper_and_freezes_retry(direct_module, recent_count):
    from backend.training_assessment.evidence import TrainingEvidencePublisher
    from question_bank.personalized_papers import PersonalizedPaperModule, CreatePaperCommand
    from question_bank.services.source_question_link_service import SourceQuestionLinkService

    _seed_handout_pool(direct_module, count=40)
    links = SourceQuestionLinkService(direct_module.db_path)
    for session, qid in ((9, 1000), (10, 1001)):
        links.confirm_link(grading_session_id=session, source_question_id="SYN-EXAM", bank_question_id=qid,
                           link_method="synthetic")
    activities = [{"student_id": "A", "session_id": "9", "occurred_at": "2026-07-29"}]
    diagnosis = {**_direct_diagnosis(), "_graded_activities": activities}
    first = _make_direct(direct_module, diagnosis=diagnosis, recent_activity_count=recent_count,
                         max_questions_per_skill=8, max_written_questions=8)
    first_ids = {item["question_id"] for item in first["students"][0]["items"]}
    assert len(first_ids) == 8
    papers = PersonalizedPaperModule(db_path=direct_module.db_path, data_root=direct_module.data_root, clock=direct_module.clock)
    paper = papers.create_review_instance(first["draft_id"], CreatePaperCommand(operation_token="b" * 32,
        expected_draft_revision=1, student_id="A", actor_ref="synthetic"))
    pid = paper["paper_instance_id"]
    # Generated but unmarked instances do not count as completed training.
    assert direct_module._recent_question_ids(("A",), diagnosis=diagnosis, recent_activity_count=recent_count) == {
        "A": {1000} if recent_count else set()}
    batch_id, submission_id, run_id = "c" * 64, "d" * 64, "e" * 64
    with connect(direct_module.db_path) as conn:
        conn.execute("INSERT INTO training_scan_batches(batch_id,operation_token,operation_fingerprint,paper_batch_id,created_by) VALUES (?,?,?,?,?)",
                     (batch_id, "c" * 32, "c" * 64, paper["paper_batch_id"], "synthetic"))
        # Submitted before the frozen exam; just marked now. Next round must
        # include it even with a one-activity window, without moving old events.
        conn.execute("INSERT INTO training_submissions(submission_id,batch_id,paper_instance_id,student_id,status,expected_total_pages,created_at) VALUES (?,?,?,'A','ready',1,'2026-07-02')",
                     (submission_id, batch_id, pid))
        conn.execute("INSERT INTO training_assessment_runs(run_id,submission_id,submission_revision,status,expected_question_count,expected_point_count,finished_at) VALUES (?,?,1,'succeeded',8,8,'2026-07-30')",
                     (run_id, submission_id))
        for item in conn.execute("SELECT * FROM personalized_paper_items WHERE paper_instance_id=? ORDER BY item_order", (pid,)).fetchall():
            result_id = hashlib.sha256(item["task_item_code"].encode()).hexdigest()
            conn.execute("INSERT INTO training_question_results(question_result_id,run_id,submission_id,submission_revision,task_item_code,item_order,criterion_version_id,criterion_hash,status,met_count,total_count) VALUES (?,?,?,1,?,?,?,?, 'candidate',1,1)",
                         (result_id, run_id, submission_id, item["task_item_code"], item["item_order"], item["criterion_version_id"], item["criterion_hash"]))
    publisher = TrainingEvidencePublisher(db_path=direct_module.db_path, data_root=direct_module.data_root,
                                         outcome_loader=lambda *args: None, clock=direct_module.clock)
    context = publisher._context(submission_id, 1)
    assert context["recent_question_ids"] == {"A": [1000] if recent_count else []}
    next_round = publisher._next_round(context, evidence_version="SYN-EVIDENCE", actor_ref="synthetic",
                                      mastery_changes=[], publication_pending=False)
    assert next_round["status"] == "draft", next_round
    next_ids = {item["question_id"] for item in next_round["student"]["items"]}
    assert len(next_ids) == 8
    assert len(first_ids & next_ids) == (0 if recent_count else 8)
    with connect(direct_module.db_path) as conn:
        request = json.loads(conn.execute("SELECT request_json FROM personalized_recommendation_drafts WHERE draft_id=?",
            (next_round["draft_id"],)).fetchone()[0])
    assert request["graded_activities"] == activities
    assert request["config"] == first["config"]
    expected = first_ids | ({1000} if recent_count > 1 else set()) if recent_count else set()
    assert set(request["recent_question_ids"]["A"]) == expected
    _record_legacy_training(direct_module, (1002,), name="SYN-AFTER-NEXT", occurred_at="2026-07-31")
    retry = publisher._next_round(context, evidence_version="SYN-EVIDENCE", actor_ref="synthetic",
                                 mastery_changes=[], publication_pending=False)
    assert retry["draft_id"] == next_round["draft_id"] and retry["student"] == next_round["student"]
    print(f"近期次数={recent_count}：首轮8题、下一轮8题，重复{len(first_ids & next_ids)}题；首轮={sorted(first_ids)}；下一轮={sorted(next_ids)}")


def test_handout_100_exports_in_order_and_consumes_download_without_training(direct_module, tmp_path):
    import zipfile
    from io import BytesIO
    from docx import Document
    from fastapi.testclient import TestClient
    from backend.api.app import create_app
    from backend.api.dependencies import get_job_manager, get_job_file_service, get_personalized_recommendation_module
    from backend.jobs.manager import JobManager
    from backend.jobs.store import JobStore
    from backend.jobs.training_handout import run_training_handout_export
    from backend.files.service import JobFileService
    from db_manager import DBManager
    from question_bank.personalized_papers import PersonalizedPaperModule, CreatePaperCommand, PaperInvalid

    direct_module.clock = lambda: datetime.now(UTC)
    _seed_handout_pool(direct_module)
    students = (("A", .9, 900, BNU_TARGET), ("B", .9, 900, BNU_TARGET))
    draft = direct_module.create(request_token="a"*32, diagnosis=_direct_diagnosis(students),
        config=PersonalizedRecommendationConfig(purpose="handout", question_count=100, max_questions_per_skill=100,
            max_written_questions=100, recent_activity_count=0, scope_keys=(BNU_CHAPTER4,)), actor_ref="synthetic")
    assert all(len(s["items"]) == 100 for s in draft["students"])
    grading_path = tmp_path / "grading.db"
    DBManager(grading_path).initialize()
    reports = tmp_path / "reports"
    manager = JobManager(JobStore(grading_path))
    manager.register("personalized_handout_export", lambda context: run_training_handout_export(context=context,
        question_bank_db_path=direct_module.db_path, data_root=direct_module.data_root, reports_dir=reports))
    app = create_app()
    app.dependency_overrides[get_job_manager] = lambda: manager
    app.dependency_overrides[get_job_file_service] = lambda: JobFileService(reports)
    app.dependency_overrides[get_personalized_recommendation_module] = lambda: direct_module
    client = TestClient(app)
    url = f"/api/training/personalized-drafts/{draft['draft_id']}/handout-exports"
    body = {"request_token": "b"*32, "expected_revision": 1}
    try:
        assert client.post(url, json={**body, "expected_revision": 2}).status_code == 409
        response = client.post(url, json=body)
        assert response.status_code == 202, response.text
        job_id = response.json()["id"]
        assert client.post(url, json=body).json()["id"] == job_id
        assert client.post(url, json={**body, "expected_revision": 2}).status_code == 409
        manager.wait(job_id, timeout=60)
        job = manager.store.get_job(job_id)
        assert job.status == "succeeded", job.error
        path = Path(job.result["file_path"])
        response = client.get(f"/api/jobs/{job_id}/download")
        assert response.status_code == 200
        with zipfile.ZipFile(BytesIO(response.content)) as archive:
            assert len(archive.namelist()) == 2
            for name, student in zip(archive.namelist(), draft["students"], strict=True):
                doc = Document(BytesIO(archive.read(name)))
                text = [p.text for p in doc.paragraphs]
                assert f"{student['student_name']} 讲义" in text
                answer_start = text.index("答案")
                expected = [i["question_id"] for i in sorted(student["items"], key=lambda i: i["item_order"])]
                from question_bank.services.question_read_service import QuestionBankReadService
                rows = QuestionBankReadService(direct_module.db_path).get_questions(expected)
                by_id = {r["id"]: r for r in rows}
                body_text = "\n".join(text[:answer_start])
                offsets = [body_text.index(by_id[q]["question_text"]) for q in expected]
                assert offsets == sorted(offsets)
                assert len([p for p in text[answer_start:] if "合成答案" in p]) == 100
        assert not path.exists()
        assert client.get(f"/api/jobs/{job_id}/download").status_code == 410
        assert "download_url" not in client.get(f"/api/jobs/{job_id}").json()["result"]
        papers = PersonalizedPaperModule(db_path=direct_module.db_path, data_root=direct_module.data_root)
        with pytest.raises(PaperInvalid, match="讲义"):
            papers.create_review_instance(draft["draft_id"], CreatePaperCommand(operation_token="c"*32,
                expected_draft_revision=1, student_id="A", actor_ref="synthetic"))
        with pytest.raises(PaperInvalid, match="讲义"):
            papers.create_review_batch(draft["draft_id"], operation_token="d"*32, expected_draft_revision=1,
                actor_ref="synthetic")
        with connect(direct_module.db_path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM personalized_paper_instances").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM training_evidence_records").fetchone()[0] == 0
        assert direct_module._recent_question_ids(("A", "B"), graded_activities=[],
                                                  recent_activity_count=3) == {"A": set(), "B": set()}
        # Shared handout publishes one Word, including a short handout that must stay unrecoverable.
        shared = direct_module.create(request_token="e"*32, diagnosis=_direct_diagnosis(students),
            config=PersonalizedRecommendationConfig(purpose="handout", paper_mode="shared", question_count=8,
                target_keys=(BNU_TARGET,), max_written_questions=5, recent_activity_count=0), actor_ref="synthetic")
        shared_url = f"/api/training/personalized-drafts/{shared['draft_id']}/handout-exports"
        response = client.post(shared_url, json={"request_token": "f"*32, "expected_revision": 1})
        assert response.status_code == 202, response.text
        manager.wait(response.json()["id"], timeout=30)
        job = manager.store.get_job(response.json()["id"])
        assert job.status == "succeeded", job.error
        assert job.result["paper_count"] == 1 and Path(job.result["file_path"]).suffix == ".docx"
        with pytest.raises(PaperInvalid, match="讲义"):
            papers.create_review_instance(shared["draft_id"], CreatePaperCommand(operation_token="0"*32,
                expected_draft_revision=1, student_id="A", actor_ref="synthetic"))
        with connect(direct_module.db_path) as conn:
            conn.execute("UPDATE questions SET question_text=question_text || '已变化' WHERE id=1001")
        assert client.post(url, json={"request_token": "0"*32, "expected_revision": 1}).status_code == 409
    finally:
        manager.shutdown()


def test_handout_generation_timings_same_synthetic_class(direct_module):
    from time import perf_counter
    _seed_handout_pool(direct_module)
    diagnosis = _direct_diagnosis(tuple((f"SYN-{i:02d}", .9, 900, BNU_TARGET) for i in range(30)))
    for count, token in ((10, "a"), (50, "b"), (100, "c")):
        started = perf_counter()
        draft = direct_module.create(request_token=token*32, diagnosis=diagnosis,
            config=PersonalizedRecommendationConfig(purpose="handout", question_count=count,
                max_questions_per_skill=count, max_written_questions=count, recent_activity_count=0,
                scope_keys=(BNU_CHAPTER4,)), actor_ref="synthetic")
        elapsed = perf_counter() - started
        assert all(len(s["items"]) == count for s in draft["students"])
        print(f"HANDOUT_BENCH students=30 questions={count} seconds={elapsed:.3f}")
