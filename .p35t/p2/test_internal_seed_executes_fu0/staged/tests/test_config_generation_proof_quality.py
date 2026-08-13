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


def test_quality_blocks_nontrivial_proof_with_one_shallow_step() -> None:
    payload = _proof_payload(
        score=6,
        steps=[
            {
                "step_id": "S1",
                "step_score": 6,
                "core_goal": "由全等得到结论",
                "required_elements": ["全等"],
            }
        ],
    )

    warnings = collect_generated_config_quality_warnings(payload)

    assert any(
        warning.startswith("[质量检查-阻断] Q12")
        and "可独立评分的逻辑步骤" in warning
        for warning in warnings
    )


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
