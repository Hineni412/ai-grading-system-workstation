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
    response = client.post(
        "/api/training/diagnosis",
        json={"scope": {"mode": "all"}, "exam_scope": exams, "grouping": settings},
    )
    assert response.status_code == 200, response.text
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
