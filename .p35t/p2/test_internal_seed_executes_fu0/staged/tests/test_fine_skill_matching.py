from question_bank.recommendation.fine_skill_matching import (
    fine_skill_match_score,
)


def test_exact_and_contained_skill_matches_are_strong() -> None:
    assert fine_skill_match_score(["角平分线性质"], ["角平分线性质"]) == 1.0
    assert (
        fine_skill_match_score(
            ["角平分线性质"],
            ["利用角平分线性质求面积"],
        )
        >= 0.8
    )


def test_same_broad_area_without_skill_overlap_does_not_match() -> None:
    assert fine_skill_match_score(
        ["角平分线性质"],
        ["线段中点计算", "线段与角"],
    ) == 0.0


def test_punctuation_and_whitespace_do_not_break_matching() -> None:
    assert fine_skill_match_score(
        ["一次函数 图像应用"],
        ["一次函数图像应用"],
    ) == 1.0
