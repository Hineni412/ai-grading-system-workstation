"""Knowledge identity and relation contracts for the Phase 4 graph."""

from question_bank.relations.contracts import (
    KnowledgeIdentity,
    KnowledgeRelation,
    RelationConflict,
    RelationStatus,
    RelationType,
    canonical_relation_key,
    find_confirmation_conflicts,
    normalize_stable_key,
)

__all__ = [
    "KnowledgeIdentity",
    "KnowledgeRelation",
    "RelationConflict",
    "RelationStatus",
    "RelationType",
    "canonical_relation_key",
    "find_confirmation_conflicts",
    "normalize_stable_key",
]
