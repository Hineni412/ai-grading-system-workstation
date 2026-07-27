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


ALLOWED_DIMENSIONS = ("curriculum", "knowledge", "ability", "method", "model")
_DIMENSION_SET = frozenset(ALLOWED_DIMENSIONS)
_ACTIVE_TERM_STATUS = "approved"
_TERM_STATUSES = frozenset({"approved", "retired"})
_PROPOSAL_STATUSES = frozenset({"pending", "approved", "merged", "rejected"})
_REVIEW_ACTIONS = frozenset({"approve", "edit", "merge", "reject", "retire"})
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
        frozenset({"origin", "source_paths"})
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
                for key in ("id", "name", "value", "proposed_name")
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
    if not isinstance(context, Mapping):
        return {"text": _text(context)}
    return dict(context)


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
            by_id[term["id"]] = copy.deepcopy(term)
        terms = sorted(
            by_id.values(),
            key=lambda item: (
                ALLOWED_DIMENSIONS.index(item["dimension"]),
                item["name"],
                item["id"],
            ),
        )
        alias_index: dict[tuple[str, str], dict[str, Any]] = {}
        for term in terms:
            if term["status"] != _ACTIVE_TERM_STATUS:
                continue
            for value in (term["id"], term["name"], *term["aliases"]):
                key = (term["dimension"], _normalized_name(value))
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
        """Return the public five-dimension catalog used by the review UI."""

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

    def prompt_contract(self, context: object = None) -> dict[str, Any]:
        state = self._read_state()
        terms, _ = self._combined_terms(state)
        query = _normalized_name(_search_text(context))
        limits = {
            "curriculum": 40,
            "knowledge": 120,
            "ability": 20,
            "method": 50,
            "model": 100,
        }

        def score(term: Mapping[str, Any]) -> tuple[int, int, str]:
            values = [_normalized_name(term["name"])]
            values.extend(_normalized_name(alias) for alias in term["aliases"])
            if not query:
                return (0, 0, term["name"])
            exact = max((100 if value and value in query else 0) for value in values)
            query_pairs = {
                query[index : index + 2]
                for index in range(max(0, len(query) - 1))
            }
            overlap = max(
                (
                    len(
                        query_pairs
                        & {
                            value[index : index + 2]
                            for index in range(max(0, len(value) - 1))
                        }
                    )
                    for value in values
                ),
                default=0,
            )
            return (exact, overlap, term["name"])

        candidates: dict[str, list[dict[str, Any]]] = {}
        truncated: dict[str, bool] = {}
        for dimension in ALLOWED_DIMENSIONS:
            dimension_terms = [
                term
                for term in terms
                if term["dimension"] == dimension
                and term["status"] == _ACTIVE_TERM_STATUS
            ]
            ranked = sorted(
                dimension_terms,
                key=lambda term: (
                    -score(term)[0],
                    -score(term)[1],
                    score(term)[2],
                ),
            )
            limit = limits[dimension]
            selected = ranked[:limit]
            truncated[dimension] = len(ranked) > len(selected)
            candidates[dimension] = [
                {
                    "id": term["id"],
                    "name": term["name"],
                }
                for term in selected
            ]
        return {
            "schema_version": 1,
            "taxonomy_revision": state["revision"],
            "allowed_dimensions": list(ALLOWED_DIMENSIONS),
            "candidates": candidates,
            "truncated": truncated,
            "rules": {
                "selection": (
                    "每个标签只能从对应维度候选词的 id/name 中选择；"
                    "不得把知识点、能力、方法和模型互相混放。"
                ),
                "curriculum": "教材归属只选择年级上下册及章，不自由描述教学阶段。",
                "unknown": (
                    "确无匹配时单独返回 proposed_tags，包含 dimension、name、reason；"
                    "新词未经教师批准不得作为正式标签保存。"
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
                "proposed_tags": [
                    {"dimension": "knowledge", "name": "新词", "reason": "原因"}
                ],
            },
        }

    def _classify(
        self,
        raw_analysis: Mapping[str, Any],
        *,
        state: Mapping[str, Any],
    ) -> dict[str, Any]:
        _, alias_index = self._combined_terms(state)
        accepted_terms = {dimension: [] for dimension in ALLOWED_DIMENSIONS}
        accepted_fields: dict[str, list[str]] = {}
        unknown_by_key: dict[tuple[str, str], dict[str, str]] = {}
        ignored_fields: list[str] = []

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
                if not any(
                    existing["id"] == term["id"]
                    for existing in accepted_terms[dimension]
                ):
                    accepted_terms[dimension].append(_term_public(term))
                accepted_fields.setdefault(source_field, [])
                if term["name"] not in accepted_fields[source_field]:
                    accepted_fields[source_field].append(term["name"])
                return
            unknown_by_key.setdefault(
                key,
                {
                    "dimension": dimension,
                    "name": name,
                    "reason": reason,
                    "source_field": source_field,
                    "definition": definition,
                    "nearest_id": nearest_id,
                    "why_not_reuse": why_not_reuse,
                },
            )

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
        if not persist:
            state = self._read_state()
            classified = self._classify(raw_analysis, state=state)
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
            classified = self._classify(raw_analysis, state=state)
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
            if (
                expected_revision is not None
                and expected_revision != state["revision"]
            ):
                raise TaxonomyRevisionConflict(
                    expected_revision, state["revision"]
                )
            proposal_index = {
                (item["dimension"], item["normalized_name"]): item
                for item in state["proposals"]
            }
            now = _now()
            persisted: list[dict[str, Any]] = []
            for unknown in classified["unknown"]:
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
                        "created_at": now,
                        "updated_at": now,
                        "reviewed_at": None,
                    }
                    state["proposals"].append(proposal)
                    proposal_index[key] = proposal
                proposal["occurrences"] += 1
                proposal["reasons"] = _unique_text(
                    [*proposal["reasons"], unknown["reason"]]
                )
                if unknown["definition"]:
                    proposal["definition"] = unknown["definition"]
                if unknown["nearest_id"]:
                    proposal["nearest_id"] = unknown["nearest_id"]
                if unknown["why_not_reuse"]:
                    proposal["why_not_reuse"] = unknown["why_not_reuse"]
                proposal["models"] = _unique_text(
                    [*proposal["models"], model]
                )
                proposal["question_refs"] = _unique_text(
                    [*proposal["question_refs"], question_ref]
                )
                proposal["updated_at"] = now
                persisted.append(copy.deepcopy(proposal))
            if not persisted:
                return self._constraint_result(
                    classified,
                    proposals=[],
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
        return {
            "accepted_analysis": copy.deepcopy(classified["accepted_analysis"]),
            "accepted_terms": copy.deepcopy(classified["accepted_terms"]),
            "accepted_fields": copy.deepcopy(classified["accepted_fields"]),
            "proposals": copy.deepcopy(proposals),
            "ignored_legacy_fields": list(
                classified["ignored_legacy_fields"]
            ),
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

    def review_proposal(
        self,
        *,
        proposal_id: str,
        decision: str,
        edited_name: str | None,
        target_term_id: str | None,
        expected_revision: int,
        request_token: str,
    ) -> dict[str, Any]:
        """Apply the UI review contract without exposing state-file details."""

        action = "approve" if decision == "edit" else decision
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
        approved_term = result.get("term")
        return {
            "revision": int(result["taxonomy_revision"]),
            "proposal": _proposal_public(proposal),
            "approved_term": (
                dict(approved_term)
                if isinstance(approved_term, Mapping)
                else None
            ),
        }

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
        operation_payload = {
            "kind": "review",
            "item_id": item_id,
            "action": action,
            "expected_revision": expected_revision,
            "name": _text(name),
            "aliases": alias_values,
            "target_term_id": _text(target_term_id),
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
                        proposal["reviewed_at"] = None
                    result = {
                        "action": action,
                        "term": None,
                        "proposal": copy.deepcopy(proposal),
                    }
                elif action == "reject":
                    proposal["status"] = "rejected"
                    proposal["resolved_term_id"] = None
                    proposal["reviewed_at"] = now
                    proposal["updated_at"] = now
                    result = {
                        "action": action,
                        "term": None,
                        "proposal": copy.deepcopy(proposal),
                    }
                elif action == "merge":
                    target_id = _required_string(
                        target_term_id, label="target_term_id"
                    )
                    target = terms_by_id.get(target_id)
                    if (
                        target is None
                        or target["status"] != _ACTIVE_TERM_STATUS
                        or target["dimension"] != proposal["dimension"]
                    ):
                        raise TaxonomyValidationError(
                            "Merge target must be an approved term in the same dimension"
                        )
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
                    proposal["status"] = "merged"
                    proposal["resolved_term_id"] = target_id
                    proposal["reviewed_at"] = now
                    proposal["updated_at"] = now
                    result = {
                        "action": action,
                        "term": _term_public(overlay),
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
