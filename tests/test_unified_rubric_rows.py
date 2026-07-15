from __future__ import annotations

import hashlib
import inspect

import pandas as pd
import pytest

from backend.config_workspace.editor import ConfigEditorValidationError
import web_app


def _payload() -> dict:
    return {
        "rubric": {
            "questions": [
                {
                    "question_id": "Q12",
                    "question_type": "proof",
                    "max_score": 6,
                    "knowledge_id": "K1",
                    "knowledge_name": "全等三角形判定",
                    "parts": [
                        {
                            "part_id": "P1",
                            "part_score": 6,
                            "response_mode": "process_required",
                            "knowledge_name": "三角形全等",
                            "knowledge_points": "使用AAS判定三角形全等并推出对应边相等",
                            "steps": [
                                {
                                    "step_id": "S1",
                                    "step_score": 3,
                                    "core_goal": "先证明两个三角形全等",
                                    "required_elements": ["列出全等条件"],
                                },
                                {
                                    "step_id": "S2",
                                    "step_score": 3,
                                    "core_goal": "再由全等得到对应边相等",
                                    "required_elements": ["写出对应边相等"],
                                },
                            ],
                        }
                    ],
                }
            ]
        },
        "answer_key": {
            "questions": [
                {
                    "question_id": "Q12",
                    "parts": [{"part_id": "Q12", "standard_answer": "证明略"}],
                }
            ]
        },
    }


def test_unified_rows_show_readable_labels_and_preserve_hidden_ids() -> None:
    rows = web_app.build_unified_rubric_rows(_payload())

    assert rows[0]["评分单元"] == "Q12"
    assert rows[0]["评分点"] == "先证明两个三角形全等"
    assert rows[1]["评分点"] == "再由全等得到对应边相等"
    assert rows[0]["标准答案"] == "证明略"
    assert rows[0]["_question_id"] == "Q12"
    assert rows[0]["_part_id"] == "P1"
    assert rows[0]["_step_id"] == "S1"
    assert rows[0]["_row_id"] == hashlib.sha256(b"Q12\0P1\0S1").hexdigest()[:24]
    assert rows[0]["知识点"] == "使用AAS判定三角形全等并推出对应边相等"


def test_unified_table_save_uses_hidden_ids_instead_of_display_labels() -> None:
    payload = _payload()
    rows = web_app.build_unified_rubric_rows(payload)
    rows[0]["分值"] = 4
    rows[1]["分值"] = 2

    updated = web_app._apply_unified_table_to_payload(payload, pd.DataFrame(rows))

    steps = updated["rubric"]["questions"][0]["parts"][0]["steps"]
    assert [step["step_id"] for step in steps] == ["S1", "S2"]
    assert [step["step_score"] for step in steps] == [4.0, 2.0]


def test_multiple_parts_use_question_order_labels() -> None:
    payload = _payload()
    question = payload["rubric"]["questions"][0]
    payload["answer_key"]["questions"][0]["parts"][0]["part_id"] = "P1"
    question["parts"].append(
        {
            "part_id": "P2",
            "part_score": 2,
            "response_mode": "short_answer_points",
            "steps": [{"step_id": "S1", "step_score": 2, "core_goal": "写出结果"}],
        }
    )
    payload["answer_key"]["questions"][0]["parts"].append({"part_id": "P2", "standard_answer": "2"})

    rows = web_app.build_unified_rubric_rows(payload)

    assert rows[0]["评分单元"] == "第(1)问"
    assert rows[-1]["评分单元"] == "第(2)问"


def test_unified_table_save_can_resolve_visible_labels_without_hidden_columns() -> None:
    payload = _payload()
    rows = web_app.build_unified_rubric_rows(payload)
    visible_rows = [
        {key: value for key, value in row.items() if not key.startswith("_")}
        for row in rows
    ]
    visible_rows[0]["分值"] = 4
    visible_rows[1]["分值"] = 2

    updated = web_app._apply_unified_table_to_payload(payload, pd.DataFrame(visible_rows))

    steps = updated["rubric"]["questions"][0]["parts"][0]["steps"]
    assert [step["step_id"] for step in steps] == ["S1", "S2"]
    assert [step["step_score"] for step in steps] == [4.0, 2.0]


def test_visible_only_reordered_rows_are_rejected_instead_of_miswritten() -> None:
    payload = _payload()
    rows = web_app.build_unified_rubric_rows(payload)
    visible_rows = [{key: value for key, value in row.items() if not key.startswith("_")} for row in reversed(rows)]

    with pytest.raises(ConfigEditorValidationError) as exc:
        web_app._apply_unified_table_to_payload(payload, pd.DataFrame(visible_rows))

    assert exc.value.issues[0]["code"] == "visible_row_identity_mismatch"


def test_manual_split_ui_uses_service_maximum() -> None:
    source = inspect.getsource(web_app._render_manual_scoring_unit_tools)

    assert "max_value=20" in source
    assert "max_value=30" not in source


def test_unified_rows_explain_complete_set_fill_blank_rule() -> None:
    payload = {
        "rubric": {
            "questions": [
                {
                    "question_id": "Q7",
                    "question_type": "fill_blank",
                    "max_score": 4,
                    "knowledge_id": "K7",
                    "knowledge_name": "等腰三角形分类讨论",
                    "parts": [
                        {
                            "part_id": "P1",
                            "part_score": 4,
                            "response_mode": "exact_objective",
                            "steps": [{"step_id": "S1", "step_score": 4, "core_goal": "填写全部正确答案"}],
                        }
                    ],
                }
            ]
        },
        "answer_key": {
            "questions": [
                {
                    "question_id": "Q7",
                    "canonical_answer": "72°或54°",
                    "match_mode": "complete_set",
                    "required_values": ["72°", "54°"],
                    "order_sensitive": False,
                    "allow_extra_values": False,
                    "partial_credit": False,
                    "parts": [{"part_id": "P1", "answer": "72°或54°"}],
                }
            ]
        },
    }

    rows = web_app.build_unified_rubric_rows(payload)

    assert rows[0]["作答匹配规则"] == "必须全部填写：72°、54°；顺序不限；少写、错写或多写均不得分"
