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


def test_vector_experiment_cosine_is_scale_independent_and_excludes_self():
    from tools.experiment_vector_similarity import VectorExperiment

    rows = [question(i, f"合成题{i}", "s") for i in (1, 3, 2, 4)]
    experiment = VectorExperiment(rows, [[10, 0], [8, 6], [80, 60], [-1, 0]])
    assert experiment.rank(1, "vector") == [2, 3, 4]


def test_vector_experiment_rejects_invalid_embeddings_before_ranking():
    import pytest
    from tools.experiment_vector_similarity import normalize_vectors

    for vectors in ([[0, 0]], [[float("nan"), 1]], [[float("inf"), 1]], [1, 2]):
        with pytest.raises(ValueError):
            normalize_vectors(vectors)
    with pytest.raises(ValueError):
        normalize_vectors([[1, 0]], expected_rows=2)


def test_hybrid_does_not_promote_a_vector_hit_without_current_rule_support():
    from tools.experiment_vector_similarity import VectorExperiment

    target = question(1, "求出满足(t-1)(t-4)=0的所有实数t", "solve")
    wrong = question(2, "证明两条直线互相垂直", "perpendicular", topic="geometry")
    useful = question(3, "解方程：x²-5x+6=0", "solve")
    experiment = VectorExperiment([target, wrong, useful], [[1, 0], [1, .01], [.7, .7]])
    assert experiment.rank(1, "vector")[0] == 2
    assert experiment.rank(1, "hybrid") == [3]


def test_vector_experiment_uses_the_adopted_ranker_as_its_baseline():
    from tools.experiment_vector_similarity import VectorExperiment

    rows = [question(1, "解方程x²-5x+6=0", "solve"),
            question(2, "求方程(t-1)(t-4)=0的所有实根", "solve"),
            question(3, "求k使方程x²-5x+k=0有两个不等实根", "discriminant")]
    experiment = VectorExperiment(rows, [[1, 0], [.8, .6], [.9, .1]])
    assert experiment.rank(1, "current") == Experiment(rows).rank(1, "rules_bm25_rrf")


def test_small_vector_holdout_covers_each_available_question_type():
    from tools.experiment_vector_similarity import held_out_sample

    rows = [question(i, chr(0x4E00+i) * 8, f"skill_{i}") for i in range(1, 121)]
    for ordinal, item in enumerate(rows):
        item.kind = ("选择题", "填空题", "解答题")[ordinal % 3]
        item.tags["chapter"] = frozenset([f"chapter_{ordinal}"])
    sample, stats = held_out_sample(rows, 12)
    assert {q.kind for q in sample} == {"选择题", "填空题", "解答题"}
    assert len(sample) == 12
    assert not {q.qid for q in sample}.intersection(q.qid for q in sample_questions(rows))


def test_local_vector_encoder_uses_cls_and_masks_padding_without_real_model():
    import numpy as np
    from types import SimpleNamespace
    from tools.experiment_vector_similarity import LocalBge

    encoder = object.__new__(LocalBge)
    encoder.cls, encoder.sep, encoder.pad = 101, 102, 0
    encoder.stats = {"encoded_texts": 0, "truncated_stems": 0,
                     "truncated_requirements": 0, "truncated_solutions": 0, "tokens": 0}
    encoder.tokenizer = SimpleNamespace(encode=lambda text, **kw: SimpleNamespace(ids=[20] * len(text)))
    captured = []

    class FakeSession:
        def get_inputs(self):
            return [SimpleNamespace(name=name) for name in ("input_ids", "attention_mask", "token_type_ids")]

        def run(self, _, inputs):
            captured.append(inputs)
            batch, width = inputs["input_ids"].shape
            values = np.full((batch, width, 2), 100., dtype=np.float32)
            values[:, 0, :] = [3., 4.]
            return [values]

    encoder.session = FakeSession()
    long = question(1, "甲" * 600, "s")
    long.answer = "乙" * 600
    short = question(2, "求未知数", "s")
    vectors = encoder.encode([long, short])
    assert np.allclose(vectors, [[.6, .8], [.6, .8]])
    inputs = captured[0]
    assert np.any(inputs["attention_mask"][1] == 0)
    assert np.all(inputs["input_ids"][inputs["attention_mask"] == 0] == 0)
    assert encoder.stats["truncated_stems"] == 1
    assert encoder.stats["truncated_solutions"] == 1
    assert inputs["input_ids"].shape[1] <= 512
