from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend.api.schemas.question_bank import (
    TaxonomyCatalogResponse,
    TaxonomyDimensionsResponse,
    TaxonomyProposalListResponse,
    TaxonomyProposalReviewResponse,
)
from question_bank.taxonomy.governance import (
    ALLOWED_DIMENSIONS,
    TaxonomyGovernance,
    TaxonomyRevisionConflict,
)
from question_bank.models.tag_schema import TagAnalysis
from question_bank.services.ai_tagging_service import _analysis_from_constraint


CATALOG_PATH = (
    Path(__file__).resolve().parents[1]
    / "question_bank"
    / "taxonomy"
    / "catalogs"
    / "tag_vocabulary_v2.json"
)


@pytest.fixture
def governance(tmp_path: Path) -> TaxonomyGovernance:
    return TaxonomyGovernance(
        catalog_path=CATALOG_PATH,
        state_path=tmp_path / "taxonomy-state.json",
    )


def _persist_unknown(
    governance: TaxonomyGovernance,
    *,
    name: str,
    dimension: str = "model",
    token: str,
) -> dict[str, object]:
    return governance.constrain(
        {
            "proposed_tags": [
                {
                    "dimension": dimension,
                    "name": name,
                    "definition": f"{name}的定义",
                    "reason": "现有候选词不能准确表达",
                    "nearest_id": "",
                    "why_not_reuse": "语义边界不同",
                }
            ],
            "teaching_stage": "期末复习",
            "sub_skills": ["不应进入新词表"],
        },
        context={
            "persist_proposals": True,
            "question_id": 17,
            "model": "isolated-fake-model",
            "request_token": token,
        },
    )


def test_catalog_and_prompt_expose_only_five_controlled_dimensions(
    governance: TaxonomyGovernance,
) -> None:
    catalog = governance.catalog()
    response = TaxonomyCatalogResponse(**catalog)
    contract = governance.prompt_contract(
        {"question_text": "用倍长中线证明三角形边之间的关系"}
    )

    assert tuple(TaxonomyDimensionsResponse.model_fields) == ALLOWED_DIMENSIONS
    assert set(contract["candidates"]) == set(ALLOWED_DIMENSIONS)
    assert contract["allowed_dimensions"] == list(ALLOWED_DIMENSIONS)
    assert contract["rules"]["forbidden_dimensions"] == [
        "sub_skill",
        "measured_skill",
        "supporting_skill",
    ]
    assert any(
        item["name"] == "倍长中线"
        for item in contract["candidates"]["method"]
    )
    assert all(
        set(item) == {"id", "name"}
        for values in contract["candidates"].values()
        for item in values
    )
    assert all(
        re.match(r"^[七八九]年级[上下]册 第[一二三四五六七八九十百0-9]+章(?:\s|$)", item.name)
        for item in response.dimensions.curriculum
    )


def test_unknown_term_is_idempotently_queued_and_legacy_fields_are_ignored(
    governance: TaxonomyGovernance,
) -> None:
    first = _persist_unknown(
        governance,
        name="自检规范模型",
        token="1" * 32,
    )
    replay = _persist_unknown(
        governance,
        name="自检规范模型",
        token="1" * 32,
    )
    pending = governance.list_proposals(status="pending")

    assert first == replay
    assert first["status"] == "needs_review"
    assert first["taxonomy_revision"] == 1
    assert first["ignored_legacy_fields"] == ["sub_skills", "teaching_stage"]
    assert pending["revision"] == 1
    assert pending["counts"] == {"pending": 1}
    assert len(pending["items"]) == 1
    assert pending["items"][0]["proposed_name"] == "自检规范模型"
    assert pending["items"][0]["question_refs"] == [17]
    TaxonomyProposalListResponse(**pending)


def test_edit_and_approve_promotes_canonical_term_and_keeps_original_as_alias(
    governance: TaxonomyGovernance,
) -> None:
    created = _persist_unknown(
        governance,
        name="三角形手拉手新说法",
        token="2" * 32,
    )
    proposal_id = str(created["proposals"][0]["id"])

    reviewed = governance.review_proposal(
        proposal_id=proposal_id,
        decision="edit",
        edited_name="手拉手旋转模型",
        target_term_id=None,
        expected_revision=1,
        request_token="3" * 32,
    )
    replay = governance.review_proposal(
        proposal_id=proposal_id,
        decision="edit",
        edited_name="手拉手旋转模型",
        target_term_id=None,
        expected_revision=1,
        request_token="3" * 32,
    )

    assert reviewed == replay
    assert reviewed["revision"] == 2
    assert reviewed["proposal"]["status"] == "approved"
    assert reviewed["approved_term"]["name"] == "手拉手旋转模型"
    assert "三角形手拉手新说法" in reviewed["approved_term"]["aliases"]
    assert (
        governance.resolve_term("model", "三角形手拉手新说法")["name"]
        == "手拉手旋转模型"
    )
    assert "三角形手拉手新说法" in governance.expand_filter_values(
        "model", ["手拉手旋转模型"]
    )
    assert any(
        item["name"] == "手拉手旋转模型"
        for item in governance.prompt_contract(
            {"question_text": "手拉手旋转模型"}
        )["candidates"]["model"]
    )
    TaxonomyProposalReviewResponse(
        **reviewed,
        application_status="not_requested",
    )


def test_stale_review_is_rejected_without_partial_write(
    governance: TaxonomyGovernance,
) -> None:
    first = _persist_unknown(
        governance,
        name="并发候选一",
        token="4" * 32,
    )
    second = _persist_unknown(
        governance,
        name="并发候选二",
        token="5" * 32,
    )
    first_id = str(first["proposals"][0]["id"])
    second_id = str(second["proposals"][0]["id"])

    governance.review_proposal(
        proposal_id=second_id,
        decision="reject",
        edited_name=None,
        target_term_id=None,
        expected_revision=2,
        request_token="6" * 32,
    )

    with pytest.raises(TaxonomyRevisionConflict) as error:
        governance.review_proposal(
            proposal_id=first_id,
            decision="reject",
            edited_name=None,
            target_term_id=None,
            expected_revision=2,
            request_token="7" * 32,
        )

    assert error.value.current_revision == 3
    pending = governance.list_proposals(status="pending")
    assert [item["id"] for item in pending["items"]] == [first_id]
    assert pending["revision"] == 3


def test_merge_adds_candidate_as_alias_without_creating_a_second_term(
    governance: TaxonomyGovernance,
) -> None:
    target = governance.resolve_term("method", "倍长中线")
    assert target is not None
    created = _persist_unknown(
        governance,
        name="中线倍长法",
        dimension="method",
        token="8" * 32,
    )
    proposal_id = str(created["proposals"][0]["id"])

    reviewed = governance.review_proposal(
        proposal_id=proposal_id,
        decision="merge",
        edited_name=None,
        target_term_id=target["id"],
        expected_revision=1,
        request_token="9" * 32,
    )

    assert reviewed["proposal"]["status"] == "merged"
    assert reviewed["approved_term"]["id"] == target["id"]
    assert governance.resolve_term("method", "中线倍长法")["id"] == target["id"]


def test_unknown_formal_value_is_removed_before_analysis_can_be_saved(
    governance: TaxonomyGovernance,
) -> None:
    original = TagAnalysis.from_dict(
        {
            "knowledge_points": ["AI自由造的知识点"],
            "method_tags": ["倍长中线"],
            "ability_tags": ["推理能力"],
            "math_model_tags": [],
            "difficulty": 5,
            "error_prone_points": [],
            "prerequisite_points": [],
            "textbook_chapter": "七年级上册 第一章 丰富的图形世界",
            "suitable_student_level": "中档提升",
            "canonical_knowledge_id": "ai_free_term",
            "reason": "隔离回归",
            "confidence": 0.9,
        }
    )
    constrained = governance.constrain(original.to_dict())

    normalized, proposals, status, _notes = _analysis_from_constraint(
        original,
        constrained,
        fallback_revision=0,
    )

    assert status == "needs_review"
    assert proposals[0]["proposed_name"] == "AI自由造的知识点"
    assert normalized.knowledge_points == []
    assert normalized.canonical_knowledge_id == ""
    assert normalized.method_tags == ["倍长中线"]
    assert normalized.ability_tags == ["推理能力"]


def test_rejected_term_stays_suppressed_when_ai_proposes_it_again(
    governance: TaxonomyGovernance,
) -> None:
    created = _persist_unknown(
        governance,
        name="已拒绝的自由词",
        token="a" * 32,
    )
    proposal_id = str(created["proposals"][0]["id"])
    governance.review_proposal(
        proposal_id=proposal_id,
        decision="reject",
        edited_name=None,
        target_term_id=None,
        expected_revision=1,
        request_token="b" * 32,
    )

    preview = governance.constrain(
        {"math_model_tags": ["已拒绝的自由词"]}
    )
    persisted = governance.constrain(
        {"math_model_tags": ["已拒绝的自由词"]},
        context={
            "persist_proposals": True,
            "question_id": 21,
            "model": "isolated-fake-model",
            "request_token": "c" * 32,
        },
    )

    assert preview["proposals"] == []
    assert preview["status"] == "empty"
    assert persisted["proposals"] == []
    assert persisted["status"] == "empty"
    assert persisted["taxonomy_revision"] == 2
    assert governance.list_proposals(status="pending")["counts"] == {"pending": 0}
