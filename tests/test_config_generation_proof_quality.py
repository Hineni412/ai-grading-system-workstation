from __future__ import annotations

from backend.config_generation.prompts import build_batch_generation_prompt
from backend.config_generation.quality import (
    collect_generated_config_quality_warnings,
)


def _proof_payload(
    *,
    score: int,
    steps: list[dict[str, object]],
    proof_obligations: list[str] | None = None,
    deduction_policy: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    question: dict[str, object] = {
        "question_id": "Q12",
        "question_type": "proof",
        "max_score": score,
        "knowledge_id": "K-CONGRUENCE",
        "knowledge_name": "全等三角形的判定与性质",
        "parts": [
            {
                "part_id": "Q12",
                "part_score": score,
                "response_mode": "process_required",
                "steps": steps,
            }
        ],
    }
    if proof_obligations is not None:
        question["proof_obligations"] = proof_obligations
    if deduction_policy is not None:
        question["deduction_policy"] = deduction_policy
    return {
        "rubric": {"questions": [question]},
        "answer_key": {
            "questions": [
                {
                    "question_id": "Q12",
                    "canonical_answer": "证明结论成立",
                    "parts": [
                        {
                            "part_id": "Q12",
                            "answer": "由 SAS 证明两三角形全等，再得对应边相等。",
                        }
                    ],
                }
            ]
        },
    }


def test_batch_prompt_requires_independently_scorable_proof_and_calculation_steps() -> None:
    prompt = build_batch_generation_prompt(
        ["Q12"],
        [
            {
                "question_id": "Q12",
                "question_type": "proof",
                "question_text": "证明两条线段相等。",
                "answer_text": "略",
            }
        ],
        [],
        "",
    )

    for requirement in (
        "可独立评分",
        "证明义务",
        "定理或判定条件",
        "推导",
        "结论",
        "扣分",
        "替代方法",
    ):
        assert requirement in prompt


def test_batch_prompt_pins_answers_and_part_rules_to_one_schema() -> None:
    prompt = build_batch_generation_prompt(
        ["Q5", "Q12"],
        [
            {
                "question_id": "Q5",
                "question_type": "fill_blank",
                "question_text": "填写结果。",
                "answer_text": "70",
            },
            {
                "question_id": "Q12",
                "question_type": "proof",
                "question_text": "证明两条线段相等。",
                "answer_text": "略",
            },
        ],
        [],
        "",
    )

    for contract_path in (
        "answer_key.questions[].canonical_answer",
        "answer_key.questions[].parts[].answer",
        "rubric.questions[].parts[].proof_obligations",
        "rubric.questions[].parts[].deduction_policy",
        "rubric.questions[].parts[].steps[].deduction_rules",
    ):
        assert contract_path in prompt


def test_batch_prompt_lists_type_enums_and_rejects_choice_response_aliases() -> None:
    prompt = build_batch_generation_prompt(
        ["Q4"],
        [
            {
                "question_id": "Q4",
                "question_type": "choice",
                "question_text": "选择正确选项。",
                "answer_text": "C",
            }
        ],
        [],
        "",
    )

    for question_type in (
        "choice",
        "fill_blank",
        "calculation",
        "proof",
        "comprehensive",
    ):
        assert question_type in prompt
    for response_mode in (
        "exact_objective",
        "short_answer_points",
        "process_required",
        "visual_construction",
    ):
        assert response_mode in prompt
    assert "single_choice" in prompt
    assert "multiple_choice" in prompt
    assert "不得写入 response_mode" in prompt


def test_quality_does_not_semantically_reject_structurally_complete_proof() -> None:
    payload = _proof_payload(
        score=6,
        proof_obligations=["证明△ABC≌△DEF，并由对应边得到 AB=DE"],
        deduction_policy=[
            {
                "rule_id": "missing_congruence_chain",
                "description": "缺少 SAS 判定条件或 AB=DE 结论时，对应步骤未达成",
            }
        ],
        steps=[
            {
                "step_id": "S1",
                "step_score": 6,
                "core_goal": "用 SAS 证明△ABC≌△DEF 并得到 AB=DE",
                "required_elements": ["SAS", "△ABC≌△DEF", "AB=DE"],
            }
        ],
    )

    warnings = collect_generated_config_quality_warnings(payload)

    assert not any("缺少可独立评分的具体步骤" in warning for warning in warnings)
    assert not any("缺少具体证明义务" in warning for warning in warnings)
    assert not any("缺少具体扣分证据" in warning for warning in warnings)


def test_quality_does_not_block_generic_wording_after_structural_validation() -> None:
    payload = _proof_payload(
        score=6,
        steps=[
            {
                "step_id": "S1",
                "step_score": 6,
                "core_goal": "正确的结论",
                "required_elements": ["完成必要的推理或计算步骤"],
            }
        ],
    )

    warnings = collect_generated_config_quality_warnings(payload)

    assert not any("通用描述" in warning for warning in warnings)
    assert not any("可独立评分的逻辑步骤" in warning for warning in warnings)


def test_quality_allows_a_one_point_proof_to_have_one_specific_step() -> None:
    payload = _proof_payload(
        score=1,
        steps=[
            {
                "step_id": "S1",
                "step_score": 1,
                "core_goal": "利用对顶角相等得到∠1=∠2",
                "required_elements": ["指出∠1与∠2是对顶角", "写出∠1=∠2"],
            }
        ],
    )

    warnings = collect_generated_config_quality_warnings(payload)

    assert not any("可独立评分的逻辑步骤" in warning for warning in warnings)
    assert not any("证明义务或扣分证据" in warning for warning in warnings)


def test_quality_allows_one_specific_scoring_step_for_a_multi_point_subpart() -> None:
    payload = _proof_payload(
        score=6,
        proof_obligations=["证明△ABC≡△DEF 并得到 AB=DE"],
        deduction_policy=[
            {
                "rule_id": "missing_congruence_chain",
                "description": "未写出判定条件或全等结论时，该评分点不得分",
            }
        ],
        steps=[
            {
                "step_id": "S1",
                "step_score": 6,
                "core_goal": "完成 SAS 全等判定并由对应边得出 AB=DE",
                "required_elements": [
                    "写出 SAS 判定链并由对应边相等得出 AB=DE",
                ],
            }
        ],
    )

    warnings = collect_generated_config_quality_warnings(payload)

    assert not any("可独立评分的逻辑步骤" in warning for warning in warnings)
    assert not any("缺少具体证明义务" in warning for warning in warnings)
    assert not any("缺少具体扣分证据" in warning for warning in warnings)


def test_quality_accepts_a_detailed_nontrivial_proof() -> None:
    payload = _proof_payload(
        score=6,
        proof_obligations=["证明△ABC≌△DEF", "由全等推出AB=DE"],
        deduction_policy=[
            {
                "rule_id": "missing_congruence_condition",
                "description": "缺少一项 SAS 条件时扣除对应步骤分",
            }
        ],
        steps=[
            {
                "step_id": "S1",
                "step_score": 2,
                "core_goal": "列出 SAS 所需的三项对应条件",
                "required_elements": ["AB=DE", "∠A=∠D", "AC=DF"],
                "allow_alternative_methods": True,
            },
            {
                "step_id": "S2",
                "step_score": 2,
                "core_goal": "依据 SAS 判定两三角形全等",
                "required_elements": ["明确写出 SAS", "对应顶点顺序正确"],
                "allow_alternative_methods": True,
            },
            {
                "step_id": "S3",
                "step_score": 2,
                "core_goal": "由全等三角形对应边相等得到结论",
                "required_elements": ["引用全等三角形性质", "写出AB=DE"],
                "allow_alternative_methods": True,
            },
        ],
    )

    warnings = collect_generated_config_quality_warnings(payload)

    assert not any("可独立评分的逻辑步骤" in warning for warning in warnings)
    assert not any("证明义务或扣分证据" in warning for warning in warnings)


def test_quality_accepts_part_level_proof_and_deduction_evidence() -> None:
    payload = _proof_payload(
        score=6,
        steps=[
            {
                "step_id": "S1",
                "step_score": 2,
                "core_goal": "列出 SAS 所需的三项对应条件",
                "required_elements": ["AB=DE", "∠A=∠D", "AC=DF"],
            },
            {
                "step_id": "S2",
                "step_score": 2,
                "core_goal": "依据 SAS 判定两三角形全等",
                "required_elements": ["明确写出 SAS", "对应顶点顺序正确"],
            },
            {
                "step_id": "S3",
                "step_score": 2,
                "core_goal": "由全等推出对应边相等",
                "required_elements": ["引用全等三角形性质", "写出AB=DE"],
            },
        ],
    )
    part = payload["rubric"]["questions"][0]["parts"][0]
    part["proof_obligations"] = ["证明△ABC≌△DEF", "由全等推出AB=DE"]
    part["deduction_policy"] = (
        "缺少一项 SAS 条件时扣对应步骤分；未写全等结论时扣判定步骤分"
    )

    warnings = collect_generated_config_quality_warnings(payload)

    assert not any("证明义务" in warning for warning in warnings)
    assert not any("扣分证据" in warning for warning in warnings)


def test_exact_objective_requires_text_even_when_an_answer_image_exists() -> None:
    payload = {
        "rubric": {
            "questions": [
                {
                    "question_id": "Q5",
                    "question_type": "fill_blank",
                    "max_score": 6,
                    "parts": [
                        {
                            "part_id": "Q5",
                            "part_score": 6,
                            "response_mode": "exact_objective",
                            "steps": [
                                {
                                    "step_id": "S1",
                                    "step_score": 6,
                                    "core_goal": "填写正确答案",
                                    "required_elements": [],
                                }
                            ],
                        }
                    ],
                }
            ]
        },
        "answer_key": {
            "questions": [
                {
                    "question_id": "Q5",
                    "canonical_answer": "",
                    "accepted_forms": [],
                    "answer_image_base64": "image",
                    "parts": [{"part_id": "Q5", "answer": ""}],
                }
            ]
        },
    }

    warnings = collect_generated_config_quality_warnings(payload)

    assert any(
        warning.startswith("[质量检查-阻断] Q5")
        and "文本标准答案" in warning
        for warning in warnings
    )
