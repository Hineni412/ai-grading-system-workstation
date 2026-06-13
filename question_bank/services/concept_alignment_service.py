from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

from question_bank.database.schema import connect, initialize_database
from question_bank.models.knowledge_alignment import (
    AlignmentStatus,
    KnowledgeConcept,
    KnowledgeSourceMapping,
    normalize_source_value,
)
from question_bank.taxonomy.registry import (
    canonical_knowledge_seed_rows,
    canonicalize_knowledge,
)


@dataclass(frozen=True)
class AlignmentResolution:
    source_namespace: str
    source_value: str
    status: AlignmentStatus
    concept: KnowledgeConcept | None
    confidence: float

    @property
    def eligible_for_recommendation(self) -> bool:
        return self.status is AlignmentStatus.CONFIRMED and self.concept is not None


class ConceptAlignmentService:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    def initialize_database(self) -> None:
        initialize_database(self.db_path)

    def create_concept(
        self,
        canonical_key: str,
        name: str,
        *,
        aliases: Iterable[str] = (),
        subject: str | None = None,
        grade: str | None = None,
    ) -> KnowledgeConcept:
        concept = KnowledgeConcept(
            id=0,
            canonical_key=canonical_key,
            name=name,
            aliases=tuple(aliases),
        )
        self.initialize_database()
        with connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO knowledge_concepts (
                    canonical_key, name, aliases_json, subject, grade
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    concept.canonical_key,
                    concept.name,
                    _json(list(concept.aliases)),
                    _optional(subject),
                    _optional(grade),
                ),
            )
            return KnowledgeConcept(
                id=int(cursor.lastrowid),
                canonical_key=concept.canonical_key,
                name=concept.name,
                aliases=concept.aliases,
            )

    def update_concept(
        self,
        concept_id: int,
        *,
        name: str | None = None,
        aliases: Iterable[str] | None = None,
        subject: str | None = None,
        grade: str | None = None,
        status: str | None = None,
    ) -> KnowledgeConcept:
        self.initialize_database()
        updates: list[str] = []
        params: list[Any] = []
        if name is not None:
            updates.append("name = ?")
            params.append(KnowledgeConcept(0, "temporary", name).name)
        if aliases is not None:
            normalized_aliases = KnowledgeConcept(0, "temporary", "temporary", tuple(aliases)).aliases
            updates.append("aliases_json = ?")
            params.append(_json(list(normalized_aliases)))
        if subject is not None:
            updates.append("subject = ?")
            params.append(_optional(subject))
        if grade is not None:
            updates.append("grade = ?")
            params.append(_optional(grade))
        if status is not None:
            updates.append("status = ?")
            params.append(status)
        if updates:
            updates.append("updated_at = datetime('now','localtime')")
            params.append(int(concept_id))
            with connect(self.db_path) as conn:
                conn.execute(
                    f"UPDATE knowledge_concepts SET {', '.join(updates)} WHERE id = ?",
                    params,
                )
        concept = self.get_concept(concept_id)
        if concept is None:
            raise KeyError(f"knowledge concept not found: {concept_id}")
        return concept

    def get_concept(self, concept_id: int) -> KnowledgeConcept | None:
        self.initialize_database()
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM knowledge_concepts WHERE id = ?",
                (int(concept_id),),
            ).fetchone()
            return _concept_from_row(row) if row is not None else None

    def list_concepts(self, *, status: str | None = "active") -> list[KnowledgeConcept]:
        self.initialize_database()
        with connect(self.db_path) as conn:
            if status is None:
                rows = conn.execute(
                    "SELECT * FROM knowledge_concepts ORDER BY name, id"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM knowledge_concepts WHERE status = ? ORDER BY name, id",
                    (status,),
                ).fetchall()
            return [_concept_from_row(row) for row in rows]

    def seed_registry_concepts(self) -> int:
        self.initialize_database()
        created = 0
        with connect(self.db_path) as conn:
            for row in canonical_knowledge_seed_rows():
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO knowledge_concepts (
                        canonical_key, name, aliases_json, subject
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        normalize_source_value(row["canonical_key"]),
                        row["name"],
                        _json(list(row["aliases"])),
                        "math",
                    ),
                )
                created += int(cursor.rowcount > 0)
        return created

    def create_relation(
        self,
        source_concept_id: int,
        target_concept_id: int,
        relation_type: str,
        *,
        weight: float = 1.0,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.initialize_database()
        with connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO knowledge_relations (
                    source_concept_id, target_concept_id, relation_type, weight, metadata_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    int(source_concept_id),
                    int(target_concept_id),
                    relation_type,
                    float(weight),
                    _json(metadata or {}),
                ),
            )
            row = conn.execute(
                "SELECT * FROM knowledge_relations WHERE id = ?",
                (int(cursor.lastrowid),),
            ).fetchone()
            return dict(row)

    def list_relations(self, source_concept_id: int | None = None) -> list[dict[str, Any]]:
        self.initialize_database()
        with connect(self.db_path) as conn:
            if source_concept_id is None:
                rows = conn.execute(
                    "SELECT * FROM knowledge_relations ORDER BY source_concept_id, id"
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM knowledge_relations
                    WHERE source_concept_id = ?
                    ORDER BY id
                    """,
                    (int(source_concept_id),),
                ).fetchall()
            return [dict(row) for row in rows]

    def confirm_mapping(
        self,
        source_namespace: str,
        source_value: str,
        concept_id: int,
        *,
        reviewed_by: str | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> KnowledgeSourceMapping:
        self.initialize_database()
        with connect(self.db_path) as conn:
            return _upsert_mapping(
                conn,
                KnowledgeSourceMapping(
                    source_namespace=source_namespace,
                    source_value=source_value,
                    concept_id=int(concept_id),
                    status=AlignmentStatus.CONFIRMED,
                    confidence=1.0,
                ),
                reviewed_by=reviewed_by,
                evidence=evidence,
            )

    def confirm_many(
        self,
        mappings: Iterable[tuple[str, str, int]],
        *,
        reviewed_by: str | None = None,
    ) -> list[KnowledgeSourceMapping]:
        self.initialize_database()
        results: list[KnowledgeSourceMapping] = []
        with connect(self.db_path) as conn:
            for source_namespace, source_value, concept_id in mappings:
                results.append(
                    _upsert_mapping(
                        conn,
                        KnowledgeSourceMapping(
                            source_namespace=source_namespace,
                            source_value=source_value,
                            concept_id=int(concept_id),
                            status=AlignmentStatus.CONFIRMED,
                            confidence=1.0,
                        ),
                        reviewed_by=reviewed_by,
                    )
                )
        return results

    def suggest_mapping(
        self,
        source_namespace: str,
        source_value: str,
        concept_id: int,
        *,
        confidence: float,
        evidence: dict[str, Any] | None = None,
    ) -> KnowledgeSourceMapping:
        self.initialize_database()
        with connect(self.db_path) as conn:
            return _upsert_mapping(
                conn,
                KnowledgeSourceMapping(
                    source_namespace=source_namespace,
                    source_value=source_value,
                    concept_id=int(concept_id),
                    status=AlignmentStatus.SUGGESTED,
                    confidence=confidence,
                ),
                evidence=evidence,
            )

    def reject_mapping(
        self,
        source_namespace: str,
        source_value: str,
        *,
        reviewed_by: str | None = None,
    ) -> KnowledgeSourceMapping:
        self.initialize_database()
        with connect(self.db_path) as conn:
            return _upsert_mapping(
                conn,
                KnowledgeSourceMapping(
                    source_namespace=source_namespace,
                    source_value=source_value,
                    concept_id=None,
                    status=AlignmentStatus.REJECTED,
                    confidence=0.0,
                ),
                reviewed_by=reviewed_by,
            )

    def list_mappings(
        self,
        *,
        source_namespace: str | None = None,
        status: AlignmentStatus | str | None = None,
    ) -> list[KnowledgeSourceMapping]:
        self.initialize_database()
        where: list[str] = []
        params: list[Any] = []
        if source_namespace:
            where.append("source_namespace = ?")
            params.append(normalize_source_value(source_namespace))
        if status:
            status_value = status.value if isinstance(status, AlignmentStatus) else str(status)
            where.append("status = ?")
            params.append(status_value)
        sql = "SELECT * FROM knowledge_source_mappings"
        if where:
            sql += f" WHERE {' AND '.join(where)}"
        sql += " ORDER BY source_namespace, source_value, id"
        with connect(self.db_path) as conn:
            return [_mapping_from_row(row) for row in conn.execute(sql, params).fetchall()]

    def coverage_metrics(self) -> dict[str, dict[str, int]]:
        self.initialize_database()
        with connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT source_namespace, status, COUNT(*) AS count
                FROM knowledge_source_mappings
                GROUP BY source_namespace, status
                ORDER BY source_namespace, status
                """
            ).fetchall()
        metrics: dict[str, dict[str, int]] = {}
        for row in rows:
            metrics.setdefault(str(row["source_namespace"]), {})[str(row["status"])] = int(row["count"])
        return metrics

    def resolve(self, source_namespace: str, source_value: str) -> AlignmentResolution:
        self.initialize_database()
        namespace = normalize_source_value(source_namespace)
        normalized_value = normalize_source_value(source_value)
        display_value = KnowledgeSourceMapping(
            source_namespace=namespace,
            source_value=source_value,
            concept_id=None,
            status=AlignmentStatus.UNMAPPED,
            confidence=0.0,
        ).source_value
        with connect(self.db_path) as conn:
            persisted = conn.execute(
                """
                SELECT * FROM knowledge_source_mappings
                WHERE source_namespace = ? AND normalized_value = ?
                ORDER BY CASE status
                    WHEN 'confirmed' THEN 0
                    WHEN 'rejected' THEN 1
                    ELSE 2
                END, id
                LIMIT 1
                """,
                (namespace, normalized_value),
            ).fetchone()
            if persisted is not None:
                mapping = _mapping_from_row(persisted)
                concept = _load_concept(conn, mapping.concept_id)
                return AlignmentResolution(
                    source_namespace=namespace,
                    source_value=mapping.source_value,
                    status=mapping.status,
                    concept=concept,
                    confidence=mapping.confidence,
                )

            concept, confidence = _suggest_concept(conn, display_value)
            if concept is not None:
                return AlignmentResolution(
                    source_namespace=namespace,
                    source_value=display_value,
                    status=AlignmentStatus.SUGGESTED,
                    concept=concept,
                    confidence=confidence,
                )
        return AlignmentResolution(
            source_namespace=namespace,
            source_value=display_value,
            status=AlignmentStatus.UNMAPPED,
            concept=None,
            confidence=0.0,
        )


def _upsert_mapping(
    conn: sqlite3.Connection,
    mapping: KnowledgeSourceMapping,
    *,
    reviewed_by: str | None = None,
    evidence: dict[str, Any] | None = None,
) -> KnowledgeSourceMapping:
    reviewed_at = "datetime('now','localtime')" if reviewed_by or mapping.status is not AlignmentStatus.SUGGESTED else "NULL"
    conn.execute(
        f"""
        INSERT INTO knowledge_source_mappings (
            source_namespace, source_value, normalized_value, concept_id,
            status, confidence, evidence_json, reviewed_by, reviewed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, {reviewed_at})
        ON CONFLICT(source_namespace, source_value) DO UPDATE SET
            normalized_value = excluded.normalized_value,
            concept_id = excluded.concept_id,
            status = excluded.status,
            confidence = excluded.confidence,
            evidence_json = excluded.evidence_json,
            reviewed_by = excluded.reviewed_by,
            reviewed_at = {reviewed_at},
            updated_at = datetime('now','localtime')
        WHERE excluded.status <> 'suggested'
           OR knowledge_source_mappings.status = 'suggested'
        """,
        (
            mapping.source_namespace,
            mapping.source_value,
            mapping.normalized_value,
            mapping.concept_id,
            mapping.status.value,
            mapping.confidence,
            _json(evidence or {}),
            _optional(reviewed_by),
        ),
    )
    row = conn.execute(
        """
        SELECT * FROM knowledge_source_mappings
        WHERE source_namespace = ? AND source_value = ?
        """,
        (mapping.source_namespace, mapping.source_value),
    ).fetchone()
    return _mapping_from_row(row)


def _suggest_concept(
    conn: sqlite3.Connection,
    source_value: str,
) -> tuple[KnowledgeConcept | None, float]:
    normalized_source = normalize_source_value(source_value)
    if not normalized_source:
        return None, 0.0
    rows = conn.execute(
        "SELECT * FROM knowledge_concepts WHERE status = 'active' ORDER BY id"
    ).fetchall()
    concepts = [_concept_from_row(row) for row in rows]
    registry_match = canonicalize_knowledge(source_value)

    best: tuple[float, KnowledgeConcept | None] = (0.0, None)
    for concept in concepts:
        candidates = (concept.canonical_key, concept.name, *concept.aliases)
        for candidate in candidates:
            normalized_candidate = normalize_source_value(candidate)
            if normalized_source == normalized_candidate:
                return concept, 0.95
            if registry_match is not None and normalize_source_value(registry_match.canonical_id) == concept.canonical_key:
                best = max(best, (0.9, concept), key=lambda item: item[0])
            if min(len(normalized_source), len(normalized_candidate)) >= 2 and (
                normalized_source in normalized_candidate or normalized_candidate in normalized_source
            ):
                best = max(best, (0.8, concept), key=lambda item: item[0])
            similarity = SequenceMatcher(None, normalized_source, normalized_candidate).ratio()
            if similarity >= 0.7:
                best = max(best, (min(0.89, similarity), concept), key=lambda item: item[0])
    return best[1], round(best[0], 4)


def _load_concept(
    conn: sqlite3.Connection,
    concept_id: int | None,
) -> KnowledgeConcept | None:
    if concept_id is None:
        return None
    row = conn.execute(
        "SELECT * FROM knowledge_concepts WHERE id = ?",
        (int(concept_id),),
    ).fetchone()
    return _concept_from_row(row) if row is not None else None


def _concept_from_row(row: sqlite3.Row) -> KnowledgeConcept:
    aliases = _json_list(row["aliases_json"])
    return KnowledgeConcept(
        id=int(row["id"]),
        canonical_key=str(row["canonical_key"]),
        name=str(row["name"]),
        aliases=tuple(aliases),
    )


def _mapping_from_row(row: sqlite3.Row) -> KnowledgeSourceMapping:
    return KnowledgeSourceMapping(
        source_namespace=str(row["source_namespace"]),
        source_value=str(row["source_value"]),
        concept_id=int(row["concept_id"]) if row["concept_id"] is not None else None,
        status=AlignmentStatus(str(row["status"])),
        confidence=float(row["confidence"]),
    )


def _json_list(value: object) -> list[str]:
    try:
        loaded = json.loads(str(value or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return [str(item) for item in loaded] if isinstance(loaded, list) else []


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _optional(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None
