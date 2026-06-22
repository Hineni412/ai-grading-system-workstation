from __future__ import annotations

from question_bank.models.skill_catalog import SkillResolutionRequest


class _JsonClient:
    def json_from_text(self, prompt: str):
        assert "角平分线性质" in prompt
        return {
            "candidates": [
                {"skill_id": 7, "confidence": 0.96, "reason": "题干直接考查"},
                {"skill_id": 999, "confidence": 0.99, "reason": "伪造候选"},
            ],
            "proposed_local_name": "",
            "proposed_topic_key": "",
            "proposed_aliases": [],
            "local_confidence": 0,
            "ambiguity_flags": [],
        }


def test_llm_ranker_accepts_only_supplied_candidate_ids() -> None:
    from question_bank.services.skill_context_ranker import LLMSkillContextRanker

    request = SkillResolutionRequest(
        source_type="assessment_item",
        source_ref="Q1",
        raw_label="角平分线计算",
        question_text="利用角平分线性质求角度",
        grade="八年级",
    )
    candidates = [
        {
            "id": 7,
            "stable_key": "math.geometry.line_angle.bisector",
            "name": "角平分线性质",
            "topic_name": "线与角",
            "grade_min": 7,
            "grade_max": 9,
        }
    ]

    ranking = LLMSkillContextRanker(_JsonClient()).rank(request, candidates)

    assert [candidate.skill_id for candidate in ranking.candidates] == [7]
    assert ranking.candidates[0].confidence == 0.96


def test_ai_tagging_service_exposes_reusable_skill_ranker() -> None:
    from question_bank.services.ai_tagging_service import AITaggingService
    from question_bank.services.skill_context_ranker import LLMSkillContextRanker

    client = _JsonClient()
    service = AITaggingService(env={}, llm_client=client)

    ranker = service.build_skill_context_ranker()

    assert isinstance(ranker, LLMSkillContextRanker)
    assert ranker.json_client is client
