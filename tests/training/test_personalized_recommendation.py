from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
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
BNU_TARGET = "sk_bnu24_math_g7_lower_4_2_101"
BNU_TARGET_TOPIC = f"{BNU_CHAPTER4}_2_2"
BNU_PREREQ_NEAR = "sk_bnu24_math_g7_lower_4_1_101"
BNU_PREREQ_EARLIER = "sk_bnu24_math_g7_lower_4_1_102"
BNU_TRANSFER_SIBLING = "sk_bnu24_math_g7_lower_4_3_101"
BNU_TRANSFER_OTHER = "sk_bnu24_math_g7_lower_4_1_01"
BNU_OTHER_CHAPTER = "sk_bnu24_math_g7_lower_5_1_101"
BNU_FIRST_LEAF = "kp_bnu24_math_g7_upper_1_1_1"
BNU_G8_LEAF = "kp_bnu24_math_g8_upper_1_1_1"
BNU_G8_QUESTION = 17
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


def test_training_fit_experiment_keeps_shared_member_fit_and_weak_purpose():
    from tools.experiment_training_fit import weak_only_selection, member_metrics
    candidate = _selection_candidate(1, "合成关系判断题")
    def entry(sid, purpose):
        return {"candidate": candidate, "student_id": sid, "key": BNU_TARGET,
                "matched_key": BNU_TARGET, "selection_kind": "direct",
                "practice_purpose": purpose, "practice_role": "step_practice",
                "distance": 0., "preference": 0., "match_level": 1}
    a = entry("TEST-A", "remediation")
    b = entry("TEST-B", "consolidation")
    config = PersonalizedRecommendationConfig(paper_mode="shared", target_keys=(BNU_TARGET,))
    selected = weak_only_selection({"TEST-A": [a], "TEST-B": [b]}, config)
    assert len(selected) == 1
    assert member_metrics(selected, "TEST-A", [BNU_TARGET], 10)["coverage"] == 1
    assert member_metrics(selected, "TEST-A", [BNU_TARGET], 10)["fully_covered_need_count"] == 0
    assert member_metrics(selected, "TEST-B", [], 10)["missing_member_fit"] == 0
    assert member_metrics(selected, "TEST-B", [], 10)["remediation_questions"] == 0
    assert weak_only_selection({"TEST-A": [a], "TEST-B": []}, config) == []
    assert weak_only_selection({"TEST-A": [a], "TEST-B": [b]}, config,
        {"TEST-A": {}, "TEST-B": {BNU_TARGET: {}}}) == []
    assert weak_only_selection({"TEST-A": [entry("TEST-A", "new")]},
        PersonalizedRecommendationConfig()) == []


def test_training_fit_experiment_counts_shortage_and_does_not_export_identity():
    from tools.experiment_training_fit import member_metrics, summarize
    row = member_metrics([], "TEST-A", [BNU_TARGET], 10)
    result = summarize([row])
    assert result["students_without_remediation"] == 1
    assert result["mean_questions"] == 0
    assert result["complete_papers"] == 0
    assert result["covered_need_count"] == 0
    assert "TEST-A" not in json.dumps(result)
    assert member_metrics([], "TEST-B", [], 10)["coverage"] is None


def test_training_fit_evidence_views_preserve_scores_and_do_not_fill_missing_steps():
    from tools.experiment_training_fit import evidence_view
    rows = [
        {"session_id": 1, "score_awarded": 4, "full_score": 6,
         "teacher_final_revision": 2, "assessment": {"granularity": "whole_question",
         "reason": "teacher_final_without_step_attribution"}},
        {"session_id": 2, "score_awarded": 2, "full_score": 6,
         "assessment": {"granularity": "part", "eligible": True},
         "point_observations": [{"point_id": "synthetic-point", "achieved": 0}],
         "target_contributions": {BNU_TARGET: [0, 4]}},
        {"session_id": 2, "score_awarded": 0, "full_score": 6,
         "assessment": {"granularity": "part", "eligible": False},
         "point_observations": [{"point_id": "synthetic-point", "achieved": 0}]},
        {"session_id": 3, "score_awarded": 6, "full_score": 6,
         "assessment": {"granularity": "part", "eligible": True},
         "source_practice_metadata": {"is_single_result": True}},
    ]
    original = deepcopy(rows)
    coarse = evidence_view(rows, "coarsened_steps")
    assert [r["score_awarded"] for r in coarse] == [4, 2, 0, 6]
    assert coarse[0]["teacher_final_revision"] == 2
    assert coarse[2]["assessment"]["eligible"] is False
    assert all("point_observations" not in r and "target_contributions" not in r for r in coarse)
    assert coarse[1]["assessment"]["granularity"] == "whole_question"
    assert coarse[-1]["assessment"]["granularity"] == "part"
    assert evidence_view(rows, "effective_results_only") == [rows[1], rows[3]]
    assert evidence_view(rows, "steps_only") == [rows[1]]
    assert evidence_view(rows, "without_total_only") == rows[1:]
    assert evidence_view(rows, "without_earliest", 1) == rows[1:]
    assert rows == original
    with pytest.raises(ValueError):
        evidence_view(rows, "invent_steps")


def test_training_fit_selection_change_is_paired_and_anonymous():
    from tools.experiment_training_fit import selection_change
    result = selection_change({"TEST-A": {1, 2}, "TEST-B": {3}},
                              {"TEST-A": {2, 4}, "TEST-B": {3}})
    assert result == {"students": 2, "changed_students": 1, "removed_slots": 1, "added_slots": 1}
    assert "TEST-A" not in json.dumps(result)


def test_training_fit_match_audit_separates_level_and_recent_gates():
    from tools.experiment_training_fit import match_rejection
    entry = {"candidate": {"question_id": 1, "difficulty": 3.},
             "target": {"difficulty_plan": {"audit_minimum": 4., "audit_maximum": 7.}}}
    assert match_rejection(entry, {1}) == "below_student_window"
    entry["candidate"]["difficulty"] = 6.
    assert match_rejection(entry, {1}) == "recent_original"
    assert match_rejection(entry, set()) is None
    entry["candidate"]["difficulty"] = 7.5
    assert match_rejection(entry, set()) == "above_student_window"
    entry["candidate"]["difficulty"] = 9.
    assert match_rejection(entry, set()) == "paper_difficulty_ceiling"


def test_training_performance_comparison_rejects_changed_profile_sources(tmp_path):
    from tools.compare_training_endpoints import run_performance

    for label in ("baseline", "latest"):
        folder = tmp_path / label
        folder.mkdir()
        (folder / "diagnosis_profile_service.py").write_text(label, encoding="utf-8")
    # Refuse differing input pipelines before opening any real database.
    with pytest.raises(ValueError, match="identical diagnosis/projection"):
        run_performance(tmp_path)


def test_training_fit_unselected_audit_keeps_overlapping_paper_limits():
    from tools.experiment_training_fit import unselected_rejections
    def candidate(qid):
        return {"question_id": qid, "question_type": "解答题", "question_text": f"合成题 {qid}",
                "stable_keys": ["sk_test"], "difficulty": 4.}
    config = PersonalizedRecommendationConfig(max_written_questions=2)
    reasons = unselected_rejections(candidate(3), [candidate(1), candidate(2)], config)
    assert "same_skill_quota" in reasons and "written_quota" in reasons


def test_training_fit_experiment_opens_original_database_readonly(tmp_path):
    import sqlite3
    from tools.experiment_training_fit import readonly_runtime
    source = tmp_path / "test_training_fit.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE example (value INTEGER)")
        connection.execute("INSERT INTO example VALUES (7)")
    with readonly_runtime() as connect_ro:
        with connect_ro(source) as connection:
            assert connection.execute("SELECT value FROM example").fetchone()[0] == 7
            with pytest.raises(sqlite3.OperationalError):
                connection.execute("UPDATE example SET value=9")
        with pytest.raises(sqlite3.OperationalError):
            with sqlite3.connect(source) as connection:
                connection.execute("DELETE FROM example")
    with sqlite3.connect(source) as connection:
        assert connection.execute("SELECT value FROM example").fetchone()[0] == 7


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


def test_coarse_total_with_one_target_is_not_a_precise_loss():
    from question_bank.recommendation.personalized import _loss_refs
    ref = {"source_kind": "current_exam", "score_awarded": 2, "full_score": 5,
           "assessment": {"eligible": True, "granularity": "whole_question", "evidence_weight": 1}}
    assert _loss_refs({"source_question_refs": [ref]}) == []
    ref["assessment"]["granularity"] = "part"
    assert _loss_refs({"source_question_refs": [ref]}) == [ref]


def test_frozen_exam_sources_keep_each_exam_task_with_shared_part_cache():
    from question_bank.recommendation.personalized import _training_tasks

    module = object.__new__(PersonalizedRecommendationModule)
    def frozen(version, target):
        return {"bank_question_id": 900, "evidence_version_id": version,
            "evidence_part_id": "part1", "direct_keys": [BNU_TARGET],
            "target_facets": [{"part_id": "part1", "direct_keys": [BNU_TARGET]}],
            "direct_fine_terms": [], "practice_observations_by_key": {BNU_TARGET: [{
                "part_id": "part1", "response_mode": "short_answer_points",
                "evidence_points": [{"evidence_point_id": "p1", "target": target}]}]}}
    metadata = {900: {"direct_keys": [BNU_PREREQ_NEAR], "parts": [],
        "exam_sources": {"1": {"EX": frozen("old-a", "写出推理依据")},
                         "2": {"EX": frozen("old-b", "完成计算并验算")}}}}
    original = deepcopy(metadata)
    cache = {}
    for session, version, code in [(1, "old-a", "written_reasoning"), (2, "old-b", "calculation_check")]:
        ref = {"session_id": session, "question_id": "EX", "bank_question_id": 900,
               "score_awarded": 0, "full_score": 3, "source_kind": "current_exam",
               "assessment": {"granularity": "part", "part_id": "exam-part",
                   "evidence_part_id": "part1", "evidence_version_id": version,
                   "point_observations": [{"point_id": "p1", "achieved": 0}]}}
        before = deepcopy(ref)
        enriched = module._enrich_source_ref(ref, metadata, part_cache=cache)
        assert enriched["direct_keys"] == [BNU_TARGET]
        assert enriched["task_evidence_version_matches"] is True
        tasks = _training_tasks({"stable_key": BNU_TARGET, "source_question_refs": [enriched]})
        assert [(task["code"], task["basis"]) for task in tasks] == [(code, "observed_step")]
        borrowed = module._enrich_source_ref(ref, metadata, part_cache=cache, copy_fields=False)
        assert borrowed == enriched
        assert _training_tasks({"stable_key": BNU_TARGET, "source_question_refs": [borrowed]}) == tasks
        # Internal reads share frozen arrays, while the normal return stays isolated.
        assert borrowed["target_facets"] is metadata[900]["exam_sources"][str(session)]["EX"]["target_facets"]
        enriched["target_facets"].append({"part_id": "TEST-PRIVATE"})
        assert ref == before
    assert metadata == original


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
def recommendation_module(
    tmp_path: Path, question_bank_database
) -> PersonalizedRecommendationModule:
    db_path = tmp_path / "question_bank.db"
    data_root = tmp_path / "data"
    question_bank_database(db_path)
    _install_release_with_skills(db_path, revision=3, skill_parents=SK_PARENTS)
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
    question_bank_database(db_path, taxonomy_revision=7)
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
    question_bank_database(db_path, taxonomy_revision=7)
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
    """Install a rev7 release plus two synthetic ancestor-level relations."""
    release = load_release_for_taxonomy_revision(7)
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


def test_group_matching_reuses_quotas_and_preserves_complete_fresh_output(direct_module, monkeypatch):
    from dataclasses import replace
    import question_bank.recommendation.personalized as engine
    from integration.result_cache import ResultCache

    monkeypatch.setattr(engine, "_GROUP_MATCHING_CACHE", ResultCache(1, max_bytes=64 * 1024 * 1024))
    module = direct_module
    diagnosis = _direct_diagnosis((("A", .8, 900, BNU_TARGET), ("B", .8, 900, BNU_TARGET)))
    config = PersonalizedRecommendationConfig(
        scope_keys=(BNU_CHAPTER4,), group_scope_keys=(BNU_CHAPTER4,), paper_mode="shared",
        question_count=10, max_questions_per_skill=10, remediation_only=False,
    )
    evaluations, prepared = [], {}
    original = module.evaluate_candidates

    def evaluate(**kwargs):
        if kwargs["config"].paper_mode == "individual":
            evaluations.append(1)
            prepared.update({key: value for key, value in kwargs.items()
                             if key not in {"evaluation_memo", "_borrow_inputs"}})
        return original(**kwargs)

    monkeypatch.setattr(module, "evaluate_candidates", evaluate)
    initial = module.chapter_groups(diagnosis=diagnosis, config=config, graded_activities=[])
    assert initial["groups"] and initial["summary"]["grouped_student_count"] == 2

    def fresh_pools(**kwargs):
        return original(**kwargs, _borrow_inputs=True)["pools"]

    for settings in ({"question_count": 12}, {"max_questions_per_skill": 8}, {"max_written_questions": 0}):
        changed = replace(config, **settings)
        reused = module.chapter_groups(diagnosis=diagnosis, config=changed, graded_activities=[])
        assert len(evaluations) == 1
        with monkeypatch.context() as fresh:
            fresh.setattr(module, "_group_matching_pools", fresh_pools)
            expected = module.chapter_groups(diagnosis=diagnosis, config=changed, graded_activities=[])
        assert reused == expected
        # Neither a returned card nor an independent caller may alter stored matching.
        reused["groups"][0]["members"].clear()
        assert module.chapter_groups(diagnosis=diagnosis, config=changed, graded_activities=[]) == expected

    # Exercise the actual evaluator with changed contents, not an evaluator stub.
    unchanged = {key: value for key, value in prepared.items() if key != "config"}
    individual = prepared["config"]
    baseline_pools = module._group_matching_pools(**unchanged, config=individual, evaluation_memo={})
    for field in ("diagnosis", "mastery", "source_metadata", "candidates", "recent", "excluded", "difficulty"):
        inputs, settings = deepcopy(unchanged), individual
        if field == "diagnosis":
            inputs[field]["students"][0]["score_rate"] = .4
        elif field == "mastery":
            inputs[field][("A", BNU_TARGET)]["value"] = .01
        elif field == "source_metadata":
            inputs[field][900]["TEST_source_revision"] = "changed"
        elif field == "candidates":
            inputs[field][0]["question_text"] += " TEST-changed"
        elif field == "recent":
            inputs[field]["A"].add(100)
        elif field == "excluded":
            inputs[field].add(100)
        else:
            settings = replace(settings, difficulty_max=6)
        before = len(evaluations)
        reused = module._group_matching_pools(**inputs, config=settings, evaluation_memo={})
        assert len(evaluations) == before + 1, field
        assert reused == original(**inputs, config=settings, evaluation_memo={}, _borrow_inputs=True)["pools"], field
        before = len(evaluations)
        assert module._group_matching_pools(**unchanged, config=individual, evaluation_memo={}) == baseline_pools
        assert len(evaluations) == before + 1  # One-entry cache evicts the other input.

    # Live point links can change without changing the supplied metadata/candidate bytes.
    before = len(evaluations)
    with connect(module.db_path) as connection:
        connection.execute("UPDATE questions SET question_text=question_text || ' TEST-generation' WHERE id=900")
    module._group_matching_pools(**unchanged, config=individual, evaluation_memo={})
    assert len(evaluations) == before + 1
    before = len(evaluations)
    with monkeypatch.context() as knowledge:
        knowledge.setattr(module, "current_knowledge", deepcopy(module.current_knowledge))
        # The resolver uses a validated release; simulate its next immutable release identity.
        knowledge.setattr(module.current_knowledge, "content_hash", "TEST-new-knowledge-content")
        module._group_matching_pools(**unchanged, config=individual, evaluation_memo={})
    assert len(evaluations) == before + 1

    # Concurrent callers restore their own candidate identities and records.
    import threading
    cache = engine._GROUP_MATCHING_CACHE
    cache.clear()
    started, waiting, release = (threading.Event() for _ in range(3))
    computes = []

    def blocked_evaluate(**kwargs):
        computes.append(1)
        started.set()
        assert release.wait(5)
        return original(**kwargs)

    monkeypatch.setattr(module, "evaluate_candidates", blocked_evaluate)
    first_inputs, second_inputs = deepcopy(unchanged), deepcopy(unchanged)
    first_memo, second_memo = {}, {}
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(module._group_matching_pools, **first_inputs, config=individual,
                                evaluation_memo=first_memo)
        assert started.wait(5)
        flight = next(iter(cache._flights.values()))
        wait = flight.event.wait

        def observed_wait():
            waiting.set()
            return wait(5)

        monkeypatch.setattr(flight.event, "wait", observed_wait)
        second = executor.submit(module._group_matching_pools, **second_inputs, config=individual,
                                 evaluation_memo=second_memo)
        try:
            assert waiting.wait(5)
        finally:
            release.set()
        first_pools, second_pools = first.result(timeout=5), second.result(timeout=5)
    expected = original(**unchanged, config=individual, evaluation_memo={}, _borrow_inputs=True)["pools"]
    assert first_pools == second_pools == expected and len(computes) == 1
    for pools, inputs, memo in ((first_pools, first_inputs, first_memo), (second_pools, second_inputs, second_memo)):
        candidates = {candidate["question_id"]: candidate for candidate in inputs["candidates"]}
        assert all(entry["candidate"] is candidates[entry["candidate"]["question_id"]]
                   for entries in pools.values() for entry in entries)
        assert all(key[:2] == (id(inputs["candidates"]), id(inputs["source_metadata"]))
                   for key in memo["target_evaluations"])
    first_pools["A"].clear()
    assert second_pools == expected
    assert module._group_matching_pools(**unchanged, config=individual, evaluation_memo={}) == expected
    assert len(computes) == 1


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
    assert len(profile_hash_requests) > len(profile_serializations)
    assert len(profile_serializations) == len(set(profile_hash_requests))
    original_entries = direct_module._candidate_entries

    def entries_without_reuse(**kwargs):
        kwargs.update(source_part_cache=None, target_match_cache=None, source_links=None, eligibility_cache=None,
                      target_evaluation_cache=None, preference_cache=None, _input_hashes=None)
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
    # every automatic group. The source-version and request-token checks remain.
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
            "difficulty_max": 8,
            "max_questions_per_skill": 10,
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
    monkeypatch, candidates, diagnosis=None, shared=False, question_count=10,
    taxonomy_revision=7, scope_keys=(BNU_CHAPTER4,), **settings
):
    module = object.__new__(PersonalizedRecommendationModule)
    from question_bank.current_knowledge import CurrentKnowledgeResolver
    from question_bank.knowledge_graph_release.loader import (
        load_release_for_taxonomy_revision,
        load_taxonomy_catalog_for_release,
    )

    release = load_release_for_taxonomy_revision(taxonomy_revision)
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


@pytest.mark.parametrize("volume_id", ["", "bnu24-math-g7-upper", "bnu24-math-g7-lower"])
def test_v9_keeps_non_type_volume_skill_recommendations(monkeypatch, volume_id):
    key, chapter = BNU_TARGET, BNU_CHAPTER4
    if volume_id == "bnu24-math-g7-upper":
        from question_bank.current_knowledge import CurrentKnowledgeResolver
        from question_bank.knowledge_graph_release.loader import load_taxonomy_catalog_for_release
        from question_bank.recommendation.target_matching import target_index
        release = load_release_for_taxonomy_revision(7)
        resolver = CurrentKnowledgeResolver(release, load_taxonomy_catalog_for_release(release))
        key = next(node.stable_key for node in resolver.nodes if node.stable_key.startswith("sk_bnu24_math_g7_upper_"))
        chapter = target_index(resolver)[key]["chapter"]
    candidates = [_selection_candidate(1, "合成未转换教材册目标题", key)]
    diagnosis = _direct_diagnosis((("A", .8, 900, key),))
    settings = {"remediation_only": True, "scope_keys": (chapter,), "curriculum_volume_id": volume_id}
    before = _selection_draft(monkeypatch, candidates, diagnosis, taxonomy_revision=7, **settings)
    after = _selection_draft(monkeypatch, candidates, diagnosis, taxonomy_revision=11, **settings)
    assert [item["question_id"] for item in after["students"][0]["items"]] == [
        item["question_id"] for item in before["students"][0]["items"]]
    assert after["students"][0]["items"]
    assert all("题型" not in warning for warning in after["students"][0]["warnings"])
    assert all("need_stats" not in target for target in after["students"][0]["targets"])


@pytest.mark.parametrize("blocker", [None, "written", "similar"])
def test_personal_exchange_improves_coverage_with_original_paper_limits(blocker):
    from question_bank.recommendation.personalized import _choose_practice_entries
    a = _selection_candidate(1, "合成甲题", stable_keys=["sk_TEST_x"])
    b = _selection_candidate(2, "合成乙题", stable_keys=["sk_TEST_y"])
    replacement = _selection_candidate(3, "合成丙题", stable_keys=["sk_TEST_x"])
    if blocker == "written":
        replacement["question_type"] = "解答题"
    elif blocker == "similar":
        b["question_text"] = replacement["question_text"] = "求解这个合成三角形的指定边长并写出相同过程"
        b["stable_keys"].append("sk_TEST_similarity")
        replacement["stable_keys"].append("sk_TEST_similarity")
    entries = []
    for candidate, keys in ((a, ("a", "b", "c", "d")), (b, ("a", "b", "e")),
                            (replacement, ("f", "g", "h"))):
        entries.extend({"candidate": candidate, "student_id": "TEST-A", "key": key,
            "matched_key": key, "selection_kind": "direct", "practice_purpose": "remediation",
            "practice_role": "full_response", "distance": 0., "preference": 0., "match_level": 1}
            for key in keys)
    audit = []
    legacy = PersonalizedRecommendationConfig(max_written_questions=0)
    original = deepcopy(entries)
    assert [e["candidate"]["question_id"] for e, _ in _choose_practice_entries(entries, 10, legacy)] == [1, 2]
    selected = _choose_practice_entries(entries, 10,
        PersonalizedRecommendationConfig(remediation_only=True, max_written_questions=0), selection_audit=audit)
    assert entries == original
    if blocker:
        assert [e["candidate"]["question_id"] for e, _ in selected] == [1, 2]
        assert audit == []
    else:
        assert {e["candidate"]["question_id"] for e, _ in selected} == {2, 3}
        assert audit == [{"removed_question_id": 1, "added_question_id": 3,
            "added_target_keys": ["f", "g", "h"], "removed_target_keys": ["c", "d"],
            "covered_before": 5, "covered_after": 6, "full_response_before": 5, "full_response_after": 6}]
    shared = _choose_practice_entries(entries, 10,
        PersonalizedRecommendationConfig(paper_mode="shared", target_keys=(BNU_TARGET,), remediation_only=True))
    assert [e["candidate"]["question_id"] for e, _ in shared] == [1, 2]


@pytest.mark.parametrize("written_limit, similar", [(0, False), (2, False), (2, True)])
def test_two_question_exchange_escapes_single_exchange_skill_conflict(written_limit, similar):
    from question_bank.recommendation.personalized import _choose_practice_entries, _improve_personal_selection
    groups = {}
    for qid, skills, needs in ((1, ["sk_x", "sk_y"], "abcd"), (2, ["sk_z"], "efg"),
                              (3, ["sk_x"], "abcd"), (4, ["sk_y", "sk_z"], "efgh")):
        candidate = _selection_candidate(qid, f"合成不同任务{chr(64+qid)}", stable_keys=skills)
        if qid == 4 and written_limit == 0:
            candidate["question_type"] = "解答题"
        if similar and qid in (3, 4):
            candidate["question_text"] = "合成：选择适当方法求下列长度并写出过程"
            candidate["stable_keys"].append("kp_TEST_similarity")
        groups[qid] = [{"candidate": candidate, "student_id": "TEST", "key": key,
            "selection_kind": "direct", "practice_purpose": "remediation", "practice_role": "full_response",
            "distance": 0., "preference": 0., "match_level": 1} for key in needs]
    config = PersonalizedRecommendationConfig(max_written_questions=written_limit, remediation_only=True)
    start = [(groups[qid][0], groups[qid]) for qid in (1, 2)]
    original = deepcopy(groups)
    audit = []
    chosen = _improve_personal_selection(groups, start, 2, config, audit)
    expected = {1, 2} if written_limit == 0 or similar else {3, 4}
    assert {entry["candidate"]["question_id"] for entry, _ in chosen} == expected
    assert groups == original
    if audit:
        assert audit[0]["removed_question_ids"] == [1, 2]
        assert set(audit[0]["added_question_ids"]) == {3, 4}
        assert (audit[0]["covered_before"], audit[0]["covered_after"]) == (7, 8)
    selected = _choose_practice_entries([e for g in groups.values() for e in g], 2, config)
    assert {e["candidate"]["question_id"] for e, _ in selected} == expected


def test_task_need_ids_pair_each_loss_with_its_skill_once():
    from question_bank.recommendation.personalized import _task_need_ids, _choose_practice_entries
    def entry(qid, key):
        ref = {"full_score": 1, "score_awarded": 0}
        return {"candidate": _selection_candidate(qid, f"合成独立任务{qid}", stable_keys=["sk_budget"]),
            "student_id": "TEST", "key": key, "matched_key": key, "selection_kind": "direct",
            "practice_purpose": "remediation", "target": {"value": .5, "source_question_refs": [ref]},
            "distance": 0., "preference": 0., "match_level": 1}
    first, other = entry(1, "sk_skill"), entry(1, "sk_other")
    assert _task_need_ids(first) == frozenset({("TEST", "sk_skill")})
    assert _task_need_ids(first) != _task_need_ids(other)
    same_skill = {**deepcopy(first)}
    same_skill["target"] = {**first["target"], "source_question_refs": [
        {"full_score": 1, "score_awarded": 0, "question_id": "OTHER-QUESTION"}]}
    assert _task_need_ids(same_skill) == frozenset({("TEST", "sk_skill")})
    distinct = [entry(2, "sk_cube"), entry(2, "sk_area")]
    chosen = _choose_practice_entries([first, *distinct], 1, PersonalizedRecommendationConfig())
    assert chosen[0][0]["candidate"]["question_id"] == 2


def test_mastery_snapshot_cache_tracks_model_source_and_private_returns(direct_module, monkeypatch):
    import question_bank.recommendation.personalized as recommendation
    from question_bank.mastery.current import CurrentMastery
    from dataclasses import replace
    recommendation._MASTERY_SNAPSHOT_CACHE.clear()
    model = {("A", BNU_TARGET): CurrentMastery(BNU_TARGET, "TEST-skill", "available",
        .2, 2, 1., recommendation.CURRENT_MASTERY_PARAMETERS.version)}
    monkeypatch.setattr(recommendation.CurrentMasteryCalculator, "calculate", lambda *args: model)
    monkeypatch.setattr(recommendation.CurrentMasteryCalculator, "training_observations", lambda *args, **kwargs: {})
    prepared = direct_module._prepare_mastery_snapshot
    calls = []
    def counted(*args):
        calls.append(1)
        return prepared(*args)
    monkeypatch.setattr(direct_module, "_prepare_mastery_snapshot", counted)
    diagnosis = _direct_diagnosis()
    before = deepcopy(diagnosis)
    first = direct_module._mastery_snapshot(diagnosis)
    expected = deepcopy(first)
    first[("A", BNU_TARGET)]["source_question_refs"].append({"TEST": "private"})
    assert direct_module._mastery_snapshot(diagnosis) == expected
    assert len(calls) == 1 and diagnosis == before
    diagnosis["students"][0]["weak_points"][0]["source_question_refs"][0]["deduction_reason"] = "TEST changed note"
    direct_module._mastery_snapshot(diagnosis)
    assert len(calls) == 2
    model[("A", BNU_TARGET)] = replace(model[("A", BNU_TARGET)], value=.8)
    assert direct_module._mastery_snapshot(diagnosis)[("A", BNU_TARGET)]["value"] == .8
    assert len(calls) == 3
    with connect(direct_module.db_path) as conn:
        conn.execute("UPDATE questions SET question_text=question_text || ' TEST-CACHE-EDIT' WHERE id=900")
    direct_module._mastery_snapshot(diagnosis)
    assert len(calls) == 4


def test_latest_mastery_changes_priority_without_erasing_historical_loss(monkeypatch):
    from question_bank.recommendation.personalized import _choose_practice_entries, _loss_refs
    def entry(qid, key, mastery):
        return {"candidate": _selection_candidate(qid, f"合成相同名额任务{qid}", stable_keys=["sk_budget"]),
            "student_id": "TEST", "key": key, "matched_key": key, "selection_kind": "direct",
            "practice_purpose": "remediation", "target": {"value": mastery, "source_question_refs": [
                {"full_score": 1, "score_awarded": 0, "source_kind": "current_exam"}]},
            "distance": 0., "preference": 0., "match_level": 1}
    old, other = entry(1, "sk_old", .2), entry(2, "sk_other", .5)
    config = PersonalizedRecommendationConfig(remediation_only=True)
    assert _choose_practice_entries([old, other], 1, config)[0][0]["key"] == "sk_old"
    old["target"]["value"] = .9
    assert _choose_practice_entries([old, other], 1, config)[0][0]["key"] == "sk_other"
    assert len(_loss_refs(old["target"])) == 1
    assert _choose_practice_entries([old], 1, config)[0][0]["key"] == "sk_old"
    diagnosis = _direct_diagnosis()
    point = diagnosis["students"][0]["weak_points"][0]
    point["mastery"] = .2
    other_point = {**deepcopy(point), "knowledge_key": BNU_PREREQ_NEAR, "mastery": .5}
    diagnosis["students"][0]["weak_points"].append(other_point)
    candidates = [_selection_candidate(1, "合成旧失分目标"),
                  _selection_candidate(2, "合成另一失分目标", BNU_PREREQ_NEAR)]
    settings = dict(purpose="handout", question_count=1, max_written_questions=0, remediation_only=True)
    assert _selection_draft(monkeypatch, candidates, diagnosis, **settings)["students"][0]["items"][0]["question_id"] == 1
    point["mastery"] = .9
    assert _selection_draft(monkeypatch, candidates, diagnosis, **settings)["students"][0]["items"][0]["question_id"] == 2


@pytest.mark.parametrize("reason", ["part_total_without_step_attribution", "teacher_final_without_step_attribution"])
def test_coarse_loss_gets_bounded_independent_diagnostic_without_weakness_claim(monkeypatch, reason):
    diagnosis = _direct_diagnosis()
    point = diagnosis["students"][0]["weak_points"][0]
    point.update(mastery=.4, evidence_count=3)
    ref = point["source_question_refs"][0]
    ref["assessment"] = {"eligible": True, "granularity": "whole_question", "reason": reason}
    candidate = _selection_candidate(1, "合成独立短题", difficulty=3,
        target_facets=[{"part_id": "p", "direct_keys": [BNU_TARGET], "skill_keys": [BNU_TARGET],
                        "topic_keys": [BNU_TARGET_TOPIC], "section_keys": [f"{BNU_CHAPTER4}_2"],
                        "chapter_keys": [BNU_CHAPTER4]}],
        practice_observations_by_key={BNU_TARGET: [{"part_id": "p", "response_mode": "exact_objective", "observable": "求结果"}]})
    candidates = [candidate, {**deepcopy(candidate), "question_id": 2, "criterion_point_count": 2}]
    original = deepcopy(diagnosis)
    settings = dict(remediation_only=True, max_unmeasured_questions=1)
    student = _selection_draft(monkeypatch, candidates, diagnosis, **settings)["students"][0]
    assert len(student["items"]) == 1
    item = student["items"][0]
    assert item["question_id"] == 1 and item["practice_purpose"] == "new"
    assert item["target"]["diagnostic_check"] and "未定位具体失分环节" in item["reason"]
    assert "不认定为已知薄弱点" in item["reason"]
    assert diagnosis == original
    assert not _selection_draft(monkeypatch, candidates, diagnosis, remediation_only=True)["students"][0]["items"]
    historical = {**deepcopy(ref), "source_kind": "historical_exam"}
    point["source_question_refs"].append(historical)
    ref["score_awarded"] = ref["full_score"]
    assert not _selection_draft(monkeypatch, candidates, diagnosis, **settings)["students"][0]["items"]
    point["source_question_refs"].pop()
    ref["score_awarded"] = 4
    ref["assessment"]["eligible"] = False
    assert not _selection_draft(monkeypatch, candidates, diagnosis, **settings)["students"][0]["items"]
    ref["assessment"] = {"eligible": True, "granularity": "part"}
    ref["score_awarded"] = ref["full_score"]
    assert not _selection_draft(monkeypatch, candidates, diagnosis, **settings)["students"][0]["items"]


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


@pytest.mark.parametrize("source_mode,candidate_mode,full", [
    ("exact_objective", "exact_objective", True),
    ("exact_objective", "process_required", True),
    ("process_required", "exact_objective", False),
    ("process_required", "process_required", True),
    ("short_answer_points", "exact_objective", False),
    (None, "process_required", False),
])
def test_valid_objective_calculation_keeps_its_original_response_requirement(source_mode, candidate_mode, full):
    from question_bank.recommendation.personalized import _full_response_supported, _loss_practice_parts, _training_tasks
    text = "计算9的算术平方根为3"
    ref = {"source_kind": "current_exam", "full_score": 3, "score_awarded": 0,
        "task_evidence_version_matches": True, "assessment": {"granularity": "part", "eligible": True,
            "point_observations": [{"point_id": "point", "achieved": 0}]},
        "practice_observations_by_key": {BNU_TARGET: [{"response_mode": source_mode, "observable": text,
            "evidence_points": [{"evidence_point_id": "point", "target": text}]}]}}
    tasks = _training_tasks({"stable_key": BNU_TARGET, "source_question_refs": [ref]})
    calculation = next(task for task in tasks if task["code"] == "calculation_check")
    assert calculation["label"] == ("计算并核对结果" if source_mode == "exact_objective" else "列出计算步骤并核对结果")
    assert _full_response_supported({"response_mode": candidate_mode, "observable": "计算16的算术平方根为4"},
        tasks, _loss_practice_parts(ref, BNU_TARGET)) is full


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


@contextmanager
def _capture_initial_source_reads(monkeypatch, selected_ids, *, legacy=False, variable_limit=None):
    from question_bank.recommendation import personalized

    original_connect = personalized.connect
    reads, seen = [], set()
    selectors = {
        "SELECT q.id, q.question_number": "questions",
        "SELECT qt.question_id, qt.tag_value FROM": "knowledge_tags",
        "SELECT qt.question_id, qt.tag_type, qt.tag_value": "skill_tags",
        "SELECT * FROM question_part_difficulty_features WHERE is_active=1": "features",
        "SELECT question_id, criteria_json FROM training_criterion_versions": "criteria",
        "SELECT e.question_id, e.evidence_version_id": "evidence_versions",
    }

    class Rows:
        def __init__(self, rows):
            self.rows = rows

        def fetchall(self):
            return self.rows

    class Connection:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, sql, params=()):
            normalized = " ".join(sql.split())
            kind = next((kind for prefix, kind in selectors.items() if normalized.startswith(prefix)), None)
            if kind is None:
                return self.connection.execute(sql, params)
            supplied = tuple(params[:-1] if kind == "evidence_versions" else params)
            if legacy:
                if kind in seen:
                    return Rows([])
                seen.add(kind)
                sql = re.sub(r" AND (?:q\.id|qt\.question_id|question_id|e\.question_id) IN \([?,]+\)", "", sql)
                params = params[-1:] if kind == "evidence_versions" else ()
            rows = self.connection.execute(sql, params).fetchall()
            key = "id" if kind == "questions" else "question_id"
            reads.append({"kind": kind, "selected_ids": supplied, "row_count": len(rows),
                          "returned_ids": tuple(int(row[key]) for row in rows)})
            if legacy and kind == "questions" and selected_ids:
                rows = [row for row in rows if int(row["id"]) in selected_ids]
            return Rows(rows)

    @contextmanager
    def recording_connect(*args, **kwargs):
        with original_connect(*args, **kwargs) as connection:
            if variable_limit is not None:
                connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, variable_limit)
            yield Connection(connection)

    with monkeypatch.context() as patch:
        patch.setattr(personalized, "connect", recording_connect)
        yield reads


def _seed_targeted_snapshot_variants(module):
    from question_bank.services.standard_difficulty import _legacy_question_content_fingerprint
    from question_bank.training_criteria.analysis import solution_evidence_source_content_hash

    variants = _printed_original_variants(module.data_root)
    with connect(module.db_path) as conn:
        for qid, variant in ((100, "original"), (101, "missing")):
            row = variants[variant]
            conn.execute("UPDATE questions SET question_text=?,has_images=1,image_paths=? WHERE id=?",
                         (row["question_text"], json.dumps(row["image_paths"]), qid))
        conn.execute("UPDATE questions SET is_deleted=1 WHERE id=31")
        conn.execute("INSERT INTO papers(id,title,import_status) VALUES(2,'TEST-deleted-paper','deleted')")
        conn.execute("UPDATE questions SET paper_id=2 WHERE id=32")
        conn.execute("UPDATE questions SET question_text='TEST-如图但没有题图' WHERE id=33")
        conn.execute("UPDATE questions SET question_text=question_text || ' TEST-changed-source' WHERE id=104")
        conn.execute("INSERT INTO question_tags(question_id,tag_type,tag_value) VALUES(100,'method','TEST-first-method')")
        conn.execute("INSERT INTO question_tags(question_id,tag_type,tag_value) VALUES(100,'method','TEST-first-method')")
        conn.execute("INSERT INTO question_tags(question_id,tag_type,tag_value) VALUES(100,'model','TEST-model')")
        conn.execute("INSERT INTO question_tags(question_id,tag_type,tag_value) VALUES(100,'prerequisite',?)", (BNU_PREREQ_NEAR,))
        conn.execute("INSERT INTO knowledge_graph_releases(release_id,schema_version,taxonomy_revision,content_hash,payload_json,status,source_reference,created_by) "
                     "VALUES('kgr_TEST_targeted_old','knowledge-graph-release-v1',1,?,'{}','retired','TEST','TEST')", ('f' * 64,))
    inputs = {question.question_id: question for question in QuestionAnalysisInputLoader(
        db_path=module.db_path, data_root=module.data_root).load([100, 105, 106])}
    with connect(module.db_path) as conn:
        criterion = json.loads(conn.execute("SELECT criteria_json FROM training_criterion_versions WHERE question_id=100").fetchone()[0])
        criterion["source_content_hash"] = inputs[100].criterion_source_content_hash
        payload = json.dumps(criterion, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        conn.execute("UPDATE training_criterion_versions SET source_content_hash=?,criteria_json=?,criteria_hash=? WHERE question_id=100",
                     (inputs[100].criterion_source_content_hash, payload, hashlib.sha256(payload.encode()).hexdigest()))
        conn.execute("UPDATE training_criterion_heads SET current_source_hash=? WHERE question_id=100", (inputs[100].criterion_source_content_hash,))
        for qid in (100, 105, 106):
            row = dict(conn.execute("SELECT * FROM questions WHERE id=?", (qid,)).fetchone())
            conn.execute("INSERT INTO question_part_difficulty_features(question_id,part_id,features_json,formula_difficulty,formula_version,source_content_hash,is_active) "
                         "VALUES(?,'part1','{}',7,'std-difficulty-v1',?,1)", (qid, _legacy_question_content_fingerprint(row)))
        for qid in (105, 106):
            evidence = {"version_id": f"TEST-semantic-{qid}", "parts": [{"part_id": "part1", "label": "TEST-part",
                "response_mode": "exact_objective", "evidence_points": [{"evidence_point_id": "p1", "target": "TEST-compute",
                "observable_evidence": "TEST-result", "fine_term_links": []}]}]}
            evidence_ids = {}
            for label, graph_id, created_at in (("current", module.current_knowledge.release_id, "2026-07-30 08:00:00"),
                                                 ("historical", "kgr_TEST_targeted_old", "2026-07-30 08:00:01")):
                if qid == 106:
                    graph_id = module.current_knowledge.release_id
                version_id = hashlib.sha256(f"TEST-{qid}-{label}".encode()).hexdigest()
                evidence_ids[label] = version_id
                conn.execute("INSERT INTO question_solution_evidence_versions(evidence_version_id,question_id,source_content_hash,schema_version,content_hash,evidence_json,status,source_kind,source_reference,created_by,graph_release_id,created_at) "
                    "VALUES(?,?,?,'question-solution-evidence-v2',?,?,'approved','combined_model',?,'TEST',?,?)",
                    (version_id, qid, solution_evidence_source_content_hash(inputs[qid]), version_id,
                     json.dumps(evidence), f"TEST-{label}", graph_id, created_at))
                conn.execute("INSERT INTO evidence_point_knowledge_links(evidence_version_id,question_id,part_id,evidence_point_id,graph_release_id,role,term_id,stable_key,resolution_status,source_kind) "
                    "VALUES(?,?,'part1','p1',?,'direct',?,?,'resolved','link_job')",
                    (version_id, qid, module.current_knowledge.release_id,
                     BNU_TARGET if label == "current" else BNU_PREREQ_NEAR,
                     BNU_TARGET if label == "current" else BNU_PREREQ_NEAR))
            conn.execute("INSERT INTO question_scope_summary(question_id,evidence_version_id,primary_section_id,direct_section_ids_json) "
                         "VALUES(?,?,'','[]')", (qid, evidence_ids["historical" if qid == 105 else "current"]))
            criterion = json.loads(conn.execute("SELECT criteria_json FROM training_criterion_versions WHERE question_id=?", (qid,)).fetchone()[0])
            criterion["solution_evidence"] = evidence
            payload = json.dumps(criterion, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            conn.execute("UPDATE training_criterion_versions SET criteria_json=?,criteria_hash=? WHERE question_id=?",
                         (payload, hashlib.sha256(payload.encode()).hexdigest(), qid))
        template = conn.execute("SELECT * FROM training_criterion_versions WHERE question_id=100").fetchone()
        for qid in range(10000, 11000):
            _insert_bnu24_questions(conn, ((qid, str(qid), "填空题", f"TEST-outside-selection-{qid}", "7", BNU_TARGET),))
            conn.execute("INSERT INTO question_part_difficulty_features(question_id,part_id,features_json,formula_difficulty,formula_version,source_content_hash,is_active) "
                         "VALUES(?,'part1','{}',7,'std-difficulty-v1',?,1)", (qid, '0' * 64))
            conn.execute("INSERT INTO training_criterion_versions(version_id,question_id,version_number,source_content_hash,schema_version,status,source_kind,source_reference,criteria_json,criteria_hash,quality_status,created_by) "
                         "VALUES(?,?,1,?,'training-criteria-draft-v1','approved','teacher_manual','TEST',?,?,'passed','TEST')",
                         (hashlib.sha256(f"TEST-background-criterion-{qid}".encode()).hexdigest(), qid,
                          template["source_content_hash"], template["criteria_json"], template["criteria_hash"]))
            conn.execute("INSERT INTO question_solution_evidence_versions(evidence_version_id,question_id,source_content_hash,schema_version,content_hash,evidence_json,status,source_kind,source_reference,created_by,graph_release_id) "
                         "VALUES(?,?,?,'question-solution-evidence-v2',?,'{}','approved','combined_model','TEST','TEST',?)",
                         (hashlib.sha256(f"TEST-background-evidence-{qid}".encode()).hexdigest(), qid,
                          '0' * 64, '0' * 64, module.current_knowledge.release_id))


@pytest.mark.parametrize("knowledge_keys", [(), (BNU_TARGET,)])
def test_targeted_source_snapshot_matches_full_read_and_bounds_all_initial_tables(direct_module, monkeypatch, knowledge_keys):
    _seed_targeted_snapshot_variants(direct_module)
    ids = [*reversed(range(100, 112)), 100, 31, 32, 33, 99999]
    settings = dict(question_ids=ids, excluded_question_ids={103}, knowledge_keys=knowledge_keys,
                    candidate_config=PersonalizedRecommendationConfig(difficulty_min=1, difficulty_max=8))
    with _capture_initial_source_reads(monkeypatch, set(ids), legacy=True) as before:
        expected = direct_module._source_snapshot_uncached(**settings)
    with _capture_initial_source_reads(monkeypatch, set(ids)) as after:
        actual = direct_module._source_snapshot_uncached(**settings)
    assert actual == expected
    candidates, relations, version = actual
    assert relations and len(version) == 64
    by_id = {candidate["question_id"]: candidate for candidate in candidates}
    assert 100 in by_id and 105 in by_id
    assert not {31, 32, 33, 101, 103, 104, 99999}.intersection(by_id)
    assert by_id[100]["image_identity"] and by_id[100]["difficulty_features"]
    assert by_id[100]["practice_tags"]["method"].count("TEST-first-method") == 1
    assert by_id[105]["stable_keys"] == [BNU_TARGET]
    assert by_id[106]["stable_keys"] == [BNU_TARGET]
    assert by_id[105]["part_assessment"] is not None
    assert [candidate["question_id"] for candidate in candidates] == sorted(by_id)
    assert {read["kind"] for read in after} == {"questions", "knowledge_tags", "skill_tags", "features", "evidence_versions", *(["criteria"] if knowledge_keys else [])}
    assert all(set(read["returned_ids"]) <= set(ids) and len(read["selected_ids"]) <= 64 for read in after)
    assert all(read["row_count"] >= 1000 for read in before)
    assert all(read["row_count"] < 100 for read in after)


def test_targeted_snapshot_batches_without_limiting_ids_and_empty_ids_keep_full_range(direct_module, monkeypatch):
    ids = [*reversed(range(100, 112)), *range(20000, 20200), 100]
    with _capture_initial_source_reads(monkeypatch, set(ids), legacy=True) as before:
        expected = direct_module._source_snapshot_uncached(question_ids=ids)
    with _capture_initial_source_reads(monkeypatch, set(ids), variable_limit=65) as reads:
        actual = direct_module._source_snapshot_uncached(question_ids=ids)
    assert actual == expected
    question_reads = [read for read in reads if read["kind"] == "questions"]
    assert len(question_reads) == 4
    assert set(qid for read in question_reads for qid in read["selected_ids"]) == set(ids)
    assert all(len(read["selected_ids"]) <= 64 for read in reads)
    with _capture_initial_source_reads(monkeypatch, set(), legacy=True):
        expected_all = direct_module._source_snapshot_uncached()
    with _capture_initial_source_reads(monkeypatch, set()) as all_reads:
        actual_all = direct_module._source_snapshot_uncached(question_ids=[])
    assert actual_all == expected_all
    assert len(actual_all[0]) > len(actual[0])
    assert all(not read["selected_ids"] for read in all_reads)
    assert direct_module._source_snapshot_uncached(question_ids=[1 << 80]) == direct_module._source_snapshot_uncached(question_ids=[99999])


def test_source_snapshot_pool_survives_restart_and_invalidates(
    direct_module, monkeypatch,
):
    import pickle

    import integration.persistent_entries as persistent_entries
    from integration import data_generation
    from integration.diagnosis_profile_service import _PROFILE_CALCULATION_STATE
    from question_bank.recommendation import personalized

    def restart():
        personalized._SOURCE_SNAPSHOT_CACHE.clear()
        data_generation.reset_commit_generations()
        with persistent_entries._STORES_LOCK:
            persistent_entries._STORES.clear()

    first = direct_module._source_snapshot()
    pool_dir = direct_module.data_root / "cache" / "recommendation_pools"
    entries = list(pool_dir.glob("*.entry"))
    assert len(entries) == 1
    saved = pickle.loads(entries[0].read_bytes())
    assert pickle.loads(pickle.dumps(saved["payload"])) == saved["payload"]
    assert saved["payload"] == first

    # Restart: the snapshot reloads from disk without recomputing.
    restart()
    monkeypatch.setattr(
        direct_module, "_source_snapshot_uncached",
        lambda **kwargs: pytest.fail("pool snapshot recomputed after restart"),
    )
    assert direct_module._source_snapshot() == first
    monkeypatch.undo()

    calls = []
    original = direct_module._source_snapshot_uncached

    def tracked(**kwargs):
        calls.append(True)
        return original(**kwargs)

    monkeypatch.setattr(direct_module, "_source_snapshot_uncached", tracked)

    # A question-bank commit changes the content revision → recompute.
    restart()
    with connect(direct_module.db_path) as conn:
        conn.execute(
            "INSERT INTO training_tasks(task_code,status)"
            " VALUES('TEST-pool-invalidation','completed')")
    direct_module._source_snapshot()
    assert calls == [True]

    # A new rich-content asset changes the manifest → recompute.
    restart()
    asset = direct_module.data_root / "question_bank" / "rich_content"
    asset.mkdir(parents=True, exist_ok=True)
    (asset / "TEST-manifest-marker.json").write_text("{}", encoding="utf-8")
    direct_module._source_snapshot()
    assert calls == [True, True]

    # A code-state change also recomputes.
    restart()
    monkeypatch.setattr(
        "integration.diagnosis_profile_service._PROFILE_CALCULATION_STATE",
        (*_PROFILE_CALCULATION_STATE, ("TEST-code-change", 1, 1)),
    )
    direct_module._source_snapshot()
    assert calls == [True, True, True]
    monkeypatch.undo()
    monkeypatch.setattr(direct_module, "_source_snapshot_uncached", tracked)

    # question_ids lookups never persist.
    restart()
    before = set(pool_dir.glob("*.entry"))
    direct_module._source_snapshot(question_ids=[100])
    assert set(pool_dir.glob("*.entry")) == before


def test_adjustable_rules_and_legacy_defaults(direct_module):
    from question_bank.recommendation.personalized import (
        _config_constructor, _difficulty_plan, _paper_diversity_allowed, _hash_payload,
        _practice_reason_summary,
    )
    from backend.api.schemas.training import TrainingGroupingRequest, PersonalizedRecommendationCreateRequest
    from pydantic import ValidationError

    defaults = PersonalizedRecommendationConfig()
    assert _config_constructor({"question_count": 10})["remediation_only"] is False
    focused = PersonalizedRecommendationConfig(remediation_only=True)
    assert PersonalizedRecommendationConfig(**_config_constructor(focused.to_dict())) == focused
    assert _config_constructor({"question_count": 10})["max_unmeasured_questions"] == 0
    with_new = PersonalizedRecommendationConfig(remediation_only=True, max_unmeasured_questions=4)
    assert PersonalizedRecommendationConfig(**_config_constructor(with_new.to_dict())) == with_new
    with pytest.raises(ValueError):
        PersonalizedRecommendationConfig(max_unmeasured_questions=-1)
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
    unknown = {"stable_key": "sk_bnu24_math_g8_upper_2_1_106", "source_question_refs": []}
    refs = [{"session_id": i, "question_id": f"Q{i}", "score_awarded": 5, "full_score": 5,
        "question_difficulty": d, "assessment": {"granularity": "part"}}
        for i, d in enumerate([2.7] * 7 + [4.1, 4.5, 5.3])]
    profile = {"weak_points": [{"knowledge_key": "sk_bnu24_math_g8_upper_2_3_106", "source_question_refs": refs}]}
    control = _difficulty_plan({}, .923, 8, unknown, profile)
    assert control["aim"] == 2.7
    assert "model_based" not in control
    evidence_profile = {"weak_points": [{"knowledge_key": "sk_evidence", "evidence_count": 3}]}
    modeled = {**unknown, "logit_mean": 0.85, "difficulty_slope": 0.32}
    plan = _difficulty_plan({}, .923, 8, modeled, evidence_profile)
    assert plan["model_based"] is True
    assert (plan["logit_mean"], plan["difficulty_slope"]) == (0.85, 0.32)
    assert plan["minimum"] == 1.0 and plan["starter"] == pytest.approx(1.42, abs=0.01)
    assert plan["aim"] == plan["baseline_aim"] == pytest.approx(3.11, abs=0.01)
    assert plan["maximum"] == pytest.approx(4.49, abs=0.01)
    assert "本技能暂无直接作答，按所在节、章与整体表现估计" in plan["basis"]
    own_refs = {**modeled, "source_question_refs": [
        {**refs[0], "score_awarded": 1}, {**refs[1], "score_awarded": 0}]}
    assert "本技能 2 次有效作答" in _difficulty_plan({}, .923, 8, own_refs, evidence_profile)["basis"]
    low = _difficulty_plan({}, .923, 8, {**modeled, "logit_mean": -0.5}, evidence_profile)
    assert (low["minimum"], low["starter"], low["aim"], low["maximum"]) == (1.0, 1.0, 1.0, 2.0)
    assert "先安排最基础题" in low["basis"]
    high = _difficulty_plan({}, .923, 8, {**modeled, "logit_mean": 4.0}, evidence_profile)
    assert high["minimum"] == 7.0
    assert high["starter"] == high["aim"] == high["maximum"] == 8.0
    assert "已到出卷难度上限" in high["basis"]
    no_evidence = {"weak_points": [{"knowledge_key": "sk_evidence", "source_question_refs": refs[:1]}]}
    assert "model_based" not in _difficulty_plan({}, .923, 8, modeled, no_evidence)
    assert "model_based" not in _difficulty_plan({}, .923, 8, {**modeled, "difficulty_slope": 0}, evidence_profile)
    assert _difficulty_plan({}, None, 8, unknown, {})["maximum"] == 3
    reason = _practice_reason_summary([{
        "key": unknown["stable_key"], "matched_key": unknown["stable_key"],
        "selection_kind": "direct", "practice_purpose": "remediation",
        "candidate": {"difficulty": "3", "stable_names": {unknown["stable_key"]: "技能·示例"}},
        "target": {"difficulty_plan": plan}}])
    assert "按掌握度估计本题做对可能性约71%" in reason
    absent_score = {**unknown, "source_question_refs": [{**refs[0], "score_awarded": None}]}
    assert _difficulty_plan({}, .923, 8, absent_score, profile)["evidence_count"] == 0
    handout = PersonalizedRecommendationConfig(purpose="handout", question_count=100,
        max_questions_per_skill=20, max_written_questions=20, recent_activity_count=20, difficulty_max=10)
    assert PersonalizedRecommendationConfig(**_config_constructor(handout.to_dict())) == handout
    for cls, extra in ((TrainingGroupingRequest, {"scope_keys": [BNU_CHAPTER4]}),
        (PersonalizedRecommendationCreateRequest, {"request_token": "e" * 32,
         "scope": {"mode": "all"}, "exam_scope": {"mode": "semester"}})):
        body = cls(**extra, purpose="handout", question_count=100, max_questions_per_skill=20,
                   max_written_questions=20, recent_activity_count=20, difficulty_max=10)
        if cls is PersonalizedRecommendationCreateRequest:
            assert body.remediation_only is True
            assert body.max_unmeasured_questions == 4
        assert body.question_count == 100 and body.recent_activity_count == 20
        with pytest.raises(ValidationError):
            cls(**extra, purpose="training", question_count=100)
        with pytest.raises(ValidationError):
            cls(**extra, purpose="handout", question_count=3, max_written_questions=4)
    legacy = defaults.to_dict()
    for field in ("purpose", "max_questions_per_skill", "max_written_questions", "recent_activity_count", "max_unmeasured_questions"):
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
    # The old-format fingerprint only exists while the draft still matches the
    # original rule defaults, so this control keeps the default skill quota.
    control = _make_direct(direct_module, token="1", max_questions_per_skill=1)
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
                      if k not in {"purpose", "max_questions_per_skill", "max_written_questions", "max_unmeasured_questions"}}
        conn.execute("UPDATE personalized_recommendation_drafts SET input_fingerprint=? WHERE draft_id=?",
            (_hash_payload({"diagnosis": old_request["diagnosis"], "config": old_config}), control["draft_id"]))
        for column in ("request_json", "draft_json"):
            value = json.loads(conn.execute(f"SELECT {column} FROM personalized_recommendation_drafts WHERE draft_id=?",
                (control["draft_id"],)).fetchone()[0])
            for field in ("purpose", "max_questions_per_skill", "max_written_questions", "recent_activity_count", "max_unmeasured_questions"):
                value["config"].pop(field)
            value.pop("recent_question_ids", None)
            conn.execute(f"UPDATE personalized_recommendation_drafts SET {column}=? WHERE draft_id=?",
                (json.dumps(value), control["draft_id"]))
    assert _make_direct(direct_module, token="1", max_questions_per_skill=1)["draft_id"] == control["draft_id"]
    # Restored old input and current defaults select the same replacement.
    current = _make_direct(direct_module, token="3", max_questions_per_skill=1)
    item = next(i for i in control["students"][0]["items"] if i["question_id"] in range(100, 114))
    def replace(draft, token):
        return direct_module.edit(draft["draft_id"], RecommendationEditCommand(request_token=token * 32,
            expected_revision=1, action="replace", student_id="A", item_id=item["item_id"],
            actor_ref="synthetic", reason="旧草稿规则对照"))["students"][0]["items"]
    assert [i["question_id"] for i in replace(control, "4")] == [i["question_id"] for i in replace(current, "5")]


_QUALITY_STEMS = ("方程 全等 作图 函数 面积 平行 圆弧 统计 实数 根式 "
                  "勾股 相似 对称 旋转 概率 分式 不等式 菱形 梯形 中位数").split()


def _group_entry(sid, qid, key, purpose="remediation"):
    return {"candidate": {"question_id": qid, "difficulty": 3., "stable_keys": (key,),
                          "question_type": "填空题",
                          "question_text": f"{_QUALITY_STEMS[qid % len(_QUALITY_STEMS)]}专项练习"},
            "student_id": sid, "key": key, "matched_key": key,
            "selection_kind": "direct", "practice_purpose": purpose,
            "practice_role": "step_practice", "distance": 0., "preference": 0.,
            "match_level": 1,
            "target": {"mastery": .5, "difficulty_plan": {"aim": 3.}}}


def _group_need(aim, *keys):
    return {key: {"difficulty_plan": {"minimum": aim - 1., "maximum": aim + 1., "aim": aim}}
            for key in keys}


def test_quality_grouping_uses_shared_paper_coverage(monkeypatch):
    from question_bank.recommendation.personalized import _quality_group_members
    config = PersonalizedRecommendationConfig(question_count=8, max_questions_per_skill=8,
                                              max_written_questions=8, recent_activity_count=0)
    # Same need and a pool both members can share -> one group.
    needs = {"A": _group_need(3., "sk_1"), "B": _group_need(3., "sk_1")}
    pools = {"A": [_group_entry("A", qid, "sk_1") for qid in range(1, 9)],
             "B": [_group_entry("B", qid, "sk_1") for qid in range(1, 9)]}
    assert _quality_group_members(needs=needs, pools=pools, recent={}, config=config) == [("A", "B")]
    assert _quality_group_members(needs=needs, pools=pools, recent={"A": {1, 2, 3}}, config=config) == []
    assert _quality_group_members(needs=needs, pools=pools, recent={"A": {1, 2}, "B": {3}}, config=config) == []
    # Unrelated recent originals do not consume the common pool. Request
    # preparation may leave an empty row for a member with no eligible work.
    assert _quality_group_members(needs=needs, pools=pools, recent={"A": {999}}, config=config) == [("A", "B")]
    assert _quality_group_members(needs=needs, pools={"A": pools["A"], "B": []}, recent={}, config=config) == []

    # C's second skill is not shareable: joining would keep only half of its
    # personal coverage, so it stays unassigned.
    needs["C"] = _group_need(3., "sk_1", "sk_2")
    pools["C"] = ([_group_entry("C", qid, "sk_1") for qid in range(3, 11)]
                  + [_group_entry("C", qid, "sk_2") for qid in range(11, 19)])
    assert _quality_group_members(needs=needs, pools=pools, recent={}, config=config) == [("A", "B")]

    # Fewer than six shareable questions -> no group.
    sparse_needs = {"D": _group_need(3., "sk_1"), "E": _group_need(3., "sk_1")}
    sparse_pools = {"D": [_group_entry("D", qid, "sk_1") for qid in range(1, 6)],
                    "E": [_group_entry("E", qid, "sk_1") for qid in range(1, 9)]}
    assert _quality_group_members(needs=sparse_needs, pools=sparse_pools,
                                  recent={}, config=config) == []

    # A member whose personal paper covers no need is never grouped.
    weak_needs = {"F": _group_need(3., "sk_1"), "G": _group_need(3., "sk_1")}
    weak_pools = {"F": [_group_entry("F", qid, "sk_1", purpose="consolidation")
                        for qid in range(1, 9)],
                  "G": [_group_entry("G", qid, "sk_1") for qid in range(1, 9)]}
    assert _quality_group_members(needs=weak_needs, pools=weak_pools,
                                  recent={}, config=config) == []

    # The compatibility prefilter still blocks aim-gap, disjoint-window and
    # thin-overlap pairs even when every question is shareable.
    aim_gap = (_group_need(2., "sk_1"),
               {"sk_1": {"difficulty_plan": {"minimum": 3., "maximum": 6., "aim": 4.3}}})
    disjoint = ({"sk_1": {"difficulty_plan": {"minimum": 1., "maximum": 2., "aim": 1.5}}},
                {"sk_1": {"difficulty_plan": {"minimum": 3., "maximum": 4., "aim": 3.}}})
    thin = (_group_need(3., "sk_1", "sk_2", "sk_3", "sk_4"),
            _group_need(3., "sk_1", "sk_5", "sk_6", "sk_7"))
    for left, right in (aim_gap, disjoint, thin):
        pair_pools = {"H": [_group_entry("H", qid, key) for qid in range(1, 9) for key in left],
                      "I": [_group_entry("I", qid, key) for qid in range(1, 9) for key in right]}
        assert _quality_group_members(needs={"H": left, "I": right}, pools=pair_pools,
                                      recent={}, config=config) == []

    # Controlled selection outputs: a true individual paper covers four lost
    # targets, while the common paper covers two. v9 must refuse this pair.
    # v8 retains its existing one-member shared baseline.
    import question_bank.recommendation.personalized as engine
    for targets, expected in (
        (tuple(f"kp_bnu24_math_g8_upper_1_1_t{i:02d}" for i in range(1, 5)), []),
        (("sk_1", "sk_2", "sk_3", "sk_4"), [("A", "B")]),
    ):
        pair_needs = {sid: _group_need(3., *targets) for sid in ("A", "B")}
        pair_pools = {sid: [_group_entry(sid, qid, targets[(qid - 1) % 4])
                           for qid in range(1, 13)] for sid in ("A", "B")}

        def controlled_selection(entries, count, settings, **kwargs):
            rows = {}
            for entry in entries:
                if settings.paper_mode == "individual" or entry["key"] in targets[:2]:
                    rows.setdefault(entry["candidate"]["question_id"], []).append(entry)
            return [(group[0], group) for group in list(rows.values())[:count]]

        with monkeypatch.context() as selection:
            selection.setattr(engine, "_choose_practice_entries", controlled_selection)
            assert _quality_group_members(needs=pair_needs, pools=pair_pools,
                                          recent={}, config=config) == expected


def test_type_group_preview_rechecks_actual_paper_against_individual_coverage(monkeypatch):
    from question_bank.current_knowledge import CurrentKnowledgeResolver
    from question_bank.knowledge_graph_release.loader import load_taxonomy_catalog_for_release

    keys = tuple(f"kp_bnu24_math_g8_upper_1_1_t{i:02d}" for i in range(1, 5))
    release = load_release_for_taxonomy_revision(11)
    module = object.__new__(PersonalizedRecommendationModule)
    module.current_knowledge = CurrentKnowledgeResolver(release, load_taxonomy_catalog_for_release(release))
    module.clock = lambda: NOW
    profiles = [{"student_id": sid, "student_code": "", "student_name": f"合成学生{sid}",
                 "class_id": "TEST", "weak_points": []} for sid in ("A", "B")]
    needs = {sid: {key: {"knowledge_point": key, "evidence_count": 2, "mastery": .1,
                        "difficulty_plan": {"minimum": 2, "maximum": 4, "aim": 3}}
                   for key in keys} for sid in ("A", "B")}
    pools = {sid: [_group_entry(sid, qid, keys[(qid - 1) % 2]) for qid in range(1, 9)]
             for sid in ("A", "B")}
    for entries in pools.values():
        for entry in entries:
            entry["target"]["target_difficulty"] = 3
    monkeypatch.setattr(module, "evaluate_candidates", lambda **kwargs: {
        "pools": pools, "warnings": {sid: [] for sid in ("A", "B")}})
    summary = module._chapter_group_summary(
        diagnosis={"students": profiles}, members=("A", "B"), targets=keys, needs=needs,
        config=PersonalizedRecommendationConfig(paper_mode="shared", scope_keys=("kp_bnu24_math_g8_upper_1",),
            question_count=8, max_questions_per_skill=8), candidates=(), relations=(),
        recent={}, excluded=set(), source_version="TEST", metadata={}, mastery={},
        personal_coverage={sid: set(keys) for sid in ("A", "B")})
    assert summary["available_question_count"] == 8
    assert not summary["ready"]
    assert any("四分之三" in issue for issue in summary["issues"])


def test_no_source_direct_match_is_level_two_same_skill_label(direct_module):
    candidate = {"question_id": 9999, "difficulty": 3., "stable_keys": ("sk_synthetic_label",),
                 "question_type": "填空题", "question_text": "合成无来源标签题"}
    evaluated = direct_module.evaluate_candidates(
        diagnosis={"exam_scope": {"mode": "current", "session_ids": [1]},
                   "students": [{"student_id": "A", "student_name": "合成学生A",
                                 "class_id": "synthetic", "score_rate": .5, "weak_points": []}]},
        config=PersonalizedRecommendationConfig(target_keys=("sk_synthetic_label",),
                                                question_count=8, recent_activity_count=0),
        candidates=(candidate,), recent={"A": set()}, excluded=set())
    entry = evaluated["pools"]["A"][0]
    assert entry["match_level"] == 2
    assert entry["match_label"] == "同技能练习"


def test_candidate_difficulty_gate_skips_task_expansion_and_keeps_warning_counts(direct_module, monkeypatch):
    import question_bank.recommendation.personalized as recommendation

    diagnosis = _direct_diagnosis()
    config = PersonalizedRecommendationConfig(target_keys=(BNU_TARGET,), question_count=8)
    candidates, _, _ = direct_module._source_snapshot(candidate_config=config)
    accepted = deepcopy(next(candidate for candidate in candidates if candidate["question_id"] == 100))
    rejected = deepcopy(accepted)
    rejected.update(question_id=10000, difficulty=1.)
    for part in rejected["practice_observations_by_key"][BNU_TARGET]:
        part["TEST_too_easy"] = True
    expanded = []
    original_response = recommendation._full_response_supported

    def response(part, *args, **kwargs):
        assert not part.get("TEST_too_easy"), "expanded a question outside the student's difficulty range"
        expanded.append(1)
        return original_response(part, *args, **kwargs)

    monkeypatch.setattr(recommendation, "_full_response_supported", response)
    result = direct_module.evaluate_candidates(diagnosis=diagnosis, config=config,
        candidates=(rejected, accepted), recent={"A": set()}, excluded=set())
    assert {entry["candidate"]["question_id"] for entry in result["pools"]["A"]} == {100}
    assert expanded
    warning = next(value for value in result["warnings"]["A"] if "目标匹配后" in value)
    assert "目标匹配后 2 道" in warning
    assert "学生适合难度检查后 1 道" in warning


def test_failed_skill_plan_steps_back_or_caps_at_failed_difficulty():
    from question_bank.recommendation.personalized import _difficulty_plan, _practice_reason_summary

    def ref(difficulty, awarded=0):
        return {"session_id": 1, "question_id": f"Q{difficulty}", "full_score": 5,
                "score_awarded": awarded, "question_difficulty": difficulty,
                "assessment": {"granularity": "part"}}

    profile = {"weak_points": [{"knowledge_key": "sk_other", "evidence_count": 3}]}
    target = {"stable_key": "sk_weak", "logit_mean": -2.0, "difficulty_slope": 0.32,
              "source_question_refs": [ref(3.0)]}
    plan = _difficulty_plan({}, .5, 8, target, profile)
    assert plan["model_based"] is True
    assert "补弱难度围绕本人该技能失分题难度上下 1 级" in plan["basis"]
    assert plan["maximum"] == 2.0 and plan["minimum"] == 1.0
    reason = _practice_reason_summary([{
        "key": "sk_weak", "matched_key": "sk_weak", "selection_kind": "direct",
        "practice_purpose": "remediation",
        "candidate": {"difficulty": "2", "stable_names": {"sk_weak": "技能·示例"}},
        "target": {"difficulty_plan": plan}}])
    assert "按掌握度估计本题做对可能性约" in reason

    strong = {**target, "logit_mean": 1.65, "source_question_refs": [ref(4.0)]}
    plan = _difficulty_plan({}, .5, 8, strong, profile)
    assert plan["model_based"] is True
    assert plan["maximum"] == 5.0 and plan["aim"] >= 3.5 and plan["minimum"] == 2.0

    mid = {**target, "logit_mean": 0.76, "source_question_refs": [ref(4.0)]}
    plan = _difficulty_plan({}, .5, 8, mid, profile)
    assert plan["maximum"] == pytest.approx(4.2, abs=0.01)

    correct_only = {**target, "source_question_refs": [ref(3.0, 5), ref(3.4, 5)]}
    plan = _difficulty_plan({}, .5, 8, correct_only, profile)
    assert plan["model_based"] is True
    assert "先安排最基础题" in plan["basis"]
    plan = _difficulty_plan({}, .5, 8, {**correct_only, "logit_mean": 0.85}, profile)
    assert plan["maximum"] == pytest.approx(4.49, abs=0.01)


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
            # Reuse a broader individual evaluation, then apply the group's
            # recent-original union and a smaller target list. All warning
            # counts and entry order must equal a fresh shared evaluation.
            from dataclasses import replace
            candidates, _, _ = direct_module._source_snapshot(
                knowledge_keys=direct_module._candidate_scope(diagnosis, config), candidate_config=config)
            prepared = dict(diagnosis=diagnosis, candidates=candidates,
                            mastery=direct_module._mastery_snapshot(diagnosis),
                            source_metadata=direct_module._source_practice_metadata(diagnosis),
                            recent={sid: set(ids) for sid, ids in request["recent_question_ids"].items()})
            memo = {}
            direct_module.evaluate_candidates(**prepared, config=replace(config, paper_mode="individual"),
                                               evaluation_memo=memo)
            restricted = replace(config, target_keys=(BNU_TARGET,))
            reused = direct_module.evaluate_candidates(**prepared, config=restricted, evaluation_memo=memo)
            fresh = direct_module.evaluate_candidates(**prepared, config=restricted)
            assert reused == fresh
            before = deepcopy(prepared)
            borrowed = direct_module.evaluate_candidates(**prepared, config=restricted, _borrow_inputs=True)
            assert borrowed == fresh
            assert prepared == before
            # The private grouping calculation stores target evidence once;
            # ordinary callers continue to receive separate target mappings.
            direct_rows = [e for e in borrowed["pools"]["A"] if e["selection_kind"] == "direct"]
            assert len(direct_rows) > 1
            assert all(e["target"] is direct_rows[0]["target"] for e in direct_rows)
            public_rows = [e for e in fresh["pools"]["A"] if e["selection_kind"] == "direct"]
            assert len({id(e["target"]) for e in public_rows}) == len(public_rows)
            fresh["targets"]["A"][0]["source_question_refs"].append({"TEST": "private-return"})
            assert prepared == before
            # A growing eligible pool must recalculate rather than reuse an
            # incomplete pool from a previous exclusion set.
            expanded = {**prepared, "recent": {"A": set(), "B": set()}}
            assert direct_module.evaluate_candidates(**expanded, config=restricted, evaluation_memo=memo) == (
                direct_module.evaluate_candidates(**expanded, config=restricted))
            # A caller-owned evaluation memo is reusable with mutable inputs.
            # It must never inherit the grouping call's identity fingerprints.
            changing = deepcopy(prepared)
            mutable_memo = {}
            earlier = direct_module.evaluate_candidates(**changing, config=restricted, evaluation_memo=mutable_memo)
            changing["mastery"][("A", BNU_TARGET)]["value"] = .01
            changed = direct_module.evaluate_candidates(**changing, config=restricted, evaluation_memo=mutable_memo)
            assert changed == direct_module.evaluate_candidates(**changing, config=restricted)
            assert changed != earlier
            assert "group_input_hashes" not in mutable_memo

    # A later marked activity cannot change either stored exclusion snapshot.
    _record_legacy_training(direct_module, (1001,), name="SYN-LATER", occurred_at="2026-07-31")
    with connect(direct_module.db_path) as conn:
        conn.execute("UPDATE questions SET updated_at='2099-01-01 00:00:00'")
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
                         max_questions_per_skill=8, max_written_questions=8,
                         remediation_only=True, max_unmeasured_questions=2)
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


def test_remediation_handout_skips_empty_students_and_preserves_saved_draft(direct_module, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from backend.jobs import training_handout
    diagnosis = _direct_diagnosis((("A", .9, 900, BNU_TARGET), ("B", .9, 900, BNU_TARGET)))
    diagnosis["students"][1]["weak_points"][0]["source_question_refs"][0]["score_awarded"] = 5
    draft = direct_module.create(request_token="2" * 32, diagnosis=diagnosis,
        config=PersonalizedRecommendationConfig(purpose="handout", remediation_only=True,
            difficulty_max=10, scope_keys=(BNU_CHAPTER4,)), actor_ref="synthetic")
    assert draft["students"][0]["items"] and not draft["students"][1]["items"]
    assert direct_module.get(draft["draft_id"])["config"]["remediation_only"] is True
    monkeypatch.setattr(training_handout, "PersonalizedRecommendationModule", lambda **_: direct_module)
    exported = []
    def export(_db, ids, destination, **options):
        exported.append((ids, options))
        path = destination / "TEST-placeholder.docx"
        path.write_bytes(b"synthetic export placeholder")
        return path
    monkeypatch.setattr(training_handout, "export_question_paper_docx", export)
    context = SimpleNamespace(payload={"draft_id": draft["draft_id"], "expected_revision": 1},
        job_id="TEST-remediation-export", raise_if_cancelled=lambda: None, report=lambda *_: None)
    result = training_handout.run_training_handout_export(context=context,
        question_bank_db_path=direct_module.db_path, data_root=direct_module.data_root, reports_dir=tmp_path / "reports")
    assert result["paper_count"] == 1 and result["skipped_student_count"] == 1
    assert len(exported) == 1 and result["question_count"] == len(draft["students"][0]["items"])
    assert exported[0][1]['sections'][0].title == '未归入章节'
    assert exported[0][1]['question_notes'] is None
    assert direct_module.get(draft["draft_id"]) == draft
    rejected = deepcopy(draft)
    rejected["config"]["remediation_only"] = False
    monkeypatch.setattr(direct_module, "ensure_current", lambda _: rejected)
    with pytest.raises(ValueError, match="空卷"):
        training_handout.checked_handout_draft(direct_module, draft["draft_id"], 1)
    rejected["config"]["remediation_only"] = True
    rejected["students"][0]["items"] = []
    with pytest.raises(ValueError, match="没有可导出"):
        training_handout.checked_handout_draft(direct_module, draft["draft_id"], 1)
    legacy = deepcopy(draft)
    for student in legacy['students']:
        for item in student['items']:
            item.pop('knowledge_section', None)
            item.pop('primary_skill_name', None)
    monkeypatch.setattr(direct_module, 'ensure_current', lambda _: legacy)
    training_handout.run_training_handout_export(context=context,
        question_bank_db_path=direct_module.db_path, data_root=direct_module.data_root, reports_dir=tmp_path / 'legacy')
    assert exported[-1][1]['sections'] is None and exported[-1][1]['question_notes'] is None


def test_student_shared_scope_without_target_keys_creates_same_paper(direct_module):
    diagnosis = _direct_diagnosis((("A", .8, 900, BNU_TARGET), ("B", .8, 900, BNU_TARGET)))
    draft = direct_module.create(request_token='6' * 32, diagnosis=diagnosis,
        config=PersonalizedRecommendationConfig(paper_mode='shared', scope_keys=(BNU_CHAPTER4,),
            target_keys=(), question_count=8), actor_ref='synthetic')
    assert draft['config']['target_keys'] == []
    first, second = [student['items'] for student in draft['students']]
    assert first and [item['question_id'] for item in first] == [item['question_id'] for item in second]
    assert all(item['beneficiary_student_ids'] == ['A', 'B'] for item in first)


def test_handout_consolidation_cap_zero_training_and_replacement(direct_module):
    diagnosis = _direct_diagnosis()
    diagnosis['students'][0]['weak_points'][0]['source_question_refs'][0]['score_awarded'] = 5
    settings = dict(remediation_only=True, scope_keys=(BNU_CHAPTER4,), question_count=4,
                    max_questions_per_skill=4, max_written_questions=4, recent_activity_count=0)
    control = direct_module.create(request_token='7' * 32, diagnosis=diagnosis,
        config=PersonalizedRecommendationConfig(purpose='handout', **settings), actor_ref='synthetic')
    assert not control['students'][0]['items']
    training = direct_module.create(request_token='8' * 32, diagnosis=diagnosis,
        config=PersonalizedRecommendationConfig(**{**settings, 'question_count': 8, 'max_questions_per_skill': 1,
            'max_written_questions': 2}, max_consolidation_questions=2), actor_ref='synthetic')
    assert not training['students'][0]['items']
    draft = direct_module.create(request_token='9' * 32, diagnosis=diagnosis,
        config=PersonalizedRecommendationConfig(purpose='handout', max_consolidation_questions=2, **settings), actor_ref='synthetic')
    items = draft['students'][0]['items']
    assert 0 < len(items) <= 2 and all(item['practice_purpose'] == 'consolidation' for item in items)
    assert draft['students'][0]['shortages'][0]['missing_count'] == 4 - len(items)
    replaced = direct_module.edit(draft['draft_id'], RecommendationEditCommand(request_token='a1' * 16,
        expected_revision=1, action='replace', student_id='A', item_id=items[0]['item_id'],
        actor_ref='synthetic', reason='TEST巩固题替换'))
    assert len(replaced['students'][0]['items']) == len(items)
    assert all(item['practice_purpose'] == 'consolidation' for item in replaced['students'][0]['items'])


def test_handout_adds_consolidation_after_remediation_and_new_under_paper_limits():
    from question_bank.recommendation.personalized import _choose_practice_entries
    entries = []
    for qid, purpose, key in [(1, 'consolidation', 'sk_test_con'), (2, 'new', 'sk_test_new'),
                              (3, 'remediation', 'sk_test_loss'), (4, 'consolidation', 'sk_test_extra')]:
        candidate = _selection_candidate(qid, f'TEST不同练习{qid}', key=key)
        entries.append({'candidate': candidate, 'key': key, 'matched_key': key, 'student_id': 'TEST',
            'selection_kind': 'direct', 'practice_purpose': purpose, 'distance': 0., 'preference': 0., 'match_level': 1})
    settings = dict(purpose='handout', remediation_only=True, question_count=5, max_unmeasured_questions=1)
    zero = _choose_practice_entries(entries, 5, PersonalizedRecommendationConfig(**settings))
    assert [entry['practice_purpose'] for entry, _ in zero] == ['remediation', 'new']
    selected = _choose_practice_entries(entries, 5,
        PersonalizedRecommendationConfig(**settings, max_consolidation_questions=1))
    assert [entry['practice_purpose'] for entry, _ in selected] == ['remediation', 'new', 'consolidation']
    # A consolidation question still consumes the whole-paper direct skill quota.
    entries[0]['candidate']['stable_keys'] = ['sk_test_loss']
    entries[-1]['candidate']['question_type'] = '解答题'
    blocked = _choose_practice_entries(entries, 5,
        PersonalizedRecommendationConfig(**settings, max_consolidation_questions=2, max_written_questions=0))
    assert [entry['practice_purpose'] for entry, _ in blocked] == ['remediation', 'new']


@pytest.mark.parametrize('omit_new,legacy', [(False, False), (True, False), (False, True), (True, True)])
def test_zero_consolidation_retries_every_old_request_fingerprint(direct_module, omit_new, legacy):
    from question_bank.recommendation.personalized import _hash_payload
    # The legacy fingerprint shape only exists while the config still matches
    # the original defaults, so this scenario keeps the default skill quota.
    draft = _make_direct(direct_module, token='b', max_questions_per_skill=1)
    with connect(direct_module.db_path) as conn:
        request = json.loads(conn.execute('SELECT request_json FROM personalized_recommendation_drafts WHERE draft_id=?',
            (draft['draft_id'],)).fetchone()[0])
        config = dict(request['config'])
        config.pop('max_consolidation_questions')
        if omit_new:
            config.pop('max_unmeasured_questions')
        if legacy:
            for key in ('purpose', 'max_questions_per_skill', 'max_written_questions'):
                config.pop(key)
        conn.execute('UPDATE personalized_recommendation_drafts SET input_fingerprint=? WHERE draft_id=?',
            (_hash_payload({'diagnosis': request['diagnosis'], 'config': config}), draft['draft_id']))
    assert _make_direct(direct_module, token='b', max_questions_per_skill=1)['draft_id'] == draft['draft_id']


def test_handout_knowledge_order_and_edit_keep_chapter_and_skill_notes(direct_module, monkeypatch):
    from question_bank.services.knowledge_order import Placement
    def placements(items, candidates, config):
        if config.purpose != 'handout':
            return None
        return {item['question_id']: Placement('ch', 1, 'TEST第一章', 's', 1, 'TEST第一节',
            'sk_test', 'TEST技能') for item in items}
    monkeypatch.setattr(direct_module, '_handout_placements', placements)
    draft = direct_module.create(request_token='c1' * 16, diagnosis=_direct_diagnosis(),
        config=PersonalizedRecommendationConfig(purpose='handout', scope_keys=(BNU_CHAPTER4,), question_count=4),
        actor_ref='synthetic')
    items = draft['students'][0]['items']
    assert items and all(item['knowledge_section']['title'] == 'TEST第一章 · TEST第一节' for item in items)
    assert all(item['primary_skill_name'] == 'TEST技能' for item in items)
    edited = direct_module.edit(draft['draft_id'], RecommendationEditCommand(request_token='c2' * 16,
        expected_revision=1, action='exclude', student_id='A', item_id=items[0]['item_id'],
        actor_ref='synthetic', reason='TEST删除题目'))
    remaining = edited['students'][0]['items']
    assert [item['item_order'] for item in remaining] == list(range(1, len(remaining) + 1))
    assert all(item['knowledge_section']['id'] == 's' for item in remaining)


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


def test_endpoint_comparison_rechecks_old_labels_and_difficulty_before_coverage():
    from tools.compare_training_endpoints import aggregate, common_audit

    def entry(qid, difficulty, maximum):
        return {"candidate": {"question_id": qid, "difficulty": difficulty},
                "key": BNU_TARGET, "selection_kind": "direct", "practice_purpose": "remediation",
                "practice_role": "full_response", "student_id": "SYN-ENDPOINT",
                "distance": 0, "target": {"difficulty_plan": {"minimum": 1, "maximum": 10,
                    "audit_minimum": 1, "audit_maximum": maximum}}}

    old_claim = entry(1, 4, 8)
    compatible = entry(2, 2, 3)
    latest_pool = [entry(1, 4, 3), compatible]
    paper = [(old_claim, [old_claim]), (compatible, [compatible])]
    audit = common_audit(paper, latest_pool, {BNU_TARGET: {}}, set(), None)
    assert audit["items"][0]["level"] == "above"
    assert audit["items"][0]["targets"] == []
    assert audit["items"][1]["targets"] == [BNU_TARGET]
    assert audit["covered"] == [BNU_TARGET]
    assert aggregate([audit])["remediation_slots"] == 1
    # The original label alone supplies neither a current need nor a task window.
    unsupported = common_audit(paper[:1], [], {BNU_TARGET: {}}, set(), None)
    assert unsupported["items"][0]["level"] == "unknown"
    assert unsupported["covered"] == []
    new = {**entry(3, 4.5, 6), "practice_purpose": "new", "target": {"difficulty_plan": {
        "minimum": 2.5, "maximum": 6, "aim": 4.5, "basis": "合成浮动依据"}}}
    checked = {**entry(3, 4.5, 3), "practice_purpose": "new",
               "task_evidence_level": "observed_task",
               "target": {"difficulty_plan": {"minimum": 1, "maximum": 10,
            "audit_minimum": 1, "audit_maximum": 3}}}
    new_audit = common_audit([(new, [new])], [checked], {}, set(), None)
    assert new_audit["items"][0]["window"] == [2.5, 6]
    assert new_audit["items"][0]["level"] == "within"
    assert new_audit["items"][0]["new_task_state"] == "observed"
    assert new_audit["items"][0]["new_target_keys"] == [BNU_TARGET]
    assert aggregate([new_audit])["new_observed_task_slots"] == 1
    assert new_audit["covered"] == []
    diagnostic = {**new, "target": {**new["target"], "diagnostic_check": True}}
    diagnostic_audit = common_audit([(diagnostic, [diagnostic])], [checked], {}, set(), None)
    assert diagnostic_audit["items"][0]["new_task_state"] == "diagnostic"
    assert diagnostic_audit["covered"] == []
    assert aggregate([diagnostic_audit])["diagnostic_slots"] == 1


def test_endpoint_report_rejects_identity_and_verifies_question_differences(tmp_path):
    from tools.build_training_endpoint_report import build

    report = {"input": {"same_student_scores": True, "same_recent_history": True, "same_read_versions": True},
              "model_requests": 0, "database_writes": 0, "raw_student_exports": 0,
              "scopes": [{"native_parity": [True, True], "papers": [{
                  "before": {"items": [{"id": 1}, {"id": 2}]},
                  "after": {"items": [{"id": 2}, {"id": 3}], "covered": [BNU_TARGET], "gaps": []},
                  "current_need_count": 1, "retained_questions": [2], "removed_questions": [1], "added_questions": [3]}]}]}
    destination = tmp_path / "comparison.json"
    destination.write_text(json.dumps(report), encoding="utf-8")
    build(tmp_path)
    assert (tmp_path / "training-comparison.html").is_file()
    report["scopes"][0]["papers"][0]["student_id"] = "SYN-ENDPOINT"
    destination.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(AssertionError, match="Raw source field"):
        build(tmp_path)


def test_endpoint_question_display_reads_only_paired_stems_without_exporting_answers(tmp_path):
    from http.server import ThreadingHTTPServer
    from threading import Thread
    from types import SimpleNamespace
    from urllib.error import HTTPError
    from urllib.request import urlopen
    from tools.serve_training_endpoint_report import make_handler, question_content

    class Reader:
        def __init__(self):
            self.calls = []

        def get_questions(self, ids):
            self.calls.extend(ids)
            return [{"id": ids[0], "question_type": "选择题", "question_text": "合成题干",
                     "answer_text": "不能导出答案", "source_file": "不能导出原始路径",
                     "asset_urls": ["/question-image", "/answer-image"], "rich_content": {
                         "question_blocks": [{"kind": "paragraph", "text": "合成题干",
                             "html": '<span class="qm" data-latex="x^2">x²</span>',
                             "segments": [], "rows": [], "asset_urls": ["/question-image"],
                             "_xml": "不能导出原始 XML"}],
                         "answer_blocks": [{"text": "不能导出答案"}]}}]

        def resolve_asset(self, qid, index):
            assert (qid, index) == (1, 0)
            return SimpleNamespace(path=image, media_type="image/png")

    reader = Reader()
    payload = question_content(reader, 1)
    assert payload["blocks"][0]["html"].startswith('<span class="qm"')
    assert payload["blocks"][0]["asset_urls"] == ["/question-image"]
    assert "不能导出" not in json.dumps(payload, ensure_ascii=False)
    assert "/answer-image" not in json.dumps(payload)
    report = {"scopes": [{"papers": [{"before": {"items": [{"id": 1}]},
                                    "after": {"items": [{"id": 2}], "gaps": []}}],
                          "groups": [[], []]}]}
    (tmp_path / "comparison.json").write_text(json.dumps(report), encoding="utf-8")
    image = tmp_path / "synthetic.png"
    image.write_bytes(b"synthetic image bytes")
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(tmp_path, reader))
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(base + "/question-content/1") as response:
            assert response.headers["Cache-Control"] == "no-store"
            assert json.load(response)["text"] == "合成题干"
        with urlopen(base + "/api/question-bank/questions/1/assets/0") as response:
            assert response.read() == image.read_bytes()
        calls = reader.calls.copy()
        for path in ("/question-content/999", "/api/question-bank/questions/999/assets/0",
                     "/comparison.json", "/../user_data/config/settings.json"):
            with pytest.raises(HTTPError) as failure:
                urlopen(base + path)
            assert failure.value.code == 404
        assert reader.calls == calls
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def test_type_need_stats_reason_reports_stable_loss():
    from question_bank.recommendation.personalized import (
        _practice_reason_summary, _type_class_totals, _type_need_stats)

    TYPE = "kp_bnu24_math_g8_upper_1_1_t05"

    def ref(session, question, score, full=5):
        return {"session_id": session, "question_id": question,
                "score_awarded": score, "full_score": full,
                "source_kind": "current_exam",
                "assessment": {"eligible": True, "evidence_weight": 1,
                               "granularity": "part"}}

    target = {"knowledge_key": TYPE,
              "source_question_refs": [ref(1, "Q1", 0), ref(1, "Q2", 1), ref(2, "Q1", 5)]}
    diagnosis = {"students": [
        {"student_id": "A", "weak_points": [target]},
        {"student_id": "B", "weak_points": [{"knowledge_key": "kp_other_t01",
            "source_question_refs": [ref(1, "Q1", 4), ref(1, "Q2", 3), ref(2, "Q1", 5)]}]},
    ]}
    stats = _type_need_stats(target, _type_class_totals(diagnosis))
    assert stats["stable"] and stats["attempted"] == 3 and stats["lost"] == 2
    assert stats["rate"] == pytest.approx(0.4) and stats["class_rate"] == pytest.approx(0.6)
    # The same exam question projected under several targets counts once.
    diagnosis["students"][0]["weak_points"].append(deepcopy(target))
    assert _type_need_stats(target, _type_class_totals(diagnosis)) == stats
    for scores, expected in (([0], False), ([0, 0], True), ([0, 5], False),
                             ([2, 4], False), ([2, 3], True)):
        measured = {"source_question_refs": [ref(1, f"Q{i}", score)
                                             for i, score in enumerate(scores)]}
        assert _type_need_stats(measured, {})["stable"] is expected
    weighted = {"source_question_refs": [ref(1, "Q1", 0, 1), ref(1, "Q2", 9, 9)]}
    weighted_stats = _type_need_stats(weighted, {})
    assert weighted_stats["rate"] == pytest.approx(.9)
    assert not weighted_stats["stable"]
    excluded = [
        {**ref(3, "Q1", 0), "source_kind": "training"},
        {**ref(3, "Q2", 0), "assessment": {"eligible": False}},
        {**ref(3, "Q3", 0), "assessment": {"granularity": "whole"}},
        {**ref(3, "Q4", 0), "assessment": {"evidence_weight": .5}},
    ]
    deduped = {**target, "source_question_refs": [*target["source_question_refs"],
        deepcopy(target["source_question_refs"][0]), *excluded]}
    assert _type_need_stats(deduped, _type_class_totals(diagnosis)) == stats
    entry = {"student_id": "A", "key": TYPE, "matched_key": TYPE,
             "selection_kind": "direct", "match_label": "同题型", "match_level": 1,
             "practice_purpose": "remediation",
             "candidate": {"stable_names": {TYPE: "八年级上册｜第一章｜题型·数轴定位"},
                           "difficulty": 2.0},
             "target": {"stable_key": TYPE, "tier": "weak", "need_stats": stats}}
    reason = _practice_reason_summary([entry])
    assert "补弱·题型·数轴定位" in reason
    assert "稳定失分：考3错2" in reason
    assert "本人 40%" in reason and "选中群体 60%" in reason
    # A one-student diagnosis still compares against its complete own class.
    from question_bank.recommendation.personalized import _normalize_diagnosis
    selected = {"students": [{**diagnosis["students"][0], "class_id": "TEST甲班"}],
                "_type_class_question_totals": {
                    "TEST甲班": {"1": {
                        "Q1": {"score_sum": 40, "full_score_sum": 50},
                        "Q2": {"score_sum": 30, "full_score_sum": 50}},
                        "2": {"Q1": {"score_sum": 50, "full_score_sum": 50}}},
                    "TEST乙班": {"1": {
                        "Q1": {"score_sum": 5, "full_score_sum": 50},
                        "Q2": {"score_sum": 45, "full_score_sum": 50}}}}}
    normalized = _normalize_diagnosis(selected)
    own_totals = _type_class_totals(normalized, "TEST甲班")
    own_stats = _type_need_stats(target, own_totals, comparison_scope="full_class")
    assert own_stats["rate"] == stats["rate"]
    assert own_stats["class_rate"] == pytest.approx(.8)
    assert own_stats["reference_exam_key"] == ["1", "Q1"]
    other_stats = _type_need_stats(target, _type_class_totals(normalized, "TEST乙班"),
                                 comparison_scope="full_class")
    assert other_stats["reference_exam_key"] == ["1", "Q2"]
    assert other_stats["class_rate"] is None  # Missing one of the same exam items.
    class_entry = {**entry, "target": {**entry["target"], "need_stats": own_stats}}
    assert "全班 80%" in _practice_reason_summary([class_entry])
    selected["_type_class_question_totals"]["TEST甲班"]["1"]["Q2"]["score_sum"] = 45
    assert _type_class_totals(normalized, "TEST甲班") == own_totals
    refreshed = _normalize_diagnosis(selected)
    refreshed_stats = _type_need_stats(target, _type_class_totals(refreshed, "TEST甲班"),
                                       comparison_scope="full_class")
    assert refreshed_stats["reference_exam_key"] == ["1", "Q2"]
    assert _type_class_totals({**normalized, "_type_class_question_totals": {}}, "TEST甲班") == {}
    assert _type_need_stats(target, {}, comparison_scope="full_class")["class_rate"] is None
    from question_bank.recommendation.personalized import _type_need_priority
    assert _type_need_priority({"stable": True, "gap": -.1}) > _type_need_priority({"stable": True, "gap": -.2})
    assert _type_need_priority({"stable": True, "gap": -.6}) > _type_need_priority({"stable": False, "gap": 1})


def test_type_remediation_uses_one_exam_anchor_and_two_difficulty_bands(monkeypatch):
    from dataclasses import replace
    from question_bank.current_knowledge import CurrentKnowledgeResolver
    from question_bank.knowledge_graph_release.loader import load_taxonomy_catalog_for_release
    from question_bank.recommendation import personalized as engine

    key = "kp_bnu24_math_g8_upper_1_1_t01"
    release = load_release_for_taxonomy_revision(11)
    module = object.__new__(PersonalizedRecommendationModule)
    module.current_knowledge = CurrentKnowledgeResolver(release, load_taxonomy_catalog_for_release(release))
    monkeypatch.setattr(module, "_source_practice_metadata", lambda _: {})
    facet = {"part_id": "p", "direct_keys": [key], "type_keys": [key],
             "section_keys": ["kp_bnu24_math_g8_upper_1_1"], "chapter_keys": ["kp_bnu24_math_g8_upper_1"]}

    def ref(qid, score, difficulty, text):
        return {"session_id": 1, "question_id": qid, "score_awarded": score, "full_score": 5,
                "source_kind": "current_exam", "question_difficulty": difficulty,
                "question_text": text, "direct_keys": [key], "target_facets": [facet],
                "assessment": {"eligible": True, "evidence_weight": 1, "granularity": "part"}}

    points = {sid: {"knowledge_key": key, "stable_key": key, "value": .1,
                    "logit_mean": -9, "difficulty_slope": 1, "evidence_count": 2,
                    "source_question_refs": [ref("Q1", 0 if sid == "A" else 5, 6, "合成参照甲"),
                                             ref("Q2", 3 if sid == "A" else 1, 3, "合成参照乙")]}
              for sid in ("A", "B", "C")}
    diagnosis = {"exam_scope": {"mode": "current", "session_ids": [1]},
                 "students": [{"student_id": sid, "student_name": f"合成学生{sid}",
                               "class_id": "TEST", "score_rate": .1, "weak_points": [point]}
                              for sid, point in points.items()]}
    candidates = (
        _selection_candidate(1, "合成全等角度关系", key, difficulty=5, target_facets=[facet]),
        _selection_candidate(2, "合成正比例函数图象", key, difficulty=6, target_facets=[facet]),
        _selection_candidate(3, "合成数轴无理数定位", key, difficulty=4, target_facets=[facet]),
        _selection_candidate(4, "合成圆弧统计中位数", key, difficulty=3, target_facets=[facet]),
    )
    similarities = {c["question_text"]: value for c, value in zip(candidates, (.8, .1, 1., 1.))}
    monkeypatch.setattr(engine, "_stem_similarity",
                        lambda source, text: similarities.get(text, .5) if source == "合成参照甲" else 1.)
    config = PersonalizedRecommendationConfig(purpose="handout", question_count=4, remediation_only=True,
        scope_keys=("kp_bnu24_math_g8_upper_1",), curriculum_volume_id="bnu24-math-g8-upper")

    def evaluate(pool=candidates, observed=points):
        result = module.evaluate_candidates(diagnosis=diagnosis, config=config, candidates=pool,
            mastery={(sid, key): point for sid, point in observed.items()}, source_metadata={},
            recent={}, excluded=set())["pools"]["A"]
        return [e for e in result if e["key"] == key and engine._is_core(e)]

    entries = evaluate()
    assert all(e["target"]["difficulty_plan"].get("reference_difficulty") == 6 for e in entries)
    assert {e["candidate"]["question_id"] for e in entries} == {1, 2, 3}
    chosen = engine._choose_practice_entries(entries, 1, config)[0][0]
    assert chosen["candidate"]["question_id"] == 1  # Similarity wins over zero difficulty gap.
    assert chosen["target"]["source_question_refs"][0]["question_id"] == "Q1"
    assert chosen["target"]["difficulty_plan"]["reference_class_rate"] == pytest.approx(2 / 3)
    assert chosen["target"]["target_difficulty"] == 6  # Model and overall marks do not lower it.
    with_patterns = deepcopy(entries[:2])
    with_patterns[0]["candidate"]["similarity_profile"]["tags"] = [{"tag_type": "method", "tag_value": "TEST同方法"}]
    filler = deepcopy(with_patterns[0])
    other_key = "kp_bnu24_math_g8_upper_1_1_t02"
    filler.update(key=other_key, matched_key=other_key)
    filler["candidate"].update(question_id=15, question_text="合成概率与中位数", stable_keys=[other_key], required_keys=[other_key])
    filler["target"].update(stable_key=other_key, need_stats={"stable": True, "gap": 1})
    patterned = engine._choose_practice_entries([filler, *with_patterns], 2, config)
    assert [e["candidate"]["question_id"] for e, _ in patterned] == [15, 1]
    wider = engine._choose_practice_entries(evaluate(candidates[2:]), 1, config)[0][0]
    assert wider["candidate"]["question_id"] == 3 and wider["reference_radius"] == 2
    assert "上下 1 级内可入卷候选不足" in engine._practice_reason_summary([wider])
    assert evaluate(candidates[3:]) == []  # More than two levels away is rejected.
    two = replace(config, max_questions_per_skill=2)
    assert {e["candidate"]["question_id"] for e, _ in engine._choose_practice_entries(entries, 2, two)} == {1, 2}
    near_and_wide = evaluate((candidates[0], candidates[2]))
    assert [e["reference_radius"] for e, _ in engine._choose_practice_entries(near_and_wide, 2, two)] == [1, 2]
    unknown_reference = deepcopy(points)
    unknown_reference["A"]["source_question_refs"][0]["question_difficulty"] = None
    assert evaluate(candidates, unknown_reference) == []

    # Correct-only and unmeasured targets keep the existing plan and purpose.
    for purpose, refs in (("consolidation", [{**r, "score_awarded": 5} for r in points["A"]["source_question_refs"]]),
                          ("new", [])):
        observed = {**points, "A": {**points["A"], "source_question_refs": refs}}
        rows = evaluate((_selection_candidate(9, "合成面积测量任务", key, difficulty=2, target_facets=[facet]),), observed)
        assert rows and rows[0]["practice_purpose"] == purpose
        assert "reference_radius" not in rows[0]

    # The public selection remains one student; its unselected classmates'
    # full-class scores choose Q2 instead of the selected student's Q1.
    diagnosis["students"] = diagnosis["students"][:1]
    diagnosis["_type_class_question_totals"] = {"TEST": {"1": {
        "Q1": {"score_sum": 5, "full_score_sum": 50},
        "Q2": {"score_sum": 45, "full_score_sum": 50}}}}
    full_class_entries = evaluate()
    assert {e["student_id"] for e in full_class_entries} == {"A"}
    anchored = engine._choose_practice_entries(full_class_entries, 1, config)[0][0]
    assert anchored["target"]["source_question_refs"][0]["question_id"] == "Q2"
    assert anchored["target"]["target_difficulty"] == 3
    assert anchored["candidate"]["question_id"] == 4
    assert "全班在参照题上的加权得分率 90%" in engine._practice_reason_summary([anchored])


@pytest.mark.parametrize("paper_mode", [None, "individual", "shared"])
def test_type_section_spread_prefers_other_sections_and_allows_top_up(paper_mode):
    """Spread is a preference in both paper modes, never a shortage-producing cap."""
    from dataclasses import replace
    from question_bank.recommendation.personalized import _choose_practice_entries

    def entry(qid, key, section, *, stable=True, gap=.5, mastery=None):
        return {"candidate": {"question_id": qid, "stable_keys": [key],
                              "question_type": "选择题"},
                "student_id": "A", "key": key, "matched_key": key,
                "selection_kind": "direct", "practice_purpose": "remediation",
                "match_level": 1, "distance": 0, "preference": 0,
                "target_section": section,
                "target": {"stable_key": key, "value": mastery,
                           "need_stats": {"stable": stable, "gap": gap,
                                          "points_lost": 5}}}

    s1 = ["kp_bnu24_math_g8_upper_1_1_t01", "kp_bnu24_math_g8_upper_1_1_t02",
          "kp_bnu24_math_g8_upper_1_1_t03", "kp_bnu24_math_g8_upper_1_1_t04"]
    s2 = "kp_bnu24_math_g8_upper_1_2_t01"
    entries = [entry(i, key, "s1") for i, key in enumerate(s1, 1)]
    entries.append(entry(5, s2, "s2", stable=False, gap=.1))
    config = (PersonalizedRecommendationConfig(
        paper_mode=paper_mode, remediation_only=True, purpose="handout", question_count=5,
        scope_keys=("kp_bnu24_math_g8_upper_1",),
        max_consolidation_questions=0, max_unmeasured_questions=0)
        if paper_mode else None)
    chosen = _choose_practice_entries(entries, 3, config)
    # The third pick would put s1 at 3/3, past the 2/3 share, so the
    # lower-priority s2 need wins; rank alone would take all three s1 needs.
    assert sorted(e["key"] for e, _ in chosen) == sorted([s1[0], s1[1], s2])

    # Two unused s1 candidates also exercise the optimizer's two-question
    # exchange: neither a single nor a double exchange may undo this spread.
    # With no other-section candidate, all four distinct types are usable.
    same_section = _choose_practice_entries(entries[:4], 4, config)
    assert [e["key"] for e, _ in same_section] == s1
    # Once the other section is exhausted, fill the remaining requested slot.
    topped_up = _choose_practice_entries(entries, 5, config)
    assert len(topped_up) == 5
    assert {e["key"] for e, _ in topped_up} == {*s1, s2}
    if config is not None:
        unavailable_other_section = deepcopy(entries)
        unavailable_other_section[-1]["candidate"]["question_type"] = "解答题"
        remaining = _choose_practice_entries(unavailable_other_section, 3,
                                            replace(config, max_written_questions=0))
        assert [e["key"] for e, _ in remaining] == s1[:3]

    skill_entries = [entry(1, "sk_a", "s1", mastery=0),
                     entry(2, "sk_b", "s1", mastery=0),
                     entry(3, "sk_c", "s1", mastery=0),
                     entry(4, "sk_d", "s2", mastery=.9)]
    chosen_skills = _choose_practice_entries(skill_entries, 3, config)
    assert [e["key"] for e, _ in chosen_skills] == ["sk_a", "sk_b", "sk_c"]


def test_knowledge_target_matching_shares_layers_and_similarity_boundary():
    from question_bank.recommendation.target_matching import match_target
    from question_bank.recommendation.personalized import _candidate_training_keys, _paper_skill_limit_exceeded
    key = "kp_bnu24_math_g8_lower_2_1_1"
    second = "kp_bnu24_math_g8_lower_2_1_2"
    section = "kp_bnu24_math_g8_lower_2_1"
    index = {key: {"kind": "topic", "target_kind": "knowledge", "section": section, "chapter": "chapter"}}
    source = [{"part_id": "source", "topic_keys": [key], "direct_keys": [key], "section_keys": [section]}]
    same = [{"part_id": "same", "topic_keys": [key], "direct_keys": [key], "section_keys": [section]}]
    near = [{"part_id": "near", "topic_keys": [second], "direct_keys": [second], "section_keys": [section]}]
    assert match_target(key, source, same, index, similarity=0)["match_label"] == "同知识点"
    assert match_target(key, source, near, index, similarity=.6)["match_level"] == 3
    assert match_target(key, source, near, index, similarity=.5999) is None
    assert match_target(key, source, [{**near[0], "section_keys": ["elsewhere"]}], index, similarity=1) is None
    candidate = {"stable_keys": [key, "sk_old"], "training_target_keys": [key]}
    assert _candidate_training_keys(candidate) == {key}
    assert _paper_skill_limit_exceeded(candidate, [candidate]) == {key}


def test_knowledge_fallback_recommendation_uses_same_target_then_near_section(monkeypatch):
    from question_bank.taxonomy.curriculum_catalog import curriculum_volume
    monkeypatch.setattr(PersonalizedRecommendationModule, "_handout_placements", lambda *_: None)
    volume = curriculum_volume(volume_id="bnu24-math-g8-lower")
    chapter = volume["chapters"][1]
    section = chapter["sections"][0]
    key, second = [point["id"] for point in section["knowledge_points"][:2]]
    def facets(topic):
        return [{"part_id": "part1", "direct_keys": [topic], "topic_keys": [topic], "skill_keys": [],
                 "type_keys": [], "section_keys": [section["knowledge_id"]], "chapter_keys": [chapter["knowledge_id"]]}]
    source_text = "解不等式2x+3>5，并在数轴上表示解集"
    candidates = [
        _selection_candidate(1, "同知识点独立练习", key=key, training_target_keys=[key], target_facets=facets(key)),
        _selection_candidate(2, source_text, key=second, training_target_keys=[second], target_facets=facets(second)),
        _selection_candidate(3, "三角形中的面积计算与角度证明", key=second, training_target_keys=[second], target_facets=facets(second)),
    ]
    diagnosis = _direct_diagnosis((("A", .9, 900, key),))
    ref = diagnosis["students"][0]["weak_points"][0]["source_question_refs"][0]
    ref.update(question_text=source_text, direct_keys=[key], target_facets=facets(key))
    draft = _selection_draft(monkeypatch, candidates, diagnosis, taxonomy_revision=11,
        scope_keys=(chapter["knowledge_id"],), curriculum_volume_id=volume["id"],
        remediation_only=True, max_unmeasured_questions=0, purpose="handout", question_count=1, max_written_questions=1)
    item, = draft["students"][0]["items"]
    assert item["question_id"] == 1
    assert item["target"]["target_kind"] == "knowledge"
    assert item["match_level"] == 1 and item["match_label"] == "同知识点"
    assert "尚未关联技能" not in " ".join(draft["students"][0]["warnings"])
    near = _selection_draft(monkeypatch, candidates[1:], diagnosis, taxonomy_revision=11,
        scope_keys=(chapter["knowledge_id"],), curriculum_volume_id=volume["id"],
        remediation_only=True, max_unmeasured_questions=0, purpose="handout", question_count=1, max_written_questions=1)
    item, = near["students"][0]["items"]
    assert item["question_id"] == 2
    assert item["match_level"] == 3 and item["match_label"] == "同小节相近题"


def test_knowledge_scope_keeps_target_when_it_has_skill_children():
    from types import SimpleNamespace
    from question_bank.recommendation.personalized import _scope_leaves
    topic = "kp_bnu24_math_g8_lower_2_1_1"
    skill = "sk_bnu24_math_g8_lower_2_1_01"
    parent = "kp_bnu24_math_g8_lower_2_1"
    relations = [{"relation_type": "parent", "source_key": topic, "target_key": parent},
                 {"relation_type": "parent", "source_key": skill, "target_key": topic}]
    resolver = SimpleNamespace(release_id="TEST-knowledge-with-skill-child", taxonomy_revision=11,
        nodes=[SimpleNamespace(stable_key=key) for key in (topic, skill)],
        relations=[SimpleNamespace(**edge) for edge in relations])
    assert _scope_leaves((parent,), diagnosis={}, relations=relations, resolver=resolver,
                         volume_id="bnu24-math-g8-lower") == (topic,)
    assert _scope_leaves((topic,), diagnosis={}, relations=relations, resolver=resolver,
                         volume_id="bnu24-math-g8-lower") == (topic,)
    assert _scope_leaves((parent,), diagnosis={}, relations=relations) == (skill,)
