from __future__ import annotations

from question_bank.models.knowledge_alignment import (
    AlignmentStatus,
    KnowledgeConcept,
    KnowledgeSourceMapping,
    normalize_source_value,
)


def test_normalize_source_value_is_stable() -> None:
    assert normalize_source_value(" 二次函数　图像 ") == "二次函数 图像"
    assert normalize_source_value("  QUADRATIC   Function ") == "quadratic function"


def test_concept_normalizes_aliases_without_losing_display_name() -> None:
    concept = KnowledgeConcept(
        id=7,
        canonical_key=" Math.Quadratic ",
        name=" 二次函数 ",
        aliases=(" 抛物线 ", "抛物线", "Quadratic Function"),
    )

    assert concept.canonical_key == "math.quadratic"
    assert concept.name == "二次函数"
    assert concept.aliases == ("抛物线", "Quadratic Function")


def test_confirmed_mapping_requires_full_confidence() -> None:
    mapping = KnowledgeSourceMapping(
        source_namespace=" grading_weak_point ",
        source_value=" 二次函数　图像 ",
        concept_id=7,
        status=AlignmentStatus.CONFIRMED,
        confidence=0.75,
    )

    assert mapping.source_namespace == "grading_weak_point"
    assert mapping.source_value == "二次函数 图像"
    assert mapping.normalized_value == "二次函数 图像"
    assert mapping.confidence == 1.0
    assert mapping.eligible_for_recommendation is True


def test_suggested_mapping_is_clamped_and_not_eligible() -> None:
    mapping = KnowledgeSourceMapping(
        source_namespace="question_tag",
        source_value="QUADRATIC Function",
        concept_id=7,
        status=AlignmentStatus.SUGGESTED,
        confidence=2.0,
    )

    assert mapping.normalized_value == "quadratic function"
    assert mapping.confidence == 0.99
    assert mapping.eligible_for_recommendation is False


def test_unmapped_mapping_has_no_concept_or_confidence() -> None:
    mapping = KnowledgeSourceMapping(
        source_namespace="grading_weak_point",
        source_value="陌生诊断词",
        concept_id=7,
        status=AlignmentStatus.UNMAPPED,
        confidence=0.8,
    )

    assert mapping.concept_id is None
    assert mapping.confidence == 0.0
