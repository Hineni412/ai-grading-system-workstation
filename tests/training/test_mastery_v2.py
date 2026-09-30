from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from question_bank.mastery.v2 import (
    EvidenceConflictError,
    EvidenceStatus,
    ExamEvidence,
    MasteryStatus,
    MasteryV2Parameters,
    PrerequisiteMastery,
    TrainingEvidence,
    compute_mastery_v2,
)


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "p4_07_mastery_v2_golden.json"
AS_OF = datetime(2026, 1, 31, tzinfo=UTC)
TARGET_KEY = "kp_alg_linear_equation"


def _exam(
    evidence_id: str,
    score: float,
    *,
    full_score: float = 10.0,
    age_days: float = 0.0,
    **kwargs,
) -> ExamEvidence:
    return ExamEvidence(
        evidence_id=evidence_id,
        stable_key=TARGET_KEY,
        occurred_at=AS_OF - timedelta(days=age_days),
        score_awarded=score,
        full_score=full_score,
        **kwargs,
    )


def _parameters(**kwargs) -> MasteryV2Parameters:
    return MasteryV2Parameters(
        prior_mean=kwargs.pop("prior_mean", 0.5),
        prior_strength=kwargs.pop("prior_strength", 0.0),
        **kwargs,
    )


def _compute(**kwargs):
    return compute_mastery_v2(stable_key=TARGET_KEY, **kwargs)


def test_mastery_v2_golden_samples() -> None:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    assert fixture["schema_version"] == "p4-mastery-v2-golden-v1"
    assert fixture["synthetic_only"] is True
    for sample in fixture["samples"]:
        parameters = MasteryV2Parameters(**sample["parameters"])
        exam = tuple(
            ExamEvidence(
                **{
                    **item,
                    "occurred_at": datetime.fromisoformat(item["occurred_at"]),
                }
            )
            for item in sample["exam"]
        )
        training = tuple(
            TrainingEvidence(
                **{
                    **item,
                    "occurred_at": datetime.fromisoformat(item["occurred_at"]),
                }
            )
            for item in sample["training"]
        )

        result = _compute(
            as_of=datetime.fromisoformat(sample["as_of"]),
            exam_evidence=exam,
            training_evidence=training,
            parameters=parameters,
        )

        assert result.status.value == sample["expected"]["status"], sample["id"]
        assert result.value == sample["expected"]["value"], sample["id"]
        assert (
            result.direct_evidence_count == sample["expected"]["direct_evidence_count"]
        ), sample["id"]
        assert result.layers[0].weighted_mean == sample["expected"]["exam_mean"], (
            sample["id"]
        )
        assert result.layers[1].weighted_mean == sample["expected"]["training_mean"], (
            sample["id"]
        )


def math_is_finite(value: float) -> bool:
    return value == value and value not in {float("inf"), float("-inf")}


def test_teacher_correction_replaces_value_and_requires_reason() -> None:
    corrected = _exam(
        "corrected",
        2,
        teacher_correction=0.9,
        teacher_correction_reason="合成核对",
    )
    result = _compute(
        as_of=AS_OF,
        exam_evidence=(corrected,),
        parameters=_parameters(),
    )

    assert result.value == 0.9
    assert result.contributions[0].raw_value == 0.2
    assert result.contributions[0].effective_value == 0.9
    assert result.contributions[0].teacher_corrected is True
    with pytest.raises(ValueError, match="reason"):
        _exam("invalid-correction", 2, teacher_correction=0.9)


def test_no_usable_evidence_does_not_publish_the_prior_as_mastery() -> None:
    result = _compute(
        as_of=AS_OF,
        exam_evidence=(
            ExamEvidence(
                "pending",
                TARGET_KEY,
                None,
                None,
                None,
                status=EvidenceStatus.PENDING_REVIEW,
            ),
        ),
    )

    assert result.status is MasteryStatus.MISSING
    assert result.value is None
    assert result.direct_evidence_count == 0
    assert "没有可用于计算" in result.explanations[0]
