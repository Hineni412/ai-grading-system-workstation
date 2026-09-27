from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any


from question_bank.solution_evidence.contracts import (
    CoreResolution,
    QuestionSolutionEvidence,
)
from question_bank.solution_evidence.convergence import converge_evidence_terms
from question_bank.solution_evidence.repository import SolutionEvidenceProjectionWriter
from question_bank.taxonomy.governance import TaxonomyGovernance
from question_bank.taxonomy.snapshot import QuestionTaxonomySnapshot


LEGACY_CATALOG_PATH = (
    Path(__file__).resolve().parents[2]
    / "question_bank"
    / "taxonomy"
    / "catalogs"
    / "tag_vocabulary_v2.json"
)
CURRENT_CATALOG_PATH = (
    Path(__file__).resolve().parents[2]
    / "question_bank"
    / "taxonomy"
    / "catalogs"
    / "tag_vocabulary_v3.json"
)


def _legacy_governance(state_path: Path) -> TaxonomyGovernance:
    return TaxonomyGovernance(
        catalog_path=LEGACY_CATALOG_PATH,
        state_path=state_path,
    )


def _payload(*links: dict[str, str]) -> dict[str, Any]:
    return {
        "schema_version": "question-solution-evidence-v1",
        "question_id": 1,
        "parts": [
            {
                "part_id": "part-1",
                "label": "第1问",
                "response_mode": "process_required",
                "canonical_answer": "",
                "accepted_forms": [],
                "full_answer": "移项并化简，得到方程的解。",
                "proof_obligations": [],
                "visual_requirements": [],
                "deduction_policy": ["缺少关键变形时该步骤未达成"],
                "allow_alternative_methods": True,
                "evidence_points": [
                    {
                        "evidence_point_id": "step-1",
                        "target": "完成关键等价变形",
                        "observable_evidence": "写出正确的中间式",
                        "fine_term_links": list(links),
                        "equivalent_rules": [],
                        "counterexamples": [],
                    }
                ],
            }
        ],
        "auxiliary_rules": [],
        "rationale": "按可观察过程拆分。",
        "confidence": 0.9,
    }


def _link(term_id: str, name: str, role: str = "direct") -> dict[str, str]:
    return {
        "fine_term_id": term_id,
        "fine_term_name": name,
        "role": role,
    }


def test_conflicting_model_id_and_name_are_not_silently_saved(
    tmp_path: Path,
) -> None:
    convergence = converge_evidence_terms(
        _payload(_link("kp_alg_polynomial", "一元一次方程")),
        taxonomy_contract={"taxonomy_revision": 2, "candidates": {"knowledge": []}},
        governance=_legacy_governance(tmp_path / "taxonomy-state.json"),
        question_ref="config-source:Q1",
        model_name="synthetic-model",
        operation_id="test:conflicting-link",
    )

    point = convergence.payload["parts"][0]["evidence_points"][0]
    assert point["fine_term_links"] == []
    assert convergence.canonical_terms == ()
    assert convergence.unresolved_links[0]["reason_code"] == "id_name_conflict"
