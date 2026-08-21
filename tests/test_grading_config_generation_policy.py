from __future__ import annotations

import copy
from pathlib import Path
import base64
from types import SimpleNamespace

import pytest

from backend.llm.gateway import LLMGateway
from backend.llm.trace import JsonlCallTraceSink, NullCallTraceSink
from backend.llm.usage import JsonlUsageSink, NullUsageSink
import score_policy
import session_manager
import llm_client
import usage_logger


def _scored_question(
    question_id: str,
    question_type: str,
    score: int,
    *,
    response_mode: str | None = None,
) -> dict:
    part = {
        "part_id": question_id,
        "part_score": score,
        "steps": [{"step_id": "S1", "step_score": score}],
    }
    if response_mode is not None:
        part["response_mode"] = response_mode
    return {
        "question_id": question_id,
        "question_type": question_type,
        "max_score": score,
        "parts": [part],
    }


def test_separate_answer_section_does_not_trigger_inline_parser() -> None:
    text = """
一、选择题
1. 下列结论正确的是（ ）
A. 甲
B. 乙
C. 丙
D. 丁

参考答案与解析：
1.
【答案】C
"""

    assert session_manager._parse_inline_answer_blocks(text) is None


def test_section_headings_and_multiline_options_drive_local_question_types() -> None:
    text = """
一、选择题：本题共2小题
1. 下列结论正确的是（ ）
A. 甲
B. 乙
C. 丙
D. 丁
2. 请选择正确结果（ ）
A. 1
B. 2
C. 3
D. 4
二、填空题：本题共1小题
3. 请写出结果：____
三、解答题：
4. 作出图形并保留作图痕迹。
5. 证明结论成立，并说明理由。

参考答案与解析：
1. 【答案】C
2. 【答案】B
3. 【答案】4
4. 【答案】略
5. 【答案】成立
"""

    blocks = session_manager.preview_question_blocks_from_docx_text(text)
    types = {block["question_id"]: block["question_type"] for block in blocks}
    answers = {block["question_id"]: block["canonical_answer"] for block in blocks}

    assert types == {
        "Q1": "choice",
        "Q2": "choice",
        "Q3": "fill_blank",
        "Q4": "comprehensive",
        "Q5": "proof",
    }
    assert answers["Q1"] == "C"
    assert answers["Q2"] == "B"
    assert answers["Q3"] == "4"


def test_plain_text_preview_splits_consecutive_main_question_mid_line() -> None:
    text = "\n".join(
        [
            "10. Tenth question.",
            "Continuation of question ten. 11. Eleventh question.",
            "12. Twelfth question.",
        ]
    )

    blocks = session_manager.preview_question_blocks_from_docx_text(text)

    assert [block["question_id"] for block in blocks] == ["Q10", "Q11", "Q12"]
    by_id = {block["question_id"]: block["question_text"] for block in blocks}
    assert "Eleventh question" not in by_id["Q10"]
    assert "Eleventh question" in by_id["Q11"]


def test_plain_text_preview_does_not_promote_nonconsecutive_inline_reference() -> None:
    text = "\n".join(
        [
            "10. Tenth question.",
            "The appendix reference 11. remains part of question ten.",
            "13. Thirteenth question.",
        ]
    )

    blocks = session_manager.preview_question_blocks_from_docx_text(text)

    assert [block["question_id"] for block in blocks] == ["Q10", "Q13"]
    assert "appendix reference 11." in blocks[0]["question_text"]


def test_plain_text_preview_does_not_promote_consecutive_inline_prose_number() -> None:
    text = "\n".join(
        [
            "10. Tenth question.",
            "According to clause 11. this remains part of question ten.",
            "12. Twelfth question.",
        ]
    )

    blocks = session_manager.preview_question_blocks_from_docx_text(text)

    assert [block["question_id"] for block in blocks] == ["Q10", "Q12"]
    assert "clause 11." in blocks[0]["question_text"]


def test_plain_text_inline_answers_still_split_consecutive_main_question() -> None:
    text = "\n".join(
        [
            "10. Tenth question.",
            "【答案】A",
            "End of question ten. 11. Eleventh question.",
            "【答案】B",
            "12. Twelfth question.",
            "【答案】C",
        ]
    )

    blocks = session_manager.preview_question_blocks_from_docx_text(text)

    assert [block["question_id"] for block in blocks] == ["Q10", "Q11", "Q12"]
    by_id = {block["question_id"]: block for block in blocks}
    assert "Eleventh question" in by_id["Q11"]["question_text"]
    assert by_id["Q11"]["canonical_answer"] == "B"
    assert by_id["Q12"]["canonical_answer"] == "C"


@pytest.mark.parametrize(
    "continuation",
    [
        "The measured value is 11.5 units.",
        "Subpart (11) remains part of question ten.",
    ],
)
def test_plain_text_preview_does_not_split_decimal_or_subpart(
    continuation: str,
) -> None:
    text = "\n".join(
        [
            "10. Tenth question.",
            continuation,
            "11. Eleventh question.",
            "12. Twelfth question.",
        ]
    )

    blocks = session_manager.preview_question_blocks_from_docx_text(text)

    assert [block["question_id"] for block in blocks] == ["Q10", "Q11", "Q12"]
    assert continuation in blocks[0]["question_text"]


def test_plain_text_preview_splits_multiple_consecutive_markers_in_one_line() -> None:
    text = "\n".join(
        [
            "10. Tenth question.",
            "Continuation. 11. Eleventh question. 12. Twelfth question.",
            "13. Thirteenth question.",
        ]
    )

    blocks = session_manager.preview_question_blocks_from_docx_text(text)

    assert [block["question_id"] for block in blocks] == [
        "Q10",
        "Q11",
        "Q12",
        "Q13",
    ]


def test_confirmed_question_type_is_sent_to_ai_and_restored_after_merge(monkeypatch: pytest.MonkeyPatch) -> None:
    confirmed = {
        "question_id": "Q5",
        "question_type": "proof",
        "question_type_confirmed": True,
        "text": "判断结论是否成立，并说明理由。",
    }
    prompt = session_manager._build_single_question_generation_prompt(confirmed, "")
    payload = {
        "rubric": {
            "questions": [
                {
                    "question_id": "Q5",
                    "question_type": "calculation",
                    "grading_mode": "deductive_obligation",
                }
            ]
        },
        "answer_key": {"questions": []},
    }

    session_manager._apply_local_question_facts(payload, [confirmed])
    session_manager.normalize_generated_config_schema(payload)

    assert "教师已确认题型：proof" in prompt
    assert payload["rubric"]["questions"][0]["question_type"] == "proof"
    assert payload["rubric"]["questions"][0]["question_type_confirmed"] is True

    captured: dict[str, object] = {}

    def fake_generate(blocks, doc_text, llm_client, model_name=None, report=None, q_images=None):
        captured["blocks"] = blocks
        return {"ok": True}

    monkeypatch.setattr(session_manager, "_generate_grading_config_by_question_blocks", fake_generate)
    result = session_manager.generate_grading_config_from_confirmed_blocks(
        [{"question_id": "Q5", "question_type": "proof", "text": "说明理由"}],
        "",
        object(),
    )

    assert result == {"ok": True}
    assert captured["blocks"][0]["question_type"] == "proof"
    assert captured["blocks"][0]["question_type_confirmed"] is True


def test_pdf_image_prompt_does_not_include_extracted_pdf_text() -> None:
    block = {
        "question_id": "Q10",
        "question_type": "comprehensive",
        "question_type_confirmed": True,
        "text": "CORRUPTED_PDF_TEXT PB+PD became PA+PB",
        "answer_text": "CORRUPTED_PDF_ANSWER",
        "semantic_source": "images",
    }

    prompt = session_manager._build_single_question_image_generation_prompt(
        block,
        has_answer_image=True,
    )

    assert "CORRUPTED_PDF_TEXT" not in prompt
    assert "CORRUPTED_PDF_ANSWER" not in prompt
    assert "图片1是题目原图" in prompt
    assert "图片2是标准答案与解析原图" in prompt
    assert "图片是唯一权威内容来源" in prompt
    assert "response_mode" in prompt
    assert "knowledge_name" not in prompt
    assert "knowledge_id" not in prompt


def test_image_semantic_source_does_not_overwrite_ai_answer_with_corrupt_local_text() -> None:
    payload = {
        "rubric": {
            "questions": [
                {
                    "question_id": "Q9",
                    "question_type": "fill_blank",
                    "stem_summary": "一次函数实际应用",
                    "knowledge_id": "一次函数",
                    "knowledge_name": "一次函数实际应用",
                }
            ]
        },
        "answer_key": {
            "questions": [
                {
                    "question_id": "Q9",
                    "canonical_answer": "y=48x+20",
                    "accepted_forms": ["y=48x+20"],
                }
            ]
        },
    }
    local_block = {
        "question_id": "Q9",
        "question_type": "fill_blank",
        "question_type_confirmed": True,
        "semantic_source": "images",
        "canonical_answer": "�=48�+20",
        "local_answer_trusted": True,
        "question_text": "乱码题干�",
    }

    session_manager._apply_local_question_facts(payload, [local_block])

    question = payload["rubric"]["questions"][0]
    answer = payload["answer_key"]["questions"][0]
    assert question["stem_summary"] == "一次函数实际应用"
    assert answer["canonical_answer"] == "y=48x+20"


def test_score_prompt_omits_document_text_for_image_semantic_source() -> None:
    prompt = session_manager._build_score_allocation_prompt(
        [{"question_id": "Q1", "question_type": "choice", "parts": []}],
        "CORRUPTED_PDF_TEXT",
        include_document_text=False,
    )

    assert "CORRUPTED_PDF_TEXT" not in prompt


def test_image_semantic_generation_rejects_missing_crop_state_before_ai_calls() -> None:
    class FakeClient:
        def json_from_text(self, *_args, **_kwargs):
            raise AssertionError("PDF image-semantic generation must never fall back to text")

        def json_from_images(self, *_args, **_kwargs):
            raise AssertionError("Missing image state must fail before AI calls")

    with pytest.raises(ValueError, match="图片裁题状态已丢失"):
        session_manager.generate_grading_config_from_confirmed_blocks(
            [
                {
                    "question_id": "Q1",
                    "question_type": "choice",
                    "semantic_source": "images",
                }
            ],
            "CORRUPTED_PDF_TEXT",
            FakeClient(),
            q_images=None,
        )


def test_image_semantic_generation_rejects_invalid_crop_without_text_fallback() -> None:
    class FakeClient:
        def json_from_text(self, *_args, **_kwargs):
            raise AssertionError("Invalid PDF crop must never fall back to text")

        def json_from_images(self, *_args, **_kwargs):
            raise AssertionError("Invalid image state must fail before AI calls")

    with pytest.raises(ValueError, match="图片裁题数据无效"):
        session_manager.generate_grading_config_from_confirmed_blocks(
            [
                {
                    "question_id": "Q1",
                    "question_type": "choice",
                    "semantic_source": "images",
                }
            ],
            "CORRUPTED_PDF_TEXT",
            FakeClient(),
            q_images={"Q1": None},
        )


def test_solution_hard_rules_respect_part_response_modes() -> None:
    question = {
        "question_id": "Q11",
        "question_type": "comprehensive",
        "max_score": 10,
        "parts": [
            {
                "part_id": "Q11(1)",
                "part_score": 4,
                "response_mode": "short_answer_points",
                "steps": [
                    {"step_id": "S1", "step_score": 2, "core_goal": "写出第一个正确结果"},
                    {"step_id": "S2", "step_score": 2, "core_goal": "写出第二个正确结果"},
                ],
            },
            {
                "part_id": "Q11(2)",
                "part_score": 6,
                "response_mode": "process_required",
                "steps": [{"step_id": "S1", "step_score": 6, "core_goal": "完成证明"}],
            },
        ],
    }

    session_manager._ensure_solution_hard_rules(question)

    direct_part, process_part = question["parts"]
    assert direct_part["answer_only_max_score"] == 4
    assert direct_part["require_final_answer"] is False
    assert not any(rule.get("rule_id") == "final_answer_required" for rule in direct_part["presentation_rules"])
    assert process_part["answer_only_max_score"] < 6
    assert question["answer_only_max_score"] == direct_part["answer_only_max_score"] + process_part["answer_only_max_score"]


def test_solution_hard_rules_infer_visual_construction_mode() -> None:
    question = {
        "question_id": "Q10",
        "question_type": "comprehensive",
        "max_score": 6,
        "parts": [
            {
                "part_id": "Q10",
                "part_score": 6,
                "steps": [{"step_id": "S1", "step_score": 6, "core_goal": "完成尺规作图并保留作图痕迹"}],
            }
        ],
    }

    session_manager._ensure_solution_hard_rules(question)

    part = question["parts"][0]
    assert part["response_mode"] == "visual_construction"
    assert part["answer_only_max_score"] == 6


def test_reference_answer_images_are_persisted_in_answer_key() -> None:
    payload = {
        "rubric": {"questions": [{"question_id": "Q10"}]},
        "answer_key": {"questions": [{"question_id": "Q10", "canonical_answer": ""}]},
    }
    image_b64 = base64.b64encode(b"answer-image").decode("ascii")

    session_manager._attach_reference_answer_images(
        payload,
        {"Q10": {"question": base64.b64encode(b"question-image").decode("ascii"), "answer": image_b64}},
    )

    answer = payload["answer_key"]["questions"][0]
    assert answer["answer_image_base64"] == image_b64
    assert answer["answer_image_role"] == "perfect_standard_answer"


def test_single_answer_part_is_aligned_to_single_rubric_part() -> None:
    payload = {
        "rubric": {
            "questions": [
                {
                    "question_id": "Q2",
                    "question_type": "choice",
                    "max_score": 6,
                    "parts": [{"part_id": "P1", "part_score": 6, "steps": [{"step_id": "S1", "step_score": 6}]}],
                }
            ]
        },
        "answer_key": {
            "questions": [
                {
                    "question_id": "Q2",
                    "canonical_answer": "B",
                    "parts": [{"part_id": "Q2", "standard_answer": "B"}],
                }
            ]
        },
    }

    session_manager.normalize_generated_config_schema(payload)

    answer_part = payload["answer_key"]["questions"][0]["parts"][0]
    assert answer_part["part_id"] == "P1"
    assert answer_part["answer"] == "B"


def test_quality_warnings_detect_garbled_content_missing_answers_and_stem_knowledge() -> None:
    payload = {
        "rubric": {
            "questions": [
                {
                    "question_id": "Q1",
                    "question_type": "fill_blank",
                    "stem_summary": "如图，��平分��",
                    "knowledge_id": "UNKNOWN",
                    "knowledge_name": "如图，��平分��",
                    "parts": [
                        {
                            "part_id": "Q1",
                            "response_mode": "exact_objective",
                            "steps": [{"step_id": "S1", "core_goal": "写出答案"}],
                        }
                    ],
                }
            ]
        },
        "answer_key": {"questions": [{"question_id": "Q1", "canonical_answer": "", "parts": []}]},
    }

    warnings = session_manager.collect_generated_config_quality_warnings(payload)

    assert not any("knowledge" in warning.lower() for warning in warnings)
    assert not any("疑似乱码" in warning for warning in warnings)
    assert any("缺少可评分的文本标准答案" in warning for warning in warnings)


def test_answer_image_satisfies_visual_answer_quality_check() -> None:
    payload = {
        "rubric": {
            "questions": [
                {
                    "question_id": "Q10",
                    "question_type": "comprehensive",
                    "knowledge_id": "K1",
                    "knowledge_name": "尺规作图",
                    "parts": [
                        {
                            "part_id": "Q10",
                            "response_mode": "visual_construction",
                            "steps": [{"step_id": "S1", "core_goal": "完成尺规作图"}],
                        }
                    ],
                }
            ]
        },
        "answer_key": {
            "questions": [
                {
                    "question_id": "Q10",
                    "canonical_answer": "",
                    "answer_image_base64": base64.b64encode(b"answer-image").decode("ascii"),
                    "parts": [],
                }
            ]
        },
    }

    warnings = session_manager.collect_generated_config_quality_warnings(payload)

    assert not any("缺少可评分的文本标准答案" in warning for warning in warnings)


def test_normalization_recovers_image_mode_aliases_and_nested_knowledge() -> None:
    payload = {
        "rubric": {
            "questions": [
                {
                    "question_id": "Q7",
                    "question_type": "fill_blank",
                    "max_score": 4,
                    "knowledge_id": "UNKNOWN",
                    "parts": [
                        {
                            "part_id": "Q7",
                            "part_score": 4,
                            "response_mode": "short_answer_points",
                            "knowledge_id": "等腰三角形",
                            "knowledge_name": "等腰三角形",
                            "knowledge_points": "等腰三角形内角性质、三角形内角和定理应用",
                            "steps": [
                                {
                                    "core_goal": "完成必要的推理或计算步骤",
                                    "required_elements": ["合理的推理过程", "正确的结论"],
                                }
                            ],
                            "points": [
                                {"desc": "答出底角度数为72°", "core_goal": "完成必要的推理或计算步骤", "score": 2},
                                {"desc": "答出底角度数为54°", "core_goal": "完成必要的推理或计算步骤", "score": 2},
                            ],
                        }
                    ],
                }
            ]
        },
        "answer_key": {
            "questions": [
                {
                    "question_id": "Q7",
                    "answers": ["72°", "54°"],
                }
            ]
        },
    }

    session_manager.normalize_generated_config_schema(payload)

    question = payload["rubric"]["questions"][0]
    answer = payload["answer_key"]["questions"][0]
    assert question["knowledge_name"] == "等腰三角形"
    assert any(
        point["knowledge_name"] == "等腰三角形内角性质、三角形内角和定理应用"
        for point in question["knowledge_points"]
    )
    assert question["parts"][0]["response_mode"] == "exact_objective"
    assert [step["core_goal"] for step in question["parts"][0]["steps"]] == ["填写正确或等价的答案"]
    assert answer["canonical_answer"] == "72°"
    assert {"72°", "54°"}.issubset(set(answer["accepted_forms"]))


def test_normalization_preserves_existing_model_step_without_phrase_rewrite() -> None:
    payload = {
        "rubric": {
            "questions": [
                {
                    "question_id": "Q12",
                    "question_type": "proof",
                    "max_score": 4,
                    "parts": [
                        {
                            "part_id": "Q12",
                            "part_score": 4,
                            "response_mode": "process_required",
                            "steps": [
                                {
                                    "core_goal": "完成必要的推理或计算步骤",
                                    "required_elements": ["合理的推理过程"],
                                }
                            ],
                            "score_points": [
                                {
                                    "desc": "先证明两个三角形全等",
                                    "core_goal": "完成必要的推理或计算步骤",
                                    "score": 2,
                                },
                                {
                                    "desc": "再由全等得到对应边相等",
                                    "core_goal": "完成必要的推理或计算步骤",
                                    "score": 2,
                                },
                            ],
                        }
                    ],
                }
            ]
        },
        "answer_key": {"questions": [{"question_id": "Q12", "canonical_answer": "证明略"}]},
    }

    session_manager.normalize_generated_config_schema(payload)

    steps = payload["rubric"]["questions"][0]["parts"][0]["steps"]
    assert [step["core_goal"] for step in steps] == ["完成必要的推理或计算步骤"]
    assert steps[0]["required_elements"] == ["合理的推理过程"]


def test_normalization_recovers_answers_without_rewriting_visual_steps() -> None:
    payload = {
        "rubric": {
            "questions": [
                {
                    "question_id": "Q10",
                    "question_type": "comprehensive",
                    "max_score": 6,
                    "parts": [
                        {
                            "part_id": "Q10(1)",
                            "part_score": 3,
                            "response_mode": "visual_construction",
                            "visual_requirements": ["作出A、B、C关于直线l的对称点", "顺次连接对应点"],
                            "steps": [{"core_goal": "完成必要的推理或计算步骤"}],
                        },
                        {
                            "part_id": "Q10(2)",
                            "part_score": 3,
                            "response_mode": "visual_construction",
                            "visual_requirements": ["连接B'D并取与直线l的交点P"],
                            "steps": [{"core_goal": "完成必要的推理或计算步骤"}],
                        },
                    ],
                }
            ]
        },
        "answer_key": {
            "questions": [
                {
                    "question_id": "Q10",
                    "answer_parts": [
                        {"part_id": "Q10(1)", "answer_content": "见标准答案图"},
                        {"part_id": "Q10(2)", "answer_content": "P点位置如图"},
                    ],
                }
            ]
        },
    }

    session_manager.normalize_generated_config_schema(payload)

    question = payload["rubric"]["questions"][0]
    answer = payload["answer_key"]["questions"][0]
    assert [step["core_goal"] for step in question["parts"][0]["steps"]] == [
        "完成必要的推理或计算步骤"
    ]
    assert question["parts"][0]["visual_requirements"] == [
        "作出A、B、C关于直线l的对称点",
        "顺次连接对应点",
    ]
    assert [part["answer"] for part in answer["parts"]] == ["见标准答案图", "P点位置如图"]


def test_normalization_recovers_nested_answer_values() -> None:
    payload = {
        "rubric": {
            "questions": [
                {
                    "question_id": "Q11",
                    "question_type": "calculation",
                    "max_score": 4,
                    "parts": [
                        {
                            "part_id": "Q11(1)",
                            "part_score": 2,
                            "response_mode": "short_answer_points",
                            "steps": [{"description": "求出小明速度"}, {"description": "求出妈妈速度"}],
                        },
                        {"part_id": "Q11(2)", "part_score": 2, "steps": [{"description": "求出相遇时间"}]},
                    ],
                }
            ]
        },
        "answer_key": {
            "questions": [
                {
                    "question_id": "Q11",
                    "parts": [
                        {"part_id": "Q11(1)", "answers": [{"value": "6"}, {"value": "2"}]},
                        {"part_id": "Q11(2)", "answers": [{"value": "35秒"}]},
                    ],
                }
            ]
        },
    }

    session_manager.normalize_generated_config_schema(payload)

    answer_parts = payload["answer_key"]["questions"][0]["parts"]
    assert answer_parts[0]["answer"] == "6；2"
    assert answer_parts[0]["answer_values"] == ["6", "2"]
    assert answer_parts[1]["answer"] == "35秒"
    steps = payload["rubric"]["questions"][0]["parts"][0]["steps"]
    assert steps[0]["required_elements"] == ["6"]
    assert steps[1]["required_elements"] == ["2"]


def test_choice_and_fill_blank_are_forced_to_objective_scoring() -> None:
    payload = {
        "rubric": {
            "questions": [
                {
                    "question_id": "Q3",
                    "question_type": "choice",
                    "max_score": 5,
                    "parts": [
                        {
                            "part_id": "Q3",
                            "part_score": 5,
                            "response_mode": "process_required",
                            "steps": [
                                {
                                    "step_id": "S1",
                                    "step_score": 5,
                                    "core_goal": "完成必要的推理或计算步骤",
                                    "required_elements": ["合理的推理过程", "正确的结论"],
                                }
                            ],
                            "presentation_rules": [{"rule_id": "final_answer_required"}],
                        }
                    ],
                }
            ]
        },
        "answer_key": {"questions": [{"question_id": "Q3", "canonical_answer": "A"}]},
    }

    session_manager.normalize_generated_config_schema(payload)

    question = payload["rubric"]["questions"][0]
    part = question["parts"][0]
    step = part["steps"][0]
    assert part["response_mode"] == "exact_objective"
    assert part["presentation_rules"] == []
    assert step["core_goal"] == "选择正确的选项"
    assert step["required_elements"] == ["A"]
    assert question.get("deduction_policy", []) == [
        "仅按标准答案或等价答案判分，不要求书写过程。"
    ]


def test_choice_normalization_removes_non_option_equivalent_answers() -> None:
    payload = {
        "rubric": {
            "questions": [
                {
                    "question_id": "Q2",
                    "question_type": "choice",
                    "max_score": 6,
                    "parts": [
                        {
                            "part_id": "P1",
                            "part_score": 6,
                            "response_mode": "exact_objective",
                            "steps": [{"step_id": "S1", "step_score": 6, "core_goal": "选择正确的选项"}],
                        }
                    ],
                }
            ]
        },
        "answer_key": {
            "questions": [
                {
                    "question_id": "Q2",
                    "canonical_answer": "B",
                    "accepted_forms": ["8"],
                    "parts": [{"part_id": "P1", "answer": "B", "accepted_forms": ["8"]}],
                }
            ]
        },
    }

    session_manager.normalize_generated_config_schema(payload)

    answer = payload["answer_key"]["questions"][0]
    assert answer["accepted_forms"] == ["B"]
    assert answer["parts"][0]["accepted_forms"] == ["B"]
    assert payload["rubric"]["questions"][0]["parts"][0]["steps"][0]["required_elements"] == ["B"]


def test_multi_value_fill_blank_records_complete_set_rule() -> None:
    payload = {
        "rubric": {
            "questions": [
                {
                    "question_id": "Q7",
                    "question_type": "fill_blank",
                    "max_score": 4,
                    "parts": [
                        {
                            "part_id": "P1",
                            "part_score": 4,
                            "response_mode": "exact_objective",
                            "steps": [{"step_id": "S1", "step_score": 4, "core_goal": "填写正确答案"}],
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
                    "accepted_forms": ["54°或72°"],
                    "parts": [{"part_id": "P1", "answer": "72°或54°", "accepted_forms": ["54°或72°"]}],
                }
            ]
        },
    }

    session_manager.normalize_generated_config_schema(payload)

    answer = payload["answer_key"]["questions"][0]
    assert answer["match_mode"] == "complete_set"
    assert answer["required_values"] == ["72°", "54°"]
    assert answer["order_sensitive"] is False
    assert answer["allow_extra_values"] is False
    assert answer["partial_credit"] is False


def test_specific_subjective_steps_gain_machine_readable_required_elements() -> None:
    payload = {
        "rubric": {
            "questions": [
                {
                    "question_id": "Q12",
                    "question_type": "proof",
                    "max_score": 4,
                    "parts": [
                        {
                            "part_id": "P1",
                            "part_score": 4,
                            "response_mode": "process_required",
                            "steps": [
                                {"step_id": "S1", "step_score": 2, "core_goal": "先证明两个三角形全等"},
                                {"step_id": "S2", "step_score": 2, "core_goal": "由全等得到对应边相等"},
                            ],
                        }
                    ],
                }
            ]
        },
        "answer_key": {"questions": [{"question_id": "Q12", "canonical_answer": "证明略"}]},
    }

    session_manager.normalize_generated_config_schema(payload)

    steps = payload["rubric"]["questions"][0]["parts"][0]["steps"]
    assert steps[0]["required_elements"] == ["先证明两个三角形全等"]
    assert steps[1]["required_elements"] == ["由全等得到对应边相等"]


def test_subjective_top_level_equivalents_do_not_absorb_part_answers_or_list_strings() -> None:
    payload = {
        "rubric": {
            "questions": [
                {
                    "question_id": "Q11",
                    "question_type": "calculation",
                    "max_score": 10,
                    "parts": [
                        {"part_id": "P1", "part_score": 4, "steps": [{"step_id": "S1", "step_score": 4}]},
                        {"part_id": "P2", "part_score": 6, "steps": [{"step_id": "S1", "step_score": 6}]},
                    ],
                }
            ]
        },
        "answer_key": {
            "questions": [
                {
                    "question_id": "Q11",
                    "canonical_answer": "(1)6，2；(2)300",
                    "accepted_forms": ["['6','2']", "300", "3"],
                    "parts": [
                        {"part_id": "P1", "answer": "6，2", "accepted_forms": ["['6','2']"]},
                        {"part_id": "P2", "answer": "300", "accepted_forms": ["300"]},
                    ],
                }
            ]
        },
    }

    session_manager.normalize_generated_config_schema(payload)

    answer = payload["answer_key"]["questions"][0]
    assert "['6','2']" not in answer["accepted_forms"]
    assert "300" not in answer["accepted_forms"]
    assert "3" not in answer["accepted_forms"]
    assert answer["parts"][0]["accepted_forms"] == ["6，2"]
    assert "3" not in answer["parts"][1]["accepted_forms"]
    assert answer["parts"][0]["answer"] == "6，2"


def test_subjective_top_level_serialized_list_is_rebuilt_from_matching_parts() -> None:
    payload = {
        "rubric": {
            "questions": [
                {
                    "question_id": "Q12",
                    "question_type": "proof",
                    "max_score": 6,
                    "parts": [
                        {"part_id": "P1", "part_score": 2, "steps": []},
                        {"part_id": "P2", "part_score": 2, "steps": []},
                        {"part_id": "P3", "part_score": 2, "steps": []},
                    ],
                }
            ]
        },
        "answer_key": {
            "questions": [
                {
                    "question_id": "Q12",
                    "canonical_answer": "['22.5', '∠1=∠3', 'AB=BD+DH']",
                    "accepted_forms": [
                        "['22.5', '∠1＝∠3', 'AB＝BD+DH']",
                    ],
                    "parts": [
                        {"part_id": "P1", "answer": "22.5"},
                        {"part_id": "P2", "answer": "∠1=∠3"},
                        {"part_id": "P3", "answer": "AB=BD+DH"},
                    ],
                }
            ]
        },
    }

    session_manager.normalize_generated_config_schema(payload)

    answer = payload["answer_key"]["questions"][0]
    assert answer["canonical_answer"] == "（1）22.5；（2）∠1=∠3；（3）AB=BD+DH"
    assert answer["accepted_forms"]
    assert not any(
        session_manager._looks_like_serialized_answer_list(value)
        for value in answer["accepted_forms"]
    )
    assert [part["answer"] for part in answer["parts"]] == [
        "22.5",
        "∠1=∠3",
        "AB=BD+DH",
    ]
    warnings = session_manager.collect_generated_config_quality_warnings(payload)
    assert not any("列表字符串" in warning for warning in warnings)


def test_subjective_top_level_serialized_list_stays_blocked_when_part_count_differs() -> None:
    payload = {
        "rubric": {
            "questions": [
                {
                    "question_id": "Q12",
                    "question_type": "proof",
                    "max_score": 6,
                    "parts": [
                        {"part_id": "P1", "part_score": 2, "steps": []},
                        {"part_id": "P2", "part_score": 2, "steps": []},
                        {"part_id": "P3", "part_score": 2, "steps": []},
                    ],
                }
            ]
        },
        "answer_key": {
            "questions": [
                {
                    "question_id": "Q12",
                    "canonical_answer": "['22.5', '∠1=∠3']",
                    "parts": [
                        {"part_id": "P1", "answer": "22.5"},
                        {"part_id": "P2", "answer": "∠1=∠3"},
                        {"part_id": "P3", "answer": "AB=BD+DH"},
                    ],
                }
            ]
        },
    }

    session_manager.normalize_generated_config_schema(payload)

    warnings = session_manager.collect_generated_config_quality_warnings(payload)
    assert not any("列表字符串" in warning for warning in warnings)
    assert sum("过程题需要至少两个可区分步骤" in warning for warning in warnings) == 3


def test_generated_config_normalization_recovers_paired_knowledge_list_strings_idempotently() -> None:
    payload = {
        "rubric": {
            "total_score": 12,
            "questions": [
                {
                    "question_id": "Q12",
                    "question_type": "proof",
                    "max_score": 12,
                    "knowledge_id": "['K-CONGRUENCE', 'K-BISECTOR', 'K-PROOF']",
                    "knowledge_name": "['全等三角形', '角平分线性质', '几何证明']",
                    "knowledge_ids": ["['K-CONGRUENCE', 'K-BISECTOR', 'K-PROOF']"],
                    "knowledge_points": [
                        {
                            "knowledge_id": "['K-CONGRUENCE', 'K-BISECTOR', 'K-PROOF']",
                            "knowledge_name": "['全等三角形', '角平分线性质', '几何证明']",
                        },
                        {
                            "knowledge_id": "['K-CONGRUENCE', 'K-BISECTOR', 'K-PROOF']",
                            "knowledge_name": "",
                        },
                    ],
                    "parts": [],
                }
            ],
        },
        "answer_key": {
            "questions": [
                {
                    "question_id": "Q12",
                    "canonical_answer": "证明结论成立",
                    "accepted_forms": ["证明结论成立"],
                }
            ]
        },
    }
    original_answer = copy.deepcopy(payload["answer_key"]["questions"][0])

    session_manager.normalize_generated_config_schema(payload)
    once = copy.deepcopy(payload["rubric"]["questions"][0])
    changed_again = session_manager.normalize_generated_config_knowledge_fields(payload)

    question = payload["rubric"]["questions"][0]
    assert question["knowledge_id"] == "K-CONGRUENCE"
    assert question["knowledge_name"] == "全等三角形"
    assert question["knowledge_ids"] == ["K-CONGRUENCE", "K-BISECTOR", "K-PROOF"]
    assert question["knowledge_points"] == [
        {"knowledge_id": "K-CONGRUENCE", "knowledge_name": "全等三角形"},
        {"knowledge_id": "K-BISECTOR", "knowledge_name": "角平分线性质"},
        {"knowledge_id": "K-PROOF", "knowledge_name": "几何证明"},
    ]
    assert question == once
    assert changed_again is False
    answer = payload["answer_key"]["questions"][0]
    assert answer["canonical_answer"] == original_answer["canonical_answer"]
    assert answer["accepted_forms"] == original_answer["accepted_forms"]
    assert question["max_score"] == 12
    warnings = session_manager.collect_generated_config_quality_warnings(payload)
    assert not any("列表字符串" in warning for warning in warnings)


def test_generated_config_normalization_recovers_paired_knowledge_tuple_strings() -> None:
    payload = {
        "rubric": {
            "total_score": 2,
            "questions": [
                {
                    "question_id": "Q2",
                    "question_type": "proof",
                    "max_score": 2,
                    "knowledge_id": "('K1', 'K2')",
                    "knowledge_name": "('知识点一', '知识点二')",
                    "parts": [],
                }
            ],
        },
        "answer_key": {
            "questions": [
                {
                    "question_id": "Q2",
                    "canonical_answer": "结论成立",
                    "accepted_forms": ["结论成立"],
                }
            ]
        },
    }

    session_manager.normalize_generated_config_schema(payload)

    question = payload["rubric"]["questions"][0]
    assert question["knowledge_id"] == "K1"
    assert question["knowledge_name"] == "知识点一"
    assert question["knowledge_ids"] == ["K1", "K2"]
    assert question["knowledge_points"] == [
        {"knowledge_id": "K1", "knowledge_name": "知识点一"},
        {"knowledge_id": "K2", "knowledge_name": "知识点二"},
    ]
    warnings = session_manager.collect_generated_config_quality_warnings(payload)
    assert not any("列表字符串" in warning for warning in warnings)


def test_generated_config_normalization_blocks_conflicting_redundant_knowledge_fields() -> None:
    payload = {
        "rubric": {
            "total_score": 2,
            "questions": [
                {
                    "question_id": "Q2",
                    "question_type": "proof",
                    "max_score": 2,
                    "knowledge_id": "['K1', 'K2']",
                    "knowledge_name": "['知识点一', '知识点二']",
                    "knowledge_ids": ["K3"],
                    "knowledge_points": [
                        {"knowledge_id": "K3", "knowledge_name": "已有合法知识点"},
                    ],
                    "parts": [],
                }
            ],
        },
        "answer_key": {
            "questions": [
                {
                    "question_id": "Q2",
                    "canonical_answer": "结论成立",
                    "accepted_forms": ["结论成立"],
                }
            ]
        },
    }

    session_manager.normalize_new_generated_config_payload(payload)

    question = payload["rubric"]["questions"][0]
    assert not any(key.startswith("knowledge") for key in question)
    assert not any(
        key.startswith("knowledge")
        for part in question.get("parts", [])
        for key in part
    )
    warnings = session_manager.collect_generated_config_quality_warnings(payload)
    assert not any("knowledge" in warning.lower() for warning in warnings)


@pytest.mark.parametrize(
    ("knowledge_id", "knowledge_name"),
    [
        ("['K1', 'K2']", "单个知识点名称"),
        ("['K1', 'K2']", "['名称一']"),
        ("['K1', invalid]", "['名称一', '名称二']"),
        ("['K1', 'K1']", "['名称一', '名称二']"),
    ],
)
def test_generated_config_normalization_keeps_unsafe_knowledge_lists_blocked(
    knowledge_id: str,
    knowledge_name: str,
) -> None:
    payload = {
        "rubric": {
            "total_score": 1,
            "questions": [
                {
                    "question_id": "Q1",
                    "question_type": "proof",
                    "max_score": 1,
                    "knowledge_id": knowledge_id,
                    "knowledge_name": knowledge_name,
                    "parts": [],
                }
            ],
        },
        "answer_key": {
            "questions": [
                {
                    "question_id": "Q1",
                    "canonical_answer": "结论成立",
                    "accepted_forms": ["结论成立"],
                }
            ]
        },
    }

    session_manager.normalize_new_generated_config_payload(payload)

    question = payload["rubric"]["questions"][0]
    assert not any(key.startswith("knowledge") for key in question)
    warnings = session_manager.collect_generated_config_quality_warnings(payload)
    assert not any("knowledge" in warning.lower() for warning in warnings)


def test_quality_warnings_flag_overly_broad_knowledge_and_serialized_answer_lists() -> None:
    payload = {
        "rubric": {
            "questions": [
                {
                    "question_id": "Q11",
                    "question_type": "calculation",
                    "knowledge_id": "K1",
                    "knowledge_name": "几何综合",
                    "parts": [],
                }
            ]
        },
        "answer_key": {
            "questions": [
                {
                    "question_id": "Q11",
                    "canonical_answer": "300",
                    "accepted_forms": ["['6','2']"],
                }
            ]
        },
    }

    warnings = session_manager.collect_generated_config_quality_warnings(payload)

    assert not any("knowledge" in warning.lower() for warning in warnings)
    assert not any("列表字符串" in warning for warning in warnings)


def test_quality_warnings_do_not_semantically_classify_generic_subjective_steps() -> None:
    payload = {
        "rubric": {
            "questions": [
                {
                    "question_id": "Q10",
                    "question_type": "comprehensive",
                    "knowledge_id": "轴对称",
                    "knowledge_name": "轴对称作图",
                    "parts": [
                        {
                            "part_id": "Q10",
                            "response_mode": "visual_construction",
                            "steps": [{"core_goal": "完成必要的推理或计算步骤"}],
                        }
                    ],
                },
                {
                    "question_id": "Q12",
                    "question_type": "proof",
                    "knowledge_id": "角平分线",
                    "knowledge_name": "角平分线性质",
                    "parts": [
                        {
                            "part_id": "Q12",
                            "response_mode": "process_required",
                            "steps": [
                                {"core_goal": "完成必要的推理或计算步骤"},
                                {"core_goal": "完成必要的推理或计算步骤"},
                            ],
                        }
                    ],
                },
            ]
        },
        "answer_key": {
            "questions": [
                {"question_id": "Q10", "answer_image_base64": "image", "parts": []},
                {"question_id": "Q12", "canonical_answer": "证明略", "parts": []},
            ]
        },
    }

    warnings = session_manager.collect_generated_config_quality_warnings(payload)

    assert not any("Q10" in warning for warning in warnings)
    assert warnings == [
        "[质量检查-阻断] Q12/Q12 过程题需要至少两个可区分步骤"
    ]


@pytest.mark.parametrize(
    ("rubric_value", "answer_value"),
    [
        (
            [{"question_id": "Q1", "question_type": "choice", "max_score": 1, "parts": []}],
            [{"question_id": "Q1", "canonical_answer": "C", "accepted_forms": ["C"]}],
        ),
        (
            {"questions": {"question_id": "Q1", "question_type": "choice", "max_score": 1, "parts": []}},
            {"questions": {"question_id": "Q1", "canonical_answer": "C", "accepted_forms": ["C"]}},
        ),
        (
            {"questions": {"Q1": {"question_type": "choice", "max_score": 1, "parts": []}}},
            {"questions": {"Q1": {"canonical_answer": "C", "accepted_forms": ["C"]}}},
        ),
    ],
)
def test_single_question_merge_accepts_common_one_question_wrappers(rubric_value: object, answer_value: object) -> None:
    payload = {"rubric": rubric_value, "answer_key": answer_value}

    merged = session_manager._merge_single_question_payloads(
        [payload],
        [{"question_id": "Q1", "question_type": "choice", "text": "Choose one"}],
    )
    session_manager._ensure_question_blocks_covered(
        merged,
        [{"question_id": "Q1", "question_type": "choice", "text": "Choose one"}],
    )

    assert [item["question_id"] for item in merged["rubric"]["questions"]] == ["Q1"]
    assert [item["question_id"] for item in merged["answer_key"]["questions"]] == ["Q1"]
    assert merged["rubric"]["questions"][0]["question_type"] == "choice"
    assert merged["answer_key"]["questions"][0]["canonical_answer"] == "C"
    assert not any("unmergeable single-question schema" in warning for warning in merged["meta"]["warnings"])
    assert not any("placeholder added" in warning for warning in merged["meta"]["warnings"])


def _single_question_payload(question_id: str, question_type: str = "choice") -> dict:
    return {
        "rubric": {
            "questions": [
                {
                    "question_id": question_id,
                    "question_type": question_type,
                    "knowledge_id": "K1",
                    "knowledge_name": "测试知识点",
                    "max_score": 1,
                    "parts": [
                        {
                            "part_id": question_id,
                            "part_score": 1,
                            "steps": [{"step_id": "S1", "step_score": 1, "core_goal": "核对答案"}],
                        }
                    ],
                }
            ]
        },
        "answer_key": {
            "questions": [
                {
                    "question_id": question_id,
                    "canonical_answer": "A",
                    "accepted_forms": ["A"],
                }
            ]
        },
    }


def test_split_generation_retries_transient_question_failure_before_scoring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class TransientError(RuntimeError):
        status_code = 503

    class FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        def json_from_text(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls < 3:
                raise TransientError("temporary upstream failure")
            if self.calls == 3:
                return _single_question_payload("Q1")
            return {
                "question_scores": [
                    {
                        "question_id": "Q1",
                        "max_score": 18,
                        "parts": [{"part_id": "Q1", "part_score": 18, "steps": [{"step_id": "S1", "step_score": 18}]}],
                    }
                ]
            }

    monkeypatch.setenv("AI_GRADING_CONFIG_RETRY_DELAYS", "0,0")
    monkeypatch.setattr(session_manager, "force_payload_total_score", lambda *_args, **_kwargs: None)
    client = FakeClient()

    payload = session_manager.generate_grading_config_from_confirmed_blocks(
        [{"question_id": "Q1", "question_type": "choice", "text": "Choose A"}],
        "",
        client,
    )

    assert client.calls == 4
    assert payload["meta"]["single_question_failure_count"] == 0
    assert payload["meta"]["single_question_attempts"]["Q1"] == 3
    assert payload["meta"]["failed_question_ids"] == []
    assert payload["meta"]["score_allocation_ai_success"] is True


def test_split_generation_pauses_scoring_when_question_still_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class TransientError(RuntimeError):
        status_code = 503

    class FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        def json_from_text(self, *_args, **_kwargs):
            self.calls += 1
            raise TransientError("temporary upstream failure")

    monkeypatch.setenv("AI_GRADING_CONFIG_RETRY_DELAYS", "0,0")
    monkeypatch.setattr(session_manager, "force_payload_total_score", lambda *_args, **_kwargs: None)
    client = FakeClient()

    payload = session_manager.generate_grading_config_from_confirmed_blocks(
        [{"question_id": "Q1", "question_type": "choice", "text": "Choose A"}],
        "",
        client,
    )

    assert client.calls == 3
    assert payload["meta"]["single_question_failure_count"] == 1
    assert payload["meta"]["failed_question_ids"] == ["Q1"]
    assert payload["meta"]["failed_questions"][0]["attempts"] == 3
    assert payload["meta"]["score_allocation_mode"] == "pending_failed_questions"
    assert payload["meta"]["score_allocation_ai_success"] is False


def test_retry_failed_questions_preserves_successes_and_then_scores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = _single_question_payload("Q1", "proof")
    existing["rubric"]["questions"][0]["parts"][0]["steps"][0]["core_goal"] = "人工修改后保留"
    existing["rubric"]["questions"].append(
        session_manager._placeholder_question_from_block(
            {"question_id": "Q2", "question_type": "choice", "text": "Choose B"}
        )
    )
    existing["answer_key"]["questions"].append(
        {"question_id": "Q2", "canonical_answer": "", "accepted_forms": [], "parts": []}
    )
    existing["meta"] = {
        "warnings": ["parallel generation failed for Q2: temporary upstream failure"],
        "failed_question_ids": ["Q2"],
        "failed_questions": [
            {
                "question_id": "Q2",
                "attempts": 3,
                "category": "transient_network",
                "error": "temporary upstream failure",
            }
        ],
    }

    class FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        def json_from_text(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                return _single_question_payload("Q2")
            return {
                "question_scores": [
                    {
                        "question_id": qid,
                        "max_score": 10,
                        "parts": [{"part_id": qid, "part_score": 10, "steps": [{"step_id": "S1", "step_score": 10}]}],
                    }
                    for qid in ["Q1", "Q2"]
                ]
            }

    monkeypatch.setenv("AI_GRADING_CONFIG_RETRY_DELAYS", "0,0")
    monkeypatch.setattr(session_manager, "force_payload_total_score", lambda *_args, **_kwargs: None)
    client = FakeClient()
    blocks = [
        {"question_id": "Q1", "question_type": "proof", "text": "Prove A"},
        {"question_id": "Q2", "question_type": "choice", "text": "Choose B"},
    ]

    payload = session_manager.retry_failed_grading_config_questions(existing, blocks, "", client)

    questions = {question["question_id"]: question for question in payload["rubric"]["questions"]}
    assert client.calls == 2
    assert not any(key.startswith("knowledge") for key in questions["Q2"])
    assert payload["meta"]["failed_question_ids"] == []
    assert payload["meta"]["score_allocation_ai_success"] is True
    assert not any(
        "Q2 placeholder added" in warning
        for warning in payload["meta"]["warnings"]
    )
    assert questions["Q1"]["parts"][0]["steps"][0]["core_goal"] == "人工修改后保留"


def test_retry_selected_failed_question_preserves_unselected_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = _single_question_payload("Q0", "proof")
    blocks = [{"question_id": "Q0", "question_type": "proof", "text": "Prove A"}]
    for qid in ("Q1", "Q2"):
        block = {"question_id": qid, "question_type": "choice", "text": f"Choose {qid}"}
        blocks.append(block)
        existing["rubric"]["questions"].append(
            session_manager._placeholder_question_from_block(block)
        )
        existing["answer_key"]["questions"].append(
            {"question_id": qid, "canonical_answer": "", "accepted_forms": [], "parts": []}
        )
    existing["meta"] = {
        "warnings": [
            "parallel generation failed for Q1: upstream failure",
            "parallel generation failed for Q2: upstream failure",
        ],
        "failed_question_ids": ["Q1", "Q2"],
        "failed_questions": [
            {
                "question_id": qid,
                "attempts": 3,
                "category": "transient_network",
                "error": "upstream failure",
            }
            for qid in ("Q1", "Q2")
        ],
    }

    class FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        def json_from_text(self, *_args, **_kwargs):
            self.calls += 1
            return _single_question_payload("Q1")

    monkeypatch.setenv("AI_GRADING_CONFIG_RETRY_DELAYS", "0,0")
    monkeypatch.setattr(session_manager, "force_payload_total_score", lambda *_args, **_kwargs: None)
    client = FakeClient()

    payload = session_manager.retry_failed_grading_config_questions(
        existing,
        blocks,
        "",
        client,
        retry_question_ids=["Q1"],
    )

    assert client.calls == 1
    assert payload["meta"]["failed_question_ids"] == ["Q2"]
    assert [item["question_id"] for item in payload["meta"]["failed_questions"]] == ["Q2"]
    assert payload["meta"]["score_allocation_pending"] is True
    questions = {item["question_id"]: item for item in payload["rubric"]["questions"]}
    assert not any(key.startswith("knowledge") for key in questions["Q1"])
    assert not any(key.startswith("knowledge") for key in questions["Q2"])


def test_word_whole_generation_is_one_request_and_uses_shared_postprocessing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scores = [17, 17, 17, 17, 17, 15]
    payload = {
        "rubric": {
            "questions": [
                _scored_question(
                    f"Q{i}",
                    "comprehensive",
                    score,
                    response_mode="short_answer_points",
                )
                for i, score in enumerate(scores, start=1)
            ]
        },
        "answer_key": {
            "questions": [
                {"question_id": f"Q{i}", "canonical_answer": "answer", "accepted_forms": []}
                for i in range(1, 7)
            ]
        },
    }

    class FakeClient:
        def __init__(self) -> None:
            self.once_calls = 0

        def json_from_text_once(self, prompt, **_kwargs):
            self.once_calls += 1
            assert "paper text" in prompt
            assert "knowledge_name" not in prompt
            return payload

        def json_from_text(self, *_args, **_kwargs):
            raise AssertionError("whole Word mode must use the strict single-request method")

    monkeypatch.setattr(session_manager, "_needs_objective_repair", lambda *_args: True)
    client = FakeClient()

    result = session_manager.generate_grading_config_from_docx_text("paper text", client)

    assert client.once_calls == 1
    assert result["rubric"]["total_score"] == 100
    assert result["meta"]["generation_mode"] == "whole_word_text_single_request"
    assert result["meta"]["score_allocation_mode"] == "single_request_local_normalization"


def test_pdf_whole_generation_is_one_visual_request_without_extracted_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scores = [17, 17, 17, 17, 17, 15]
    payload = {
        "rubric": {
            "questions": [
                _scored_question(
                    f"Q{i}",
                    "comprehensive",
                    score,
                    response_mode="short_answer_points",
                )
                for i, score in enumerate(scores, start=1)
            ]
        },
        "answer_key": {
            "questions": [
                {"question_id": f"Q{i}", "canonical_answer": "answer", "accepted_forms": []}
                for i in range(1, 7)
            ]
        },
    }

    class FakeClient:
        def __init__(self) -> None:
            self.once_calls = 0

        def json_from_images_once(self, prompt, images, **_kwargs):
            self.once_calls += 1
            assert images == [b"page-1", b"page-2"]
            assert "CORRUPTED_PDF_TEXT" not in prompt
            assert "图片是唯一权威内容来源" in prompt
            return payload

        def json_from_images_with_options(self, *_args, **_kwargs):
            raise AssertionError("whole PDF mode must use the strict single-request method")

        def json_from_text(self, *_args, **_kwargs):
            raise AssertionError("whole PDF mode must never send extracted PDF text")

    monkeypatch.setattr(session_manager, "_needs_objective_repair", lambda *_args: True)
    client = FakeClient()

    result = session_manager.generate_grading_config_from_images(
        [b"page-1", b"page-2"],
        "CORRUPTED_PDF_TEXT",
        client,
    )

    assert client.once_calls == 1
    assert result["rubric"]["total_score"] == 100
    assert result["meta"]["generation_mode"] == "whole_pdf_visual_single_request"
    assert result["meta"]["score_allocation_mode"] == "single_request_local_normalization"


@pytest.mark.parametrize("kind", ["text", "images"])
def test_whole_generation_failure_is_never_automatically_retried(kind: str) -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        def json_from_text_once(self, *_args, **_kwargs):
            self.calls += 1
            raise RuntimeError("single text failure")

        def json_from_images_once(self, *_args, **_kwargs):
            self.calls += 1
            raise RuntimeError("single visual failure")

        def json_from_text(self, *_args, **_kwargs):
            raise AssertionError("whole mode must not fall back to retrying text")

    client = FakeClient()

    with pytest.raises(RuntimeError, match="single"):
        if kind == "text":
            session_manager.generate_grading_config_from_text("paper", client)
        else:
            session_manager.generate_grading_config_from_images(
                [b"page"],
                "ignored",
                client,
            )

    assert client.calls == 1


def test_word_split_and_pdf_split_share_retry_and_normalization_pipeline() -> None:
    source = Path(session_manager.__file__).read_text(encoding="utf-8")

    assert "return _generate_grading_config_by_question_blocks(" in source
    assert "_generate_question_block_results(" in source
    assert "_call_question_generation_with_retry" in source
    assert "normalize_new_generated_config_payload(merged)" in source
    assert "retry_grading_config_score_allocation(" in source


def test_word_split_sends_embedded_images_with_rich_text_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_path = tmp_path / "diagram.png"
    image_path.write_bytes(b"word-image")

    class FakeClient:
        def __init__(self) -> None:
            self.image_calls = 0
            self.text_calls = 0

        def json_from_images(self, prompt, images, **_kwargs):
            self.image_calls += 1
            assert "题目文本" in prompt
            assert images == [b"word-image"]
            return _single_question_payload("Q1", "proof")

        def json_from_text(self, *_args, **_kwargs):
            self.text_calls += 1
            return {"question_scores": []}

    monkeypatch.setattr(session_manager, "force_payload_total_score", lambda *_args, **_kwargs: None)
    client = FakeClient()
    payload = session_manager.generate_grading_config_from_confirmed_blocks(
        [
            {
                "question_id": "Q1",
                "question_type": "proof",
                "text": "证明图中结论",
                "image_paths": [str(image_path)],
            }
        ],
        "Word paper text",
        client,
    )

    assert client.image_calls == 1
    assert client.text_calls == 1
    assert payload["meta"]["failed_question_ids"] == []
    assert payload["meta"]["generation_mode"] == "word_rich_split_parallel"


def test_pdf_split_keeps_image_semantics_and_shared_split_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.image_calls = 0
            self.text_calls = 0

        def json_from_images(self, prompt, images, **_kwargs):
            self.image_calls += 1
            assert "CORRUPTED_PDF_TEXT" not in prompt
            assert images == [b"question-image", b"answer-image"]
            return _single_question_payload("Q1", "choice")

        def json_from_text(self, *_args, **_kwargs):
            self.text_calls += 1
            return {"question_scores": []}

    monkeypatch.setattr(session_manager, "force_payload_total_score", lambda *_args, **_kwargs: None)
    client = FakeClient()
    payload = session_manager.generate_grading_config_from_confirmed_blocks(
        [
            {
                "question_id": "Q1",
                "question_type": "choice",
                "semantic_source": "images",
                "text": "CORRUPTED_PDF_TEXT",
            }
        ],
        "CORRUPTED_PDF_TEXT",
        client,
        q_images={
            "Q1": {
                "question": base64.b64encode(b"question-image").decode(),
                "answer": base64.b64encode(b"answer-image").decode(),
            }
        },
    )

    assert client.image_calls == 1
    assert client.text_calls == 1
    assert payload["meta"]["generation_mode"] == "pdf_image_split_parallel"


def test_llm_single_request_json_methods_do_not_run_ai_repair(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeCompletions:
        def __init__(self) -> None:
            self.calls = 0

        def create(self, **_kwargs):
            self.calls += 1
            return type(
                "Completion",
                (),
                {
                    "choices": [
                        type(
                            "Choice",
                            (),
                            {"message": type("Message", (), {"content": '{"ok": true}'})()},
                        )()
                    ]
                },
            )()

    completions = FakeCompletions()
    fake_openai = type("FakeOpenAI", (), {"chat": type("Chat", (), {"completions": completions})()})()
    settings = llm_client.LLMSettings(
        api_key="x",
        base_url="https://example.invalid/v1",
        ocr_model="model",
        grading_model="model",
        config_model="model",
    )
    monkeypatch.setattr(llm_client, "_create_openai_client", lambda *_args, **_kwargs: fake_openai)
    client = llm_client.LLMClient(
        settings,
        usage_sink_factory=NullUsageSink,
        trace_sink_factory=NullCallTraceSink,
    )
    monkeypatch.setattr(
        client,
        "_parse_or_repair_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("AI repair must not run")),
    )

    assert client.json_from_text_once("prompt") == {"ok": True}
    assert client.json_from_images_once("prompt", [b"not-an-image"]) == {"ok": True}
    assert completions.calls == 2


def test_llm_single_request_method_sends_explicit_output_limit_without_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeCompletions:
        def __init__(self) -> None:
            self.calls = 0
            self.last_kwargs = {}

        def create(self, **kwargs):
            self.calls += 1
            self.last_kwargs = kwargs
            return type(
                "Completion",
                (),
                {
                    "choices": [
                        type(
                            "Choice",
                            (),
                            {"message": type("Message", (), {"content": '{"ok": true}'})()},
                        )()
                    ]
                },
            )()

    completions = FakeCompletions()
    fake_openai = type("FakeOpenAI", (), {"chat": type("Chat", (), {"completions": completions})()})()
    settings = llm_client.LLMSettings(
        api_key="x",
        base_url="https://example.invalid/v1",
        ocr_model="model",
        grading_model="model",
        config_model="model",
    )
    monkeypatch.setattr(llm_client, "_create_openai_client", lambda *_args, **_kwargs: fake_openai)
    client = llm_client.LLMClient(
        settings,
        usage_sink_factory=NullUsageSink,
        trace_sink_factory=NullCallTraceSink,
    )

    assert client.json_from_text_once("prompt") == {"ok": True}
    assert completions.calls == 1
    assert completions.last_kwargs["max_tokens"] == 32000
    assert "response_format" not in completions.last_kwargs


def test_llm_single_request_forwards_strict_response_format_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeCompletions:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            return type(
                "Completion",
                (),
                {
                    "choices": [
                        type(
                            "Choice",
                            (),
                            {
                                "message": type(
                                    "Message", (), {"content": '{"results": []}'}
                                )()
                            },
                        )()
                    ]
                },
            )()

    completions = FakeCompletions()
    fake_openai = type(
        "FakeOpenAI",
        (),
        {"chat": type("Chat", (), {"completions": completions})()},
    )()
    settings = llm_client.LLMSettings(
        api_key="x",
        base_url="https://example.invalid/v1",
        ocr_model="model",
        grading_model="model",
        config_model="model",
    )
    monkeypatch.setattr(
        llm_client,
        "_create_openai_client",
        lambda *_args, **_kwargs: fake_openai,
    )
    client = llm_client.LLMClient(
        settings,
        usage_sink_factory=NullUsageSink,
        trace_sink_factory=NullCallTraceSink,
    )
    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "taxonomy_suggestions",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {"results": {"type": "array"}},
                "required": ["results"],
                "additionalProperties": False,
            },
        },
    }

    assert client.json_from_text_once(
        "prompt",
        response_format=response_format,
    ) == {"results": []}
    assert len(completions.calls) == 1
    assert completions.calls[0]["response_format"] == response_format


def test_llm_single_request_does_not_retry_unsupported_output_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeCompletions:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            raise RuntimeError("unsupported compatibility parameter")

    completions = FakeCompletions()
    fake_openai = type("FakeOpenAI", (), {"chat": type("Chat", (), {"completions": completions})()})()
    settings = llm_client.LLMSettings(
        api_key="x",
        base_url="https://example.invalid/v1",
        ocr_model="model",
        grading_model="model",
        config_model="model",
    )
    monkeypatch.setattr(llm_client, "_create_openai_client", lambda *_args, **_kwargs: fake_openai)
    client = llm_client.LLMClient(
        settings,
        usage_sink_factory=NullUsageSink,
        trace_sink_factory=NullCallTraceSink,
    )

    with pytest.raises(RuntimeError, match="unsupported compatibility parameter"):
        client.json_from_text_once("prompt")

    assert len(completions.calls) == 1
    assert completions.calls[0]["max_tokens"] == 32000


class _GatewayTestCompletions:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class _GatewayTestSink:
    def __init__(self) -> None:
        self.events = []

    def write(self, event: object) -> None:
        self.events.append(event)


class _GatewayTestPacer:
    def acquire(self, *_args, **_kwargs) -> None:
        return None


def _gateway_json_completion(
    text: str,
    *,
    finish_reason: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason=finish_reason,
                message=SimpleNamespace(content=text),
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=1,
            completion_tokens=1,
            total_tokens=2,
        ),
    )


def test_llm_single_request_reports_provider_length_stop_without_raw_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret_fragment = '{"rubric":{"student_answer":"private answer"'
    client, completions, sink, _gateway_configs = _gateway_client_factory(
        monkeypatch,
        [_gateway_json_completion(secret_fragment, finish_reason="length")],
    )

    with pytest.raises(llm_client.LLMOutputTruncatedError) as raised:
        client.json_from_text_once("prompt")

    assert len(completions.calls) == 1
    assert raised.value.finish_reason == "length"
    assert raised.value.response_chars == len(secret_fragment)
    assert len(raised.value.response_sha256) == 64
    assert "输出长度上限" in str(raised.value)
    assert "未自动重试" in str(raised.value)
    assert "重试失败批次" in str(raised.value)
    assert "private answer" not in str(raised.value)
    assert sink.events[0].finish_reason == "length"
    assert sink.events[0].output_truncated is True


def test_llm_single_request_reports_length_stop_even_when_content_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, completions, _sink, _gateway_configs = _gateway_client_factory(
        monkeypatch,
        [_gateway_json_completion("", finish_reason="length")],
    )

    with pytest.raises(llm_client.LLMOutputTruncatedError) as raised:
        client.json_from_text_once("prompt")

    assert len(completions.calls) == 1
    assert raised.value.finish_reason == "length"
    assert raised.value.response_chars == 0


def test_llm_single_request_reports_structural_truncation_without_finish_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, completions, sink, _gateway_configs = _gateway_client_factory(
        monkeypatch,
        [_gateway_json_completion('{"rubric":{"questions":[')],
    )

    with pytest.raises(llm_client.LLMOutputTruncatedError) as raised:
        client.json_from_text_once("prompt")

    assert len(completions.calls) == 1
    assert raised.value.finish_reason == ""
    assert "JSON 结构未闭合" in str(raised.value)
    assert "未自动重试" in str(raised.value)
    assert sink.events[0].output_truncated is True


def test_llm_single_request_keeps_mismatched_json_distinct_from_truncation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, completions, sink, _gateway_configs = _gateway_client_factory(
        monkeypatch,
        [_gateway_json_completion('{"a":1], "b":2}', finish_reason="stop")],
    )

    with pytest.raises(ValueError) as raised:
        client.json_from_text_once("prompt")

    assert not isinstance(raised.value, llm_client.LLMOutputTruncatedError)
    assert len(completions.calls) == 1
    assert "模型返回非 JSON" in str(raised.value)
    assert sink.events[0].output_truncated is False


def test_llm_single_request_keeps_non_truncated_invalid_json_distinct_and_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid = '{"student_answer": private_answer}'
    client, completions, sink, _gateway_configs = _gateway_client_factory(
        monkeypatch,
        [_gateway_json_completion(invalid, finish_reason="stop")],
    )

    with pytest.raises(ValueError) as raised:
        client.json_from_text_once("prompt")

    assert not isinstance(raised.value, llm_client.LLMOutputTruncatedError)
    assert len(completions.calls) == 1
    assert "模型返回非 JSON" in str(raised.value)
    assert "private_answer" not in str(raised.value)
    assert "响应字符数" in str(raised.value)
    assert sink.events[0].finish_reason == "stop"
    assert sink.events[0].output_truncated is False


def test_llm_single_request_repairs_json_locally_without_second_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, completions, sink, _gateway_configs = _gateway_client_factory(
        monkeypatch,
        [
            _gateway_json_completion(
                '{"rubric":{},"answer_key" {"questions":[]},"meta":{}}',
                finish_reason="stop",
            )
        ],
    )

    result = client.json_from_text_once("prompt")

    assert len(completions.calls) == 1
    assert result["answer_key"] == {"questions": []}
    repair = result["meta"]["local_json_repair"]
    assert repair["operations"] == ["insert_missing_colon"]
    assert len(repair["response_sha256"]) == 64
    assert sink.events[0].finish_reason == "stop"


def _gateway_client_factory(
    monkeypatch: pytest.MonkeyPatch,
    outcomes: list[object],
    *,
    policy_profile: dict[str, object] | None = None,
    trace_sink: object | None = None,
):
    completions = _GatewayTestCompletions(outcomes)
    fake_openai = SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
    )
    monkeypatch.setattr(
        llm_client,
        "_create_openai_client",
        lambda *_args, **_kwargs: fake_openai,
    )
    sink = _GatewayTestSink()
    gateway_configs: list[dict[str, object]] = []

    def gateway_factory(**kwargs):
        gateway_configs.append(kwargs)
        return LLMGateway(
            **kwargs,
            pacers=_GatewayTestPacer(),
            sleeper=lambda _seconds: None,
        )

    settings = llm_client.LLMSettings(
        api_key="secret-main-key",
        base_url="https://example.invalid",
        ocr_model="ocr-model",
        grading_model="grading-model",
        config_model="config-model",
        policy_profile=policy_profile
        or {"llm_config_generation_max_retries": 5},
    )
    return (
        llm_client.LLMClient(
            settings,
            gateway_factory=gateway_factory,
            usage_sink_factory=lambda: sink,
            trace_sink_factory=(
                (lambda: trace_sink)
                if trace_sink is not None
                else NullCallTraceSink
            ),
        ),
        completions,
        sink,
        gateway_configs,
    )


def test_llm_client_defaults_both_distinct_gateways_to_safe_jsonl_sink_without_writing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        llm_client,
        "_create_openai_client",
        lambda *_args, **_kwargs: object(),
    )
    isolated_log = tmp_path / "logs" / "llm_usage.jsonl"
    isolated_trace_log = tmp_path / "logs" / "llm_api_calls.jsonl"
    monkeypatch.setattr(llm_client, "LLM_USAGE_LOG_FILE", isolated_log)
    monkeypatch.setattr(llm_client, "LLM_TRACE_LOG_FILE", isolated_trace_log)
    gateway_calls: list[dict[str, object]] = []

    def gateway_factory(**kwargs):
        gateway_calls.append(kwargs)
        return object()

    settings = llm_client.LLMSettings(
        api_key="main-key",
        base_url="https://main.invalid",
        ocr_model="ocr",
        grading_model="grading",
        config_model="config",
        config_api_key="config-key",
        config_base_url="https://config.invalid",
    )

    client = llm_client.LLMClient(settings, gateway_factory=gateway_factory)

    assert client.config_gateway is not client.gateway
    assert len(gateway_calls) == 2
    assert all(
        isinstance(call["usage_sink"], JsonlUsageSink)
        and call["usage_sink"].path == isolated_log
        for call in gateway_calls
    )
    assert all(
        isinstance(call["trace_sink"], JsonlCallTraceSink)
        and call["trace_sink"].path == isolated_trace_log
        for call in gateway_calls
    )
    assert [call["endpoint_host"] for call in gateway_calls] == [
        "main.invalid",
        "config.invalid",
    ]
    assert usage_logger.LOG_FILE == Path("logs/llm_usage.jsonl")
    assert not isolated_log.exists()
    assert not isolated_trace_log.exists()


def test_llm_client_gateway_chat_uses_timeout_and_request_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, completions, sink, gateway_configs = _gateway_client_factory(
        monkeypatch,
        [_gateway_json_completion('{"ok": true}')],
    )

    assert client.json_from_text("prompt") == {"ok": True}
    assert completions.calls[0]["timeout"] == 600.0
    assert sink.events[0].request_kind == "config_generation"
    assert sink.events[0].request_id
    assert len(gateway_configs) == 1
    config_key = str(gateway_configs[0]["config_key"])
    assert len(config_key) == 64
    assert set(config_key) <= set("0123456789abcdef")
    assert "secret-main-key" not in config_key
    assert "example.invalid" not in config_key


def test_llm_client_gateway_maps_legacy_request_kinds_exactly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, completions, sink, _gateway_configs = _gateway_client_factory(
        monkeypatch,
        [
            _gateway_json_completion("recognized text"),
            _gateway_json_completion('{"ok": true}'),
            _gateway_json_completion('{"ok": true}'),
            _gateway_json_completion('{"ok": true}'),
        ],
    )

    assert client.text_from_images("prompt", [b"image"]) == "recognized text"
    assert client.json_from_images("prompt", [b"image"]) == {"ok": True}
    assert client.json_from_images_with_options(
        "prompt",
        [b"image"],
        use_config_client=True,
    ) == {"ok": True}
    assert client.json_from_text("prompt") == {"ok": True}

    assert [event.request_kind for event in sink.events] == [
        "recognition",
        "grading",
        "config_generation",
        "config_generation",
    ]
    assert [call["timeout"] for call in completions.calls] == [
        60.0,
        300.0,
        600.0,
        600.0,
    ]


def test_three_legacy_outer_rounds_make_three_physical_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    errors = [TimeoutError("timed out") for _ in range(9)]
    client, completions, _sink, _gateway_configs = _gateway_client_factory(
        monkeypatch,
        errors,
        policy_profile={"llm_config_generation_max_retries": 2},
    )

    for _round in range(3):
        with pytest.raises(TimeoutError):
            client.json_from_text("prompt")

    assert len(completions.calls) == 3


class _CompatibilityStatusError(RuntimeError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError("operation not supported before timeout"),
        ConnectionError("connection path not supported"),
        _CompatibilityStatusError(429, "request not supported while limited"),
        _CompatibilityStatusError(500, "operation not supported by server"),
        RuntimeError("operation not supported"),
    ],
)
def test_retryable_or_unknown_not_supported_errors_never_enter_parameter_fallback(
    monkeypatch: pytest.MonkeyPatch,
    error: BaseException,
) -> None:
    client, completions, _sink, _gateway_configs = _gateway_client_factory(
        monkeypatch,
        [error] * 8,
    )

    with pytest.raises(type(error)) as raised:
        client.json_from_text("prompt")

    assert raised.value is error
    assert len(completions.calls) == 1


def test_public_json_call_does_not_send_a_hidden_model_repair_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, completions, _sink, _gateway_configs = _gateway_client_factory(
        monkeypatch,
        [
            _gateway_json_completion("not json"),
            _gateway_json_completion('{"ok": true}'),
        ],
    )

    with pytest.raises(llm_client.LLMResponseFormatError):
        client.json_from_text("prompt")

    assert len(completions.calls) == 1


def test_public_json_call_does_not_send_a_hidden_parameter_fallback_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = RuntimeError("unsupported parameter max_tokens")
    client, completions, _sink, _gateway_configs = _gateway_client_factory(
        monkeypatch,
        [error, _gateway_json_completion('{"ok": true}')],
    )

    with pytest.raises(RuntimeError) as raised:
        client.json_from_text("prompt")

    assert raised.value is error
    assert len(completions.calls) == 1


def test_text_from_images_does_not_send_a_hidden_parameter_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = RuntimeError("unsupported parameter max_tokens")
    client, completions, sink, _gateway_configs = _gateway_client_factory(
        monkeypatch,
        [
            error,
            _gateway_json_completion("recognized text"),
        ],
    )

    with pytest.raises(RuntimeError) as raised:
        client.text_from_images("prompt", [b"image"])

    assert raised.value is error
    assert len(completions.calls) == 1
    assert [event.compatibility_fallback for event in sink.events] == [""]


def test_public_call_does_not_cycle_through_parameter_variants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = RuntimeError("unsupported parameter max_tokens")
    client, completions, sink, _gateway_configs = _gateway_client_factory(
        monkeypatch,
        [
            error,
            RuntimeError("unsupported parameter max_completion_tokens"),
            _gateway_json_completion('{"ok": true}'),
        ],
    )

    with pytest.raises(RuntimeError) as raised:
        client.json_from_text("prompt")

    assert raised.value is error
    assert len(completions.calls) == 1
    assert [event.compatibility_fallback for event in sink.events] == [""]
    assert [event.attempt for event in sink.events] == [1]


def test_parameter_error_trace_marks_that_no_followup_is_planned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace_sink = _GatewayTestSink()
    client, completions, _sink, _gateway_configs = _gateway_client_factory(
        monkeypatch,
        [
            RuntimeError("unsupported parameter max_tokens"),
            RuntimeError("unsupported parameter max_completion_tokens"),
            _gateway_json_completion('{"ok": true}'),
        ],
        trace_sink=trace_sink,
    )

    with pytest.raises(RuntimeError, match="unsupported parameter max_tokens"):
        client.json_from_text("prompt")

    assert len(completions.calls) == 1
    failed = [
        event
        for event in trace_sink.events
        if event.event_type == "request_failed"
    ]
    assert [event.attempt for event in failed] == [1]
    assert [event.will_retry for event in failed] == [False]
    assert [event.retry_delay_ms for event in failed] == [0]


def test_parameter_error_does_not_consume_a_later_network_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parameter_error = RuntimeError("unsupported parameter max_tokens")
    timeout = TimeoutError("fallback timed out")
    client, completions, sink, _gateway_configs = _gateway_client_factory(
        monkeypatch,
        [
            parameter_error,
            timeout,
        ],
    )

    with pytest.raises(RuntimeError) as raised:
        client.json_from_text("prompt")

    assert raised.value is parameter_error
    assert len(completions.calls) == 1
    assert [event.compatibility_fallback for event in sink.events] == [""]


def test_truncated_json_does_not_trigger_a_second_model_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, completions, sink, _gateway_configs = _gateway_client_factory(
        monkeypatch,
        [
            _gateway_json_completion('{"ok":'),
            _gateway_json_completion('{"ok": true}'),
        ],
    )

    with pytest.raises(llm_client.LLMOutputTruncatedError):
        client.json_from_text("prompt")

    assert len(completions.calls) == 1
    assert [event.attempt for event in sink.events] == [1]


def test_invalid_json_does_not_trigger_a_second_model_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, completions, sink, _gateway_configs = _gateway_client_factory(
        monkeypatch,
        [
            _gateway_json_completion("not json"),
            _gateway_json_completion('{"ok": true}'),
        ],
    )

    with pytest.raises(llm_client.LLMResponseFormatError):
        client.json_from_text("prompt")

    assert len(completions.calls) == 1
    assert [event.attempt for event in sink.events] == [1]


def test_separate_top_level_compatibility_calls_restart_attempt_at_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _completions, sink, _gateway_configs = _gateway_client_factory(
        monkeypatch,
        [
            _gateway_json_completion('{"call": 1}'),
            _gateway_json_completion('{"call": 2}'),
        ],
    )

    assert client.json_from_text("first") == {"call": 1}
    assert client.json_from_text("second") == {"call": 2}
    assert [event.attempt for event in sink.events] == [1, 1]


def test_llm_client_gateway_reuses_only_identical_client_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_clients = [object(), object(), object()]
    monkeypatch.setattr(
        llm_client,
        "_create_openai_client",
        lambda *_args, **_kwargs: fake_clients.pop(0),
    )
    gateway_calls: list[dict[str, object]] = []

    def gateway_factory(**kwargs):
        gateway_calls.append(kwargs)
        return object()

    shared_settings = llm_client.LLMSettings(
        api_key="shared-key",
        base_url="https://shared.invalid",
        ocr_model="ocr",
        grading_model="grading",
        config_model="config",
    )
    shared = llm_client.LLMClient(
        shared_settings,
        gateway_factory=gateway_factory,
        usage_sink_factory=NullUsageSink,
        trace_sink_factory=NullCallTraceSink,
    )
    assert shared.config_gateway is shared.gateway
    assert len(gateway_calls) == 1

    distinct_settings = llm_client.LLMSettings(
        api_key="main-key",
        base_url="https://main.invalid",
        ocr_model="ocr",
        grading_model="grading",
        config_model="config",
        config_api_key="config-key",
        config_base_url="https://config.invalid/v1",
    )
    distinct = llm_client.LLMClient(
        distinct_settings,
        gateway_factory=gateway_factory,
        usage_sink_factory=NullUsageSink,
        trace_sink_factory=NullCallTraceSink,
    )
    assert distinct.config_gateway is not distinct.gateway
    assert len(gateway_calls) == 3
    assert len({str(call["config_key"]) for call in gateway_calls}) == 3


def test_global_score_search_adjusts_an_infeasible_objective_budget() -> None:
    questions = [
        *[_scored_question(f"Q{i}", "choice", 5) for i in range(1, 6)],
        *[_scored_question(f"Q{i}", "fill_blank", score) for i, score in zip(range(6, 10), [7, 7, 8, 8])],
        *[_scored_question(f"Q{i}", "calculation", 15) for i in range(10, 13)],
    ]
    payload = {"rubric": {"questions": questions}}

    session_manager.force_payload_total_score(payload, target_total=100)

    choice_scores = {question["max_score"] for question in questions if question["question_type"] == "choice"}
    fill_scores = {question["max_score"] for question in questions if question["question_type"] == "fill_blank"}
    assert sum(question["max_score"] for question in questions) == 100
    assert choice_scores and len(choice_scores) == 1
    assert fill_scores and len(fill_scores) == 1
    assert all(1 <= question["max_score"] <= 18 for question in questions)


def test_objective_types_only_need_equal_scores_within_their_own_type() -> None:
    questions = [
        _scored_question("Q1", "choice", 16),
        _scored_question("Q2", "choice", 16),
        _scored_question("Q3", "fill_blank", 3),
        _scored_question("Q4", "fill_blank", 3),
        *[_scored_question(f"Q{i}", "calculation", 13) for i in range(5, 10)],
    ]

    score_policy.enforce_integer_scores_by_type(questions, target_total=100)

    choice_score = questions[0]["max_score"]
    fill_score = questions[2]["max_score"]
    assert choice_score == questions[1]["max_score"]
    assert fill_score == questions[3]["max_score"]
    assert choice_score <= fill_score
    assert choice_score * 2 >= fill_score
    assert max(question["max_score"] for question in questions) <= 18
    assert sum(question["max_score"] for question in questions) == 100


def test_all_generation_paths_use_the_shared_18_point_limit() -> None:
    root = Path(__file__).resolve().parents[1]
    session_source = (root / "session_manager.py").read_text(encoding="utf-8")
    template_source = (root / "template_analyzer.py").read_text(encoding="utf-8")

    assert score_policy.MAX_QUESTION_SCORE == 18
    assert "max_question_score=12" not in session_source
    assert "max_question_score=12" not in template_source
    assert "must not exceed 12" not in session_source
    assert "必须小于或等于 12" not in session_source
    assert "必须小于或等于 12" not in template_source
    assert "choice 的 max_score 必须小于或等于 fill_blank" not in session_source
    assert "choice 的 max_score 必须小于或等于 fill_blank" not in template_source
