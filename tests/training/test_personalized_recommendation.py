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
    assert not _unmeasured_entry({**observed, "target": {"observed_same_task": True}})

    from question_bank.recommendation.personalized import (
        _observed_new_practice_tasks, _new_target_operation, _new_practice_operations,
        _choose_practice_entries,
    )
    chapter = "kp_bnu24_math_g8_upper_1"
    source_key = "sk_bnu24_math_g8_upper_1_3_102"
    frozen = {"full_score": 5, "score_awarded": 5, "task_evidence_version_matches": True,
        "assessment": {"granularity": "part", "eligible": True},
        "practice_observations_by_key": {source_key: [{"part_observable": "作垂线构造直角三角形，再求边长",
            "evidence_points": [{"evidence_point_id": "p1", "observable_evidence": "求边长"},
                {"evidence_point_id": "p2", "observable_evidence": "构造直角三角形"}]}]}}
    original = deepcopy(frozen)
    assert "right_triangle_construction" in _observed_new_practice_tasks([frozen])[chapter]["observed"]
    assert frozen == original
    for invalid in ({"task_evidence_version_matches": False},
        {"assessment": {"granularity": "whole_question", "eligible": True}},
        {"assessment": {"granularity": "part", "eligible": False}}):
        assert not _observed_new_practice_tasks([{**frozen, **invalid}])
    step = {**frozen, "assessment": {"granularity": "step", "step_id": "p1"}}
    assert "right_triangle_construction" not in _observed_new_practice_tasks([step])[chapter]["observed"]
    step["assessment"]["step_id"] = "p2"
    assert "right_triangle_construction" in _observed_new_practice_tasks([step])[chapter]["observed"]
    assert "right_triangle_construction" not in _new_practice_operations("无需作垂线或构造直角三角形")
    assert _new_target_operation("八上｜技能·构造直角三角形") == "right_triangle_construction"
    assert _new_target_operation("八上｜技能·未知任务") == ""
    for name, text, operation in (
        ("分类讨论直角位置", "分别以三条边为斜边讨论直角三角形的位置", "right_angle_position_cases"),
        ("用线段的和差关系列式求长度", "用线段的和差关系列式求长度", "segment_sum_difference_equation"),
    ):
        assert _new_target_operation(f"八上｜技能·{name}") == operation
        seen = deepcopy(frozen)
        seen["practice_observations_by_key"][source_key][0]["part_observable"] = text
        assert operation in _observed_new_practice_tasks([seen])[chapter]["observed"]
        assert operation not in _new_practice_operations("无需" + text)
        seen["assessment"] = {"granularity": "step", "step_id": "p1"}
        assert operation not in _observed_new_practice_tasks([seen])[chapter]["observed"]
    roots = deepcopy(frozen)
    roots["practice_observations_by_key"][source_key][0]["part_observable"] = "合并同类二次根式"
    root_history = _observed_new_practice_tasks([roots])[chapter]
    assert "like_radical_recognition" in root_history["related"]
    assert "like_radical_recognition" not in root_history["observed"]

    def new_entry(qid, difficulty, keys, related=False):
        q = _selection_candidate(qid, f"合成新任务{qid}", key=keys[0], difficulty=difficulty, stable_keys=keys)
        return {"candidate": q, "student_id": "TEST", "key": keys[0], "matched_key": keys[0],
            "selection_kind": "direct", "practice_purpose": "new", "match_level": 1,
            "distance": abs(difficulty-4.5), "preference": 0,
            "target": {"related_task_observed": related, "difficulty_plan": {"aim": 4.5, "starter": 3.5}}}
    entries = [new_entry(101, 3.5, ["sk_new_a"]), new_entry(102, 4.5, ["sk_new_a"]),
        new_entry(103, 4.5, ["sk_new_b"]), new_entry(104, 4.5, ["sk_new_c", "sk_known_1", "sk_known_2"]),
        new_entry(105, 4.5, ["sk_new_d"], related=True)]
    chosen = _choose_practice_entries(entries, 10, PersonalizedRecommendationConfig(
        remediation_only=True, max_unmeasured_questions=2))
    assert [e["candidate"]["question_id"] for e, _ in chosen] == [101, 103]


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


def test_same_frozen_task_links_count_once_and_unknown_context_stays_separate():
    from question_bank.recommendation.personalized import _task_need_ids, _choose_practice_entries
    def entry(qid, key, text, mode="exact_objective", chapter="TEST-chapter"):
        part = {"part_id": "p", "response_mode": mode, "observable": text}
        ref = {"full_score": 1, "score_awarded": 0, "task_evidence_version_matches": True,
               "practice_observations_by_key": {key: [part]},
               "target_facets": [{"part_id": "p", "chapter_keys": [chapter] if chapter else []}]}
        return {"candidate": _selection_candidate(qid, f"合成独立任务{qid}", stable_keys=["sk_budget"]),
            "student_id": "TEST", "key": key, "matched_key": key, "selection_kind": "direct",
            "practice_purpose": "remediation", "target": {"value": .5, "source_question_refs": [ref]},
            "distance": 0., "preference": 0., "match_level": 1}
    topic = entry(1, "kp_topic", "计算9的算术平方根为3")
    skill = entry(1, "sk_skill", "计算9的算术平方根为3")
    assert _task_need_ids(topic) == _task_need_ids(skill)
    assert _task_need_ids(topic) != _task_need_ids(entry(1, "sk_skill", "计算9的算术平方根为3", "process_required"))
    assert _task_need_ids(topic) != _task_need_ids(entry(1, "sk_skill", "计算9的算术平方根为3", chapter="OTHER"))
    another_source = deepcopy(skill)
    another_source["target"]["source_question_refs"][0]["question_id"] = "OTHER-QUESTION"
    assert _task_need_ids(topic) != _task_need_ids(another_source)
    multiple_sources = deepcopy(skill)
    multiple_sources["target"]["source_question_refs"] += another_source["target"]["source_question_refs"]
    assert _task_need_ids(multiple_sources) == frozenset({("TEST", "sk_skill")})
    unknown = entry(1, "sk_unknown", "作答为B")
    assert _task_need_ids(unknown) == frozenset({("TEST", "sk_unknown")})
    assert _task_need_ids(entry(1, "sk_missing_context", "计算9的算术平方根为3", chapter="")) == frozenset({("TEST", "sk_missing_context")})
    distinct = [entry(2, "sk_cube", "计算8的立方根为2"), entry(2, "sk_area", "利用面积关系相加")]
    chosen = _choose_practice_entries([topic, skill, *distinct], 1, PersonalizedRecommendationConfig())
    assert chosen[0][0]["candidate"]["question_id"] == 2


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
        target_facets=[{"part_id": "p", "direct_keys": [BNU_TARGET], "topic_keys": [BNU_TARGET],
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


@pytest.mark.parametrize("source_text,candidate_text,expected", [
    ("得到64的平方根为±8", "求81的平方根为±9", True),
    ("得到64的平方根为±8", "求81的算术平方根为9", False),
    ("两次逆向运用算术平方根定义，反求被开方数的值", "求9的算术平方根", False),
    ("求9的算术平方根", "算术平方根的定义与性质", False),
    ("求8的立方根", "计算∛(-27)=-3", True),
    ("对开得尽方的因数开方并约分", "将完全平方数开方得到最简结果", True),
    ("给分母同乘共轭根式，消去根号", "求9的算术平方根", False),
    ("作答为B", "求9的算术平方根", False),
    ("利用正方形面积关系和勾股等式求边长", "判断勾股数并列式a²+b²=c²", False),
    ("利用正方形面积关系和勾股等式求边长", "正方形面积关系相加，利用勾股等式求边长", True),
    ("分类讨论直角顶点的位置", "判断三边能否组成直角三角形", False),
    ("利用线段之和与差列方程", "根据勾股定理列式a²+b²=c²", False),
])
def test_observed_root_tasks_bridge_only_the_same_operation(source_text, candidate_text, expected):
    from question_bank.recommendation.personalized import _task_matched_part
    ref = {"practice_observations_by_key": {BNU_TARGET: [{"observable": source_text}]}}
    candidate = {"target_facets": [{"part_id": "p"}], "practice_observations_by_key": {
        "sk_TEST_root": [{"part_id": "p", "response_mode": "exact_objective", "observable": candidate_text}]}}
    result = _task_matched_part(candidate, BNU_TARGET, ref, [], {"p"})
    assert bool(result) is expected
    assert _task_matched_part(candidate, BNU_TARGET, ref, [], {"other-part"}) is None
    if any(word in source_text for word in ("面积关系", "直角顶点", "线段之和")):
        source = ref["practice_observations_by_key"][BNU_TARGET][0]
        source.update(fine_terms=["sk_TEST_root"], evidence_points=[{"evidence_point_id": "p1"}])
        assert bool(_task_matched_part(candidate, BNU_TARGET, ref, [], {"p"})) is expected


def test_frozen_point_skill_bridge_preserves_actual_skill_and_class_core_filter(monkeypatch):
    # Stand in for two published skills; the one-part topic/scope check remains.
    monkeypatch.setattr("question_bank.recommendation.personalized._allowed_keys_for_config",
        lambda *args: frozenset({BNU_TARGET, "sk_TEST_roots", "sk_TEST_other"}))
    diagnosis = _direct_diagnosis()
    for profile in diagnosis["students"]:
        ref = profile["weak_points"][0]["source_question_refs"][0]
        ref["practice_observations_by_key"] = {BNU_TARGET: [{"part_id": "source", "observable": "求64的平方根为±8",
            "response_mode": "exact_objective", "fine_terms": [BNU_TARGET, "sk_TEST_roots"],
            "evidence_points": [{"evidence_point_id": "point", "target": "求64的平方根为±8"}]}]}
        ref["target_facets"] = [{"part_id": "source", "direct_keys": [BNU_TARGET], "topic_keys": [BNU_TARGET],
            "chapter_keys": [BNU_CHAPTER4], "section_keys": [BNU_CHAPTER4 + "_2"]}]
    candidates = []
    for qid, skill in ((1, "sk_TEST_roots"), (2, "sk_TEST_other")):
        candidate = _selection_candidate(qid, f"合成任务{qid}", skill, difficulty=3,
            target_facets=[{"part_id": "p", "direct_keys": [skill], "skill_keys": [skill], "topic_keys": [BNU_TARGET],
                "chapter_keys": [BNU_CHAPTER4], "section_keys": [BNU_CHAPTER4 + "_2"]}],
            practice_observations_by_key={skill: [{"part_id": "p", "response_mode": "exact_objective", "observable": "作答为A"}]})
        candidates.append(candidate)
    draft = _selection_draft(monkeypatch, candidates, diagnosis, remediation_only=True)
    item = draft["students"][0]["items"][0]
    assert [q["question_id"] for q in draft["students"][0]["items"]] == [1]
    assert item["selection_kind"] == "task_matched"
    assert item["matched_key"] == "sk_TEST_roots"
    different_operation = deepcopy(candidates)
    different_operation[0]["practice_observations_by_key"]["sk_TEST_roots"][0]["observable"] = "计算81的算术平方根为9"
    assert _selection_draft(monkeypatch, different_operation, diagnosis, remediation_only=True)["students"][0]["items"] == []
    ambiguous = deepcopy(diagnosis)
    ambiguous["students"][0]["weak_points"][0]["source_question_refs"][0]["practice_observations_by_key"][BNU_TARGET][0]["evidence_points"].append({"evidence_point_id": "other"})
    assert _selection_draft(monkeypatch, candidates, ambiguous, remediation_only=True)["students"][0]["items"] == []

    # An earlier new-target evaluation must not raise a later remediation bridge.
    import question_bank.recommendation.personalized as recommendation
    original_entries = PersonalizedRecommendationModule._candidate_entries
    original_plan = recommendation._difficulty_plan
    evaluated = []
    with monkeypatch.context() as cache_guard:
        def split_plan(ref, rate, cap, target=None, profile=None, **kwargs):
            plan = original_plan(ref, rate, cap, target, profile, **kwargs)
            if (target or {}).get("stable_key") == "sk_TEST_roots":
                aim = 6. if kwargs.get("new_practice") else 3.
                plan = {**plan, "aim": aim, "minimum": 1., "maximum": 8.}
            return plan
        def include_new_target(self, **kwargs):
            kwargs["targets"] = [{"stable_key": BNU_TRANSFER_SIBLING, "source_question_refs": []}, *kwargs["targets"]]
            result = original_entries(self, **kwargs)
            evaluated.extend(result[0])
            return result
        cache_guard.setattr(recommendation, "_difficulty_plan", split_plan)
        cache_guard.setattr(PersonalizedRecommendationModule, "_candidate_entries", include_new_target)
        _selection_draft(cache_guard, candidates, diagnosis, remediation_only=True)
    same_skill = [e for e in evaluated if e["matched_key"] == "sk_TEST_roots"]
    assert any(e["practice_purpose"] == "new" and e["target"]["difficulty_plan"]["aim"] == 6. for e in same_skill)
    assert any(e["practice_purpose"] == "remediation" and e["target"]["difficulty_plan"]["aim"] == 3. for e in same_skill)

    # The teacher list uses core_only, so mere same-topic supplements stay out.
    original = PersonalizedRecommendationModule._candidate_entries
    calls = []
    def capture(self, **kwargs):
        kwargs["core_only"] = True
        result = original(self, **kwargs)
        calls.extend(result[0])
        return result
    monkeypatch.setattr(PersonalizedRecommendationModule, "_candidate_entries", capture)
    _selection_draft(monkeypatch, candidates, diagnosis, remediation_only=False)
    assert calls and {entry["candidate"]["question_id"] for entry in calls} == {1}


def test_task_bridge_does_not_train_an_already_correct_point_or_reassign_aggregate_skills():
    from question_bank.recommendation.personalized import _loss_practice_parts, _task_evidence_level, _task_matched_part
    equation = "根据勾股定理列式a²+b²=c²"
    root = "计算9的算术平方根为3"
    ref = {"task_evidence_version_matches": True,
        "assessment": {"point_observations": [{"point_id": "correct", "achieved": 1}, {"point_id": "failed", "achieved": 0}]},
        "practice_observations_by_key": {BNU_TARGET: [{"part_id": "s", "response_mode": "process_required",
            "observable": equation + "；" + root, "part_observable": equation + "；" + root,
            "fine_terms": [BNU_TARGET, "sk_TEST_equation", "sk_TEST_root"],
            "evidence_points": [{"evidence_point_id": "correct", "target": equation}, {"evidence_point_id": "failed", "target": root}]}]}}
    original = deepcopy(ref)
    parts = _loss_practice_parts(ref, BNU_TARGET)
    assert _task_evidence_level(parts) == "observed_step"
    # A multi-point aggregate does not identify which actual skill belongs to the failed point.
    assert parts[0]["fine_terms"] == []
    def candidate(text):
        return {"target_facets": [{"part_id": "p"}], "practice_observations_by_key": {"sk_TEST_x": [
            {"part_id": "p", "response_mode": "process_required", "observable": text}]}}
    assert _task_matched_part(candidate(equation), BNU_TARGET, ref, [], {"p"}) is None
    matched = _task_matched_part(candidate(root), BNU_TARGET, ref, [], {"p"})
    assert matched["practice_role"] == "step_practice"  # Only the failed root step, not the original full task.
    assert matched["task_operations"] == ["arithmetic_root_value"]
    assert ref == original
    ref["task_evidence_version_matches"] = False
    assert _task_evidence_level(_loss_practice_parts(ref, BNU_TARGET)) == "observed_task"
    ref["task_evidence_version_matches"] = True
    ref["assessment"]["point_observations"][1]["achieved"] = 1
    assert _loss_practice_parts(ref, BNU_TARGET) == []


def test_same_point_skill_can_support_component_root_practice_without_full_claim():
    from question_bank.recommendation.personalized import _task_matched_part
    ref = {"practice_observations_by_key": {BNU_TARGET: [{"response_mode": "exact_objective",
        "observable": "对开得尽方的因数开方并约分", "fine_terms": ["sk_TEST_root"],
        "evidence_points": [{"evidence_point_id": "point"}]}]}}
    candidate = {"target_facets": [{"part_id": "p"}], "practice_observations_by_key": {"sk_TEST_root": [
        {"part_id": "p", "response_mode": "exact_objective", "observable": "计算√16=4"}]}}
    result = _task_matched_part(candidate, BNU_TARGET, ref, [], {"p"})
    assert result["practice_role"] == "step_practice"
    assert result["task_match_basis"] == "same_point_skill"


@pytest.mark.parametrize("source_mode,candidate_mode,full", [
    ("exact_objective", "exact_objective", True),
    ("exact_objective", "process_required", True),
    ("process_required", "exact_objective", False),
    ("process_required", "process_required", True),
    ("short_answer_points", "exact_objective", False),
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


@pytest.mark.parametrize("source_text,candidate_text,full", [
    ("圆柱侧面展开成矩形，再根据勾股定理列式a²+b²=c²", "根据勾股定理列式a²+b²=c²", False),
    ("圆柱侧面展开成矩形，再根据勾股定理列式a²+b²=c²", "展开圆柱侧面为矩形，根据勾股定理列式a²+b²=c²", True),
    ("作辅助线构造直角三角形，根据勾股定理列式a²+b²=c²", "根据勾股定理列式a²+b²=c²", False),
    ("作辅助线构造直角三角形，根据勾股定理列式a²+b²=c²", "作辅助线后根据勾股定理列式a²+b²=c²", True),
    ("作辅助线构造直角三角形，根据勾股定理列式a²+b²=c²", "无需作辅助线，直接根据勾股定理列式a²+b²=c²", False),
    ("圆柱侧面展开成矩形，再根据勾股定理列式a²+b²=c²", "不用展开圆柱侧面，直接根据勾股定理列式a²+b²=c²", False),
    ("作答为B", "根据勾股定理列式a²+b²=c²", False),
])
def test_full_task_claim_requires_known_operations_and_original_construction(source_text, candidate_text, full):
    from question_bank.recommendation.personalized import _full_response_supported
    assert _full_response_supported({"response_mode": "process_required", "observable": candidate_text}, [],
        [{"response_mode": "process_required", "observable": source_text}]) is full


def test_objective_task_can_use_single_part_solution_without_borrowing_multipart_solution():
    from question_bank.recommendation.personalized import _task_matched_part, _training_tasks
    ref = {"source_kind": "current_exam", "full_score": 3, "score_awarded": 0,
        "assessment": {"granularity": "part", "eligible": True},
        "deduction_reason": "计算错误", "practice_observations_by_key": {BNU_TARGET: [
            {"response_mode": "exact_objective", "observable": "计算9的算术平方根为3"}]}}
    tasks = _training_tasks({"stable_key": BNU_TARGET, "source_question_refs": [ref]})
    candidate = {"target_facets": [{"part_id": "p"}], "solution_observable": "计算16的算术平方根为4",
        "practice_observations_by_key": {"sk_TEST_root": [{"part_id": "p", "response_mode": "exact_objective", "observable": "答案B"}]}}
    assert _task_matched_part(candidate, BNU_TARGET, ref, tasks, {"p"})["practice_role"] == "full_response"
    candidate["target_facets"].append({"part_id": "other"})
    assert _task_matched_part(candidate, BNU_TARGET, ref, tasks, {"p"}) is None


def test_direct_tag_cannot_override_a_different_observed_failed_operation(monkeypatch):
    diagnosis = _direct_diagnosis()
    text = "逆向运用算术平方根定义，反求被开方数的值"
    for profile in diagnosis["students"]:
        ref = profile["weak_points"][0]["source_question_refs"][0]
        ref.update(task_evidence_version_matches=True, assessment={"granularity": "part", "eligible": True,
            "point_observations": [{"point_id": "failed", "achieved": 0}]})
        ref["practice_observations_by_key"] = {BNU_TARGET: [{"response_mode": "exact_objective", "observable": text,
            "evidence_points": [{"evidence_point_id": "failed", "target": text}]}]}
    candidates = [_selection_candidate(qid, "合成根任务", difficulty=3,
        practice_observations_by_key={BNU_TARGET: [{"response_mode": "exact_objective", "observable": observed}]})
        for qid, observed in ((1, "计算9的算术平方根为3"), (2, text))]
    items = _selection_draft(monkeypatch, candidates, diagnosis, remediation_only=True)["students"][0]["items"]
    assert [item["question_id"] for item in items] == [2]
    assert items[0]["task_evidence_level"] == "observed_step"


def test_opaque_objective_result_remains_practice_without_claiming_full_task(monkeypatch):
    diagnosis = _direct_diagnosis()
    for profile in diagnosis["students"]:
        ref = profile["weak_points"][0]["source_question_refs"][0]
        ref["practice_observations_by_key"] = {BNU_TARGET: [{"response_mode": "exact_objective", "observable": "作答为B"}]}
    candidate = _selection_candidate(1, "合成有效客观题", difficulty=3,
        practice_observations_by_key={BNU_TARGET: [{"response_mode": "exact_objective", "observable": "作答为A"}]})
    item = _selection_draft(monkeypatch, [candidate], diagnosis, remediation_only=True)["students"][0]["items"][0]
    assert item["practice_purpose"] == "remediation"
    assert item["practice_role"] == "step_practice"
    assert item["task_evidence_level"] == "target_only"
    assert "不能确认完整任务覆盖" in item["reason"]
    assert "原任务信息不足" in item["reason"]


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
    advanced = _difficulty_plan({}, .923, 8, unknown, profile, new_practice=True)
    assert control["aim"] == 2.7
    assert advanced["aim"] > 4.5
    assert advanced["baseline_aim"] == control["aim"]
    assert "整体得分率浮动" in advanced["basis"]
    assert _difficulty_plan({}, .5, 8, unknown, profile, new_practice=True)["aim"] < control["aim"]
    assert _difficulty_plan({}, None, 8, unknown, profile, new_practice=True)["aim"] == control["aim"]
    sparse = {"weak_points": [{"knowledge_key": profile["weak_points"][0]["knowledge_key"], "source_question_refs": refs[-1:]}]}
    assert "较难题" not in _difficulty_plan({}, .923, 8, unknown, sparse, new_practice=True)["basis"]
    own_loss = {**unknown, "source_question_refs": [{**refs[0], "score_awarded": 1}]}
    assert _difficulty_plan({}, .923, 8, own_loss, profile, new_practice=True) == _difficulty_plan({}, .923, 8, own_loss, profile)
    coarse = {"weak_points": [{**profile["weak_points"][0], "source_question_refs": [
        {**r, "assessment": {"granularity": "whole_question"}} for r in refs]}]}
    assert "较难题" not in _difficulty_plan({}, .923, 8, unknown, coarse, new_practice=True)["basis"]
    assert _difficulty_plan({}, None, 8, unknown, {}, new_practice=True)["maximum"] == 3
    assert _difficulty_plan({}, .99, 4, unknown, profile, new_practice=True)["maximum"] == 4
    absent_score = {**unknown, "source_question_refs": [{**refs[0], "score_awarded": None}]}
    assert _difficulty_plan({}, .923, 8, absent_score, profile, new_practice=True)["evidence_count"] == 0
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
    def export(_db, ids, destination, **_):
        exported.append(ids)
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
    checked = {**entry(3, 4.5, 3), "practice_purpose": "new", "target": {
        "related_task_observed": True, "difficulty_plan": {"minimum": 1, "maximum": 10,
            "audit_minimum": 1, "audit_maximum": 3}}}
    new_audit = common_audit([(new, [new])], [checked], {}, set(), None)
    assert new_audit["items"][0]["window"] == [2.5, 6]
    assert new_audit["items"][0]["level"] == "within"
    assert new_audit["items"][0]["new_task_state"] == "related"
    assert new_audit["items"][0]["new_target_keys"] == [BNU_TARGET]
    assert aggregate([new_audit])["new_related_task_slots"] == 1
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
