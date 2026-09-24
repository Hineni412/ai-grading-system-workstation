from __future__ import annotations

import pytest

from integration.mastery_overview import build_mastery_overview

VOLUME_ID = "bnu24-math-g8-upper"
CHAPTER = "kp_bnu24_math_g8_upper_1"
SECTION = "kp_bnu24_math_g8_upper_1_1"
TOPIC = "kp_bnu24_math_g8_upper_1_1_1"
TOPIC_B = "kp_bnu24_math_g8_upper_1_1_2"
MISSING_FROM_CATALOG = "kp_bnu24_math_g8_upper_1_1_3"
SKILL_ON_SECTION = "sk_test_section_skill"
SKILL_ON_TOPIC = "sk_test_topic_skill"
SKILL_OUTSIDE = "sk_test_outside"


def _catalog_entry(key: str, kind: str, parent: str | None = None) -> dict:
    return {
        "knowledge_key": key,
        "knowledge_point": f"目录｜章｜节｜{key}",
        "parent_knowledge_key": parent,
        "parent_knowledge_point": parent,
        "node_kind": kind,
    }


def _point(key: str, mastery: float | None, evidence_count: int) -> dict:
    return {
        "knowledge_key": key,
        "knowledge_point": key,
        "mastery": mastery,
        "evidence_count": evidence_count,
    }


def _student(student_id: str, points: list[dict], score_rate=None) -> dict:
    return {
        "student_id": student_id,
        "student_code": f"S{student_id}",
        "student_name": f"学生{student_id}",
        "class_id": "八年级1班",
        "score_rate": score_rate,
        "score_rate_source": "current_exam" if score_rate is not None else "none",
        "weak_points": points,
    }


def _diagnosis(students: list[dict], group_mastery: dict[str, float] | None = None) -> dict:
    catalog = [
        _catalog_entry(CHAPTER, "chapter"),
        _catalog_entry(SECTION, "section", CHAPTER),
        _catalog_entry(TOPIC, "topic", SECTION),
        _catalog_entry(TOPIC_B, "topic", SECTION),
        _catalog_entry(SKILL_ON_SECTION, "skill", SECTION),
        _catalog_entry(SKILL_ON_TOPIC, "skill", TOPIC),
        _catalog_entry(SKILL_OUTSIDE, "skill", "kp_bnu24_math_g8_lower_1_1"),
    ]
    return {
        "scope": {"mode": "all"},
        "exam_scope": {
            "mode": "semester",
            "curriculum_volume_id": VOLUME_ID,
            "session_ids": [],
            "sessions": [],
        },
        "knowledge_catalog": catalog,
        "group_weak_points": [
            {"knowledge_key": key, "mastery": mastery}
            for key, mastery in (group_mastery or {}).items()
        ],
        "students": students,
        "warnings": ["示例警告"],
    }


def _node(overview: dict, key: str) -> dict:
    return next(node for node in overview["nodes"] if node["knowledge_key"] == key)


def _node_keys(overview: dict) -> list[str]:
    return [node["knowledge_key"] for node in overview["nodes"]]


def test_overview_rejects_empty_and_unknown_volume() -> None:
    with pytest.raises(ValueError):
        build_mastery_overview(_diagnosis([]), volume_id="")
    with pytest.raises(ValueError):
        build_mastery_overview(_diagnosis([]), volume_id="no-such-volume")


def test_overview_node_order_and_catalog_skip() -> None:
    overview = build_mastery_overview(_diagnosis([]), volume_id=VOLUME_ID)
    keys = _node_keys(overview)
    assert keys == [
        CHAPTER,
        SECTION,
        TOPIC,
        TOPIC_B,
        SKILL_ON_SECTION,
        SKILL_ON_TOPIC,
    ]
    assert MISSING_FROM_CATALOG not in keys
    assert SKILL_OUTSIDE not in keys
    assert _node(overview, CHAPTER)["kind"] == "chapter"
    assert _node(overview, CHAPTER)["section_key"] == ""
    assert _node(overview, SECTION)["kind"] == "section"
    assert _node(overview, SECTION)["section_key"] == SECTION
    assert _node(overview, SKILL_ON_TOPIC)["section_key"] == SECTION
    assert _node(overview, SKILL_ON_TOPIC)["chapter_key"] == CHAPTER


def test_overview_tier_boundaries_and_missing_evidence() -> None:
    students = [
        _student("1", [_point(TOPIC, 0.5999, 1)], score_rate=0.8),
        _student("2", [_point(TOPIC, 0.60, 1)], score_rate=0.6),
        _student("3", [_point(TOPIC, 0.7499, 1)]),
        _student("4", [_point(TOPIC, 0.75, 1)]),
        _student("5", [_point(TOPIC, None, 3)]),
        _student("6", [_point(TOPIC, 0.3, 0)]),
        _student("7", []),
    ]
    overview = build_mastery_overview(_diagnosis(students), volume_id=VOLUME_ID)
    node = _node(overview, TOPIC)
    assert node["distribution"] == {
        "weak": 1,
        "review": 2,
        "stable": 1,
        "missing": 3,
    }
    assert node["evidence_student_count"] == 4
    assert node["students"] == [
        {"student_id": "1", "mastery": 0.5999},
        {"student_id": "2", "mastery": 0.6},
        {"student_id": "3", "mastery": 0.7499},
        {"student_id": "4", "mastery": 0.75},
    ]


def test_overview_summary_and_student_counts() -> None:
    students = [
        _student(
            "1",
            [
                _point(TOPIC, 0.5, 2),
                _point(TOPIC_B, 0.7, 1),
                _point(SKILL_ON_SECTION, 0.4, 1),
                _point(CHAPTER, 0.55, 4),
            ],
            score_rate=0.8,
        ),
        _student("2", [_point(TOPIC, 0.9, 1)], score_rate=0.6),
        _student("3", []),
    ]
    overview = build_mastery_overview(
        _diagnosis(
            students,
            group_mastery={TOPIC: 0.5, SKILL_ON_SECTION: 0.55, TOPIC_B: 0.8},
        ),
        volume_id=VOLUME_ID,
    )
    summary = overview["summary"]
    assert summary["student_count"] == 3
    assert summary["evidence_student_count"] == 2
    assert summary["exam_student_count"] == 2
    assert summary["exam_score_rate"] == pytest.approx(0.7)
    assert summary["topic_count"] == 2
    assert summary["skill_count"] == 2
    assert summary["weak_topic_count"] == 1
    assert summary["weak_skill_count"] == 1

    by_id = {row["student_id"]: row for row in overview["students"]}
    assert by_id["1"]["topics"] == {
        "weak": 1,
        "review": 1,
        "stable": 0,
        "evidence": 2,
    }
    assert by_id["1"]["skills"] == {
        "weak": 1,
        "review": 0,
        "stable": 0,
        "evidence": 1,
    }
    assert by_id["2"]["topics"]["stable"] == 1
    assert by_id["3"]["topics"] == {
        "weak": 0,
        "review": 0,
        "stable": 0,
        "evidence": 0,
    }
    assert _node(overview, TOPIC)["group_mastery"] == 0.5
    assert _node(overview, SECTION)["group_mastery"] is None
    assert overview["warnings"] == ["示例警告"]
    assert overview["scope"]["mode"] == "all"
    assert overview["exam_scope"]["curriculum_volume_id"] == VOLUME_ID


def test_overview_no_scores_reports_none_rate() -> None:
    overview = build_mastery_overview(
        _diagnosis([_student("1", [])]), volume_id=VOLUME_ID
    )
    assert overview["summary"]["exam_student_count"] == 0
    assert overview["summary"]["exam_score_rate"] is None
