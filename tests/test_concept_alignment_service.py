from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from question_bank.services.concept_alignment_service import ConceptAlignmentService


@pytest.fixture
def alignment_service(tmp_path: Path) -> ConceptAlignmentService:
    return ConceptAlignmentService(tmp_path / "question_bank.db")


def test_confirmed_mapping_resolves_directly(alignment_service: ConceptAlignmentService) -> None:
    concept = alignment_service.create_concept("math.quadratic", "二次函数")
    alignment_service.confirm_mapping(
        source_namespace="grading_weak_point",
        source_value="二次函数图像",
        concept_id=concept.id,
    )

    resolved = alignment_service.resolve("grading_weak_point", "二次函数图像")

    assert resolved.status.value == "confirmed"
    assert resolved.concept is not None
    assert resolved.concept.id == concept.id
    assert resolved.eligible_for_recommendation is True


def test_suggested_mapping_never_becomes_eligible_without_confirmation(
    alignment_service: ConceptAlignmentService,
) -> None:
    concept = alignment_service.create_concept(
        "math.quadratic",
        "二次函数",
        aliases=["抛物线"],
    )

    resolved = alignment_service.resolve("grading_weak_point", "抛物线")

    assert resolved.status.value == "suggested"
    assert resolved.concept is not None
    assert resolved.concept.id == concept.id
    assert resolved.eligible_for_recommendation is False
    assert alignment_service.coverage_metrics() == {}


def test_rejected_mapping_is_not_suggested_again(
    alignment_service: ConceptAlignmentService,
) -> None:
    alignment_service.create_concept("math.quadratic", "二次函数", aliases=["抛物线"])
    alignment_service.reject_mapping("grading_weak_point", "抛物线", reviewed_by="teacher")

    resolved = alignment_service.resolve("grading_weak_point", "抛物线")

    assert resolved.status.value == "rejected"
    assert resolved.concept is None
    assert resolved.eligible_for_recommendation is False


def test_suggestion_cannot_overwrite_teacher_decision(
    alignment_service: ConceptAlignmentService,
) -> None:
    confirmed = alignment_service.create_concept("math.function", "函数")
    suggested = alignment_service.create_concept("math.quadratic", "二次函数")
    alignment_service.confirm_mapping(
        "grading_weak_point",
        "函数图像",
        confirmed.id,
        reviewed_by="teacher",
    )

    mapping = alignment_service.suggest_mapping(
        "grading_weak_point",
        "函数图像",
        suggested.id,
        confidence=0.95,
    )

    assert mapping.status.value == "confirmed"
    assert mapping.concept_id == confirmed.id


def test_batch_confirm_is_atomic(alignment_service: ConceptAlignmentService) -> None:
    concept = alignment_service.create_concept("math.function", "函数")

    with pytest.raises(sqlite3.IntegrityError):
        alignment_service.confirm_many(
            [
                ("grading_weak_point", "函数关系", concept.id),
                ("question_tag", "函数", 999999),
            ]
        )

    assert alignment_service.list_mappings() == []


def test_relation_and_coverage_metrics_are_persisted(
    alignment_service: ConceptAlignmentService,
) -> None:
    parent = alignment_service.create_concept("math.function", "函数")
    child = alignment_service.create_concept("math.quadratic", "二次函数")
    alignment_service.create_relation(child.id, parent.id, "parent", weight=0.8)
    alignment_service.confirm_many(
        [
            ("grading_weak_point", "二次函数图像", child.id),
            ("question_tag", "二次函数", child.id),
        ]
    )

    assert alignment_service.list_relations(child.id)[0]["target_concept_id"] == parent.id
    assert alignment_service.coverage_metrics() == {
        "grading_weak_point": {"confirmed": 1},
        "question_tag": {"confirmed": 1},
    }


def test_registry_seed_creates_stable_concepts(
    alignment_service: ConceptAlignmentService,
) -> None:
    created = alignment_service.seed_registry_concepts()

    assert created > 0
    assert alignment_service.list_concepts()
    assert alignment_service.seed_registry_concepts() == 0


def test_sub_skill_tags_persistence(alignment_service: ConceptAlignmentService) -> None:
    concept = alignment_service.create_concept("math.quadratic", "二次函数")

    # Test confirm_mapping
    m = alignment_service.confirm_mapping(
        source_namespace="grading_weak_point",
        source_value="二次函数图像",
        concept_id=concept.id,
        sub_skill_tags=["图像性质", "顶点"],
        reviewed_by="teacher"
    )
    assert m.sub_skill_tags == ("图像性质", "顶点")

    # Test resolve
    res = alignment_service.resolve("grading_weak_point", "二次函数图像")
    assert res.sub_skill_tags == ("图像性质", "顶点")

    # Test confirm_many
    alignment_service.confirm_many([
        ("grading_weak_point", "二次函数的最大值", concept.id, ["最大值", "极值"])
    ])
    res2 = alignment_service.resolve("grading_weak_point", "二次函数的最大值")
    assert res2.sub_skill_tags == ("最大值", "极值")


class MockLLMClient:
    def __init__(self, response: dict[str, Any]):
        self.response = response
        self.prompts = []

    def json_from_text(self, prompt: str) -> dict[str, Any]:
        self.prompts.append(prompt)
        return self.response


def test_ai_batch_align(alignment_service: ConceptAlignmentService) -> None:
    concept = alignment_service.create_concept("math.quadratic", "二次函数")

    mock_response = {
        "alignments": [
            {
                "source_value": "等腰三角形的角度计算",
                "concept_key": "math.quadratic",
                "sub_skill_tags": ["角度计算"],
                "confidence": 0.85
            }
        ]
    }
    mock_client = MockLLMClient(mock_response)

    results = alignment_service.ai_batch_align(
        source_namespace="grading_weak_point",
        source_terms=["等腰三角形的角度计算"],
        llm_client=mock_client
    )

    assert len(results) == 1
    assert results[0].source_value == "等腰三角形的角度计算"
    assert results[0].concept_id == concept.id
    assert results[0].sub_skill_tags == ("角度计算",)
    assert results[0].confidence == 0.85

    # Check that rule "仅从原始词本身推断子技能标签，绝不能从对齐的标准知识点过度发散" is in the prompt
    assert len(mock_client.prompts) == 1
    prompt_text = mock_client.prompts[0]
    assert "仅从原始词" in prompt_text
    assert "绝不能从对齐的标准知识点过度发散" in prompt_text
