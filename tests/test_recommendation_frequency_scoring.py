from question_bank.recommendation.scoring import recommendation_score


def test_regular_recommendation_score_uses_frequency_instead_of_typicality() -> None:
    score = recommendation_score(
        mastery=0.2,
        frequency_rate=0.8,
        tag_score=1.0,
        difficulty_score=1.0,
    )

    assert score == 0.9


def test_practice_question_without_frequency_is_not_penalized() -> None:
    score = recommendation_score(
        mastery=0.2,
        frequency_rate=None,
        tag_score=1.0,
        difficulty_score=1.0,
    )

    assert score == 0.9176


def test_external_zhongkao_recommendation_prioritizes_shenzhen_fit() -> None:
    high_fit = recommendation_score(
        mastery=0.2,
        frequency_rate=0.2,
        tag_score=0.8,
        difficulty_score=0.8,
        shenzhen_fit_score=0.9,
        shenzhen_frequency_rate=0.5,
        national_frequency_rate=0.7,
    )
    low_fit = recommendation_score(
        mastery=0.2,
        frequency_rate=1.0,
        tag_score=0.8,
        difficulty_score=0.8,
        shenzhen_fit_score=0.2,
        shenzhen_frequency_rate=0.5,
        national_frequency_rate=1.0,
    )

    assert high_fit > low_fit
