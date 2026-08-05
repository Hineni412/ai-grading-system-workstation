from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from question_bank.solution_evidence.contracts import (
    CoreResolution,
    QuestionSolutionEvidence,
    validate_evidence_fine_terms,
)
from question_bank.solution_evidence.convergence import converge_evidence_terms
from question_bank.solution_evidence.repository import SolutionEvidenceProjectionWriter
from question_bank.taxonomy.governance import TaxonomyGovernance
from question_bank.training_criteria.analysis import combined_response_format


LEGACY_CATALOG_PATH = (
    Path(__file__).resolve().parents[2]
    / "question_bank"
    / "taxonomy"
    / "catalogs"
    / "tag_vocabulary_v2.json"
)


def _legacy_governance(state_path: Path) -> TaxonomyGovernance:
    return TaxonomyGovernance(
        catalog_path=LEGACY_CATALOG_PATH,
        state_path=state_path,
    )


class _Resolver:
    def resolve(self, fine_term_id: str) -> CoreResolution:
        return CoreResolution(status="unmapped", reason=f"test:{fine_term_id}")


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


def test_convergence_uses_full_vocabulary_and_keeps_unknown_out_of_formal_links(
    tmp_path: Path,
) -> None:
    governance = _legacy_governance(tmp_path / "taxonomy-state.json")

    convergence = converge_evidence_terms(
        _payload(
            _link("kp_alg_linear_equation", "解一元一次方程"),
            _link(
                "invented-local-term",
                "模型新造词",
                "supporting_prerequisite",
            ),
        ),
        taxonomy_contract={
            "taxonomy_revision": 2,
            "allowed_term_ids": {"knowledge": []},
            "candidates": {"knowledge": []},
        },
        governance=governance,
        question_ref="Q1",
        model_name="synthetic-model",
        operation_id="test:read-only-convergence",
    )

    point = convergence.payload["parts"][0]["evidence_points"][0]
    assert point["fine_term_links"] == [
        {
            "fine_term_id": "kp_alg_linear_equation",
            "fine_term_name": "一元一次方程",
            "role": "direct",
        }
    ]
    assert convergence.canonical_term_ids == ("kp_alg_linear_equation",)
    assert convergence.canonical_terms == (
        {
            "id": "kp_alg_linear_equation",
            "name": "一元一次方程",
            "aliases": ["解一元一次方程", "一元一次方程的解法"],
        },
    )
    assert convergence.unresolved_links == (
        {
            "part_id": "part-1",
            "evidence_point_id": "step-1",
            "role": "supporting_prerequisite",
            "submitted_id": "invented-local-term",
            "submitted_name": "模型新造词",
            "proposal_id": convergence.proposals[0]["id"],
            "reason_code": "unknown_term",
        },
    )
    assert convergence.proposals[0]["proposed_name"] == "模型新造词"
    assert convergence.retrieval_misses[0]["canonical_id"] == (
        "kp_alg_linear_equation"
    )
    assert convergence.taxonomy_revision >= 0
    assert governance.list_proposals(status="pending")["counts"] == {"pending": 0}


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


@pytest.mark.parametrize(
    ("submitted_id", "submitted_name", "correction"),
    [
        (
            "kp_alg_linear_equation",
            "模型改写的显示名称",
            "display_name_replaced",
        ),
        ("model-internal-id", "解一元一次方程", "term_id_replaced"),
    ],
)
def test_unambiguous_id_or_name_match_is_repaired_locally(
    tmp_path: Path,
    submitted_id: str,
    submitted_name: str,
    correction: str,
) -> None:
    convergence = converge_evidence_terms(
        _payload(_link(submitted_id, submitted_name)),
        taxonomy_contract={"taxonomy_revision": 2, "candidates": {"knowledge": []}},
        governance=_legacy_governance(
            tmp_path / f"taxonomy-{correction}.json"
        ),
        question_ref="Q1",
        model_name="synthetic-model",
        operation_id=f"test:{correction}",
    )

    point = convergence.payload["parts"][0]["evidence_points"][0]
    assert point["fine_term_links"] == [
        {
            "fine_term_id": "kp_alg_linear_equation",
            "fine_term_name": "一元一次方程",
            "role": "direct",
        }
    ]
    assert convergence.unresolved_links == ()
    assert convergence.proposals == ()
    assert convergence.secondary_matches[0]["correction"] == correction


def test_same_term_keeps_distinct_roles_and_deduplicates_only_exact_links(
    tmp_path: Path,
) -> None:
    convergence = converge_evidence_terms(
        _payload(
            _link(
                "kp_alg_linear_equation",
                "一元一次方程",
                "supporting_prerequisite",
            ),
            _link("kp_alg_linear_equation", "一元一次方程", "direct"),
            _link("kp_alg_linear_equation", "一元一次方程", "direct"),
        ),
        taxonomy_contract={"taxonomy_revision": 2, "candidates": {"knowledge": []}},
        governance=_legacy_governance(tmp_path / "taxonomy-roles.json"),
        question_ref="Q1",
        model_name="synthetic-model",
        operation_id="test:distinct-roles",
    )

    links = convergence.payload["parts"][0]["evidence_points"][0][
        "fine_term_links"
    ]
    assert [(item["fine_term_id"], item["role"]) for item in links] == [
        ("kp_alg_linear_equation", "supporting_prerequisite"),
        ("kp_alg_linear_equation", "direct"),
    ]
    assert convergence.canonical_term_ids == ("kp_alg_linear_equation",)


def test_free_text_does_not_turn_short_alias_substrings_into_formal_links(
    tmp_path: Path,
) -> None:
    payload = _payload()
    point = payload["parts"][0]["evidence_points"][0]
    point["target"] = "求三角形面积"
    point["observable_evidence"] = "写出面积计算结果"

    convergence = converge_evidence_terms(
        payload,
        taxonomy_contract={"taxonomy_revision": 2, "candidates": {"knowledge": []}},
        governance=_legacy_governance(tmp_path / "taxonomy-short-alias.json"),
        question_ref="Q1",
        model_name="synthetic-model",
        operation_id="test:no-free-text-substring-inference",
    )

    assert convergence.payload["parts"][0]["evidence_points"][0][
        "fine_term_links"
    ] == []
    assert convergence.canonical_terms == ()
    assert convergence.secondary_matches == ()


def test_scoring_evidence_and_validator_allow_zero_taxonomy_links() -> None:
    evidence = QuestionSolutionEvidence.from_model_dict(
        _payload(),
        question_id=1,
        source_content_hash="a" * 64,
        resolver=_Resolver(),
    )

    validate_evidence_fine_terms(
        evidence,
        {"taxonomy_revision": 2, "candidates": {"knowledge": []}},
    )
    assert evidence.parts[0].evidence_points[0].fine_term_links == ()


def test_combined_schema_allows_zero_taxonomy_links() -> None:
    schema = combined_response_format("both")["schema"]
    link_array = schema["properties"]["results"]["items"]["properties"][
        "solution_evidence"
    ]["properties"]["parts"]["items"]["properties"]["evidence_points"][
        "items"
    ]["properties"]["fine_term_links"]

    assert link_array["type"] == "array"
    assert link_array.get("minItems", 0) == 0


class _EvidenceRepositorySpy:
    def __init__(self) -> None:
        self.saved: QuestionSolutionEvidence | None = None

    def save(self, evidence: QuestionSolutionEvidence, **_kwargs: Any) -> str:
        self.saved = evidence
        return evidence.version_id


def test_formal_writer_saves_sound_scoring_without_unresolved_formal_links(
    tmp_path: Path,
) -> None:
    repository = _EvidenceRepositorySpy()
    writer = SolutionEvidenceProjectionWriter(
        mapping_repository=_Resolver(),
        evidence_repository=repository,  # type: ignore[arg-type]
        taxonomy_governance=_legacy_governance(
            tmp_path / "taxonomy-state.json"
        ),
    )

    evidence = writer.write(
        SimpleNamespace(
            question_id=1,
            question_type_group="subjective",
            question_type_confirmed=True,
            explicit_part_labels=(),
            objective_response_shape=None,
            taxonomy_contract={
                "taxonomy_revision": 2,
                "candidates": {"knowledge": []},
            },
            tagging_context=SimpleNamespace(
                question_text="完成关键等价变形",
                answer_text="写出正确的中间式",
                question_type="解答题",
                has_images=False,
            ),
            rich_question_blocks=(),
            rich_answer_blocks=(),
            images=(),
        ),
        _payload(_link("invented-local-term", "模型新造词")),
        model_name="synthetic-model",
        operation_id="test:formal-persistence-gate",
    )

    assert repository.saved is evidence
    assert evidence.parts[0].evidence_points[0].fine_term_links == ()
    audit = writer.audit_summary("test:formal-persistence-gate", (1,))
    assert audit["unresolved_links"][0]["submitted_name"] == "模型新造词"
    assert audit["missing_link_points"] == [
        {
            "part_id": "part-1",
            "evidence_point_id": "part-1-step-1",
            "reason_code": "missing_formal_link",
        }
    ]
    assert audit["proposals"][0]["question_refs"] == ["1"]
