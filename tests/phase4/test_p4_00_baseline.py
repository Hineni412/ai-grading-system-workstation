from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "p4_00_gold_set.json"
FORBIDDEN_CRITERION_KEYS = {
    "score",
    "max_score",
    "step_score",
    "points_awarded",
}
REQUIRED_FAULT_CATEGORIES = {
    "duplicate_operation",
    "concurrent_operation",
    "mid_operation_exit",
    "restart",
    "retry_after_failure",
    "cancel",
    "partial_completion",
    "missing_data",
    "conflicting_data",
}


@pytest.fixture(scope="module")
def gold_set() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_gold_set_is_synthetic_and_bound_to_the_recorded_m0(
    gold_set: dict[str, Any],
) -> None:
    assert gold_set["schema_version"] == "p4-gold-set-v1"
    assert gold_set["baseline_commit"] == (
        "bb01d8db1e49aff6c7716c045e4d8918bc4371dc"
    )
    assert gold_set["synthetic_only"] is True


def test_every_p4_package_has_a_frozen_assertion(
    gold_set: dict[str, Any],
) -> None:
    expected_packages = {f"P4-{index:02d}" for index in range(18)}

    assert set(gold_set["package_assertion_map"]) == expected_packages
    assert all(gold_set["package_assertion_map"].values())


def test_relation_gold_set_has_positive_negative_and_conflict_examples(
    gold_set: dict[str, Any],
) -> None:
    kinds = {sample["kind"] for sample in gold_set["relation_samples"]}
    relation_types = {
        sample["relation_type"] for sample in gold_set["relation_samples"]
    }

    assert kinds == {"positive", "negative", "conflict"}
    assert relation_types == {"parent", "prerequisite", "related"}


def test_five_question_types_have_non_numeric_criterion_points(
    gold_set: dict[str, Any],
) -> None:
    samples = gold_set["criterion_samples"]

    assert {sample["question_type"] for sample in samples} == {
        "single_choice",
        "fill_blank",
        "calculation",
        "proof",
        "construction",
    }
    for sample in samples:
        point_ids = [point["point_id"] for point in sample["points"]]
        assert point_ids
        assert len(point_ids) == len(set(point_ids))
        for point in sample["points"]:
            assert point["target"]
            assert point["observable_evidence"]
            assert not FORBIDDEN_CRITERION_KEYS.intersection(point)


def test_current_v1_mastery_formula_matches_frozen_outputs(
    gold_set: dict[str, Any],
) -> None:
    for sample in gold_set["mastery_v1_golden"]:
        score_sum = sum(
            min(
                max(float(item["score_awarded"]), 0.0),
                max(float(item["full_score"]), 0.0),
            )
            for item in sample["evidence"]
        )
        full_score_sum = sum(
            max(float(item["full_score"]), 0.0)
            for item in sample["evidence"]
        )
        actual = round(
            score_sum / full_score_sum if full_score_sum > 0 else 1.0,
            4,
        )

        assert actual == sample["expected_mastery"], sample["id"]


def test_five_students_have_distinct_explainable_paper_scenarios(
    gold_set: dict[str, Any],
) -> None:
    students = gold_set["personalized_students"]
    paper_signatures = {
        tuple(student["expected_question_ids"]) for student in students
    }

    assert len(students) == 5
    assert len({student["student_id"] for student in students}) == 5
    assert len(paper_signatures) == 5
    assert all(student["expected_difference"] for student in students)


def test_fault_matrix_covers_required_failure_classes(
    gold_set: dict[str, Any],
) -> None:
    categories = {item["category"] for item in gold_set["fault_matrix"]}

    assert REQUIRED_FAULT_CATEGORIES <= categories
    assert all(item["trigger"] and item["expected"] for item in gold_set["fault_matrix"])


def test_stage_thresholds_preserve_cost_identity_and_exam_boundaries(
    gold_set: dict[str, Any],
) -> None:
    thresholds = gold_set["success_thresholds"]

    assert thresholds["invalid_relation_escape_max"] == 0
    assert thresholds["criterion_no_score_field_rate"] == 1.0
    assert thresholds["mixed_scan_routing_accuracy"] == 1.0
    assert thresholds["assessment_requests_per_student_max"] == 1
    assert thresholds["automatic_model_retries_max"] == 0
    assert thresholds["idempotency_duplicate_writes_max"] == 0
    assert thresholds["ordinary_exam_score_changes_max"] == 0
