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
from question_bank.taxonomy.curriculum_catalog import curriculum_volume_contract


ALLOWED_DIMENSIONS = (
    "curriculum",
    "knowledge",
    "ability",
    "method",
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
_CATALOG_PATH = (
    Path(__file__).resolve().parent / "catalogs" / "tag_vocabulary_v2.json"
)
_PROCESS_LOCK = threading.RLock()
_LOCK_TIMEOUT_SECONDS = 10.0
_MAX_OPERATION_RECEIPTS = 5000
_MAX_TERM_NAME_LENGTH = 36

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
    "model": "model",
    "math_model": "model",
    "math_models": "model",
    "math_model_tags": "model",
    "special_type": "special_type",
    "special_type_tag": "special_type",
    "special_type_tags": "special_type",
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
        frozenset({"origin", "source_paths", "retrieval_hints"})
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
    return {
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


def _empty_state(catalog: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "base_catalog_id": catalog["catalog_id"],
        "base_catalog_revision": catalog["revision"],
        "revision": 0,
        "approved_terms": [],
        "proposals": [],
        "applied_operations": [],
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
    ) -> None:
        self.catalog_path = Path(catalog_path or _CATALOG_PATH)
        self.state_path = Path(
            state_path or get_path_manager().taxonomy_state_path
        )
        try:
            catalog_payload = _read_json(self.catalog_path)
            self._catalog = _validate_catalog(
                catalog_payload, source=self.catalog_path
            )
        except (OSError, json.JSONDecodeError, TaxonomyValidationError) as exc:
            raise TaxonomyStorageError(
                f"Controlled taxonomy catalog is unavailable: {self.catalog_path}"
            ) from exc

    def _read_state_unlocked(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return _empty_state(self._catalog)
        failures: list[Exception] = []
        for candidate in (self.state_path, _backup_path(self.state_path)):
            if not candidate.is_file():
                continue
            try:
                return _validate_state(
                    _read_json(candidate),
                    catalog=self._catalog,
                    source=candidate,
                )
            except (OSError, json.JSONDecodeError, TaxonomyValidationError) as exc:
                failures.append(exc)
        raise TaxonomyStorageError(
            "Taxonomy state and backup are both unavailable or invalid"
        ) from (failures[-1] if failures else None)

    def _read_state(self) -> dict[str, Any]:
        with _PROCESS_LOCK:
            return self._read_state_unlocked()

    def _combined_terms(
        self, state: Mapping[str, Any]
    ) -> tuple[list[dict[str, Any]], dict[tuple[str, str], dict[str, Any]]]:
        by_id = {
            term["id"]: copy.deepcopy(term)
            for term in self._catalog["terms"]
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
        return terms, alias_index

    def snapshot(self) -> dict[str, Any]:
        state = self._read_state()
        terms, _ = self._combined_terms(state)
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
            "base_catalog_id": self._catalog["catalog_id"],
            "base_catalog_revision": self._catalog["revision"],
            "allowed_dimensions": list(ALLOWED_DIMENSIONS),
            "terms_by_dimension": active_by_dimension,
            "retired_terms": retired,
            "pending_proposal_count": sum(
                proposal["status"] == "pending"
                for proposal in state["proposals"]
            ),
            "reference_candidate_count": len(
                self._catalog["reference_candidates"]
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
        self, dimension: str, value: object
    ) -> dict[str, Any] | None:
        dimension = _text(dimension)
        if dimension not in _DIMENSION_SET:
            return None
        state = self._read_state()
        _, alias_index = self._combined_terms(state)
        term = alias_index.get((dimension, _normalized_name(value)))
        return _term_public(term) if term is not None else None

    def expand_filter_values(
        self, dimension: str, canonical_values: Iterable[object]
    ) -> tuple[str, ...]:
        dimension = _text(dimension)
        if dimension not in _DIMENSION_SET:
            return ()
        state = self._read_state()
        _, alias_index = self._combined_terms(state)
        expanded: list[str] = []
        for value in canonical_values:
            term = alias_index.get((dimension, _normalized_name(value)))
            if term is None:
                text = _text(value)
                if text:
                    expanded.append(text)
                continue
            expanded.extend([term["name"], *term["aliases"]])
        return tuple(_unique_text(expanded))

    def prompt_contracts(
        self,
        contexts: Mapping[object, object],
    ) -> dict[object, dict[str, Any]]:
        """Build isolated per-question candidate contracts from one snapshot."""

        state = self._read_state()
        terms, _alias_index = self._combined_terms(state)
        active_by_dimension = {
            dimension: [
                term
                for term in terms
                if term["dimension"] == dimension
                and term["status"] == _ACTIVE_TERM_STATUS
            ]
            for dimension in ALLOWED_DIMENSIONS
        }
        return {
            key: self._prompt_contract_from_snapshot(
                context,
                revision=state["revision"],
                active_by_dimension=active_by_dimension,
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
    ) -> dict[str, Any]:
        values = _context_mapping(context)
        volume_contract = curriculum_volume_contract(
            values.get("curriculum_volume_id")
        )
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
            "knowledge": 32,
            "ability": 10,
            "method": 12,
            "model": 16,
            "special_type": 8,
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
            elif dimension == "ability":
                eligible = ranked
            elif not has_query:
                eligible = []
            else:
                eligible = [item for item in ranked if item[0] > 0]
            selected = [term for _score, term in eligible[: limits[dimension]]]
            truncated[dimension] = len(eligible) > len(selected)
            candidates[dimension] = [
                {"id": term["id"], "name": term["name"]}
                for term in selected
            ]
            allowed_term_ids[dimension] = [term["id"] for term in selected]

        fingerprint = _fingerprint(
            {
                "revision": revision,
                "allowed_term_ids": allowed_term_ids,
            }
        )
        retrieval_status = (
            "ok" if candidates["knowledge"] else "insufficient"
        )
        result = {
            "schema_version": 1,
            "taxonomy_revision": revision,
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
                "unknown": (
                    "候选中确无匹配时可返回 proposed_tags；每题最多2个。"
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
    ) -> dict[str, Any]:
        terms, alias_index = self._combined_terms(state)
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
            if len(free_keys) >= 2:
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
            accepted_fields.setdefault(source_field, [])
            if term["name"] not in accepted_fields[source_field]:
                accepted_fields[source_field].append(term["name"])

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
            if term is not None:
                within_candidates = (
                    not restricted
                    or term["id"] in allowed_ids[dimension]
                )
                accept(dimension, term, source_field=source_field)
                if not within_candidates:
                    retrieval_misses.append(
                        {
                            "dimension": dimension,
                            "submitted_name": name,
                            "canonical_id": term["id"],
                            "canonical_name": term["name"],
                            "source_field": source_field,
                        }
                    )
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
        if not persist:
            state = self._read_state()
            classified = self._classify(
                raw_analysis,
                state=state,
                allowed_term_ids=(
                    allowed_term_ids
                    if isinstance(allowed_term_ids, Mapping)
                    else None
                ),
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
            state = self._read_state_unlocked()
            classified = self._classify(
                raw_analysis,
                state=state,
                allowed_term_ids=(
                    allowed_term_ids
                    if isinstance(allowed_term_ids, Mapping)
                    else None
                ),
            )
            if not classified["unknown"]:
                return self._constraint_result(
                    classified, proposals=[], revision=state["revision"]
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
            state["base_catalog_revision"] = self._catalog["revision"]
            _write_state_atomic(
                self.state_path, state, catalog=self._catalog
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
                "已从完整词表归并。"
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
        return {
            "revision": state["revision"],
            "items": [_proposal_public(item) for item in items],
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
            terms, alias_index = self._combined_terms(state)
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
