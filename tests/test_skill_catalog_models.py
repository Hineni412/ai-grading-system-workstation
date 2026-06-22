from __future__ import annotations

import importlib

import pytest


def _models():
    return importlib.import_module("question_bank.models.skill_catalog")


def test_resolution_request_normalizes_identity_and_context() -> None:
    models = _models()

    request = models.SkillResolutionRequest(
        source_type="assessment_item",
        source_ref="  Q1　.1  ",
        raw_label="  角平分线　性质  ",
        grade=" 七年级 ",
        existing_tags=(" 几何 ", "几何", ""),
    )

    assert request.source_type is models.SkillSourceType.ASSESSMENT_ITEM
    assert request.source_ref == "Q1 .1"
    assert request.raw_label == "角平分线 性质"
    assert request.grade == "七年级"
    assert request.existing_tags == ("几何",)


def test_ranked_candidate_clamps_confidence() -> None:
    models = _models()

    high = models.RankedSkillCandidate(skill_id=7, confidence=3, reason="命中")
    low = models.RankedSkillCandidate(skill_id=8, confidence=-1, reason="备选")

    assert high.confidence == 1.0
    assert low.confidence == 0.0


def test_non_conflict_resolution_requires_skill_id() -> None:
    models = _models()

    with pytest.raises(ValueError, match="skill_id"):
        models.SkillResolution(
            outcome=models.ResolutionOutcome.RESOLVED_EXISTING,
            skill_id=None,
            confidence=0.95,
            reason="精确名称",
        )


def test_conflict_resolution_clears_skill_and_confidence() -> None:
    models = _models()

    resolution = models.SkillResolution(
        outcome="conflict",
        skill_id=99,
        confidence=0.8,
        reason="候选接近",
    )

    assert resolution.outcome is models.ResolutionOutcome.CONFLICT
    assert resolution.skill_id is None
    assert resolution.confidence == 0.0


def test_resolved_skill_link_normalizes_role_and_evidence() -> None:
    models = _models()

    link = models.ResolvedSkillLink(
        skill_id=9,
        role="measured",
        raw_knowledge_id=" G7_15 ",
        raw_knowledge_label=" 角平分线性质 ",
        source=" ai ",
        confidence=1.4,
        evidence={"kind": "rubric"},
    )

    assert link.role is models.SkillRole.MEASURED
    assert link.raw_knowledge_id == "G7_15"
    assert link.raw_knowledge_label == "角平分线性质"
    assert link.source == "ai"
    assert link.confidence == 1.0
    assert link.evidence == {"kind": "rubric"}
