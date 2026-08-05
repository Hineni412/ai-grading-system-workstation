from __future__ import annotations

import json
from pathlib import Path

from backend.api.schemas.question_bank import QuestionTagWriteItem
from question_bank.current_knowledge import CurrentKnowledgeResolver
from question_bank.models.tag_schema import TagAnalysis
from question_bank.knowledge_graph_release import (
    load_release,
    load_release_for_taxonomy_revision,
    load_taxonomy_catalog,
    load_taxonomy_catalog_for_release,
    validate_release,
)
from question_bank.services.ai_tagging_service import (
    _batch_question_contract,
    _batch_shared_contract,
)
from question_bank.services.question_write_service import (
    ConfirmedQuestionTag,
    _normalize_tags,
)
from question_bank.taxonomy.curriculum_catalog import (
    curriculum_chapter_exam_scope_values,
    curriculum_knowledge_ancestors,
    curriculum_knowledge_node,
    eligible_curriculum_knowledge_nodes,
    load_curriculum_catalog,
)
from question_bank.taxonomy.governance import TaxonomyGovernance


ROOT = Path(__file__).resolve().parents[1]
CATALOG_DIR = ROOT / "question_bank" / "taxonomy" / "catalogs"
NEW_TAXONOMY_PATH = CATALOG_DIR / "tag_vocabulary_v3.json"
LEGACY_MAPPING_PATH = (
    CATALOG_DIR / "knowledge_standard_v2_legacy_mapping.json"
)


def _governance(tmp_path: Path) -> TaxonomyGovernance:
    return TaxonomyGovernance(
        catalog_path=NEW_TAXONOMY_PATH,
        state_path=tmp_path / "taxonomy-state.json",
        knowledge_graph_db_path=tmp_path / "not-created-question-bank.db",
    )


def test_five_volume_catalog_keeps_exactly_the_frozen_1124_nodes() -> None:
    catalog = load_curriculum_catalog()

    assert catalog["schema_version"] == 2
    assert [volume["id"] for volume in catalog["volumes"]] == [
        "bnu24-math-g7-upper",
        "bnu24-math-g7-lower",
        "bnu24-math-g8-upper",
        "bnu24-math-g8-lower",
        "bnu24-math-g9-upper",
    ]
    assert catalog["statistics"] == {
        "raw_nodes": 1186,
        "excluded_nodes": 62,
        "retained_nodes": 1124,
        "chapters": 36,
        "sections": 126,
        "knowledge_points": 962,
    }
    labels = {
        chapter["label"]
        for volume in catalog["volumes"]
        for chapter in volume["chapters"]
    } | {
        section["label"]
        for volume in catalog["volumes"]
        for chapter in volume["chapters"]
        for section in chapter["sections"]
    } | {
        point["label"]
        for volume in catalog["volumes"]
        for chapter in volume["chapters"]
        for section in chapter["sections"]
        for point in section["knowledge_points"]
    }
    assert "回顾与思考" not in labels
    assert "复习题" not in labels


def test_catalog_keeps_historical_exam_scope_names_as_read_only_aliases() -> None:
    aliases = curriculum_chapter_exam_scope_values()

    assert "七年级上册 第三章 字母表示数" in aliases[
        "bnu24-math-g7-upper-c03"
    ]
    assert "八年级下册 第一章 三角形的证明" in aliases[
        "bnu24-math-g8-lower-c01"
    ]
    assert "八年级下册 第一章 三角形的证明及其应用" in aliases[
        "bnu24-math-g8-lower-c01"
    ]


def test_selected_volume_includes_only_that_volume_and_prior_volumes() -> None:
    expected_counts = {
        "bnu24-math-g7-upper": 243,
        "bnu24-math-g7-lower": 428,
        "bnu24-math-g8-upper": 687,
        "bnu24-math-g8-lower": 916,
        "bnu24-math-g9-upper": 1124,
    }

    for volume_id, expected_count in expected_counts.items():
        nodes = eligible_curriculum_knowledge_nodes(volume_id)
        assert len(nodes) == expected_count
        selected_order = max(int(node["volume_order"]) for node in nodes)
        assert all(int(node["volume_order"]) <= selected_order for node in nodes)


def test_aas_leaf_and_parent_fallback_are_distinct_knowledge_identities() -> None:
    leaf_id = "kp_bnu24_math_g7_lower_4_3_7"
    leaf = curriculum_knowledge_node(leaf_id)

    assert leaf is not None
    assert leaf["label"] == "用ASA（AAS）证明三角形全等（ASA或者AAS）"
    assert curriculum_knowledge_ancestors(leaf_id) == (
        "kp_bnu24_math_g7_lower_4_3",
        "kp_bnu24_math_g7_lower_4",
    )

    resolver = CurrentKnowledgeResolver(
        load_release(),
        load_taxonomy_catalog(),
    )
    leaf_resolution = resolver.resolve(leaf_id)
    section_resolution = resolver.resolve("kp_bnu24_math_g7_lower_4_3")
    assert [item.stable_key for item in leaf_resolution] == [leaf_id]
    assert [item.stable_key for item in section_resolution] == [
        "kp_bnu24_math_g7_lower_4_3"
    ]


def test_full_curriculum_path_survives_tag_analysis_normalization() -> None:
    leaf = curriculum_knowledge_node("kp_bnu24_math_g7_lower_4_3_7")
    assert leaf is not None

    analysis = TagAnalysis.from_dict({"knowledge_points": [leaf["name"]]})

    assert analysis.knowledge_points == [leaf["name"]]
    assert QuestionTagWriteItem(
        tag_type="knowledge_point",
        tag_value=leaf["name"],
    ).tag_value == leaf["name"]
    assert _normalize_tags(
        [ConfirmedQuestionTag("knowledge_point", leaf["name"])]
    )[0].tag_value == leaf["name"]


def test_new_prompt_candidates_are_scoped_retrieved_and_include_parents(
    tmp_path: Path,
) -> None:
    governance = _governance(tmp_path)
    contract = governance.prompt_contract(
        {
            "curriculum_volume_id": "bnu24-math-g7-lower",
            "question_text": "已知两个角和一条边，使用 AAS 证明两个三角形全等",
            "answer_text": "由 AAS 可知两个三角形全等",
            "question_type": "解答题",
        }
    )

    ids = set(contract["allowed_term_ids"]["knowledge"])
    assert "kp_bnu24_math_g7_lower_4_3_7" in ids
    assert "kp_bnu24_math_g7_lower_4_3" in ids
    assert "kp_bnu24_math_g7_lower_4" in ids
    assert len(ids) <= 192
    assert all(
        item.get("volume_id")
        in {"bnu24-math-g7-upper", "bnu24-math-g7-lower"}
        for item in contract["candidates"]["knowledge"]
    )
    assert all("九年级" not in item["name"] for item in contract["candidates"]["knowledge"])


def test_new_standard_requires_a_confirmed_volume_before_knowledge_selection(
    tmp_path: Path,
) -> None:
    contract = _governance(tmp_path).prompt_contract(
        {"question_text": "使用 AAS 证明两个三角形全等"}
    )

    assert contract["retrieval_status"] == "insufficient"
    assert contract["candidates"]["knowledge"] == []


def test_batch_shared_catalog_is_union_but_each_question_keeps_allowed_ids(
    tmp_path: Path,
) -> None:
    governance = _governance(tmp_path)
    contexts = {
        1: {
            "curriculum_volume_id": "bnu24-math-g7-lower",
            "question_text": "使用 AAS 证明三角形全等",
        },
        2: {
            "curriculum_volume_id": "bnu24-math-g8-lower",
            "question_text": "利用一元一次不等式组选择方案",
        },
    }
    contracts = governance.prompt_contracts(contexts)
    batch_contexts = [(1, object()), (2, object())]
    shared = _batch_shared_contract(batch_contexts, contracts)
    shared_ids = {item["id"] for item in shared["shared_knowledge_catalog"]}
    first_ids = set(contracts[1]["allowed_term_ids"]["knowledge"])
    second_ids = set(contracts[2]["allowed_term_ids"]["knowledge"])

    assert shared_ids == first_ids | second_ids
    assert first_ids != second_ids
    assert _batch_question_contract(contracts[1])["allowed_term_ids"][
        "knowledge"
    ] == contracts[1]["allowed_term_ids"]["knowledge"]


def test_new_standard_rejects_known_knowledge_outside_question_scope(
    tmp_path: Path,
) -> None:
    governance = _governance(tmp_path)
    contract = governance.prompt_contract(
        {
            "curriculum_volume_id": "bnu24-math-g7-upper",
            "question_text": "利用一元一次方程解决问题",
        }
    )
    g7_ids = {
        str(item["id"])
        for item in eligible_curriculum_knowledge_nodes(
            "bnu24-math-g7-upper"
        )
    }
    out_of_scope = next(
        item
        for item in eligible_curriculum_knowledge_nodes(
            "bnu24-math-g9-upper"
        )
        if item["id"] not in g7_ids and int(item["level"]) == 3
    )

    constrained = governance.constrain(
        {"knowledge_points": [out_of_scope["name"]]},
        context={
            "allowed_term_ids": contract["allowed_term_ids"],
            "knowledge_catalog_revision": contract[
                "knowledge_catalog_revision"
            ],
        },
    )

    assert constrained["accepted_analysis"]["knowledge"] == []
    assert constrained["proposals"] == []
    assert constrained["retrieval_misses"] == [
        {
            "dimension": "knowledge",
            "submitted_name": out_of_scope["name"],
            "canonical_id": out_of_scope["id"],
            "canonical_name": out_of_scope["name"],
            "source_field": "knowledge_points",
        }
    ]


def test_new_and_legacy_releases_validate_with_their_own_taxonomy_versions() -> None:
    current = load_release()
    legacy = load_release_for_taxonomy_revision(3)

    assert validate_release(
        current,
        load_taxonomy_catalog_for_release(current),
    ).valid
    assert validate_release(
        legacy,
        load_taxonomy_catalog_for_release(legacy),
    ).valid
    assert not validate_release(
        current,
        load_taxonomy_catalog_for_release(legacy),
    ).valid


def test_all_294_legacy_terms_have_an_explicit_non_guessing_disposition() -> None:
    payload = json.loads(LEGACY_MAPPING_PATH.read_text(encoding="utf-8"))

    assert len(payload["items"]) == 294
    assert sum(payload["statistics"].values()) == 294
    assert set(payload["statistics"]) == {
        "exact",
        "unique_alias",
        "split",
        "retired",
    }
    assert all(
        not item["target_ids"]
        for item in payload["items"]
        if item["disposition"] == "retired"
    )
    assert all(
        len(item["target_ids"]) > 1
        for item in payload["items"]
        if item["disposition"] == "split"
    )
