import json
from datetime import UTC, datetime

import pytest

from question_bank.solution_evidence.knowledge_links import links_from_embedded


def _model_observations():
    from question_bank.mastery.model import week_of
    date = datetime(2026, 9, 7, tzinfo=UTC)
    return [dict(student=student, item=(session, q), activity=("exam", session),
                 source="exam", occurred_at=date, week=week_of(date)+session,
                 d=3.1, y=y, links={"leaf": 1.})
            for student, y in (("high", 1.), ("low", 0.), ("middle", .5))
            for session in range(3) for q in range(6)]


def _fit_eager_mastery(monkeypatch, model, observations):
    from question_bank.mastery import model as mastery_model
    solve = mastery_model.lbfgs

    def eager(objective, initial, iterations=600, memory=10, *, loss=None):
        assert loss is not None
        return solve(objective, initial, iterations, memory)

    with monkeypatch.context() as context:
        context.setattr(mastery_model, "lbfgs", eager)
        return model.fit(observations)


def _assert_mastery_models_equal(current, reference, observations):
    import numpy as np
    assert current.index == reference.index
    assert current.rows == reference.rows
    assert current.state_weeks == reference.state_weeks
    assert np.array_equal(current.x, reference.x)
    assert np.array_equal(current.prec, reference.prec)
    assert current.cov.keys() == reference.cov.keys()
    for student in current.cov:
        current_position, current_cov = current.cov[student]
        reference_position, reference_cov = reference.cov[student]
        assert current_position == reference_position
        assert np.array_equal(current_cov, reference_cov)
    week = max(observation["week"] for observation in observations)
    nodes = set(current.parent) | set(current.parent.values())
    for student in {observation["student"] for observation in observations}:
        for node in nodes:
            for when in (week, week + 10):
                assert current.mastery(student, node, when) == reference.mastery(student, node, when)
                assert current.result(student, node, when) == reference.result(student, node, when)
    for observation in observations:
        assert current.predict(observation) == reference.predict(observation)


def test_hierarchical_model_static_reproducible_and_zero_time_drift(monkeypatch):
    import hashlib
    from dataclasses import asdict, replace
    from question_bank.mastery.model import MasteryModel, MasteryParameters, classify
    parent = {"leaf": "section", "section": "chapter"}
    observations = _model_observations()
    model = MasteryModel(parent).fit(observations)
    repeat = _fit_eager_mastery(monkeypatch,
        MasteryModel(parent, MasteryParameters(tau_theta=0, tau_kc=0)), list(reversed(observations)))
    _assert_mastery_models_equal(model, repeat, observations)
    for student in ("high", "low", "middle"):
        mean, sd = model.mastery(student, "leaf", 20000)
        assert (mean, sd) == repeat.mastery(student, "leaf", 20000)
        assert (mean, sd) == model.mastery(student, "leaf", 30000)
    assert classify(*model.mastery("high", "leaf", 20000)) == "stable"
    assert classify(*model.mastery("low", "leaf", 20000)) == "weak"
    assert ("gamma",) not in model.index
    # Parent results use the same latent model, with no recomputed score ratio.
    assert model.result("high", "chapter", 20000)["value"] > model.result("low", "chapter", 20000)["value"]
    parameters = MasteryParameters()
    variants = [parameters, replace(parameters, formula_version="TEST-formula"),
                replace(parameters, slip=.02, tau_theta=.15, tau_kc=.2),
                *[replace(parameters, sigma_theta=value) for value in (True, 1, 1.0)]]
    for variant in variants:
        serialized = asdict(variant)
        expected = hashlib.sha256(json.dumps(serialized, sort_keys=True).encode()).hexdigest()
        assert variant.version == variant.version == expected
        assert asdict(variant) == serialized
        assert "version" not in asdict(variant)
        cloned = replace(variant)
        assert cloned is not variant
        assert asdict(cloned) == serialized and cloned.version == expected
    assert variants[-3] == variants[-2] == variants[-1]
    assert len({variant.version for variant in variants[-3:]}) == 3
    assert len({variant.version for variant in variants[:3]}) == 3


def test_hierarchical_time_walk_widens_interval_and_handles_deep_paths(monkeypatch):
    from question_bank.mastery.model import MasteryModel, MasteryParameters
    observations = _model_observations()
    parent = {"leaf": "extra", "extra": "section", "section": "chapter"}
    parameters = MasteryParameters(tau_theta=.15, tau_kc=.2, slip=.02)
    model = MasteryModel(parent, parameters).fit(observations)
    eager = _fit_eager_mastery(monkeypatch, MasteryModel(parent, parameters), observations)
    _assert_mastery_models_equal(model, eager, observations)
    week = max(o["week"] for o in observations)
    mean, sd = model.mastery("high", "leaf", week)
    later_mean, later_sd = model.mastery("high", "leaf", week+10)
    assert later_mean == mean
    assert later_sd > sd
    assert later_sd**2-sd**2 == pytest.approx(10*(.15**2+.2**2))
    result = model.result("high", "leaf", week)
    assert 0 <= result["interval_low"] <= result["value"] <= result["interval_high"] <= .98


@pytest.mark.parametrize("reject_all", [False, True])
def test_lbfgs_scalar_loss_skips_rejected_gradients_with_identical_steps(reject_all):
    import numpy as np
    from question_bank.mastery.model import lbfgs
    initial = np.array([1., 2.])
    original = initial.copy()
    weights = np.array([1000., 1.])
    eager_points, screened_points, loss_points = [], [], []

    def evaluate(x):
        if reject_all:
            return 0., np.ones_like(x)
        return 0.5 * np.sum(weights * x*x), weights * x

    def eager(x):
        eager_points.append(x.copy())
        return evaluate(x)

    def screened(x):
        screened_points.append(x.copy())
        return evaluate(x)

    def loss(x):
        loss_points.append(x.copy())
        return 0. if reject_all else 0.5 * np.sum(weights * x*x)

    expected = lbfgs(eager, initial, iterations=1)
    actual = lbfgs(screened, initial, iterations=1, loss=loss)
    assert np.array_equal(actual, expected)
    assert np.array_equal(initial, original)
    assert len(eager_points) == len(loss_points) + 1
    assert len(screened_points) == (1 if reject_all else 2)
    assert len(loss_points) > len(screened_points)
    assert all(np.array_equal(before, after)
               for before, after in zip(eager_points[1:], loss_points, strict=True))
    if reject_all:
        assert len(loss_points) == 40
        assert np.array_equal(actual, initial)


def test_exam_observations_keep_point_weight_and_exclude_ineligible_missing():
    from types import SimpleNamespace
    from question_bank.mastery.model import build_exam_observations
    class Resolver:
        def resolve(self, key):
            return [SimpleNamespace(stable_key=key)]
    row = dict(student_id="synthetic", session_id=1, question_id="Q1", score_awarded=1, full_score=2,
               assessment={"part_difficulty": 3.1}, point_observations=[
                   dict(point_id="p1", stable_key="leaf", achieved=.5, weight=.1),
                   dict(point_id="p1", stable_key="second", achieved=.5, weight=.3)])
    result = build_exam_observations([row, {**row, "assessment": {"eligible": False}},
                                      {**row, "session_id": 2}], Resolver(), {"1": "2026-09-07"})
    assert len(result) == 1
    assert result[0]["y"] == .5
    assert result[0]["links"] == pytest.approx({"leaf": .25, "second": .75})


@pytest.mark.parametrize("time_slip", [(0., 0., 0.), (.15, .2, .02)])
def test_current_mastery_filters_after_fitting_and_counts_activity_trends(tmp_path, monkeypatch, time_slip):
    from datetime import timedelta
    from types import SimpleNamespace
    from question_bank.mastery.current import CurrentMasteryCalculator, aggregate_current_mastery
    from question_bank.mastery.model import MasteryParameters, week_of
    class Resolver:
        nodes = [SimpleNamespace(stable_key=k, display_name=k)
                 for k in ("leaf", "spare", "extra", "section", "chapter", "outside")]
        relations = [SimpleNamespace(source_key=k, target_key=p, relation_type="parent")
                     for k, p in (("leaf", "extra"), ("extra", "section"),
                                  ("spare", "section"), ("section", "chapter"))]
        def node(self, key):
            return next((n for n in self.nodes if n.stable_key == key), None)
    as_of = [datetime(2026, 10, 1, tzinfo=UTC)]
    parameters = MasteryParameters(tau_theta=time_slip[0], tau_kc=time_slip[1], slip=time_slip[2])
    calculator = CurrentMasteryCalculator(tmp_path / "synthetic.db", Resolver(),
        parameters=parameters, clock=lambda: as_of[0])
    monkeypatch.setattr(calculator, "training_observations", lambda **kwargs: {})
    observations = _model_observations()
    for observation in observations:
        if observation["student"] == "high":
            observation["y"] = float(observation["activity"][1] > 0)
        elif observation["student"] == "low":
            observation["y"] = float(observation["activity"][1] == 0)
    occurred = datetime(2026, 9, 28, tzinfo=UTC)
    observations.extend(dict(student="middle", item=("training", "TEST-task", point),
        activity=("training", "TEST-submission"), source="training", occurred_at=occurred,
        week=week_of(occurred), d=4., y=y, links={"leaf": .5, "TEST-frozen-skill": .5},
        display_names={"TEST-frozen-skill": "TEST 冻结技能"})
        for point, y in (("p1", 1.), ("p2", 0.)))
    profile = {"_mastery_observations": observations}
    values = calculator.calculate(profile)
    selected = calculator.calculate(profile, allowed_student_ids=frozenset({"high"}))
    assert list(values) == sorted(values)
    assert len(values) == 16
    assert not any(key == "outside" for _student, key in values)
    assert selected == {identity: value for identity, value in values.items() if identity[0] == "high"}
    assert selected["high", "extra"].observation_count == 18
    assert selected["high", "leaf"].recent_trend == "最近 2 次全对 ↑"
    assert values["low", "leaf"].recent_trend == "最近一次出错"
    assert selected["high", "chapter"].observation_count == 18
    assert selected["high", "chapter"].full_correct_count == 12
    assert selected["high", "spare"].tier == "insufficient"
    assert selected["high", "spare"].value is not None
    untested = selected["high", "spare"]
    assert untested.interval_low <= untested.value <= untested.interval_high
    assert untested.evidence_count == untested.observation_count == untested.full_correct_count == 0
    assert untested.exam_evidence_count == untested.training_evidence_count == 0
    assert untested.direct_evidence_count == untested.precise_training_evidence_count == 0
    assert untested.recent_trend is None
    assert untested.parameter_version == parameters.version
    assert values["middle", "leaf"].exam_evidence_count == 18
    assert values["middle", "leaf"].training_evidence_count == 1
    assert values["middle", "leaf"].precise_training_evidence_count == 2
    assert values["middle", "leaf"].direct_evidence_count == 20
    assert values["middle", "TEST-frozen-skill"].display_name == "TEST 冻结技能"
    assert values["middle", "TEST-frozen-skill"].observation_count == 2
    assert values["middle", "TEST-frozen-skill"].full_correct_count == 1
    group = aggregate_current_mastery(values)
    assert "spare" not in group
    assert group["leaf"].value == pytest.approx(sum(values[s, "leaf"].value for s in ("high", "middle", "low"))/3)
    assert sum(dict(group["leaf"].tier_counts).values()) == 3
    as_of[0] += timedelta(days=14)
    later = calculator.calculate(profile, allowed_student_ids=frozenset({"high"}))
    assert later["high", "spare"].logit_mean == untested.logit_mean
    if parameters.tau_theta:
        assert later["high", "spare"].logit_sd > untested.logit_sd
    else:
        assert later == selected
    next(relation for relation in calculator.resolver.relations
         if relation.source_key == "spare").target_key = "outside"
    updated = calculator.calculate(profile, allowed_student_ids=frozenset({"high"}))
    assert ("high", "spare") not in updated
    assert ("high", "leaf") in updated


def _insert_feature_rows(conn, question_id, parts, *, fingerprint=None):
    """Write active formula difficulty rows: [(part_id, difficulty), ...]."""
    from question_bank.services.standard_difficulty import _legacy_question_content_fingerprint

    if fingerprint is None:
        row = conn.execute(
            "SELECT * FROM questions WHERE id=?", (int(question_id),)
        ).fetchone()
        fingerprint = _legacy_question_content_fingerprint(dict(row))
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
    from tests.training.test_personalized_recommendation import _install_release_with_skills
    from tests.training.test_solution_evidence_semantics import _evidence_payload

    root = tmp_path / "data"
    path = root / "databases" / "question_bank.db"
    initialize_database(path)
    _install_release_with_skills(
        path,
        revision=3,
        skill_parents={
            "sk_alg_linear_equation": "kp_bnu24_math_g7_upper_5_2_1",
            "sk_geo_triangle_congruence": "kp_bnu24_math_g7_lower_4_3_9",
            "sk_geo_construction": "kp_bnu24_math_g7_lower_4_3_4",
            "sk_fun_linear": "kp_bnu24_math_g8_upper_4_2_2",
        },
    )
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
            "fine_term_id": "sk_alg_linear_equation",
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
                "fine_term_id": "sk_geo_triangle_congruence",
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
    assert criteria.read(question)["state"] == "available"
    assert criteria.freeze((question,))[0]["version_id"] != old_version
    with connect(path) as conn:
        assert (
            conn.execute("SELECT COUNT(*) FROM training_criterion_versions").fetchone()[
                0
            ]
            == 0
        )
    module = PersonalizedRecommendationModule(db_path=path, data_root=root)
    candidates, _, version = module._source_snapshot()
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["difficulty"] == 8  # Previously the whole question was tagged 2.
    assert set(candidate["stable_keys"]) == {
        "sk_alg_linear_equation",
        "sk_geo_triangle_congruence",
    }
    assert candidate["criterion_version_id"] != old_version
    from question_bank.training_criteria.versioning import CriterionVersionNotFound
    # The previous model draft had no teacher decision or historical reference.
    with pytest.raises(CriterionVersionNotFound):
        criteria.get_version(old_version)
    assert module._source_snapshot()[2] == version
    with connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM training_criterion_versions").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM question_solution_evidence_versions").fetchone()[0] == 1
    eligible = module._eligible_candidates(
        candidates,
        stage="direct",
        target_keys=("sk_alg_linear_equation",),
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
        matched_key="sk_alg_linear_equation",
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
        ("sk_alg_linear_equation", 1, 2),
        ("sk_geo_triangle_congruence", 0, 8),
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
            target_keys=("sk_alg_linear_equation",),
            curriculum_volume_id="bnu24-math-g7-lower",
            teaching_progress_chapter_id="bnu24-math-g7-lower-c04",
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


def test_published_training_keeps_saved_links_after_current_material_changes(tmp_path, monkeypatch):
    import sqlite3
    from types import SimpleNamespace
    from question_bank.mastery.current import CurrentMasteryCalculator
    from question_bank.solution_evidence import part_assessments, knowledge_links
    key = "kp_bnu24_math_g8_lower_1_1_t01"
    old_key = "kp_bnu24_math_g8_lower_1_1_1"
    path = tmp_path / "TEST-frozen-training.db"
    frozen = {"points": [{"point_id": pid, "target": "冻结要求", "observable_evidence": "冻结作答", "depends_on": []}
                         for pid in ("p1", "p2")], "solution_evidence": {"source_content_hash": "same-source"}}
    source = json.dumps({"bank_question_id": 1})
    states = json.dumps([{"point_id": "p1", "state": "met"}, {"point_id": "p2", "state": "not_met"}])
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE training_evidence_records(evidence_id,student_id,stable_key,occurred_at,status,
                source_json,criterion_version_id,criterion_hash,task_item_code,final_points_json,submission_id,submission_revision);
            CREATE TABLE training_criterion_versions(version_id,question_id,criteria_json,criteria_hash);
            CREATE TABLE question_tags(question_id,tag_type,tag_value,id);
        """)
        conn.execute("INSERT INTO training_evidence_records VALUES('e1','TEST-student',?,'2026-10-01','active',?,'v1','hash','task',?,'submission',1)", (old_key, source, states))
        conn.execute("INSERT INTO training_evidence_records VALUES('e2','TEST-student',?,'2026-10-01','active',?,'v1','hash','task',?,'submission',1)", (old_key, source, states))
        conn.execute("INSERT INTO training_criterion_versions VALUES('v1',1,?,'hash')", (json.dumps(frozen),))
        conn.execute("INSERT INTO question_tags VALUES(1,'knowledge_point',?,1)", (key,))
    before = path.read_bytes()
    profile = {"question_id": 1, "available": True, "evidence_version_id": "current",
               "current_source_content_hash": "same-source", "evidence_source_hash": "same-source",
               "evidence": {"parts": [{"part_id": "new-part", "evidence_points": [{"evidence_point_id": "new-point"}]}]}}
    monkeypatch.setattr(part_assessments, "load_profiles", lambda *_args, **_kwargs: {1: profile})
    monkeypatch.setattr(knowledge_links, "load_point_links", lambda *_args, **_kwargs: {})
    resolver = SimpleNamespace(release_id="TEST-training-current-type", taxonomy_revision=11,
        nodes=[SimpleNamespace(stable_key=key)], relations=[],
        resolve=lambda value: [SimpleNamespace(stable_key=key)] if value == old_key else [])
    observed = CurrentMasteryCalculator(path, resolver).training_observations(
        exclude_evidence_ids=frozenset(), allowed_student_ids=None)
    assert set(observed) == {("TEST-student", key)}
    assert [(item.achieved_points, item.total_points) for item in observed["TEST-student", key]] == [(1, 1), (0, 1)]
    assert path.read_bytes() == before
    profile["current_source_content_hash"] = profile["evidence_source_hash"] = "changed-source"
    rejected = CurrentMasteryCalculator(path, resolver).training_observations(
        exclude_evidence_ids=frozenset(), allowed_student_ids=None)
    assert rejected == observed
    assert path.read_bytes() == before


@pytest.mark.parametrize("candidate_order,changed,selected_kind,expected_alias", [
    (("current", "legacy_type"), False, "current", ""),
    (("legacy_type", "current"), False, "legacy_type", "解答题（计算）"),
    (("stale", "legacy_type", "current"), False, "legacy_type", "解答题（计算）"),
    (("legacy_format", "current"), False, "legacy_format", ""),
    (("current", "legacy_type"), True, "current", ""),
])
def test_profile_source_checks_keep_candidate_order_and_complete_persisted_hashes(
    tmp_path, monkeypatch, candidate_order, changed, selected_kind, expected_alias,
):
    from dataclasses import replace
    from question_bank.database.schema import connect, initialize_database
    from question_bank.solution_evidence import part_assessments
    from question_bank.training_criteria import analysis
    from tests.training.test_combined_question_analysis import _question

    path = tmp_path / "TEST-profile-source.db"
    initialize_database(path)
    question = replace(_question(1, question_type="解答题"), taxonomy_contract={})
    hashes = part_assessments._input_source_hashes(question)
    fingerprints = {
        "current": hashes.current,
        "legacy_type": next(value for kind, value in hashes.legacy if kind == "解答题（计算）"),
        "legacy_format": next(value for value in hashes.compatible if value != hashes.current),
        "stale": "0" * 64,
    }
    versions = {kind: str(index + 1) * 64 for index, kind in enumerate(candidate_order)}
    evidence = json.dumps({"parts": [{"part_id": "TEST-part", "evidence_points": []}]})
    with connect(path) as conn:
        conn.execute(
            "INSERT INTO questions(id,question_number,question_type,question_text,answer_text) VALUES(1,'1','解答题',?,?)",
            (question.tagging_context.question_text, question.tagging_context.answer_text),
        )
        for kind in reversed(candidate_order):
            conn.execute(
                """INSERT INTO question_solution_evidence_versions
                   (evidence_version_id,question_id,source_content_hash,schema_version,content_hash,
                    evidence_json,status,source_kind,source_reference,created_by,created_at)
                   VALUES (?,1,?,'question-solution-evidence-v2',?,?,'proposed','backfill',?,'test','2026-01-01')""",
                (versions[kind], fingerprints[kind], versions[kind], evidence, kind),
            )
        _insert_feature_rows(conn, 1, [("TEST-part", 4)], fingerprint=hashes.current)
    if changed:
        question = replace(question, tagging_context=replace(question.tagging_context, answer_text="TEST-changed-answer"))
    monkeypatch.setattr(part_assessments, "current_inputs", lambda *_args, **_kwargs: {1: question})
    original = part_assessments._compute_input_source_hashes
    bundles = []
    def compute(value, input_key=""):
        bundles.append(input_key)
        return original(value, input_key)
    monkeypatch.setattr(part_assessments, "_compute_input_source_hashes", compute)
    with analysis.source_content_hash_reuse():
        direct = part_assessments.load_profiles(path, [1], question_inputs={1: question}, verify_source=True)[1]
        if not changed and candidate_order[0] == "current":
            assert not bundles
        else:
            assert len(bundles) == 1
        persisted = {}
        retained = part_assessments.load_profiles(path, [1], source_hashes=persisted, verify_source=True)[1]
        assert direct == retained
        assert persisted[1] == original(question, persisted[1].input_key)
        assert persisted[1].legacy and persisted[1].compatible
        assert len(persisted[1].input_key) == 64
        assert direct["evidence_version_id"] == versions[selected_kind]
        assert direct["source_type_alias"] == expected_alias
        assert direct["available"] is (not changed)
        assert direct["reason"] == ("part_assessment_source_changed" if changed else None)
        assert direct["parts"][0]["difficulty"] == (None if changed else 4)
        assert direct["current_source_content_hash"] == question.source_content_hash
        assert part_assessments.load_profiles(path, [1], source_hashes=persisted, verify_source=True)[1] == direct


def test_saved_training_facts_use_frozen_material_without_reading_current_question(refined_training_source, tmp_path, monkeypatch):
    import sqlite3
    from types import SimpleNamespace
    from question_bank.mastery.current import CurrentMasteryCalculator
    from question_bank.training_criteria.analysis import training_criteria_from_solution_evidence
    from question_bank.solution_evidence import part_assessments
    _path, _root, question, evidence, profile, _old = refined_training_source
    frozen = training_criteria_from_solution_evidence(evidence, question=question).to_dict()
    path = tmp_path / "TEST-saved-training-observations.db"
    states = [{"point_id": p["point_id"], "state": "met" if i == 0 else "not_met"}
              for i, p in enumerate(frozen["points"])]
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE training_evidence_records(evidence_id,student_id,stable_key,occurred_at,status,
                source_json,criterion_version_id,criterion_hash,task_item_code,final_points_json,submission_id,submission_revision);
            CREATE TABLE personalized_paper_items(paper_instance_id,task_item_code,criterion_version_id,
                criterion_snapshot_json,recommendation_snapshot_json);
            CREATE TABLE questions(id,answer_text);
            CREATE TABLE question_tags(question_id,tag_value);
        """)
        source = json.dumps({"bank_question_id": 1, "paper_instance_id": "TEST-paper"})
        conn.execute("INSERT INTO training_evidence_records VALUES('TEST-evidence','TEST-student','old-key','2026-10-01','active',?,'saved-id','old-code','task',?,'TEST-submission',1)", (source, json.dumps(states)))
        conn.execute("INSERT INTO personalized_paper_items VALUES('TEST-paper','task','saved-id',?,?)", (
            json.dumps({"criteria": frozen}), json.dumps({"part_assessment": {"parts": profile["parts"]}})))
        conn.execute("INSERT INTO questions VALUES(1,'TEST-original-answer')")
        conn.execute("INSERT INTO question_tags VALUES(1,'TEST-original-tag')")
    resolver = SimpleNamespace(resolve=lambda key: [SimpleNamespace(stable_key=key)])
    monkeypatch.setattr(part_assessments, "load_profiles", lambda *_args, **_kwargs: pytest.fail("historical training must not read current question inputs"))
    calculator = CurrentMasteryCalculator(path, resolver)
    first = calculator.training_observations(exclude_evidence_ids=frozenset(), allowed_student_ids=None)
    assert {(key, items[0].achieved_points, items[0].part_difficulty) for (_student, key), items in first.items()} == {
        ("sk_alg_linear_equation", 1, 2), ("sk_geo_triangle_congruence", 0, 8)}
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE questions SET answer_text='TEST-changed-answer'")
        conn.execute("UPDATE question_tags SET tag_value='TEST-changed-tag'")
    before = path.read_bytes()
    second = calculator.training_observations(exclude_evidence_ids=frozenset(), allowed_student_ids=None)
    assert second == first
    assert sum(len(items) for items in second.values()) == 2
    assert {items[0].display_name for items in second.values()} == {"一元一次方程", "三角形全等"}
    calculator.resolver = SimpleNamespace(resolve=lambda _key: [])
    unmapped = calculator.training_observations(exclude_evidence_ids=frozenset(), allowed_student_ids=None)
    assert unmapped == second
    assert path.read_bytes() == before
