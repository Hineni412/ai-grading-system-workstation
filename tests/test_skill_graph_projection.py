from __future__ import annotations

from integration.skill_graph_projection import build_skill_graph_rows


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
    from integration.skill_graph_projection import build_question_tag_graph_rows

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
            "source_question_refs": [{"session_id": 7, "question_id": "Q12(1)"}],
            "tag_context": {"method": ["构造辅助线"]},
            "error_counts": {
                "primary": {"辅助线思路缺失": 2},
                "secondary": {"条件识别不完整": 1},
            },
        }
    ]
