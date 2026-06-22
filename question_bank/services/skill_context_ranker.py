from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from question_bank.models.skill_catalog import (
    RankedSkillCandidate,
    SkillResolutionRequest,
    normalize_confidence,
    normalize_display_text,
    normalize_stable_key,
    normalize_text_values,
)


@dataclass(frozen=True, slots=True)
class ContextRanking:
    candidates: tuple[RankedSkillCandidate, ...] = ()
    proposed_local_name: str = ""
    proposed_topic_key: str = ""
    proposed_aliases: tuple[str, ...] = ()
    local_confidence: float = 0.0
    ambiguity_flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        candidates: list[RankedSkillCandidate] = []
        for item in self.candidates or ():
            if isinstance(item, RankedSkillCandidate):
                candidate = item
            elif isinstance(item, Mapping):
                candidate = RankedSkillCandidate(
                    skill_id=int(item.get("skill_id") or 0),
                    confidence=item.get("confidence") or 0.0,
                    reason=normalize_display_text(item.get("reason")),
                )
            else:
                raise TypeError("context candidate must be a mapping or RankedSkillCandidate")
            candidates.append(candidate)
        candidates.sort(key=lambda item: (-item.confidence, item.skill_id))
        object.__setattr__(self, "candidates", tuple(candidates))
        object.__setattr__(self, "proposed_local_name", normalize_display_text(self.proposed_local_name))
        object.__setattr__(self, "proposed_topic_key", normalize_stable_key(self.proposed_topic_key))
        object.__setattr__(self, "proposed_aliases", normalize_text_values(self.proposed_aliases))
        object.__setattr__(self, "local_confidence", normalize_confidence(self.local_confidence))
        object.__setattr__(self, "ambiguity_flags", normalize_text_values(self.ambiguity_flags))


class SkillContextRanker(Protocol):
    def rank(
        self,
        request: SkillResolutionRequest,
        candidates: Sequence[Mapping[str, object]],
    ) -> ContextRanking:
        ...


class LLMSkillContextRanker:
    def __init__(self, json_client: Any) -> None:
        self.json_client = json_client

    def rank(
        self,
        request: SkillResolutionRequest,
        candidates: Sequence[Mapping[str, object]],
    ) -> ContextRanking:
        allowed_ids = {int(item["id"]) for item in candidates}
        prompt = _ranking_prompt(request, candidates)
        payload = self.json_client.json_from_text(prompt)
        if not isinstance(payload, Mapping):
            raise ValueError("skill ranker response must be a JSON object")
        ranked: list[RankedSkillCandidate] = []
        raw_candidates = payload.get("candidates") or []
        if isinstance(raw_candidates, list):
            for item in raw_candidates:
                if not isinstance(item, Mapping):
                    continue
                try:
                    skill_id = int(item.get("skill_id") or 0)
                except (TypeError, ValueError):
                    continue
                if skill_id not in allowed_ids:
                    continue
                ranked.append(
                    RankedSkillCandidate(
                        skill_id=skill_id,
                        confidence=item.get("confidence") or 0.0,
                        reason=normalize_display_text(item.get("reason")),
                    )
                )
        return ContextRanking(
            candidates=tuple(ranked),
            proposed_local_name=normalize_display_text(payload.get("proposed_local_name")),
            proposed_topic_key=normalize_stable_key(payload.get("proposed_topic_key")),
            proposed_aliases=normalize_text_values(payload.get("proposed_aliases")),
            local_confidence=payload.get("local_confidence") or 0.0,
            ambiguity_flags=normalize_text_values(payload.get("ambiguity_flags")),
        )


def _ranking_prompt(
    request: SkillResolutionRequest,
    candidates: Sequence[Mapping[str, object]],
) -> str:
    candidate_rows = [
        {
            "skill_id": int(item["id"]),
            "name": item.get("name"),
            "topic": item.get("topic_name"),
            "grade_min": item.get("grade_min"),
            "grade_max": item.get("grade_max"),
        }
        for item in candidates
    ]
    context = {
        "raw_label": request.raw_label,
        "grade": request.grade,
        "topic_hint": request.topic_hint,
        "question_text": request.question_text,
        "answer_text": request.answer_text,
        "rubric_text": request.rubric_text,
        "existing_tags": list(request.existing_tags),
    }
    return (
        "你是初中数学具体技能归一器。只能从候选列表返回 skill_id，不能发明编号。"
        "技能必须具体到可直接训练的数学对象与动作；存在歧义时写入 ambiguity_flags。"
        "只有候选均不等价且原名称足够具体时，才提出一个本校技能。"
        "返回 JSON 对象，字段为 candidates、proposed_local_name、proposed_topic_key、"
        "proposed_aliases、local_confidence、ambiguity_flags。\n"
        f"上下文：{json.dumps(context, ensure_ascii=False)}\n"
        f"候选：{json.dumps(candidate_rows, ensure_ascii=False)}"
    )
