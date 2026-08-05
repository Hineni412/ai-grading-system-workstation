from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from question_bank.knowledge_graph_release.contracts import (
    KnowledgeGraphRelease,
    SCHEMA_VERSION,
    ValidationIssue,
    ValidationReport,
)
from question_bank.relations.contracts import normalize_stable_key


_DISPOSITIONS = {
    "direct_core",
    "maps_to_core",
    "maps_to_many",
    "retrieval_only",
    "wrong_dimension",
    "retired",
}
_MAPPING_ROLES = {"primary", "secondary", "context_only"}
_NODE_KINDS = {"core", "structural", "legacy"}
_NODE_STATUSES = {"active", "retired"}
_RELATION_TYPES = {"parent", "prerequisite", "related"}
_BASIS_KINDS = {
    "mathematical_logic",
    "curriculum_structure",
    "multi_textbook_sequence",
    "teacher_judgment",
    "empirical_evidence",
}
_STRENGTHS = {"required", "recommended", "contextual"}
_REPLACEMENT_KINDS = {"exact", "broader", "narrower", "split"}


def validate_release(
    release: KnowledgeGraphRelease,
    taxonomy_catalog: Mapping[str, Any],
) -> ValidationReport:
    """Validate a release against the exact governed taxonomy snapshot."""

    issues: list[ValidationIssue] = []
    payload = release.payload
    if release.schema_version != SCHEMA_VERSION:
        _error(
            issues,
            "schema_version",
            "$.schema_version",
            f"must equal {SCHEMA_VERSION}",
        )
    if not release.release_id.startswith("kgr_"):
        _error(
            issues,
            "release_id",
            "$.release_id",
            "must use the kgr_ prefix",
        )

    catalog_revision = _integer(taxonomy_catalog.get("revision"), -1)
    if release.taxonomy_revision != catalog_revision:
        _error(
            issues,
            "taxonomy_revision_mismatch",
            "$.taxonomy_revision",
            "must match the governed taxonomy revision",
        )

    taxonomy_terms = {
        _text(term.get("id")): term
        for term in _objects(taxonomy_catalog.get("terms"))
        if _text(term.get("dimension")) == "knowledge"
        and _text(term.get("status")) == "approved"
        and _text(term.get("id"))
    }
    expected_term_count = _integer(
        taxonomy_catalog.get("expected_approved_knowledge_count"),
        294 if catalog_revision == 3 else -1,
    )
    if expected_term_count <= 0:
        _error(
            issues,
            "taxonomy_snapshot_size_missing",
            "$.taxonomy_revision",
            "taxonomy must declare expected_approved_knowledge_count",
        )
    elif len(taxonomy_terms) != expected_term_count:
        _error(
            issues,
            "taxonomy_snapshot_size",
            "$.taxonomy_revision",
            (
                f"expected {expected_term_count} approved knowledge terms, "
                f"found {len(taxonomy_terms)}"
            ),
        )

    sources = _validate_sources(payload.get("sources"), issues)
    nodes = _validate_nodes(payload.get("core_nodes"), sources, issues)
    dispositions = _validate_dispositions(
        payload.get("fine_term_dispositions"),
        taxonomy_terms,
        sources,
        issues,
    )
    mappings = _validate_mappings(
        payload.get("mappings"),
        dispositions,
        nodes,
        issues,
    )
    replacements = _validate_replacements(
        payload.get("replacements"),
        nodes,
        issues,
    )
    _validate_legacy_identities(
        taxonomy_terms,
        nodes,
        replacements,
        issues,
    )
    _validate_disposition_mapping_counts(dispositions, mappings, issues)
    _validate_relations(
        payload.get("relations"),
        nodes,
        sources,
        issues,
    )
    return ValidationReport(release_id=release.release_id, issues=tuple(issues))


def _validate_sources(
    raw: object,
    issues: list[ValidationIssue],
) -> set[str]:
    source_ids: set[str] = set()
    for index, source in enumerate(_objects(raw)):
        path = f"$.sources[{index}]"
        source_id = _required(source, "source_id", path, issues)
        for field in ("kind", "title", "reference"):
            _required(source, field, path, issues)
        if source_id in source_ids:
            _error(issues, "duplicate_source", path, "source_id is duplicated")
        source_ids.add(source_id)
    if not source_ids:
        _error(issues, "missing_sources", "$.sources", "at least one source is required")
    return source_ids


def _validate_nodes(
    raw: object,
    source_ids: set[str],
    issues: list[ValidationIssue],
) -> dict[str, Mapping[str, Any]]:
    nodes: dict[str, Mapping[str, Any]] = {}
    for index, node in enumerate(_objects(raw)):
        path = f"$.core_nodes[{index}]"
        key = _required(node, "stable_key", path, issues)
        if key:
            try:
                normalize_stable_key(key)
            except ValueError as exc:
                _error(issues, "invalid_stable_key", f"{path}.stable_key", str(exc))
        if key in nodes:
            _error(issues, "duplicate_node", path, "stable_key is duplicated")
        nodes[key] = node
        _required(node, "display_name", path, issues)
        for field in (
            "definition",
            "include_scope",
            "exclude_scope",
            "observable_evidence",
            "rationale",
        ):
            _required(node, field, path, issues)
        if _text(node.get("node_kind")) not in _NODE_KINDS:
            _error(issues, "invalid_node_kind", f"{path}.node_kind", "invalid node kind")
        if _text(node.get("status")) not in _NODE_STATUSES:
            _error(issues, "invalid_node_status", f"{path}.status", "invalid node status")
        anchors = _text_list(node.get("curriculum_anchors"))
        if not anchors:
            _error(
                issues,
                "missing_curriculum_anchor",
                f"{path}.curriculum_anchors",
                "at least one curriculum anchor is required",
            )
        _validate_source_refs(node, path, source_ids, issues)
    if not nodes:
        _error(issues, "missing_nodes", "$.core_nodes", "core nodes are required")
    return nodes


def _validate_dispositions(
    raw: object,
    taxonomy_terms: Mapping[str, Mapping[str, Any]],
    source_ids: set[str],
    issues: list[ValidationIssue],
) -> dict[str, Mapping[str, Any]]:
    dispositions: dict[str, Mapping[str, Any]] = {}
    for index, item in enumerate(_objects(raw)):
        path = f"$.fine_term_dispositions[{index}]"
        term_id = _required(item, "fine_term_id", path, issues)
        if term_id in dispositions:
            _error(issues, "duplicate_disposition", path, "fine_term_id is duplicated")
        dispositions[term_id] = item
        expected = taxonomy_terms.get(term_id)
        if expected is None:
            _error(issues, "unknown_fine_term", f"{path}.fine_term_id", "term is not approved")
        elif _text(item.get("display_name")) != _text(expected.get("name")):
            _error(
                issues,
                "fine_term_name_mismatch",
                f"{path}.display_name",
                "must match the governed taxonomy name",
            )
        if _text(item.get("disposition")) not in _DISPOSITIONS:
            _error(issues, "invalid_disposition", f"{path}.disposition", "invalid disposition")
        for field in (
            "display_name",
            "definition",
            "include_scope",
            "exclude_scope",
            "rationale",
        ):
            _required(item, field, path, issues)
        if not _text_list(item.get("curriculum_anchors")):
            _error(
                issues,
                "missing_curriculum_anchor",
                f"{path}.curriculum_anchors",
                "at least one curriculum anchor is required",
            )
        confidence = item.get("confidence")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            _error(issues, "invalid_confidence", f"{path}.confidence", "must be a number")
        elif not 0.0 <= float(confidence) <= 1.0:
            _error(issues, "invalid_confidence", f"{path}.confidence", "must be from 0 to 1")
        if _text(item.get("review_priority")) not in {"normal", "high_impact"}:
            _error(
                issues,
                "invalid_review_priority",
                f"{path}.review_priority",
                "must be normal or high_impact",
            )
        _validate_source_refs(item, path, source_ids, issues)

    missing = sorted(set(taxonomy_terms) - set(dispositions))
    extra = sorted(set(dispositions) - set(taxonomy_terms))
    if missing:
        _error(
            issues,
            "missing_fine_term_dispositions",
            "$.fine_term_dispositions",
            f"missing {len(missing)} approved terms: {', '.join(missing[:5])}",
        )
    if extra:
        _error(
            issues,
            "extra_fine_term_dispositions",
            "$.fine_term_dispositions",
            f"contains {len(extra)} unknown terms",
        )
    return dispositions


def _validate_mappings(
    raw: object,
    dispositions: Mapping[str, Mapping[str, Any]],
    nodes: Mapping[str, Mapping[str, Any]],
    issues: list[ValidationIssue],
) -> dict[str, list[Mapping[str, Any]]]:
    mappings: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(_objects(raw)):
        path = f"$.mappings[{index}]"
        term_id = _required(item, "fine_term_id", path, issues)
        key = _required(item, "stable_key", path, issues)
        pair = (term_id, key)
        if pair in seen:
            _error(issues, "duplicate_mapping", path, "mapping is duplicated")
        seen.add(pair)
        mappings[term_id].append(item)
        if term_id not in dispositions:
            _error(issues, "mapping_unknown_term", f"{path}.fine_term_id", "unknown term")
        if key not in nodes:
            _error(issues, "mapping_unknown_node", f"{path}.stable_key", "unknown node")
        elif _text(nodes[key].get("status")) != "active":
            _error(issues, "mapping_retired_node", f"{path}.stable_key", "node is retired")
        if _text(item.get("mapping_role")) not in _MAPPING_ROLES:
            _error(issues, "invalid_mapping_role", f"{path}.mapping_role", "invalid role")
        _required(item, "rationale", path, issues)
    return mappings


def _validate_disposition_mapping_counts(
    dispositions: Mapping[str, Mapping[str, Any]],
    mappings: Mapping[str, Sequence[Mapping[str, Any]]],
    issues: list[ValidationIssue],
) -> None:
    for term_id, item in dispositions.items():
        disposition = _text(item.get("disposition"))
        term_mappings = list(mappings.get(term_id, ()))
        primary_count = sum(
            _text(mapping.get("mapping_role")) == "primary"
            for mapping in term_mappings
        )
        path = f"$.fine_term_dispositions[{term_id}]"
        if disposition in {"direct_core", "maps_to_core"} and len(term_mappings) != 1:
            _error(issues, "mapping_cardinality", path, "requires exactly one mapping")
        elif disposition == "maps_to_many" and len(term_mappings) < 2:
            _error(issues, "mapping_cardinality", path, "requires at least two mappings")
        elif disposition in {"retrieval_only", "wrong_dimension", "retired"} and term_mappings:
            _error(issues, "mapping_cardinality", path, "must not have a core mapping")
        if term_mappings and primary_count != 1:
            _error(issues, "mapping_primary_count", path, "must have exactly one primary mapping")


def _validate_replacements(
    raw: object,
    nodes: Mapping[str, Mapping[str, Any]],
    issues: list[ValidationIssue],
) -> dict[str, list[Mapping[str, Any]]]:
    result: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(_objects(raw)):
        path = f"$.replacements[{index}]"
        retired = _required(item, "retired_key", path, issues)
        replacement = _required(item, "replacement_key", path, issues)
        pair = (retired, replacement)
        if pair in seen:
            _error(issues, "duplicate_replacement", path, "replacement is duplicated")
        seen.add(pair)
        result[retired].append(item)
        for field, key in (("retired_key", retired), ("replacement_key", replacement)):
            try:
                normalize_stable_key(key)
            except ValueError as exc:
                _error(issues, "invalid_stable_key", f"{path}.{field}", str(exc))
        if replacement not in nodes:
            _error(issues, "replacement_unknown_node", f"{path}.replacement_key", "unknown node")
        if retired == replacement:
            _error(issues, "self_replacement", path, "identity cannot replace itself")
        if _text(item.get("replacement_kind")) not in _REPLACEMENT_KINDS:
            _error(issues, "invalid_replacement_kind", f"{path}.replacement_kind", "invalid kind")
        _required(item, "rationale", path, issues)
    return result


def _validate_legacy_identities(
    taxonomy_terms: Mapping[str, Mapping[str, Any]],
    nodes: Mapping[str, Mapping[str, Any]],
    replacements: Mapping[str, Sequence[Mapping[str, Any]]],
    issues: list[ValidationIssue],
) -> None:
    legacy_keys = {
        term_id
        for term_id, item in taxonomy_terms.items()
        if _text(item.get("origin")) == "p3_registry"
    }
    missing = sorted(legacy_keys - set(nodes) - set(replacements))
    if missing:
        _error(
            issues,
            "legacy_identity_unaccounted",
            "$.core_nodes",
            f"{len(missing)} legacy identities have no profile or replacement",
        )


def _validate_relations(
    raw: object,
    nodes: Mapping[str, Mapping[str, Any]],
    source_ids: set[str],
    issues: list[ValidationIssue],
) -> None:
    seen_keys: set[str] = set()
    seen_pairs: set[frozenset[str]] = set()
    directed: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for index, item in enumerate(_objects(raw)):
        path = f"$.relations[{index}]"
        relation_key = _required(item, "relation_key", path, issues)
        source = _required(item, "source_key", path, issues)
        target = _required(item, "target_key", path, issues)
        relation_type = _text(item.get("relation_type"))
        basis_kind = _text(item.get("basis_kind"))
        strength = _text(item.get("strength"))
        if len(relation_key) != 64:
            _error(issues, "invalid_relation_key", f"{path}.relation_key", "must be a SHA-256 hash")
        if relation_key in seen_keys:
            _error(issues, "duplicate_relation_key", path, "relation_key is duplicated")
        seen_keys.add(relation_key)
        if source not in nodes or target not in nodes:
            _error(issues, "relation_unknown_node", path, "relation references an unknown node")
        if source == target:
            _error(issues, "self_relation", path, "self relations are forbidden")
        if relation_type not in _RELATION_TYPES:
            _error(issues, "invalid_relation_type", f"{path}.relation_type", "invalid type")
        if basis_kind not in _BASIS_KINDS:
            _error(issues, "invalid_basis_kind", f"{path}.basis_kind", "invalid basis")
        if strength not in _STRENGTHS:
            _error(issues, "invalid_relation_strength", f"{path}.strength", "invalid strength")
        pair = frozenset((source, target))
        if pair in seen_pairs:
            _error(issues, "relation_pair_conflict", path, "a node pair has more than one semantic relation")
        seen_pairs.add(pair)
        if relation_type == "related" and source > target:
            _error(issues, "related_not_normalized", path, "related endpoints must be sorted")
        if relation_type == "parent" and strength != "required":
            _error(issues, "parent_strength", f"{path}.strength", "parent must be required")
        if relation_type == "prerequisite":
            if strength == "required" and basis_kind != "mathematical_logic":
                _error(
                    issues,
                    "required_prerequisite_basis",
                    path,
                    "required prerequisites need mathematical_logic evidence",
                )
            if strength == "contextual":
                _error(issues, "prerequisite_strength", path, "prerequisite cannot be contextual")
        if relation_type == "related" and strength != "contextual":
            _error(issues, "related_strength", path, "related must be contextual")
        if relation_type in {"parent", "prerequisite"}:
            directed[relation_type].append((source, target))
        _required(item, "rationale", path, issues)
        _required(item, "source_locator", path, issues)
        _validate_source_refs(item, path, source_ids, issues)

    for relation_type, edges in directed.items():
        if _has_cycle(edges):
            _error(
                issues,
                f"{relation_type}_cycle",
                "$.relations",
                f"{relation_type} relations must be acyclic",
            )


def _has_cycle(edges: Iterable[tuple[str, str]]) -> bool:
    adjacency: dict[str, set[str]] = defaultdict(set)
    nodes: set[str] = set()
    for source, target in edges:
        adjacency[source].add(target)
        nodes.update((source, target))
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        for target in adjacency.get(node, ()):
            if visit(target):
                return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in nodes if node not in visited)


def _validate_source_refs(
    item: Mapping[str, Any],
    path: str,
    source_ids: set[str],
    issues: list[ValidationIssue],
) -> None:
    references = _text_list(item.get("evidence_source_ids"))
    if not references:
        _error(
            issues,
            "missing_evidence_source",
            f"{path}.evidence_source_ids",
            "at least one evidence source is required",
        )
        return
    unknown = sorted(set(references) - source_ids)
    if unknown:
        _error(
            issues,
            "unknown_evidence_source",
            f"{path}.evidence_source_ids",
            f"unknown source IDs: {', '.join(unknown[:5])}",
        )


def _objects(raw: object) -> list[Mapping[str, Any]]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        return []
    return [item for item in raw if isinstance(item, Mapping)]


def _text_list(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        return ()
    return tuple(value for item in raw if (value := _text(item)))


def _text(value: object) -> str:
    return str(value or "").strip()


def _integer(value: object, fallback: int) -> int:
    if isinstance(value, bool):
        return fallback
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _required(
    item: Mapping[str, Any],
    field: str,
    path: str,
    issues: list[ValidationIssue],
) -> str:
    value = _text(item.get(field))
    if not value:
        _error(issues, "required_field", f"{path}.{field}", "field is required")
    return value


def _error(
    issues: list[ValidationIssue],
    code: str,
    path: str,
    message: str,
) -> None:
    issues.append(ValidationIssue(code=code, path=path, message=message))


__all__ = ["validate_release"]
