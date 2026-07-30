from __future__ import annotations

import hashlib
import json
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from integration.skill_graph_projection import (
    build_question_tag_graph_evidence,
    build_question_tag_graph_nodes,
    build_question_tag_graph_rows,
)
from question_bank.relations.repository import (
    ActiveKnowledgeRelation,
    KnowledgeIdentityRecord,
    KnowledgeRelationRepository,
)
from question_bank.taxonomy.registry import canonicalize_knowledge_exact


@dataclass(frozen=True, slots=True)
class GraphV2Query:
    knowledge_keys: tuple[str, ...] = ()
    prerequisite_depth: int = 1

    def __post_init__(self) -> None:
        keys = tuple(
            dict.fromkeys(
                str(value or "").strip().casefold()
                for value in self.knowledge_keys
                if str(value or "").strip()
            )
        )
        depth = int(self.prerequisite_depth)
        if depth < 0 or depth > 5:
            raise ValueError("prerequisite_depth must be between 0 and 5")
        object.__setattr__(self, "knowledge_keys", keys)
        object.__setattr__(self, "prerequisite_depth", depth)


class KnowledgeGraphV2QueryService:
    """Join scoped v1 evidence to the teacher-confirmed stable relation graph."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.repository = KnowledgeRelationRepository(self.db_path)

    def query(
        self,
        profile: Mapping[str, Any],
        query: GraphV2Query,
    ) -> dict[str, object]:
        cache = _RequestGraphCache(self.repository)
        identities = cache.identities()
        active_relations = cache.active_relations()
        identity_by_key = {
            identity.stable_key: identity for identity in identities
        }
        remapped_rows, unmapped_counts = _stable_evidence_rows(profile)
        evidence_nodes = {
            str(node["knowledge_key"]): node
            for node in build_question_tag_graph_nodes(remapped_rows)
        }

        missing: list[dict[str, object]] = [
            {
                "kind": "ungoverned_knowledge_label",
                "label": label,
                "count": count,
            }
            for label, count in sorted(unmapped_counts.items())
        ]
        if query.knowledge_keys:
            seed_keys: set[str] = set()
            for key in query.knowledge_keys:
                if key not in identity_by_key:
                    missing.append(
                        {
                            "kind": "requested_identity_not_found",
                            "stable_key": key,
                            "count": 1,
                        }
                    )
                    continue
                seed_keys.add(key)
        else:
            seed_keys = {
                key for key in evidence_nodes if key in identity_by_key
            }

        included_keys = _expand_prerequisites(
            seed_keys,
            active_relations,
            depth=query.prerequisite_depth,
        )
        edges = [
            _edge_payload(relation)
            for relation in active_relations
            if relation.source_key in included_keys
            and relation.target_key in included_keys
        ]
        nodes = [
            _node_payload(
                identity_by_key[key],
                evidence_nodes.get(key),
            )
            for key in sorted(included_keys)
            if key in identity_by_key
        ]
        payload: dict[str, object] = {
            "response_schema_version": "knowledge-graph-v2",
            "scope": dict(profile.get("scope") or {}),
            "exam_scope": dict(profile.get("exam_scope") or {}),
            "coverage": dict(profile.get("coverage") or {}),
            "nodes": nodes,
            "edges": edges,
            "missing": missing,
            "warnings": [
                *(
                    str(value)
                    for value in profile.get("warnings", [])
                    if str(value or "").strip()
                ),
                "掌握度 v2 尚未启用，当前并列字段显示为不可用。",
            ],
            "counts": {
                "node_count": len(nodes),
                "edge_count": len(edges),
                "evidence_row_count": len(remapped_rows),
                "missing_count": sum(
                    int(item.get("count") or 0) for item in missing
                ),
            },
        }
        payload["response_version"] = _payload_version(payload)
        return payload

    def evidence(
        self,
        profile: Mapping[str, Any],
        *,
        stable_key: str,
        page: int,
        page_size: int,
    ) -> dict[str, object]:
        key = str(stable_key or "").strip().casefold()
        normalized_page = int(page)
        normalized_page_size = int(page_size)
        if normalized_page < 1:
            raise ValueError("page must be positive")
        if normalized_page_size < 1 or normalized_page_size > 100:
            raise ValueError("page_size must be between 1 and 100")
        identity_by_key = {
            identity.stable_key: identity
            for identity in self.repository.list_identities()
        }
        identity = identity_by_key.get(key)
        if identity is None:
            raise KeyError(key)
        legacy_keys = _legacy_profile_keys(profile, key)
        items: list[dict[str, Any]] = []
        seen: set[tuple[object, ...]] = set()
        for legacy_key in legacy_keys:
            for item in build_question_tag_graph_evidence(
                profile,
                legacy_key,
            ):
                identity_key = (
                    item.get("student_id"),
                    item.get("session_id"),
                    item.get("question_id"),
                    item.get("bank_question_id"),
                )
                if identity_key in seen:
                    continue
                seen.add(identity_key)
                items.append(
                    {
                        **item,
                        "stable_key": key,
                        "knowledge_label": identity.display_name,
                    }
                )
        items.sort(
            key=lambda item: (
                int(item.get("session_id") or 0),
                int(item.get("student_id") or 0),
                str(item.get("question_id") or ""),
                int(item.get("bank_question_id") or 0),
            )
        )
        total = len(items)
        start = (normalized_page - 1) * normalized_page_size
        payload: dict[str, object] = {
            "response_schema_version": "knowledge-graph-evidence-v2",
            "scope": dict(profile.get("scope") or {}),
            "exam_scope": dict(profile.get("exam_scope") or {}),
            "coverage": dict(profile.get("coverage") or {}),
            "stable_key": key,
            "display_name": identity.display_name,
            "items": items[start : start + normalized_page_size],
            "total": total,
            "page": normalized_page,
            "page_size": normalized_page_size,
            "total_pages": max(
                1,
                (total + normalized_page_size - 1)
                // normalized_page_size,
            ),
        }
        payload["response_version"] = _payload_version(payload)
        return payload


class _RequestGraphCache:
    def __init__(self, repository: KnowledgeRelationRepository) -> None:
        self.repository = repository
        self._identities: tuple[KnowledgeIdentityRecord, ...] | None = None
        self._relations: tuple[ActiveKnowledgeRelation, ...] | None = None

    def identities(self) -> tuple[KnowledgeIdentityRecord, ...]:
        if self._identities is None:
            self._identities = self.repository.list_identities(
                status="active"
            )
        return self._identities

    def active_relations(self) -> tuple[ActiveKnowledgeRelation, ...]:
        if self._relations is None:
            self._relations = self.repository.list_active_relations()
        return self._relations


def _stable_evidence_rows(
    profile: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    remapped: list[dict[str, Any]] = []
    missing: dict[str, int] = {}
    for row in build_question_tag_graph_rows(profile):
        label = str(row.get("knowledge_label") or "").strip()
        canonical = canonicalize_knowledge_exact(label)
        if canonical is None:
            missing[label] = missing.get(label, 0) + 1
            continue
        remapped.append(
            {
                **row,
                "knowledge_key": canonical.canonical_id.casefold(),
                "knowledge_label": canonical.canonical_name,
            }
        )
    return remapped, missing


def _legacy_profile_keys(
    profile: Mapping[str, Any],
    stable_key: str,
) -> tuple[str, ...]:
    keys: list[str] = []
    students = profile.get("students")
    if not isinstance(students, list):
        return ()
    for student in students:
        if not isinstance(student, Mapping):
            continue
        weak_points = student.get("weak_points")
        if not isinstance(weak_points, list):
            continue
        for weak_point in weak_points:
            if not isinstance(weak_point, Mapping):
                continue
            label = str(weak_point.get("knowledge_point") or "").strip()
            canonical = canonicalize_knowledge_exact(label)
            if (
                canonical is None
                or canonical.canonical_id.casefold() != stable_key
            ):
                continue
            legacy_key = str(
                weak_point.get("knowledge_key")
                or f"knowledge_point:{label}"
            ).strip()
            if legacy_key and legacy_key not in keys:
                keys.append(legacy_key)
    return tuple(keys)


def _expand_prerequisites(
    seeds: set[str],
    relations: Sequence[ActiveKnowledgeRelation],
    *,
    depth: int,
) -> set[str]:
    included = set(seeds)
    adjacency: dict[str, set[str]] = {}
    for relation in relations:
        if relation.relation_type.value == "prerequisite":
            adjacency.setdefault(relation.source_key, set()).add(
                relation.target_key
            )
    queue = deque((key, 0) for key in sorted(seeds))
    visited_depth: dict[str, int] = {key: 0 for key in seeds}
    while queue:
        current, current_depth = queue.popleft()
        if current_depth >= depth:
            continue
        for target in sorted(adjacency.get(current, ())):
            next_depth = current_depth + 1
            included.add(target)
            previous = visited_depth.get(target)
            if previous is not None and previous <= next_depth:
                continue
            visited_depth[target] = next_depth
            queue.append((target, next_depth))
    return included


def _node_payload(
    identity: KnowledgeIdentityRecord,
    evidence: Mapping[str, Any] | None,
) -> dict[str, object]:
    if evidence is None:
        mastery_v1 = {
            "status": "missing",
            "value": None,
            "evidence_count": 0,
        }
        evidence_summary = {
            "student_count": 0,
            "item_count": 0,
            "deduction_count": 0,
            "tag_context": {},
            "error_counts": {"primary": {}, "secondary": {}},
        }
        missing_reasons = ["no_evidence_in_scope"]
    else:
        mastery_v1 = {
            "status": "available",
            "value": float(evidence.get("average_mastery") or 0.0),
            "evidence_count": int(evidence.get("item_count") or 0),
        }
        evidence_summary = {
            "student_count": int(evidence.get("student_count") or 0),
            "item_count": int(evidence.get("item_count") or 0),
            "deduction_count": int(
                evidence.get("deduction_count") or 0
            ),
            "tag_context": dict(evidence.get("tag_context") or {}),
            "error_counts": dict(evidence.get("error_counts") or {}),
        }
        missing_reasons = []
    return {
        "stable_key": identity.stable_key,
        "display_name": identity.display_name,
        "identity_revision": identity.revision,
        "mastery_v1": mastery_v1,
        "mastery_v2": {
            "status": "unavailable",
            "value": None,
            "evidence_count": 0,
            "reason": "mastery_v2_not_enabled",
        },
        "evidence": evidence_summary,
        "missing_reasons": missing_reasons,
    }


def _edge_payload(relation: ActiveKnowledgeRelation) -> dict[str, object]:
    return {
        "relation_id": relation.relation_id,
        "source_key": relation.source_key,
        "target_key": relation.target_key,
        "relation_type": relation.relation_type.value,
        "rationale": relation.rationale,
        "revision": relation.revision,
    }


def _payload_version(payload: Mapping[str, object]) -> str:
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


__all__ = [
    "GraphV2Query",
    "KnowledgeGraphV2QueryService",
]
