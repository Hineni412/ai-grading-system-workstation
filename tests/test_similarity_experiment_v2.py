"""Result-oriented synthetic checks for the four authorized B corrections."""
from tools.experiment_similar_questions import Experiment, Part, Question
from tools.experiment_similar_questions_v2 import (
    RevisedExperiment, aims_from_text, holdout_questions, math_text, math_tokens,
    part_prompts,
)


def q(qid, text, skill="root", topic="radicals", mode="exact_objective", parts=None):
    tags = {"topic": frozenset([topic])}
    if skill:
        tags["skill"] = frozenset([skill])
    return Question(qid, text, difficulty=3, tags=tags,
                    parts=parts if parts is not None else (
                        Part(frozenset([skill]) if skill else frozenset(), frozenset([topic]), mode, frozenset()),))


def test_html_math_and_unicode_math_have_the_same_meaning():
    assert math_text("x<sup>2</sup>+y<sub>1</sub>") == math_text("x²+y₁")
    assert math_text("x<sup>-2</sup>") == math_text("x⁻²")
    assert math_text("x&lt;3且x&gt;-1") == "x<3且x>-1"
    assert math_text("(1+2)×3") != math_text("1+2×3")
    assert math_text("a-b") != math_text("ab")
    assert math_text("3.14+2") == "3.14+2"


def test_table_presentation_does_not_become_search_vocabulary():
    text = "<table><tr><td>x<sup>2</sup></td><td>&radic;3</td></tr></table>"
    words = math_tokens(text)
    assert not {"table", "tr", "td", "sup"}.intersection(words)
    assert "^" in words and "√" in words and "2" in words


def test_unknown_requirements_are_not_full_matches():
    a, b = q(1, "甲材料"), q(2, "乙材料")
    revised = RevisedExperiment(Experiment([a, b]))
    assert revised.demand_score(a, b) is None
    assert revised.components(a, b)["demand"] is None


def test_calculation_preferred_to_radical_concept_identification():
    target = q(1, "计算：(√3)²-(π-1)⁰")
    concept = q(2, "下列根式中是最简二次根式的是：√3、√8")
    practice = q(3, "计算：√12-2026⁰+|1-√3|", mode="process_required")
    revised = RevisedExperiment(Experiment([target, concept, practice]))
    assert revised.rank(1)[0] == 3
    assert revised.demand_score(target, concept) == 0


def test_missing_skill_is_distinct_from_known_nonmatching_skill():
    target = q(1, "求角平分线所构成三角形的面积", skill=None, topic="angle_bisector")
    useful = q(2, "求角平分线分割图形的面积", skill="area", topic="angle_bisector")
    unknown = RevisedExperiment(Experiment([target, useful])).components(target, useful)
    target.tags["skill"] = frozenset(["coordinate"])
    contradicted = RevisedExperiment(Experiment([target, useful])).components(target, useful)
    assert unknown["quality"] > contradicted["quality"]
    assert unknown["quality"] >= .25


def test_minimum_requirement_not_hidden_by_same_function_topic():
    target = q(1, "在-2≤x≤0时，求一次函数y=2x+3的最小值", "linear", "function")
    useful = q(2, "当1≤t≤4时，求一次函数y=3t+7的最小值", "linear", "function")
    different = q(3, "一次函数y=2x+3与坐标轴交于两点，求点的坐标", "linear", "function")
    revised = RevisedExperiment(Experiment([target, useful, different]))
    assert revised.rank(1)[0] == 2
    assert "optimization" in aims_from_text(target.text)


def test_subquestions_keep_their_own_goals_and_ignore_formula_indices():
    parts = (Part(frozenset(["s"]), frozenset(["t"]), "process_required", frozenset(), "(1)"),
             Part(frozenset(["s"]), frozenset(["t"]), "process_required", frozenset(), "(2)"))
    target = q(1, "已知y=x²+(1)/(2)。(1)求函数的最小值；(2)证明两条直线平行。", parts=parts)
    prompts = part_prompts(target)
    assert len(prompts) == 2
    assert "optimization" in aims_from_text(prompts[0])
    assert "proof" not in aims_from_text(prompts[0])
    assert "proof" in aims_from_text(prompts[1])
    assert "optimization" not in aims_from_text(prompts[1])


def test_whole_question_not_equated_to_one_matching_small_part():
    one = q(1, "计算：√18-√8")
    parts = (Part(frozenset(["root"]), frozenset(["radicals"]), "exact_objective", frozenset()),) * 2
    mixed = q(2, "(1)计算：√18-√8；(2)证明两三角形全等。", parts=parts)
    full = q(3, "(1)计算：√32-√8；(2)证明两三角形全等。", parts=parts)
    revised = RevisedExperiment(Experiment([one, mixed, full]))
    assert revised.demand_score(mixed, full) > revised.demand_score(mixed, one)


def test_holdout_excludes_development_numeric_variants():
    old = q(1, "计算：√18-√8+(√3)²")
    duplicate = q(2, "计算：√32-√2+(√5)²")
    different = q(3, "证明三角形的两条边相等", "congruence", "triangle")
    selected, stats = holdout_questions([old, duplicate, different], [old], size=2)
    assert [item.qid for item in selected] == [3]
    assert stats["rejected_near_template_queries"] == 1


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
