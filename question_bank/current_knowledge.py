from __future__ import annotations

import json
import sqlite3
import threading
from collections import OrderedDict, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from question_bank.knowledge_graph_release.contracts import (
    KnowledgeGraphRelease,
    KnowledgeGraphReleaseError,
    cached_release_from_json,
)
from question_bank.knowledge_graph_release.loader import (
    load_release,
    load_taxonomy_catalog_for_release,
)
from question_bank.knowledge_graph_release.repository import (
    KnowledgeGraphReleaseConflict,
    bootstrap_release,
    load_active_release,
)
from question_bank.knowledge_graph_release.validation import validate_release
from question_bank.relations.bootstrap import normalize_knowledge_alias
from question_bank.solution_evidence.contracts import CoreResolution

_RESOLVER_CACHE_LIMIT = 4
_RESOLVER_CACHE_LOCK = threading.Lock()


class CurrentKnowledgeUnavailable(RuntimeError):
    """The single current knowledge standard cannot be read safely."""

    def __init__(self, reason: str) -> None:
        self.reason = str(reason or "current_knowledge_unavailable")
        super().__init__(self.reason)


class CurrentFineTermResolver:
    """Adapt the one current standard to solution-evidence resolution."""

    def __init__(self, resolver: CurrentKnowledgeResolver) -> None:
        self.current = resolver
        self.release_id = resolver.release_id

    @classmethod
    def from_active_database(cls, db_path: Path) -> CurrentFineTermResolver:
        return cls(CurrentKnowledgeResolver.from_active_database(db_path))

    def resolve(self, fine_term_id: str) -> CoreResolution:
        matches = self.current.resolve(fine_term_id)
        stable_keys = tuple(
            dict.fromkeys(item.stable_key for item in matches)
        )
        if len(stable_keys) == 1:
            return CoreResolution(
                status="resolved",
                stable_keys=stable_keys,
                reason=f"current_release:{self.release_id}",
            )
        if stable_keys:
            return CoreResolution(
                status="ambiguous",
                stable_keys=stable_keys,
                reason=f"current_release:{self.release_id}:maps_to_many",
            )
        return CoreResolution(
            status="unmapped",
            reason=f"current_release:{self.release_id}:not_current",
        )


@dataclass(frozen=True, slots=True)
class CurrentKnowledgeNode:
    stable_key: str
    display_name: str
    definition: str
    include_scope: str
    exclude_scope: str
    curriculum_anchors: tuple[str, ...]
    observable_evidence: str
    rationale: str
    evidence_source_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CurrentKnowledgeRelation:
    relation_key: str
    source_key: str
    target_key: str
    relation_type: str
    rationale: str
    basis_kind: str
    strength: str
    evidence_source_ids: tuple[str, ...]
    source_locator: str


@dataclass(frozen=True, slots=True)
class ResolvedKnowledge:
    fine_term_id: str
    canonical_name: str
    stable_key: str
    display_name: str


class CurrentKnowledgeResolver:
    """Resolve all current knowledge identity rules behind one small interface.

    Canonical IDs and names are the public inputs. Registered aliases and legacy
    IDs/names are accepted only as exact, unambiguous compatibility inputs. The
    result always contains the current canonical term and an active core node.
    """

    def __init__(
        self,
        release: KnowledgeGraphRelease,
        taxonomy_catalog: Mapping[str, Any],
    ) -> None:
        report = validate_release(release, taxonomy_catalog)
        if not report.valid:
            raise CurrentKnowledgeUnavailable("current_knowledge_invalid")

        self.release_id = release.release_id
        self.content_hash = release.content_hash
        self.taxonomy_revision = release.taxonomy_revision
        payload = release.payload

        active_nodes = {
            str(item["stable_key"]).strip().casefold(): _node(item)
            for item in _objects(payload.get("core_nodes"))
            if str(item.get("status") or "").strip() == "active"
        }
        self._nodes = active_nodes
        self._relations = tuple(
            _relation(item)
            for item in _objects(payload.get("relations"))
            if str(item.get("source_key") or "").strip().casefold()
            in active_nodes
            and str(item.get("target_key") or "").strip().casefold()
            in active_nodes
        )

        terms = {
            str(item["id"]).strip().casefold(): item
            for item in _objects(taxonomy_catalog.get("terms"))
            if str(item.get("dimension") or "").strip() == "knowledge"
            and str(item.get("status") or "").strip() == "approved"
        }
        dispositions = {
            str(item["fine_term_id"]).strip().casefold(): item
            for item in _objects(payload.get("fine_term_dispositions"))
        }
        targets: dict[str, list[str]] = defaultdict(list)
        for item in _objects(payload.get("mappings")):
            term_id = str(item.get("fine_term_id") or "").strip().casefold()
            stable_key = str(item.get("stable_key") or "").strip().casefold()
            if stable_key in active_nodes and stable_key not in targets[term_id]:
                targets[term_id].append(stable_key)

        exact: dict[str, str] = {}
        compatibility: dict[str, set[str]] = defaultdict(set)
        storage_values: dict[str, set[str]] = defaultdict(set)
        term_names: dict[str, str] = {}
        eligible_terms: set[str] = set()
        for term_id, term in terms.items():
            disposition = str(
                dispositions.get(term_id, {}).get("disposition") or ""
            ).strip()
            if disposition not in {"direct_core", "maps_to_core", "maps_to_many"}:
                continue
            if not targets.get(term_id):
                continue
            name = str(term.get("name") or "").strip()
            if not name:
                continue
            eligible_terms.add(term_id)
            term_names[term_id] = name
            for value in (term_id, name):
                normalized = normalize_knowledge_alias(value)
                previous = exact.get(normalized)
                if previous is not None and previous != term_id:
                    raise CurrentKnowledgeUnavailable(
                        "current_knowledge_canonical_identity_ambiguous"
                    )
                exact[normalized] = term_id
                storage_values[term_id].add(str(value).strip())
            for field in ("aliases", "legacy_names", "legacy_ids"):
                for value in _strings(term.get(field)):
                    normalized = normalize_knowledge_alias(value)
                    if normalized:
                        compatibility[normalized].add(term_id)
                        storage_values[term_id].add(value)

        self._term_names = term_names
        self._targets = {
            term_id: tuple(targets[term_id]) for term_id in eligible_terms
        }
        self._exact = exact
        self._compatibility = {
            key: tuple(sorted(values)) for key, values in compatibility.items()
        }
        self._storage_values = {
            term_id: tuple(sorted(values))
            for term_id, values in storage_values.items()
        }

    @classmethod
    def from_active_database(
        cls,
        db_path: Path,
        *,
        taxonomy_catalog: Mapping[str, Any] | None = None,
    ) -> CurrentKnowledgeResolver:
        path = Path(db_path)
        if not path.is_file():
            raise CurrentKnowledgeUnavailable("current_knowledge_database_missing")
        try:
            uri = path.resolve(strict=True).as_uri() + "?mode=ro"
            connection = sqlite3.connect(uri, uri=True)
            connection.row_factory = sqlite3.Row
            try:
                connection.execute("PRAGMA query_only = ON")
                connection.execute("BEGIN")
                return cls.from_connection(
                    connection,
                    taxonomy_catalog=taxonomy_catalog,
                )
            finally:
                connection.close()
        except (OSError, sqlite3.Error) as exc:
            raise CurrentKnowledgeUnavailable(
                "current_knowledge_storage_unavailable"
            ) from exc

    @classmethod
    def from_connection(
        cls,
        connection: sqlite3.Connection,
        *,
        taxonomy_catalog: Mapping[str, Any] | None = None,
    ) -> CurrentKnowledgeResolver:
        try:
            if taxonomy_catalog is not None:
                rows = connection.execute(
                    """
                    SELECT payload_json
                    FROM knowledge_graph_releases
                    WHERE status = 'active'
                    ORDER BY release_id
                    """
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT
                        release_id,
                        content_hash,
                        taxonomy_revision
                    FROM knowledge_graph_releases
                    WHERE status = 'active'
                    ORDER BY release_id
                    """
                ).fetchall()
        except sqlite3.Error as exc:
            raise CurrentKnowledgeUnavailable(
                "current_knowledge_storage_unavailable"
            ) from exc
        if taxonomy_catalog is None:
            return cls._from_cached_active_rows(connection, rows)
        return cls._from_active_rows(rows, taxonomy_catalog=taxonomy_catalog)

    @classmethod
    def _from_cached_active_rows(
        cls,
        connection: sqlite3.Connection,
        rows: Sequence[object],
    ) -> CurrentKnowledgeResolver:
        if len(rows) != 1:
            raise CurrentKnowledgeUnavailable("current_knowledge_release_missing")
        row = rows[0]
        try:
            if isinstance(row, sqlite3.Row):
                release_id = str(row["release_id"]).strip()
                content_hash = str(row["content_hash"]).strip().casefold()
                taxonomy_revision = int(row["taxonomy_revision"])
            else:
                release_id = str(row[0]).strip()  # type: ignore[index]
                content_hash = str(row[1]).strip().casefold()  # type: ignore[index]
                taxonomy_revision = int(row[2])  # type: ignore[index]
        except (KeyError, TypeError, ValueError) as exc:
            raise CurrentKnowledgeUnavailable(
                "current_knowledge_release_corrupt"
            ) from exc

        cache_key = (release_id, content_hash, taxonomy_revision)
        with _RESOLVER_CACHE_LOCK:
            cached = _RESOLVER_CACHE.get(cache_key)
            if cached is not None:
                _RESOLVER_CACHE.move_to_end(cache_key)
                return cached
            try:
                payload_row = connection.execute(
                    """
                    SELECT payload_json
                    FROM knowledge_graph_releases
                    WHERE release_id = ?
                      AND content_hash = ?
                      AND taxonomy_revision = ?
                    """,
                    cache_key,
                ).fetchone()
            except sqlite3.Error as exc:
                raise CurrentKnowledgeUnavailable(
                    "current_knowledge_storage_unavailable"
                ) from exc
            if payload_row is None:
                raise CurrentKnowledgeUnavailable(
                    "current_knowledge_release_missing"
                )
            raw_value = (
                payload_row["payload_json"]
                if isinstance(payload_row, sqlite3.Row)
                else payload_row[0]
            )
            resolver = cls._resolver_from_payload(
                raw_value,
                expected_identity=cache_key,
                taxonomy_catalog=None,
            )
            _RESOLVER_CACHE[cache_key] = resolver
            _RESOLVER_CACHE.move_to_end(cache_key)
            while len(_RESOLVER_CACHE) > _RESOLVER_CACHE_LIMIT:
                _RESOLVER_CACHE.popitem(last=False)
            return resolver

    @classmethod
    def _from_active_rows(
        cls,
        rows: Sequence[object],
        *,
        taxonomy_catalog: Mapping[str, Any] | None,
    ) -> CurrentKnowledgeResolver:
        if len(rows) != 1:
            raise CurrentKnowledgeUnavailable("current_knowledge_release_missing")
        row = rows[0]
        raw_value = (
            row["payload_json"]
            if isinstance(row, sqlite3.Row)
            else row[0]  # type: ignore[index]
        )
        return cls._resolver_from_payload(
            raw_value,
            expected_identity=None,
            taxonomy_catalog=taxonomy_catalog,
        )

    @classmethod
    def _resolver_from_payload(
        cls,
        raw_value: object,
        *,
        expected_identity: tuple[str, str, int] | None,
        taxonomy_catalog: Mapping[str, Any] | None,
    ) -> CurrentKnowledgeResolver:
        try:
            if expected_identity is not None:
                release = cached_release_from_json(
                    str(raw_value),
                    release_id=expected_identity[0],
                    content_hash=expected_identity[1],
                )
            else:
                release = KnowledgeGraphRelease.from_mapping(
                    json.loads(str(raw_value))
                )
            if expected_identity is not None and expected_identity != (
                release.release_id,
                release.content_hash,
                release.taxonomy_revision,
            ):
                raise CurrentKnowledgeUnavailable(
                    "current_knowledge_release_corrupt"
                )
            catalog = dict(
                taxonomy_catalog or load_taxonomy_catalog_for_release(release)
            )
            return cls(release, catalog)
        except (
            json.JSONDecodeError,
            KnowledgeGraphReleaseError,
            OSError,
            TypeError,
            ValueError,
        ) as exc:
            if isinstance(exc, CurrentKnowledgeUnavailable):
                raise
            raise CurrentKnowledgeUnavailable("current_knowledge_release_corrupt") from exc

    @property
    def nodes(self) -> tuple[CurrentKnowledgeNode, ...]:
        return tuple(self._nodes[key] for key in sorted(self._nodes))

    @property
    def relations(self) -> tuple[CurrentKnowledgeRelation, ...]:
        return self._relations

    def node(self, stable_key: object) -> CurrentKnowledgeNode | None:
        return self._nodes.get(str(stable_key or "").strip().casefold())

    def resolve(self, value: object) -> tuple[ResolvedKnowledge, ...]:
        text = str(value or "").strip()
        if text.startswith("knowledge_point:"):
            text = text.removeprefix("knowledge_point:").strip()
        if not text:
            return ()
        key = text.casefold()
        direct_node = self._nodes.get(key)
        if direct_node is not None:
            return (
                ResolvedKnowledge(
                    fine_term_id=key,
                    canonical_name=direct_node.display_name,
                    stable_key=key,
                    display_name=direct_node.display_name,
                ),
            )
        normalized = normalize_knowledge_alias(text)
        term_id = self._exact.get(normalized)
        if term_id is None:
            matches = self._compatibility.get(normalized, ())
            if len(matches) != 1:
                return ()
            term_id = matches[0]
        return tuple(
            ResolvedKnowledge(
                fine_term_id=term_id,
                canonical_name=self._term_names[term_id],
                stable_key=stable_key,
                display_name=self._nodes[stable_key].display_name,
            )
            for stable_key in self._targets[term_id]
        )

    def canonical_term(self, value: object) -> tuple[str, str] | None:
        """Return the governed current fine term, never a core/legacy identity."""

        text = str(value or "").strip()
        if not text:
            return None
        normalized = normalize_knowledge_alias(text)
        term_id = self._exact.get(normalized)
        if term_id is None:
            matches = self._compatibility.get(normalized, ())
            if len(matches) != 1:
                return None
            term_id = matches[0]
        return term_id, self._term_names[term_id]

    def stored_values_for_term(self, value: object) -> tuple[str, ...]:
        term = self.canonical_term(value)
        if term is None:
            return ()
        term_id = term[0]
        return tuple(
            stored
            for stored in self._storage_values[term_id]
            if self._exact.get(normalize_knowledge_alias(stored)) == term_id
            or len(
                self._compatibility.get(
                    normalize_knowledge_alias(stored),
                    (term_id,),
                )
            ) == 1
        )

    def resolve_many(
        self,
        values: Iterable[object],
    ) -> tuple[ResolvedKnowledge, ...]:
        result: list[ResolvedKnowledge] = []
        seen: set[tuple[str, str]] = set()
        for value in values:
            for item in self.resolve(value):
                identity = (item.fine_term_id, item.stable_key)
                if identity in seen:
                    continue
                seen.add(identity)
                result.append(item)
        return tuple(result)


_RESOLVER_CACHE: OrderedDict[
    tuple[str, str, int],
    CurrentKnowledgeResolver,
] = OrderedDict()


def _clear_resolver_cache_for_tests() -> None:
    with _RESOLVER_CACHE_LOCK:
        _RESOLVER_CACHE.clear()


def ensure_checked_in_current_standard(db_path: Path) -> str:
    """Install the checked-in standard only when the database has no active one.

    A different, valid active release is deliberately left untouched: it is the
    internally selected current standard (including an emergency rollback), so
    startup must not silently replace it with the checked-in package.
    """

    path = Path(db_path)
    checked_in = load_release()
    report = validate_release(
        checked_in,
        load_taxonomy_catalog_for_release(checked_in),
    )
    if not report.valid:
        raise CurrentKnowledgeUnavailable("current_knowledge_checked_in_invalid")
    active = load_active_release(path)
    if active is not None:
        active_report = validate_release(
            active,
            load_taxonomy_catalog_for_release(active),
        )
        if not active_report.valid:
            raise CurrentKnowledgeUnavailable("current_knowledge_active_invalid")
        # Do not replace an explicitly selected current release during startup.
        return active.release_id

    try:
        return bootstrap_release(
            path,
            checked_in,
            actor_ref="system-current-standard",
            source_reference="checked-in-current-standard",
            reason="内部启用唯一当前知识标准",
            taxonomy_catalog=load_taxonomy_catalog_for_release(checked_in),
        )
    except KnowledgeGraphReleaseConflict:
        # A concurrent starter may have completed the same activation.
        active = load_active_release(path)
        if (
            active is not None
            and active.release_id == checked_in.release_id
            and active.content_hash == checked_in.content_hash
        ):
            return checked_in.release_id
        raise CurrentKnowledgeUnavailable(
            "current_knowledge_activation_conflict"
        )


def _node(item: Mapping[str, Any]) -> CurrentKnowledgeNode:
    return CurrentKnowledgeNode(
        stable_key=str(item["stable_key"]).strip().casefold(),
        display_name=str(item["display_name"]).strip(),
        definition=str(item["definition"]).strip(),
        include_scope=str(item["include_scope"]).strip(),
        exclude_scope=str(item["exclude_scope"]).strip(),
        curriculum_anchors=_strings(item.get("curriculum_anchors")),
        observable_evidence=str(item["observable_evidence"]).strip(),
        rationale=str(item["rationale"]).strip(),
        evidence_source_ids=_strings(item.get("evidence_source_ids")),
    )


def _relation(item: Mapping[str, Any]) -> CurrentKnowledgeRelation:
    return CurrentKnowledgeRelation(
        relation_key=str(item["relation_key"]).strip(),
        source_key=str(item["source_key"]).strip().casefold(),
        target_key=str(item["target_key"]).strip().casefold(),
        relation_type=str(item["relation_type"]).strip(),
        rationale=str(item["rationale"]).strip(),
        basis_kind=str(item["basis_kind"]).strip(),
        strength=str(item["strength"]).strip(),
        evidence_source_ids=_strings(item.get("evidence_source_ids")),
        source_locator=str(item["source_locator"]).strip(),
    )


def _objects(raw: object) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        return ()
    return tuple(item for item in raw if isinstance(item, Mapping))


def _strings(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        return ()
    return tuple(
        text for item in raw if (text := str(item or "").strip())
    )


__all__ = [
    "CurrentKnowledgeNode",
    "CurrentKnowledgeRelation",
    "CurrentKnowledgeResolver",
    "CurrentKnowledgeUnavailable",
    "ResolvedKnowledge",
    "ensure_checked_in_current_standard",
]
