from __future__ import annotations

import copy
from typing import Any

import pytest

from question_bank.solution_evidence import CoreResolution, QuestionSolutionEvidence
from question_bank.solution_evidence.normalization import (
    normalize_model_solution_evidence,
)


class Resolver:
    def resolve(self, fine_term_id: str) -> CoreResolution:
        return CoreResolution(status="unmapped", reason="normalization-test")


def _point(
    point_id: str,
    *,
    index: int,
    target: str,
    anchor: str,
    depends_on: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "evidence_point_id": point_id,
        "step_index": index,
        "target": target,
        "justification": "依据题目条件",
        "answer_anchor": anchor,
        "observable_evidence": target,
        "depends_on": depends_on or [],
        "fine_term_links": [],
        "equivalent_rules": [],
        "counterexamples": [],
    }


def _part(
    *,
    part_id: str = "part-1",
    mode: str = "process_required",
    canonical_answer: str = "",
    full_answer: str = "由条件完成推导。",
    points: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "part_id": part_id,
        "label": "",
        "response_mode": mode,
        "canonical_answer": canonical_answer,
        "accepted_forms": [canonical_answer] if canonical_answer else [],
        "full_answer": full_answer,
        "proof_obligations": [],
        "visual_requirements": [],
        "deduction_policy": ["按评分点判断"],
        "allow_alternative_methods": mode != "exact_objective",
        "evidence_points": points
        or [
            _point(
                "part-1-step-1",
                index=1,
                target=canonical_answer or full_answer,
                anchor=canonical_answer or full_answer,
            )
        ],
    }


def _payload(*parts: dict[str, Any], question_id: int = 1) -> dict[str, Any]:
    return {
        "schema_version": "question-solution-evidence-v2",
        "question_id": question_id,
        "parts": list(parts),
        "auxiliary_rules": [],
        "rationale": "合成回归",
        "confidence": 0.9,
    }


def _parse(
    payload: dict[str, Any], *, question_id: int = 1
) -> QuestionSolutionEvidence:
    return QuestionSolutionEvidence.from_model_dict(
        payload,
        question_id=question_id,
        source_content_hash="0" * 64,
        resolver=Resolver(),
    )


def test_normalizer_never_discards_model_supplied_score_fields() -> None:
    raw = _payload(_part())
    raw["parts"][0]["evidence_points"][0]["step_score"] = 2

    with pytest.raises(ValueError, match="score field is forbidden"):
        normalize_model_solution_evidence(
            copy.deepcopy(raw),
            question_id=1,
            question_type="calculation",
            taxonomy_contract={},
        )


def test_normalizer_preserves_numeric_zero_answers_and_evidence() -> None:
    part = _part(
        mode="short_answer_points",
        canonical_answer="",
        full_answer="",
    )
    part["canonical_answer"] = 0
    part["full_answer"] = 0
    point = part["evidence_points"][0]
    point["target"] = 0
    point["answer_anchor"] = 0
    point["observable_evidence"] = 0

    normalized = normalize_model_solution_evidence(
        _payload(part),
        question_id=1,
        question_type="fill_blank",
        taxonomy_contract={},
    )

    evidence = _parse(normalized.payload)
    assert evidence.parts[0].canonical_answer == "0"
    assert evidence.parts[0].full_answer == "0"
    assert evidence.parts[0].evidence_points[0].answer_anchor == "0"

@pytest.mark.parametrize("schema_version", ["question-solution-evidence-v1", "question-solution-evidence-v2"])
def test_fixed_answer_kind_keeps_legacy_evidence_payload_and_hash(schema_version: str) -> None:
    from question_bank.canonical_hash import canonical_hash

    raw = _payload(_part(mode="exact_objective", canonical_answer="3", full_answer=""))
    raw["schema_version"] = schema_version
    point = raw["parts"][0]["evidence_points"][0]
    if schema_version.endswith("v1"):
        for key in ("step_index", "justification", "answer_anchor", "depends_on"):
            point.pop(key)
    legacy = _parse(raw)
    submitted = copy.deepcopy(raw)
    submitted["parts"][0]["evidence_points"][0]["answer_kind"] = "fixed"
    explicit = _parse(submitted)
    assert explicit.to_dict() == legacy.to_dict()
    assert explicit.parts[0].to_dict(schema_version=schema_version) == raw["parts"][0]
    assert explicit.content_hash == canonical_hash({**raw, "source_content_hash": "0" * 64})


def test_open_result_normalization_preserves_constraints_instead_of_example() -> None:
    point = _point("open-result", index=1, target="给出一个大于 2 的整数", anchor="3")
    point.update(answer_kind="conditions", observable_evidence="答案是整数且大于 2",
                 counterexamples=["2 不大于 2", "2.5 不是整数"])
    raw = _payload(_part(mode="exact_objective", canonical_answer="3", full_answer="", points=[point]))
    normalized = normalize_model_solution_evidence(raw, question_id=1, question_type="fill_blank",
        taxonomy_contract={}, objective_response_shape="single_blank")
    actual = _parse(normalized.payload).parts[0].evidence_points[0]
    assert actual.answer_kind == "conditions"
    assert actual.target == point["target"]
    assert actual.observable_evidence == point["observable_evidence"]
    assert actual.answer_anchor == "3"
    assert actual.counterexamples == ("2 不大于 2", "2.5 不是整数")


@pytest.mark.parametrize("mode", ["process_required", "visual_construction"])
def test_conditions_marker_cannot_relax_process_or_visual_obligations(mode: str) -> None:
    point = _point("open-result", index=1, target="给出一个大于 2 的整数", anchor="3")
    point["answer_kind"] = "conditions"
    with pytest.raises(ValueError, match="result-only response mode"):
        _parse(_payload(_part(mode=mode, canonical_answer="3", full_answer="3", points=[point])))


def test_choice_or_empty_constraints_cannot_be_saved_as_open_answer() -> None:
    point = _point("open-result", index=1, target="选择 A", anchor="A")
    point["answer_kind"] = "conditions"
    raw = _payload(_part(mode="exact_objective", canonical_answer="A", full_answer="", points=[point]))
    with pytest.raises(ValueError, match="choice answers"):
        normalize_model_solution_evidence(raw, question_id=1, question_type="single_choice",
            taxonomy_contract={}, objective_response_shape="single_choice")
    raw["parts"][0]["evidence_points"][0]["observable_evidence"] = ""
    with pytest.raises(ValueError, match="explicit mathematical conditions"):
        normalize_model_solution_evidence(raw, question_id=1, question_type="fill_blank",
            taxonomy_contract={}, objective_response_shape="single_blank")
