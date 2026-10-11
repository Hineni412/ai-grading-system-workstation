"""Synthetic checks of user-visible experimental ranking requirements."""
from tools.experiment_similar_questions import (
    Part, Question, candidate_pool, summary_metrics, paired_comparisons,
)


def question(qid, text, skill, topic="equation", mode="process_required", **tags):
    return Question(qid, text, difficulty=4, tags={
        "skill": frozenset([skill]), "topic": frozenset([topic]),
        **{key: frozenset(value) for key, value in tags.items()},
    }, parts=(Part(frozenset([skill]), frozenset([topic]), mode, frozenset(["calculation"])),))


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
