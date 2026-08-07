"""Machine-local governance for the controlled question taxonomy.

The repository catalog is immutable at runtime. Teacher decisions are kept in
one machine-local overlay so changing branches or replacing application code
cannot silently replace approved terms or the review queue.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
import time
import unicodedata
from collections.abc import Iterable, Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator

from path_manager import get_path_manager
from question_bank.database.paths import question_bank_db_path
from question_bank.knowledge_graph_release.loader import (
    DEFAULT_TAXONOMY_PATH,
    load_release_for_taxonomy_revision,
    load_taxonomy_catalog_for_release,
)
from question_bank.knowledge_graph_release.repository import load_active_release
from question_bank.taxonomy.curriculum_catalog import (
    curriculum_knowledge_ancestors,
    curriculum_volume_contract,
    eligible_curriculum_knowledge_nodes,
)


ALLOWED_DIMENSIONS = (
    "curriculum",
    "knowledge",
    "ability",
    "method",
    "thought",
    "model",
    "special_type",
)
_DIMENSION_SET = frozenset(ALLOWED_DIMENSIONS)
_ACTIVE_TERM_STATUS = "approved"
_TERM_STATUSES = frozenset({"approved", "retired"})
_PROPOSAL_STATUSES = frozenset({"pending", "approved", "merged", "rejected"})
_REVIEW_ACTIONS = frozenset(
    {"approve", "edit", "merge", "map_many", "reject", "retire"}
)
_CATALOG_PATH = DEFAULT_TAXONOMY_PATH
_PROCESS_LOCK = threading.RLock()
_LOCK_TIMEOUT_SECONDS = 10.0
_MAX_OPERATION_RECEIPTS = 5000
_MAX_TERM_NAME_LENGTH = 160
_KNOWLEDGE_RETRIEVAL_LIMIT = 64

_RAW_FIELD_DIMENSIONS: dict[str, str] = {
    "curriculum": "curriculum",
    "curriculum_chapter": "curriculum",
    "textbook_chapter": "curriculum",
    "textbook_chapters": "curriculum",
    "knowledge": "knowledge",
    "knowledge_point": "knowledge",
    "knowledge_points": "knowledge",
    "canonical_knowledge_id": "knowledge",
    "prerequisite_points": "knowledge",
    "ability": "ability",
    "ability_tag": "ability",
    "ability_tags": "ability",
    "method": "method",
    "method_tag": "method",
    "method_tags": "method",
    "thought": "thought",
    "thought_tag": "thought",
    "thought_tags": "thought",
    "math_thought": "thought",
    "math_thought_tags": "thought",
    "model": "model",
    "math_model": "model",
    "math_models": "model",
    "math_model_tags": "model",
    "special_type": "special_type",
    "special_type_tag": "special_type",
    "special_type_tags": "special_type",
}
_DIMENSION_PRIMARY_FIELD: dict[str, str] = {
    "curriculum": "textbook_chapters",
    "knowledge": "knowledge_points",
    "ability": "ability_tags",
    "method": "method_tags",
    "thought": "thought_tags",
    "model": "math_model_tags",
    "special_type": "special_type_tags",
}
_LEGACY_UNCONTROLLED_FIELDS = frozenset(
    {
        "teaching_stage",
        "teaching_stages",
        "sub_skill",
        "sub_skills",
        "measured_skill",
        "measured_skills",
        "supporting_skill",
        "supporting_skills",
    }
)


class TaxonomyGovernanceError(RuntimeError):
    """Base error for controlled-taxonomy operations."""


class TaxonomyStorageError(TaxonomyGovernanceError):
    """Raised when neither the primary state nor its backup can be trusted."""


class TaxonomyValidationError(TaxonomyGovernanceError):
    """Raised when a catalog, state, or review command violates the schema."""


class TaxonomyProposalNotFound(TaxonomyValidationError):
    """Raised when a requested proposal does not exist."""


class TaxonomyTargetTermNotFound(TaxonomyValidationError):
    """Raised when a merge target is missing or belongs to another dimension."""


class TaxonomyReviewInvalid(TaxonomyValidationError):
    """Raised when a teacher review command is incomplete or contradictory."""


class TaxonomyRevisionConflict(TaxonomyGovernanceError):
    """Raised when a teacher writes against a stale taxonomy revision."""

    def __init__(self, expected: int, current: int) -> None:
        super().__init__(
            f"Taxonomy revision changed: expected {expected}, current {current}"
        )
        self.expected = expected
        self.current = current

    @property
    def current_revision(self) -> int:
        return self.current


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _text(value: object) -> str:
    return str(value or "").strip()


def _normalized_name(value: object) -> str:
    normalized = unicodedata.normalize("NFKC", _text(value)).casefold()
    return re.sub(r"[\s\W_]+", "", normalized)


def _stable_id(prefix: str, *parts: object) -> str:
    digest = hashlib.sha256(
        "\0".join(_text(part) for part in parts).encode("utf-8")
    ).hexdigest()[:16]
    return f"{prefix}-{digest}"


def _fingerprint(payload: object) -> str:
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _unique_text(values: Iterable[object]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _text(value)
        key = _normalized_name(text)
        if text and key and key not in seen:
            seen.add(key)
            result.append(text)
    return result


def _require_exact_keys(
    value: Mapping[str, Any],
    *,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
    label: str,
) -> None:
    keys = frozenset(value)
    missing = required - keys
    unknown = keys - required - optional
    if missing or unknown:
        raise TaxonomyValidationError(
            f"{label} schema mismatch (missing={sorted(missing)}, "
            f"unknown={sorted(unknown)})"
        )


def _required_string(value: object, *, label: str) -> str:
    text = _text(value)
    if not isinstance(value, str) or not text:
        raise TaxonomyValidationError(f"{label} must be a non-empty string")
    return text


def _string_list(value: object, *, label: str) -> list[str]:
    if not isinstance(value, list):
        raise TaxonomyValidationError(f"{label} must be a list")
    result: list[str] = []
    for index, item in enumerate(value):
        result.append(_required_string(item, label=f"{label}[{index}]"))
    return _unique_text(result)


def _validate_term(
    raw: object,
    *,
    label: str,
    allow_metadata: bool,
) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise TaxonomyValidationError(f"{label} must be an object")
    optional = (
        frozenset(
            {
                "origin",
                "source_paths",
                "retrieval_hints",
                "legacy_names",
                "legacy_ids",
            }
        )
        if allow_metadata
        else frozenset({"origin", "created_at", "updated_at"})
    )
    _require_exact_keys(
        raw,
        required=frozenset({"id", "dimension", "name", "aliases", "status"}),
        optional=optional,
        label=label,
    )
    term_id = _required_string(raw.get("id"), label=f"{label}.id")
    dimension = _required_string(
        raw.get("dimension"), label=f"{label}.dimension"
    )
    if dimension not in _DIMENSION_SET:
        raise TaxonomyValidationError(f"{label}.dimension is not supported")
    name = _required_string(raw.get("name"), label=f"{label}.name")
    aliases = _string_list(raw.get("aliases"), label=f"{label}.aliases")
    if len(name) > _MAX_TERM_NAME_LENGTH or any(
        len(alias) > _MAX_TERM_NAME_LENGTH for alias in aliases
    ):
        raise TaxonomyValidationError(
            f"{label} name and aliases must not exceed {_MAX_TERM_NAME_LENGTH} characters"
        )
    status = _required_string(raw.get("status"), label=f"{label}.status")
    if status not in _TERM_STATUSES:
        raise TaxonomyValidationError(f"{label}.status is not supported")
    result: dict[str, Any] = {
        "id": term_id,
        "dimension": dimension,
        "name": name,
        "aliases": [
            alias
            for alias in aliases
            if _normalized_name(alias) != _normalized_name(name)
        ],
        "status": status,
    }
    if "origin" in raw:
        result["origin"] = _required_string(
            raw.get("origin"), label=f"{label}.origin"
        )
    if allow_metadata and "source_paths" in raw:
        source_paths = raw.get("source_paths")
        if not isinstance(source_paths, list):
            raise TaxonomyValidationError(f"{label}.source_paths must be a list")
        validated_paths: list[list[str]] = []
        for index, path in enumerate(source_paths):
            validated_paths.append(
                _string_list(path, label=f"{label}.source_paths[{index}]")
            )
        result["source_paths"] = validated_paths
    if allow_metadata and "retrieval_hints" in raw:
        retrieval_hints = _string_list(
            raw.get("retrieval_hints"),
            label=f"{label}.retrieval_hints",
        )
        if any(
            len(hint) > _MAX_TERM_NAME_LENGTH for hint in retrieval_hints
        ):
            raise TaxonomyValidationError(
                f"{label}.retrieval_hints must not exceed "
                f"{_MAX_TERM_NAME_LENGTH} characters"
            )
        result["retrieval_hints"] = retrieval_hints
    if allow_metadata:
        for field in ("legacy_names", "legacy_ids"):
            if field not in raw:
                continue
            values = _string_list(raw.get(field), label=f"{label}.{field}")
            if any(len(value) > _MAX_TERM_NAME_LENGTH for value in values):
                raise TaxonomyValidationError(
                    f"{label}.{field} must not exceed "
                    f"{_MAX_TERM_NAME_LENGTH} characters"
                )
            result[field] = values
    if not allow_metadata:
        for field in ("created_at", "updated_at"):
            if field in raw:
                result[field] = _required_string(
                    raw.get(field), label=f"{label}.{field}"
                )
    return result


def _validate_catalog(payload: object, *, source: Path) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise TaxonomyValidationError(f"Catalog in {source} must be an object")
    _require_exact_keys(
        payload,
        required=frozenset(
            {
                "schema_version",
                "catalog_id",
                "revision",
                "source_snapshot",
                "classification_policy",
                "terms",
                "reference_candidates",
            }
        ),
        optional=frozenset({"expected_approved_knowledge_count"}),
        label="catalog",
    )
    if payload.get("schema_version") != 2:
        raise TaxonomyValidationError("Catalog schema_version must be 2")
    revision = payload.get("revision")
    if type(revision) is not int or revision < 1:  # bool is invalid
        raise TaxonomyValidationError("Catalog revision must be a positive integer")
    source_snapshot = payload.get("source_snapshot")
    if not isinstance(source_snapshot, Mapping):
        raise TaxonomyValidationError("Catalog source_snapshot must be an object")
    classification_policy = payload.get("classification_policy")
    if not isinstance(classification_policy, Mapping):
        raise TaxonomyValidationError(
            "Catalog classification_policy must be an object"
        )
    raw_terms = payload.get("terms")
    if not isinstance(raw_terms, list):
        raise TaxonomyValidationError("Catalog terms must be a list")
    terms = [
        _validate_term(item, label=f"catalog.terms[{index}]", allow_metadata=True)
        for index, item in enumerate(raw_terms)
    ]
    raw_candidates = payload.get("reference_candidates")
    if not isinstance(raw_candidates, list):
        raise TaxonomyValidationError("Catalog reference_candidates must be a list")
    reference_candidates: list[dict[str, Any]] = []
    for index, item in enumerate(raw_candidates):
        if not isinstance(item, Mapping):
            raise TaxonomyValidationError(
                f"catalog.reference_candidates[{index}] must be an object"
            )
        _require_exact_keys(
            item,
            required=frozenset({"name", "source_path", "reason"}),
            label=f"catalog.reference_candidates[{index}]",
        )
        reference_candidates.append(
            {
                "name": _required_string(
                    item.get("name"),
                    label=f"catalog.reference_candidates[{index}].name",
                ),
                "source_path": _string_list(
                    item.get("source_path"),
                    label=f"catalog.reference_candidates[{index}].source_path",
                ),
                "reason": _required_string(
                    item.get("reason"),
                    label=f"catalog.reference_candidates[{index}].reason",
                ),
            }
        )

    seen_ids: set[str] = set()
    aliases: dict[tuple[str, str], str] = {}
    for term in terms:
        if term["id"] in seen_ids:
            raise TaxonomyValidationError(
                f"Duplicate catalog term id: {term['id']}"
            )
        seen_ids.add(term["id"])
        for value in (term["id"], term["name"], *term["aliases"]):
            key = (term["dimension"], _normalized_name(value))
            owner = aliases.get(key)
            if owner is not None and owner != term["id"]:
                raise TaxonomyValidationError(
                    f"Ambiguous catalog alias in {term['dimension']}: {value}"
                )
            aliases[key] = term["id"]
    result = {
        "schema_version": 2,
        "catalog_id": _required_string(
            payload.get("catalog_id"), label="catalog.catalog_id"
        ),
        "revision": revision,
        "source_snapshot": copy.deepcopy(dict(source_snapshot)),
        "classification_policy": copy.deepcopy(dict(classification_policy)),
        "terms": terms,
        "reference_candidates": reference_candidates,
    }
    if "expected_approved_knowledge_count" in payload:
        expected_count = payload.get("expected_approved_knowledge_count")
        if type(expected_count) is not int or expected_count <= 0:
            raise TaxonomyValidationError(
                "Catalog expected_approved_knowledge_count must be positive"
            )
        result["expected_approved_knowledge_count"] = expected_count
    return result


def _validate_proposal(raw: object, *, index: int) -> dict[str, Any]:
    label = f"state.proposals[{index}]"
    if not isinstance(raw, Mapping):
        raise TaxonomyValidationError(f"{label} must be an object")
    _require_exact_keys(
        raw,
        required=frozenset(
            {
                "id",
                "dimension",
                "proposed_name",
                "normalized_name",
                "edited_name",
                "aliases",
                "definition",
                "nearest_id",
                "why_not_reuse",
                "reasons",
                "models",
                "question_refs",
                "occurrences",
                "status",
                "resolved_term_id",
                "created_at",
                "updated_at",
                "reviewed_at",
            }
        ),
        optional=frozenset({"resolved_term_ids"}),
        label=label,
    )
    dimension = _required_string(
        raw.get("dimension"), label=f"{label}.dimension"
    )
    if dimension not in _DIMENSION_SET:
        raise TaxonomyValidationError(f"{label}.dimension is not supported")
    proposed_name = _required_string(
        raw.get("proposed_name"), label=f"{label}.proposed_name"
    )
    if len(proposed_name) > _MAX_TERM_NAME_LENGTH:
        raise TaxonomyValidationError(
            f"{label}.proposed_name must not exceed {_MAX_TERM_NAME_LENGTH} characters"
        )
    normalized_name = _required_string(
        raw.get("normalized_name"), label=f"{label}.normalized_name"
    )
    if normalized_name != _normalized_name(proposed_name):
        raise TaxonomyValidationError(f"{label}.normalized_name is inconsistent")
    edited_name = raw.get("edited_name")
    if edited_name is not None:
        edited_name = _required_string(edited_name, label=f"{label}.edited_name")
        if len(edited_name) > _MAX_TERM_NAME_LENGTH:
            raise TaxonomyValidationError(
                f"{label}.edited_name must not exceed {_MAX_TERM_NAME_LENGTH} characters"
            )
    status = _required_string(raw.get("status"), label=f"{label}.status")
    if status not in _PROPOSAL_STATUSES:
        raise TaxonomyValidationError(f"{label}.status is not supported")
    occurrences = raw.get("occurrences")
    if type(occurrences) is not int or occurrences < 1:
        raise TaxonomyValidationError(
            f"{label}.occurrences must be a positive integer"
        )
    resolved_term_id = raw.get("resolved_term_id")
    if resolved_term_id is not None:
        resolved_term_id = _required_string(
            resolved_term_id, label=f"{label}.resolved_term_id"
        )
    resolved_term_ids = _string_list(
        raw.get(
            "resolved_term_ids",
            [resolved_term_id] if resolved_term_id is not None else [],
        ),
        label=f"{label}.resolved_term_ids",
    )
    if resolved_term_id and resolved_term_id not in resolved_term_ids:
        resolved_term_ids.insert(0, resolved_term_id)
    reviewed_at = raw.get("reviewed_at")
    if reviewed_at is not None:
        reviewed_at = _required_string(
            reviewed_at, label=f"{label}.reviewed_at"
        )
    proposal_aliases = _string_list(
        raw.get("aliases"), label=f"{label}.aliases"
    )
    if any(len(alias) > _MAX_TERM_NAME_LENGTH for alias in proposal_aliases):
        raise TaxonomyValidationError(
            f"{label}.aliases must not exceed {_MAX_TERM_NAME_LENGTH} characters"
        )
    return {
        "id": _required_string(raw.get("id"), label=f"{label}.id"),
        "dimension": dimension,
        "proposed_name": proposed_name,
        "normalized_name": normalized_name,
        "edited_name": edited_name,
        "aliases": proposal_aliases,
        "definition": _text(raw.get("definition")),
        "nearest_id": _text(raw.get("nearest_id")),
        "why_not_reuse": _text(raw.get("why_not_reuse")),
        "reasons": _string_list(raw.get("reasons"), label=f"{label}.reasons"),
        "models": _string_list(raw.get("models"), label=f"{label}.models"),
        "question_refs": _string_list(
            raw.get("question_refs"), label=f"{label}.question_refs"
        ),
        "occurrences": occurrences,
        "status": status,
        "resolved_term_id": resolved_term_id,
        "resolved_term_ids": resolved_term_ids,
        "created_at": _required_string(
            raw.get("created_at"), label=f"{label}.created_at"
        ),
        "updated_at": _required_string(
            raw.get("updated_at"), label=f"{label}.updated_at"
        ),
        "reviewed_at": reviewed_at,
    }


def _validate_operation(raw: object, *, index: int) -> dict[str, Any]:
    label = f"state.applied_operations[{index}]"
    if not isinstance(raw, Mapping):
        raise TaxonomyValidationError(f"{label} must be an object")
    _require_exact_keys(
        raw,
        required=frozenset(
            {"request_token", "fingerprint", "result", "applied_revision"}
        ),
        label=label,
    )
    result = raw.get("result")
    if not isinstance(result, Mapping):
        raise TaxonomyValidationError(f"{label}.result must be an object")
    applied_revision = raw.get("applied_revision")
    if type(applied_revision) is not int or applied_revision < 0:
        raise TaxonomyValidationError(
            f"{label}.applied_revision must be a non-negative integer"
        )
    return {
        "request_token": _required_string(
            raw.get("request_token"), label=f"{label}.request_token"
        ),
        "fingerprint": _required_string(
            raw.get("fingerprint"), label=f"{label}.fingerprint"
        ),
        "result": copy.deepcopy(dict(result)),
        "applied_revision": applied_revision,
    }


def _empty_observation_lifecycle() -> dict[str, Any]:
    return {
        "next_source_sequence": 1,
        "evidence_revision": 0,
        "allocations": {},
        "observations": [],
    }


def _validate_observation_lifecycle(raw: object) -> dict[str, Any]:
    if raw is None:
        return _empty_observation_lifecycle()
    if not isinstance(raw, Mapping):
        raise TaxonomyValidationError(
            "State observation_lifecycle must be an object"
        )
    _require_exact_keys(
        raw,
        required=frozenset(
            {
                "next_source_sequence",
                "evidence_revision",
                "allocations",
                "observations",
            }
        ),
        label="state.observation_lifecycle",
    )
    next_sequence = raw.get("next_source_sequence")
    evidence_revision = raw.get("evidence_revision")
    allocations = raw.get("allocations")
    observations = raw.get("observations")
    if type(next_sequence) is not int or next_sequence < 1:
        raise TaxonomyValidationError(
            "State next_source_sequence must be a positive integer"
        )
    if type(evidence_revision) is not int or evidence_revision < 0:
        raise TaxonomyValidationError(
            "State evidence_revision must be a non-negative integer"
        )
    if not isinstance(allocations, Mapping) or not isinstance(observations, list):
        raise TaxonomyValidationError("State observation lifecycle is invalid")
    clean_allocations: dict[str, dict[str, int]] = {}
    seen_sequences: set[int] = set()
    for generation_id, by_question in allocations.items():
        generation = _required_string(
            generation_id, label="state.observation_lifecycle.generation_id"
        )
        if not isinstance(by_question, Mapping):
            raise TaxonomyValidationError("Observation allocation must be an object")
        clean_allocations[generation] = {}
        for question_id, sequence in by_question.items():
            question = _required_string(
                question_id, label="state.observation_lifecycle.question_id"
            )
            if type(sequence) is not int or sequence < 1:
                raise TaxonomyValidationError(
                    "Observation source_sequence must be a positive integer"
                )
            if sequence in seen_sequences:
                raise TaxonomyValidationError(
                    "Observation source_sequence must be unique"
                )
            seen_sequences.add(sequence)
            clean_allocations[generation][question] = sequence
    clean_observations: list[dict[str, Any]] = []
    current_questions: set[str] = set()
    seen_observations: set[tuple[str, str]] = set()
    for index, item in enumerate(observations):
        label = f"state.observation_lifecycle.observations[{index}]"
        if not isinstance(item, Mapping):
            raise TaxonomyValidationError(f"{label} must be an object")
        _require_exact_keys(
            item,
            required=frozenset(
                {
                    "question_id",
                    "generation_id",
                    "source_sequence",
                    "proposal_ids",
                    "completed_at",
                    "taxonomy_revision",
                    "graph_release_id",
                    "is_current",
                    "superseded_by_generation_id",
                }
            ),
            label=label,
        )
        question_id = _required_string(
            item.get("question_id"), label=f"{label}.question_id"
        )
        generation_id = _required_string(
            item.get("generation_id"), label=f"{label}.generation_id"
        )
        key = (question_id, generation_id)
        if key in seen_observations:
            raise TaxonomyValidationError("Observation identity must be unique")
        seen_observations.add(key)
        sequence = item.get("source_sequence")
        taxonomy_revision = item.get("taxonomy_revision")
        if type(sequence) is not int or sequence < 1:
            raise TaxonomyValidationError(f"{label}.source_sequence is invalid")
        if type(taxonomy_revision) is not int or taxonomy_revision < 0:
            raise TaxonomyValidationError(f"{label}.taxonomy_revision is invalid")
        is_current = item.get("is_current")
        if type(is_current) is not bool:
            raise TaxonomyValidationError(f"{label}.is_current is invalid")
        if is_current:
            if question_id in current_questions:
                raise TaxonomyValidationError(
                    "Only one current observation is allowed per question"
                )
            current_questions.add(question_id)
        superseded_by = item.get("superseded_by_generation_id")
        if superseded_by is not None:
            superseded_by = _required_string(
                superseded_by,
                label=f"{label}.superseded_by_generation_id",
            )
        clean_observations.append(
            {
                "question_id": question_id,
                "generation_id": generation_id,
                "source_sequence": sequence,
                "proposal_ids": _string_list(
                    item.get("proposal_ids"), label=f"{label}.proposal_ids"
                ),
                "completed_at": _required_string(
                    item.get("completed_at"), label=f"{label}.completed_at"
                ),
                "taxonomy_revision": taxonomy_revision,
                "graph_release_id": _text(item.get("graph_release_id")),
                "is_current": is_current,
                "superseded_by_generation_id": superseded_by,
            }
        )
    return {
        "next_source_sequence": next_sequence,
        "evidence_revision": evidence_revision,
        "allocations": clean_allocations,
        "observations": clean_observations,
    }


def _empty_state(catalog: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "base_catalog_id": catalog["catalog_id"],
        "base_catalog_revision": catalog["revision"],
        "revision": 0,
        "approved_terms": [],
        "proposals": [],
        "applied_operations": [],
        "observation_lifecycle": _empty_observation_lifecycle(),
    }


def _validate_state(
    payload: object,
    *,
    catalog: Mapping[str, Any],
    source: Path,
) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise TaxonomyValidationError(f"Taxonomy state in {source} must be an object")
    _require_exact_keys(
        payload,
        required=frozenset(
            {
                "schema_version",
                "base_catalog_id",
                "base_catalog_revision",
                "revision",
                "approved_terms",
                "proposals",
                "applied_operations",
            }
        ),
        optional=frozenset({"observation_lifecycle"}),
        label="state",
    )
    if payload.get("schema_version") != 1:
        raise TaxonomyValidationError("State schema_version must be 1")
    if payload.get("base_catalog_id") != catalog["catalog_id"]:
        raise TaxonomyValidationError(
            "State belongs to a different base taxonomy catalog"
        )
    base_revision = payload.get("base_catalog_revision")
    revision = payload.get("revision")
    if type(base_revision) is not int or base_revision < 1:
        raise TaxonomyValidationError(
            "State base_catalog_revision must be a positive integer"
        )
    if type(revision) is not int or revision < 0:
        raise TaxonomyValidationError(
            "State revision must be a non-negative integer"
        )
    raw_terms = payload.get("approved_terms")
    raw_proposals = payload.get("proposals")
    raw_operations = payload.get("applied_operations")
    if not isinstance(raw_terms, list):
        raise TaxonomyValidationError("State approved_terms must be a list")
    if not isinstance(raw_proposals, list):
        raise TaxonomyValidationError("State proposals must be a list")
    if not isinstance(raw_operations, list):
        raise TaxonomyValidationError("State applied_operations must be a list")
    approved_terms = [
        _validate_term(item, label=f"state.approved_terms[{index}]", allow_metadata=False)
        for index, item in enumerate(raw_terms)
    ]
    proposals = [
        _validate_proposal(item, index=index)
        for index, item in enumerate(raw_proposals)
    ]
    operations = [
        _validate_operation(item, index=index)
        for index, item in enumerate(raw_operations)
    ]
    if len({term["id"] for term in approved_terms}) != len(approved_terms):
        raise TaxonomyValidationError("State approved_terms has duplicate ids")
    proposal_keys = {
        (proposal["dimension"], proposal["normalized_name"])
        for proposal in proposals
    }
    if len(proposal_keys) != len(proposals):
        raise TaxonomyValidationError(
            "State proposals has duplicate dimension/name pairs"
        )
    operation_tokens = [item["request_token"] for item in operations]
    if len(set(operation_tokens)) != len(operation_tokens):
        raise TaxonomyValidationError(
            "State applied_operations has duplicate request tokens"
        )
    return {
        "schema_version": 1,
        "base_catalog_id": catalog["catalog_id"],
        "base_catalog_revision": base_revision,
        "revision": revision,
        "approved_terms": approved_terms,
        "proposals": proposals,
        "applied_operations": operations,
        "observation_lifecycle": _validate_observation_lifecycle(
            payload.get("observation_lifecycle")
        ),
    }


def _backup_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.bak")


@contextmanager
def _exclusive_state_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f"{path.name}.lock")
    with _PROCESS_LOCK:
        with lock_path.open("a+b") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            if os.name == "nt":
                import msvcrt

                deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
                while True:
                    try:
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise TaxonomyStorageError(
                                f"Timed out waiting for taxonomy lock: {lock_path}"
                            )
                        time.sleep(0.05)
                try:
                    yield
                finally:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_state_atomic(
    path: Path,
    state: Mapping[str, Any],
    *,
    catalog: Mapping[str, Any],
) -> None:
    validated = _validate_state(state, catalog=catalog, source=path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f"{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(validated, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        _validate_state(_read_json(temporary), catalog=catalog, source=temporary)
        if path.is_file():
            try:
                _validate_state(_read_json(path), catalog=catalog, source=path)
            except (OSError, json.JSONDecodeError, TaxonomyValidationError):
                pass
            else:
                shutil.copy2(path, _backup_path(path))
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _flatten_values(value: object) -> list[tuple[str, str]]:
    if value is None:
        return []
    if isinstance(value, str):
        return [(value.strip(), "")] if value.strip() else []
    if isinstance(value, Mapping):
        name = next(
            (
                _text(value.get(key))
                for key in ("name", "proposed_name", "value", "id")
                if _text(value.get(key))
            ),
            "",
        )
        reason = _text(value.get("reason") or value.get("rationale"))
        return [(name, reason)] if name else []
    if isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray)):
        result: list[tuple[str, str]] = []
        for item in value:
            result.extend(_flatten_values(item))
        return result
    text = _text(value)
    return [(text, "")] if text else []


def _context_mapping(context: object) -> dict[str, Any]:
    if context is None:
        return {}
    if isinstance(context, Mapping):
        return dict(context)
    to_dict = getattr(context, "to_dict", None)
    if callable(to_dict):
        values = to_dict()
        if isinstance(values, Mapping):
            return dict(values)
    return {"text": _text(context)}


def _search_text(value: object, *, depth: int = 0) -> str:
    if depth > 3 or value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        return " ".join(_search_text(item, depth=depth + 1) for item in value.values())
    if isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray)):
        return " ".join(_search_text(item, depth=depth + 1) for item in value)
    return _text(value)


def _bigrams(value: object) -> set[str]:
    text = _normalized_name(value)
    if len(text) < 2:
        return {text} if text else set()
    return {text[index : index + 2] for index in range(len(text) - 1)}


def _knowledge_release_prompt_contract(
    db_path: Path,
    *,
    taxonomy_revision: int,
) -> dict[str, Any]:
    """Return the compact, immutable knowledge catalog shared by a batch."""

    packaged_release = load_release_for_taxonomy_revision(taxonomy_revision)
    database_exists = Path(db_path).is_file()
    active_release = load_active_release(db_path) if database_exists else None
    if (
        active_release is not None
        and active_release.taxonomy_revision != taxonomy_revision
    ):
        raise TaxonomyStorageError(
            "Active knowledge release and taxonomy catalog revisions do not match"
        )
    release = active_release or packaged_release
    payload = release.payload
    node_names = {
        str(item["stable_key"]): str(item["display_name"])
        for item in payload.get("core_nodes", [])
        if isinstance(item, Mapping)
    }
    targets: dict[str, list[str]] = {}
    for item in payload.get("mappings", []):
        if not isinstance(item, Mapping):
            continue
        fine_term_id = str(item.get("fine_term_id") or "").strip()
        target_name = node_names.get(str(item.get("stable_key") or ""), "")
        if fine_term_id and target_name:
            targets.setdefault(fine_term_id, []).append(target_name)
    terms: dict[str, dict[str, str]] = {}
    for item in payload.get("fine_term_dispositions", []):
        if not isinstance(item, Mapping):
            continue
        fine_term_id = str(item.get("fine_term_id") or "").strip()
        disposition = str(item.get("disposition") or "").strip()
        anchors = item.get("curriculum_anchors")
        topic = "初中数学"
        if isinstance(anchors, list) and anchors:
            topic = str(anchors[0]).removeprefix("第四学段/")
        usage = disposition
        if disposition == "wrong_dimension":
            usage = "do_not_use_as_knowledge"
        row = {
            "topic": topic,
            "usage": usage,
        }
        mapped_names = targets.get(fine_term_id, [])
        if mapped_names:
            row["core"] = "/".join(mapped_names)
        if str(item.get("review_priority") or "") == "high_impact":
            row["boundary"] = str(item.get("exclude_scope") or "")[:72]
        terms[fine_term_id] = row
    return {
        "release_id": (
            release.release_id
            if active_release is not None or not database_exists
            else ""
        ),
        "taxonomy_revision": release.taxonomy_revision,
        "terms": terms,
    }


def _term_public(term: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": term["id"],
        "dimension": term["dimension"],
        "name": term["name"],
        "aliases": list(term.get("aliases", [])),
        "status": "active" if term["status"] == _ACTIVE_TERM_STATUS else "retired",
    }


def _proposal_public(proposal: Mapping[str, Any]) -> dict[str, Any]:
    question_refs: list[int] = []
    for value in proposal.get("question_refs", []):
        try:
            question_id = int(value)
        except (TypeError, ValueError):
            continue
        if question_id > 0 and question_id not in question_refs:
            question_refs.append(question_id)
    reasons = [
        _text(value)
        for value in proposal.get("reasons", [])
        if _text(value)
    ]
    return {
        "id": proposal["id"],
        "dimension": proposal["dimension"],
        "proposed_name": proposal["proposed_name"],
        "edited_name": proposal.get("edited_name"),
        "definition": _text(proposal.get("definition")),
        "reason": "；".join(reasons),
        "nearest_id": _text(proposal.get("nearest_id")),
        "why_not_reuse": _text(proposal.get("why_not_reuse")),
        "question_refs": question_refs,
        "status": proposal["status"],
        "created_at": proposal.get("created_at"),
        "updated_at": proposal.get("updated_at"),
    }


def _proposal_public_extended(proposal: Mapping[str, Any]) -> dict[str, Any]:
    result = _proposal_public(proposal)
    result["resolved_term_ids"] = _unique_text(
        proposal.get("resolved_term_ids")
        or (
            [proposal.get("resolved_term_id")]
            if proposal.get("resolved_term_id")
            else []
        )
    )
    return result


class TaxonomyGovernance:
    """Deep boundary for catalog lookup, AI constraints, and teacher decisions."""

    def __init__(
        self,
        *,
        catalog_path: Path | None = None,
        state_path: Path | None = None,
        knowledge_graph_db_path: Path | None = None,
    ) -> None:
        self.state_path = Path(
            state_path or get_path_manager().taxonomy_state_path
        )
        self.knowledge_graph_db_path = Path(
            knowledge_graph_db_path or question_bank_db_path()
        )
        self.catalog_path = Path(catalog_path or _CATALOG_PATH)
        self._catalog_is_explicit = catalog_path is not None
        try:
            if catalog_path is not None:
                catalog_payload = _read_json(self.catalog_path)
            else:
                active_release = (
                    load_active_release(self.knowledge_graph_db_path)
                    if self.knowledge_graph_db_path.is_file()
                    else None
                )
                catalog_payload = (
                    load_taxonomy_catalog_for_release(active_release)
                    if active_release is not None
                    else _read_json(self.catalog_path)
                )
            self._catalog = _validate_catalog(
                catalog_payload, source=self.catalog_path
            )
        except (
            OSError,
            ValueError,
            json.JSONDecodeError,
            TaxonomyValidationError,
        ) as exc:
            raise TaxonomyStorageError(
                f"Controlled taxonomy catalog is unavailable: {self.catalog_path}"
            ) from exc
        self._catalogs_by_revision: dict[int, dict[str, Any]] = {
            int(self._catalog["revision"]): self._catalog
        }

    def _catalog_for_revision(self, revision: int) -> dict[str, Any]:
        cached = self._catalogs_by_revision.get(revision)
        if cached is not None:
            return cached
        try:
            release = load_release_for_taxonomy_revision(revision)
            catalog = _validate_catalog(
                load_taxonomy_catalog_for_release(release),
                source=Path(f"bundled-taxonomy-revision-{revision}"),
            )
        except (
            OSError,
            ValueError,
            json.JSONDecodeError,
            TaxonomyValidationError,
        ) as exc:
            raise TaxonomyStorageError(
                f"Controlled taxonomy revision {revision} is unavailable"
            ) from exc
        self._catalogs_by_revision[revision] = catalog
        return catalog

    def _prompt_catalog(self) -> dict[str, Any]:
        if (
            self._catalog_is_explicit
            or not self.knowledge_graph_db_path.is_file()
        ):
            return self._catalog
        active_release = load_active_release(self.knowledge_graph_db_path)
        if active_release is None:
            return self._catalog
        return self._catalog_for_revision(active_release.taxonomy_revision)

    def _read_state_unlocked(
        self,
        *,
        catalog: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        selected_catalog = catalog or self._catalog
        if not self.state_path.exists():
            return _empty_state(selected_catalog)
        failures: list[Exception] = []
        for candidate in (self.state_path, _backup_path(self.state_path)):
            if not candidate.is_file():
                continue
            try:
                return _validate_state(
                    _read_json(candidate),
                    catalog=selected_catalog,
                    source=candidate,
                )
            except (OSError, json.JSONDecodeError, TaxonomyValidationError) as exc:
                failures.append(exc)
        raise TaxonomyStorageError(
            "Taxonomy state and backup are both unavailable or invalid"
        ) from (failures[-1] if failures else None)

    def _read_state(
        self,
        *,
        catalog: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        with _PROCESS_LOCK:
            return self._read_state_unlocked(catalog=catalog)

    def _combined_terms(
        self,
        state: Mapping[str, Any],
        *,
        catalog: Mapping[str, Any] | None = None,
    ) -> tuple[
        list[dict[str, Any]],
        dict[tuple[str, str], dict[str, Any]],
        dict[tuple[str, str], dict[str, Any]],
    ]:
        selected_catalog = catalog or self._catalog
        by_id = {
            term["id"]: copy.deepcopy(term)
            for term in selected_catalog["terms"]
        }
        for term in state["approved_terms"]:
            # Keep catalog-only retrieval metadata when a teacher overlay changes
            # aliases/status for an existing base term.
            combined = copy.deepcopy(by_id.get(term["id"], {}))
            combined.update(copy.deepcopy(term))
            by_id[term["id"]] = combined
        terms = sorted(
            by_id.values(),
            key=lambda item: (
                ALLOWED_DIMENSIONS.index(item["dimension"]),
                item["name"],
                item["id"],
            ),
        )
        canonical_owners = {
            (term["dimension"], _normalized_name(term["name"])): term["id"]
            for term in terms
            if term["status"] == _ACTIVE_TERM_STATUS
        }
        alias_index: dict[tuple[str, str], dict[str, Any]] = {}
        legacy_candidates: dict[
            tuple[str, str], list[dict[str, Any]]
        ] = {}
        for term in terms:
            if term["status"] != _ACTIVE_TERM_STATUS:
                continue
            for position, value in enumerate(
                (term["id"], term["name"], *term["aliases"])
            ):
                key = (term["dimension"], _normalized_name(value))
                # A newer catalog canonical name wins over an alias retained in
                # an older machine-local overlay. The state file stays untouched.
                canonical_owner = canonical_owners.get(key)
                if position >= 2 and canonical_owner not in (None, term["id"]):
                    continue
                owner = alias_index.get(key)
                if owner is not None and owner["id"] != term["id"]:
                    raise TaxonomyStorageError(
                        f"Ambiguous approved taxonomy alias: {value}"
                    )
                alias_index[key] = term
            for value in (
                *term.get("legacy_names", []),
                *term.get("legacy_ids", []),
            ):
                normalized = _normalized_name(value)
                if normalized:
                    key = (term["dimension"], normalized)
                    legacy_candidates.setdefault(key, []).append(term)
        legacy_index = {
            key: owners[0]
            for key, owners in legacy_candidates.items()
            if len({owner["id"] for owner in owners}) == 1
        }
        return terms, alias_index, legacy_index

    def snapshot(
        self,
        *,
        knowledge_catalog_revision: int | None = None,
    ) -> dict[str, Any]:
        if (
            knowledge_catalog_revision is not None
            and type(knowledge_catalog_revision) is not int
        ):
            raise TaxonomyValidationError(
                "knowledge_catalog_revision must be an integer"
            )
        catalog = (
            self._catalog_for_revision(knowledge_catalog_revision)
            if knowledge_catalog_revision is not None
            else self._prompt_catalog()
        )
        state = self._read_state(catalog=catalog)
        terms, _, _ = self._combined_terms(state, catalog=catalog)
        active_by_dimension = {
            dimension: [
                _term_public(term)
                for term in terms
                if term["dimension"] == dimension
                and term["status"] == _ACTIVE_TERM_STATUS
            ]
            for dimension in ALLOWED_DIMENSIONS
        }
        retired = [
            _term_public(term)
            for term in terms
            if term["status"] == "retired"
        ]
        return {
            "schema_version": 1,
            "revision": state["revision"],
            "base_catalog_id": catalog["catalog_id"],
            "base_catalog_revision": catalog["revision"],
            "allowed_dimensions": list(ALLOWED_DIMENSIONS),
            "terms_by_dimension": active_by_dimension,
            "retired_terms": retired,
            "pending_proposal_count": sum(
                proposal["status"] == "pending"
                for proposal in state["proposals"]
            ),
            "reference_candidate_count": len(
                catalog["reference_candidates"]
            ),
        }

    def catalog(self) -> dict[str, Any]:
        """Return the public controlled catalog used by the review UI."""

        snapshot = self.snapshot()
        return {
            "schema_version": 1,
            "catalog_id": snapshot["base_catalog_id"],
            "revision": snapshot["revision"],
            "dimensions": snapshot["terms_by_dimension"],
        }

    def resolve_term(
        self,
        dimension: str,
        value: object,
        *,
        knowledge_catalog_revision: int | None = None,
    ) -> dict[str, Any] | None:
        dimension = _text(dimension)
        if dimension not in _DIMENSION_SET:
            return None
        if (
            knowledge_catalog_revision is not None
            and type(knowledge_catalog_revision) is not int
        ):
            raise TaxonomyValidationError(
                "knowledge_catalog_revision must be an integer"
            )
        catalog = (
            self._catalog_for_revision(knowledge_catalog_revision)
            if knowledge_catalog_revision is not None
            else self._catalog
        )
        state = self._read_state(catalog=catalog)
        _, alias_index, legacy_index = self._combined_terms(
            state,
            catalog=catalog,
        )
        key = (dimension, _normalized_name(value))
        term = alias_index.get(key)
        if term is None:
            term = legacy_index.get(key)
        return _term_public(term) if term is not None else None

    def resolve_teacher_term(
        self, dimension: str, value: object
    ) -> dict[str, Any] | None:
        """Resolve only an active term explicitly created by teacher review."""

        wanted_dimension = _text(dimension)
        normalized = _normalized_name(value)
        if wanted_dimension not in _DIMENSION_SET or not normalized:
            return None
        state = self._read_state()
        for term in state["approved_terms"]:
            if (
                term.get("origin") != "teacher"
                or term.get("status") != _ACTIVE_TERM_STATUS
                or term.get("dimension") != wanted_dimension
            ):
                continue
            if normalized in {
                _normalized_name(term.get("id")),
                _normalized_name(term.get("name")),
                *(
                    _normalized_name(alias)
                    for alias in term.get("aliases", [])
                ),
            }:
                return _term_public(term)
        return None

    def identity_lookup(self) -> dict[str, dict[str, str]]:
        """Return internal same-dimension lookup values for stored tags.

        Hidden legacy names and IDs are included for local reads and filters,
        but this map is never returned by the catalog API or sent to the model.
        """

        state = self._read_state()
        _, alias_index, legacy_index = self._combined_terms(state)
        lookup = {dimension: {} for dimension in ALLOWED_DIMENSIONS}
        for index in (alias_index, legacy_index):
            for (dimension, normalized), term in index.items():
                lookup[dimension][normalized] = term["name"]
        return lookup

    def identity_and_teacher_lookup(
        self,
    ) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]]]:
        """Build every question-read lookup from one state snapshot."""

        state = self._read_state()
        _, alias_index, legacy_index = self._combined_terms(state)
        identity = {dimension: {} for dimension in ALLOWED_DIMENSIONS}
        for index in (alias_index, legacy_index):
            for (dimension, normalized), term in index.items():
                identity[dimension][normalized] = term["name"]
        teacher = {dimension: {} for dimension in ALLOWED_DIMENSIONS}
        for term in state["approved_terms"]:
            dimension = str(term.get("dimension") or "")
            if (
                dimension not in teacher
                or term.get("origin") != "teacher"
                or term.get("status") != _ACTIVE_TERM_STATUS
            ):
                continue
            values = [term.get("id"), term.get("name"), *term.get("aliases", [])]
            for value in values:
                normalized = _normalized_name(value)
                if normalized:
                    teacher[dimension][normalized] = str(term["name"])
        return identity, teacher

    def snapshot_and_identity_lookup(
        self,
    ) -> tuple[dict[str, Any], dict[str, dict[str, str]]]:
        """Return the public taxonomy and stored-tag lookup from one read."""

        catalog = self._prompt_catalog()
        state = self._read_state(catalog=catalog)
        terms, alias_index, legacy_index = self._combined_terms(
            state,
            catalog=catalog,
        )
        active_by_dimension = {
            dimension: [
                _term_public(term)
                for term in terms
                if term["dimension"] == dimension
                and term["status"] == _ACTIVE_TERM_STATUS
            ]
            for dimension in ALLOWED_DIMENSIONS
        }
        snapshot = {
            "schema_version": 1,
            "revision": state["revision"],
            "base_catalog_id": catalog["catalog_id"],
            "base_catalog_revision": catalog["revision"],
            "allowed_dimensions": list(ALLOWED_DIMENSIONS),
            "terms_by_dimension": active_by_dimension,
            "retired_terms": [
                _term_public(term)
                for term in terms
                if term["status"] == "retired"
            ],
            "pending_proposal_count": sum(
                proposal["status"] == "pending"
                for proposal in state["proposals"]
            ),
            "reference_candidate_count": len(catalog["reference_candidates"]),
        }
        identity = {dimension: {} for dimension in ALLOWED_DIMENSIONS}
        for index in (alias_index, legacy_index):
            for (dimension, normalized), term in index.items():
                identity[dimension][normalized] = term["name"]
        return snapshot, identity

    def expand_filter_values(
        self, dimension: str, canonical_values: Iterable[object]
    ) -> tuple[str, ...]:
        dimension = _text(dimension)
        if dimension not in _DIMENSION_SET:
            return ()
        state = self._read_state()
        _, alias_index, legacy_index = self._combined_terms(state)
        expanded: list[str] = []
        for value in canonical_values:
            term = alias_index.get((dimension, _normalized_name(value)))
            if term is None:
                term = legacy_index.get(
                    (dimension, _normalized_name(value))
                )
            if term is None:
                text = _text(value)
                if text:
                    expanded.append(text)
                continue
            expanded.extend(
                [
                    term["id"],
                    term["name"],
                    *term["aliases"],
                    *term.get("legacy_names", []),
                    *term.get("legacy_ids", []),
                ]
            )
        return tuple(_unique_text(expanded))

    def prompt_contracts(
        self,
        contexts: Mapping[object, object],
    ) -> dict[object, dict[str, Any]]:
        """Build isolated per-question candidate contracts from one snapshot."""

        catalog = self._prompt_catalog()
        state = self._read_state(catalog=catalog)
        terms, _alias_index, _legacy_index = self._combined_terms(
            state,
            catalog=catalog,
        )
        active_by_dimension = {
            dimension: [
                term
                for term in terms
                if term["dimension"] == dimension
                and term["status"] == _ACTIVE_TERM_STATUS
            ]
            for dimension in ALLOWED_DIMENSIONS
        }
        knowledge_release = _knowledge_release_prompt_contract(
            self.knowledge_graph_db_path,
            taxonomy_revision=int(catalog["revision"]),
        )
        return {
            key: self._prompt_contract_from_snapshot(
                context,
                revision=state["revision"],
                active_by_dimension=active_by_dimension,
                knowledge_release=knowledge_release,
            )
            for key, context in contexts.items()
        }

    def prompt_contract(self, context: object = None) -> dict[str, Any]:
        return self.prompt_contracts({"single": context})["single"]

    def _prompt_contract_from_snapshot(
        self,
        context: object,
        *,
        revision: int,
        active_by_dimension: Mapping[str, list[dict[str, Any]]],
        knowledge_release: Mapping[str, Any],
    ) -> dict[str, Any]:
        values = _context_mapping(context)
        volume_contract = curriculum_volume_contract(
            values.get("curriculum_volume_id")
        )
        scoped_knowledge_nodes = (
            eligible_curriculum_knowledge_nodes(volume_contract["id"])
            if volume_contract is not None
            else ()
        )
        scoped_knowledge_by_id = {
            str(item["id"]): item for item in scoped_knowledge_nodes
        }
        question_text = _text(values.get("question_text") or values.get("text"))
        answer_text = _text(values.get("answer_text"))
        question_type = _text(values.get("question_type"))
        grade_text = " ".join(
            filter(
                None,
                (
                    _text(values.get("grade")),
                    _text(values.get("semester")),
                    _text(values.get("exam_type")),
                ),
            )
        )
        weighted_queries = tuple(
            (query, weight, _bigrams(query))
            for query, weight in (
                (_normalized_name(question_text), 5),
                (_normalized_name(answer_text), 3),
                (_normalized_name(question_type), 2),
                (_normalized_name(grade_text), 2),
            )
        )
        has_query = any(query for query, _weight, _pairs in weighted_queries)
        limits = {
            "curriculum": 8,
            "knowledge": _KNOWLEDGE_RETRIEVAL_LIMIT,
            "ability": 10,
            "method": 32,
            "thought": 20,
            "model": 48,
            "special_type": 16,
        }

        def phrase_score(
            phrase: object,
            *,
            exact_weight: int,
            overlap_weight: int,
        ) -> int:
            normalized = _normalized_name(phrase)
            if not normalized:
                return 0
            pairs = _bigrams(normalized)
            score_value = 0
            for query, field_weight, query_pairs in weighted_queries:
                if not query:
                    continue
                if normalized in query:
                    score_value = max(
                        score_value,
                        exact_weight * field_weight,
                    )
                overlap = len(pairs & query_pairs)
                score_value = max(
                    score_value,
                    overlap * overlap_weight * field_weight,
                )
            return score_value

        def term_score(term: Mapping[str, Any]) -> int:
            score_value = phrase_score(
                term["name"],
                exact_weight=1000,
                overlap_weight=20,
            )
            for alias in term.get("aliases", []):
                score_value = max(
                    score_value,
                    phrase_score(
                        alias,
                        exact_weight=850,
                        overlap_weight=16,
                    ),
                )
            for hint in term.get("retrieval_hints", []):
                score_value = max(
                    score_value,
                    phrase_score(
                        hint,
                        exact_weight=550,
                        overlap_weight=10,
                    ),
                )
            for legacy_name in term.get("legacy_names", []):
                score_value = max(
                    score_value,
                    phrase_score(
                        legacy_name,
                        exact_weight=700,
                        overlap_weight=12,
                    ),
                )
            for path in term.get("source_paths", []):
                score_value = max(
                    score_value,
                    phrase_score(
                        " ".join(path),
                        exact_weight=240,
                        overlap_weight=4,
                    ),
                )
            return score_value

        candidates: dict[str, list[dict[str, Any]]] = {}
        allowed_term_ids: dict[str, list[str]] = {}
        truncated: dict[str, bool] = {}
        for dimension in ALLOWED_DIMENSIONS:
            ranked = sorted(
                (
                    (term_score(term), term)
                    for term in active_by_dimension[dimension]
                ),
                key=lambda item: (-item[0], item[1]["name"], item[1]["id"]),
            )
            if dimension == "curriculum" and volume_contract is not None:
                allowed_chapter_ids = {
                    str(item["id"])
                    for item in volume_contract["chapters"]
                }
                eligible = [
                    item for item in ranked if item[1]["id"] in allowed_chapter_ids
                ]
            elif dimension == "knowledge":
                release_term_ids = set(knowledge_release["terms"])
                eligible = [
                    item
                    for item in ranked
                    if item[1]["id"] in release_term_ids
                    or item[1].get("origin") == "teacher"
                ]
                governed_curriculum_ids = {
                    term["id"]
                    for _score, term in eligible
                    if term.get("origin")
                    == "xkw_bnu_curriculum_2026_08_05"
                }
                if governed_curriculum_ids:
                    if volume_contract is None:
                        eligible = [
                            item
                            for item in eligible
                            if item[1].get("origin") == "teacher"
                        ]
                    else:
                        allowed_knowledge_ids = set(scoped_knowledge_by_id)
                        eligible = [
                            item
                            for item in eligible
                            if item[1]["id"] in allowed_knowledge_ids
                            or item[1].get("origin") == "teacher"
                        ]
            elif dimension in {
                "ability",
                "method",
                "thought",
                "model",
                "special_type",
            }:
                eligible = ranked
            elif not has_query:
                eligible = []
            else:
                eligible = [item for item in ranked if item[0] > 0]
            if dimension == "knowledge" and scoped_knowledge_by_id:
                positive = [item for item in eligible if item[0] > 0]
                base = positive[: limits[dimension]]
                if not base:
                    base = [
                        item
                        for item in eligible
                        if int(
                            scoped_knowledge_by_id.get(
                                item[1]["id"], {}
                            ).get("level", 0)
                        )
                        == 1
                    ][: limits[dimension]]
                term_by_id = {term["id"]: term for _score, term in eligible}
                selected = []
                selected_ids: set[str] = set()

                def append_term(term_id: str) -> None:
                    term = term_by_id.get(term_id)
                    if term is None or term_id in selected_ids:
                        return
                    selected_ids.add(term_id)
                    selected.append(term)

                for _score, term in base:
                    append_term(term["id"])
                    for ancestor_id in curriculum_knowledge_ancestors(
                        term["id"]
                    ):
                        append_term(ancestor_id)
            else:
                selected = [
                    term
                    for _score, term in (
                        eligible if dimension == "knowledge"
                        else eligible[: limits[dimension]]
                    )
                ]
            truncated[dimension] = len(eligible) > len(selected)
            if dimension == "knowledge":
                release_terms = knowledge_release["terms"]
                candidates[dimension] = [
                    {
                        "id": term["id"],
                        "name": term["name"],
                        **dict(
                            release_terms.get(
                                term["id"],
                                {
                                    "topic": "教师确认的新词观察",
                                    "usage": "temporary_observation",
                                },
                            )
                        ),
                        **(
                            {
                                "volume_id": metadata["volume_id"],
                                "level": metadata["level"],
                                "parent_id": metadata["parent_id"],
                                "label": metadata["label"],
                            }
                            if (
                                metadata := scoped_knowledge_by_id.get(
                                    term["id"]
                                )
                            )
                            is not None
                            else {}
                        ),
                    }
                    for term in selected
                ]
            else:
                candidates[dimension] = [
                    {"id": term["id"], "name": term["name"]}
                    for term in selected
                ]
            allowed_term_ids[dimension] = [term["id"] for term in selected]

        fingerprint = _fingerprint(
            {
                "revision": revision,
                "knowledge_graph_release_id": knowledge_release["release_id"],
                "allowed_term_ids": allowed_term_ids,
            }
        )
        retrieval_status = (
            "ok" if candidates["knowledge"] else "insufficient"
        )
        result = {
            "schema_version": 1,
            "taxonomy_revision": revision,
            "knowledge_graph_release_id": knowledge_release["release_id"],
            "knowledge_catalog_revision": knowledge_release[
                "taxonomy_revision"
            ],
            "candidate_fingerprint": fingerprint,
            "retrieval_status": retrieval_status,
            "allowed_dimensions": list(ALLOWED_DIMENSIONS),
            "candidates": candidates,
            "allowed_term_ids": allowed_term_ids,
            "truncated": truncated,
            "rules": {
                "selection": (
                    "每道题只能从它自己的对应维度候选词中选择；"
                    "不得跨维度混放。"
                ),
                "curriculum": "教材归属只选择年级上下册及章，不自由描述教学阶段。",
                "special_type": (
                    "特殊题型/考法描述可复用的呈现方式，不等同于选择、填空等基本题型。"
                ),
                "knowledge": (
                    "知识点只能从教师所选册别及以前册别的教材路径候选中选择；"
                    "优先选择能够可靠确定的最深节点，不能确定时选择最近的父节点。"
                ),
                "method": "只选本题实际使用的具体解题程序或构造办法。",
                "thought": "只选跨知识主题复用的通用思考策略，不与具体方法混放。",
                "model": "只选题目满足明确条件的稳定结构，不按图形外观或网络绰号自由命名。",
                "unknown": (
                    "知识点候选中确无匹配时可返回 proposed_tags；每题最多1个。"
                    "本地会先与完整正式词表匹配，仍未知才进入教师审核。"
                ),
                "forbidden_dimensions": [
                    "sub_skill",
                    "measured_skill",
                    "supporting_skill",
                ],
            },
            "output_shape": {
                "curriculum": ["term-id"],
                "knowledge": ["term-id"],
                "ability": ["term-id"],
                "method": ["term-id"],
                "thought": ["term-id"],
                "model": ["term-id"],
                "special_type": ["term-id"],
                "proposed_tags": [
                    {"dimension": "knowledge", "name": "新词", "reason": "原因"}
                ],
            },
        }
        if volume_contract is not None:
            result["curriculum_volume"] = volume_contract
            result["rules"]["curriculum"] = (
                "教材归属只能从当前册别的小节稳定 ID 中选择；"
                "模型返回 curriculum_sections，本地程序据此派生所属章节。"
            )
            result["output_shape"]["curriculum_sections"] = ["section-id"]
        return result

    def _classify(
        self,
        raw_analysis: Mapping[str, Any],
        *,
        state: Mapping[str, Any],
        allowed_term_ids: Mapping[str, object] | None = None,
        catalog: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        selected_catalog = catalog or self._catalog
        terms, alias_index, legacy_index = self._combined_terms(
            state,
            catalog=selected_catalog,
        )
        accepted_terms = {dimension: [] for dimension in ALLOWED_DIMENSIONS}
        accepted_fields: dict[str, list[str]] = {}
        unknown_by_key: dict[tuple[str, str], dict[str, str]] = {}
        retrieval_misses: list[dict[str, str]] = []
        proposal_overflow: list[dict[str, str]] = []
        free_keys: set[tuple[str, str]] = set()
        ignored_fields: list[str] = []
        restricted = isinstance(allowed_term_ids, Mapping)
        allowed_ids: dict[str, set[str]] = {
            dimension: {
                _text(value)
                for value in (
                    allowed_term_ids.get(dimension, [])
                    if restricted
                    else []
                )
                if _text(value)
            }
            for dimension in ALLOWED_DIMENSIONS
        }

        def nearest_term(dimension: str, name: str) -> dict[str, Any] | None:
            wanted = _bigrams(name)
            if not wanted:
                return None
            ranked: list[tuple[float, str, dict[str, Any]]] = []
            for candidate in terms:
                if (
                    candidate["dimension"] != dimension
                    or candidate["status"] != _ACTIVE_TERM_STATUS
                ):
                    continue
                candidate_pairs = _bigrams(candidate["name"])
                union = wanted | candidate_pairs
                similarity = (
                    len(wanted & candidate_pairs) / len(union)
                    if union
                    else 0.0
                )
                ranked.append(
                    (similarity, candidate["name"], candidate)
                )
            if not ranked:
                return None
            similarity, _name, candidate = max(
                ranked,
                key=lambda item: (item[0], item[1]),
            )
            return candidate if similarity >= 0.2 else None

        def reserve_free_slot(
            key: tuple[str, str],
            *,
            dimension: str,
            name: str,
            source_field: str,
        ) -> bool:
            if key in free_keys:
                return True
            limit = 1 if restricted else 2
            if len(free_keys) >= limit:
                proposal_overflow.append(
                    {
                        "dimension": dimension,
                        "name": name,
                        "source_field": source_field,
                    }
                )
                return False
            free_keys.add(key)
            return True

        def accept(
            dimension: str,
            term: Mapping[str, Any],
            *,
            source_field: str,
        ) -> None:
            if not any(
                existing["id"] == term["id"]
                for existing in accepted_terms[dimension]
            ):
                accepted_terms[dimension].append(_term_public(term))
            output_field = source_field
            source_dimension = _RAW_FIELD_DIMENSIONS.get(source_field)
            if source_dimension is not None and source_dimension != dimension:
                output_field = _DIMENSION_PRIMARY_FIELD[dimension]
            accepted_fields.setdefault(output_field, [])
            if term["name"] not in accepted_fields[output_field]:
                accepted_fields[output_field].append(term["name"])

        def exact_composite_terms(
            dimension: str,
            name: str,
        ) -> list[dict[str, Any]]:
            if dimension != "curriculum":
                return []
            parts = [
                part.strip()
                for part in re.split(r"[,，、;；\n]+", name)
                if part.strip()
            ]
            if len(parts) < 2:
                return []
            resolved: list[dict[str, Any]] = []
            for part in parts:
                term = alias_index.get((dimension, _normalized_name(part)))
                if term is None:
                    return []
                if not any(existing["id"] == term["id"] for existing in resolved):
                    resolved.append(term)
            return resolved if len(resolved) >= 2 else []

        def classify_one(
            dimension: str,
            name: str,
            reason: str,
            *,
            source_field: str,
            definition: str = "",
            nearest_id: str = "",
            why_not_reuse: str = "",
        ) -> None:
            key = (dimension, _normalized_name(name))
            if not key[1]:
                return
            term = alias_index.get(key)
            if term is None:
                term = legacy_index.get(key)
            if term is None and dimension == "method":
                thought_key = ("thought", key[1])
                term = alias_index.get(thought_key)
                if term is None:
                    term = legacy_index.get(thought_key)
            if term is not None:
                resolved_dimension = term["dimension"]
                within_candidates = (
                    not restricted
                    or term["id"] in allowed_ids[resolved_dimension]
                )
                if not within_candidates:
                    retrieval_misses.append(
                        {
                            "dimension": resolved_dimension,
                            "submitted_name": name,
                            "canonical_id": term["id"],
                            "canonical_name": term["name"],
                            "source_field": source_field,
                        }
                    )
                    if (
                        resolved_dimension == "knowledge"
                        and int(selected_catalog["revision"]) >= 4
                    ):
                        return
                accept(resolved_dimension, term, source_field=source_field)
                return
            # A value known in another controlled dimension is misplaced model
            # output, not a new term.  The only intentional cross-dimension
            # compatibility is the old combined method field handled above.
            if any(
                alias_index.get((other_dimension, key[1])) is not None
                or legacy_index.get((other_dimension, key[1])) is not None
                for other_dimension in ALLOWED_DIMENSIONS
                if other_dimension != dimension
            ):
                return
            composite_terms = exact_composite_terms(dimension, name)
            if composite_terms:
                for composite_term in composite_terms:
                    accept(
                        dimension,
                        composite_term,
                        source_field=source_field,
                    )
                return
            # A per-question model contract is closed for every optional
            # semantic dimension.  The compressed catalog deliberately sends
            # all method/thought/model/special-type choices, so an out-of-list
            # value is model drift rather than a new vocabulary proposal.
            # Knowledge keeps the single controlled escape hatch needed when
            # local retrieval misses a legitimate concept.
            if restricted and dimension != "knowledge":
                return
            if not reserve_free_slot(
                key,
                dimension=dimension,
                name=name,
                source_field=source_field,
            ):
                return
            nearest = nearest_term(dimension, name)
            unknown = unknown_by_key.get(key)
            if unknown is None:
                unknown_by_key[key] = {
                    "dimension": dimension,
                    "name": name,
                    "reason": reason,
                    "source_field": source_field,
                    "definition": definition,
                    "nearest_id": nearest_id
                    or (nearest["id"] if nearest is not None else ""),
                    "why_not_reuse": why_not_reuse,
                }
                return
            for field_name, replacement in (
                ("reason", reason),
                ("definition", definition),
                ("nearest_id", nearest_id),
                ("why_not_reuse", why_not_reuse),
            ):
                if replacement and not unknown[field_name]:
                    unknown[field_name] = replacement

        for field, value in raw_analysis.items():
            if field in _LEGACY_UNCONTROLLED_FIELDS:
                ignored_fields.append(field)
                continue
            dimension = _RAW_FIELD_DIMENSIONS.get(field)
            if dimension is not None:
                for name, reason in _flatten_values(value):
                    classify_one(
                        dimension, name, reason, source_field=field
                    )

        for proposal_field in ("proposed_tags", "proposed_terms"):
            proposed_terms = raw_analysis.get(proposal_field)
            if not isinstance(proposed_terms, Iterable) or isinstance(
                proposed_terms, (str, bytes, bytearray, Mapping)
            ):
                continue
            for item in proposed_terms:
                if not isinstance(item, Mapping):
                    continue
                dimension = _text(item.get("dimension"))
                if dimension not in _DIMENSION_SET:
                    continue
                values = _flatten_values(item)
                for name, reason in values:
                    classify_one(
                        dimension,
                        name,
                        reason,
                        source_field="proposed_tags",
                        definition=_text(item.get("definition")),
                        nearest_id=_text(item.get("nearest_id")),
                        why_not_reuse=_text(item.get("why_not_reuse")),
                    )

        accepted_analysis = {
            dimension: [term["name"] for term in accepted_terms[dimension]]
            for dimension in ALLOWED_DIMENSIONS
        }
        return {
            "accepted_analysis": accepted_analysis,
            "accepted_terms": accepted_terms,
            "accepted_fields": accepted_fields,
            "unknown": list(unknown_by_key.values()),
            "retrieval_misses": retrieval_misses,
            "proposal_overflow": proposal_overflow,
            "ignored_legacy_fields": sorted(set(ignored_fields)),
        }

    def constrain(
        self,
        raw_analysis: Mapping[str, Any],
        context: object = None,
    ) -> dict[str, Any]:
        if not isinstance(raw_analysis, Mapping):
            raise TaxonomyValidationError("raw_analysis must be an object")
        context_values = _context_mapping(context)
        persist = bool(context_values.get("persist_proposals", False))
        allowed_term_ids = context_values.get("allowed_term_ids")
        catalog_revision = context_values.get("knowledge_catalog_revision")
        if catalog_revision is not None and type(catalog_revision) is not int:
            raise TaxonomyValidationError(
                "knowledge_catalog_revision must be an integer"
            )
        catalog = (
            self._catalog_for_revision(catalog_revision)
            if catalog_revision is not None
            else self._catalog
        )
        if not persist:
            state = self._read_state(catalog=catalog)
            classified = self._classify(
                raw_analysis,
                state=state,
                allowed_term_ids=(
                    allowed_term_ids
                    if isinstance(allowed_term_ids, Mapping)
                    else None
                ),
                catalog=catalog,
            )
            suppressed_keys = {
                (item["dimension"], item["normalized_name"])
                for item in state["proposals"]
                if item["status"] != "pending"
            }
            proposals = [
                {
                    "id": _stable_id(
                        "proposal", item["dimension"], _normalized_name(item["name"])
                    ),
                    "dimension": item["dimension"],
                    "proposed_name": item["name"],
                    "definition": item["definition"],
                    "reason": item["reason"],
                    "nearest_id": item["nearest_id"],
                    "why_not_reuse": item["why_not_reuse"],
                    "status": "unpersisted",
                }
                for item in classified["unknown"]
                if (
                    item["dimension"],
                    _normalized_name(item["name"]),
                )
                not in suppressed_keys
            ]
            return self._constraint_result(
                classified, proposals=proposals, revision=state["revision"]
            )

        with _exclusive_state_lock(self.state_path):
            state = self._read_state_unlocked(catalog=catalog)
            classified = self._classify(
                raw_analysis,
                state=state,
                allowed_term_ids=(
                    allowed_term_ids
                    if isinstance(allowed_term_ids, Mapping)
                    else None
                ),
                catalog=catalog,
            )
            question_ref = _text(
                context_values.get("question_ref")
                or context_values.get("question_id")
            )
            model = _text(context_values.get("model"))
            expected_revision = context_values.get("expected_revision")
            if expected_revision is not None and type(expected_revision) is not int:
                raise TaxonomyValidationError("expected_revision must be an integer")
            operation_payload = {
                "kind": "constrain",
                "raw_analysis": dict(raw_analysis),
                "question_ref": question_ref,
                "model": model,
                "knowledge_catalog_revision": catalog_revision,
                "allowed_term_ids": (
                    {
                        dimension: sorted(
                            {
                                _text(value)
                                for value in allowed_term_ids.get(
                                    dimension, []
                                )
                                if _text(value)
                            }
                        )
                        for dimension in ALLOWED_DIMENSIONS
                    }
                    if isinstance(allowed_term_ids, Mapping)
                    else None
                ),
            }
            operation_fingerprint = _fingerprint(operation_payload)
            request_token = _text(context_values.get("request_token"))
            if not request_token:
                request_token = f"auto-constrain-{operation_fingerprint[:24]}"
            replay = self._operation_replay(
                state, request_token, operation_fingerprint
            )
            if replay is not None:
                return replay
            if not classified["unknown"]:
                return self._constraint_result(
                    classified, proposals=[], revision=state["revision"]
                )
            # Machine observations are merged under the file lock against the
            # latest state. A teacher decision made while the model was running
            # must not cause another paid model request or a stale-write failure.
            proposal_index = {
                (item["dimension"], item["normalized_name"]): item
                for item in state["proposals"]
            }
            now = _now()
            persisted: list[dict[str, Any]] = []
            state_changed = False
            for unknown in classified["unknown"]:
                proposal_changed = False
                normalized = _normalized_name(unknown["name"])
                key = (unknown["dimension"], normalized)
                proposal = proposal_index.get(key)
                if proposal is not None and proposal["status"] != "pending":
                    continue
                if proposal is None:
                    proposal = {
                        "id": _stable_id(
                            "proposal", unknown["dimension"], normalized
                        ),
                        "dimension": unknown["dimension"],
                        "proposed_name": unknown["name"],
                        "normalized_name": normalized,
                        "edited_name": None,
                        "aliases": [],
                        "definition": unknown["definition"],
                        "nearest_id": unknown["nearest_id"],
                        "why_not_reuse": unknown["why_not_reuse"],
                        "reasons": [],
                        "models": [],
                        "question_refs": [],
                        "occurrences": 0,
                        "status": "pending",
                        "resolved_term_id": None,
                        "resolved_term_ids": [],
                        "created_at": now,
                        "updated_at": now,
                        "reviewed_at": None,
                    }
                    state["proposals"].append(proposal)
                    proposal_index[key] = proposal
                    proposal_changed = True
                observation_ref = question_ref or f"request:{request_token}"
                if observation_ref not in proposal["question_refs"]:
                    proposal["question_refs"].append(observation_ref)
                    proposal["occurrences"] += 1
                    proposal_changed = True
                next_reasons = _unique_text(
                    [*proposal["reasons"], unknown["reason"]]
                )
                if next_reasons != proposal["reasons"]:
                    proposal["reasons"] = next_reasons
                    proposal_changed = True
                if unknown["definition"]:
                    if proposal["definition"] != unknown["definition"]:
                        proposal["definition"] = unknown["definition"]
                        proposal_changed = True
                if unknown["nearest_id"]:
                    if proposal["nearest_id"] != unknown["nearest_id"]:
                        proposal["nearest_id"] = unknown["nearest_id"]
                        proposal_changed = True
                if unknown["why_not_reuse"]:
                    if proposal["why_not_reuse"] != unknown["why_not_reuse"]:
                        proposal["why_not_reuse"] = unknown["why_not_reuse"]
                        proposal_changed = True
                next_models = _unique_text(
                    [*proposal["models"], model]
                )
                if next_models != proposal["models"]:
                    proposal["models"] = next_models
                    proposal_changed = True
                if proposal_changed:
                    proposal["updated_at"] = now
                    state_changed = True
                persisted.append(copy.deepcopy(proposal))
            if not persisted:
                return self._constraint_result(
                    classified,
                    proposals=[],
                    revision=state["revision"],
                )
            if not state_changed:
                return self._constraint_result(
                    classified,
                    proposals=persisted,
                    revision=state["revision"],
                )
            next_revision = state["revision"] + 1
            state["revision"] = next_revision
            result = self._constraint_result(
                classified, proposals=persisted, revision=next_revision
            )
            self._remember_operation(
                state, request_token, operation_fingerprint, result
            )
            state["base_catalog_revision"] = catalog["revision"]
            _write_state_atomic(
                self.state_path, state, catalog=catalog
            )
            return result

    @staticmethod
    def _constraint_result(
        classified: Mapping[str, Any],
        *,
        proposals: list[dict[str, Any]],
        revision: int,
    ) -> dict[str, Any]:
        accepted_count = sum(
            len(values)
            for values in classified["accepted_analysis"].values()
        )
        status = (
            "needs_review"
            if proposals
            else ("accepted" if accepted_count else "empty")
        )
        retrieval_misses = copy.deepcopy(
            classified.get("retrieval_misses", [])
        )
        proposal_overflow = copy.deepcopy(
            classified.get("proposal_overflow", [])
        )
        notes: list[str] = []
        if retrieval_misses:
            notes.append(
                f"本题有 {len(retrieval_misses)} 个正式标签未被本地候选召回，"
                "已按当前知识目录边界处理并保留审计记录。"
            )
        if proposal_overflow:
            notes.append(
                f"模型返回的候选外标签超过2个，已忽略 "
                f"{len(proposal_overflow)} 个多余值。"
            )
        return {
            "accepted_analysis": copy.deepcopy(classified["accepted_analysis"]),
            "accepted_terms": copy.deepcopy(classified["accepted_terms"]),
            "accepted_fields": copy.deepcopy(classified["accepted_fields"]),
            "proposals": copy.deepcopy(proposals),
            "retrieval_misses": retrieval_misses,
            "proposal_overflow": proposal_overflow,
            "ignored_legacy_fields": list(
                classified["ignored_legacy_fields"]
            ),
            "notes": notes,
            "status": status,
            "taxonomy_revision": revision,
        }

    def allocate_observation_sequences(
        self,
        *,
        generation_id: str,
        question_ids: Iterable[object],
    ) -> dict[str, int]:
        """Allocate immutable ordering before a full tag analysis starts."""

        generation = _required_string(generation_id, label="generation_id")
        questions = _unique_text(question_ids)
        if not questions:
            return {}
        with _exclusive_state_lock(self.state_path):
            state = self._read_state_unlocked()
            lifecycle = state["observation_lifecycle"]
            allocation = lifecycle["allocations"].setdefault(generation, {})
            changed = False
            for question_id in questions:
                if question_id in allocation:
                    continue
                allocation[question_id] = lifecycle["next_source_sequence"]
                lifecycle["next_source_sequence"] += 1
                changed = True
            if changed:
                _write_state_atomic(
                    self.state_path, state, catalog=self._catalog
                )
            return {
                question_id: int(allocation[question_id])
                for question_id in questions
            }

    def record_successful_observation(
        self,
        *,
        question_id: object,
        generation_id: str,
        proposal_ids: Iterable[object],
        taxonomy_revision: int,
        graph_release_id: str = "",
    ) -> dict[str, Any]:
        """Publish one complete successful observation, including an empty one."""

        question = _required_string(question_id, label="question_id")
        generation = _required_string(generation_id, label="generation_id")
        proposals = _unique_text(proposal_ids)
        if type(taxonomy_revision) is not int or taxonomy_revision < 0:
            raise TaxonomyValidationError(
                "taxonomy_revision must be a non-negative integer"
            )
        with _exclusive_state_lock(self.state_path):
            state = self._read_state_unlocked()
            lifecycle = state["observation_lifecycle"]
            sequence = (
                lifecycle["allocations"].get(generation, {}).get(question)
            )
            if type(sequence) is not int:
                raise TaxonomyValidationError(
                    "Observation sequence must be allocated before analysis"
                )
            proposal_ids_in_state = {
                str(item["id"]) for item in state["proposals"]
            }
            if not set(proposals).issubset(proposal_ids_in_state):
                raise TaxonomyValidationError(
                    "Observation contains an unknown proposal"
                )
            for observation in lifecycle["observations"]:
                if (
                    observation["question_id"] == question
                    and observation["generation_id"] == generation
                ):
                    if (
                        observation["proposal_ids"] != proposals
                        or observation["taxonomy_revision"] != taxonomy_revision
                        or observation["graph_release_id"]
                        != _text(graph_release_id)
                    ):
                        raise TaxonomyValidationError(
                            "Observation identity was reused with different content"
                        )
                    return copy.deepcopy(observation)

            current = next(
                (
                    item
                    for item in lifecycle["observations"]
                    if item["question_id"] == question and item["is_current"]
                ),
                None,
            )
            becomes_current = (
                current is None
                or int(sequence) > int(current["source_sequence"])
            )
            if becomes_current and current is not None:
                current["is_current"] = False
                current["superseded_by_generation_id"] = generation
            observation = {
                "question_id": question,
                "generation_id": generation,
                "source_sequence": int(sequence),
                "proposal_ids": proposals,
                "completed_at": _now(),
                "taxonomy_revision": taxonomy_revision,
                "graph_release_id": _text(graph_release_id),
                "is_current": becomes_current,
                "superseded_by_generation_id": None,
            }
            lifecycle["observations"].append(observation)
            if becomes_current:
                lifecycle["evidence_revision"] += 1
            _write_state_atomic(
                self.state_path, state, catalog=self._catalog
            )
            return copy.deepcopy(observation)

    def observation_snapshot(self) -> dict[str, Any]:
        catalog = self._prompt_catalog()
        state = self._read_state(catalog=catalog)
        lifecycle = state["observation_lifecycle"]
        release = _knowledge_release_prompt_contract(
            self.knowledge_graph_db_path,
            taxonomy_revision=int(catalog["revision"]),
        )
        refs_by_proposal: dict[str, list[int]] = {}
        for observation in lifecycle["observations"]:
            if not observation["is_current"]:
                continue
            try:
                question_id = int(observation["question_id"])
            except (TypeError, ValueError):
                continue
            if question_id <= 0:
                continue
            for proposal_id in observation["proposal_ids"]:
                refs_by_proposal.setdefault(proposal_id, []).append(question_id)
        return {
            "evidence_revision": int(lifecycle["evidence_revision"]),
            "taxonomy_revision": int(state["revision"]),
            "graph_release_id": str(release.get("release_id") or ""),
            "current_question_refs": {
                proposal_id: sorted(set(question_ids))
                for proposal_id, question_ids in refs_by_proposal.items()
            },
        }

    def read_audit_history(
        self,
        *,
        question_id: object | None = None,
    ) -> dict[str, Any]:
        state = self._read_state()
        wanted = _text(question_id)
        observations = [
            copy.deepcopy(item)
            for item in state["observation_lifecycle"]["observations"]
            if not wanted or item["question_id"] == wanted
        ]
        observations.sort(
            key=lambda item: (item["source_sequence"], item["generation_id"]),
            reverse=True,
        )
        return {
            "evidence_revision": state["observation_lifecycle"][
                "evidence_revision"
            ],
            "items": observations,
        }

    def legacy_observation_reconciliation_preview(self) -> dict[str, Any]:
        """Keep unproven legacy refs in audit only; never guess current state."""

        state = self._read_state()
        observed = {
            proposal_id
            for item in state["observation_lifecycle"]["observations"]
            for proposal_id in item["proposal_ids"]
        }
        items = [
            {
                "proposal_id": proposal["id"],
                "historical_question_refs": _proposal_public(proposal)[
                    "question_refs"
                ],
                "classification": "historical_unproven",
            }
            for proposal in state["proposals"]
            if proposal["id"] not in observed and proposal["question_refs"]
        ]
        return {
            "read_only": True,
            "evidence_revision": state["observation_lifecycle"][
                "evidence_revision"
            ],
            "items": items,
        }

    def list_proposals(
        self,
        *,
        status: str | None = "pending",
        dimension: str | None = None,
    ) -> dict[str, Any]:
        if status is not None and status not in _PROPOSAL_STATUSES:
            raise TaxonomyValidationError("Unsupported proposal status")
        if dimension is not None and dimension not in _DIMENSION_SET:
            raise TaxonomyValidationError("Unsupported proposal dimension")
        state = self._read_state()
        items = [
            copy.deepcopy(proposal)
            for proposal in state["proposals"]
            if (status is None or proposal["status"] == status)
            and (dimension is None or proposal["dimension"] == dimension)
        ]
        items.sort(
            key=lambda proposal: (
                proposal["updated_at"],
                proposal["id"],
            ),
            reverse=True,
        )
        observation = self.observation_snapshot()
        public_items = []
        for item in items:
            public = _proposal_public(item)
            current_refs = observation["current_question_refs"].get(
                public["id"], []
            )
            public["active_question_refs"] = current_refs
            public_items.append(public)
        return {
            "revision": state["revision"],
            "evidence_revision": observation["evidence_revision"],
            "items": public_items,
            "counts": {
                "pending": sum(
                    item["status"] == "pending"
                    for item in state["proposals"]
                )
            },
        }

    def get_proposal(self, proposal_id: str) -> dict[str, Any] | None:
        wanted = _required_string(proposal_id, label="proposal_id")
        state = self._read_state()
        for proposal in state["proposals"]:
            if proposal["id"] == wanted:
                return _proposal_public(proposal)
        return None

    def review_proposal(
        self,
        *,
        proposal_id: str,
        decision: str,
        expected_revision: int,
        request_token: str,
        edited_name: str | None = None,
        target_term_id: str | None = None,
        target_term_ids: Iterable[object] = (),
        include_extended: bool = False,
    ) -> dict[str, Any]:
        """Apply the UI review contract without exposing state-file details."""

        normalized_target_ids = _unique_text(
            [*target_term_ids, target_term_id]
        )
        action = "approve" if decision == "edit" else decision
        if action == "merge" and len(normalized_target_ids) > 1:
            action = "map_many"
        if decision == "edit" and not _text(edited_name):
            raise TaxonomyReviewInvalid(
                "edited_name is required when approving a renamed term"
            )
        try:
            result = self.review(
                proposal_id,
                action,
                expected_revision=expected_revision,
                request_token=request_token,
                name=edited_name if decision == "edit" else None,
                target_term_id=target_term_id,
                target_term_ids=normalized_target_ids,
            )
        except TaxonomyRevisionConflict:
            raise
        except TaxonomyValidationError as exc:
            message = str(exc)
            if "Proposal does not exist" in message:
                raise TaxonomyProposalNotFound(message) from exc
            if "Merge target" in message:
                raise TaxonomyTargetTermNotFound(message) from exc
            raise TaxonomyReviewInvalid(message) from exc
        proposal = result.get("proposal")
        if not isinstance(proposal, Mapping):
            raise TaxonomyStorageError(
                "Taxonomy review completed without a proposal result"
            )
        approved_terms = [
            dict(item)
            for item in result.get("terms", [])
            if isinstance(item, Mapping)
        ]
        approved_term = result.get("term")
        if isinstance(approved_term, Mapping) and not approved_terms:
            approved_terms = [dict(approved_term)]
        response = {
            "revision": int(result["taxonomy_revision"]),
            "proposal": (
                _proposal_public_extended(proposal)
                if include_extended
                else _proposal_public(proposal)
            ),
            "approved_term": (
                dict(approved_term)
                if isinstance(approved_term, Mapping)
                else None
            ),
        }
        if include_extended:
            response["approved_terms"] = approved_terms
        return response

    def review_batch(
        self,
        *,
        commands: Iterable[Mapping[str, Any]],
        expected_revision: int,
        request_token: str,
    ) -> dict[str, Any]:
        """Apply a frozen group of proposal decisions with one revision step."""

        if type(expected_revision) is not int:
            raise TaxonomyValidationError("expected_revision must be an integer")
        token = _required_string(request_token, label="request_token")
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in commands:
            if not isinstance(raw, Mapping):
                raise TaxonomyReviewInvalid("Batch decision must be an object")
            proposal_id = _required_string(
                raw.get("proposal_id"), label="proposal_id"
            )
            if proposal_id in seen:
                raise TaxonomyReviewInvalid(
                    "A proposal can only be decided once per batch"
                )
            seen.add(proposal_id)
            decision = _required_string(raw.get("decision"), label="decision")
            if decision not in {"merge", "approve", "reject"}:
                raise TaxonomyReviewInvalid("Unsupported batch decision")
            normalized.append(
                {
                    "proposal_id": proposal_id,
                    "decision": decision,
                    "target_term_ids": _unique_text(
                        raw.get("target_term_ids", [])
                    ),
                    "edited_name": _text(raw.get("edited_name")),
                }
            )
        if not normalized:
            raise TaxonomyReviewInvalid("Batch has no decisions")
        operation_payload = {
            "kind": "review_batch",
            "expected_revision": expected_revision,
            "commands": normalized,
        }
        fingerprint = _fingerprint(operation_payload)
        with _exclusive_state_lock(self.state_path):
            state = self._read_state_unlocked()
            replay = self._operation_replay(state, token, fingerprint)
            if replay is not None:
                return replay
            if expected_revision != state["revision"]:
                raise TaxonomyRevisionConflict(
                    expected_revision, state["revision"]
                )
            terms, alias_index, _legacy_index = self._combined_terms(state)
            terms_by_id = {term["id"]: term for term in terms}
            proposals_by_id = {
                proposal["id"]: proposal for proposal in state["proposals"]
            }
            for command in normalized:
                proposal = proposals_by_id.get(command["proposal_id"])
                if proposal is None or proposal["status"] != "pending":
                    raise TaxonomyProposalNotFound(
                        "Batch proposal is not currently pending"
                    )
                targets = [
                    terms_by_id.get(target_id)
                    for target_id in command["target_term_ids"]
                ]
                if command["decision"] == "merge":
                    if len(targets) != 1:
                        raise TaxonomyReviewInvalid(
                            "Batch merge requires exactly one target"
                        )
                    target = targets[0]
                    if (
                        target is None
                        or target["status"] != _ACTIVE_TERM_STATUS
                        or target["dimension"] != proposal["dimension"]
                    ):
                        raise TaxonomyTargetTermNotFound(
                            "Batch merge target must be active and in the same dimension"
                        )
                elif command["target_term_ids"]:
                    raise TaxonomyReviewInvalid(
                        "Only a merge decision may contain a target"
                    )

            before = {
                "approved_terms": copy.deepcopy(state["approved_terms"]),
                "proposals": copy.deepcopy(state["proposals"]),
            }
            now = _now()
            results: list[dict[str, Any]] = []
            for command in normalized:
                proposal = proposals_by_id[command["proposal_id"]]
                approved_terms: list[dict[str, Any]] = []
                if command["decision"] == "merge":
                    target_id = command["target_term_ids"][0]
                    target = terms_by_id[target_id]
                    overlay = copy.deepcopy(target)
                    overlay["aliases"] = _unique_text(
                        [
                            *target["aliases"],
                            proposal["proposed_name"],
                            proposal["edited_name"],
                            *proposal["aliases"],
                        ]
                    )
                    overlay["origin"] = "teacher"
                    overlay.setdefault("created_at", now)
                    overlay["updated_at"] = now
                    self._assert_aliases_available(
                        overlay, alias_index, owner_id=target_id
                    )
                    self._upsert_overlay_term(state, overlay)
                    for value in (overlay["name"], *overlay["aliases"]):
                        alias_index[
                            (overlay["dimension"], _normalized_name(value))
                        ] = overlay
                    proposal["status"] = "merged"
                    proposal["resolved_term_id"] = target_id
                    proposal["resolved_term_ids"] = [target_id]
                    approved_terms = [_term_public(overlay)]
                elif command["decision"] == "approve":
                    approved_name = _required_string(
                        command["edited_name"] or proposal["proposed_name"],
                        label="approved_name",
                    )
                    if alias_index.get(
                        (proposal["dimension"], _normalized_name(approved_name))
                    ) is not None:
                        raise TaxonomyReviewInvalid(
                            "An active term already matches this name; use merge"
                        )
                    term = {
                        "id": _stable_id(
                            "local",
                            proposal["dimension"],
                            _normalized_name(approved_name),
                        ),
                        "dimension": proposal["dimension"],
                        "name": approved_name,
                        "aliases": [
                            value
                            for value in _unique_text(
                                [proposal["proposed_name"], proposal["edited_name"]]
                            )
                            if _normalized_name(value)
                            != _normalized_name(approved_name)
                        ],
                        "status": "approved",
                        "origin": "teacher",
                        "created_at": now,
                        "updated_at": now,
                    }
                    self._assert_aliases_available(term, alias_index)
                    self._upsert_overlay_term(state, term)
                    terms_by_id[term["id"]] = term
                    for value in (term["name"], *term["aliases"]):
                        alias_index[
                            (term["dimension"], _normalized_name(value))
                        ] = term
                    proposal["status"] = "approved"
                    proposal["resolved_term_id"] = term["id"]
                    proposal["resolved_term_ids"] = [term["id"]]
                    approved_terms = [_term_public(term)]
                else:
                    proposal["status"] = "rejected"
                    proposal["resolved_term_id"] = None
                    proposal["resolved_term_ids"] = []
                proposal["reviewed_at"] = now
                proposal["updated_at"] = now
                results.append(
                    {
                        "proposal": _proposal_public_extended(proposal),
                        "approved_terms": approved_terms,
                    }
                )
            state["revision"] += 1
            state["base_catalog_revision"] = self._catalog["revision"]
            operation_id = _stable_id("taxonomy-batch", token)
            result = {
                "operation_id": operation_id,
                "taxonomy_revision": state["revision"],
                "decisions": results,
                "_undo": before,
            }
            self._remember_operation(state, token, fingerprint, result)
            _write_state_atomic(self.state_path, state, catalog=self._catalog)
            return copy.deepcopy(result)

    def undo_review_batch(
        self,
        *,
        operation_id: str,
        expected_revision: int,
        request_token: str,
    ) -> dict[str, Any]:
        wanted = _required_string(operation_id, label="operation_id")
        token = _required_string(request_token, label="request_token")
        fingerprint = _fingerprint(
            {
                "kind": "undo_review_batch",
                "operation_id": wanted,
                "expected_revision": expected_revision,
            }
        )
        with _exclusive_state_lock(self.state_path):
            state = self._read_state_unlocked()
            replay = self._operation_replay(state, token, fingerprint)
            if replay is not None:
                return replay
            source = next(
                (
                    item
                    for item in state["applied_operations"]
                    if item.get("result", {}).get("operation_id") == wanted
                ),
                None,
            )
            if source is None:
                raise TaxonomyProposalNotFound("Batch operation does not exist")
            if (
                state["revision"] != expected_revision
                or source["applied_revision"] != expected_revision
            ):
                raise TaxonomyRevisionConflict(
                    expected_revision, state["revision"]
                )
            undo = source["result"].get("_undo")
            if not isinstance(undo, Mapping):
                raise TaxonomyReviewInvalid("Batch operation cannot be undone")
            state["approved_terms"] = copy.deepcopy(undo["approved_terms"])
            state["proposals"] = copy.deepcopy(undo["proposals"])
            state["revision"] += 1
            result = {
                "operation_id": wanted,
                "undone": True,
                "taxonomy_revision": state["revision"],
            }
            self._remember_operation(state, token, fingerprint, result)
            _write_state_atomic(self.state_path, state, catalog=self._catalog)
            return copy.deepcopy(result)

    def review(
        self,
        item_id: str,
        action: str,
        *,
        expected_revision: int,
        request_token: str,
        name: str | None = None,
        aliases: Iterable[object] = (),
        target_term_id: str | None = None,
        target_term_ids: Iterable[object] = (),
    ) -> dict[str, Any]:
        item_id = _required_string(item_id, label="item_id")
        action = _required_string(action, label="action")
        if action not in _REVIEW_ACTIONS:
            raise TaxonomyValidationError("Unsupported taxonomy review action")
        if type(expected_revision) is not int:
            raise TaxonomyValidationError("expected_revision must be an integer")
        request_token = _required_string(
            request_token, label="request_token"
        )
        alias_values = _unique_text(aliases)
        normalized_target_ids = _unique_text(
            [*target_term_ids, target_term_id]
        )
        operation_payload = {
            "kind": "review",
            "item_id": item_id,
            "action": action,
            "expected_revision": expected_revision,
            "name": _text(name),
            "aliases": alias_values,
            "target_term_id": _text(target_term_id),
            "target_term_ids": normalized_target_ids,
        }
        operation_fingerprint = _fingerprint(operation_payload)
        with _exclusive_state_lock(self.state_path):
            state = self._read_state_unlocked()
            replay = self._operation_replay(
                state, request_token, operation_fingerprint
            )
            if replay is not None:
                return replay
            if expected_revision != state["revision"]:
                raise TaxonomyRevisionConflict(
                    expected_revision, state["revision"]
                )
            terms, alias_index, _legacy_index = self._combined_terms(state)
            terms_by_id = {term["id"]: term for term in terms}
            proposals_by_id = {
                proposal["id"]: proposal for proposal in state["proposals"]
            }
            now = _now()
            result: dict[str, Any]

            if action == "retire":
                target = terms_by_id.get(item_id)
                if target is None:
                    raise TaxonomyValidationError("Term to retire does not exist")
                overlay = copy.deepcopy(target)
                overlay["status"] = "retired"
                overlay["origin"] = "teacher"
                overlay.setdefault("created_at", now)
                overlay["updated_at"] = now
                self._upsert_overlay_term(state, overlay)
                result = {
                    "action": action,
                    "term": _term_public(overlay),
                    "proposal": None,
                }
            else:
                proposal = proposals_by_id.get(item_id)
                if proposal is None:
                    raise TaxonomyValidationError("Proposal does not exist")
                if action == "edit":
                    edited_name = _required_string(name, label="name")
                    proposal["edited_name"] = edited_name
                    proposal["aliases"] = alias_values
                    proposal["updated_at"] = now
                    if proposal["status"] != "pending":
                        proposal["status"] = "pending"
                        proposal["resolved_term_id"] = None
                        proposal["resolved_term_ids"] = []
                        proposal["reviewed_at"] = None
                    result = {
                        "action": action,
                        "term": None,
                        "proposal": copy.deepcopy(proposal),
                    }
                elif action == "reject":
                    proposal["status"] = "rejected"
                    proposal["resolved_term_id"] = None
                    proposal["resolved_term_ids"] = []
                    proposal["reviewed_at"] = now
                    proposal["updated_at"] = now
                    result = {
                        "action": action,
                        "term": None,
                        "proposal": copy.deepcopy(proposal),
                    }
                elif action in {"merge", "map_many"}:
                    if action == "merge" and len(normalized_target_ids) != 1:
                        raise TaxonomyValidationError(
                            "Merge requires exactly one target term"
                        )
                    if action == "map_many" and len(normalized_target_ids) < 2:
                        raise TaxonomyValidationError(
                            "Multi-target merge requires at least two target terms"
                        )
                    targets = [
                        terms_by_id.get(target_id)
                        for target_id in normalized_target_ids
                    ]
                    if any(
                        target is None
                        or target["status"] != _ACTIVE_TERM_STATUS
                        or target["dimension"] != proposal["dimension"]
                        for target in targets
                    ):
                        raise TaxonomyValidationError(
                            "Merge target must be an approved term in the same dimension"
                        )
                    public_targets: list[dict[str, Any]] = []
                    for target_id, target in zip(
                        normalized_target_ids,
                        targets,
                        strict=True,
                    ):
                        assert target is not None
                        if action == "merge":
                            overlay = copy.deepcopy(target)
                            overlay["aliases"] = _unique_text(
                                [
                                    *target["aliases"],
                                    proposal["proposed_name"],
                                    proposal["edited_name"],
                                    *proposal["aliases"],
                                    *alias_values,
                                ]
                            )
                            overlay["origin"] = "teacher"
                            overlay.setdefault("created_at", now)
                            overlay["updated_at"] = now
                            self._assert_aliases_available(
                                overlay, alias_index, owner_id=target_id
                            )
                            self._upsert_overlay_term(state, overlay)
                            public_targets.append(_term_public(overlay))
                        else:
                            public_targets.append(_term_public(target))
                    proposal["status"] = "merged"
                    proposal["resolved_term_id"] = normalized_target_ids[0]
                    proposal["resolved_term_ids"] = normalized_target_ids
                    proposal["reviewed_at"] = now
                    proposal["updated_at"] = now
                    result = {
                        "action": action,
                        "term": public_targets[0],
                        "terms": public_targets,
                        "proposal": copy.deepcopy(proposal),
                    }
                else:  # approve
                    approved_name = _text(name or proposal["edited_name"])
                    if not approved_name:
                        approved_name = proposal["proposed_name"]
                    approved_name = _required_string(
                        approved_name, label="name"
                    )
                    existing = alias_index.get(
                        (
                            proposal["dimension"],
                            _normalized_name(approved_name),
                        )
                    )
                    if existing is not None:
                        raise TaxonomyValidationError(
                            "An approved term already matches this name; use merge"
                        )
                    term = {
                        "id": _stable_id(
                            "local",
                            proposal["dimension"],
                            _normalized_name(approved_name),
                        ),
                        "dimension": proposal["dimension"],
                        "name": approved_name,
                        "aliases": _unique_text(
                            [
                                proposal["proposed_name"],
                                proposal["edited_name"],
                                *proposal["aliases"],
                                *alias_values,
                            ]
                        ),
                        "status": "approved",
                        "origin": "teacher",
                        "created_at": now,
                        "updated_at": now,
                    }
                    term["aliases"] = [
                        alias
                        for alias in term["aliases"]
                        if _normalized_name(alias)
                        != _normalized_name(approved_name)
                    ]
                    self._assert_aliases_available(term, alias_index)
                    self._upsert_overlay_term(state, term)
                    proposal["status"] = "approved"
                    proposal["resolved_term_id"] = term["id"]
                    proposal["resolved_term_ids"] = [term["id"]]
                    proposal["reviewed_at"] = now
                    proposal["updated_at"] = now
                    result = {
                        "action": action,
                        "term": _term_public(term),
                        "proposal": copy.deepcopy(proposal),
                    }

            state["revision"] += 1
            state["base_catalog_revision"] = self._catalog["revision"]
            result["taxonomy_revision"] = state["revision"]
            self._remember_operation(
                state, request_token, operation_fingerprint, result
            )
            _write_state_atomic(
                self.state_path, state, catalog=self._catalog
            )
            return copy.deepcopy(result)

    @staticmethod
    def _upsert_overlay_term(
        state: dict[str, Any], term: Mapping[str, Any]
    ) -> None:
        stored = {
            key: copy.deepcopy(term[key])
            for key in (
                "id",
                "dimension",
                "name",
                "aliases",
                "status",
                "origin",
                "created_at",
                "updated_at",
            )
            if key in term
        }
        for index, existing in enumerate(state["approved_terms"]):
            if existing["id"] == term["id"]:
                state["approved_terms"][index] = stored
                return
        state["approved_terms"].append(stored)

    @staticmethod
    def _assert_aliases_available(
        term: Mapping[str, Any],
        alias_index: Mapping[tuple[str, str], Mapping[str, Any]],
        *,
        owner_id: str | None = None,
    ) -> None:
        allowed_owner = owner_id or term["id"]
        for value in (term["name"], *term["aliases"]):
            existing = alias_index.get(
                (term["dimension"], _normalized_name(value))
            )
            if existing is not None and existing["id"] != allowed_owner:
                raise TaxonomyValidationError(
                    f"Alias already belongs to another approved term: {value}"
                )

    @staticmethod
    def _operation_replay(
        state: Mapping[str, Any],
        request_token: str,
        operation_fingerprint: str,
    ) -> dict[str, Any] | None:
        for operation in state["applied_operations"]:
            if operation["request_token"] != request_token:
                continue
            if operation["fingerprint"] != operation_fingerprint:
                raise TaxonomyValidationError(
                    "request_token was already used for a different operation"
                )
            return copy.deepcopy(operation["result"])
        return None

    @staticmethod
    def _remember_operation(
        state: dict[str, Any],
        request_token: str,
        operation_fingerprint: str,
        result: Mapping[str, Any],
    ) -> None:
        state["applied_operations"].append(
            {
                "request_token": request_token,
                "fingerprint": operation_fingerprint,
                "result": copy.deepcopy(dict(result)),
                "applied_revision": state["revision"],
            }
        )
        if len(state["applied_operations"]) > _MAX_OPERATION_RECEIPTS:
            state["applied_operations"] = state["applied_operations"][
                -_MAX_OPERATION_RECEIPTS:
            ]


@lru_cache(maxsize=1)
def get_taxonomy_governance() -> TaxonomyGovernance:
    """Return the process-wide controlled-taxonomy boundary."""

    return TaxonomyGovernance()
