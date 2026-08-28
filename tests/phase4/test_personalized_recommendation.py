from __future__ import annotations

import hashlib
import json
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
    _stage_targets_with_fallback,
    _textbook_leaf_index,
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
            and item["question_text"]
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


def test_quality_passed_unapproved_criterion_can_be_recommended(
    recommendation_module: PersonalizedRecommendationModule,
) -> None:
    with connect(recommendation_module.db_path) as connection:
        connection.execute(
            "UPDATE training_criterion_versions SET status = 'proposed'"
        )
        connection.execute(
            "UPDATE training_criterion_heads SET approved_version_id = NULL"
        )
    draft = recommendation_module.create(
        request_token="a" * 32,
        diagnosis=_diagnosis(student_ids=("SYN-S01",)),
        config=PersonalizedRecommendationConfig(
            question_count=8,
            expected_minutes=120,
            target_keys=("kp_alg_linear_equation",),
        ),
        actor_ref="teacher-1",
    )
    student = draft["students"][0]
    assert student["items"]
    assert all(item["criterion_version_id"] for item in student["items"])


def test_individual_scope_assigns_per_student_leaf_targets(
    recommendation_module: PersonalizedRecommendationModule,
) -> None:
    diagnosis = _diagnosis(student_ids=("SYN-S01", "SYN-S02"))
    diagnosis["knowledge_catalog"] = [
        {
            "knowledge_key": "kp_chapter_scope",
            "knowledge_point": "合成章",
            "parent_knowledge_key": None,
        },
        {
            "knowledge_key": "kp_alg_linear_equation",
            "knowledge_point": "一元一次方程",
            "parent_knowledge_key": "kp_chapter_scope",
        },
        {
            "knowledge_key": "kp_geo_triangle_congruence",
            "knowledge_point": "三角形全等",
            "parent_knowledge_key": "kp_chapter_scope",
        },
    ]
    draft = recommendation_module.create(
        request_token="b" * 32,
        diagnosis=diagnosis,
        config=PersonalizedRecommendationConfig(
            paper_mode="individual",
            question_count=8,
            expected_minutes=120,
            scope_keys=("kp_chapter_scope",),
        ),
        actor_ref="teacher-1",
    )
    by_student = {item["student_id"]: item for item in draft["students"]}
    first_keys = {
        str(item["stable_key"]) for item in by_student["SYN-S01"]["targets"]
    }
    second_keys = {
        str(item["stable_key"]) for item in by_student["SYN-S02"]["targets"]
    }
    assert "kp_alg_linear_equation" in first_keys
    assert "kp_geo_triangle_congruence" in second_keys
    assert first_keys != second_keys
    assert by_student["SYN-S01"]["items"]
    assert by_student["SYN-S02"]["items"]


def test_scope_without_evidence_does_not_invent_weakness(
    recommendation_module: PersonalizedRecommendationModule,
) -> None:
    diagnosis = _diagnosis(student_ids=("SYN-S05",))
    diagnosis["knowledge_catalog"] = [
        {
            "knowledge_key": "kp_chapter_scope",
            "knowledge_point": "合成章",
            "parent_knowledge_key": None,
        },
        {
            "knowledge_key": "kp_alg_linear_equation",
            "knowledge_point": "一元一次方程",
            "parent_knowledge_key": "kp_chapter_scope",
        },
    ]
    draft = recommendation_module.create(
        request_token="c" * 32,
        diagnosis=diagnosis,
        config=PersonalizedRecommendationConfig(
            paper_mode="individual",
            question_count=8,
            expected_minutes=120,
            scope_keys=("kp_chapter_scope",),
        ),
        actor_ref="teacher-1",
    )
    student = draft["students"][0]
    assert student["items"] == []
    assert student["selection_mode"] == "maintenance_fallback"
    assert any("未编造薄弱点" in warning for warning in student["warnings"])
    assert all("可练判定点" not in warning for warning in student["warnings"])
    assert all("请勾选纳入" not in warning for warning in student["warnings"])


def test_individual_scope_uses_diagnosis_mastery_without_session_times(
    recommendation_module: PersonalizedRecommendationModule,
) -> None:
    diagnosis = {
        "students": [
            {
                "student_id": "SYN-S01",
                "student_code": "S01",
                "student_name": "合成学生1",
                "class_id": "SYN-C01",
                "weak_points": [
                    {
                        "knowledge_key": "kp_alg_linear_equation",
                        "knowledge_point": "一元一次方程",
                        "mastery": 0.31,
                        "evidence_count": 2,
                        "source_question_refs": [],
                    }
                ],
            }
        ],
        "exam_scope": {"mode": "current", "session_ids": [1]},
        "knowledge_catalog": [
            {
                "knowledge_key": "kp_chapter_scope",
                "knowledge_point": "合成章",
                "parent_knowledge_key": None,
            },
            {
                "knowledge_key": "kp_alg_linear_equation",
                "knowledge_point": "一元一次方程",
                "parent_knowledge_key": "kp_chapter_scope",
            },
        ],
    }
    draft = recommendation_module.create(
        request_token="d" * 32,
        diagnosis=diagnosis,
        config=PersonalizedRecommendationConfig(
            paper_mode="individual",
            question_count=8,
            expected_minutes=120,
            scope_keys=("kp_chapter_scope",),
        ),
        actor_ref="teacher-1",
    )
    student = draft["students"][0]
    assert student["selection_mode"] == "mastery_targeted"
    assert any(
        str(item["stable_key"]) == "kp_alg_linear_equation"
        for item in student["targets"]
    )
    assert student["items"]


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
        in {
            "approved_candidate_shortage",
            "time_limit_reached",
            "stage_targets_empty",
        }
        for shortage in student["shortages"]
    )


def test_empty_stage_targets_report_honest_shortage_reason(
    recommendation_module: PersonalizedRecommendationModule,
) -> None:
    # 合成关系里一元一次方程只有先修关系，没有已确认的相关关系。
    draft = recommendation_module.create(
        request_token="7" * 32,
        diagnosis=_diagnosis(student_ids=("SYN-S01",)),
        config=PersonalizedRecommendationConfig(),
        actor_ref="teacher-1",
    )
    student = draft["students"][0]
    by_stage = {item["stage"]: item for item in student["shortages"]}

    assert by_stage["transfer"]["reason_code"] == "stage_targets_empty"
    assert by_stage["transfer"]["missing_count"] == 1
    assert any(
        "当前知识标准中没有这些细点已确认的相关关系，"
        "且同章内没有并列的可练内容" in warning
        for warning in student["warnings"]
    )

    # LOCAL_ONE 没有任何已确认的先修或相关关系，两个阶段都为空目标。
    isolated = recommendation_module.create(
        request_token="6" * 32,
        diagnosis=_diagnosis(student_ids=("SYN-S01",)),
        config=PersonalizedRecommendationConfig(
            target_keys=(LOCAL_ONE,),
        ),
        actor_ref="teacher-1",
    )["students"][0]
    isolated_by_stage = {
        item["stage"]: item for item in isolated["shortages"]
    }
    assert (
        isolated_by_stage["prerequisite"]["reason_code"]
        == "stage_targets_empty"
    )
    assert (
        isolated_by_stage["transfer"]["reason_code"]
        == "stage_targets_empty"
    )
    assert any(
        "当前知识标准中没有这些细点已确认的先修关系，"
        "且同章内没有更早的可练内容" in warning
        for warning in isolated["warnings"]
    )


def test_textbook_order_fallback_fills_relation_gaps_within_chapter(
    bnu24_recommendation_module: PersonalizedRecommendationModule,
) -> None:
    # 发布版没有这些细点的已确认先修/相关关系，按教材顺序在同章兜底。
    draft = bnu24_recommendation_module.create(
        request_token="b" * 32,
        diagnosis=_bnu24_diagnosis(),
        config=PersonalizedRecommendationConfig(
            question_count=8,
            expected_minutes=120,
            direct_ratio=0.5,
            prerequisite_ratio=0.25,
            transfer_ratio=0.25,
            target_keys=(BNU_TARGET,),
        ),
        actor_ref="teacher-1",
    )
    student = draft["students"][0]
    by_stage: dict[str, list[dict[str, object]]] = {
        "direct": [],
        "prerequisite": [],
        "transfer": [],
    }
    for item in student["items"]:
        by_stage[str(item["stage"])].append(item)

    assert [item["matched_key"] for item in by_stage["direct"]] == [
        BNU_TARGET
    ]
    assert {item["matched_key"] for item in by_stage["prerequisite"]} == {
        BNU_PREREQ_NEAR,
        BNU_PREREQ_EARLIER,
    }
    assert {item["matched_key"] for item in by_stage["transfer"]} == {
        BNU_TRANSFER_SIBLING,
        BNU_TRANSFER_OTHER,
    }

    # 兜底题沿用来源薄弱细点的 target 证据，不伪造关系，理由如实标注。
    for item in (*by_stage["prerequisite"], *by_stage["transfer"]):
        assert item["target"]["stable_key"] == BNU_TARGET
        assert item["relation"] is None
        assert "未经逐条教研确认" in str(item["reason"])
    assert all(
        str(item["reason"]).startswith("按教材编排顺序补强同章先学内容")
        for item in by_stage["prerequisite"]
    )
    assert all(
        str(item["reason"]).startswith("练习同章并列相关内容")
        for item in by_stage["transfer"]
    )
    assert "未经逐条教研确认" not in str(by_stage["direct"][0]["reason"])

    selected_ids = {int(item["question_id"]) for item in student["items"]}
    assert 16 not in selected_ids  # 跨章内容不进入兜底
    shortage_stages = {item["stage"] for item in student["shortages"]}
    assert "prerequisite" not in shortage_stages
    assert "transfer" not in shortage_stages


def test_confirmed_relations_always_beat_textbook_fallback() -> None:
    target_with_relations = f"{BNU_CHAPTER4}_2_2"
    target_without_relations = f"{BNU_CHAPTER4}_3_2"
    confirmed_prerequisite = f"{BNU_CHAPTER4}_1_1"
    confirmed_related = f"{BNU_CHAPTER4}_3_3"
    relations = {
        target_with_relations: (
            {
                "relation_type": "prerequisite",
                "source_key": target_with_relations,
                "target_key": confirmed_prerequisite,
            },
            {
                "relation_type": "related",
                "source_key": target_with_relations,
                "target_key": confirmed_related,
            },
        ),
    }
    textbook_index = _textbook_leaf_index(
        [
            confirmed_prerequisite,
            f"{BNU_CHAPTER4}_1_2",
            f"{BNU_CHAPTER4}_2_1",
            target_with_relations,
            f"{BNU_CHAPTER4}_2_3",
            f"{BNU_CHAPTER4}_3_1",
            target_without_relations,
            confirmed_related,
            f"{BNU_CHAPTER4}_4_1",
            BNU_OTHER_CHAPTER,
        ]
    )

    stage_targets, match_info = _stage_targets_with_fallback(
        [{"stable_key": target_with_relations}, {"stable_key": target_without_relations}],
        relations,
        textbook_index,
    )

    # 已确认关系推导的目标排在兜底之前。
    assert stage_targets["prerequisite"][0] == confirmed_prerequisite
    assert stage_targets["transfer"][0] == confirmed_related
    # 关系推导的 key 也记录 origin 与触发关系，且不是兜底。
    assert match_info[confirmed_prerequisite] == {
        "origin": target_with_relations,
        "relation": relations[target_with_relations][0],
        "fallback": False,
    }
    assert match_info[confirmed_related]["origin"] == target_with_relations
    assert match_info[confirmed_related]["fallback"] is False
    # 有已确认关系的目标不再加兜底，兜底只属于无关系的目标。
    fallbacks = {
        key: info for key, info in match_info.items() if info["fallback"]
    }
    assert {info["origin"] for info in fallbacks.values()} == {
        target_without_relations
    }
    # 同阶段已被已确认关系占位的 key 不会被兜底重复占用。
    assert confirmed_related not in fallbacks
    # 兜底不跨章。
    assert not any(
        key.startswith("kp_bnu24_math_g7_lower_5")
        for stage in ("prerequisite", "transfer")
        for key in stage_targets[stage]
    )
    assert not any(
        key.startswith("kp_bnu24_math_g7_lower_5") for key in match_info
    )


def test_ancestor_relation_expansion_orders_caps_and_gates_fallback() -> None:
    section = f"{BNU_CHAPTER4}_2"
    leaf = f"{section}_2"
    end_section = "kp_bnu24_math_g7_lower_1_1"
    parent_to_section = {
        "relation_type": "parent",
        "source_key": leaf,
        "target_key": section,
    }
    parent_to_chapter = {
        "relation_type": "parent",
        "source_key": section,
        "target_key": BNU_CHAPTER4,
    }
    section_prerequisite = {
        "relation_type": "prerequisite",
        "source_key": section,
        "target_key": end_section,
    }
    relations = {
        leaf: (parent_to_section,),
        section: (parent_to_chapter, section_prerequisite),
    }
    end_leaves = [f"{end_section}_{index}" for index in range(1, 9)]
    textbook_index = _textbook_leaf_index(
        [f"{section}_1", leaf, *end_leaves]
    )

    stage_targets, match_info = _stage_targets_with_fallback(
        [{"stable_key": leaf}],
        relations,
        textbook_index,
    )

    # 节级 confirmed 先修关系展开为端点节的后代细点：教材顺序升序、每关系封顶 6。
    assert stage_targets["prerequisite"] == tuple(end_leaves[:6])
    # 展开命中记录 origin 与触发关系，不是兜底。
    for key in end_leaves[:6]:
        assert match_info[key]["origin"] == leaf
        assert match_info[key]["relation"] is section_prerequisite
        assert match_info[key]["fallback"] is False
    # 有 confirmed 来源的阶段不再产生教材兜底（同章更早细点不进入）。
    assert f"{section}_1" not in stage_targets["prerequisite"]
    # 没有 confirmed related 的阶段仍走教材兜底。
    assert stage_targets["transfer"] == (f"{section}_1",)
    assert match_info[f"{section}_1"]["fallback"] is True


def test_stage_targets_empty_only_when_relations_and_fallback_both_empty(
    bnu24_recommendation_module: PersonalizedRecommendationModule,
) -> None:
    # 教材第一章第一叶子没有更早的同章内容，先修补强目标为空；
    # 同节兄弟可以兜底迁移目标，但题库没有对应题目，只报候选不足。
    draft = bnu24_recommendation_module.create(
        request_token="c" * 32,
        diagnosis=_bnu24_diagnosis(),
        config=PersonalizedRecommendationConfig(
            target_keys=(BNU_FIRST_LEAF,),
        ),
        actor_ref="teacher-1",
    )
    student = draft["students"][0]
    by_stage = {item["stage"]: item for item in student["shortages"]}

    assert by_stage["prerequisite"]["reason_code"] == "stage_targets_empty"
    assert any(
        "当前知识标准中没有这些细点已确认的先修关系，"
        "且同章内没有更早的可练内容" in warning
        for warning in student["warnings"]
    )
    assert (
        by_stage["transfer"]["reason_code"] == "approved_candidate_shortage"
    )
    assert by_stage["direct"]["reason_code"] == "approved_candidate_shortage"
    assert [int(item["question_id"]) for item in student["items"]] == [21]


def test_difficulty_aim_follows_student_mastery(
    bnu24_difficulty_module: PersonalizedRecommendationModule,
) -> None:
    # 同一细点三个难度候选：目标难度 = 下界 + 带宽 × 掌握度。
    draft = bnu24_difficulty_module.create(
        request_token="d" * 32,
        diagnosis=_bnu24_mastery_diagnosis(
            (("SYN-D01", 0.2), ("SYN-D02", 0.9), ("SYN-D03", None))
        ),
        config=PersonalizedRecommendationConfig(
            question_count=8,
            expected_minutes=120,
            direct_ratio=1.0,
            prerequisite_ratio=0.0,
            transfer_ratio=0.0,
            target_keys=(BNU_TARGET,),
        ),
        actor_ref="teacher-1",
    )
    first_direct = {
        student["student_id"]: student["items"][0]
        for student in draft["students"]
    }

    # 掌握度 0.2 → 目标难度 2.8，偏好难度 2；0.9 → 9.1，偏好难度 8。
    assert int(first_direct["SYN-D01"]["difficulty"]) == 2
    assert int(first_direct["SYN-D02"]["difficulty"]) == 8
    # 掌握度缺失 → 难度带中点 5.5，偏好难度 5。
    assert int(first_direct["SYN-D03"]["difficulty"]) == 5


def test_stage_offsets_rank_prerequisite_easier_and_transfer_harder(
    bnu24_difficulty_module: PersonalizedRecommendationModule,
) -> None:
    # 掌握度 0.5 → 基准难度 5.5；先修补强 −1.5，迁移应用 +1.0。
    draft = bnu24_difficulty_module.create(
        request_token="e" * 32,
        diagnosis=_bnu24_mastery_diagnosis((("SYN-D11", 0.5),)),
        config=PersonalizedRecommendationConfig(
            question_count=10,
            expected_minutes=120,
            direct_ratio=0.6,
            prerequisite_ratio=0.3,
            transfer_ratio=0.1,
            target_keys=(BNU_TARGET,),
        ),
        actor_ref="teacher-1",
    )
    first_by_stage: dict[str, dict[str, object]] = {}
    for item in draft["students"][0]["items"]:
        first_by_stage.setdefault(str(item["stage"]), item)

    # 先修补强（目标 4.0）选中难度 3，直接巩固（5.5）选中 5，
    # 迁移应用（6.5）选中 6：偏移方向体现在选题难度上。
    assert int(first_by_stage["prerequisite"]["difficulty"]) == 3
    assert int(first_by_stage["direct"]["difficulty"]) == 5
    assert int(first_by_stage["transfer"]["difficulty"]) == 6


def test_replace_keeps_the_same_stage_difficulty_aim(
    bnu24_difficulty_module: PersonalizedRecommendationModule,
) -> None:
    draft = bnu24_difficulty_module.create(
        request_token="f" * 32,
        diagnosis=_bnu24_mastery_diagnosis((("SYN-D21", 0.5),)),
        config=PersonalizedRecommendationConfig(
            question_count=8,
            expected_minutes=120,
            direct_ratio=0.5,
            prerequisite_ratio=0.25,
            transfer_ratio=0.25,
            target_keys=(BNU_TARGET,),
        ),
        actor_ref="teacher-1",
    )
    student = draft["students"][0]
    # 天花板：迁移瞄准 6.5，上限 8.5，只有难度 9 的题 46 不会入选；
    # 直接巩固瞄准 5.5，上限 7.5，难度 8 的题 33 不入选。
    selected = {int(item["question_id"]) for item in student["items"]}
    assert 46 not in selected and 33 not in selected
    # 迁移阶段 4_2_3 细点的首选是难度 6 的题 43（先修阶段已消耗 51/52）。
    transfer_item = next(
        item for item in student["items"] if int(item["question_id"]) == 43
    )

    replaced = bnu24_difficulty_module.edit(
        draft["draft_id"],
        RecommendationEditCommand(
            request_token="0123456789abcdef" * 2,
            expected_revision=1,
            action="replace",
            student_id="SYN-D21",
            item_id=transfer_item["item_id"],
            actor_ref="teacher-1",
            reason="换一道同目标题",
        ),
    )
    replacement = next(
        value
        for value in replaced["students"][0]["items"]
        if value["item_id"] == transfer_item["item_id"]
    )

    # 迁移阶段目标难度 6.5：44 已被补位占用，剩余候选中距瞄准最近的是
    # 难度 5 的题 42（与难度 8 的 45 同距，题号小者优先）。
    assert int(replacement["question_id"]) == 42
    assert replacement["reason"] == transfer_item["reason"]


def test_ancestor_level_confirmed_relations_expand_to_leaf_targets(
    bnu24_expansion_module: PersonalizedRecommendationModule,
) -> None:
    # 节级 confirmed 先修关系、章级 confirmed 相关关系都展开成细点目标。
    draft = bnu24_expansion_module.create(
        request_token="8" * 32,
        diagnosis=_bnu24_diagnosis(),
        config=PersonalizedRecommendationConfig(
            expected_minutes=120,
            target_keys=(BNU_TARGET,),
        ),
        actor_ref="teacher-1",
    )
    student = draft["students"][0]
    by_stage: dict[str, list[dict[str, object]]] = {
        "direct": [],
        "prerequisite": [],
        "transfer": [],
    }
    for item in student["items"]:
        by_stage[str(item["stage"])].append(item)

    # 节级先修关系展开为端点节的后代细点，按教材顺序选题。
    assert [item["matched_key"] for item in by_stage["prerequisite"]] == [
        "kp_bnu24_math_g7_lower_1_1_1",
        "kp_bnu24_math_g7_lower_1_1_2",
    ]
    # 章级相关关系展开为端点章的后代细点。
    assert [item["matched_key"] for item in by_stage["transfer"]] == [
        BNU_OTHER_CHAPTER
    ]
    # 展开命中属于已确认关系：沿用来源细点 target、附关系证据、
    # 使用 confirmed 文案而非兜底文案。
    for item in (*by_stage["prerequisite"], *by_stage["transfer"]):
        assert item["target"]["stable_key"] == BNU_TARGET
        assert item["relation"] is not None
        assert "未经逐条教研确认" not in str(item["reason"])
    prerequisite_relation = by_stage["prerequisite"][0]["relation"]
    assert prerequisite_relation["relation_type"] == "prerequisite"
    assert prerequisite_relation["rationale"] == "合成节级先修关系"
    assert str(by_stage["prerequisite"][0]["reason"]).startswith(
        "补强已确认的先修知识"
    )
    transfer_relation = by_stage["transfer"][0]["relation"]
    assert transfer_relation["relation_type"] == "related"
    assert transfer_relation["rationale"] == "合成章级相关关系"
    assert str(by_stage["transfer"][0]["reason"]).startswith(
        "练习与目标已确认相关的迁移知识"
    )
    # 有 confirmed 来源的阶段不再产生兜底：同章更早/并列细点不进入。
    assert all(
        not str(item["matched_key"]).startswith(BNU_CHAPTER4)
        for item in (*by_stage["prerequisite"], *by_stage["transfer"])
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
        if value["question_id"] == 8
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
            replacement_question_id=3,
        ),
    )
    replacement = next(
        value
        for value in replaced["students"][0]["items"]
        if value["item_id"] == item["item_id"]
    )
    assert unlocked["revision"] == 3
    assert replaced["revision"] == 4
    assert replacement["question_id"] == 3
    assert replacement["question_text"] == "解一元一次方程"
    assert replacement["replacement_history"] == [
        {
            "question_id": 8,
            "reason": "换成更熟悉的题面",
            "actor_ref": "teacher-1",
            "revision": 4,
        }
    ]
    assert replaced["history"][-1]["before_question_id"] == 8
    assert replaced["history"][-1]["after_question_id"] == 3

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


def test_curriculum_volume_bound_excludes_later_volume_candidates(
    bnu24_recommendation_module: PersonalizedRecommendationModule,
) -> None:
    module = bnu24_recommendation_module
    _seed_bnu24_g8_question(module.db_path, module.data_root)

    unbounded = module.create(
        request_token="e" * 32,
        diagnosis=_bnu24_diagnosis(),
        config=PersonalizedRecommendationConfig(
            question_count=8,
            expected_minutes=120,
            target_keys=(BNU_G8_LEAF,),
        ),
        actor_ref="teacher-1",
    )
    assert any(
        int(item["question_id"]) == BNU_G8_QUESTION
        for item in unbounded["students"][0]["items"]
    )

    bounded = module.create(
        request_token="f" * 32,
        diagnosis=_bnu24_diagnosis(),
        config=PersonalizedRecommendationConfig(
            question_count=8,
            expected_minutes=120,
            target_keys=(BNU_G8_LEAF,),
            curriculum_volume_id="bnu24-math-g7-lower",
        ),
        actor_ref="teacher-1",
    )
    student = bounded["students"][0]
    assert all(
        int(item["question_id"]) != BNU_G8_QUESTION
        for item in student["items"]
    )
    # 关系展开或教材兜底带入的册外目标一并移除，不会配出八上题。
    assert all(
        not str(item["matched_key"]).startswith("kp_bnu24_math_g8_")
        for item in student["items"]
    )
    direct_shortages = [
        item for item in student["shortages"] if item["stage"] == "direct"
    ]
    assert direct_shortages
    assert direct_shortages[0]["reason_code"] == "stage_targets_empty"


def test_curriculum_volume_bound_limits_maintenance_fallback(
    bnu24_recommendation_module: PersonalizedRecommendationModule,
) -> None:
    module = bnu24_recommendation_module
    _seed_bnu24_g8_question(module.db_path, module.data_root)
    config_kwargs: dict[str, object] = {
        "question_count": 8,
        "expected_minutes": 180,
        "direct_ratio": 1.0,
        "prerequisite_ratio": 0.0,
        "transfer_ratio": 0.0,
    }

    unbounded = module.create(
        request_token="0" * 32,
        diagnosis=_bnu24_diagnosis(),
        config=PersonalizedRecommendationConfig(**config_kwargs),
        actor_ref="teacher-1",
    )
    assert any(
        int(item["question_id"]) == BNU_G8_QUESTION
        for item in unbounded["students"][0]["items"]
    )

    bounded = module.create(
        request_token="9" * 32,
        diagnosis=_bnu24_diagnosis(),
        config=PersonalizedRecommendationConfig(
            **config_kwargs,
            curriculum_volume_id="bnu24-math-g7-lower",
        ),
        actor_ref="teacher-1",
    )
    student = bounded["students"][0]
    assert student["selection_mode"] == "maintenance_fallback"
    assert student["items"]
    assert all(
        int(item["question_id"]) != BNU_G8_QUESTION
        for item in student["items"]
    )


def test_direct_stage_interleaves_targets_and_caps_difficulty(
    bnu24_difficulty_module: PersonalizedRecommendationModule,
) -> None:
    config_kwargs: dict[str, object] = {
        "question_count": 8,
        "expected_minutes": 120,
        "direct_ratio": 1.0,
        "prerequisite_ratio": 0.0,
        "transfer_ratio": 0.0,
        "target_keys": (BNU_TARGET, BNU_TRANSFER_SIBLING),
    }
    draft = bnu24_difficulty_module.create(
        request_token="9" * 32,
        diagnosis=_bnu24_mastery_diagnosis((("SYN-D31", 0.5),)),
        config=PersonalizedRecommendationConfig(**config_kwargs),
        actor_ref="teacher-1",
    )
    items = draft["students"][0]["items"]
    matched = {str(item["matched_key"]) for item in items}
    # 分摊：两个细点轮流取题，不再被排名第一的细点独占。
    assert BNU_TARGET in matched and BNU_TRANSFER_SIBLING in matched
    # 天花板：瞄准 5.5，上限 7.5，难度 8 及以上的题（33/45/46）不入选。
    assert all(int(item["difficulty"]) <= 7 for item in items)

    weak_diagnosis = _bnu24_mastery_diagnosis((("SYN-D32", 0.2),))
    # 两个显式细点都带 0.2 掌握度证据。
    weak_diagnosis["students"][0]["weak_points"].append(
        {
            "knowledge_point": BNU_TRANSFER_SIBLING,
            "mastery": 0.2,
            "evidence_count": 2,
            "source_question_refs": [],
        }
    )
    weak = bnu24_difficulty_module.create(
        request_token="a" * 32,
        diagnosis=weak_diagnosis,
        config=PersonalizedRecommendationConfig(**config_kwargs),
        actor_ref="teacher-1",
    )
    weak_items = weak["students"][0]["items"]
    # 掌握度 0.2 → 瞄准 2.8，天花板 4.8：难度 2 的题 31 与难度 4 的题 41 合格，
    # 难度 5 及以上（32/42/43/44…）不入选；不够就如实报缺口。
    assert [int(item["question_id"]) for item in weak_items] == [31, 41]
    assert any(
        shortage["reason_code"] == "approved_candidate_shortage"
        for shortage in weak["students"][0]["shortages"]
    )


def test_same_leaf_same_question_type_prefers_diversity_then_fills(
    bnu24_difficulty_module: PersonalizedRecommendationModule,
) -> None:
    # BNU_TARGET 的三道候选全是填空题：先去重拿最接近瞄准值的一道，
    # 仍有缺口时允许同型补位，但补位排在其他细点候选之后。
    draft = bnu24_difficulty_module.create(
        request_token="6" * 32,
        diagnosis=_bnu24_mastery_diagnosis((("SYN-D41", 0.5),)),
        config=PersonalizedRecommendationConfig(
            question_count=8,
            expected_minutes=120,
            direct_ratio=1.0,
            prerequisite_ratio=0.0,
            transfer_ratio=0.0,
            target_keys=(BNU_TARGET, BNU_TRANSFER_SIBLING),
        ),
        actor_ref="teacher-1",
    )
    items = draft["students"][0]["items"]
    target_items = [
        int(item["question_id"])
        for item in items
        if item["matched_key"] == BNU_TARGET
    ]
    sibling_items = [
        int(item["question_id"])
        for item in items
        if item["matched_key"] == BNU_TRANSFER_SIBLING
    ]
    # 首选各细点最接近瞄准值的题（BNU_TARGET→32；TRANSFER_SIBLING 的
    # 42/43 与瞄准值同距，题号小者优先 → 42）。
    assert target_items[0] == 32
    assert sibling_items[0] == 42
    # 补位才出现同细点第二道同型题。
    assert target_items[1:] == [31]


def test_near_duplicate_questions_are_not_selected_twice(
    bnu24_difficulty_module: PersonalizedRecommendationModule,
) -> None:
    module = bnu24_difficulty_module
    # 71 与 32 题干逐字相同（跨试卷引用同一题）；72 知识点与方法标签都和
    # 32 重合（近重复）；73 只同知识点、方法不同（真正的另一道题）。
    _insert_bnu24_questions_with_skill_tags(
        module.db_path,
        (
            (71, "71", "填空题", "32. 全等三角形性质中档填空", "5", BNU_TARGET, ()),
            (72, "72", "填空题", "全等三角形性质中档变式填空", "5", BNU_TARGET, ("倍长中线法",)),
            (73, "73", "填空题", "全等三角形性质截长补短填空", "5", BNU_TARGET, ("截长补短法",)),
        ),
    )
    # 32 也标上方法标签，保证 72 与它在方法维上完全重合。
    with connect(module.db_path) as connection:
        connection.execute(
            "INSERT INTO question_tags (question_id, tag_type, tag_value, source)"
            " VALUES (32, 'method', '倍长中线法', 'synthetic')"
        )
    _approve_synthetic_criteria(module.db_path, module.data_root, (71, 72, 73))

    draft = module.create(
        request_token="b" * 32,
        diagnosis=_bnu24_mastery_diagnosis((("SYN-D51", 0.5),)),
        config=PersonalizedRecommendationConfig(
            question_count=8,
            expected_minutes=120,
            direct_ratio=1.0,
            prerequisite_ratio=0.0,
            transfer_ratio=0.0,
            target_keys=(BNU_TARGET,),
        ),
        actor_ref="teacher-1",
    )

    ids = [int(item["question_id"]) for item in draft["students"][0]["items"]]
    # 同一题一卷只出一次：71（逐字重复）与 72（知识+方法近重复）都被拦下；
    # 73 是同细点的另一道题，正常补位。
    assert ids[0] == 32
    assert 71 not in ids
    assert 72 not in ids
    assert 73 in ids


def test_high_mastery_shortfall_fills_harder_nearby_questions(
    bnu24_difficulty_module: PersonalizedRecommendationModule,
) -> None:
    config_kwargs: dict[str, object] = {
        "question_count": 8,
        "expected_minutes": 120,
        "direct_ratio": 1.0,
        "prerequisite_ratio": 0.0,
        "transfer_ratio": 0.0,
        "target_keys": (BNU_TARGET,),
    }
    strong = bnu24_difficulty_module.create(
        request_token="c" * 32,
        diagnosis=_bnu24_mastery_diagnosis((("SYN-D52", 0.8),)),
        config=PersonalizedRecommendationConfig(**config_kwargs),
        actor_ref="teacher-1",
    )
    student = strong["students"][0]
    ids = [int(item["question_id"]) for item in student["items"]]
    # 严格+软化先拿下本细点的 33/32/31；培优退路从同章近旁细点补到难度
    # 更高的 46（d9），同细点同题型的 45（d8）仍受去重约束，低于该生
    # 舒适难度的 41-44/51/52 不拿来凑数。
    assert ids[:3] == [33, 32, 31]
    assert 46 in ids
    assert 45 not in ids
    assert all(qid not in ids for qid in (41, 42, 43, 44, 51, 52))
    enrichment_item = next(
        item for item in student["items"] if int(item["question_id"]) == 46
    )
    assert enrichment_item["matched_key"] == BNU_TRANSFER_SIBLING
    assert "培优提升" in str(enrichment_item["reason"])
    # 仍配不满的部分如实报缺口：高分学生允许少于设定题量。
    shortage = next(
        item for item in student["shortages"] if item["stage"] == "direct"
    )
    assert shortage["reason_code"] == "approved_candidate_shortage"
    assert int(shortage["missing_count"]) == 8 - len(ids)

    # 低掌握度学生不触发培优：天花板保持严格，难题不硬塞。
    weak = bnu24_difficulty_module.create(
        request_token="d" * 32,
        diagnosis=_bnu24_mastery_diagnosis((("SYN-D53", 0.2),)),
        config=PersonalizedRecommendationConfig(**config_kwargs),
        actor_ref="teacher-1",
    )
    weak_items = weak["students"][0]["items"]
    assert [int(item["question_id"]) for item in weak_items] == [31]
    assert all("培优提升" not in str(item["reason"]) for item in weak_items)


def test_source_paper_level_bound_excludes_later_grade_papers(
    bnu24_recommendation_module: PersonalizedRecommendationModule,
) -> None:
    module = bnu24_recommendation_module
    _seed_g8_paper_with_g7_question(module.db_path, module.data_root)
    config_kwargs: dict[str, object] = {
        "question_count": 8,
        "expected_minutes": 120,
        "direct_ratio": 1.0,
        "prerequisite_ratio": 0.0,
        "transfer_ratio": 0.0,
        "target_keys": (BNU_TARGET,),
    }

    unbounded = module.create(
        request_token="7" * 32,
        diagnosis=_bnu24_diagnosis(),
        config=PersonalizedRecommendationConfig(**config_kwargs),
        actor_ref="teacher-1",
    )
    assert any(
        int(item["question_id"]) == 18
        for item in unbounded["students"][0]["items"]
    )

    bounded = module.create(
        request_token="5" * 32,
        diagnosis=_bnu24_diagnosis(),
        config=PersonalizedRecommendationConfig(
            **config_kwargs,
            curriculum_volume_id="bnu24-math-g7-lower",
        ),
        actor_ref="teacher-1",
    )
    bounded_ids = {
        int(item["question_id"]) for item in bounded["students"][0]["items"]
    }
    # 题 18 的知识点合规（七下全等），但来源卷是八年级上学期，被排除；
    # 没有年级信息的合成题源（paper 1）不受影响。
    assert 18 not in bounded_ids
    assert 11 in bounded_ids


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
        "INSERT INTO papers (id, title, import_status)"
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
                                    "score_awarded": 4,
                                    "full_score": 10,
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
