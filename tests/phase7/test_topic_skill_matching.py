import hashlib
import json
from copy import deepcopy

import pytest

from question_bank.recommendation.target_matching import match_target


INDEX = {"sk_test": {"kind": "skill", "chapter": "chapter", "section": "section"}}


def facet(
    *,
    skills=("sk_test",),
    topics=("topic",),
    section="section",
    chapter="chapter",
    part="p1",
):
    return {
        "part_id": part,
        "direct_keys": list(skills),
        "skill_keys": list(skills),
        "topic_keys": list(topics),
        "section_keys": [section],
        "chapter_keys": [chapter],
    }


def test_four_levels_and_no_unrelated_filler():
    source = [facet()]
    assert match_target("sk_test", source, [facet()], INDEX)["match_level"] == 1
    assert (
        match_target("sk_test", source, [facet(topics=("other-topic",))], INDEX)[
            "match_level"
        ]
        == 2
    )
    assert (
        match_target("sk_test", source, [facet(skills=("sk_other",))], INDEX)[
            "match_level"
        ]
        == 3
    )
    assert (
        match_target(
            "sk_test",
            source,
            [facet(skills=("sk_other",), topics=("other-topic",))],
            INDEX,
        )["match_level"]
        == 4
    )
    assert (
        match_target(
            "sk_test",
            source,
            [
                facet(
                    skills=("sk_other",),
                    topics=("other-topic",),
                    section="elsewhere",
                    chapter="elsewhere",
                )
            ],
            INDEX,
        )
        is None
    )


SKILL = "sk_bnu24_math_g8_upper_3_3_201"
OTHER_SKILL = "sk_bnu24_math_g8_upper_3_3_202"
TOPIC = "kp_bnu24_math_g8_upper_3_3_1"
OTHER_TOPIC = "kp_bnu24_math_g8_upper_3_3_2"


@pytest.fixture
def current_link_module(tmp_path):
    from question_bank.database.schema import connect, initialize_database
    from question_bank.recommendation.personalized import (
        PersonalizedRecommendationModule,
    )
    from question_bank.training_criteria import QuestionAnalysisInputLoader
    from question_bank.training_criteria.analysis import (
        solution_evidence_source_content_hash,
    )
    from question_bank.solution_evidence.knowledge_links import replace_point_links
    from question_bank.taxonomy.curriculum_catalog import curriculum_volume
    from tests.current_knowledge_support import install_current_knowledge
    from tests.phase4.test_personalized_recommendation import (
        _approve_synthetic_criteria,
    )

    db = tmp_path / "bank.db"
    initialize_database(db)
    release_id = install_current_knowledge(db, taxonomy_revision=8)
    volume = curriculum_volume(volume_id="bnu24-math-g8-upper")
    stems = [
        "合成原题：观察图象中的点并写出坐标",
        "合成练习：利用位置关系确定变量的取值",
        "合成基础题：从表格中读取数值",
        "合成难题：推导一般表达式",
        "合成超进度题：建立新的方程模型",
        "合成练习：利用位置关系确定变量的取值",
        "合成近期题：比较两个观测量的变化",
    ]
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO papers(id,title,import_status,grade,semester,textbook_version) VALUES(1,'合成题库','success',?,?,?)",
            (volume["grade"], volume["semester"], volume["textbook_version"]),
        )
        for qid, stem in enumerate(stems, 1):
            difficulty = {1: 6, 3: 2, 4: 8}.get(qid, 5)
            conn.execute(
                "INSERT INTO questions(id,paper_id,question_number,question_type,question_text,answer_text,difficulty) VALUES(?,1,?,'填空题',?,'合成答案',?)",
                (qid, str(qid), stem, str(difficulty)),
            )
    _approve_synthetic_criteria(db, tmp_path, tuple(range(1, 8)))
    inputs = QuestionAnalysisInputLoader(db_path=db, data_root=tmp_path).load(
        tuple(range(1, 8))
    )
    with connect(db) as conn:
        for question in inputs:
            qid = question.question_id
            semantic = hashlib.sha256(f"semantic:{qid}".encode()).hexdigest()
            stored = hashlib.sha256(f"storage:{qid}:{release_id}".encode()).hexdigest()
            evidence = {
                "version_id": semantic,
                "question_id": qid,
                "source_content_hash": solution_evidence_source_content_hash(question),
                "parts": [
                    {
                        "part_id": "part1",
                        "response_mode": "exact_objective",
                        "evidence_points": [
                            {"evidence_point_id": "point1", "fine_term_links": []}
                        ],
                    }
                ],
            }
            conn.execute(
                """INSERT INTO question_solution_evidence_versions(evidence_version_id,question_id,
                source_content_hash,schema_version,content_hash,evidence_json,status,source_kind,source_reference,created_by,graph_release_id)
                VALUES(?,?,?,'question-solution-evidence-v2',?,?,'approved','backfill',?,'synthetic',?)""",
                (
                    stored,
                    qid,
                    evidence["source_content_hash"],
                    semantic,
                    json.dumps(evidence),
                    f"synthetic:{qid}",
                    release_id,
                ),
            )
            row = conn.execute(
                "SELECT version_id,criteria_json FROM training_criterion_versions WHERE question_id=?",
                (qid,),
            ).fetchone()
            criterion = json.loads(row["criteria_json"])
            criterion["solution_evidence"] = evidence
            conn.execute(
                "UPDATE training_criterion_versions SET criteria_json=? WHERE version_id=?",
                (json.dumps(criterion), row["version_id"]),
            )
            topic = "kp_bnu24_math_g8_upper_5_1_1" if qid == 5 else TOPIC
            replace_point_links(
                conn,
                evidence_version_id=stored,
                question_id=qid,
                graph_release_id=release_id,
                points=[
                    {
                        "part_id": "part1",
                        "evidence_point_id": "point1",
                        "links": [
                            {"term_id": key, "role": "direct"} for key in (SKILL, topic)
                        ],
                    }
                ],
            )
        # Reproduce an upgraded bank whose coarse tags still contain only topics.
        conn.execute(
            "DELETE FROM question_tags WHERE tag_type='knowledge_point' AND tag_value=?",
            (SKILL,),
        )
        conn.execute(
            "DELETE FROM question_tags WHERE question_id=2 AND tag_type='knowledge_point'"
        )
    return PersonalizedRecommendationModule(db_path=db, data_root=tmp_path)


def test_whole_question_topics_still_block_future_chapters_after_skill_refinement(
    current_link_module,
):
    from dataclasses import replace
    from question_bank.database.schema import connect
    from question_bank.recommendation.personalized import (
        PersonalizedRecommendationConfig,
        _question_scope_allowed,
        _allowed_keys_for_config,
    )

    module = current_link_module
    future = "kp_bnu24_math_g8_upper_5_1_1"
    with connect(module.db_path) as conn:
        conn.execute(
            "INSERT INTO question_tags(question_id,tag_type,tag_value,source) VALUES(2,'knowledge_point',?,'synthetic')",
            (future,),
        )
    candidates, _, _ = module._source_snapshot()
    candidate = next(q for q in candidates if q["question_id"] == 2)
    assert future in candidate["required_keys"]
    assert future not in candidate["stable_keys"]
    assert future not in candidate["target_facets"][0]["topic_keys"]
    config = PersonalizedRecommendationConfig(
        curriculum_volume_id="bnu24-math-g8-upper",
        target_keys=(SKILL,),
        teaching_progress_chapter_id="bnu24-math-g8-upper-c03",
    )
    assert not _question_scope_allowed(
        candidate,
        config,
        _allowed_keys_for_config(config, module.current_knowledge),
        module.current_knowledge,
    )
    later = replace(config, teaching_progress_chapter_id="bnu24-math-g8-upper-c05")
    assert _question_scope_allowed(
        candidate,
        later,
        _allowed_keys_for_config(later, module.current_knowledge),
        module.current_knowledge,
    )


@pytest.mark.parametrize("paper_mode", ["individual", "shared"])
def test_new_skill_links_generate_and_persist_personal_and_shared_drafts(
    current_link_module, monkeypatch, paper_mode
):
    from question_bank.recommendation.personalized import (
        PersonalizedRecommendationConfig,
    )
    from tests.phase4.test_personalized_recommendation import _direct_diagnosis

    module = current_link_module
    diagnosis = _direct_diagnosis(
        (("synthetic-A", 0.6, 1, SKILL), ("synthetic-B", 0.7, 1, SKILL))
    )
    monkeypatch.setattr(module, "current_exam_question_ids", lambda diagnosis: {1})
    monkeypatch.setattr(
        module,
        "_recent_question_ids",
        lambda student_ids, **kwargs: {sid: {7} for sid in student_ids},
    )
    config = PersonalizedRecommendationConfig(
        paper_mode=paper_mode,
        target_keys=(SKILL,),
        question_count=8,
        curriculum_volume_id="bnu24-math-g8-upper",
        teaching_progress_chapter_id="bnu24-math-g8-upper-c03",
    )
    request = dict(
        request_token=("1" if paper_mode == "individual" else "2") * 32,
        diagnosis=diagnosis,
        config=config,
        actor_ref="synthetic",
    )
    draft = module.create(**request)
    assert (
        module.create(**request) == draft
    )  # Stored draft reload uses the original request.
    assert len(draft["students"]) == 2
    for student in draft["students"]:
        assert [item["question_id"] for item in student["items"]] == [2]
        assert student["shortages"][0]["missing_count"] == 7
        if paper_mode == "shared":
            assert set(student["items"][0]["beneficiary_student_ids"]) == {
                "synthetic-A",
                "synthetic-B",
            }
