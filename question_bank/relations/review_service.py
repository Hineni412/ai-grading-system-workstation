from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from question_bank.database.schema import connect
from question_bank.relations.contracts import (
    KnowledgeRelation,
    RelationConflict,
    RelationStatus,
    RelationType,
    find_confirmation_conflicts,
)
from question_bank.relations.repository import (
    KnowledgeRelationConfirmationConflict,
    KnowledgeRelationDuplicate,
    KnowledgeRelationNotFound,
    KnowledgeRelationRecord,
    KnowledgeRelationRepository,
    KnowledgeRelationRevisionConflict,
    KnowledgeRelationTransitionError,
)


@dataclass(frozen=True, slots=True)
class RelationReviewCommand:
    relation_id: str
    expected_revision: int
    action: str
    reason: str

    def __post_init__(self) -> None:
        action = str(self.action or "").strip().casefold()
        if action not in {"confirm", "reject", "retire", "restore"}:
            raise ValueError("batch relation action is invalid")
        if isinstance(self.expected_revision, bool) or int(
            self.expected_revision
        ) < 1:
            raise ValueError("expected_revision must be a positive integer")
        if not str(self.relation_id or "").strip():
            raise ValueError("relation_id must be nonblank")
        if not str(self.reason or "").strip():
            raise ValueError("reason must be nonblank")
        object.__setattr__(self, "action", action)


class RelationReviewService:
    """Teacher-only relation decisions with impact preview and partial results."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.repository = KnowledgeRelationRepository(self.db_path)

    def list_queue(
        self,
        *,
        status: RelationStatus | str = RelationStatus.SUGGESTED,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, object]:
        self.repository.current_release_id()
        normalized_status = RelationStatus(status)
        normalized_page = int(page)
        normalized_page_size = int(page_size)
        if normalized_page < 1:
            raise ValueError("page must be positive")
        if normalized_page_size < 1 or normalized_page_size > 100:
            raise ValueError("page_size must be between 1 and 100")
        offset = (normalized_page - 1) * normalized_page_size
        with connect(self.db_path) as connection:
            total = int(
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM knowledge_relations relation
                    JOIN knowledge_graph_releases current_release
                      ON current_release.release_id = relation.graph_release_id
                     AND current_release.status = 'active'
                    WHERE relation.status = ?
                    """,
                    (normalized_status.value,),
                ).fetchone()[0]
            )
            rows = connection.execute(
                """
                SELECT
                    relation.*,
                    source_identity.display_name AS source_name,
                    target_identity.display_name AS target_name
                FROM knowledge_relations relation
                JOIN knowledge_tag_identities source_identity
                  ON source_identity.stable_key = relation.source_key
                JOIN knowledge_tag_identities target_identity
                  ON target_identity.stable_key = relation.target_key
                JOIN knowledge_graph_releases current_release
                  ON current_release.release_id = relation.graph_release_id
                 AND current_release.status = 'active'
                WHERE relation.status = ?
                ORDER BY relation.updated_at, relation.relation_id
                LIMIT ? OFFSET ?
                """,
                (
                    normalized_status.value,
                    normalized_page_size,
                    offset,
                ),
            ).fetchall()
        return {
            "status": normalized_status.value,
            "items": [
                {
                    "relation_id": str(row["relation_id"]),
                    "source_key": str(row["source_key"]),
                    "source_name": str(row["source_name"]),
                    "target_key": str(row["target_key"]),
                    "target_name": str(row["target_name"]),
                    "relation_type": str(row["relation_type"]),
                    "source_kind": str(row["source_kind"]),
                    "source_reference": row["source_reference"],
                    "rationale": str(row["rationale"]),
                    "model_name": row["model_name"],
                    "model_version": row["model_version"],
                    "prompt_version": row["prompt_version"],
                    "confidence": row["confidence"],
                    "conflict_codes": _json_string_list(
                        row["conflict_codes_json"]
                    ),
                    "revision": int(row["revision"]),
                    "updated_at": str(row["updated_at"]),
                }
                for row in rows
            ],
            "total": total,
            "page": normalized_page,
            "page_size": normalized_page_size,
            "total_pages": max(
                1,
                (total + normalized_page_size - 1)
                // normalized_page_size,
            ),
        }

    def preview(
        self,
        relation_id: str,
        *,
        action: str,
        amended_relation: KnowledgeRelation | None = None,
    ) -> dict[str, object]:
        current = self.repository.get_current_relation(relation_id)
        normalized_action = str(action or "").strip().casefold()
        candidate = (
            amended_relation
            if amended_relation is not None
            else current.as_contract()
        )
        conflicts: tuple[RelationConflict, ...] = ()
        target_status: RelationStatus | None = None
        allowed = True
        if normalized_action in {"confirm", "restore"}:
            target_status = RelationStatus.CONFIRMED
            conflicts = find_confirmation_conflicts(
                candidate,
                self._active_contracts(exclude_relation_id=current.relation_id),
            )
            allowed = (
                target_status
                in _legal_targets(current.status)
                and not conflicts
            )
        elif normalized_action == "reject":
            target_status = RelationStatus.REJECTED
            allowed = target_status in _legal_targets(current.status)
        elif normalized_action == "retire":
            target_status = RelationStatus.RETIRED
            allowed = target_status in _legal_targets(current.status)
        elif normalized_action == "amend":
            allowed = (
                current.status is RelationStatus.SUGGESTED
                and amended_relation is not None
            )
            if allowed:
                conflicts = find_confirmation_conflicts(
                    candidate,
                    self._active_contracts(),
                )
        else:
            raise ValueError("relation review action is invalid")
        return {
            "relation_id": current.relation_id,
            "current_status": current.status.value,
            "action": normalized_action,
            "target_status": (
                None if target_status is None else target_status.value
            ),
            "can_apply": allowed,
            "conflict_codes": [
                conflict.value for conflict in conflicts
            ],
            "activity_effect": (
                "include_in_next_standard_candidate"
                if normalized_action in {"confirm", "restore"}
                else "exclude_from_next_standard_candidate"
                if normalized_action == "retire"
                else "no_current_graph_change"
            ),
            "recommendation_effect": (
                "unchanged_until_next_standard"
                if normalized_action in {"confirm", "restore", "retire"}
                else "none"
            ),
            "revision": current.revision,
        }

    def review_one(
        self,
        relation_id: str,
        *,
        expected_revision: int,
        action: str,
        actor_ref: str,
        reason: str,
        amended_relation: KnowledgeRelation | None = None,
    ) -> dict[str, object]:
        normalized_action = str(action or "").strip().casefold()
        if normalized_action == "amend":
            if amended_relation is None:
                raise ValueError("amended_relation is required")
            updated = self.repository.amend_suggestion(
                relation_id,
                expected_revision=expected_revision,
                relation=amended_relation,
                actor_ref=actor_ref,
                reason=reason,
            )
        else:
            target_status = _action_status(normalized_action)
            preview = self.preview(
                relation_id,
                action=normalized_action,
            )
            if (
                target_status is RelationStatus.CONFIRMED
                and preview["conflict_codes"]
            ):
                raise KnowledgeRelationConfirmationConflict(
                    tuple(
                        RelationConflict(value)
                        for value in preview["conflict_codes"]
                    )
                )
            updated = self.repository.transition(
                relation_id,
                expected_revision=expected_revision,
                to_status=target_status,
                actor_ref=actor_ref,
                reason=reason,
            )
        return {
            "relation": _public_relation(updated),
            "timeline": list(self.timeline(updated.relation_id)),
        }

    def review_batch(
        self,
        commands: Sequence[RelationReviewCommand],
        *,
        actor_ref: str,
    ) -> dict[str, object]:
        normalized = tuple(commands)
        if not normalized or len(normalized) > 20:
            raise ValueError("batch review requires between 1 and 20 commands")
        relation_ids = [command.relation_id for command in normalized]
        if len(relation_ids) != len(set(relation_ids)):
            raise ValueError("batch review relation_id values must be unique")

        preflight_failures = self._batch_confirmation_conflicts(normalized)
        results: list[dict[str, object]] = []
        for command in normalized:
            if command.relation_id in preflight_failures:
                results.append(
                    {
                        "relation_id": command.relation_id,
                        "status": "failed",
                        "category": "confirmation_conflict",
                        "conflict_codes": [
                            conflict.value
                            for conflict in preflight_failures[
                                command.relation_id
                            ]
                        ],
                    }
                )
                continue
            try:
                reviewed = self.review_one(
                    command.relation_id,
                    expected_revision=command.expected_revision,
                    action=command.action,
                    actor_ref=actor_ref,
                    reason=command.reason,
                )
            except KnowledgeRelationRevisionConflict as exc:
                results.append(
                    {
                        "relation_id": command.relation_id,
                        "status": "failed",
                        "category": "revision_conflict",
                        "current_revision": exc.current_revision,
                    }
                )
            except KnowledgeRelationNotFound:
                results.append(
                    {
                        "relation_id": command.relation_id,
                        "status": "failed",
                        "category": "not_found",
                    }
                )
            except (
                KnowledgeRelationConfirmationConflict,
                KnowledgeRelationDuplicate,
                KnowledgeRelationTransitionError,
                ValueError,
            ):
                results.append(
                    {
                        "relation_id": command.relation_id,
                        "status": "failed",
                        "category": "invalid_decision",
                    }
                )
            else:
                results.append(
                    {
                        "relation_id": command.relation_id,
                        "status": "applied",
                        "relation": reviewed["relation"],
                    }
                )
        applied = sum(result["status"] == "applied" for result in results)
        return {
            "status": (
                "applied"
                if applied == len(results)
                else "failed"
                if applied == 0
                else "partial"
            ),
            "applied_count": applied,
            "failed_count": len(results) - applied,
            "results": results,
        }

    def timeline(
        self,
        relation_id: str,
    ) -> tuple[dict[str, object], ...]:
        self.repository.get_current_relation(relation_id)
        events: list[dict[str, object]] = []
        for event in self.repository.audit_events(relation_id):
            events.append(
                {
                    "event_type": str(event["event_type"]),
                    "actor_kind": str(event["actor_kind"]),
                    "actor_ref": event["actor_ref"],
                    "reason": str(event["reason"]),
                    "from_status": event["from_status"],
                    "to_status": str(event["to_status"]),
                    "revision": int(event["resulting_revision"]),
                    "created_at": str(event["created_at"]),
                }
            )
        for event in self.repository.amendment_events(relation_id):
            events.append(
                {
                    "event_type": "amended",
                    "actor_kind": "teacher",
                    "actor_ref": str(event["actor_ref"]),
                    "reason": str(event["reason"]),
                    "from_status": "suggested",
                    "to_status": "suggested",
                    "revision": int(event["resulting_revision"]),
                    "created_at": str(event["created_at"]),
                    "change": {
                        "from": {
                            "source_key": str(event["from_source_key"]),
                            "target_key": str(event["from_target_key"]),
                            "relation_type": str(
                                event["from_relation_type"]
                            ),
                        },
                        "to": {
                            "source_key": str(event["to_source_key"]),
                            "target_key": str(event["to_target_key"]),
                            "relation_type": str(event["to_relation_type"]),
                        },
                    },
                }
            )
        events.sort(key=lambda item: int(item["revision"]))
        return tuple(events)

    def _batch_confirmation_conflicts(
        self,
        commands: Sequence[RelationReviewCommand],
    ) -> dict[str, tuple[RelationConflict, ...]]:
        confirm_records: list[KnowledgeRelationRecord] = []
        failures: dict[str, tuple[RelationConflict, ...]] = {}
        for command in commands:
            if command.action not in {"confirm", "restore"}:
                continue
            try:
                record = self.repository.get_current_relation(
                    command.relation_id
                )
            except KnowledgeRelationNotFound:
                continue
            confirm_records.append(record)
        active = self._active_contracts(
            exclude_relation_ids={
                record.relation_id for record in confirm_records
            }
        )
        for record in confirm_records:
            other_batch = tuple(
                KnowledgeRelation(
                    source_key=other.source_key,
                    target_key=other.target_key,
                    relation_type=other.relation_type,
                    status=RelationStatus.CONFIRMED,
                )
                for other in confirm_records
                if other.relation_id != record.relation_id
            )
            conflicts = find_confirmation_conflicts(
                record.as_contract(),
                (*active, *other_batch),
            )
            if conflicts:
                failures[record.relation_id] = conflicts
        return failures

    def _active_contracts(
        self,
        *,
        exclude_relation_id: str | None = None,
        exclude_relation_ids: set[str] | None = None,
    ) -> tuple[KnowledgeRelation, ...]:
        excluded = set(exclude_relation_ids or ())
        if exclude_relation_id is not None:
            excluded.add(exclude_relation_id)
        return tuple(
            KnowledgeRelation(
                source_key=item.source_key,
                target_key=item.target_key,
                relation_type=item.relation_type,
                status=RelationStatus.CONFIRMED,
            )
            for item in self.repository.list_active_relations()
            if item.relation_id not in excluded
        )


def _legal_targets(status: RelationStatus) -> frozenset[RelationStatus]:
    return {
        RelationStatus.SUGGESTED: frozenset(
            {RelationStatus.CONFIRMED, RelationStatus.REJECTED}
        ),
        RelationStatus.CONFIRMED: frozenset({RelationStatus.RETIRED}),
        RelationStatus.REJECTED: frozenset(),
        RelationStatus.RETIRED: frozenset({RelationStatus.CONFIRMED}),
    }[status]


def _action_status(action: str) -> RelationStatus:
    mapping = {
        "confirm": RelationStatus.CONFIRMED,
        "restore": RelationStatus.CONFIRMED,
        "reject": RelationStatus.REJECTED,
        "retire": RelationStatus.RETIRED,
    }
    try:
        return mapping[action]
    except KeyError as exc:
        raise ValueError("relation review action is invalid") from exc


def _public_relation(record: KnowledgeRelationRecord) -> dict[str, object]:
    return {
        "relation_id": record.relation_id,
        "source_key": record.source_key,
        "target_key": record.target_key,
        "relation_type": record.relation_type.value,
        "status": record.status.value,
        "source_kind": record.source_kind,
        "rationale": record.rationale,
        "model_name": record.model_name,
        "model_version": record.model_version,
        "prompt_version": record.prompt_version,
        "confidence": record.confidence,
        "conflict_codes": list(record.conflict_codes),
        "decision_by": record.decision_by,
        "decision_note": record.decision_note,
        "decided_at": record.decided_at,
        "revision": record.revision,
        "updated_at": record.updated_at,
    }


def _json_string_list(value: object) -> list[str]:
    import json

    try:
        loaded = json.loads(str(value or "[]"))
    except (TypeError, ValueError):
        return []
    if not isinstance(loaded, list):
        return []
    return [str(item) for item in loaded if str(item or "").strip()]


__all__ = [
    "RelationReviewCommand",
    "RelationReviewService",
]
