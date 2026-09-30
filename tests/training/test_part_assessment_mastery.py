import json
from datetime import UTC, datetime

import pytest

from question_bank.mastery.v2 import compute_mastery_v2
from question_bank.solution_evidence.knowledge_links import links_from_embedded


def _insert_feature_rows(conn, question_id, parts, *, fingerprint=None):
    """Write active formula difficulty rows: [(part_id, difficulty), ...]."""
    from question_bank.services.standard_difficulty import question_content_fingerprint

    if fingerprint is None:
        row = conn.execute(
            "SELECT * FROM questions WHERE id=?", (int(question_id),)
        ).fetchone()
        fingerprint = question_content_fingerprint(dict(row))
    for part_id, difficulty in parts:
        conn.execute(
            """INSERT INTO question_part_difficulty_features
               (question_id, part_id, features_json, formula_difficulty,
                formula_version, source_content_hash, is_active)
               VALUES (?,?,?,?,?,?,1)""",
            (
                int(question_id),
                str(part_id),
                json.dumps({"evidence": "合成难度依据"}),
                float(difficulty),
                "std-difficulty-v1",
                fingerprint,
            ),
        )


@pytest.fixture()
def refined_training_source(tmp_path):
    from copy import deepcopy
    from question_bank.database.schema import initialize_database, connect
    from question_bank.solution_evidence.contracts import (
        CoreResolution,
        QuestionSolutionEvidence,
    )
    from question_bank.solution_evidence.repository import SolutionEvidenceRepository
    from question_bank.solution_evidence.part_assessments import load_profiles
    from question_bank.training_criteria import (
        QuestionAnalysisInputLoader,
        TrainingCriterionModule,
    )
    from question_bank.training_criteria.analysis import (
        solution_evidence_source_content_hash,
        training_criteria_from_solution_evidence,
    )
    from tests.current_knowledge_support import install_current_knowledge
    from tests.training.test_solution_evidence_semantics import _evidence_payload

    root = tmp_path / "data"
    path = root / "databases" / "question_bank.db"
    initialize_database(path)
    install_current_knowledge(path)
    with connect(path) as conn:
        conn.execute(
            "INSERT INTO papers(id,title,import_status) VALUES(1,'合成试卷','ready')"
        )
        conn.execute(
            "INSERT INTO questions(id,paper_id,question_number,question_type,question_text,answer_text,difficulty) VALUES(1,1,'1','解答题','合成第一问及第二问','合成答案',2)"
        )
        conn.execute(
            "INSERT INTO question_tags(question_id,tag_type,tag_value,source) VALUES(1,'knowledge_point','kp_geo_construction','synthetic')"
        )
        conn.execute(
            "INSERT INTO grading_question_links(grading_session_id,source_question_id,bank_question_id,link_method,status) VALUES('1','Q1',1,'manual','confirmed')"
        )
    from question_bank.services.rich_content_service import save_question_rich_content

    save_question_rich_content(
        1,
        root=root / "question_bank" / "rich_content",
        question_blocks=[{"type": "text", "text": "合成题目的完整排版正文"}],
    )
    question = QuestionAnalysisInputLoader(db_path=path, data_root=root).load((1,))[0]

    class Resolver:
        def resolve(self, key):
            return CoreResolution(
                status="resolved", stable_keys=(key,), reason="synthetic"
            )

    raw = _evidence_payload(1)
    raw["parts"][0]["evidence_points"][0]["fine_term_links"] = [
        {
            "fine_term_id": "kp_alg_linear_equation",
            "fine_term_name": "一元一次方程",
            "role": "direct",
        }
    ]
    second = deepcopy(raw["parts"][0])
    second.update(part_id="part-2", label="第2问", full_answer="证明三角形全等")
    second["evidence_points"][0].update(
        evidence_point_id="step-2",
        target="证明全等",
        observable_evidence="列出全等条件",
        fine_term_links=[
            {
                "fine_term_id": "kp_geo_triangle_congruence",
                "fine_term_name": "三角形全等",
                "role": "direct",
            }
        ],
    )
    raw["parts"].append(second)
    old = deepcopy(raw)
    old["parts"][1]["evidence_points"][0]["target"] = "旧的判定目标"
    make = lambda payload: QuestionSolutionEvidence.from_model_dict(
        payload,
        question_id=1,
        source_content_hash=solution_evidence_source_content_hash(question),
        resolver=Resolver(),
    )
    criteria = TrainingCriterionModule(path)
    criteria.propose(
        question=question,
        draft=training_criteria_from_solution_evidence(make(old), question=question),
        source_kind="backfill",
        source_reference="synthetic-old",
        actor_ref="test",
        reason="synthetic",
    )
    old_version = criteria.read(question)["current_version"]["version_id"]
    evidence = make(raw)
    version = SolutionEvidenceRepository(path).save(
        evidence,
        source_kind="backfill",
        source_reference="synthetic-refined",
        created_by="test",
    )
    with connect(path) as conn:
        _insert_feature_rows(conn, 1, [("part-1", 2), ("part-2", 8)])
    return path, root, question, evidence, load_profiles(path, [1])[1], old_version


def test_refined_recommendation_freezes_current_criteria_and_returns_part_evidence(
    refined_training_source,
):
    from question_bank.database.schema import connect
    from question_bank.recommendation.personalized import (
        PersonalizedRecommendationModule,
        PersonalizedRecommendationConfig,
        _draft_item,
    )
    from question_bank.training_criteria import (
        TrainingCriterionModule,
        usable_training_criterion,
    )
    from question_bank.personalized_papers.module import PersonalizedPaperModule
    from question_bank.solution_evidence.part_assessments import (
        training_part_observations,
    )

    path, root, question, evidence, profile, old_version = refined_training_source
    criteria = TrainingCriterionModule(path)
    assert criteria.read(question)["state"] == "part_evidence_changed"
    assert usable_training_criterion(criteria.read(question)) is None
    with connect(path) as conn:
        assert (
            conn.execute("SELECT COUNT(*) FROM training_criterion_versions").fetchone()[
                0
            ]
            == 1
        )
    module = PersonalizedRecommendationModule(db_path=path, data_root=root)
    candidates, _, version = module._source_snapshot(prepare_refinements=True)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["difficulty"] == 8  # Previously the whole question was tagged 2.
    assert set(candidate["stable_keys"]) == {
        "kp_alg_linear_equation",
        "kp_geo_triangle_congruence",
    }
    assert candidate["criterion_version_id"] != old_version
    assert (
        criteria.get_version(old_version)["criteria"]["points"][1]["target"]
        == "旧的判定目标"
    )
    assert module._source_snapshot()[2] == version
    assert module._source_snapshot(prepare_refinements=True)[2] == version
    eligible = module._eligible_candidates(
        candidates,
        stage="direct",
        target_keys=("kp_alg_linear_equation",),
        maintenance=False,
        used=set(),
        recent=set(),
        excluded=set(),
        config=PersonalizedRecommendationConfig(difficulty_max=5),
    )
    assert eligible == []
    item = _draft_item(
        candidate,
        stage="direct",
        slot=1,
        student_id="1",
        target={},
        matched_key="kp_alg_linear_equation",
        maintenance=True,
    )
    frozen = PersonalizedPaperModule(db_path=path, data_root=root)._prepare_items(
        {"items": [item]}, paper_instance_id="a" * 32
    )[0]
    assert (
        frozen["recommendation_snapshot"]["part_assessment"]["parts"][1]["difficulty"]
        == 8
    )
    points = frozen["criterion_snapshot"]["criteria"]["points"]
    observed = training_part_observations(
        profile,
        frozen["criterion_snapshot"]["criteria"],
        [
            {"point_id": point["point_id"], "state": "met" if index == 0 else "not_met"}
            for index, point in enumerate(points)
        ],
        links_from_embedded(profile["evidence"]),
    )
    assert [(o["stable_key"], o["achieved"], o["difficulty"]) for o in observed] == [
        ("kp_alg_linear_equation", 1, 2),
        ("kp_geo_triangle_congruence", 0, 8),
    ]
    from tests.training.test_personalized_recommendation import _diagnosis

    # The source exam is older than the three latest graded activities.
    diagnosis = _diagnosis(student_ids=("SYN-S01",))
    graded_activities = [{"student_id": "SYN-S01", "session_id": i,
                          "occurred_at": f"2026-09-0{i}"} for i in (2, 3, 4)]
    diagnosis["students"][0]["weak_points"][0]["source_question_refs"][0][
        "question_difficulty"
    ] = 8
    # The loss stays recorded (8 < 10) while the readiness plan can reach the
    # difficulty-8 candidate; a 40% score would cap the plan at foundation.
    diagnosis["students"][0]["weak_points"][0]["source_question_refs"][0][
        "score_awarded"
    ] = 8
    draft = module.create(
        request_token="a" * 32,
        actor_ref="test",
        diagnosis=diagnosis,
        graded_activities=graded_activities,
        config=PersonalizedRecommendationConfig(
            question_count=8,
            expected_minutes=45,
            difficulty_min=8,
            difficulty_max=8,
            training_intent="challenge",
            target_keys=("kp_alg_linear_equation",),
            exclude_current_exam_originals=False,
        ),
    )
    reopened = module.get(draft["draft_id"])
    assert len(reopened["students"][0]["items"]) == 1
    assert (
        reopened["students"][0]["items"][0]["part_assessment"]
        == item["part_assessment"]
    )


def _part(name, key):
    return {
        "part_id": name,
        "response_mode": "constructed",
        "evidence_points": [
            {
                "evidence_point_id": name + "-step-1",
                "target": name,
                "observable_evidence": "show " + name,
                "fine_term_links": [
                    {
                        "role": "direct",
                        "core_resolution": {"status": "resolved", "stable_keys": [key]},
                    },
                    {
                        "role": "supporting_prerequisite",
                        "core_resolution": {
                            "status": "resolved",
                            "stable_keys": ["kp_aux"],
                        },
                    },
                ],
            }
        ],
    }


def test_new_analysis_survives_checkpoint_and_linked_adoption(tmp_path, monkeypatch):
    from question_bank.solution_evidence import part_assessments as profiles
    from question_bank.solution_evidence import (
        FineTermCoreMappingRepository,
        SolutionEvidenceRepository,
    )
    from question_bank.training_criteria import (
        DeferredCombinedQuestionAnalysisModule,
        ConfigQuestionAnalysisSource,
        DeferredCombinedAnalysisBundle,
        DeferredCombinedProjectionWriter,
        ConfirmedQuestionAdoptionLink,
    )
    from tests.training.test_solution_evidence_semantics import (
        _question,
        _combined_payload,
        QueueGateway,
        Resolver,
        VOLUME_ID,
        SuccessfulTagWriter,
    )
    from question_bank.database.schema import initialize_database, connect

    question = _question(1, source_ref="Q1")
    payload = _combined_payload(1)
    raw = payload["results"][0]
    # Legacy model payloads may still carry part_assessments; the key is
    # accepted and ignored — per-part difficulty now comes from formula rows.
    raw["part_assessments"] = [
        {
            "part_id": p["part_id"],
            "difficulty": 3,
            "rationale": "synthetic reasoning demand",
        }
        for p in raw["solution_evidence"]["parts"]
    ]
    bundle = DeferredCombinedQuestionAnalysisModule(
        gateway=QueueGateway([payload]), resolver=Resolver()
    ).analyze(
        operation_id="synthetic-parts",
        curriculum_volume_id=VOLUME_ID,
        sources=(ConfigQuestionAnalysisSource("Q1", question),),
    )
    assert bundle.status == "succeeded"
    checkpoint = bundle.to_dict()
    assert (
        checkpoint["items"][0]["schema_version"] == "deferred-combined-analysis-item-v4"
    )
    assert "part_assessments" not in checkpoint["items"][0]
    restored = DeferredCombinedAnalysisBundle.from_dict(checkpoint, resolver=Resolver())
    assert restored.to_dict() == checkpoint
    # A stored v5 checkpoint that still carries part_assessments loads cleanly.
    from question_bank.training_criteria.combined_analysis import _hash_payload

    legacy_item = {
        k: v for k, v in checkpoint["items"][0].items() if k != "content_hash"
    }
    legacy_item["schema_version"] = "deferred-combined-analysis-item-v5"
    legacy_item["part_assessments"] = raw["part_assessments"]
    legacy_item["content_hash"] = _hash_payload(legacy_item)
    legacy_bundle = {k: v for k, v in checkpoint.items() if k != "content_hash"}
    legacy_bundle["items"] = [legacy_item]
    legacy_bundle["content_hash"] = _hash_payload(legacy_bundle)
    legacy_restored = DeferredCombinedAnalysisBundle.from_dict(
        legacy_bundle, resolver=Resolver()
    )
    assert "part_assessments" not in legacy_restored.to_dict()["items"][0]
    path = tmp_path / "question_bank.db"
    initialize_database(path)
    with connect(path) as conn:
        conn.execute(
            "INSERT INTO questions(id,question_number,question_text) VALUES(1,'1','synthetic')"
        )
        _insert_feature_rows(
            conn, 1, [(p["part_id"], 3) for p in raw["solution_evidence"]["parts"]]
        )
    monkeypatch.setattr(
        profiles, "current_inputs", lambda *args, **kwargs: {1: question}
    )
    writer = DeferredCombinedProjectionWriter(
        tag_writer=SuccessfulTagWriter(),
        mapping_repository=FineTermCoreMappingRepository(path),
        evidence_repository=SolutionEvidenceRepository(path),
    )
    result = writer.adopt_linked(
        restored.get("Q1"),
        question=question,
        link=ConfirmedQuestionAdoptionLink(
            source_question_ref="Q1", bank_question_id=1, confirmed_by="test"
        ),
    )
    assert result["evidence_status"] == "succeeded"
    saved = profiles.load_profiles(path, [1])[1]
    assert saved["available"]
    assert saved["parts"][0]["difficulty"] == 3
    assert saved["parts"][0]["source"] == "formula"
    assert saved["evidence_version_id"] == result["source_evidence_version_id"]
    from question_bank.solution_evidence.repository import (
        SolutionEvidenceProjectionWriter,
    )

    direct_writer = SolutionEvidenceProjectionWriter(
        mapping_repository=Resolver(),
        evidence_repository=SolutionEvidenceRepository(path),
    )
    direct_writer.write(
        question,
        raw["solution_evidence"],
        model_name="synthetic",
        operation_id="direct-parts",
    )
    assert profiles.load_profiles(path, [1])[1]["parts"][0]["difficulty"] == 3
