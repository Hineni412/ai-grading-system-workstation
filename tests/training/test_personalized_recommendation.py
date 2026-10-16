from __future__ import annotations
import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
import pytest
from question_bank.database.schema import connect
from question_bank.knowledge_graph_release import bootstrap_release, load_release_for_taxonomy_revision
from question_bank.knowledge_graph_release.contracts import KnowledgeGraphRelease
from question_bank.recommendation.personalized import PersonalizedRecommendationConfig, PersonalizedRecommendationModule, RecommendationEditCommand, RecommendationEditInvalid, RecommendationRevisionConflict
from question_bank.training_criteria import QuestionAnalysisInputLoader


NOW = datetime(2026, 7, 30, 8, 0, tzinfo=UTC)


LOCAL_ONE = "ki_00000000000000000000000000000001"


LOCAL_TWO = "ki_00000000000000000000000000000002"


BNU_CHAPTER4 = "kp_bnu24_math_g7_lower_4"


BNU_TARGET = "sk_bnu24_math_g7_lower_4_2_101"


BNU_PREREQ_NEAR = "sk_bnu24_math_g7_lower_4_1_101"


BNU_PREREQ_EARLIER = "sk_bnu24_math_g7_lower_4_1_102"


BNU_TRANSFER_SIBLING = "sk_bnu24_math_g7_lower_4_3_101"


BNU_TRANSFER_OTHER = "sk_bnu24_math_g7_lower_4_1_01"


BNU_OTHER_CHAPTER = "sk_bnu24_math_g7_lower_5_1_101"


SK_LINEAR = "sk_alg_linear_equation"


SK_CONGRUENCE = "sk_geo_triangle_congruence"


SK_CONSTRUCTION = "sk_geo_construction"


SK_FUN_LINEAR = "sk_fun_linear"


SK_PARENTS = {
    SK_LINEAR: "kp_bnu24_math_g7_upper_5_2_1",
    SK_CONGRUENCE: "kp_bnu24_math_g7_lower_4_3_9",
    SK_CONSTRUCTION: "kp_bnu24_math_g7_lower_4_3_4",
    SK_FUN_LINEAR: "kp_bnu24_math_g8_upper_4_2_2",
}


def _install_release_with_skills(
    db_path: Path, *, revision: int, skill_parents: dict[str, str]
) -> None:
    """Bootstrap a test release that adds synthetic skill nodes under parents."""
    release = load_release_for_taxonomy_revision(revision)
    payload = release.to_dict()
    payload.pop("content_hash", None)
    source_id = str(payload["sources"][0]["source_id"])
    payload["release_id"] = f"{payload['release_id']}-skill-test"
    for node in payload["core_nodes"]:
        node.setdefault("exclude_scope", "无排除范围")
    existing_keys = {str(node["stable_key"]) for node in payload["core_nodes"]}
    payload["core_nodes"] = [
        *payload["core_nodes"],
        *(
            {
                "stable_key": key,
                "display_name": f"合成技能 {key}",
                "aliases": [],
                "node_kind": "skill",
                "status": "active",
                "definition": "合成测试技能",
                "include_scope": "合成测试技能范围",
                "exclude_scope": "无排除范围",
                "curriculum_anchors": [f"合成锚点/{key}"],
                "observable_evidence": "合成技能证据",
                "rationale": "合成技能节点",
                "evidence_source_ids": [source_id],
            }
            for key in skill_parents
        ),
        *(
            {
                "stable_key": parent,
                "display_name": f"合成主题 {parent}",
                "aliases": [],
                "node_kind": "core",
                "status": "active",
                "definition": "合成测试主题",
                "include_scope": "合成测试主题范围",
                "exclude_scope": "无排除范围",
                "curriculum_anchors": [f"合成锚点/{parent}"],
                "observable_evidence": "合成主题证据",
                "rationale": "合成主题节点",
                "evidence_source_ids": [source_id],
            }
            for parent in set(skill_parents.values())
            if parent not in existing_keys
        ),
    ]
    payload["relations"] = [
        *payload["relations"],
        *(
            {
                "relation_key": hashlib.sha256(
                    f"rel-skill-{key}".encode()
                ).hexdigest(),
                "source_key": key,
                "target_key": parent,
                "relation_type": "parent",
                "rationale": "合成技能归属",
                "basis_kind": "curriculum_structure",
                "strength": "required",
                "evidence_source_ids": [source_id],
                "source_locator": "合成来源定位",
            }
            for key, parent in skill_parents.items()
        ),
    ]
    bootstrap_release(
        Path(db_path),
        KnowledgeGraphRelease.from_mapping(payload),
        actor_ref="test-suite",
        source_reference="synthetic-skill-test-release",
        reason="install skill test release",
    )


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


@pytest.mark.parametrize("mismatch", ["missing", "version", "part", "question"])
def test_unaligned_exam_source_does_not_borrow_current_question_meaning(mismatch):
    module = object.__new__(PersonalizedRecommendationModule)
    source = {"bank_question_id": 900, "evidence_version_id": "frozen-version",
              "evidence_part_id": "part1", "direct_keys": [BNU_TARGET],
              "target_facets": [{"part_id": "part1", "direct_keys": [BNU_TARGET]}],
              "practice_observations_by_key": {}, "direct_fine_terms": []}
    if mismatch != "missing":
        source[{"version": "evidence_version_id", "part": "evidence_part_id",
                "question": "bank_question_id"}[mismatch]] = "other"
    metadata = {900: {"direct_keys": [BNU_PREREQ_NEAR], "parts": [],
        "exam_sources": {} if mismatch == "missing" else {"1": {"EX": source}}}}
    ref = {"session_id": 1, "question_id": "EX", "bank_question_id": 900,
           "source_kind": "current_exam", "assessment": {"part_id": "exam-part",
               "evidence_part_id": "part1", "evidence_version_id": "frozen-version"},
           "direct_keys": [BNU_PREREQ_NEAR], "target_facets": [{"direct_keys": [BNU_PREREQ_NEAR]}]}
    enriched = module._enrich_source_ref(ref, metadata)
    assert enriched["direct_keys"] == [] and enriched["target_facets"] == []
    assert enriched["practice_observations_by_key"] == {}
    assert enriched["task_evidence_version_matches"] is False
    assert enriched["source_alignment_reason"] == (
        "frozen_source_missing" if mismatch == "missing" else "frozen_source_mismatch")


@pytest.fixture()
def bnu24_difficulty_module(
    tmp_path: Path,
    question_bank_database,
) -> PersonalizedRecommendationModule:
    db_path = tmp_path / "question_bank.db"
    data_root = tmp_path / "data"
    question_bank_database(db_path, taxonomy_revision=7)
    _seed_bnu24_difficulty_sources(db_path, data_root)
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
        3: SK_LINEAR,
        4: SK_CONGRUENCE,
        5: SK_CONSTRUCTION,
        6: SK_LINEAR,
        7: SK_LINEAR,
        8: SK_LINEAR,
        9: SK_LINEAR,
        10: SK_CONGRUENCE,
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
            SK_LINEAR,
            LOCAL_TWO,
            "prerequisite",
        )
        _insert_relation(
            connection,
            "rel-congruence-prerequisite",
            SK_CONGRUENCE,
            LOCAL_ONE,
            "prerequisite",
        )
        _insert_relation(
            connection,
            "rel-construction-related",
            SK_CONSTRUCTION,
            SK_CONGRUENCE,
            "related",
        )
        _insert_relation(
            connection,
            "rel-function-prerequisite",
            SK_FUN_LINEAR,
            SK_LINEAR,
            "prerequisite",
        )
        _insert_relation(
            connection,
            "rel-function-related",
            SK_FUN_LINEAR,
            SK_CONSTRUCTION,
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
                            "knowledge_key": {
                                "一元一次方程": SK_LINEAR,
                                "三角形全等": SK_CONGRUENCE,
                                "尺规作图": SK_CONSTRUCTION,
                                "一次函数": SK_FUN_LINEAR,
                            }[weak],
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
                                            "一元一次方程": SK_LINEAR,
                                            "三角形全等": SK_CONGRUENCE,
                                            "尺规作图": SK_CONSTRUCTION,
                                            "一次函数": SK_FUN_LINEAR,
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
            **{"question_count": 8, "scope_keys": (BNU_CHAPTER4,),
               "max_questions_per_skill": 8, **settings}
        ),
        actor_ref="synthetic",
    )


def test_semester_group_of_seventeen_creates_one_shared_paper_through_api(
    direct_module,
    monkeypatch,
):
    from backend.api.app import create_app
    from backend.api.dependencies import get_diagnosis_profile_service, get_request_diagnosis_profile_service
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
                if student["student_id"] in members:
                    ref["assessment"]["part_difficulty"] = 8

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
    settings = {
        "scope_keys": [BNU_CHAPTER4],
        "question_count": 10,
        "difficulty_max": 8,
        "max_questions_per_skill": 10,
    }
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
    import question_bank.recommendation.personalized as recommendation
    preference_reads = []
    original_preference = recommendation._direct_preference

    def read_preference(*args, **kwargs):
        preference_reads.append(1)
        return original_preference(*args, **kwargs)

    monkeypatch.setattr(recommendation, "_direct_preference", read_preference)
    profile_hash_requests, profile_serializations = [], []
    original_hash, original_json = recommendation._hash_payload, recommendation._json

    def hash_input(value, *, _memo=None):
        if _memo is not None and isinstance(value, dict) and "weak_points" in value:
            profile_hash_requests.append((id(value), value["student_id"]))
        return original_hash(value, _memo=_memo)

    def serialize_input(value):
        if isinstance(value, dict) and "student_id" in value and "weak_points" in value:
            profile_serializations.append((id(value), value["student_id"]))
        return original_json(value)

    monkeypatch.setattr(recommendation, "_hash_payload", hash_input)
    monkeypatch.setattr(recommendation, "_json", serialize_input)
    response = client.post(
        "/api/training/diagnosis",
        json={"scope": {"mode": "all"}, "exam_scope": exams, "grouping": settings},
    )
    assert response.status_code == 200, response.text
    # Each group reuses the source snapshot covering the full selected roster.
    assert metadata_reads == [len(source["students"])]
    cached_eligibility_reads = len(eligibility_reads)
    cached_preference_reads = len(preference_reads)
    assert cached_eligibility_reads < len(source["students"])
    # The same profiles serve the first evaluation and the resulting cards.
    # Serialize them once per grouping request, even when revisited.
    assert profile_hash_requests == []
    assert profile_serializations == []
    original_entries = direct_module._candidate_entries

    def entries_without_reuse(**kwargs):
        kwargs.update(source_part_cache=None, target_match_cache=None, source_links=None, eligibility_cache=None,
                      target_evaluation_cache=None, preference_cache=None, _input_values=None)
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
    assert len(preference_reads) - cached_preference_reads > cached_preference_reads
    group = next(
        group
        for group in response.json()["grouping"]["groups"]
        if len(group["members"]) == 17
    )
    assert group["ready"]
    targets = [target["knowledge_key"] for target in group["targets"]]
    def unexpected_regrouping(**kwargs):
        raise AssertionError("group adoption rebuilt automatic groups")
    monkeypatch.setattr("question_bank.recommendation.personalized._quality_group_members", unexpected_regrouping)
    checked = client.post(
        "/api/training/diagnosis",
        json={
            "scope": {"mode": "all"},
            "exam_scope": exams,
            "grouping": {**settings, "member_ids": members, "target_keys": targets},
        },
    )
    assert checked.status_code == 200, checked.text
    assert checked.json()['grouping']['groups'] == []
    assert checked.json()['grouping']['unassigned'] == []
    # Adopting a checked group must only validate its members, without rebuilding
    # every automatic group. The source-version checks remain.
    created = client.post(
        "/api/training/personalized-drafts",
        json={
            "scope": {"mode": "selected", "student_ids": members},
            "exam_scope": exams,
            "paper_mode": "shared",
            "question_count": 10,
            "difficulty_max": 8,
            "max_questions_per_skill": 10,
            "target_keys": targets,
            "group_scope_keys": [BNU_CHAPTER4],
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
    with connect(direct_module.db_path) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM personalized_recommendation_drafts"
            ).fetchone()[0]
            == 1
        )


def test_saved_lock_replace_exclude_and_revision_guard(direct_module):
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
    with pytest.raises(RecommendationRevisionConflict):
        direct_module.edit(draft["draft_id"], lock)
    with pytest.raises(RecommendationEditInvalid):
        direct_module.edit(draft["draft_id"], command("replace", "c", 2))
    unlocked = direct_module.edit(draft["draft_id"], command("unlock", "d", 2))
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
    assert _make_direct(direct_module, difficulty_max=10) == updated
    with connect(direct_module.db_path) as connection:
        saved_history = connection.execute(
            """SELECT e.action, e.before_json, e.after_json, r.draft_json
               FROM personalized_recommendation_events e
               JOIN personalized_recommendation_revisions r
                 ON r.draft_id = e.draft_id AND r.revision = e.resulting_revision
               WHERE e.draft_id = ? ORDER BY e.event_id""",
            (draft["draft_id"],),
        ).fetchall()
    assert [json.loads(row["draft_json"]) for row in saved_history] == [
        draft, locked, unlocked, replaced, removed, updated,
    ]
    assert json.loads(saved_history[1]["before_json"])["locked"] is False
    assert json.loads(saved_history[1]["after_json"])["locked"] is True

    measured_correct = _direct_diagnosis()
    measured_correct["students"][0]["weak_points"][0]["source_question_refs"][0]["score_awarded"] = 5
    probe = _make_direct(direct_module, diagnosis=measured_correct, token="6",
        remediation_only=True, max_unmeasured_questions=1, difficulty_max=10)
    assert len(probe["students"][0]["items"]) == 1
    novel = probe["students"][0]["items"][0]
    assert novel["practice_purpose"] == "new"
    changed = direct_module.edit(probe["draft_id"], RecommendationEditCommand(
        request_token="7" * 32, expected_revision=1, action="replace", student_id="A",
        item_id=novel["item_id"], actor_ref="synthetic", reason="合成未测目标换题"))
    assert changed["students"][0]["items"][0]["question_id"] != novel["question_id"]
    assert changed["students"][0]["items"][0]["practice_purpose"] == "new"
    assert changed["students"][0]["structure"]["new_practice_count"] == 1
    assert direct_module.get(probe["draft_id"]) == changed


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
    monkeypatch, candidates, diagnosis=None, shared=False, question_count=10,
    taxonomy_revision=7, scope_keys=(BNU_CHAPTER4,), **settings
):
    module = object.__new__(PersonalizedRecommendationModule)
    from question_bank.current_knowledge import CurrentKnowledgeResolver
    from question_bank.knowledge_graph_release.loader import load_release_for_taxonomy_revision as load_release_for_taxonomy_revision, load_taxonomy_catalog_for_release

    release = load_release_for_taxonomy_revision(taxonomy_revision)
    module.current_knowledge = CurrentKnowledgeResolver(
        release, load_taxonomy_catalog_for_release(release)
    )
    monkeypatch.setattr(module, "_source_practice_metadata", lambda _: {})
    monkeypatch.setattr(module, "_exam_reference_candidates", lambda items: tuple(items))
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
            scope_keys=scope_keys,
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


def test_personal_remediation_keeps_shortage_and_does_not_fill_correct_targets(monkeypatch):
    candidates = [_selection_candidate(1, "合成方程题"),
                  _selection_candidate(2, "合成邻近目标题", BNU_PREREQ_NEAR)]
    diagnosis = _direct_diagnosis()
    diagnosis["students"][0]["weak_points"].append({
        "knowledge_key": BNU_PREREQ_NEAR, "knowledge_point": BNU_PREREQ_NEAR,
        "source_question_refs": [{"full_score": 5, "score_awarded": 5,
            "question_difficulty": 5, "source_kind": "current_exam"}]})
    draft = _selection_draft(monkeypatch, candidates, diagnosis, remediation_only=True)
    student = draft["students"][0]
    assert [item["question_id"] for item in student["items"]] == [1]
    assert student["items"][0]["practice_purpose"] == "remediation"
    assert student["shortages"][0]["missing_count"] == 9
    assert draft["config"]["remediation_only"] is True
    assert any("只安排" in text for text in student["warnings"])
    diagnosis["students"][0]["weak_points"][0]["source_question_refs"][0]["score_awarded"] = 5
    empty = _selection_draft(monkeypatch, candidates, diagnosis, remediation_only=True)
    assert empty["students"][0]["items"] == []
    assert empty["students"][0]["shortages"][0]["missing_count"] == 10

    # No observation of a target is different from a fresh question on a
    # successful target. Add only bounded, suitable, direct new-target practice.
    candidates += [_selection_candidate(3, "合成未测面积任务", BNU_TRANSFER_SIBLING),
                   _selection_candidate(4, "合成未测推理任务", BNU_TRANSFER_OTHER),
                   _selection_candidate(5, "合成过难任务", BNU_PREREQ_EARLIER, difficulty=10),
                   _selection_candidate(6, "合成超范围任务", BNU_OTHER_CHAPTER)]
    diagnosis["students"][0]["weak_points"][0]["source_question_refs"][0]["score_awarded"] = 4
    supplemented = _selection_draft(monkeypatch, candidates, diagnosis,
        remediation_only=True, max_unmeasured_questions=1)["students"][0]
    assert [item["question_id"] for item in supplemented["items"]] == [1, 3]
    assert [item["practice_purpose"] for item in supplemented["items"]] == ["remediation", "new"]
    assert supplemented["structure"]["new_practice_count"] == 1
    assert "不认定为薄弱" in supplemented["items"][1]["reason"]
    assert all(not item["target"]["source_question_refs"] for item in supplemented["items"] if item["practice_purpose"] == "new")
    diagnosis["students"][0]["weak_points"][0]["source_question_refs"][0]["score_awarded"] = 5
    no_loss = _selection_draft(monkeypatch, candidates, diagnosis,
        remediation_only=True, max_unmeasured_questions=1)["students"][0]
    assert [item["question_id"] for item in no_loss["items"]] == [3]
    from question_bank.recommendation.personalized import _unmeasured_entry, _loss_refs
    observed = {"candidate": candidates[2], "selection_kind": "direct", "practice_purpose": "new",
                "key": BNU_TRANSFER_SIBLING, "matched_key": BNU_TRANSFER_SIBLING}
    for granularity in ("part", "step"):
        for score in (0, 5):
            assert not _unmeasured_entry({**observed, "target": {"source_question_refs": [
                {"full_score": 5, "score_awarded": score, "assessment": {"granularity": granularity}}]}})
    assert not _loss_refs({"source_question_refs": [{"full_score": 5, "score_awarded": None}]})
    assert _unmeasured_entry(observed)


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
    relaxed_skill_cap = PersonalizedRecommendationConfig(max_questions_per_skill=5)
    assert not _paper_diversity_allowed(by_id[911], [by_id[910]], relaxed_skill_cap)
    # Same stem text folds even when the drawing itself differs.
    assert not _paper_diversity_allowed(by_id[912], [by_id[910]], relaxed_skill_cap)
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
        assert len(chosen & {910, 911, 912}) == 1
    student = draft["students"][0]
    chosen = {q["question_id"] for q in student["items"]}
    duplicate = next(qid for qid in (910, 911, 912) if qid not in chosen)
    other = next(q for q in student["items"] if q["question_id"] not in {910, 911, 912})
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


def _seed_handout_pool(module, count=120):
    rows = tuple((1000+i, str(1000+i), "解答题" if i % 5 == 0 else "填空题",
                  f"测例{i:03d}", "7", BNU_TARGET) for i in range(count))
    with connect(module.db_path) as conn:
        _insert_bnu24_questions(conn, rows)
    _approve_synthetic_criteria(module.db_path, module.data_root, tuple(row[0] for row in rows))


def test_fixed_class_assembly_preserves_all_members_order_and_idempotency(direct_module, tmp_path):
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
    assert {s['student_id'] for s in first['students']} == {'A', 'NO-EVIDENCE'}
    assert first['config']['assembly_source'] == snapshot
    for student in first['students']:
        assert [i['question_id'] for i in student['items']] == order
        assert all(i['locked'] for i in student['items'])
    assert direct_module.ensure_current(first['draft_id']) == first
    with connect(direct_module.db_path) as conn:
        assert conn.execute('SELECT COUNT(*) FROM personalized_recommendation_drafts').fetchone()[0] == 1
        receipts = conn.execute("""SELECT e.request_token, e.after_json, r.draft_json
            FROM personalized_recommendation_events e
            JOIN personalized_recommendation_revisions r
              ON r.draft_id=e.draft_id AND r.revision=e.resulting_revision
            ORDER BY e.event_id""").fetchall()
        assert [row['request_token'] for row in receipts] == ['1'*32, '2'*32]
        assert [json.loads(row['draft_json']) for row in receipts] == [first, first]
        assert json.loads(receipts[1]['after_json']) == snapshot
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
    body = {'class_ids':['合成班'],'draft_revision':saved.revision,'rules':saved.practice_rules}
    response = client.post('/api/training/personalized-drafts/from-assembly', json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert {s['student_id'] for s in result['students']} == {'A','NO-EVIDENCE'}
    assert [i['question_id'] for i in result['students'][0]['items']] == order
    assert client.post('/api/training/personalized-drafts/from-assembly', json=body).json() == result
    assert client.post('/api/training/personalized-drafts/from-assembly', json={**body,'draft_revision':'0'*64}).status_code == 409
    client.close()

    # Draft saves recompute recent original ids on every call; the memoized
    # result must stay equal until graded evidence or links commit.
    from backend.repositories.db_manager import DBManager
    from integration.diagnosis_profile_service import DiagnosisProfileService
    from question_bank.services.source_question_link_service import SourceQuestionLinkService
    db_dir = tmp_path / "databases"
    db_dir.mkdir()
    grading_db_path = db_dir / "grading.db"
    grading_db = DBManager(grading_db_path)
    grading_db.initialize()
    rubric_path = tmp_path / "rubric.json"
    rubric_path.write_text(json.dumps(
        {"questions": [{"question_id": "Q1", "max_score": 10}]}, ensure_ascii=False),
        encoding="utf-8")
    with grading_db._connect() as conn:
        conn.execute(
            "INSERT INTO students (id, student_code, name, class_name) VALUES (12, 'S12', '学生甲', '合成班')")
        conn.execute(
            "INSERT INTO grading_sessions (id, session_name, rubric_path, answer_key_path, status, is_deleted)"
            " VALUES (7, '合成考试', ?, '', 'completed', 0)", (str(rubric_path),))
        conn.execute(
            "INSERT INTO exam_papers (id, session_id, front_image, back_image, student_id, match_status, processing_status)"
            " VALUES (701, 7, '', '', 12, 'matched', 'graded')")
        conn.execute(
            "INSERT INTO session_results (id, session_id, student_id, paper_id, total_score, student_score, needs_human_review, raw_json)"
            " VALUES (7001, 7, 12, 701, 10, 6, 0, '{}')")
        conn.execute(
            "INSERT INTO session_details (result_id, question_id, score_awarded, deduction_reason, knowledge_ids, error_category, error_summary, secondary_errors_json)"
            " VALUES (7001, 'Q1', 6, '缺少辅助线', '[\"UNKNOWN\"]', '逻辑断裂', '辅助线思路缺失', '[]')")
    links = SourceQuestionLinkService(direct_module.db_path)
    links.confirm_link(grading_session_id=7, source_question_id='Q1', bank_question_id=900,
                       link_method='paper_question_number')
    diagnosis_service = DiagnosisProfileService(grading_db_path, direct_module.db_path)
    context = {'class_ids': ['合成班'], 'session_ids': [7]}
    rules = {'recent_activity_count': 3, 'purpose': 'handout'}
    first = diagnosis_service.assembly_recent_question_ids(direct_module, context, rules)
    assert 900 in first
    assert diagnosis_service.assembly_recent_question_ids(direct_module, context, rules) == first
    # A newly committed graded result must be reflected on the next call.
    with grading_db._connect() as conn:
        conn.execute(
            "INSERT INTO grading_sessions (id, session_name, rubric_path, answer_key_path, status, is_deleted)"
            " VALUES (8, '合成考试二', ?, '', 'completed', 0)", (str(rubric_path),))
        conn.execute(
            "INSERT INTO exam_papers (id, session_id, front_image, back_image, student_id, match_status, processing_status)"
            " VALUES (801, 8, '', '', 12, 'matched', 'graded')")
        conn.execute(
            "INSERT INTO session_results (id, session_id, student_id, paper_id, total_score, student_score, needs_human_review, raw_json)"
            " VALUES (8001, 8, 12, 801, 10, 7, 0, '{}')")
        conn.execute(
            "INSERT INTO session_details (result_id, question_id, score_awarded, deduction_reason, knowledge_ids, error_category, error_summary, secondary_errors_json)"
            " VALUES (8001, 'Q1', 7, '', '[\"UNKNOWN\"]', NULL, NULL, '[]')")
    links.confirm_link(grading_session_id=8, source_question_id='Q1', bank_question_id=901,
                       link_method='paper_question_number')
    updated = diagnosis_service.assembly_recent_question_ids(direct_module, context, rules)
    assert first <= updated
    assert 901 in updated


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
    from backend.repositories.db_manager import DBManager
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
    body = {"expected_revision": 1}
    try:
        assert client.post(url, json={**body, "expected_revision": 2}).status_code == 409
        response = client.post(url, json=body)
        assert response.status_code == 202, response.text
        job_id = response.json()["id"]
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
        response = client.post(shared_url, json={"expected_revision": 1})
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
        continued = client.post(url, json={"expected_revision": 1})
        assert continued.status_code == 202, continued.text
        manager.wait(continued.json()["id"], timeout=30)
        assert manager.store.get_job(continued.json()["id"]).status == "succeeded"
        assert direct_module.ensure_current(draft["draft_id"])["draft_id"] == draft["draft_id"]
    finally:
        manager.shutdown()
