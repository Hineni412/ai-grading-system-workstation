from __future__ import annotations

from integration.skill_graph_projection import (
    build_question_tag_graph_evidence,
    build_question_tag_graph_nodes,
    build_question_tag_graph_rows,
    build_skill_graph_rows,
)


def test_projection_uses_skill_identity_and_keeps_source_refs() -> None:
    profile = {
        "students": [
            {
                "student_id": "5",
                "student_code": "S005",
                "student_name": "测试学生",
                "weak_points": [
                    {
                        "skill_id": 12,
                        "skill_name": "角平分线性质",
                        "topic_name": "三角形",
                        "mastery": 0.625,
                        "deduction_count": 2,
                        "evidence_count": 3,
                        "actionable_reasons": ["辅助线缺失"],
                        "source_question_refs": [
                            {"session_id": 7, "question_id": "Q12(1)"}
                        ],
                    }
                ],
            }
        ]
    }

    assert build_skill_graph_rows(profile) == [
        {
            "student_id": 5,
            "student_code": "S005",
            "student_name": "测试学生",
            "skill_id": 12,
            "knowledge_id": "skill:12",
            "knowledge_label": "角平分线性质",
            "topic_name": "三角形",
            "weighted_score_rate": 62.5,
            "deduction_count": 2,
            "item_count": 3,
            "sample_reasons": "辅助线缺失",
            "source_question_refs": [
                {"session_id": 7, "question_id": "Q12(1)"}
            ],
        }
    ]


def test_projection_skips_rows_without_positive_skill_identity() -> None:
    profile = {
        "students": [
            {
                "student_id": "5",
                "weak_points": [
                    {"skill_id": None, "skill_name": "未知"},
                    {"skill_id": 0, "skill_name": "无效"},
                    {"skill_id": "bad", "skill_name": "错误"},
                ],
            }
        ]
    }

    assert build_skill_graph_rows(profile) == []


def test_question_tag_projection_uses_exact_knowledge_identity() -> None:
    profile = {
        "diagnosis_identity": "question_tag",
        "students": [
            {
                "student_id": "5",
                "student_code": "S005",
                "student_name": "测试学生",
                "weak_points": [
                    {
                        "knowledge_key": "knowledge_point:三角形全等",
                        "knowledge_point": "三角形全等",
                        "mastery": 0.625,
                        "deduction_count": 2,
                        "evidence_count": 3,
                        "actionable_reasons": ["辅助线缺失"],
                        "source_question_refs": [
                            {"session_id": 7, "question_id": "Q12(1)"}
                        ],
                        "tag_context": {"method": ["构造辅助线"]},
                        "error_counts": {
                            "primary": {"辅助线思路缺失": 2},
                            "secondary": {"条件识别不完整": 1},
                        },
                    }
                ],
            }
        ],
    }

    assert build_question_tag_graph_rows(profile) == [
        {
            "student_id": 5,
            "student_code": "S005",
            "student_name": "测试学生",
            "knowledge_key": "knowledge_point:三角形全等",
            "knowledge_label": "三角形全等",
            "weighted_score_rate": 62.5,
            "deduction_count": 2,
            "item_count": 3,
            "sample_reasons": "辅助线缺失",
            "source_question_refs": [
                {
                    "session_id": 7,
                    "session_name": "",
                    "question_id": "Q12(1)",
                    "bank_question_id": 0,
                    "score_awarded": 0.0,
                    "full_score": 0.0,
                    "score_rate": None,
                }
            ],
            "tag_context": {"method": ["构造辅助线"]},
            "error_counts": {
                "primary": {"辅助线思路缺失": 2},
                "secondary": {"条件识别不完整": 1},
            },
        }
    ]


def _tag_graph_profile() -> dict[str, object]:
    shared_reference = {
        "session_id": 14,
        "session_name": "当前考试",
        "question_id": "Q1",
        "bank_question_id": 101,
        "score_awarded": 5.0,
        "full_score": 10.0,
        "score_rate": 0.5,
        "front_image": "C:/private/paper.png",
    }
    return {
        "diagnosis_identity": "question_tag",
        "students": [
            {
                "student_id": "12",
                "student_code": "S12",
                "student_name": "学生甲",
                "class_id": "八年级1班",
                "weak_points": [
                    {
                        "knowledge_key": "knowledge_point:三角形全等",
                        "knowledge_point": "三角形全等",
                        "mastery": 0.5,
                        "deduction_count": 2,
                        "evidence_count": 2,
                        "actionable_reasons": ["辅助线缺失"],
                        "source_question_refs": [
                            shared_reference,
                            dict(shared_reference),
                        ],
                        "tag_context": {"method": ["构造辅助线"]},
                        "error_counts": {
                            "primary": {"辅助线思路缺失": 1},
                            "secondary": {},
                        },
                    }
                ],
            },
            {
                "student_id": "15",
                "student_code": "S15",
                "student_name": "学生乙",
                "class_id": "八年级1班",
                "weak_points": [
                    {
                        "knowledge_key": "knowledge_point:三角形全等",
                        "knowledge_point": "三角形全等",
                        "mastery": 0.9,
                        "deduction_count": 1,
                        "evidence_count": 1,
                        "actionable_reasons": ["条件遗漏"],
                        "source_question_refs": [
                            {
                                "session_id": 15,
                                "session_name": "第二次考试",
                                "question_id": "Q2",
                                "bank_question_id": 102,
                                "score_awarded": 9.0,
                                "full_score": 10.0,
                                "score_rate": 0.9,
                            }
                        ],
                        "tag_context": {
                            "method": ["构造辅助线", "对应关系"],
                        },
                        "error_counts": {
                            "primary": {"辅助线思路缺失": 2},
                            "secondary": {"条件识别不完整": 1},
                        },
                    }
                ],
            },
        ],
    }


def test_tag_graph_nodes_aggregate_students_items_and_context() -> None:
    rows = build_question_tag_graph_rows(_tag_graph_profile())

    assert build_question_tag_graph_nodes(rows) == [
        {
            "knowledge_key": "knowledge_point:三角形全等",
            "knowledge_label": "三角形全等",
            "student_count": 2,
            "item_count": 3,
            "deduction_count": 3,
            "average_mastery": 0.6333,
            "tag_context": {
                "method": ["构造辅助线", "对应关系"],
            },
            "error_counts": {
                "primary": {"辅助线思路缺失": 3},
                "secondary": {"条件识别不完整": 1},
            },
        }
    ]


def test_tag_graph_evidence_is_deduplicated_sorted_and_path_free() -> None:
    items = build_question_tag_graph_evidence(
        _tag_graph_profile(),
        "knowledge_point:三角形全等",
    )

    assert [
        (item["student_id"], item["session_id"], item["question_id"])
        for item in items
    ] == [(12, 14, "Q1"), (15, 15, "Q2")]
    assert items[0] == {
        "student_id": 12,
        "student_code": "S12",
        "student_name": "学生甲",
        "class_id": "八年级1班",
        "knowledge_key": "knowledge_point:三角形全等",
        "knowledge_label": "三角形全等",
        "session_id": 14,
        "session_name": "当前考试",
        "question_id": "Q1",
        "bank_question_id": 101,
        "score_awarded": 5.0,
        "full_score": 10.0,
        "score_rate": 0.5,
        "tag_context": {"method": ["构造辅助线"]},
        "actionable_reasons": ["辅助线缺失"],
        "error_counts": {
            "primary": {"辅助线思路缺失": 1},
            "secondary": {},
        },
    }
    assert "C:/private" not in repr(items)


def test_tag_graph_rows_explicitly_drop_unapproved_reference_fields() -> None:
    rows = build_question_tag_graph_rows(_tag_graph_profile())

    assert rows[0]["source_question_refs"] == [
        {
            "session_id": 14,
            "session_name": "当前考试",
            "question_id": "Q1",
            "bank_question_id": 101,
            "score_awarded": 5.0,
            "full_score": 10.0,
            "score_rate": 0.5,
        },
        {
            "session_id": 14,
            "session_name": "当前考试",
            "question_id": "Q1",
            "bank_question_id": 101,
            "score_awarded": 5.0,
            "full_score": 10.0,
            "score_rate": 0.5,
        },
    ]
    assert "front_image" not in repr(rows)
    assert "C:/private" not in repr(rows)
