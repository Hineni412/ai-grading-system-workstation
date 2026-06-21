from __future__ import annotations

import pytest

from question_bank.recommendation.scoring import (
    frequency_fit_score,
    score_candidate,
)
from question_bank.services.question_frequency_service import FrequencyMetrics


def test_candidate_without_confirmed_concept_match_is_ineligible() -> None:
    result = score_candidate(
        concept_match=0.0,
        mapping_status="suggested",
        fine_skill_match=1.0,
        frequency_fit=1.0,
        gradient_fit=1.0,
        diversity_fit=1.0,
    )

    assert result.eligible is False
    assert result.total_score == 0.0


def test_default_weights_prioritize_concept_and_frequency() -> None:
    result = score_candidate(
        concept_match=1.0,
        mapping_status="confirmed",
        fine_skill_match=1.0,
        frequency_fit=0.8,
        gradient_fit=0.5,
        diversity_fit=0.5,
    )

    assert result.total_score == pytest.approx(0.805)
    assert result.components["frequency"] == 0.8


def test_invalid_custom_weight_total_is_rejected() -> None:
    with pytest.raises(ValueError, match="sum to 1"):
        score_candidate(
            concept_match=1.0,
            mapping_status="confirmed",
            fine_skill_match=1.0,
            frequency_fit=0.8,
            gradient_fit=0.5,
            diversity_fit=0.5,
            weights={"concept": 0.5, "frequency": 0.5, "gradient": 0.5, "diversity": 0.5},
        )


def test_missing_difficulty_uses_neutral_gradient_with_warning() -> None:
    result = score_candidate(
        concept_match=1.0,
        mapping_status="confirmed",
        fine_skill_match=1.0,
        frequency_fit=0.8,
        gradient_fit=None,
        diversity_fit=0.5,
    )

    assert result.components["gradient"] == 0.5
    assert "候选题缺少难度标签" in result.warnings


def test_frequency_fit_prioritizes_shenzhen_fit_and_retains_wider_frequency() -> None:
    high_shenzhen = frequency_fit_score(
        FrequencyMetrics(
            available=True,
            questions_per_paper=0.4,
            national_questions_per_paper=0.8,
            shenzhen_questions_per_paper=0.6,
            shenzhen_fit_available=True,
            shenzhen_fit_score=0.9,
        )
    )
    low_shenzhen = frequency_fit_score(
        FrequencyMetrics(
            available=True,
            questions_per_paper=1.0,
            national_questions_per_paper=1.0,
            shenzhen_questions_per_paper=0.6,
            shenzhen_fit_available=True,
            shenzhen_fit_score=0.2,
        )
    )

    assert high_shenzhen > low_shenzhen


def test_regular_frequency_fit_uses_local_frequency() -> None:
    assert frequency_fit_score(FrequencyMetrics(available=True, questions_per_paper=0.75)) == 0.75


def test_sub_skill_boost_applies_bonus_multiplier() -> None:
    base_result = score_candidate(
        concept_match=1.0,
        mapping_status="confirmed",
        fine_skill_match=1.0,
        frequency_fit=0.8,
        gradient_fit=0.5,
        diversity_fit=0.5,
    )
    boosted_result = score_candidate(
        concept_match=1.0,
        mapping_status="confirmed",
        fine_skill_match=1.0,
        frequency_fit=0.8,
        gradient_fit=0.5,
        diversity_fit=0.5,
        sub_skill_boost=1.0,
    )
    assert boosted_result.total_score == pytest.approx(base_result.total_score * 1.3, rel=1e-4)


def test_confirmed_broad_concept_without_fine_skill_is_ineligible_by_default() -> None:
    result = score_candidate(
        concept_match=1.0,
        mapping_status="confirmed",
        fine_skill_match=0.0,
        frequency_fit=1.0,
        gradient_fit=1.0,
        diversity_fit=1.0,
    )

    assert result.eligible is False
    assert "具体训练技能不匹配" in result.warnings


def test_advanced_broad_fallback_is_explicit_and_warned() -> None:
    result = score_candidate(
        concept_match=1.0,
        mapping_status="confirmed",
        fine_skill_match=0.0,
        frequency_fit=1.0,
        gradient_fit=1.0,
        diversity_fit=1.0,
        allow_broad_fallback=True,
    )

    assert result.eligible is True
    assert "仅按标准知识点大类补足" in result.warnings
