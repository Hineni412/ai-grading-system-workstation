"""Synthetic checks of user-visible experimental ranking requirements."""
from tools.experiment_similar_questions import (
    Experiment, Part, Question, clean_text, smooth_difficulty, sample_questions,
    candidate_pool, summary_metrics, paired_comparisons,
)


def question(qid, text, skill, topic="equation", mode="process_required", **tags):
    return Question(qid, text, difficulty=4, tags={
        "skill": frozenset([skill]), "topic": frozenset([topic]),
        **{key: frozenset(value) for key, value in tags.items()},
    }, parts=(Part(frozenset([skill]), frozenset([topic]), mode, frozenset(["calculation"])),))


def test_practice_skill_beats_same_chapter_and_surface_wording():
    target = question(1, "解方程：x²-5x+6=0", "solve")
    different = question(2, "方程x²-5x+k=0有两个不等实根，求k的范围", "discriminant")
    useful = question(3, "求出满足(t-1)(t-4)=0的所有实数t", "solve")
    experiment = Experiment([target, different, useful])
    assert experiment.rank(1, "skill_rules")[0] == 3
    assert experiment.rank(1, "rules_bm25_rrf")[0] == 3


def test_continuous_difficulty_does_not_reward_a_larger_gap():
    scores = [smooth_difficulty(3, value) for value in (4.9, 5., 5.1, 5.9, 6.)]
    assert scores == sorted(scores, reverse=True)


def test_math_conditions_survive_text_preparation():
    assert clean_text("计算(1+2)×3") != clean_text("计算1+2×3")
    assert clean_text("求a-b") != clean_text("求ab")
    assert clean_text("求x²") != clean_text("求x³")


def test_written_response_preferred_to_choice_for_same_skill():
    target = question(1, "证明两个三角形全等", "congruence", "triangle")
    choice = question(2, "下列全等判定正确的是", "congruence", "triangle", "exact_objective")
    written = question(3, "写出两组三角形全等的推理依据", "congruence", "triangle")
    experiment = Experiment([target, choice, written])
    assert experiment.demand_score(target, written) > experiment.demand_score(target, choice)


def test_one_shared_part_does_not_equal_whole_paper_coverage():
    a = question(1, "先解方程再讨论根的情况", "solve")
    a.parts += (Part(frozenset(["discriminant"]), frozenset(["roots"]), "process_required", frozenset(["parameter"])),)
    partial = question(2, "解方程", "solve")
    complete = question(3, "求根并讨论根的情况", "solve")
    complete.parts = a.parts
    assert Experiment.demand_score(a, complete) > Experiment.demand_score(a, partial)


def test_adding_shared_evidence_never_reduces_text_fallback():
    target = question(1, "计算x的数值", "a")
    other = question(2, "计算x的数值", "b", topic="other")
    before = Experiment([target, other]).components(target, other)["final"]
    other.tags["skill"] = target.tags["skill"]
    after = Experiment([target, other]).components(target, other)["final"]
    assert after >= before


def test_sampling_is_reproducible_and_has_no_duplicates():
    questions = [question(i, f"合成第{i}题", "s") for i in range(40)]
    first, second = sample_questions(questions), sample_questions(questions)
    assert [q.qid for q in first] == [q.qid for q in second]
    assert len({q.qid for q in first}) == 30


def test_short_lists_and_unknown_labels_do_not_inflate_comparison():
    target = question(1, "合成目标题", "s")
    rankings = {"current": [2, 3], "skill_rules": [2], "rules_bm25_rrf": [2, 4]}
    labels = {2: 3, 3: 1, 4: None}
    pool = candidate_pool(rankings, 0)
    ratings = {"Q01": {"grades": [labels[qid] for qid in pool]}}
    result = summary_metrics([target], {1: rankings}, ratings)
    for route in rankings:
        assert result[route]["useful_candidates"] == 1
        assert result[route]["useful_per_six_lower_bound"] == 0.1667
        assert result[route]["ndcg_queries"] == 0
    assert result["rules_bm25_rrf"]["unassessable_candidates"] == 1
    comparison = paired_comparisons(result)["skill_rules_vs_current"]
    assert comparison["tied_queries"] == 1
    assert comparison["paired_query_bootstrap_95_percentile_interval_pp"] == [0., 0.]
