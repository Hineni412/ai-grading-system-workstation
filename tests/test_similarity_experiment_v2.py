"""Result-oriented synthetic checks for the four authorized B corrections."""
from tools.experiment_similar_questions import Experiment, Part, Question
from tools.experiment_similar_questions_v2 import RevisedExperiment


def q(qid, text, skill="root", topic="radicals", mode="exact_objective", parts=None):
    tags = {"topic": frozenset([topic])}
    if skill:
        tags["skill"] = frozenset([skill])
    return Question(qid, text, difficulty=3, tags=tags,
                    parts=parts if parts is not None else (
                        Part(frozenset([skill]) if skill else frozenset(), frozenset([topic]), mode, frozenset()),))


def test_lexical_candidate_is_not_discarded_by_original_rule_cutoff():
    target = q(1, "计算：(√7)²-(π+2)⁰", "property")
    good = q(2, "计算：(1)√63÷√7；(2)√28-(2028-π)⁰+|3-√2|", "arithmetic", mode="process_required")
    good.tags["skill"] = frozenset(["arithmetic", "division", "multiply"])
    good.tags["method"] = frozenset(["simplify"])
    bad = q(3, "下列根式是最简二次根式的是：√75，√3", "property")
    base = Experiment([target, good, bad])
    revised = RevisedExperiment(base)
    assert base.components(target, good)["final"] < .25
    assert 2 not in base.rank(1, "rules_bm25_rrf")
    assert revised.rank(1)[0] == 2
