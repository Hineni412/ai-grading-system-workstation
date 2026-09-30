from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

_CANONICAL_KEY = re.compile(r"^kp_[a-z0-9]+(?:_[a-z0-9]+)*$")
_SKILL_KEY = re.compile(r"^sk_[a-z0-9]+(?:_[a-z0-9]+)*$")
_LOCAL_KEY = re.compile(r"^ki_[0-9a-f]{32}$")


class RelationType(StrEnum):
    """Teacher-governed relation meanings and their stored directions."""

    PARENT = "parent"
    PREREQUISITE = "prerequisite"
    RELATED = "related"


class RelationStatus(StrEnum):
    """Lifecycle states; only confirmed relations may enter the active graph."""

    SUGGESTED = "suggested"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    RETIRED = "retired"


class RelationConflict(StrEnum):
    SELF_RELATION = "self_relation"
    DUPLICATE = "duplicate"
    REVERSE_CONFLICT = "reverse_conflict"
    TYPE_CONFLICT = "type_conflict"
    PARENT_CYCLE = "parent_cycle"
    PREREQUISITE_CYCLE = "prerequisite_cycle"


def normalize_stable_key(value: str) -> str:
    """Normalize a governed canonical ID or validate an opaque local ID."""

    key = str(value or "").strip().casefold()
    if (
        _CANONICAL_KEY.fullmatch(key)
        or _SKILL_KEY.fullmatch(key)
        or _LOCAL_KEY.fullmatch(key)
    ):
        return key
    raise ValueError(
        "knowledge stable key must be a governed kp_*/sk_* or opaque ki_* ID"
    )


@dataclass(frozen=True, slots=True)
class KnowledgeIdentity:
    stable_key: str
    display_name: str
    aliases: tuple[str, ...] = ()
    status: str = "active"

    def __post_init__(self) -> None:
        object.__setattr__(self, "stable_key", normalize_stable_key(self.stable_key))
        display_name = str(self.display_name or "").strip()
        if not display_name:
            raise ValueError("knowledge display name is required")
        object.__setattr__(self, "display_name", display_name)
        if self.status not in {"active", "retired"}:
            raise ValueError("knowledge identity status must be active or retired")
        normalized_aliases: list[str] = []
        for raw_alias in self.aliases:
            alias = str(raw_alias or "").strip()
            if alias and alias != display_name and alias not in normalized_aliases:
                normalized_aliases.append(alias)
        object.__setattr__(self, "aliases", tuple(normalized_aliases))


@dataclass(frozen=True, slots=True)
class KnowledgeRelation:
    source_key: str
    target_key: str
    relation_type: RelationType
    status: RelationStatus = RelationStatus.SUGGESTED

    def __post_init__(self) -> None:
        source_key = normalize_stable_key(self.source_key)
        target_key = normalize_stable_key(self.target_key)
        relation_type = RelationType(self.relation_type)
        status = RelationStatus(self.status)
        if relation_type is RelationType.RELATED and source_key > target_key:
            source_key, target_key = target_key, source_key
        object.__setattr__(self, "source_key", source_key)
        object.__setattr__(self, "target_key", target_key)
        object.__setattr__(self, "relation_type", relation_type)
        object.__setattr__(self, "status", status)


def canonical_relation_key(
    source_key: str,
    target_key: str,
    relation_type: RelationType | str,
) -> tuple[str, str, RelationType]:
    relation = KnowledgeRelation(
        source_key=source_key,
        target_key=target_key,
        relation_type=RelationType(relation_type),
    )
    return relation.source_key, relation.target_key, relation.relation_type


def find_confirmation_conflicts(
    candidate: KnowledgeRelation,
    confirmed_relations: Iterable[KnowledgeRelation],
) -> tuple[RelationConflict, ...]:
    """
    Return deterministic reasons why a candidate cannot become active.

    ``parent`` is stored child -> parent. ``prerequisite`` is stored
    target -> prerequisite. ``related`` is stored as a normalized unordered
    pair. A knowledge pair has one active semantic relation in the first
    release; changing its meaning retires the old relation first.
    """

    conflicts: list[RelationConflict] = []
    if candidate.source_key == candidate.target_key:
        conflicts.append(RelationConflict.SELF_RELATION)
        return tuple(conflicts)

    active = tuple(
        relation
        for relation in confirmed_relations
        if relation.status is RelationStatus.CONFIRMED
    )
    candidate_pair = frozenset((candidate.source_key, candidate.target_key))
    for relation in active:
        relation_pair = frozenset((relation.source_key, relation.target_key))
        if relation_pair != candidate_pair:
            continue
        if (
            relation.relation_type is candidate.relation_type
            and relation.source_key == candidate.source_key
            and relation.target_key == candidate.target_key
        ):
            _append_once(conflicts, RelationConflict.DUPLICATE)
        elif relation.relation_type is candidate.relation_type:
            _append_once(conflicts, RelationConflict.REVERSE_CONFLICT)
        else:
            _append_once(conflicts, RelationConflict.TYPE_CONFLICT)

    if (
        not conflicts
        and candidate.relation_type
        in {RelationType.PARENT, RelationType.PREREQUISITE}
        and _would_create_cycle(candidate, active)
    ):
        cycle_conflict = (
            RelationConflict.PARENT_CYCLE
            if candidate.relation_type is RelationType.PARENT
            else RelationConflict.PREREQUISITE_CYCLE
        )
        _append_once(conflicts, cycle_conflict)
    return tuple(conflicts)


def _would_create_cycle(
    candidate: KnowledgeRelation,
    active: Iterable[KnowledgeRelation],
) -> bool:
    adjacency: dict[str, set[str]] = {}
    for relation in active:
        if relation.relation_type is candidate.relation_type:
            adjacency.setdefault(relation.source_key, set()).add(relation.target_key)
    adjacency.setdefault(candidate.source_key, set()).add(candidate.target_key)

    pending = [candidate.target_key]
    visited: set[str] = set()
    while pending:
        current = pending.pop()
        if current == candidate.source_key:
            return True
        if current in visited:
            continue
        visited.add(current)
        pending.extend(adjacency.get(current, ()))
    return False


def _append_once(
    values: list[RelationConflict],
    value: RelationConflict,
) -> None:
    if value not in values:
        values.append(value)
