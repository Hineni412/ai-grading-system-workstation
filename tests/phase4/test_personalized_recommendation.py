from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest

from question_bank.database.schema import connect, initialize_database
from question_bank.recommendation.personalized import (
    PersonalizedRecommendationConfig,
    PersonalizedRecommendationModule,
    RecommendationEditCommand,
    RecommendationEditInvalid,
    RecommendationRequestConflict,
    RecommendationRevisionConflict,
    RecommendationSourceChanged,
)
from question_bank.training_criteria import QuestionAnalysisInputLoader
from tests.current_knowledge_support import install_current_knowledge


NOW = datetime(2026, 7, 30, 8, 0, tzinfo=UTC)
LOCAL_ONE = "ki_00000000000000000000000000000001"
LOCAL_TWO = "ki_00000000000000000000000000000002"


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


def test_five_synthetic_students_receive_explainable_different_drafts(
    recommendation_module: PersonalizedRecommendationModule,
) -> None:
    assert recommendation_module.resolve_target_names(
        ("一元一次方程", "尺规作图")
    ) == ("kp_alg_linear_equation", "kp_geo_construction")
    with pytest.raises(ValueError):
        recommendation_module.resolve_target_names(("未治理目标",))

    config = PersonalizedRecommendationConfig(
        question_count=8,
        expected_minutes=120,
        direct_ratio=0.5,
        prerequisite_ratio=0.25,
        transfer_ratio=0.25,
    )
    first = recommendation_module.create(
        request_token="1" * 32,
        diagnosis=_diagnosis(),
        config=config,
        actor_ref="teacher-1",
    )
    repeated = recommendation_module.create(
        request_token="1" * 32,
        diagnosis=_diagnosis(),
        config=config,
        actor_ref="teacher-1",
    )
    same_input_new_request = recommendation_module.create(
        request_token="2" * 32,
        diagnosis=_diagnosis(),
        config=config,
        actor_ref="teacher-1",
    )

    assert repeated == first
    assert same_input_new_request["draft_id"] != first["draft_id"]
    assert same_input_new_request["result_version"] == first["result_version"]

    by_student = {
        item["student_id"]: item for item in first["students"]
    }
    for student_id in ("SYN-S01", "SYN-S02", "SYN-S03", "SYN-S04"):
        selected = {
            item["question_id"]
            for item in by_student[student_id]["items"]
        }
        assert all(
            item["criterion_version_id"]
            and item["criterion_point_count"] == 1
            and item["reason"]
            for item in by_student[student_id]["items"]
        )
    assert sum(bool(item["items"]) for item in by_student.values()) >= 3

    fallback = by_student["SYN-S05"]
    assert fallback["selection_mode"] == "maintenance_fallback"
    assert fallback["items"]
    assert "不代表系统判断出新的薄弱点" in fallback["warnings"][0]
    serialized = json.dumps(first, ensure_ascii=False)
    assert LOCAL_ONE not in serialized and LOCAL_TWO not in serialized


def test_shared_mode_keeps_questions_and_order_identical_per_student(
    recommendation_module: PersonalizedRecommendationModule,
) -> None:
    draft = recommendation_module.create(
        request_token="9" * 32,
        diagnosis=_diagnosis(student_ids=("SYN-S01", "SYN-S02", "SYN-S03")),
        config=PersonalizedRecommendationConfig(
            paper_mode="shared",
            question_count=8,
            expected_minutes=120,
            direct_ratio=0.5,
            prerequisite_ratio=0.25,
            transfer_ratio=0.25,
            target_keys=("kp_alg_linear_equation",),
        ),
        actor_ref="teacher-1",
    )

    question_sequences = [
        [item["question_id"] for item in student["items"]]
        for student in draft["students"]
    ]
    assert question_sequences[0]
    assert all(sequence == question_sequences[0] for sequence in question_sequences)
    assert draft["config"]["paper_mode"] == "shared"
    with pytest.raises(RecommendationEditInvalid):
        recommendation_module.edit(
            draft["draft_id"],
            RecommendationEditCommand(
                request_token="8" * 32,
                expected_revision=1,
                action="lock",
                student_id=draft["students"][0]["student_id"],
                item_id=draft["students"][0]["items"][0]["item_id"],
                actor_ref="teacher-1",
                reason="同题模式不能只修改一人",
            ),
        )


def test_shortage_unknown_difficulty_and_recent_use_fail_closed(
    recommendation_module: PersonalizedRecommendationModule,
) -> None:
    _mark_question_recent(
        recommendation_module.db_path,
        student_id="SYN-S01",
        question_id=3,
    )
    with connect(recommendation_module.db_path) as connection:
        connection.execute(
            "UPDATE questions SET difficulty = NULL WHERE id = 6"
        )
    draft = recommendation_module.create(
        request_token="3" * 32,
        diagnosis=_diagnosis(student_ids=("SYN-S01",)),
        config=PersonalizedRecommendationConfig(
            question_count=8,
            expected_minutes=30,
        ),
        actor_ref="teacher-1",
    )
    student = draft["students"][0]
    selected = {item["question_id"] for item in student["items"]}

    assert 3 not in selected
    assert 6 not in selected
    assert student["shortages"]
    assert all(
        shortage["reason_code"]
        in {"approved_candidate_shortage", "time_limit_reached"}
        for shortage in student["shortages"]
    )


def test_teacher_lock_replace_and_exclude_keep_history_and_revision(
    recommendation_module: PersonalizedRecommendationModule,
) -> None:
    draft = recommendation_module.create(
        request_token="4" * 32,
        diagnosis=_diagnosis(student_ids=("SYN-S01",)),
        config=PersonalizedRecommendationConfig(
            question_count=8,
            expected_minutes=120,
            direct_ratio=0.25,
            prerequisite_ratio=0.75,
            transfer_ratio=0.0,
        ),
        actor_ref="teacher-1",
    )
    item = next(
        value
        for value in draft["students"][0]["items"]
        if value["question_id"] == 3
    )
    locked = recommendation_module.edit(
        draft["draft_id"],
        RecommendationEditCommand(
            request_token="5" * 32,
            expected_revision=1,
            action="lock",
            student_id="SYN-S01",
            item_id=item["item_id"],
            actor_ref="teacher-1",
            reason="这道题与课堂讲解一致",
        ),
    )
    repeated = recommendation_module.edit(
        draft["draft_id"],
        RecommendationEditCommand(
            request_token="5" * 32,
            expected_revision=1,
            action="lock",
            student_id="SYN-S01",
            item_id=item["item_id"],
            actor_ref="teacher-1",
            reason="这道题与课堂讲解一致",
        ),
    )
    assert repeated == locked
    assert locked["revision"] == 2
    assert next(
        value
        for value in locked["students"][0]["items"]
        if value["item_id"] == item["item_id"]
    )["locked"]

    with pytest.raises(RecommendationEditInvalid):
        recommendation_module.edit(
            draft["draft_id"],
            RecommendationEditCommand(
                request_token="6" * 32,
                expected_revision=2,
                action="exclude",
                student_id="SYN-S01",
                item_id=item["item_id"],
                actor_ref="teacher-1",
                reason="教师决定排除",
            ),
        )

    unlocked = recommendation_module.edit(
        draft["draft_id"],
        RecommendationEditCommand(
            request_token="7" * 32,
            expected_revision=2,
            action="unlock",
            student_id="SYN-S01",
            item_id=item["item_id"],
            actor_ref="teacher-1",
            reason="准备替换",
        ),
    )
    replaced = recommendation_module.edit(
        draft["draft_id"],
        RecommendationEditCommand(
            request_token="8" * 32,
            expected_revision=3,
            action="replace",
            student_id="SYN-S01",
            item_id=item["item_id"],
            actor_ref="teacher-1",
            reason="换成更熟悉的题面",
            replacement_question_id=7,
        ),
    )
    replacement = next(
        value
        for value in replaced["students"][0]["items"]
        if value["item_id"] == item["item_id"]
    )
    assert unlocked["revision"] == 3
    assert replaced["revision"] == 4
    assert replacement["question_id"] == 7
    assert replacement["replacement_history"] == [
        {
            "question_id": 3,
            "reason": "换成更熟悉的题面",
            "actor_ref": "teacher-1",
            "revision": 4,
        }
    ]
    assert replaced["history"][-1]["before_question_id"] == 3
    assert replaced["history"][-1]["after_question_id"] == 7

    with pytest.raises(RecommendationRevisionConflict):
        recommendation_module.edit(
            draft["draft_id"],
            RecommendationEditCommand(
                request_token="9" * 32,
                expected_revision=3,
                action="lock",
                student_id="SYN-S01",
                item_id=item["item_id"],
                actor_ref="teacher-1",
                reason="过期页面",
            ),
        )


def test_request_conflict_and_source_change_are_explicit(
    recommendation_module: PersonalizedRecommendationModule,
) -> None:
    draft = recommendation_module.create(
        request_token="a" * 32,
        diagnosis=_diagnosis(student_ids=("SYN-S01",)),
        config=PersonalizedRecommendationConfig(),
        actor_ref="teacher-1",
    )
    with pytest.raises(RecommendationRequestConflict):
        recommendation_module.create(
            request_token="a" * 32,
            diagnosis=_diagnosis(student_ids=("SYN-S02",)),
            config=PersonalizedRecommendationConfig(),
            actor_ref="teacher-1",
        )

    item = draft["students"][0]["items"][0]
    command = RecommendationEditCommand(
        request_token="b" * 32,
        expected_revision=1,
        action="lock",
        student_id="SYN-S01",
        item_id=item["item_id"],
        actor_ref="teacher-1",
        reason="锁定后安全重试",
    )
    locked = recommendation_module.edit(draft["draft_id"], command)

    with connect(recommendation_module.db_path) as connection:
        connection.execute(
            """
            UPDATE question_tags
            SET tag_value = '等式的性质'
            WHERE question_id = 1 AND tag_type = 'knowledge_point'
            """
        )
    assert recommendation_module.edit(draft["draft_id"], command) == locked
    with pytest.raises(RecommendationSourceChanged):
        recommendation_module.edit(
            draft["draft_id"],
            RecommendationEditCommand(
                request_token="c" * 32,
                expected_revision=2,
                action="lock",
                student_id="SYN-S01",
                item_id=item["item_id"],
                actor_ref="teacher-1",
                reason="来源已经变化",
            ),
        )


def test_concurrent_teacher_edits_allow_only_one_revision(
    recommendation_module: PersonalizedRecommendationModule,
) -> None:
    draft = recommendation_module.create(
        request_token="d" * 32,
        diagnosis=_diagnosis(student_ids=("SYN-S01",)),
        config=PersonalizedRecommendationConfig(),
        actor_ref="teacher-1",
    )
    item = draft["students"][0]["items"][0]

    def edit(token: str) -> str:
        try:
            recommendation_module.edit(
                draft["draft_id"],
                RecommendationEditCommand(
                    request_token=token,
                    expected_revision=1,
                    action="lock",
                    student_id="SYN-S01",
                    item_id=item["item_id"],
                    actor_ref="teacher-1",
                    reason="并发锁定",
                ),
            )
            return "applied"
        except RecommendationRevisionConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(
            executor.map(edit, ("e" * 32, "f" * 32))
        )

    assert sorted(results) == ["applied", "conflict"]
    assert recommendation_module.get(draft["draft_id"])["revision"] == 2


def test_isolated_and_cyclic_relation_data_stop_at_safe_one_hop(
    recommendation_module: PersonalizedRecommendationModule,
) -> None:
    isolated = recommendation_module.create(
        request_token="0" * 32,
        diagnosis=_diagnosis(student_ids=("SYN-S01",)),
        config=PersonalizedRecommendationConfig(
            target_keys=(LOCAL_ONE,),
        ),
        actor_ref="teacher-1",
    )["students"][0]
    assert isolated["targets"][0]["status"] == "missing"
    assert isolated["items"] == []
    assert isolated["shortages"]

    with connect(recommendation_module.db_path) as connection:
        _insert_relation(
            connection,
            "rel-synthetic-cycle",
            LOCAL_TWO,
            "kp_fun_linear",
            "prerequisite",
        )
    cyclic = recommendation_module.create(
        request_token="9" * 32,
        diagnosis=_diagnosis(student_ids=("SYN-S04",)),
        config=PersonalizedRecommendationConfig(),
        actor_ref="teacher-1",
    )["students"][0]
    question_ids = [item["question_id"] for item in cyclic["items"]]
    assert len(question_ids) == len(set(question_ids))
    assert all(
        item["relation"] is None
        or item["relation"]["relation_type"] in {"prerequisite", "related"}
        for item in cyclic["items"]
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
    )
    stable_keys = {
        1: LOCAL_ONE,
        2: LOCAL_TWO,
        3: "kp_alg_linear_equation",
        4: "kp_geo_triangle_congruence",
        5: "kp_geo_construction",
        6: "kp_alg_linear_equation",
        7: "kp_alg_linear_equation",
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

    loader = QuestionAnalysisInputLoader(
        db_path=db_path,
        data_root=data_root,
    )
    loaded = loader.load(tuple(item[0] for item in questions))
    with connect(db_path) as connection:
        for question in loaded:
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
        "exam_scope": {"mode": "current", "session_ids": []},
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
