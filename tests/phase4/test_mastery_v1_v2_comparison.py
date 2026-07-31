from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from question_bank.database.schema import initialize_database
from question_bank.mastery.comparison import (
    MasteryComparisonCase,
    build_profile_comparison_cases,
    compare_mastery_v1_v2,
)
from question_bank.mastery.rollout import (
    MasteryRolloutGateBlocked,
    MasteryRolloutRepository,
    MasteryRolloutRevisionConflict,
    MasterySpotCheckConflict,
)
from question_bank.mastery.v2 import (
    EvidenceStatus,
    ExamEvidence,
    MasteryV2Parameters,
)


AS_OF = datetime(2026, 1, 31, tzinfo=UTC)
STABLE_KEY = "kp_alg_linear_equation"


def _case(
    *,
    student_id: str,
    v1: float | None,
    awarded: float | None,
    full_score: float | None,
    occurred_at: datetime | None = AS_OF,
    status: EvidenceStatus = EvidenceStatus.COMPLETED,
) -> MasteryComparisonCase:
    return MasteryComparisonCase(
        student_id=student_id,
        student_code=f"S-{student_id}",
        student_name=f"合成学生{student_id}",
        class_id="合成班",
        stable_key=STABLE_KEY,
        display_name="一元一次方程",
        mastery_v1=v1,
        exam_evidence=(
            ExamEvidence(
                evidence_id=f"exam-{student_id}",
                stable_key=STABLE_KEY,
                occurred_at=occurred_at,
                score_awarded=awarded,
                full_score=full_score,
                status=status,
            ),
        ),
    )


def _repository(tmp_path: Path) -> MasteryRolloutRepository:
    database = tmp_path / "question-bank.db"
    initialize_database(database)
    return MasteryRolloutRepository(database)


def test_comparison_ranks_differences_and_requires_confirmed_threshold_items(
) -> None:
    report = compare_mastery_v1_v2(
        (
            _case(
                student_id="01",
                v1=1.0,
                awarded=10,
                full_score=10,
            ),
            _case(
                student_id="02",
                v1=0.7,
                awarded=7,
                full_score=10,
            ),
            _case(
                student_id="03",
                v1=1.0,
                awarded=None,
                full_score=None,
                occurred_at=None,
                status=EvidenceStatus.MISSING,
            ),
        ),
        as_of=AS_OF,
    )

    assert report.review_delta == 0.10
    assert [item.student_id for item in report.items] == ["03", "01", "02"]
    assert report.items[0].absolute_delta is None
    assert report.items[0].requires_review is True
    assert "availability_changed" in report.items[0].reason_codes
    assert report.items[1].absolute_delta == pytest.approx(0.233333)
    assert report.items[1].requires_review is True
    assert report.items[2].absolute_delta == pytest.approx(0.033333)
    assert report.items[2].requires_review is False
    assert report.required_review_count == 2
    assert len(report.evaluation_id) == 64
    assert report.duration_ms >= 0
    assert report.items_per_second > 0


def test_comparison_still_requires_one_teacher_sample_when_all_deltas_are_small(
) -> None:
    parameters = MasteryV2Parameters(prior_strength=0.0)
    report = compare_mastery_v1_v2(
        (
            _case(
                student_id="01",
                v1=0.8,
                awarded=8,
                full_score=10,
            ),
            _case(
                student_id="02",
                v1=0.6,
                awarded=6,
                full_score=10,
            ),
        ),
        as_of=AS_OF,
        parameters=parameters,
    )

    assert report.required_review_count == 1
    assert report.items[0].requires_review is True
    assert "teacher_sample_required" in report.items[0].reason_codes
    assert report.items[1].requires_review is False


def test_profile_adapter_uses_session_time_and_fails_missing_time_closed(
) -> None:
    profile = {
        "_mastery_session_times": {
            "7": "2026-01-15 08:00:00",
        },
        "students": [
            {
                "student_id": "12",
                "student_code": "S12",
                "student_name": "合成学生",
                "class_id": "合成班",
                "weak_points": [
                    _weak_point("一元一次方程", session_id=7),
                    _weak_point("等式的性质", session_id=8),
                    _weak_point("未治理名称", session_id=7),
                ],
            }
        ],
    }

    cases = build_profile_comparison_cases(profile)

    assert len(cases) == 2
    first = next(item for item in cases if item.display_name == "一元一次方程")
    missing = next(item for item in cases if item.display_name == "等式的性质")
    assert first.exam_evidence[0].status is EvidenceStatus.COMPLETED
    assert first.exam_evidence[0].occurred_at == datetime(
        2026,
        1,
        15,
        0,
        0,
        tzinfo=UTC,
    )
    assert missing.exam_evidence[0].status is EvidenceStatus.MISSING


def test_rollout_defaults_to_v1_and_enable_requires_every_required_acceptance(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    parameters = MasteryV2Parameters()
    repository.register_parameters(parameters)
    report = compare_mastery_v1_v2(
        (
            _case(
                student_id="01",
                v1=1.0,
                awarded=10,
                full_score=10,
            ),
            _case(
                student_id="02",
                v1=1.0,
                awarded=None,
                full_score=None,
                occurred_at=None,
                status=EvidenceStatus.MISSING,
            ),
        ),
        as_of=AS_OF,
        parameters=parameters,
    )
    gate = repository.record_evaluation(report)

    state = repository.get_state()
    assert state.enabled is False
    assert state.active_mode == "v1"
    assert repository.select_parameters() is None
    with pytest.raises(MasteryRolloutGateBlocked):
        repository.update_state(
            enabled=True,
            expected_revision=state.revision,
            actor_ref="teacher-synthetic",
            reason="尚未完成抽检",
            evaluation_id=report.evaluation_id,
        )

    for item in (item for item in report.items if item.requires_review):
        gate = repository.review_item(
            evaluation_id=report.evaluation_id,
            item_hash=item.item_hash,
            decision="accepted",
            teacher_ref="teacher-synthetic",
            reason="合成证据与解释可接受",
            expected_revision=gate.revision,
        )
    assert gate.passed is True

    enabled = repository.update_state(
        enabled=True,
        expected_revision=state.revision,
        actor_ref="teacher-synthetic",
        reason="所需差异均已核对",
        evaluation_id=report.evaluation_id,
    )
    assert enabled.active_mode == "v2"
    assert enabled.active_parameter_version == parameters.version
    assert repository.select_parameters() == parameters

    disabled = repository.update_state(
        enabled=False,
        expected_revision=enabled.revision,
        actor_ref="teacher-synthetic",
        reason="合成回退演练",
    )
    assert disabled.active_mode == "v1"
    assert disabled.active_parameter_version is None
    assert disabled.approved_evaluation_id is None
    assert repository.select_parameters() is None
    assert repository.evaluation_gate(report.evaluation_id).passed is True


def test_duplicate_review_is_idempotent_but_conflicts_are_immutable(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    parameters = MasteryV2Parameters()
    repository.register_parameters(parameters)
    report = compare_mastery_v1_v2(
        (
            _case(
                student_id="01",
                v1=1.0,
                awarded=10,
                full_score=10,
            ),
        ),
        as_of=AS_OF,
        parameters=parameters,
    )
    gate = repository.record_evaluation(report)
    item = report.items[0]
    accepted = repository.review_item(
        evaluation_id=report.evaluation_id,
        item_hash=item.item_hash,
        decision="accepted",
        teacher_ref="teacher-synthetic",
        reason="已核对",
        expected_revision=gate.revision,
    )
    duplicate = repository.review_item(
        evaluation_id=report.evaluation_id,
        item_hash=item.item_hash,
        decision="accepted",
        teacher_ref="teacher-synthetic",
        reason="已核对",
        expected_revision=gate.revision,
    )
    assert duplicate == accepted
    with pytest.raises(MasterySpotCheckConflict):
        repository.review_item(
            evaluation_id=report.evaluation_id,
            item_hash=item.item_hash,
            decision="rejected",
            teacher_ref="teacher-synthetic",
            reason="试图改写",
            expected_revision=accepted.revision,
        )


def test_concurrent_rollout_update_uses_revision_and_parameter_history_is_immutable(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    first = MasteryV2Parameters()
    second = MasteryV2Parameters(prior_mean=0.6)
    repository.register_parameters(first)
    repository.register_parameters(second)

    state = repository.get_state()
    updated = repository.update_state(
        enabled=False,
        expected_revision=state.revision,
        actor_ref="teacher-synthetic",
        reason="显式保持 v1",
    )
    with pytest.raises(MasteryRolloutRevisionConflict):
        repository.update_state(
            enabled=False,
            expected_revision=state.revision,
            actor_ref="teacher-other",
            reason="并发旧版本",
        )

    history = repository.list_parameters()
    assert [item["parameter_version"] for item in history] == sorted(
        [first.version, second.version]
    )
    assert repository.get_parameters(first.version) == first
    assert repository.get_parameters(second.version) == second
    assert updated.active_mode == "v1"


def test_p4_00_v1_golden_comparison_keeps_legacy_values_and_flags_large_delta(
) -> None:
    fixture_path = (
        Path(__file__).parent / "fixtures" / "p4_00_gold_set.json"
    )
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    cases: list[MasteryComparisonCase] = []
    for index, sample in enumerate(fixture["mastery_v1_golden"], start=1):
        evidence: list[ExamEvidence] = []
        for evidence_index, raw in enumerate(sample["evidence"], start=1):
            full_score = float(raw["full_score"])
            if full_score <= 0:
                evidence.append(
                    ExamEvidence(
                        evidence_id=f"gold-{index}-{evidence_index}",
                        stable_key=STABLE_KEY,
                        occurred_at=None,
                        score_awarded=None,
                        full_score=None,
                        status=EvidenceStatus.MISSING,
                    )
                )
            else:
                evidence.append(
                    ExamEvidence(
                        evidence_id=f"gold-{index}-{evidence_index}",
                        stable_key=STABLE_KEY,
                        occurred_at=AS_OF - timedelta(days=index),
                        score_awarded=min(
                            float(raw["score_awarded"]),
                            full_score,
                        ),
                        full_score=full_score,
                    )
                )
        cases.append(
            MasteryComparisonCase(
                student_id=f"gold-{index}",
                student_code=f"G{index}",
                student_name="合成 golden",
                class_id="合成班",
                stable_key=STABLE_KEY,
                display_name="一元一次方程",
                mastery_v1=float(sample["expected_mastery"]),
                exam_evidence=tuple(evidence),
            )
        )

    report = compare_mastery_v1_v2(tuple(cases), as_of=AS_OF)

    assert {item.mastery_v1 for item in report.items} == {
        0.7333,
        1.0,
    }
    assert any(
        item.absolute_delta is not None
        and item.absolute_delta > report.review_delta
        and item.requires_review
        for item in report.items
    )
    assert any(
        "availability_changed" in item.reason_codes
        for item in report.items
    )


def test_1000_case_comparison_records_performance_baseline() -> None:
    cases = tuple(
        _case(
            student_id=f"{index:04d}",
            v1=0.8,
            awarded=8,
            full_score=10,
            occurred_at=AS_OF - timedelta(days=index % 365),
        )
        for index in range(1000)
    )

    report = compare_mastery_v1_v2(cases, as_of=AS_OF)

    assert len(report.items) == 1000
    assert report.duration_ms < 2000
    assert report.items_per_second > 0


def _weak_point(label: str, *, session_id: int) -> dict[str, object]:
    return {
        "knowledge_point": label,
        "mastery": 0.8,
        "source_question_refs": [
            {
                "session_id": session_id,
                "question_id": "Q1",
                "bank_question_id": 1,
                "score_awarded": 8,
                "full_score": 10,
            }
        ],
    }
