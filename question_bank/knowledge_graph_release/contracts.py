from __future__ import annotations

import copy
import hashlib
import json
import threading
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "knowledge-graph-release-v1"


class KnowledgeGraphReleaseError(ValueError):
    """Raised when a release document cannot safely be used."""


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    code: str
    path: str
    message: str
    severity: str = "error"

    def __post_init__(self) -> None:
        if self.severity not in {"error", "warning"}:
            raise ValueError("validation issue severity is invalid")

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "path": self.path,
            "message": self.message,
            "severity": self.severity,
        }


@dataclass(frozen=True, slots=True)
class ValidationReport:
    release_id: str
    issues: tuple[ValidationIssue, ...]

    @property
    def valid(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)

    @property
    def errors(self) -> tuple[ValidationIssue, ...]:
        return tuple(
            issue for issue in self.issues if issue.severity == "error"
        )

    @property
    def warnings(self) -> tuple[ValidationIssue, ...]:
        return tuple(
            issue for issue in self.issues if issue.severity == "warning"
        )

    def raise_for_errors(self) -> None:
        if not self.valid:
            summary = "; ".join(
                f"{issue.path}: {issue.message}" for issue in self.errors[:8]
            )
            raise KnowledgeGraphReleaseError(summary)

    def to_dict(self) -> dict[str, Any]:
        return {
            "release_id": self.release_id,
            "valid": self.valid,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass(frozen=True, slots=True)
class KnowledgeGraphRelease:
    """An immutable, content-addressed knowledge graph release document."""

    payload: dict[str, Any]
    content_hash: str

    @classmethod
    def from_mapping(
        cls,
        raw: Mapping[str, Any],
    ) -> KnowledgeGraphRelease:
        if not isinstance(raw, Mapping):
            raise KnowledgeGraphReleaseError("release document must be an object")
        payload = copy.deepcopy(dict(raw))
        provided_hash = str(payload.get("content_hash") or "").strip().lower()
        computed_hash = compute_content_hash(payload)
        if provided_hash and provided_hash != computed_hash:
            raise KnowledgeGraphReleaseError(
                "release content_hash does not match canonical content"
            )
        payload["content_hash"] = computed_hash
        return cls(payload=payload, content_hash=computed_hash)

    @classmethod
    def from_path(cls, path: Path) -> KnowledgeGraphRelease:
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise KnowledgeGraphReleaseError(
                f"cannot read knowledge graph release: {exc}"
            ) from exc
        return cls.from_mapping(raw)

    @property
    def release_id(self) -> str:
        return str(self.payload.get("release_id") or "").strip()

    @property
    def schema_version(self) -> str:
        return str(self.payload.get("schema_version") or "").strip()

    @property
    def taxonomy_revision(self) -> int:
        value = self.payload.get("taxonomy_revision")
        if isinstance(value, bool):
            return -1
        try:
            return int(value)
        except (TypeError, ValueError):
            return -1

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self.payload)

    def canonical_json(self) -> str:
        return json.dumps(
            self.payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )


def compute_content_hash(payload: Mapping[str, Any]) -> str:
    canonical = copy.deepcopy(dict(payload))
    canonical.pop("content_hash", None)
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def stable_record_hash(namespace: str, *parts: object) -> str:
    encoded = json.dumps(
        [str(namespace or "").strip(), *(str(part or "").strip() for part in parts)],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


_RELEASE_CACHE_LIMIT = 8
_RELEASE_CACHE_LOCK = threading.Lock()
_RELEASE_CACHE: OrderedDict[tuple[str, str], KnowledgeGraphRelease] = OrderedDict()


def cached_release_from_json(
    payload_json: str,
    *,
    release_id: str,
    content_hash: str,
) -> KnowledgeGraphRelease:
    """Return the parsed release for a known immutable identity.

    Releases are content-addressed, so a repeated load of the same
    (release_id, content_hash) reuses one shared object. Callers must treat the
    returned payload as read-only; use ``release.to_dict()`` for a mutable copy.
    """
    key = (
        str(release_id or "").strip(),
        str(content_hash or "").strip().casefold(),
    )
    with _RELEASE_CACHE_LOCK:
        cached = _RELEASE_CACHE.get(key)
        if cached is not None:
            _RELEASE_CACHE.move_to_end(key)
            return cached
        release = KnowledgeGraphRelease.from_mapping(json.loads(str(payload_json)))
        if (release.release_id, release.content_hash.casefold()) != key:
            raise KnowledgeGraphReleaseError(
                "release payload does not match its recorded identity"
            )
        _RELEASE_CACHE[key] = release
        _RELEASE_CACHE.move_to_end(key)
        while len(_RELEASE_CACHE) > _RELEASE_CACHE_LIMIT:
            _RELEASE_CACHE.popitem(last=False)
        return release


def _register_release(release: KnowledgeGraphRelease) -> None:
    key = (release.release_id, release.content_hash.casefold())
    with _RELEASE_CACHE_LOCK:
        _RELEASE_CACHE[key] = release
        _RELEASE_CACHE.move_to_end(key)
        while len(_RELEASE_CACHE) > _RELEASE_CACHE_LIMIT:
            _RELEASE_CACHE.popitem(last=False)


def _clear_release_cache_for_tests() -> None:
    with _RELEASE_CACHE_LOCK:
        _RELEASE_CACHE.clear()


__all__ = [
    "KnowledgeGraphRelease",
    "KnowledgeGraphReleaseError",
    "SCHEMA_VERSION",
    "ValidationIssue",
    "ValidationReport",
    "cached_release_from_json",
    "compute_content_hash",
    "stable_record_hash",
]
