from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


_WHITESPACE = re.compile(r"\s+")


class SkillRole(str, Enum):
    MEASURED = "measured"
    SUPPORTING = "supporting"


class ResolutionOutcome(str, Enum):
    RESOLVED_EXISTING = "resolved_existing"
    CREATED_LOCAL = "created_local"
    CONFLICT = "conflict"


class SkillOrigin(str, Enum):
    BUILTIN = "builtin"
    LOCAL = "local"


class SkillSourceType(str, Enum):
    ASSESSMENT_ITEM = "assessment_item"
    QUESTION_BANK_ITEM = "question_bank_item"
    LEGACY_TERM = "legacy_term"


@dataclass(frozen=True, slots=True)
class SkillResolutionRequest:
    source_type: SkillSourceType
    source_ref: str
    raw_label: str
    stable_key_hint: str = ""
    grade: str = ""
    topic_hint: str = ""
    question_text: str = ""
    answer_text: str = ""
    rubric_text: str = ""
    existing_tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        source_type = (
            self.source_type
            if isinstance(self.source_type, SkillSourceType)
            else SkillSourceType(str(self.source_type))
        )
        object.__setattr__(self, "source_type", source_type)
        for name in (
            "source_ref",
            "raw_label",
            "grade",
            "topic_hint",
            "question_text",
            "answer_text",
            "rubric_text",
        ):
            object.__setattr__(self, name, normalize_display_text(getattr(self, name)))
        object.__setattr__(self, "stable_key_hint", normalize_stable_key(self.stable_key_hint))
        object.__setattr__(self, "existing_tags", normalize_text_values(self.existing_tags))


@dataclass(frozen=True, slots=True)
class RankedSkillCandidate:
    skill_id: int
    confidence: float
    reason: str

    def __post_init__(self) -> None:
        skill_id = int(self.skill_id)
        if skill_id <= 0:
            raise ValueError("skill_id must be positive")
        object.__setattr__(self, "skill_id", skill_id)
        object.__setattr__(self, "confidence", normalize_confidence(self.confidence))
        object.__setattr__(self, "reason", normalize_display_text(self.reason))


@dataclass(frozen=True, slots=True)
class SkillResolution:
    outcome: ResolutionOutcome
    skill_id: int | None
    confidence: float
    reason: str
    candidates: tuple[RankedSkillCandidate, ...] = ()

    def __post_init__(self) -> None:
        outcome = (
            self.outcome
            if isinstance(self.outcome, ResolutionOutcome)
            else ResolutionOutcome(str(self.outcome))
        )
        object.__setattr__(self, "outcome", outcome)
        object.__setattr__(self, "reason", normalize_display_text(self.reason))
        object.__setattr__(self, "candidates", tuple(self.candidates or ()))
        if outcome is ResolutionOutcome.CONFLICT:
            object.__setattr__(self, "skill_id", None)
            object.__setattr__(self, "confidence", 0.0)
            return
        if self.skill_id is None or int(self.skill_id) <= 0:
            raise ValueError("non-conflict resolution requires a positive skill_id")
        object.__setattr__(self, "skill_id", int(self.skill_id))
        object.__setattr__(self, "confidence", normalize_confidence(self.confidence))


@dataclass(frozen=True, slots=True)
class ResolvedSkillLink:
    skill_id: int
    role: SkillRole
    raw_knowledge_id: str = ""
    raw_knowledge_label: str = ""
    source: str = "resolver"
    confidence: float = 1.0
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        skill_id = int(self.skill_id)
        if skill_id <= 0:
            raise ValueError("skill_id must be positive")
        role = self.role if isinstance(self.role, SkillRole) else SkillRole(str(self.role))
        object.__setattr__(self, "skill_id", skill_id)
        object.__setattr__(self, "role", role)
        object.__setattr__(self, "raw_knowledge_id", normalize_display_text(self.raw_knowledge_id))
        object.__setattr__(self, "raw_knowledge_label", normalize_display_text(self.raw_knowledge_label))
        object.__setattr__(self, "source", normalize_display_text(self.source) or "resolver")
        object.__setattr__(self, "confidence", normalize_confidence(self.confidence))
        object.__setattr__(self, "evidence", dict(self.evidence or {}))


def normalize_display_text(value: object) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    return _WHITESPACE.sub(" ", normalized).strip()


def normalize_stable_key(value: object) -> str:
    return normalize_display_text(value).casefold()


def normalize_text_values(values: object) -> tuple[str, ...]:
    if values in (None, ""):
        return ()
    if isinstance(values, str):
        raw_values = (values,)
    else:
        try:
            raw_values = tuple(values)  # type: ignore[arg-type]
        except TypeError:
            raw_values = (values,)
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        text = normalize_display_text(raw)
        key = text.casefold()
        if not text or key in seen:
            continue
        normalized.append(text)
        seen.add(key)
    return tuple(normalized)


def normalize_confidence(value: object) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    return round(min(1.0, max(0.0, number)), 4)
