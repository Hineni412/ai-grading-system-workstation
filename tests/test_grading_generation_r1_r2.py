from __future__ import annotations

import json

from backend.config_generation.quality import blocking_quality_question_ids
from backend.config_generation.reference_context import build_reference_context
from backend.config_generation.status_projection import project_question_states
from question_bank.models.tag_schema import TaggingContext
from question_bank.training_criteria.adapters import _combined_prompt
from question_bank.training_criteria.analysis import PlannedAnalysisBatch, QuestionAnalysisInput


def test_source_extracted_analysis_and_rich_text_reach_model_input() -> None:
    reference = build_reference_context(
        {
            "answer_text": "答案：54°",
            "analysis_html": "<p>解析：先求角 <sup>2</sup>，再得 72°</p>",
            "local_answer_trusted": True,
        }
    )
    assert reference["trust_level"] == "source_extracted"
    assert reference["source_segments"] == [
        {"kind": "answer", "text": "答案：54°"},
        {"kind": "analysis", "text": "解析：先求角 ^(2)，再得 72°"},
    ]
    question = QuestionAnalysisInput(
        question_id=1,
        tagging_context=TaggingContext(
            question_text="求这个角。",
            answer_text="答案：54°",
            question_number="Q1",
        ),
        reference_solution=reference,
    )
    prompt = _combined_prompt(
        PlannedAnalysisBatch((question,), 10, 10),
        "both",
    )
    payload = json.loads(prompt[1]["content"][0]["text"])
    assert payload["questions"][0]["reference_solution"] == reference
    assert "never a reason to omit" in payload["rules"]


def _generated_payload(step_count: int) -> dict:
    steps = [
        {
            "step_id": f"S{index}",
            "core_goal": f"步骤{index}",
            "required_elements": [f"结果{index}"],
        }
        for index in range(1, step_count + 1)
    ]
    return {
        "rubric": {
            "questions": [{
                "question_id": "Q10",
                "question_type": "calculation",
                "parts": [{"part_id": "Q10", "response_mode": "process_required", "steps": steps}],
            }],
        },
        "answer_key": {
            "questions": [{
                "question_id": "Q10",
                "parts": [{
                    "part_id": "Q10",
                    "answer": "∠A=20°，∠B=30°，x=1；y=2，因此 z=3",
                    "analysis": "∠A=20°，∠B=30°，x=1；y=2，因此 z=3",
                }],
            }],
        },
    }


def test_structure_gate_ignores_punctuation_equations_and_angle_symbols() -> None:
    assert blocking_quality_question_ids(_generated_payload(2)) == []


def test_structure_gate_still_blocks_process_question_with_one_step() -> None:
    assert blocking_quality_question_ids(_generated_payload(1)) == ["Q10"]


def test_question_status_projection_uses_durable_checkpoint_states() -> None:
    projected = project_question_states(
        ["Q1", "Q2", "Q3"],
        {
            "meta": {
                "question_states": [
                    {"question_id": "Q1", "state": "passed", "retryable": False},
                    {"question_id": "Q2", "state": "blocked", "reason": "local_validation", "retryable": True},
                    {"question_id": "Q3", "state": "running", "retryable": False},
                ]
            }
        },
    )
    assert [item["state"] for item in projected] == ["passed", "blocked", "running"]
    assert projected[1]["retryable"] is True
