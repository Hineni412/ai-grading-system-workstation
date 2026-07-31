from __future__ import annotations

import json
import random
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
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


FIXTURE_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "p4_07_mastery_v2_golden.json"
)
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


def _training(
    evidence_id: str,
    achieved: int,
    total: int,
    *,
    age_days: float = 0.0,
    **kwargs,
) -> TrainingEvidence:
    return TrainingEvidence(
        evidence_id=evidence_id,
        stable_key=TARGET_KEY,
        occurred_at=AS_OF - timedelta(days=age_days),
        achieved_points=achieved,
        total_points=total,
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
                    "occurred_at": datetime.fromisoformat(
                        item["occurred_at"]
                    ),
                }
            )
            for item in sample["exam"]
        )
        training = tuple(
            TrainingEvidence(
                **{
                    **item,
                    "occurred_at": datetime.fromisoformat(
                        item["occurred_at"]
                    ),
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
            result.direct_evidence_count
            == sample["expected"]["direct_evidence_count"]
        ), sample["id"]
        assert result.layers[0].weighted_mean == sample["expected"][
            "exam_mean"
        ], sample["id"]
        assert result.layers[1].weighted_mean == sample["expected"][
            "training_mean"
        ], sample["id"]


def test_monotonicity_when_one_exam_score_increases() -> None:
    values = [
        _compute(
            as_of=AS_OF,
            exam_evidence=(_exam("exam", score),),
            parameters=_parameters(prior_strength=2.0),
        ).value
        for score in range(11)
    ]

    assert values == sorted(values)
    assert len(set(values)) == len(values)


def test_output_stays_in_range_for_extreme_synthetic_inputs() -> None:
    generator = random.Random(407)
    for sample_index in range(200):
        evidence = tuple(
            _exam(
                f"exam-{sample_index}-{index}",
                generator.uniform(0.0, 100.0),
                full_score=100.0,
                age_days=generator.uniform(0.0, 3_650.0),
                difficulty_weight=generator.uniform(0.25, 2.0),
                evidence_weight=generator.uniform(0.25, 2.0),
            )
            for index in range(generator.randint(1, 30))
        )
        result = _compute(
            as_of=AS_OF,
            exam_evidence=evidence,
        )

        assert result.status is MasteryStatus.AVAILABLE
        assert result.value is not None
        assert 0.0 <= result.value <= 1.0
        assert math_is_finite(result.value)


def math_is_finite(value: float) -> bool:
    return value == value and value not in {float("inf"), float("-inf")}


def test_equivalent_timezones_produce_identical_output() -> None:
    utc_evidence = ExamEvidence(
        "exam",
        TARGET_KEY,
        datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
        8,
        10,
    )
    china_evidence = ExamEvidence(
        "exam",
        TARGET_KEY,
        datetime(
            2026,
            1,
            1,
            8,
            0,
            tzinfo=timezone(timedelta(hours=8)),
        ),
        8,
        10,
    )

    assert utc_evidence == china_evidence
    assert _compute(
        as_of=AS_OF,
        exam_evidence=(utc_evidence,),
    ) == _compute(
        as_of=AS_OF,
        exam_evidence=(china_evidence,),
    )


def test_naive_or_materially_future_datetimes_are_rejected() -> None:
    with pytest.raises(ValueError, match="timezone"):
        ExamEvidence(
            "exam",
            TARGET_KEY,
            datetime(2026, 1, 1),
            8,
            10,
        )

    future = _exam("future", 8)
    with pytest.raises(ValueError, match="after as_of"):
        _compute(
            as_of=AS_OF - timedelta(days=1),
            exam_evidence=(future,),
        )


def test_exact_duplicate_is_idempotent_but_conflicting_duplicate_fails() -> None:
    evidence = _exam("same", 8)
    single = _compute(
        as_of=AS_OF,
        exam_evidence=(evidence,),
    )
    duplicate = _compute(
        as_of=AS_OF,
        exam_evidence=(evidence, evidence),
    )

    assert duplicate == single
    with pytest.raises(EvidenceConflictError, match="same"):
        _compute(
            as_of=AS_OF,
            exam_evidence=(evidence, replace(evidence, score_awarded=7)),
        )


def test_same_identity_cannot_cross_exam_and_training_layers() -> None:
    with pytest.raises(EvidenceConflictError, match="shared"):
        _compute(
            as_of=AS_OF,
            exam_evidence=(_exam("shared", 8),),
            training_evidence=(_training("shared", 1, 1),),
        )


def test_evidence_cannot_cross_stable_knowledge_identities() -> None:
    wrong_target = replace(
        _exam("wrong-target", 8),
        stable_key="kp_alg_equation_properties",
    )

    with pytest.raises(ValueError, match="does not match target"):
        _compute(
            as_of=AS_OF,
            exam_evidence=(wrong_target,),
        )


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (EvidenceStatus.PENDING_REVIEW, "pending_review_not_counted"),
        (EvidenceStatus.INCOMPLETE, "incomplete_not_counted"),
        (EvidenceStatus.MISSING, "missing_not_counted"),
        (EvidenceStatus.WITHDRAWN, "withdrawn_not_counted"),
    ],
)
def test_noncompleted_evidence_is_excluded_not_zero(
    status: EvidenceStatus,
    reason: str,
) -> None:
    excluded = ExamEvidence(
        evidence_id=f"excluded-{status.value}",
        stable_key=TARGET_KEY,
        occurred_at=None,
        score_awarded=None,
        full_score=None,
        status=status,
    )
    result = _compute(
        as_of=AS_OF,
        exam_evidence=(_exam("included", 8), excluded),
    )
    without_excluded = _compute(
        as_of=AS_OF,
        exam_evidence=(_exam("included", 8),),
    )

    assert result.value == without_excluded.value
    assert result.direct_evidence_count == 1
    assert result.contributions[0].exclusion_reason == reason
    assert result.contributions[0].effective_value is None


def test_withdrawal_and_physical_removal_recompute_the_same_value() -> None:
    retained = _exam("retained", 8)
    withdrawn = ExamEvidence(
        "withdrawn",
        TARGET_KEY,
        None,
        None,
        None,
        status=EvidenceStatus.WITHDRAWN,
    )

    with_withdrawal = _compute(
        as_of=AS_OF,
        exam_evidence=(retained, withdrawn),
    )
    after_deletion = _compute(
        as_of=AS_OF,
        exam_evidence=(retained,),
    )

    assert with_withdrawal.value == after_deletion.value
    assert with_withdrawal.direct_evidence_count == 1


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


def test_small_sample_shrinks_more_than_large_sample() -> None:
    parameters = _parameters(prior_mean=0.5, prior_strength=2.0)
    one = _compute(
        as_of=AS_OF,
        exam_evidence=(_exam("one", 10),),
        parameters=parameters,
    )
    many = _compute(
        as_of=AS_OF,
        exam_evidence=tuple(
            _exam(f"many-{index}", 10)
            for index in range(20)
        ),
        parameters=parameters,
    )

    assert one.value is not None and many.value is not None
    assert 0.5 < one.value < many.value < 1.0


def test_newer_evidence_has_more_weight_than_older_evidence() -> None:
    result = _compute(
        as_of=AS_OF,
        exam_evidence=(
            _exam("old-high", 10, age_days=180),
            _exam("new-low", 0, age_days=0),
        ),
        parameters=_parameters(
            prior_strength=0.0,
            exam_half_life_days=180.0,
        ),
    )

    assert result.value == pytest.approx(1 / 3, abs=1e-6)
    weights = {
        item.evidence_id: item.effective_weight
        for item in result.contributions
    }
    assert weights["old-high"] < weights["new-low"]


def test_difficulty_and_evidence_weights_change_relative_contribution() -> None:
    result = _compute(
        as_of=AS_OF,
        exam_evidence=(
            _exam(
                "high-weight-low-result",
                0,
                difficulty_weight=2.0,
                evidence_weight=2.0,
            ),
            _exam("normal-high-result", 10),
        ),
        parameters=_parameters(),
    )

    assert result.value == 0.2
    weights = {
        item.evidence_id: item.effective_weight
        for item in result.contributions
    }
    assert weights["high-weight-low-result"] == 4.0
    assert weights["normal-high-result"] == 1.0


def test_training_points_are_normalized_inside_each_question() -> None:
    result = _compute(
        as_of=AS_OF,
        training_evidence=(
            _training("short", 1, 1),
            _training("long", 0, 4),
        ),
        parameters=_parameters(training_source_weight=1.0),
    )

    assert result.value == 0.5
    assert result.value != 0.2


def test_prerequisites_are_explained_but_never_change_direct_score() -> None:
    direct = (_exam("direct", 8),)
    prerequisite = PrerequisiteMastery(
        stable_key="kp_alg_real_numbers",
        display_name="实数",
        status=MasteryStatus.AVAILABLE,
        value=0.2,
        evidence_count=3,
        relation_id="rel-prerequisite",
    )

    without = _compute(as_of=AS_OF, exam_evidence=direct)
    with_prerequisite = _compute(
        as_of=AS_OF,
        exam_evidence=direct,
        prerequisites=(prerequisite,),
    )

    assert with_prerequisite.value == without.value
    assert with_prerequisite.prerequisites == (prerequisite,)
    assert any(
        "不计入本知识点掌握度" in line
        for line in with_prerequisite.explanations
    )


def test_fixed_reference_time_and_input_order_are_deterministic() -> None:
    first = _exam("a", 8, age_days=10)
    second = _training("b", 1, 2, age_days=5)

    left = _compute(
        as_of=AS_OF,
        exam_evidence=(first,),
        training_evidence=(second,),
    )
    right = _compute(
        as_of=AS_OF,
        training_evidence=(second,),
        exam_evidence=(first,),
    )

    assert left == right
    assert left.to_dict() == right.to_dict()
    assert left.to_dict()["status"] == "available"


def test_concurrent_retries_have_no_shared_mutable_state() -> None:
    evidence = tuple(_exam(f"exam-{index}", index) for index in range(11))

    def calculate() -> dict:
        return _compute(
            as_of=AS_OF,
            exam_evidence=evidence,
        ).to_dict()

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _index: calculate(), range(64)))

    assert all(result == results[0] for result in results)


def test_parameter_version_changes_without_mutating_old_result() -> None:
    evidence = (_exam("exam", 8),)
    first_parameters = MasteryV2Parameters()
    second_parameters = replace(first_parameters, prior_strength=3.0)
    first = _compute(
        as_of=AS_OF,
        exam_evidence=evidence,
        parameters=first_parameters,
    )
    second = _compute(
        as_of=AS_OF,
        exam_evidence=evidence,
        parameters=second_parameters,
    )

    assert first.parameter_version == first_parameters.version
    assert second.parameter_version == second_parameters.version
    assert first.parameter_version != second.parameter_version
    assert first.prior_strength == 2.0
    assert second.prior_strength == 3.0


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
