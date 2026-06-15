from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum


WHITESPACE_PATTERN = re.compile(r"\s+")


class AlignmentStatus(str, Enum):
    CONFIRMED = "confirmed"
    SUGGESTED = "suggested"
    REJECTED = "rejected"
    UNMAPPED = "unmapped"


def normalize_source_value(value: object) -> str:
    return _clean_display_value(value).casefold()


@dataclass(frozen=True)
class KnowledgeConcept:
    id: int
    canonical_key: str
    name: str
    aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "canonical_key", normalize_source_value(self.canonical_key))
        object.__setattr__(self, "name", _clean_display_value(self.name))

        aliases: list[str] = []
        seen: set[str] = set()
        for raw_alias in self.aliases:
            alias = _clean_display_value(raw_alias)
            normalized_alias = normalize_source_value(alias)
            if not alias or normalized_alias in seen:
                continue
            aliases.append(alias)
            seen.add(normalized_alias)
        object.__setattr__(self, "aliases", tuple(aliases))


@dataclass(frozen=True)
class KnowledgeSourceMapping:
    source_namespace: str
    source_value: str
    concept_id: int | None
    status: AlignmentStatus
    confidence: float
    sub_skill_tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        status = self.status if isinstance(self.status, AlignmentStatus) else AlignmentStatus(self.status)
        concept_id = self.concept_id
        confidence = _confidence(self.confidence)
        if status is AlignmentStatus.CONFIRMED:
            if concept_id is None:
                raise ValueError("confirmed mapping requires a concept_id")
            confidence = 1.0
        elif status is AlignmentStatus.SUGGESTED:
            confidence = min(confidence, 0.99)
        elif status is AlignmentStatus.UNMAPPED:
            concept_id = None
            confidence = 0.0

        object.__setattr__(self, "source_namespace", normalize_source_value(self.source_namespace))
        object.__setattr__(self, "source_value", _clean_display_value(self.source_value))
        object.__setattr__(self, "concept_id", concept_id)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "confidence", confidence)

        sub_skills: list[str] = []
        seen_skills: set[str] = set()
        for s in (self.sub_skill_tags or ()):
            s_clean = _clean_display_value(s)
            if s_clean and s_clean not in seen_skills:
                sub_skills.append(s_clean)
                seen_skills.add(s_clean)
        object.__setattr__(self, "sub_skill_tags", tuple(sub_skills))

    @property
    def normalized_value(self) -> str:
        return normalize_source_value(self.source_value)

    @property
    def eligible_for_recommendation(self) -> bool:
        return self.status is AlignmentStatus.CONFIRMED and self.concept_id is not None


def _clean_display_value(value: object) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    return WHITESPACE_PATTERN.sub(" ", normalized).strip()


def _confidence(value: object) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        confidence = 0.0
    return round(min(1.0, max(0.0, confidence)), 4)
