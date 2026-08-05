from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from integration.skill_graph_projection import (
    build_question_tag_graph_evidence,
    build_question_tag_graph_nodes,
    build_question_tag_graph_rows,
)
from question_bank.current_knowledge import (
    CurrentKnowledgeRelation,
    CurrentKnowledgeResolver,
)
from question_bank.mastery.current import (
    CurrentMasteryCalculator,
    aggregate_current_mastery,
)


@dataclass(frozen=True, slots=True)
class CurrentGraphQuery:
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


class CurrentKnowledgeGraphQueryService:
    """Project scoped evidence through the one current knowledge standard."""

    def __init__(
        self,
        db_path: Path,
        *,
        resolver: CurrentKnowledgeResolver | None = None,
        mastery_calculator: CurrentMasteryCalculator | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.resolver = resolver or CurrentKnowledgeResolver.from_active_database(
            self.db_path
        )
        self.clock = clock or (lambda: datetime.now(UTC))
        self.mastery_calculator = mastery_calculator or CurrentMasteryCalculator(
            self.db_path,
            self.resolver,
            clock=self.clock,
        )

    def query(
        self,
        profile: Mapping[str, Any],
        query: CurrentGraphQuery,
    ) -> dict[str, object]:
        remapped_rows = _current_evidence_rows(profile, self.resolver)
        evidence_nodes = {
            str(node["knowledge_key"]): node
            for node in build_question_tag_graph_nodes(remapped_rows)
        }
        mastery_warning: str | None = None
        try:
            scope = profile.get("scope")
            if isinstance(scope, Mapping):
                allowed_student_ids = frozenset(
                    str(value) for value in scope.get("student_ids", [])
                )
            else:
                allowed_student_ids = frozenset(
                    str(student.get("student_id") or "")
                    for student in profile.get("students", [])
                    if isinstance(student, Mapping)
                    and str(student.get("student_id") or "").strip()
                )
            mastery_by_key = aggregate_current_mastery(
                self.mastery_calculator.calculate(
                    profile,
                    allowed_student_ids=allowed_student_ids,
                )
            )
        except (OSError, sqlite3.Error, TypeError, ValueError):
            mastery_by_key = {}
            mastery_warning = "当前掌握度参数或证据不可用。"

        node_by_key = {node.stable_key: node for node in self.resolver.nodes}
        missing: list[dict[str, object]] = []
        if query.knowledge_keys:
            seed_keys: set[str] = set()
            for key in query.knowledge_keys:
                if key not in node_by_key:
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
            seed_keys = (
                set(evidence_nodes) | set(mastery_by_key)
            ).intersection(node_by_key)

        included_keys = _expand_prerequisites(
            seed_keys,
            self.resolver.relations,
            depth=query.prerequisite_depth,
        )
        edges = [
            _edge_payload(relation)
            for relation in self.resolver.relations
            if relation.source_key in included_keys
            and relation.target_key in included_keys
        ]
        nodes = [
            _node_payload(
                node_by_key[key],
                evidence_nodes.get(key),
                mastery_by_key.get(key),
                mastery_available=mastery_warning is None,
            )
            for key in sorted(included_keys)
            if key in node_by_key
        ]
        payload: dict[str, object] = {
            "response_schema_version": "knowledge-graph-current",
            "scope": dict(profile.get("scope") or {}),
            "exam_scope": dict(profile.get("exam_scope") or {}),
            "coverage": dict(profile.get("coverage") or {}),
            "current_standard": {
                "release_id": self.resolver.release_id,
                "content_hash": self.resolver.content_hash,
                "taxonomy_revision": self.resolver.taxonomy_revision,
            },
            "nodes": nodes,
            "edges": edges,
            "missing": missing,
            "warnings": [
                *(
                    str(value)
                    for value in profile.get("warnings", [])
                    if str(value or "").strip()
                ),
                *([mastery_warning] if mastery_warning else []),
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
        node = self.resolver.node(key)
        if node is None:
            raise KeyError(key)

        items: list[dict[str, Any]] = []
        seen: set[tuple[object, ...]] = set()
        for legacy_key in _profile_keys_for_target(profile, key, self.resolver):
            for item in build_question_tag_graph_evidence(profile, legacy_key):
                identity = (
                    item.get("student_id"),
                    item.get("session_id"),
                    item.get("question_id"),
                    item.get("bank_question_id"),
                )
                if identity in seen:
                    continue
                seen.add(identity)
                items.append(
                    {
                        **item,
                        "stable_key": key,
                        "knowledge_key": key,
                        "knowledge_label": node.display_name,
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
            "response_schema_version": "knowledge-graph-evidence-current",
            "scope": dict(profile.get("scope") or {}),
            "exam_scope": dict(profile.get("exam_scope") or {}),
            "coverage": dict(profile.get("coverage") or {}),
            "current_standard": {
                "release_id": self.resolver.release_id,
                "content_hash": self.resolver.content_hash,
                "taxonomy_revision": self.resolver.taxonomy_revision,
            },
            "stable_key": key,
            "display_name": node.display_name,
            "items": items[start : start + normalized_page_size],
            "total": total,
            "page": normalized_page,
            "page_size": normalized_page_size,
            "total_pages": max(
                1,
                (total + normalized_page_size - 1) // normalized_page_size,
            ),
        }
        payload["response_version"] = _payload_version(payload)
        return payload


def _current_evidence_rows(
    profile: Mapping[str, Any],
    resolver: CurrentKnowledgeResolver,
) -> list[dict[str, Any]]:
    remapped: list[dict[str, Any]] = []
    seen: set[tuple[object, ...]] = set()
    for row in build_question_tag_graph_rows(profile):
        for target in resolver.resolve(row.get("knowledge_label")):
            identity = (
                row.get("student_id"),
                target.stable_key,
                tuple(
                    (
                        ref.get("session_id"),
                        ref.get("question_id"),
                        ref.get("bank_question_id"),
                    )
                    for ref in row.get("source_question_refs", [])
                    if isinstance(ref, Mapping)
                ),
            )
            if identity in seen:
                continue
            seen.add(identity)
            remapped.append(
                {
                    **row,
                    "knowledge_key": target.stable_key,
                    "knowledge_label": target.display_name,
                }
            )
    return remapped


def _profile_keys_for_target(
    profile: Mapping[str, Any],
    stable_key: str,
    resolver: CurrentKnowledgeResolver,
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
            resolved = resolver.resolve(
                weak_point.get("knowledge_point")
                or weak_point.get("knowledge_key")
            )
            if stable_key not in {item.stable_key for item in resolved}:
                continue
            legacy_key = str(
                weak_point.get("knowledge_key")
                or f"knowledge_point:{weak_point.get('knowledge_point') or ''}"
            ).strip()
            if legacy_key and legacy_key not in keys:
                keys.append(legacy_key)
    return tuple(keys)


def _expand_prerequisites(
    seeds: set[str],
    relations: Sequence[CurrentKnowledgeRelation],
    *,
    depth: int,
) -> set[str]:
    included = set(seeds)
    adjacency: dict[str, set[str]] = {}
    for relation in relations:
        if relation.relation_type == "prerequisite":
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
    node: Any,
    evidence: Mapping[str, Any] | None,
    mastery: Any | None,
    *,
    mastery_available: bool,
) -> dict[str, object]:
    mastery_evidence_count = int(
        getattr(mastery, "evidence_count", 0) or 0
    )
    mastery_student_count = int(
        getattr(mastery, "contributing_student_count", 0) or 0
    )
    has_mastery_evidence = (
        mastery_evidence_count > 0 and mastery_student_count > 0
    )
    evidence_summary = (
        {
            "student_count": (
                mastery_student_count if has_mastery_evidence else 0
            ),
            "item_count": (
                mastery_evidence_count if has_mastery_evidence else 0
            ),
            "deduction_count": 0,
            "tag_context": {},
            "error_counts": {"primary": {}, "secondary": {}},
        }
        if evidence is None
        else {
            "student_count": max(
                int(evidence.get("student_count") or 0),
                mastery_student_count,
            ),
            "item_count": max(
                int(evidence.get("item_count") or 0),
                mastery_evidence_count,
            ),
            "deduction_count": int(evidence.get("deduction_count") or 0),
            "tag_context": dict(evidence.get("tag_context") or {}),
            "error_counts": dict(evidence.get("error_counts") or {}),
        }
    )
    if not mastery_available:
        mastery_payload = {
            "status": "unavailable",
            "value": None,
            "evidence_count": 0,
            "parameter_version": None,
            "reason": "current_mastery_unavailable",
            "contributing_student_count": 0,
            "exam_evidence_count": 0,
            "training_evidence_count": 0,
        }
    elif mastery is None:
        mastery_payload = {
            "status": "missing",
            "value": None,
            "evidence_count": 0,
            "parameter_version": None,
            "reason": "current_mastery_evidence_missing",
            "contributing_student_count": 0,
            "exam_evidence_count": 0,
            "training_evidence_count": 0,
        }
    else:
        mastery_payload = mastery.to_dict()
    return {
        "stable_key": node.stable_key,
        "display_name": node.display_name,
        "definition": node.definition,
        "include_scope": node.include_scope,
        "exclude_scope": node.exclude_scope,
        "curriculum_anchors": list(node.curriculum_anchors),
        "observable_evidence": node.observable_evidence,
        "rationale": node.rationale,
        "evidence_source_ids": list(node.evidence_source_ids),
        "mastery": mastery_payload,
        "evidence": evidence_summary,
        "missing_reasons": (
            ["no_evidence_in_scope"]
            if evidence is None and not has_mastery_evidence else []
        ),
    }


def _edge_payload(relation: CurrentKnowledgeRelation) -> dict[str, object]:
    return asdict(relation)


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
    "CurrentGraphQuery",
    "CurrentKnowledgeGraphQueryService",
]
