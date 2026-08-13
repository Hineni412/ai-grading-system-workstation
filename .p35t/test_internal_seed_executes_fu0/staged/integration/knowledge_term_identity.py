from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


_WHITESPACE = re.compile(r"\s+")
_LEADING_SEPARATORS = re.compile(r"^[\s·路:：|\-]+")


@dataclass(frozen=True, slots=True)
class GradingKnowledgeTerm:
    knowledge_id: str
    display_value: str
    source_value: str


def _clean(value: object) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    return _WHITESPACE.sub(" ", normalized).strip()


def build_grading_knowledge_term(
    knowledge_id: object,
    knowledge_label: object,
) -> GradingKnowledgeTerm:
    normalized_id = _clean(knowledge_id)
    display_value = _clean(knowledge_label) or normalized_id or "UNKNOWN"
    source_value = display_value
    if normalized_id and source_value.startswith(normalized_id):
        remainder = source_value[len(normalized_id) :]
        source_value = _LEADING_SEPARATORS.sub("", remainder).strip() or normalized_id
    return GradingKnowledgeTerm(
        knowledge_id=normalized_id,
        display_value=display_value,
        source_value=source_value or normalized_id or "UNKNOWN",
    )


__all__ = ["GradingKnowledgeTerm", "build_grading_knowledge_term"]
