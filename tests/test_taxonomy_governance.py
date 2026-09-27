from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from backend.api.schemas.question_bank import (
    TaxonomyCatalogResponse,
    TaxonomyDimensionsResponse,
    TaxonomyProposalListResponse,
    TaxonomyProposalReviewResponse,
)
import question_bank.taxonomy.governance as governance_module
from question_bank.taxonomy.governance import (
    ALLOWED_DIMENSIONS,
    TaxonomyGovernance,
    TaxonomyRevisionConflict,
    TaxonomyValidationError,
    unique_catalog_term,
)
from question_bank.taxonomy.curriculum_catalog import (
    infer_curriculum_volume_from_text,
)
from question_bank.models.tag_schema import TagAnalysis
from question_bank.services.ai_tagging_service import _analysis_from_constraint
from path_manager import PathManager
from tools.build_taxonomy_catalog import build as build_taxonomy_catalog


CATALOG_PATH = (
    Path(__file__).resolve().parents[1]
    / "question_bank"
    / "taxonomy"
    / "catalogs"
    / "tag_vocabulary_v2.json"
)


def test_curriculum_volume_filename_inference_requires_one_unambiguous_volume() -> None:
    inferred = infer_curriculum_volume_from_text(
        "2025年七年级下册期末数学试卷.docx"
    )

    assert inferred is not None
    assert inferred["id"] == "bnu24-math-g7-lower"
    assert infer_curriculum_volume_from_text("0526学情小结.docx") is None
    assert infer_curriculum_volume_from_text(
        "七年级上册与下册复习资料.docx"
    ) is None


@pytest.fixture
def governance(tmp_path: Path) -> TaxonomyGovernance:
    return TaxonomyGovernance(
        catalog_path=CATALOG_PATH,
        state_path=tmp_path / "taxonomy-state.json",
        knowledge_graph_db_path=tmp_path / "not-created-question-bank.db",
    )


def test_repeated_proposal_reads_reuse_one_validated_state_snapshot(
    governance: TaxonomyGovernance,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = governance._read_state()
    governance.state_path.write_text(
        json.dumps(state, ensure_ascii=False),
        encoding="utf-8",
    )
    original_read_json = governance_module._read_json
    state_read_count = 0

    def counted_read_json(path: Path):
        nonlocal state_read_count
        if Path(path) == governance.state_path:
            state_read_count += 1
        return original_read_json(path)

    monkeypatch.setattr(governance_module, "_read_json", counted_read_json)

    governance.list_proposals(status="pending")
    governance.list_proposals(status="pending")

    assert state_read_count == 1


def test_cached_state_is_invalidated_after_an_external_writer_changes_the_file(
    governance: TaxonomyGovernance,
) -> None:
    assert governance.list_proposals(status="pending")["counts"] == {
        "pending": 0
    }
    writer = TaxonomyGovernance(
        catalog_path=CATALOG_PATH,
        state_path=governance.state_path,
        knowledge_graph_db_path=governance.knowledge_graph_db_path,
    )

    _persist_unknown(writer, name="缓存失效验证词", token="cache-invalidation")

    assert governance.list_proposals(status="pending")["counts"] == {
        "pending": 1
    }


def test_empty_dimension_filter_keeps_the_global_pending_count(
    governance: TaxonomyGovernance,
) -> None:
    _persist_unknown(
        governance,
        name="全局待审核计数验证词",
        dimension="model",
        token="global-pending-count",
    )

    page = governance.list_proposals(status="pending", dimension="method")

    assert page["items"] == []
    assert page["counts"] == {"pending": 1}


def test_default_governance_state_uses_a_clean_v2_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AI_GRADING_TAXONOMY_STATE_PATH", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    manager = PathManager()

    assert manager.taxonomy_state_path == (
        tmp_path / "AIGradingSystem" / "config" / "taxonomy_state_v2.json"
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


def test_catalog_and_prompt_expose_seven_controlled_dimensions(
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
    assert contract["candidate_fingerprint"]
    assert contract["allowed_term_ids"].keys() == contract["candidates"].keys()
    assert contract["rules"]["forbidden_dimensions"] == [
        "sub_skill",
        "measured_skill",
        "supporting_skill",
    ]
    assert any(
        item["name"] == "构造辅助线法"
        for item in contract["candidates"]["method"]
    )
    assert all(
        {"id", "name"}.issubset(item)
        for values in contract["candidates"].values()
        for item in values
    )
    assert len(contract["candidates"]["knowledge"]) == 294
    assert contract["truncated"]["knowledge"] is False
    assert contract["knowledge_graph_release_id"].startswith("kgr_")
    assert all(
        re.match(r"^[七八九]年级[上下]册 第[一二三四五六七八九十百0-9]+章(?:\s|$)", item.name)
        for item in response.dimensions.curriculum
    )
    assert {
        item.name for item in response.dimensions.special_type
    } >= {"动态几何题", "新定义题", "数学阅读理解题"}


def test_catalog_build_is_deterministic_and_keeps_protected_dimensions() -> None:
    built = build_taxonomy_catalog()
    checked_in = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))

    assert built == checked_in
    counts = {
        dimension: sum(
            term["dimension"] == dimension for term in built["terms"]
        )
        for dimension in ALLOWED_DIMENSIONS
    }
    assert counts == {
        "curriculum": 25,
        "knowledge": 294,
        "ability": 10,
        "method": 23,
        "thought": 13,
        "model": 41,
        "special_type": 14,
    }
    assert sum(
        len(term.get("legacy_names", []))
        for term in built["terms"]
        if term["dimension"] == "knowledge"
    ) >= 1000
    model_names = {
        term["name"]
        for term in built["terms"]
        if term["dimension"] == "model"
    }
    assert not model_names.intersection(
        {'"鸡翅"型', '"骨折"型', "A字型", "8字型", "K字型相似"}
    )
    knowledge_names = {
        term["name"]
        for term in built["terms"]
        if term["dimension"] == "knowledge"
    }
    assert not knowledge_names.intersection(
        {
            "一元一次不等式的应用",
            "一次函数的实际应用",
            "图形的相似",
            "图形的变换",
            "列二元一次方程组",
            "画轴对称图形",
        }
    )


def test_model_contract_only_allows_one_new_knowledge_proposal(
    governance: TaxonomyGovernance,
) -> None:
    contract = governance.prompt_contract({"question_text": "一道未分类新题"})
    constrained = governance.constrain(
        {
            "knowledge_points": ["新知识甲", "新知识乙"],
            "method_tags": ["模型自由造的方法"],
            "thought_tags": ["模型自由造的思想"],
            "math_model_tags": ["模型自由造的模型"],
        },
        context={"allowed_term_ids": contract["allowed_term_ids"]},
    )

    assert constrained["accepted_analysis"]["method"] == []
    assert constrained["accepted_analysis"]["thought"] == []
    assert constrained["accepted_analysis"]["model"] == []
    assert [item["proposed_name"] for item in constrained["proposals"]] == [
        "新知识甲"
    ]
    assert any(
        item["name"] == "数学建模思想"
        for item in contract["candidates"]["thought"]
    )


def test_revision_two_state_remains_readable_with_revision_three_catalog(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "taxonomy-state.json"
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "base_catalog_id": "junior-math-controlled-vocabulary-v2",
                "base_catalog_revision": 2,
                "revision": 0,
                "approved_terms": [],
                "proposals": [],
                "applied_operations": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    snapshot = TaxonomyGovernance(
        catalog_path=CATALOG_PATH,
        state_path=state_path,
    ).snapshot()

    assert snapshot["base_catalog_revision"] == 3


def test_per_question_candidates_share_full_knowledge_but_keep_other_shortlists(
    governance: TaxonomyGovernance,
) -> None:
    contracts = governance.prompt_contracts(
        {
            1: {
                "question_text": "用配方法解一元二次方程",
                "grade": "九年级",
                "semester": "上册",
            },
            2: {
                "question_text": "阅读新定义并研究动态几何图形",
                "grade": "八年级",
                "semester": "下册",
            },
        }
    )

    assert contracts[1]["taxonomy_revision"] == contracts[2]["taxonomy_revision"]
    assert contracts[1]["candidate_fingerprint"] != contracts[2]["candidate_fingerprint"]
    assert (
        set(contracts[1]["allowed_term_ids"]["knowledge"])
        == set(contracts[2]["allowed_term_ids"]["knowledge"])
    )
    assert len(contracts[1]["allowed_term_ids"]["knowledge"]) == 294
    assert (
        contracts[1]["allowed_term_ids"]["special_type"]
        != contracts[2]["allowed_term_ids"]["special_type"]
    )
    assert any(
        item["name"] == "新定义题"
        for item in contracts[2]["candidates"]["special_type"]
    )


def test_curriculum_candidates_are_scoped_to_teacher_selected_volume(
    governance: TaxonomyGovernance,
) -> None:
    contract = governance.prompt_contract(
        {
            "question_text": "求三角形内角和",
            "grade": "七年级",
            "semester": "下册",
            "curriculum_volume_id": "bnu24-math-g7-lower",
        }
    )

    assert contract["curriculum_volume"]["id"] == "bnu24-math-g7-lower"
    assert contract["curriculum_volume"]["sections"]
    assert all(
        item["id"].startswith("bnu24-math-g7-lower-")
        for item in contract["candidates"]["curriculum"]
    )
    assert all(
        item["id"].startswith("bnu24-math-g7-lower-")
        for item in contract["curriculum_volume"]["sections"]
    )


def test_curriculum_leaf_label_resolves_to_scoped_stable_term(
    tmp_path: Path,
) -> None:
    governance = TaxonomyGovernance(
        catalog_path=CATALOG_PATH.with_name("tag_vocabulary_v3.json"),
        state_path=tmp_path / "taxonomy-state-v3.json",
        knowledge_graph_db_path=tmp_path / "not-created-question-bank.db",
    )
    contract = governance.prompt_contract(
        {
            "question_text": "线段垂直平分线上的点到两端距离相等",
            "curriculum_volume_id": "bnu24-math-g7-lower",
        }
    )
    candidate = next(
        item
        for item in contract["candidates"]["knowledge"]
        if item["id"] == "kp_bnu24_math_g7_lower_5_2_3"
    )

    constrained = governance.constrain(
        {
            "knowledge_points": [candidate["name"]],
            "canonical_knowledge_id": candidate["id"],
        },
        context={
            "allowed_term_ids": contract["allowed_term_ids"],
            "knowledge_catalog_revision": contract[
                "knowledge_catalog_revision"
            ],
        },
    )

    assert [
        item["id"] for item in constrained["accepted_terms"]["knowledge"]
    ] == [candidate["id"]]
    assert constrained["proposals"] == []


def test_constraint_echoes_accepted_controlled_fields() -> None:
    original = TagAnalysis.from_dict(
        {
            "method_tags": ["方法甲", "方法乙"],
            "difficulty": 3,
            "confidence": 0.98,
        }
    )
    constrained = {
        "accepted_fields": {
            "method_tags": ["规范方法甲", "规范方法乙"],
        },
        "proposals": [],
        "status": "complete",
        "taxonomy_revision": 4,
    }

    normalized, proposals, status, _notes = _analysis_from_constraint(
        original,
        constrained,
        fallback_revision=4,
    )

    assert normalized.method_tags == ["规范方法甲", "规范方法乙"]
    assert proposals == []
    assert status == "complete"


def test_historical_saved_tags_do_not_bias_the_new_candidate_contract(
    governance: TaxonomyGovernance,
) -> None:
    current = governance.prompt_contract({"question_text": "计算 1+1"})
    with_legacy_tags = governance.prompt_contract(
        {
            "question_text": "计算 1+1",
            "existing_tags_by_dimension": {
                "special_type": ["动态几何题"],
                "knowledge": ["旧版混乱标签"],
            },
        }
    )

    assert (
        with_legacy_tags["candidate_fingerprint"]
        == current["candidate_fingerprint"]
    )


def test_legacy_fine_name_recalls_canonical_parent_without_becoming_candidate(
    governance: TaxonomyGovernance,
) -> None:
    contract = governance.prompt_contract(
        {"question_text": "根据成轴对称图形的特征进行求解"}
    )

    assert governance.resolve_term(
        "knowledge", "根据成轴对称图形的特征进行求解"
    )["name"] == "轴对称的性质"
    assert any(
        item["name"] == "轴对称的性质"
        for item in contract["candidates"]["knowledge"]
    )
    assert all(
        item["name"] != "根据成轴对称图形的特征进行求解"
        for item in contract["candidates"]["knowledge"]
    )


def test_obvious_near_synonym_resolves_to_existing_core_identity(
    governance: TaxonomyGovernance,
) -> None:
    resolved = governance.resolve_term("knowledge", "一次函数的实际应用")

    assert resolved is not None
    assert resolved["name"] == "一次函数应用"


def test_unique_suffix_and_particle_paraphrases_adopt_existing_terms() -> None:
    terms = [
        {
            "id": "k-proof",
            "dimension": "knowledge",
            "status": "approved",
            "name": "三角形内角和定理的证明",
        },
        {
            "id": "k-isosceles",
            "dimension": "knowledge",
            "status": "approved",
            "name": "等腰三角形的性质",
        },
        {
            "id": "k-complement",
            "dimension": "knowledge",
            "status": "approved",
            "name": "直角三角形两锐角互余",
        },
    ]

    theorem = unique_catalog_term("knowledge", "三角形内角和定理", terms)
    complement = unique_catalog_term(
        "knowledge", "直角三角形的两锐角互余", terms
    )
    fuzzy = unique_catalog_term("knowledge", "三角形内角和性质", terms)

    assert theorem is not None
    assert theorem["id"] == "k-proof"
    assert complement is not None
    assert complement["id"] == "k-complement"
    assert fuzzy is None


def test_ambiguous_suffix_paraphrase_does_not_auto_adopt() -> None:
    terms = [
        {
            "id": "k-proof",
            "dimension": "knowledge",
            "status": "approved",
            "name": "三角形内角和定理的证明",
        },
        {
            "id": "k-property",
            "dimension": "knowledge",
            "status": "approved",
            "name": "三角形内角和定理的性质",
        },
    ]

    assert unique_catalog_term("knowledge", "三角形内角和定理", terms) is None


def test_constrain_adopts_unique_proof_suffix_without_proposal(
    governance: TaxonomyGovernance,
) -> None:
    constrained = governance.constrain(
        {"knowledge_points": ["一次函数应用的证明"]}
    )
    resolved = governance.resolve_term("knowledge", "一次函数应用的证明")

    assert constrained["accepted_analysis"]["knowledge"] == ["一次函数应用"]
    assert constrained["proposals"] == []
    assert resolved is not None
    assert resolved["name"] == "一次函数应用"


def test_dimension_resolution_is_strict_except_old_method_thought_field(
    governance: TaxonomyGovernance,
) -> None:
    assert governance.resolve_term("method", "方程模型") is None
    assert governance.resolve_term("knowledge", "方程思想") is None

    contract = governance.prompt_contract({"question_text": "列方程求解"})
    constrained = governance.constrain(
        {
            "knowledge_points": ["方程思想"],
            "method_tags": ["方程思想"],
        },
        context={"allowed_term_ids": contract["allowed_term_ids"]},
    )

    assert constrained["accepted_analysis"]["knowledge"] == []
    assert constrained["accepted_analysis"]["thought"] == ["方程思想"]
    assert constrained["proposals"] == []


def test_filter_expansion_includes_hidden_legacy_values(
    governance: TaxonomyGovernance,
) -> None:
    expanded = governance.expand_filter_values(
        "knowledge",
        ["一次函数应用"],
    )

    assert "一次函数应用" in expanded
    assert "一次函数的实际应用" in expanded


def test_full_vocabulary_match_outside_shortlist_is_accepted_and_reported(
    governance: TaxonomyGovernance,
) -> None:
    constrained = governance.constrain(
        {"knowledge_points": ["整式运算"]},
        context={
            "allowed_term_ids": {
                dimension: [] for dimension in ALLOWED_DIMENSIONS
            }
        },
    )

    assert constrained["accepted_analysis"]["knowledge"] == ["整式运算"]
    assert constrained["proposals"] == []
    assert constrained["retrieval_misses"] == [
        {
            "dimension": "knowledge",
            "submitted_name": "整式运算",
            "canonical_id": "kp_alg_polynomial",
            "canonical_name": "整式运算",
            "source_field": "knowledge_points",
        }
    ]


def test_each_question_keeps_only_two_unknown_free_proposals(
    governance: TaxonomyGovernance,
) -> None:
    constrained = governance.constrain(
        {
            "proposed_tags": [
                {"dimension": "knowledge", "name": "自由词一"},
                {"dimension": "method", "name": "自由词二"},
                {"dimension": "special_type", "name": "自由词三"},
            ]
        }
    )

    assert [
        item["proposed_name"] for item in constrained["proposals"]
    ] == ["自由词一", "自由词二"]
    assert constrained["proposal_overflow"] == [
        {
            "dimension": "special_type",
            "name": "自由词三",
            "source_field": "proposed_tags",
        }
    ]


def test_persistable_proposal_uses_its_name_not_its_transport_id(
    governance: TaxonomyGovernance,
) -> None:
    constrained = governance.constrain(
        {
            "knowledge_points": ["待治理的新知识"],
            "proposed_tags": [
                {
                    "id": "proposal-transport-id",
                    "dimension": "knowledge",
                    "proposed_name": "待治理的新知识",
                    "definition": "一个需要教师判断的新知识标签",
                    "reason": "当前正式词表没有准确表达",
                    "nearest_id": "",
                    "why_not_reuse": "语义边界不同",
                }
            ],
        }
    )

    assert len(constrained["proposals"]) == 1
    assert constrained["proposals"][0]["proposed_name"] == "待治理的新知识"
    assert constrained["proposals"][0]["definition"] == "一个需要教师判断的新知识标签"
    assert constrained["proposals"][0]["reason"] == "当前正式词表没有准确表达"


def test_exact_composite_curriculum_name_resolves_to_multiple_existing_terms(
    governance: TaxonomyGovernance,
) -> None:
    first = "七年级下册 第二章 相交线与平行线"
    second = "七年级下册 第四章 三角形"

    constrained = governance.constrain(
        {
            "proposed_tags": [
                {
                    "dimension": "curriculum",
                    "name": f"{first}，{second}",
                    "reason": "本题综合两个章节",
                }
            ]
        }
    )

    assert constrained["accepted_analysis"]["curriculum"] == [first, second]
    assert constrained["proposals"] == []
    assert constrained["status"] == "accepted"


def test_composite_curriculum_is_not_split_when_any_part_is_unknown(
    governance: TaxonomyGovernance,
) -> None:
    constrained = governance.constrain(
        {
            "proposed_tags": [
                {
                    "dimension": "curriculum",
                    "name": "七年级下册 第二章 相交线与平行线，未来教材未知章",
                    "reason": "本题综合两个章节",
                }
            ]
        }
    )

    assert constrained["accepted_analysis"]["curriculum"] == []
    assert [
        item["proposed_name"] for item in constrained["proposals"]
    ] == ["七年级下册 第二章 相交线与平行线，未来教材未知章"]


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
    retried_by_new_job = _persist_unknown(
        governance,
        name="自检规范模型",
        token="d" * 32,
    )
    pending = governance.list_proposals(status="pending")

    assert first == replay
    assert retried_by_new_job["taxonomy_revision"] == 1
    assert first["status"] == "needs_review"
    assert first["taxonomy_revision"] == 1
    assert first["ignored_legacy_fields"] == ["sub_skills", "teaching_stage"]
    assert pending["revision"] == 1
    assert pending["counts"] == {"pending": 1}
    assert len(pending["items"]) == 1
    assert pending["items"][0]["proposed_name"] == "自检规范模型"
    assert pending["items"][0]["question_refs"] == [17]
    TaxonomyProposalListResponse(**pending)


def test_proposal_receipt_is_bound_to_the_frozen_candidate_contract(
    governance: TaxonomyGovernance,
) -> None:
    current_term_id = "kp_bnu24_math_g7_lower_4_3_7"
    current_term_name = (
        "七年级下册｜第四章 三角形｜3 探索三角形全等的条件｜"
        "用ASA（AAS）证明三角形全等（ASA或者AAS）"
    )
    payload = {
        "proposed_tags": [
            {
                "dimension": "knowledge",
                "name": current_term_name,
                "definition": "用于验证候选目录边界",
                "reason": "现有候选词不能准确表达",
                "nearest_id": "",
                "why_not_reuse": "语义边界不同",
            }
        ]
    }
    token = "candidate-contract-token"
    common_context = {
        "persist_proposals": True,
        "question_ref": "Q-contract",
        "model": "isolated-fake-model",
        "request_token": token,
    }
    first = governance.constrain(
        payload,
        context={
            **common_context,
            "knowledge_catalog_revision": 3,
            "allowed_term_ids": {"knowledge": ["b", "a", "a"]},
        },
    )
    assert first["proposals"]
    replay = governance.constrain(
        payload,
        context={
            **common_context,
            "knowledge_catalog_revision": 3,
            "allowed_term_ids": {"knowledge": ["a", "b"]},
        },
    )

    assert replay == first
    state_before_conflict = governance.list_proposals(status="pending")
    with pytest.raises(
        TaxonomyValidationError,
        match="request_token was already used for a different operation",
    ):
        governance.constrain(
            payload,
            context={
                **common_context,
                "knowledge_catalog_revision": 4,
                "allowed_term_ids": {"knowledge": [current_term_id]},
            },
        )
    assert governance.list_proposals(status="pending") == state_before_conflict


def test_catalog_revision_is_loaded_once_per_governance_instance(
    governance: TaxonomyGovernance,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    governance.snapshot(knowledge_catalog_revision=4)

    monkeypatch.setattr(
        governance_module,
        "load_release_for_taxonomy_revision",
        lambda _revision: pytest.fail("cached catalog must be reused"),
    )

    resolved = governance.resolve_term(
        "knowledge",
        "用ASA（AAS）证明三角形全等（ASA或者AAS）",
        knowledge_catalog_revision=4,
    )
    assert resolved is not None
    assert resolved["id"] == "kp_bnu24_math_g7_lower_4_3_7"


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
            "method_tags": ["AI自由造的方法", "倍长中线"],
            "ability_tags": ["推理能力"],
            "math_model_tags": [],
            "difficulty": 5,
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
    assert proposals[0]["proposed_name"] == "AI自由造的方法"
    assert normalized.method_tags == ["构造辅助线法"]
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


@pytest.fixture
def governance_v3(tmp_path: Path) -> TaxonomyGovernance:
    return TaxonomyGovernance(
        catalog_path=CATALOG_PATH.with_name("tag_vocabulary_v3.json"),
        state_path=tmp_path / "taxonomy-state-v3.json",
        knowledge_graph_db_path=tmp_path / "not-created-question-bank.db",
    )


def _g7_lower_context(governance: TaxonomyGovernance) -> dict[str, object]:
    contract = governance.prompt_contract(
        {
            "question_text": "本地预归并规则验证题",
            "curriculum_volume_id": "bnu24-math-g7-lower",
        }
    )
    return {
        "allowed_term_ids": contract["allowed_term_ids"],
        "knowledge_catalog_revision": contract["knowledge_catalog_revision"],
    }


def _write_premerge_catalog(
    tmp_path: Path, terms: list[dict[str, object]]
) -> Path:
    catalog_path = tmp_path / "premerge-catalog.json"
    catalog_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "catalog_id": "test-local-premerge",
                "revision": 4,
                "source_snapshot": {},
                "classification_policy": {},
                "terms": terms,
                "reference_candidates": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return catalog_path


def test_sanitized_catalog_name_adopts_scoped_section_leaf(
    governance_v3: TaxonomyGovernance,
) -> None:
    constrained = governance_v3.constrain(
        {"knowledge_points": ["平行线的性质"]},
        context=_g7_lower_context(governance_v3),
    )

    assert [
        item["id"] for item in constrained["accepted_terms"]["knowledge"]
    ] == ["kp_bnu24_math_g7_lower_2_3"]
    assert constrained["proposals"] == []
    assert constrained["local_adoptions"] == [
        {
            "dimension": "knowledge",
            "submitted_name": "平行线的性质",
            "rule": "sanitized_name",
            "canonical_id": "kp_bnu24_math_g7_lower_2_3",
            "canonical_name": "七年级下册｜第二章 相交线与平行线｜3 平行线的性质",
            "source_field": "knowledge_points",
        }
    ]


def test_curriculum_verb_prefix_is_stripped_before_matching(
    governance_v3: TaxonomyGovernance,
) -> None:
    constrained = governance_v3.constrain(
        {"knowledge_points": ["掌握轴对称图形的识别"]},
        context=_g7_lower_context(governance_v3),
    )

    assert [
        item["id"] for item in constrained["accepted_terms"]["knowledge"]
    ] == ["kp_bnu24_math_g7_lower_5_1_1"]
    assert constrained["proposals"] == []
    assert [
        (item["rule"], item["canonical_id"])
        for item in constrained["local_adoptions"]
    ] == [("verb_stripped", "kp_bnu24_math_g7_lower_5_1_1")]


def test_unique_containment_adopts_without_proposal(
    governance_v3: TaxonomyGovernance,
) -> None:
    constrained = governance_v3.constrain(
        {"knowledge_points": ["角平分线的定义"]},
        context=_g7_lower_context(governance_v3),
    )

    assert [
        item["id"] for item in constrained["accepted_terms"]["knowledge"]
    ] == ["kp_bnu24_math_g7_lower_4_1_12"]
    assert constrained["proposals"] == []
    assert [
        (item["rule"], item["canonical_id"])
        for item in constrained["local_adoptions"]
    ] == [("unique_containment", "kp_bnu24_math_g7_lower_4_1_12")]


def test_ambiguous_sanitized_name_stays_a_proposal(
    governance_v3: TaxonomyGovernance,
) -> None:
    # "最短路径问题" is the sanitized leaf of both a g7-upper and a g7-lower
    # term; without a volume scope no deterministic adoption is allowed.
    constrained = governance_v3.constrain(
        {"knowledge_points": ["最短路径问题"]}
    )

    assert constrained["accepted_terms"]["knowledge"] == []
    assert [
        item["proposed_name"] for item in constrained["proposals"]
    ] == ["最短路径问题"]
    assert constrained["local_adoptions"] == []


def test_ambiguous_containment_stays_a_proposal(
    governance_v3: TaxonomyGovernance,
) -> None:
    # Unscoped, "角平分线的定义" is contained in "三角形角平分线的定义" and
    # itself contains the g8-lower section leaf "角平分线".
    constrained = governance_v3.constrain(
        {"knowledge_points": ["角平分线的定义"]}
    )

    assert constrained["accepted_terms"]["knowledge"] == []
    assert [
        item["proposed_name"] for item in constrained["proposals"]
    ] == ["角平分线的定义"]
    assert constrained["local_adoptions"] == []


def test_short_containment_stem_stays_a_proposal(tmp_path: Path) -> None:
    catalog_path = _write_premerge_catalog(
        tmp_path,
        [
            {
                "id": "kp-draw-axis",
                "dimension": "knowledge",
                "name": "七年级下册｜第五章 图形的轴对称｜1 轴对称及其性质｜画对称轴",
                "aliases": [],
                "status": "approved",
            }
        ],
    )
    governance = TaxonomyGovernance(
        catalog_path=catalog_path,
        state_path=tmp_path / "taxonomy-state.json",
        knowledge_graph_db_path=tmp_path / "not-created-question-bank.db",
    )

    constrained = governance.constrain({"knowledge_points": ["对称轴"]})

    assert constrained["accepted_terms"]["knowledge"] == []
    assert [
        item["proposed_name"] for item in constrained["proposals"]
    ] == ["对称轴"]
    assert constrained["local_adoptions"] == []


def test_out_of_scope_match_keeps_the_restricted_boundary(
    governance_v3: TaxonomyGovernance,
) -> None:
    # "角平分线的判定定理" only exists in the g8-lower volume, outside the
    # teacher-selected g7-lower scope: the pre-merge rules must not adopt it.
    constrained = governance_v3.constrain(
        {"knowledge_points": ["角平分线的判定定理"]},
        context=_g7_lower_context(governance_v3),
    )

    assert constrained["accepted_terms"]["knowledge"] == []
    assert [
        item["canonical_id"] for item in constrained["retrieval_misses"]
    ] == ["kp_bnu24_math_g8_lower_1_5_2"]
    assert constrained["proposals"] == []
    assert constrained["local_adoptions"] == []
