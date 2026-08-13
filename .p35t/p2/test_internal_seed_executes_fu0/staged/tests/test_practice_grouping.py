from __future__ import annotations

from pathlib import Path

import pytest

from question_bank.recommendation.practice_plan_service import PracticePlanService


@pytest.fixture
def service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PracticePlanService:
    planner = PracticePlanService(tmp_path / "question_bank.db")

    def fake_generate_variant(profile, **_kwargs):
        return {
            "student_ids": [
                str(item["student_id"]) for item in profile.get("students", [])
            ]
            or [str(profile["student_id"])],
            "items": [],
            "shortages": [],
            "warnings": [],
        }

    monkeypatch.setattr(planner, "generate_variant", fake_generate_variant)
    return planner


@pytest.fixture
def profile_set() -> dict:
    return {
        "diagnosis_identity": "question_tag",
        "scope": {"mode": "selected", "student_ids": ["12", "15", "18"]},
        "exam_scope": {"mode": "manual", "session_ids": [12, 14]},
        "students": [
            _student("12", 0.42, [(1, 0.35), (2, 0.5)]),
            _student("15", 0.46, [(1, 0.4), (2, 0.55)]),
            _student("18", 0.81, [(3, 0.65)]),
        ],
    }


def test_individual_mode_creates_one_variant_per_student(
    service: PracticePlanService,
    profile_set: dict,
) -> None:
    plan = service.generate(profile_set, variant_mode="individual")

    assert len(plan["variants"]) == 3
    assert all(item["variant_type"] == "individual" for item in plan["variants"])


def test_auto_group_mode_groups_students_with_the_same_question_tags(
    service: PracticePlanService,
    profile_set: dict,
) -> None:
    plan = service.generate(profile_set, variant_mode="auto_group")

    assert len(plan["variants"]) == 2
    assert sorted(len(item["student_ids"]) for item in plan["variants"]) == [1, 2]
    grouped = next(item for item in plan["variants"] if len(item["student_ids"]) == 2)
    assert grouped["grouping_reason"]["covered_knowledge_points"] == [
        "knowledge-1",
        "knowledge-2",
    ]


def test_unconfirmed_terms_never_drive_automatic_grouping(
    service: PracticePlanService,
    profile_set: dict,
) -> None:
    profile_set["students"][2]["weak_points"] = [
        {
            "knowledge_point": "unconfirmed",
            "eligible_for_recommendation": False,
            "mastery": 0.2,
        }
    ]

    plan = service.generate(profile_set, variant_mode="auto_group")

    assert plan["ungrouped_students"] == ["18"]
    assert next(item for item in plan["variants"] if item["student_ids"] == ["18"])["variant_type"] == "individual"


def test_teacher_override_assignments_are_recorded(
    service: PracticePlanService,
    profile_set: dict,
) -> None:
    plan = service.generate(
        profile_set,
        variant_mode="auto_group",
        teacher_groups={"教师指定A组": ["12", "18"]},
    )

    assert plan["teacher_override"]["applied"] is True
    assert plan["teacher_override"]["assignments"]["教师指定A组"] == ["12", "18"]
    assert any(item["student_ids"] == ["12", "18"] for item in plan["variants"])


def test_generation_config_records_original_exclusion_choice(
    service: PracticePlanService,
    profile_set: dict,
) -> None:
    plan = service.generate(
        profile_set,
        exclude_current_exam_originals=False,
    )

    assert plan["generation_config"]["exclude_current_exam_originals"] is False
    assert plan["generation_config"]["question_count"] == 10


def _student(student_id: str, score_rate: float, concepts: list[tuple[int, float]]) -> dict:
    return {
        "student_id": student_id,
        "student_name": f"学生{student_id}",
        "class_id": "九年级1班",
        "score_rate": score_rate,
        "weak_points": [
            {
                "knowledge_point": f"knowledge-{concept_id}",
                "eligible_for_recommendation": True,
                "mastery": mastery,
            }
            for concept_id, mastery in concepts
        ],
    }
