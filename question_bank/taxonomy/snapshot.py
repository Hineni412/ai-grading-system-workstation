"""Immutable per-question taxonomy input captured before model analysis."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze(item) for key, item in value.items()}
        )
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: object) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class QuestionTaxonomySnapshot(Mapping[str, Any]):
    """A question-bound, deeply immutable copy of one taxonomy contract."""

    question_id: int
    _payload: Mapping[str, object] = field(repr=False)
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        question_id = int(self.question_id)
        if isinstance(self.question_id, bool) or question_id <= 0:
            raise ValueError("taxonomy snapshot question_id must be positive")
        payload = _thaw(self._payload)
        if not isinstance(payload, dict):
            raise TypeError("taxonomy snapshot payload must be a mapping")
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        normalized = json.loads(serialized)
        revision = normalized.get("taxonomy_revision", 0)
        if isinstance(revision, bool):
            raise ValueError("taxonomy snapshot revision must be an integer")
        try:
            normalized_revision = int(revision or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "taxonomy snapshot revision must be an integer"
            ) from exc
        if normalized_revision < 0:
            raise ValueError("taxonomy snapshot revision must not be negative")
        if "taxonomy_revision" in normalized:
            normalized["taxonomy_revision"] = normalized_revision
        serialized = json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        object.__setattr__(self, "question_id", question_id)
        object.__setattr__(self, "_payload", _freeze(normalized))
        object.__setattr__(
            self,
            "content_hash",
            hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
        )

    @classmethod
    def capture(
        cls,
        question_id: int,
        contract: Mapping[str, Any] | QuestionTaxonomySnapshot | None,
    ) -> QuestionTaxonomySnapshot:
        if isinstance(contract, cls):
            if contract.question_id != int(question_id):
                raise ValueError("taxonomy snapshot belongs to another question")
            return contract
        return cls(question_id=int(question_id), _payload=dict(contract or {}))

    def __getitem__(self, key: str) -> Any:
        return _thaw(self._payload[key])

    def __iter__(self) -> Iterator[str]:
        return iter(self._payload)

    def __len__(self) -> int:
        return len(self._payload)

    @property
    def taxonomy_revision(self) -> int:
        return int(self._payload.get("taxonomy_revision") or 0)

    @property
    def knowledge_graph_release_id(self) -> str:
        return str(self._payload.get("knowledge_graph_release_id") or "").strip()

    @property
    def candidate_fingerprint(self) -> str:
        return str(self._payload.get("candidate_fingerprint") or "").strip()

    def allowed_term_ids(self, dimension: str) -> tuple[str, ...]:
        allowed = self._payload.get("allowed_term_ids")
        if not isinstance(allowed, Mapping):
            return ()
        values = allowed.get(str(dimension or "").strip())
        if not isinstance(values, tuple):
            return ()
        return tuple(str(value) for value in values if str(value).strip())

    def to_dict(self) -> dict[str, Any]:
        return _thaw(self._payload)


__all__ = ["QuestionTaxonomySnapshot"]
