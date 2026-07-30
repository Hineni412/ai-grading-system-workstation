from __future__ import annotations

import sqlite3
import json
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator
from uuid import uuid4

from question_bank.database.schema import connect
from question_bank.relations.contracts import (
    KnowledgeRelation,
    RelationConflict,
    RelationStatus,
    RelationType,
    find_confirmation_conflicts,
)


class RelationRepositoryError(RuntimeError):
    """Base error for governed knowledge-relation persistence."""


class KnowledgeIdentityNotFound(RelationRepositoryError):
    pass


class KnowledgeIdentityRetired(RelationRepositoryError):
    pass


class KnowledgeRelationNotFound(RelationRepositoryError):
    pass


class KnowledgeRelationDuplicate(RelationRepositoryError):
    def __init__(self, relation_id: str, status: RelationStatus) -> None:
        self.relation_id = relation_id
        self.status = status
        super().__init__("knowledge relation already exists")


class KnowledgeRelationRevisionConflict(RelationRepositoryError):
    def __init__(self, expected_revision: int, current_revision: int) -> None:
        self.expected_revision = int(expected_revision)
        self.current_revision = int(current_revision)
        super().__init__("knowledge relation revision is stale")


class KnowledgeRelationTransitionError(RelationRepositoryError):
    def __init__(
        self,
        from_status: RelationStatus,
        to_status: RelationStatus,
    ) -> None:
        self.from_status = from_status
        self.to_status = to_status
        super().__init__(
            f"knowledge relation cannot transition from {from_status} to {to_status}"
        )


class KnowledgeRelationConfirmationConflict(RelationRepositoryError):
    def __init__(self, conflicts: tuple[RelationConflict, ...]) -> None:
        self.conflicts = conflicts
        super().__init__("knowledge relation conflicts with the active graph")


@dataclass(frozen=True, slots=True)
class KnowledgeIdentityRecord:
    stable_key: str
    display_name: str
    origin: str
    status: str
    revision: int
    created_at: str
    updated_at: str
    retired_at: str | None


@dataclass(frozen=True, slots=True)
class KnowledgeRelationRecord:
    relation_id: str
    source_key: str
    target_key: str
    relation_type: RelationType
    status: RelationStatus
    source_kind: str
    source_reference: str | None
    source_operation_id: str | None
    rationale: str
    model_name: str | None
    model_version: str | None
    prompt_version: str | None
    confidence: float | None
    conflict_codes: tuple[str, ...]
    decision_by: str | None
    decision_note: str | None
    decided_at: str | None
    revision: int
    created_at: str
    updated_at: str

    def as_contract(self) -> KnowledgeRelation:
        return KnowledgeRelation(
            source_key=self.source_key,
            target_key=self.target_key,
            relation_type=self.relation_type,
            status=self.status,
        )


@dataclass(frozen=True, slots=True)
class ActiveKnowledgeRelation:
    relation_id: str
    source_key: str
    source_name: str
    target_key: str
    target_name: str
    relation_type: RelationType
    rationale: str
    revision: int
    updated_at: str


_LEGAL_TRANSITIONS: dict[RelationStatus, frozenset[RelationStatus]] = {
    RelationStatus.SUGGESTED: frozenset(
        {RelationStatus.CONFIRMED, RelationStatus.REJECTED}
    ),
    RelationStatus.CONFIRMED: frozenset({RelationStatus.RETIRED}),
    RelationStatus.REJECTED: frozenset(),
    RelationStatus.RETIRED: frozenset({RelationStatus.CONFIRMED}),
}


class KnowledgeRelationRepository:
    """Owns atomic relation writes and exposes the confirmed graph read model."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)

    def list_identities(
        self,
        *,
        status: str | None = None,
    ) -> tuple[KnowledgeIdentityRecord, ...]:
        parameters: tuple[object, ...] = ()
        where = ""
        if status is not None:
            normalized_status = str(status).strip().casefold()
            if normalized_status not in {"active", "retired"}:
                raise ValueError("identity status must be active or retired")
            where = "WHERE status = ?"
            parameters = (normalized_status,)
        with connect(self.db_path) as connection:
            rows = connection.execute(
                f"""
                SELECT *
                FROM knowledge_tag_identities
                {where}
                ORDER BY stable_key
                """,
                parameters,
            ).fetchall()
        return tuple(_identity_record(row) for row in rows)

    def get_relation(self, relation_id: str) -> KnowledgeRelationRecord:
        clean_id = _required_text(relation_id, "relation_id")
        with connect(self.db_path) as connection:
            row = connection.execute(
                "SELECT * FROM knowledge_relations WHERE relation_id = ?",
                (clean_id,),
            ).fetchone()
        if row is None:
            raise KnowledgeRelationNotFound(clean_id)
        return _relation_record(row)

    def list_relations(
        self,
        *,
        status: RelationStatus | str | None = None,
    ) -> tuple[KnowledgeRelationRecord, ...]:
        parameters: tuple[object, ...] = ()
        where = ""
        if status is not None:
            normalized_status = RelationStatus(status)
            where = "WHERE status = ?"
            parameters = (normalized_status.value,)
        with connect(self.db_path) as connection:
            rows = connection.execute(
                f"""
                SELECT *
                FROM knowledge_relations
                {where}
                ORDER BY created_at, relation_id
                """,
                parameters,
            ).fetchall()
        return tuple(_relation_record(row) for row in rows)

    def list_active_relations(self) -> tuple[ActiveKnowledgeRelation, ...]:
        with connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM knowledge_active_relations
                ORDER BY relation_type, source_key, target_key
                """
            ).fetchall()
        return tuple(
            ActiveKnowledgeRelation(
                relation_id=str(row["relation_id"]),
                source_key=str(row["source_key"]),
                source_name=str(row["source_name"]),
                target_key=str(row["target_key"]),
                target_name=str(row["target_name"]),
                relation_type=RelationType(row["relation_type"]),
                rationale=str(row["rationale"]),
                revision=int(row["revision"]),
                updated_at=str(row["updated_at"]),
            )
            for row in rows
        )

    def create_suggestion(
        self,
        relation: KnowledgeRelation,
        *,
        source_kind: str,
        rationale: str,
        source_reference: str | None = None,
        source_operation_id: str | None = None,
        model_name: str | None = None,
        model_version: str | None = None,
        prompt_version: str | None = None,
        confidence: float | None = None,
        conflict_codes: tuple[str, ...] = (),
    ) -> KnowledgeRelationRecord:
        candidate = KnowledgeRelation(
            source_key=relation.source_key,
            target_key=relation.target_key,
            relation_type=relation.relation_type,
            status=relation.status,
        )
        if candidate.status is not RelationStatus.SUGGESTED:
            raise ValueError("new knowledge relations must start as suggested")
        clean_source_kind = _source_kind(source_kind)
        clean_rationale = _required_text(rationale, "rationale")
        clean_operation_id = _optional_text(source_operation_id)
        clean_model_name = _optional_text(model_name)
        clean_model_version = _optional_text(model_version)
        clean_prompt_version = _optional_text(prompt_version)
        clean_confidence = _optional_confidence(confidence)
        clean_conflict_codes = tuple(
            dict.fromkeys(
                _required_text(code, "conflict_code")
                for code in conflict_codes
            )
        )
        if clean_source_kind == "model" and (
            clean_model_name is None or clean_model_version is None
        ):
            raise ValueError(
                "model suggestions require model_name and model_version"
            )

        with self._transaction() as connection:
            self._require_active_identity(connection, candidate.source_key)
            self._require_active_identity(connection, candidate.target_key)
            existing = connection.execute(
                """
                SELECT *
                FROM knowledge_relations
                WHERE source_key = ?
                  AND target_key = ?
                  AND relation_type = ?
                """,
                (
                    candidate.source_key,
                    candidate.target_key,
                    candidate.relation_type.value,
                ),
            ).fetchone()
            if existing is not None:
                existing_record = _relation_record(existing)
                if (
                    clean_operation_id is not None
                    and existing_record.source_operation_id == clean_operation_id
                ):
                    return existing_record
                raise KnowledgeRelationDuplicate(
                    existing_record.relation_id,
                    existing_record.status,
                )

            relation_id = f"kr_{uuid4().hex}"
            connection.execute(
                """
                INSERT INTO knowledge_relations (
                    relation_id,
                    source_key,
                    target_key,
                    relation_type,
                    status,
                    source_kind,
                    source_reference,
                    source_operation_id,
                    rationale,
                    model_name,
                    model_version,
                    prompt_version,
                    confidence,
                    conflict_codes_json
                ) VALUES (?, ?, ?, ?, 'suggested', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    relation_id,
                    candidate.source_key,
                    candidate.target_key,
                    candidate.relation_type.value,
                    clean_source_kind,
                    _optional_text(source_reference),
                    clean_operation_id,
                    clean_rationale,
                    clean_model_name,
                    clean_model_version,
                    clean_prompt_version,
                    clean_confidence,
                    json.dumps(
                        clean_conflict_codes,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                ),
            )
            self._append_audit(
                connection,
                relation_id=relation_id,
                event_type="suggested",
                from_status=None,
                to_status=RelationStatus.SUGGESTED,
                actor_kind=clean_source_kind,
                actor_ref=_optional_text(source_reference),
                reason=clean_rationale,
                expected_revision=0,
                resulting_revision=1,
                source_operation_id=clean_operation_id,
                model_name=clean_model_name,
                model_version=clean_model_version,
            )
            created = connection.execute(
                "SELECT * FROM knowledge_relations WHERE relation_id = ?",
                (relation_id,),
            ).fetchone()
            assert created is not None
            return _relation_record(created)

    def transition(
        self,
        relation_id: str,
        *,
        expected_revision: int,
        to_status: RelationStatus | str,
        actor_ref: str,
        reason: str,
        actor_kind: str = "teacher",
    ) -> KnowledgeRelationRecord:
        clean_id = _required_text(relation_id, "relation_id")
        clean_actor = _required_text(actor_ref, "actor_ref")
        clean_reason = _required_text(reason, "reason")
        clean_actor_kind = _source_kind(actor_kind)
        desired_status = RelationStatus(to_status)
        if isinstance(expected_revision, bool) or int(expected_revision) < 1:
            raise ValueError("expected_revision must be a positive integer")

        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM knowledge_relations WHERE relation_id = ?",
                (clean_id,),
            ).fetchone()
            if row is None:
                raise KnowledgeRelationNotFound(clean_id)
            current = _relation_record(row)
            if current.revision != int(expected_revision):
                raise KnowledgeRelationRevisionConflict(
                    int(expected_revision),
                    current.revision,
                )
            if desired_status not in _LEGAL_TRANSITIONS[current.status]:
                raise KnowledgeRelationTransitionError(
                    current.status,
                    desired_status,
                )
            if desired_status is RelationStatus.CONFIRMED:
                self._require_active_identity(connection, current.source_key)
                self._require_active_identity(connection, current.target_key)
                conflicts = self._confirmation_conflicts(
                    connection,
                    current,
                )
                if conflicts:
                    raise KnowledgeRelationConfirmationConflict(conflicts)

            new_revision = current.revision + 1
            cursor = connection.execute(
                """
                UPDATE knowledge_relations
                SET status = ?,
                    decision_by = ?,
                    decision_note = ?,
                    decided_at = datetime('now','localtime'),
                    revision = ?,
                    updated_at = datetime('now','localtime')
                WHERE relation_id = ?
                  AND revision = ?
                """,
                (
                    desired_status.value,
                    clean_actor,
                    clean_reason,
                    new_revision,
                    clean_id,
                    current.revision,
                ),
            )
            if cursor.rowcount != 1:
                latest = connection.execute(
                    """
                    SELECT revision
                    FROM knowledge_relations
                    WHERE relation_id = ?
                    """,
                    (clean_id,),
                ).fetchone()
                if latest is None:
                    raise KnowledgeRelationNotFound(clean_id)
                raise KnowledgeRelationRevisionConflict(
                    current.revision,
                    int(latest[0]),
                )

            event_type = desired_status.value
            if (
                current.status is RelationStatus.RETIRED
                and desired_status is RelationStatus.CONFIRMED
            ):
                event_type = "restored"
            self._append_audit(
                connection,
                relation_id=clean_id,
                event_type=event_type,
                from_status=current.status,
                to_status=desired_status,
                actor_kind=clean_actor_kind,
                actor_ref=clean_actor,
                reason=clean_reason,
                expected_revision=current.revision,
                resulting_revision=new_revision,
            )
            updated = connection.execute(
                "SELECT * FROM knowledge_relations WHERE relation_id = ?",
                (clean_id,),
            ).fetchone()
            assert updated is not None
            return _relation_record(updated)

    def audit_events(self, relation_id: str) -> tuple[dict[str, object], ...]:
        clean_id = _required_text(relation_id, "relation_id")
        with connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM knowledge_relation_audit_events
                WHERE relation_id = ?
                ORDER BY resulting_revision
                """,
                (clean_id,),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            yield connection

    @staticmethod
    def _require_active_identity(
        connection: sqlite3.Connection,
        stable_key: str,
    ) -> None:
        row = connection.execute(
            """
            SELECT status
            FROM knowledge_tag_identities
            WHERE stable_key = ?
            """,
            (stable_key,),
        ).fetchone()
        if row is None:
            raise KnowledgeIdentityNotFound(stable_key)
        if str(row[0]) != "active":
            raise KnowledgeIdentityRetired(stable_key)

    @staticmethod
    def _confirmation_conflicts(
        connection: sqlite3.Connection,
        candidate: KnowledgeRelationRecord,
    ) -> tuple[RelationConflict, ...]:
        rows = connection.execute(
            """
            SELECT source_key, target_key, relation_type, status
            FROM knowledge_relations
            WHERE status = 'confirmed'
              AND relation_id <> ?
            ORDER BY relation_id
            """,
            (candidate.relation_id,),
        ).fetchall()
        active = tuple(
            KnowledgeRelation(
                source_key=str(row["source_key"]),
                target_key=str(row["target_key"]),
                relation_type=RelationType(row["relation_type"]),
                status=RelationStatus(row["status"]),
            )
            for row in rows
        )
        return find_confirmation_conflicts(candidate.as_contract(), active)

    @staticmethod
    def _append_audit(
        connection: sqlite3.Connection,
        *,
        relation_id: str,
        event_type: str,
        from_status: RelationStatus | None,
        to_status: RelationStatus,
        actor_kind: str,
        actor_ref: str | None,
        reason: str,
        expected_revision: int,
        resulting_revision: int,
        source_operation_id: str | None = None,
        model_name: str | None = None,
        model_version: str | None = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO knowledge_relation_audit_events (
                relation_id,
                event_type,
                from_status,
                to_status,
                actor_kind,
                actor_ref,
                reason,
                expected_revision,
                resulting_revision,
                source_operation_id,
                model_name,
                model_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                relation_id,
                event_type,
                None if from_status is None else from_status.value,
                to_status.value,
                actor_kind,
                actor_ref,
                reason,
                int(expected_revision),
                int(resulting_revision),
                source_operation_id,
                model_name,
                model_version,
            ),
        )


def _identity_record(row: sqlite3.Row) -> KnowledgeIdentityRecord:
    return KnowledgeIdentityRecord(
        stable_key=str(row["stable_key"]),
        display_name=str(row["display_name"]),
        origin=str(row["origin"]),
        status=str(row["status"]),
        revision=int(row["revision"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        retired_at=(
            None if row["retired_at"] is None else str(row["retired_at"])
        ),
    )


def _relation_record(row: sqlite3.Row) -> KnowledgeRelationRecord:
    return KnowledgeRelationRecord(
        relation_id=str(row["relation_id"]),
        source_key=str(row["source_key"]),
        target_key=str(row["target_key"]),
        relation_type=RelationType(row["relation_type"]),
        status=RelationStatus(row["status"]),
        source_kind=str(row["source_kind"]),
        source_reference=(
            None
            if row["source_reference"] is None
            else str(row["source_reference"])
        ),
        source_operation_id=(
            None
            if row["source_operation_id"] is None
            else str(row["source_operation_id"])
        ),
        rationale=str(row["rationale"]),
        model_name=(
            None if row["model_name"] is None else str(row["model_name"])
        ),
        model_version=(
            None if row["model_version"] is None else str(row["model_version"])
        ),
        prompt_version=(
            None
            if row["prompt_version"] is None
            else str(row["prompt_version"])
        ),
        confidence=(
            None if row["confidence"] is None else float(row["confidence"])
        ),
        conflict_codes=tuple(
            str(value)
            for value in json.loads(str(row["conflict_codes_json"] or "[]"))
        ),
        decision_by=(
            None if row["decision_by"] is None else str(row["decision_by"])
        ),
        decision_note=(
            None if row["decision_note"] is None else str(row["decision_note"])
        ),
        decided_at=(
            None if row["decided_at"] is None else str(row["decided_at"])
        ),
        revision=int(row["revision"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _required_text(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} must be nonblank")
    return text


def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _source_kind(value: object) -> str:
    normalized = _required_text(value, "source_kind").casefold()
    if normalized not in {"system", "model", "teacher", "import"}:
        raise ValueError("source_kind is invalid")
    return normalized


def _optional_confidence(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("confidence must be between zero and one")
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("confidence must be between zero and one") from exc
    if not 0.0 <= normalized <= 1.0:
        raise ValueError("confidence must be between zero and one")
    return normalized


__all__ = [
    "ActiveKnowledgeRelation",
    "KnowledgeIdentityNotFound",
    "KnowledgeIdentityRecord",
    "KnowledgeIdentityRetired",
    "KnowledgeRelationConfirmationConflict",
    "KnowledgeRelationDuplicate",
    "KnowledgeRelationNotFound",
    "KnowledgeRelationRecord",
    "KnowledgeRelationRepository",
    "KnowledgeRelationRepositoryError",
    "KnowledgeRelationRevisionConflict",
    "KnowledgeRelationTransitionError",
]
