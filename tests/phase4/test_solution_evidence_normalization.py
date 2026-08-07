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


def _parse(payload: dict[str, Any], *, question_id: int = 1) -> QuestionSolutionEvidence:
    return QuestionSolutionEvidence.from_model_dict(
        payload,
        question_id=question_id,
        source_content_hash="0" * 64,
        resolver=Resolver(),
    )


def test_normalizer_accepts_mathematically_equivalent_non_verbatim_anchor() -> None:
    raw = _payload(
        _part(
            full_answer="由垂直平分线得AB=2BD=12，所以结果为12。",
            points=[
                _point(
                    "model-step",
                    index=1,
                    target="得到AB=12",
                    anchor="AB=12",
                )
            ],
        )
    )

    normalized = normalize_model_solution_evidence(
        raw,
        question_id=1,
        question_type="calculation",
        taxonomy_contract={},
    )

    evidence = _parse(normalized.payload)
    assert evidence.parts[0].evidence_points[0].answer_anchor == "AB=12"
    assert not any("锚点" in item for item in normalized.notes)


def test_normalizer_regenerates_ids_and_drops_cross_part_dependencies() -> None:
    first = _part(
        part_id="P2",
        full_answer="推出y=x/2。",
        points=[
            _point(
                "part2-step1",
                index=7,
                target="推出y=x/2",
                anchor="y=x/2",
            )
        ],
    )
    second = _part(
        part_id="P3",
        full_answer="利用第(2)问结论得到45°。",
        points=[
            _point(
                "part3-step1",
                index=4,
                target="得到45°",
                anchor="45°",
                depends_on=["part2-step1"],
            )
        ],
    )

    normalized = normalize_model_solution_evidence(
        _payload(first, second),
        question_id=1,
        question_type="calculation",
        taxonomy_contract={},
    )

    evidence = _parse(normalized.payload)
    assert [part.part_id for part in evidence.parts] == ["part-1", "part-2"]
    assert evidence.parts[0].evidence_points[0].evidence_point_id == "part-1-step-1"
    assert evidence.parts[1].evidence_points[0].depends_on == ()
    assert any("跨小问" in item for item in normalized.notes)


def test_normalizer_collapses_objective_explanation_into_canonical_evidence() -> None:
    answer = _part(
        part_id="answer",
        mode="exact_objective",
        canonical_answer="C",
        full_answer="逐项判断后，选项C正确。",
        points=[
            _point("reason-a", index=1, target="排除A", anchor="选项A错误"),
            _point("reason-c", index=2, target="选择C", anchor="C"),
        ],
    )
    non_scoring_explanation = _part(
        part_id="option-analysis",
        mode="process_required",
        canonical_answer="",
        full_answer="",
        points=[
            _point(
                "option-analysis-step",
                index=1,
                target="说明各选项",
                anchor="选项分析",
            )
        ],
    )

    normalized = normalize_model_solution_evidence(
        _payload(answer, non_scoring_explanation),
        question_id=1,
        question_type="single_choice",
        taxonomy_contract={},
    )

    evidence = _parse(normalized.payload)
    assert len(evidence.parts) == 1
    assert evidence.parts[0].response_mode == "exact_objective"
    assert evidence.parts[0].canonical_answer == "C"
    assert len(evidence.parts[0].evidence_points) == 1
    assert evidence.parts[0].evidence_points[0].answer_anchor == "C"


def test_normalizer_moves_reserved_auxiliary_part_to_top_level_rules() -> None:
    main = _part(
        full_answer="由DB=DC得到最终角为70°。",
        points=[
            _point(
                "main-step",
                index=1,
                target="得到70°",
                anchor="70°",
            )
        ],
    )
    auxiliary = _part(
        part_id="auxiliary_rules",
        full_answer="",
        points=[
            _point(
                "aux-step",
                index=1,
                target="使用AD∥BC和DB=DC",
                anchor="AD∥BC，DB=DC",
            )
        ],
    )

    normalized = normalize_model_solution_evidence(
        _payload(main, auxiliary),
        question_id=1,
        question_type="calculation",
        taxonomy_contract={},
    )

    evidence = _parse(normalized.payload)
    assert len(evidence.parts) == 1
    assert any("AD∥BC" in item for item in evidence.auxiliary_rules)


def test_normalizer_matches_safe_field_aliases_and_defaults_optional_fields() -> None:
    raw = {
        "schemaVersion": "v2",
        "questionId": 1,
        "questionParts": {
            "partId": "P1",
            "responseMode": "process",
            "canonicalAnswer": "x=1",
            "fullAnswer": "由x+1=2得到x=1。",
            "evidencePoints": {
                "evidencePointId": "Step1",
                "target": "得到x=1",
                "answerAnchor": "x=1",
                "observableEvidence": "写出x=1",
            },
        },
        "reason": "别名回归",
        "confidenceScore": "0.8",
    }

    normalized = normalize_model_solution_evidence(
        raw,
        question_id=1,
        question_type="calculation",
        taxonomy_contract={},
    )

    evidence = _parse(normalized.payload)
    assert evidence.parts[0].part_id == "part-1"
    assert evidence.parts[0].response_mode == "process_required"
    assert evidence.parts[0].deduction_policy
    assert evidence.parts[0].evidence_points[0].justification


def test_normalizer_does_not_repair_wrong_question_identity() -> None:
    raw = _payload(_part(), question_id=2)

    normalized = normalize_model_solution_evidence(
        raw,
        question_id=1,
        question_type="calculation",
        taxonomy_contract={},
    )

    with pytest.raises(ValueError, match="another question"):
        _parse(normalized.payload, question_id=1)


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


def test_normalizer_still_blocks_part_with_no_answer_or_observable_evidence() -> None:
    raw = _payload(
        _part(
            canonical_answer="",
            full_answer="",
            points=[
                {
                    "evidence_point_id": "step",
                    "step_index": 1,
                    "target": "",
                    "justification": "",
                    "answer_anchor": "",
                    "observable_evidence": "",
                    "depends_on": [],
                    "fine_term_links": [],
                    "equivalent_rules": [],
                    "counterexamples": [],
                }
            ],
        )
    )

    normalized = normalize_model_solution_evidence(
        raw,
        question_id=1,
        question_type="calculation",
        taxonomy_contract={},
    )

    with pytest.raises(ValueError, match="full_answer|target"):
        _parse(normalized.payload)


def test_normalizer_is_idempotent_and_preserves_mathematical_content() -> None:
    raw = _payload(
        _part(
            part_id="第2问",
            full_answer="∠ACD=90°-x/2，所以y=x/2。",
            points=[
                _point(
                    "模型步骤A",
                    index=8,
                    target="推出y=x/2",
                    anchor="y=x/2",
                )
            ],
        )
    )

    first = normalize_model_solution_evidence(
        raw,
        question_id=1,
        question_type="calculation",
        taxonomy_contract={},
    )
    second = normalize_model_solution_evidence(
        first.payload,
        question_id=1,
        question_type="calculation",
        taxonomy_contract={},
    )

    assert second.payload == first.payload
    assert first.payload["parts"][0]["full_answer"] == (
        "∠ACD=90°-x/2，所以y=x/2。"
    )
    assert first.payload["parts"][0]["evidence_points"][0]["answer_anchor"] == (
        "y=x/2"
    )


def test_normalizer_preserves_legacy_v1_exact_point_shape() -> None:
    raw = _payload(_part())
    raw["schema_version"] = "question-solution-evidence-v1"
    for point in raw["parts"][0]["evidence_points"]:
        for field in ("step_index", "justification", "answer_anchor", "depends_on"):
            point.pop(field)

    normalized = normalize_model_solution_evidence(
        raw,
        question_id=1,
        question_type="calculation",
        taxonomy_contract={},
    )

    evidence = _parse(normalized.payload)
    point = normalized.payload["parts"][0]["evidence_points"][0]
    assert evidence.schema_version == "question-solution-evidence-v1"
    assert set(point) == {
        "evidence_point_id",
        "target",
        "observable_evidence",
        "fine_term_links",
        "equivalent_rules",
        "counterexamples",
    }


@pytest.mark.parametrize("score_field", ["stepScore", "step-score", "fullScore"])
def test_normalizer_rejects_score_field_spelling_variants(score_field: str) -> None:
    raw = _payload(_part())
    raw["parts"][0]["evidence_points"][0][score_field] = 2

    with pytest.raises(ValueError, match="score field is forbidden"):
        normalize_model_solution_evidence(
            raw,
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


def test_normalizer_keeps_same_part_dependency_when_old_ids_repeat_by_part() -> None:
    first = _part(
        part_id="P1",
        points=[_point("step-1", index=1, target="先得a", anchor="a")],
    )
    second = _part(
        part_id="P2",
        points=[
            _point("step-1", index=1, target="先得b", anchor="b"),
            _point(
                "step-2",
                index=2,
                target="再得c",
                anchor="c",
                depends_on=["step-1"],
            ),
        ],
    )

    normalized = normalize_model_solution_evidence(
        _payload(first, second),
        question_id=1,
        question_type="calculation",
        taxonomy_contract={},
    )

    evidence = _parse(normalized.payload)
    assert evidence.parts[1].evidence_points[1].depends_on == (
        "part-2-step-1",
    )


def test_normalizer_drops_dependency_on_ambiguous_duplicate_old_id() -> None:
    part = _part(
        points=[
            _point("duplicate", index=1, target="先得a", anchor="a"),
            _point("duplicate", index=2, target="再得b", anchor="b"),
            _point(
                "later",
                index=3,
                target="最后得c",
                anchor="c",
                depends_on=["duplicate"],
            ),
        ],
    )

    normalized = normalize_model_solution_evidence(
        _payload(part),
        question_id=1,
        question_type="calculation",
        taxonomy_contract={},
    )

    evidence = _parse(normalized.payload)
    assert evidence.parts[0].evidence_points[2].depends_on == ()
    assert any("歧义依赖" in item for item in normalized.notes)


def test_normalizer_preserves_auxiliary_full_answer_as_rule() -> None:
    auxiliary = _part(part_id="auxiliary_rules", full_answer="AD∥BC，且DB=DC。")
    auxiliary["evidence_points"] = []

    normalized = normalize_model_solution_evidence(
        _payload(_part(), auxiliary),
        question_id=1,
        question_type="calculation",
        taxonomy_contract={},
    )

    evidence = _parse(normalized.payload)
    assert "AD∥BC，且DB=DC。" in evidence.auxiliary_rules


def test_normalizer_marks_discarded_invalid_term_link_for_review() -> None:
    part = _part()
    part["evidence_points"][0]["fine_term_links"] = [
        {
            "fine_term_id": "valid",
            "fine_term_name": "有效词",
            "role": "direct",
        },
        {"fine_term_id": "missing-name", "role": "direct"},
    ]

    normalized = normalize_model_solution_evidence(
        _payload(part),
        question_id=1,
        question_type="calculation",
        taxonomy_contract={},
    )

    assert normalized.requires_review is True
    assert len(normalized.payload["parts"][0]["evidence_points"][0]["fine_term_links"]) == 1


def test_normalizer_does_not_supply_missing_question_identity() -> None:
    raw = _payload(_part())
    raw.pop("question_id")

    normalized = normalize_model_solution_evidence(
        raw,
        question_id=1,
        question_type="calculation",
        taxonomy_contract={},
    )

    with pytest.raises(ValueError, match="question_id is invalid"):
        _parse(normalized.payload)


def test_normalizer_rejects_conflicting_alias_values_in_any_key_order() -> None:
    for aliases in (
        {"answer": "C", "standardAnswer": "D"},
        {"standardAnswer": "D", "answer": "C"},
    ):
        part = _part(mode="exact_objective", canonical_answer="")
        part.pop("canonical_answer")
        part.update(aliases)
        with pytest.raises(ValueError, match="冲突字段"):
            normalize_model_solution_evidence(
                _payload(part),
                question_id=1,
                question_type="single_choice",
                taxonomy_contract={},
            )


def test_normalizer_does_not_collapse_process_question_mislabeled_objective() -> None:
    part = _part(
        mode="exact_objective",
        canonical_answer="x=1",
        full_answer="由x+1=2得到x=1。",
        points=[
            _point("s1", index=1, target="列出x+1=2", anchor="x+1=2"),
            _point("s2", index=2, target="得到x=1", anchor="x=1"),
        ],
    )

    normalized = normalize_model_solution_evidence(
        _payload(part),
        question_id=1,
        question_type="calculation",
        taxonomy_contract={},
    )

    evidence = _parse(normalized.payload)
    assert evidence.parts[0].response_mode == "process_required"
    assert len(evidence.parts[0].evidence_points) == 2


def test_normalizer_rejects_conflicting_single_choice_answers() -> None:
    first = _part(mode="exact_objective", canonical_answer="A")
    second = _part(
        part_id="option-analysis",
        mode="exact_objective",
        canonical_answer="C",
    )

    with pytest.raises(ValueError, match="conflicting canonical answers"):
        normalize_model_solution_evidence(
            _payload(first, second),
            question_id=1,
            question_type="single_choice",
            taxonomy_contract={},
        )


def test_normalizer_treats_single_choice_process_text_as_explanation() -> None:
    option = _part(
        mode="exact_objective",
        canonical_answer="C",
        full_answer="答案为 C。",
    )
    process = _part(
        part_id="process-explanation",
        mode="process_required",
        canonical_answer="腰长为5cm或底边长为5cm",
        full_answer="分两种情况计算，得到3/5或5/4。",
    )

    normalized = normalize_model_solution_evidence(
        _payload(option, process),
        question_id=4,
        question_type="single_choice",
        taxonomy_contract={},
        expected_answer="C",
    )

    evidence = _parse(normalized.payload)
    assert len(evidence.parts) == 1
    assert evidence.parts[0].canonical_answer == "C"
    assert "分两种情况" in evidence.parts[0].full_answer
