from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping, Sequence
from uuid import uuid4

from backend.teaching_prep.domain.errors import (
    TeachingPrepConflictError,
    TeachingPrepNotFoundError,
    TeachingPrepRetryAvailableError,
    TeachingPrepStateError,
)
from backend.teaching_prep.domain.models import (
    ReferencePptCollection,
    ReferencePptCollectionMember,
    SemesterMappingProposal,
)
from backend.teaching_prep.infrastructure.database import TeachingPrepDatabase


class SemesterMappingRepository:
    def __init__(self, database: TeachingPrepDatabase) -> None:
        self._database = database

    def snapshot(
        self,
        semester_id: str,
        material_record_ids: Sequence[str],
    ) -> tuple[dict[str, object], str]:
        with self._database.connect() as connection:
            return self._snapshot(
                connection,
                semester_id,
                material_record_ids,
            )

    def find_generation(
        self,
        *,
        operation_id: str,
        request_hash: str,
    ) -> SemesterMappingProposal | None:
        with self._database.connect() as connection:
            operation = connection.execute(
                """
                SELECT *
                FROM teaching_prep_operations
                WHERE operation_id = ?
                """,
                (operation_id,),
            ).fetchone()
            if operation is None:
                operation = self._find_active_semantic_generation(
                    connection,
                    request_hash=request_hash,
                )
                if operation is None:
                    return None
            return self._resolve_generation(
                connection,
                operation=operation,
                request_hash=request_hash,
            )

    def begin_generation(
        self,
        *,
        operation_id: str,
        request_hash: str,
        semester_id: str,
    ) -> SemesterMappingProposal | None:
        with self._database.connect(immediate=True) as connection:
            if connection.execute(
                "SELECT 1 FROM teaching_semesters WHERE id = ?",
                (semester_id,),
            ).fetchone() is None:
                raise TeachingPrepNotFoundError("semester was not found")
            existing = connection.execute(
                """
                SELECT request_hash, status
                FROM teaching_prep_operations
                WHERE operation_id = ?
                """,
                (operation_id,),
            ).fetchone()
            if existing is not None:
                if str(existing["request_hash"]) != request_hash:
                    raise TeachingPrepConflictError(
                        "operation ID was reused for different mapping input"
                    )
                operation_status = str(existing["status"])
                if operation_status == "failed":
                    raise TeachingPrepRetryAvailableError(
                        "previous semester mapping did not complete"
                    )
                if operation_status == "interrupted":
                    raise TeachingPrepStateError(
                        "semester mapping result is unknown after application restart; "
                        "automatic retry is blocked"
                    )
                raise TeachingPrepStateError(
                    "semester mapping already started; wait for its result"
                )
            semantic = self._find_active_semantic_generation(
                connection,
                request_hash=request_hash,
            )
            if semantic is not None:
                return self._resolve_generation(
                    connection,
                    operation=semantic,
                    request_hash=request_hash,
                )
            connection.execute(
                """
                INSERT INTO teaching_prep_operations (
                    operation_id,
                    operation_type,
                    idempotency_key,
                    request_hash,
                    target_kind,
                    target_id,
                    status,
                    error_code
                )
                VALUES (?, 'semester_mapping_model', ?, ?, 'semester', ?, 'running',
                        'semester_mapping_model_call_pending')
                """,
                (
                    operation_id,
                    operation_id,
                    request_hash,
                    semester_id,
                ),
            )
        return None

    def mark_generation_model_call_started(self, operation_id: str) -> None:
        with self._database.connect(immediate=True) as connection:
            updated = connection.execute(
                """
                UPDATE teaching_prep_operations
                SET error_code = 'semester_mapping_model_call_started',
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE operation_id = ?
                  AND status = 'running'
                  AND error_code = 'semester_mapping_model_call_pending'
                """,
                (operation_id,),
            ).rowcount
            if updated != 1:
                raise TeachingPrepStateError(
                    "semester mapping operation is no longer ready to call the model"
                )

    def finish_generation(
        self,
        *,
        operation_id: str,
        semester_id: str,
        source_state_sha256: str,
        payload: dict[str, object],
    ) -> SemesterMappingProposal:
        with self._database.connect(immediate=True) as connection:
            operation = connection.execute(
                """
                SELECT status, target_id
                FROM teaching_prep_operations
                WHERE operation_id = ?
                """,
                (operation_id,),
            ).fetchone()
            if (
                operation is None
                or str(operation["status"]) != "running"
                or str(operation["target_id"]) != semester_id
            ):
                raise TeachingPrepConflictError(
                    "mapping operation is no longer running"
                )
            proposal_id = uuid4().hex
            reviewable_payload = dict(payload)
            reviewable_payload["mappings"] = [
                {
                    **dict(item),
                    "mapping_id": uuid4().hex,
                    "decision": "pending",
                    "teacher_revision": None,
                    "decision_reason": None,
                }
                for item in list(payload.get("mappings") or [])
            ]
            connection.execute(
                """
                INSERT INTO semester_mapping_proposals (
                    id,
                    semester_id,
                    operation_id,
                    source_state_sha256,
                    payload_json
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    proposal_id,
                    semester_id,
                    operation_id,
                    source_state_sha256,
                    _json(reviewable_payload),
                ),
            )
            connection.execute(
                """
                UPDATE teaching_prep_operations
                SET status = 'succeeded',
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    finished_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE operation_id = ? AND status = 'running'
                """,
                (operation_id,),
            )
            return self._get(connection, proposal_id)

    def review_mapping(
        self,
        proposal_id: str,
        mapping_id: str,
        *,
        expected_revision: int,
        decision: dict[str, object],
    ) -> SemesterMappingProposal:
        with self._database.connect(immediate=True) as connection:
            proposal = self._get(connection, proposal_id)
            if proposal.status != "proposed":
                raise TeachingPrepConflictError(
                    "mapping proposal can no longer be reviewed"
                )
            if proposal.revision != expected_revision:
                raise TeachingPrepConflictError(
                    "mapping proposal changed; refresh before reviewing"
                )
            payload = dict(proposal.payload)
            mappings = [dict(item) for item in payload["mappings"]]
            target = next(
                (
                    item
                    for item in mappings
                    if str(item.get("mapping_id") or "") == mapping_id
                ),
                None,
            )
            if target is None:
                raise TeachingPrepNotFoundError(
                    "mapping proposal row was not found"
                )
            material_ids = tuple(
                str(item)
                for item in proposal.payload["source_material_record_ids"]
            )
            snapshot, source_sha = self._snapshot(
                connection, proposal.semester_id, material_ids
            )
            if not _source_state_matches(
                connection, proposal, snapshot, source_sha
            ):
                raise TeachingPrepConflictError(
                    "semester lessons or materials changed; review a new proposal"
                )
            normalized = dict(decision)
            if normalized["decision"] in {"accepted", "modified"}:
                material = next(
                    item
                    for item in snapshot["materials"]
                    if str(item["record_id"])
                    == str(target["material_record_id"])
                )
                start = int(normalized["start_unit"])
                end = int(normalized["end_unit"])
                if start < 1 or end < start or end > int(material["unit_count"]):
                    raise TeachingPrepConflictError(
                        "reviewed mapping range is outside the material"
                    )
                valid_lessons = {
                    str(item["id"])
                    for item in snapshot["lessons"]
                    if item["node_type"] == "lesson"
                }
                valid_lessons.update(
                    f"proposal:{lesson['key']}"
                    for chapter in payload["tree"]
                    for section in chapter["sections"]
                    for lesson in section["lessons"]
                )
                if str(normalized["lesson_ref"]) not in valid_lessons:
                    raise TeachingPrepConflictError(
                        "reviewed mapping lesson is no longer available"
                    )
            target["decision"] = str(normalized["decision"])
            target["decision_reason"] = normalized.get("reason")
            target["teacher_revision"] = (
                {
                    "lesson_ref": normalized["lesson_ref"],
                    "start_unit": normalized["start_unit"],
                    "end_unit": normalized["end_unit"],
                }
                if normalized["decision"] == "modified"
                else None
            )
            payload["mappings"] = mappings
            updated = connection.execute(
                """
                UPDATE semester_mapping_proposals
                SET payload_json = ?, revision = revision + 1,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ? AND revision = ? AND status = 'proposed'
                """,
                (_json(payload), proposal.id, expected_revision),
            ).rowcount
            if updated != 1:
                raise TeachingPrepConflictError(
                    "mapping proposal changed; refresh before reviewing"
                )
            return self._get(connection, proposal.id)

    def accept_local_high_confidence(
        self,
        proposal_id: str,
        *,
        expected_revision: int,
    ) -> SemesterMappingProposal:
        with self._database.connect(immediate=True) as connection:
            proposal = self._get(connection, proposal_id)
            if proposal.status != "proposed" or proposal.revision != expected_revision:
                raise TeachingPrepConflictError(
                    "mapping proposal changed; refresh before reviewing"
                )
            payload = dict(proposal.payload)
            if payload.get("generation_source") != "local_reference_ppt_names":
                raise TeachingPrepConflictError(
                    "only a local reference PPT proposal supports bulk review"
                )
            material_ids = tuple(
                str(item) for item in payload["source_material_record_ids"]
            )
            _snapshot, digest = self._snapshot(
                connection,
                proposal.semester_id,
                material_ids,
            )
            if not _source_state_matches(
                connection, proposal, _snapshot, digest
            ):
                raise TeachingPrepConflictError(
                    "semester lessons or materials changed; review a new proposal"
                )
            mappings = [dict(item) for item in payload["mappings"]]
            changed = 0
            for mapping in mappings:
                if (
                    str(mapping.get("decision") or "pending") == "pending"
                    and str(mapping.get("confidence") or "") == "high"
                ):
                    mapping["decision"] = "accepted"
                    mapping["decision_reason"] = (
                        "教师批量确认本机高置信课件命名建议"
                    )
                    mapping["teacher_revision"] = None
                    changed += 1
            if changed == 0:
                return proposal
            payload["mappings"] = mappings
            updated = connection.execute(
                """
                UPDATE semester_mapping_proposals
                SET payload_json = ?, revision = revision + 1,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ? AND revision = ? AND status = 'proposed'
                """,
                (_json(payload), proposal.id, expected_revision),
            ).rowcount
            if updated != 1:
                raise TeachingPrepConflictError(
                    "mapping proposal changed; refresh before reviewing"
                )
            return self._get(connection, proposal.id)

    def reject(
        self,
        proposal_id: str,
        *,
        expected_revision: int,
    ) -> SemesterMappingProposal:
        with self._database.connect(immediate=True) as connection:
            proposal = self._get(connection, proposal_id)
            if proposal.status == "rejected":
                return proposal
            if proposal.status != "proposed" or proposal.revision != expected_revision:
                raise TeachingPrepConflictError(
                    "mapping proposal changed; refresh before rejecting"
                )
            connection.execute(
                """
                UPDATE semester_mapping_proposals
                SET status = 'rejected', revision = revision + 1,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ? AND revision = ? AND status = 'proposed'
                """,
                (proposal.id, expected_revision),
            )
            return self._get(connection, proposal.id)

    def fail_generation(self, operation_id: str, error_code: str) -> None:
        with self._database.connect(immediate=True) as connection:
            connection.execute(
                """
                UPDATE teaching_prep_operations
                SET status = 'failed',
                    error_code = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    finished_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE operation_id = ? AND status = 'running'
                """,
                (error_code, operation_id),
            )

    def mark_generation_result_unknown(
        self,
        operation_id: str,
        error_code: str,
    ) -> None:
        """Keep an indeterminate physical model call behind the durable guard."""
        with self._database.connect(immediate=True) as connection:
            connection.execute(
                """
                UPDATE teaching_prep_operations
                SET status = 'interrupted',
                    error_code = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    finished_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE operation_id = ? AND status = 'running'
                """,
                (error_code, operation_id),
            )

    def discard_interrupted_generation(self, *, request_hash: str) -> bool:
        """Release one semantic request only after the teacher discards it."""
        with self._database.connect(immediate=True) as connection:
            changed = connection.execute(
                """
                UPDATE teaching_prep_operations
                SET status = 'cancelled',
                    error_code = 'semester_mapping_result_discarded',
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    finished_at = COALESCE(
                        finished_at,
                        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    )
                WHERE operation_type = 'semester_mapping_model'
                  AND request_hash = ?
                  AND status = 'interrupted'
                """,
                (request_hash,),
            ).rowcount
        return changed > 0

    def list(self, semester_id: str) -> tuple[SemesterMappingProposal, ...]:
        with self._database.connect() as connection:
            if connection.execute(
                "SELECT 1 FROM teaching_semesters WHERE id = ?",
                (semester_id,),
            ).fetchone() is None:
                raise TeachingPrepNotFoundError("semester was not found")
            rows = connection.execute(
                """
                SELECT *
                FROM semester_mapping_proposals
                WHERE semester_id = ?
                ORDER BY created_at DESC, id DESC
                """,
                (semester_id,),
            ).fetchall()
        return tuple(_proposal(row) for row in rows)

    def create_local_reference_ppt_collection(
        self,
        *,
        semester_id: str,
        request_token: str,
        display_name: str,
        ignored_file_count: int,
        material_record_ids: Sequence[str],
        payload: dict[str, object],
    ) -> tuple[ReferencePptCollection, bool]:
        with self._database.connect(immediate=True) as connection:
            snapshot, source_digest = self._snapshot(
                connection,
                semester_id,
                material_record_ids,
            )
            request_values = {
                "semester_id": semester_id,
                "display_name": display_name,
                "ignored_file_count": ignored_file_count,
                "material_record_ids": list(material_record_ids),
                "source_state_sha256": source_digest,
                "members": payload.get("collection_members", []),
            }
            request_hash = _digest(request_values)
            existing = connection.execute(
                """
                SELECT id, request_hash
                FROM reference_ppt_collections
                WHERE request_token = ?
                """,
                (request_token,),
            ).fetchone()
            if existing is not None:
                if str(existing["request_hash"]) != request_hash:
                    raise TeachingPrepConflictError(
                        "reference PPT collection request token was reused"
                    )
                return self._get_reference_ppt_collection(
                    connection,
                    str(existing["id"]),
                ), False
            snapshot_ids = {
                str(item["record_id"])
                for item in snapshot["materials"]
            }
            if snapshot_ids != set(material_record_ids):
                raise TeachingPrepConflictError(
                    "reference PPT collection materials changed"
                )
            if any(
                str(item.get("material_role") or "") != "reference_ppt"
                for item in snapshot["materials"]
            ):
                raise TeachingPrepConflictError(
                    "reference PPT collection only accepts reference PPT materials"
                )
            operation_id = f"ppt-collection-{uuid4().hex}"
            collection_id = uuid4().hex
            proposal_id = uuid4().hex
            members = list(payload.get("collection_members") or [])
            reviewable_payload = dict(payload)
            reviewable_payload.pop("collection_members", None)
            reviewable_payload["mappings"] = [
                {
                    **dict(item),
                    "mapping_id": uuid4().hex,
                    "decision": "pending",
                    "teacher_revision": None,
                    "decision_reason": item.get("decision_reason"),
                }
                for item in list(payload.get("mappings") or [])
            ]
            connection.execute(
                """
                INSERT INTO teaching_prep_operations (
                    operation_id,
                    operation_type,
                    idempotency_key,
                    request_hash,
                    target_kind,
                    target_id,
                    status,
                    error_code,
                    finished_at
                )
                VALUES (?, 'reference_ppt_collection_local', ?, ?,
                        'semester', ?, 'succeeded', NULL,
                        strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                """,
                (
                    operation_id,
                    request_token,
                    request_hash,
                    semester_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO semester_mapping_proposals (
                    id,
                    semester_id,
                    operation_id,
                    source_state_sha256,
                    payload_json
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    proposal_id,
                    semester_id,
                    operation_id,
                    source_digest,
                    _json(reviewable_payload),
                ),
            )
            connection.execute(
                """
                INSERT INTO reference_ppt_collections (
                    id,
                    semester_id,
                    request_token,
                    request_hash,
                    display_name,
                    mapping_proposal_id,
                    ignored_file_count
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    collection_id,
                    semester_id,
                    request_token,
                    request_hash,
                    display_name,
                    proposal_id,
                    ignored_file_count,
                ),
            )
            if len(members) != len(material_record_ids):
                raise TeachingPrepConflictError(
                    "reference PPT collection member count is invalid"
                )
            for member in members:
                item = dict(member)
                record_id = str(item.get("material_record_id") or "")
                if record_id not in snapshot_ids:
                    raise TeachingPrepConflictError(
                        "reference PPT collection contains an unknown material"
                    )
                connection.execute(
                    """
                    INSERT INTO reference_ppt_collection_members (
                        id,
                        collection_id,
                        material_record_id,
                        relative_path,
                        kind,
                        confidence,
                        chapter_number,
                        section_number,
                        subsection_number,
                        lesson_number,
                        normalized_title,
                        evidence_json,
                        issues_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        uuid4().hex,
                        collection_id,
                        record_id,
                        str(item["relative_path"]),
                        str(item["kind"]),
                        str(item["confidence"]),
                        item.get("chapter_number"),
                        item.get("section_number"),
                        item.get("subsection_number"),
                        item.get("lesson_number"),
                        str(item["title"]),
                        _json(list(item.get("evidence") or [])),
                        _json(list(item.get("issues") or [])),
                    ),
                )
            return self._get_reference_ppt_collection(
                connection,
                collection_id,
            ), True

    def list_reference_ppt_collections(
        self,
        semester_id: str,
        *,
        include_inactive: bool = False,
    ) -> tuple[ReferencePptCollection, ...]:
        with self._database.connect() as connection:
            if connection.execute(
                "SELECT 1 FROM teaching_semesters WHERE id = ?",
                (semester_id,),
            ).fetchone() is None:
                raise TeachingPrepNotFoundError("semester was not found")
            rows = connection.execute(
                """
                SELECT id
                FROM reference_ppt_collections
                WHERE semester_id = ?
                  AND (? OR is_active = 1)
                ORDER BY created_at DESC, id DESC
                """,
                (semester_id, int(include_inactive)),
            ).fetchall()
            return tuple(
                self._get_reference_ppt_collection(connection, str(row["id"]))
                for row in rows
            )

    def update_reference_ppt_collection(
        self,
        collection_id: str,
        *,
        is_active: bool,
    ) -> ReferencePptCollection:
        with self._database.connect(immediate=True) as connection:
            cursor = connection.execute(
                """
                UPDATE reference_ppt_collections
                SET is_active = ?,
                    revision = revision + 1,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ?
                """,
                (int(is_active), collection_id),
            )
            if cursor.rowcount != 1:
                raise TeachingPrepNotFoundError(
                    "reference PPT collection was not found"
                )
            return self._get_reference_ppt_collection(
                connection,
                collection_id,
            )

    @staticmethod
    def _get_reference_ppt_collection(
        connection: sqlite3.Connection,
        collection_id: str,
    ) -> ReferencePptCollection:
        row = connection.execute(
            "SELECT * FROM reference_ppt_collections WHERE id = ?",
            (collection_id,),
        ).fetchone()
        if row is None:
            raise TeachingPrepNotFoundError(
                "reference PPT collection was not found"
            )
        member_rows = connection.execute(
            """
            SELECT *
            FROM reference_ppt_collection_members
            WHERE collection_id = ?
            ORDER BY relative_path, id
            """,
            (collection_id,),
        ).fetchall()
        return ReferencePptCollection(
            id=str(row["id"]),
            semester_id=str(row["semester_id"]),
            display_name=str(row["display_name"]),
            mapping_proposal_id=str(row["mapping_proposal_id"]),
            ignored_file_count=int(row["ignored_file_count"]),
            is_active=bool(row["is_active"]),
            revision=int(row["revision"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            members=tuple(
                ReferencePptCollectionMember(
                    id=str(member["id"]),
                    collection_id=str(member["collection_id"]),
                    material_record_id=str(member["material_record_id"]),
                    relative_path=str(member["relative_path"]),
                    kind=str(member["kind"]),
                    confidence=str(member["confidence"]),
                    chapter_number=(
                        int(member["chapter_number"])
                        if member["chapter_number"] is not None
                        else None
                    ),
                    section_number=(
                        int(member["section_number"])
                        if member["section_number"] is not None
                        else None
                    ),
                    subsection_number=(
                        int(member["subsection_number"])
                        if member["subsection_number"] is not None
                        else None
                    ),
                    lesson_number=(
                        int(member["lesson_number"])
                        if member["lesson_number"] is not None
                        else None
                    ),
                    normalized_title=str(member["normalized_title"]),
                    evidence=tuple(json.loads(str(member["evidence_json"]))),
                    issues=tuple(json.loads(str(member["issues_json"]))),
                    created_at=str(member["created_at"]),
                )
                for member in member_rows
            ),
        )

    def apply(
        self,
        proposal_id: str,
        *,
        expected_revision: int,
        chapter_key: str | None = None,
    ) -> SemesterMappingProposal:
        with self._database.connect(immediate=True) as connection:
            proposal = self._get(connection, proposal_id)
            if proposal.status == "applied":
                return proposal
            if proposal.status != "proposed":
                raise TeachingPrepConflictError(
                    "mapping proposal can no longer be applied"
                )
            if proposal.revision != expected_revision:
                raise TeachingPrepConflictError(
                    "mapping proposal changed; refresh before applying"
                )
            tree = [dict(item) for item in proposal.payload["tree"]]
            target_chapter: dict[str, object] | None = None
            target_refs: set[str] | None = None
            if chapter_key is not None:
                target_chapter = next(
                    (
                        dict(item)
                        for item in tree
                        if str(item["key"]) == chapter_key
                    ),
                    None,
                )
                if target_chapter is None:
                    raise TeachingPrepNotFoundError(
                        "proposal chapter was not found"
                    )
                target_refs = _chapter_lesson_refs(target_chapter)
            material_ids = tuple(
                str(item)
                for item in proposal.payload["source_material_record_ids"]
            )
            snapshot, digest = self._snapshot(
                connection,
                proposal.semester_id,
                material_ids,
            )
            if not _source_state_matches(
                connection,
                proposal,
                snapshot,
                digest,
            ):
                raise TeachingPrepConflictError(
                    "semester lessons or materials changed; generate a new proposal"
                )
            reviewed_mappings = [
                dict(item) for item in proposal.payload["mappings"]
            ]
            if target_refs is None:
                scoped_mappings = reviewed_mappings
                pending_message = (
                    "decide every mapping row before applying the proposal"
                )
            else:
                scoped_mappings = [
                    item
                    for item in reviewed_mappings
                    if _mapping_lesson_ref(item) in target_refs
                ]
                pending_message = (
                    "decide every mapping row in this chapter "
                    "before applying it"
                )
            if any(
                str(item.get("decision") or "pending") == "pending"
                for item in scoped_mappings
            ):
                raise TeachingPrepConflictError(pending_message)
            semester = connection.execute(
                """
                SELECT curriculum_id
                FROM teaching_semesters
                WHERE id = ?
                """,
                (proposal.semester_id,),
            ).fetchone()
            if semester is None:
                raise TeachingPrepNotFoundError("semester was not found")
            curriculum_id = str(semester["curriculum_id"])
            node_tokens = _proposal_node_tokens(proposal.id, tree)
            existing_rows = connection.execute(
                """
                SELECT id, node_type, request_token
                FROM lesson_nodes
                WHERE curriculum_id = ? AND is_active = 1
                """,
                (curriculum_id,),
            ).fetchall()
            if target_chapter is not None:
                chapter_row = connection.execute(
                    """
                    SELECT id
                    FROM lesson_nodes
                    WHERE request_token = ?
                    """,
                    (
                        _token(
                            "mapping-node",
                            proposal.id,
                            str(target_chapter["key"]),
                        ),
                    ),
                ).fetchone()
                if chapter_row is not None:
                    # Repeating a confirmed chapter is a no-op: its nodes
                    # and links were written by the first chapter apply.
                    return self._get(connection, proposal.id)
            if tree and any(
                str(row["node_type"]) == "lesson"
                and (
                    target_chapter is None
                    or str(row["request_token"]) not in node_tokens
                )
                for row in existing_rows
            ):
                raise TeachingPrepConflictError(
                    "lesson tree is no longer empty"
                )
            chapters_to_insert = (
                [target_chapter] if target_chapter is not None else tree
            )
            root_order_base = 0
            if chapters_to_insert:
                # Leftover chapter/section skeletons without lessons may
                # precede the initial tree; append new roots after them.
                root_order_base = int(
                    connection.execute(
                        """
                        SELECT COALESCE(MAX(sort_order), 0)
                        FROM lesson_nodes
                        WHERE curriculum_id = ? AND parent_id IS NULL
                        """,
                        (curriculum_id,),
                    ).fetchone()[0]
                )
            lesson_refs = {
                str(row["id"]): str(row["id"])
                for row in existing_rows
                if str(row["node_type"]) == "lesson"
            }
            for chapter_order, raw_chapter in enumerate(
                chapters_to_insert,
                start=1,
            ):
                chapter = dict(raw_chapter)
                chapter_id = self._insert_node(
                    connection,
                    proposal_id=proposal.id,
                    key=str(chapter["key"]),
                    curriculum_id=curriculum_id,
                    parent_id=None,
                    node_type="chapter",
                    title=str(chapter["title"]),
                    sort_order=root_order_base + chapter_order,
                    duration_minutes=None,
                )
                for section_order, raw_section in enumerate(
                    chapter["sections"],
                    start=1,
                ):
                    section = dict(raw_section)
                    section_id = self._insert_node(
                        connection,
                        proposal_id=proposal.id,
                        key=str(section["key"]),
                        curriculum_id=curriculum_id,
                        parent_id=chapter_id,
                        node_type="section",
                        title=str(section["title"]),
                        sort_order=section_order,
                        duration_minutes=None,
                    )
                    for lesson_order, raw_lesson in enumerate(
                        section["lessons"],
                        start=1,
                    ):
                        lesson = dict(raw_lesson)
                        lesson_id = self._insert_node(
                            connection,
                            proposal_id=proposal.id,
                            key=str(lesson["key"]),
                            curriculum_id=curriculum_id,
                            parent_id=section_id,
                            node_type="lesson",
                            title=str(lesson["title"]),
                            sort_order=lesson_order,
                            duration_minutes=int(
                                lesson["duration_minutes"]
                            ),
                        )
                        lesson_refs[
                            f"proposal:{lesson['key']}"
                        ] = lesson_id

            accepted_mappings = [
                item
                for item in scoped_mappings
                if str(item.get("decision")) in {"accepted", "modified"}
            ]
            for index, raw_mapping in enumerate(
                accepted_mappings,
                start=1,
            ):
                mapping = dict(raw_mapping)
                teacher_revision = mapping.get("teacher_revision")
                if isinstance(teacher_revision, dict):
                    mapping.update(teacher_revision)
                record_id = str(mapping["material_record_id"])
                material = connection.execute(
                    """
                    SELECT
                        record.material_source_id,
                        version.id AS material_version_id,
                        version.content_sha256
                    FROM semester_material_records AS record
                    JOIN material_versions AS version
                      ON version.id = (
                          SELECT latest.id
                          FROM material_versions AS latest
                          WHERE latest.source_id = record.material_source_id
                          ORDER BY latest.created_at DESC, latest.id DESC
                          LIMIT 1
                      )
                    WHERE record.id = ?
                      AND record.semester_id = ?
                      AND record.is_active = 1
                    """,
                    (record_id, proposal.semester_id),
                ).fetchone()
                if material is None:
                    raise TeachingPrepConflictError(
                        "semester material changed before apply"
                    )
                lesson_id = lesson_refs[str(mapping["lesson_ref"])]
                next_order = int(
                    connection.execute(
                        """
                        SELECT COALESCE(MAX(sort_order), 0) + 1
                        FROM lesson_material_links
                        WHERE lesson_node_id = ?
                        """,
                        (lesson_id,),
                    ).fetchone()[0]
                )
                link_values = {
                    "lesson_node_id": lesson_id,
                    "material_version_id": str(
                        material["material_version_id"]
                    ),
                    "start_unit": int(mapping["start_unit"]),
                    "end_unit": int(mapping["end_unit"]),
                    "crop": None,
                    "purpose": str(mapping["purpose"]),
                    "teacher_note": "学期建库建议已由教师确认",
                    "confirmation_status": "confirmed",
                }
                if target_chapter is None:
                    link_token = _token("mapping-link", proposal.id, str(index))
                else:
                    link_token = _token(
                        "mapping-link",
                        proposal.id,
                        str(target_chapter["key"]),
                        str(index),
                    )
                connection.execute(
                    """
                    INSERT INTO lesson_material_links (
                        id,
                        request_token,
                        request_hash,
                        lesson_node_id,
                        material_version_id,
                        start_unit,
                        end_unit,
                        crop_json,
                        purpose,
                        teacher_note,
                        confirmation_status,
                        source_version_sha256,
                        sort_order
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, 'confirmed', ?, ?)
                    """,
                    (
                        uuid4().hex,
                        link_token,
                        _digest(link_values),
                        lesson_id,
                        str(material["material_version_id"]),
                        int(mapping["start_unit"]),
                        int(mapping["end_unit"]),
                        str(mapping["purpose"]),
                        "学期建库建议已由教师确认",
                        str(material["content_sha256"]),
                        next_order,
                    ),
                )
            finalize = target_chapter is None or _all_chapters_applied(
                connection,
                proposal.id,
                tree,
            )
            if finalize:
                mapped_records = {
                    str(item["material_record_id"])
                    for item in reviewed_mappings
                    if str(item.get("decision")) in {"accepted", "modified"}
                }
                for record_id in material_ids:
                    connection.execute(
                        """
                        UPDATE semester_material_records
                        SET mapping_status = ?,
                            revision = revision + 1,
                            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                        WHERE id = ?
                        """,
                        (
                            "confirmed"
                            if record_id in mapped_records
                            else "needs_review",
                            record_id,
                        ),
                    )
                connection.execute(
                    """
                    UPDATE semester_mapping_proposals
                    SET status = 'applied',
                        revision = revision + 1,
                        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                        applied_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    WHERE id = ? AND revision = ? AND status = 'proposed'
                    """,
                    (proposal.id, expected_revision),
                )
            else:
                # Remaining chapters stay reviewable; only the optimistic
                # revision moves so the client refreshes before confirming
                # the next chapter.  Material mapping_status is updated
                # once, when the final chapter is applied.
                connection.execute(
                    """
                    UPDATE semester_mapping_proposals
                    SET revision = revision + 1,
                        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    WHERE id = ? AND revision = ? AND status = 'proposed'
                    """,
                    (proposal.id, expected_revision),
                )
            return self._get(connection, proposal.id)

    @staticmethod
    def _insert_node(
        connection: sqlite3.Connection,
        *,
        proposal_id: str,
        key: str,
        curriculum_id: str,
        parent_id: str | None,
        node_type: str,
        title: str,
        sort_order: int,
        duration_minutes: int | None,
    ) -> str:
        node_id = uuid4().hex
        values = {
            "curriculum_id": curriculum_id,
            "parent_id": parent_id,
            "node_type": node_type,
            "title": title,
            "duration_minutes": duration_minutes,
            "source_kind": "assistant_draft",
        }
        connection.execute(
            """
            INSERT INTO lesson_nodes (
                id,
                curriculum_id,
                parent_id,
                request_token,
                request_hash,
                node_type,
                title,
                sort_order,
                duration_minutes,
                source_kind
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'assistant_draft')
            """,
            (
                node_id,
                curriculum_id,
                parent_id,
                _token("mapping-node", proposal_id, key),
                _digest(values),
                node_type,
                title,
                sort_order,
                duration_minutes,
            ),
        )
        return node_id

    def _snapshot(
        self,
        connection: sqlite3.Connection,
        semester_id: str,
        material_record_ids: Sequence[str],
    ) -> tuple[dict[str, object], str]:
        semester = connection.execute(
            """
            SELECT semester.*, curriculum.title AS curriculum_title
            FROM teaching_semesters AS semester
            JOIN curriculum_editions AS curriculum
              ON curriculum.id = semester.curriculum_id
            WHERE semester.id = ?
            """,
            (semester_id,),
        ).fetchone()
        if semester is None:
            raise TeachingPrepNotFoundError("semester was not found")
        clean_ids = tuple(dict.fromkeys(material_record_ids))
        if not clean_ids:
            raise TeachingPrepConflictError(
                "at least one semester material is required"
            )
        placeholders = ",".join("?" for _item in clean_ids)
        material_rows = connection.execute(
            f"""
            SELECT
                record.*,
                source.display_name,
                version.id AS current_version_id,
                version.unit_count
            FROM semester_material_records AS record
            JOIN material_sources AS source
              ON source.id = record.material_source_id
            JOIN material_versions AS version
              ON version.id = (
                  SELECT latest.id
                  FROM material_versions AS latest
                  WHERE latest.source_id = record.material_source_id
                  ORDER BY latest.created_at DESC, latest.id DESC
                  LIMIT 1
              )
            WHERE record.semester_id = ?
              AND record.id IN ({placeholders})
              AND record.is_active = 1
            ORDER BY record.created_at, record.id
            """,
            (semester_id, *clean_ids),
        ).fetchall()
        if len(material_rows) != len(clean_ids):
            raise TeachingPrepNotFoundError(
                "one or more semester materials were not found"
            )
        materials: list[dict[str, object]] = []
        for row in material_rows:
            if (
                str(row["parse_status"]) != "parsed"
                or str(row["last_parsed_version_id"])
                != str(row["current_version_id"])
            ):
                raise TeachingPrepConflictError(
                    "all selected materials must be parsed first"
                )
            units = connection.execute(
                """
                SELECT
                    unit_index,
                    title,
                    extracted_text,
                    text_status,
                    object_summary_json,
                    preview_sha256
                FROM material_units
                WHERE material_version_id = ?
                ORDER BY unit_index
                """,
                (str(row["current_version_id"]),),
            ).fetchall()
            materials.append(
                {
                    "record_id": str(row["id"]),
                    "display_name": str(row["display_name"]),
                    "material_role": str(row["material_role"]),
                    "current_version_id": str(row["current_version_id"]),
                    "record_revision": int(row["revision"]),
                    "unit_count": len(units),
                    "units": [
                        {
                            "unit_index": int(unit["unit_index"]),
                            "title": (
                                str(unit["title"])
                                if unit["title"] is not None
                                else None
                            ),
                            "text_excerpt": str(
                                unit["extracted_text"]
                            )[:4000],
                            "text_status": str(unit["text_status"]),
                            "object_summary": _compact_object_summary(
                                str(
                                    unit["object_summary_json"]
                                    or "{}"
                                )
                            ),
                            "preview_sha256": str(
                                unit["preview_sha256"]
                            ),
                        }
                        for unit in units
                    ],
                }
            )
        lessons = connection.execute(
            """
            SELECT
                id,
                parent_id,
                node_type,
                title,
                sort_order,
                duration_minutes,
                revision
            FROM lesson_nodes
            WHERE curriculum_id = ? AND is_active = 1
            ORDER BY created_at, id
            """,
            (str(semester["curriculum_id"]),),
        ).fetchall()
        # The source-state digest guards "semester lessons or materials
        # changed", so the semester dict only carries identity fields.
        # Semester metadata (revision, planned_new_lesson_count, status)
        # changes on archive/unarchive/rename and must not invalidate an
        # in-flight proposal.  Compatibility boundary: proposals stored
        # before this fix kept revision/planned_new_lesson_count in the
        # digest input, so their stored fingerprint mismatches the new
        # one on first check; those proposals must be regenerated.
        snapshot = {
            "semester": {
                "semester_id": semester_id,
                "school_year": str(semester["school_year"]),
                "term": str(semester["term"]),
                "curriculum_id": str(semester["curriculum_id"]),
                "curriculum_title": str(semester["curriculum_title"]),
            },
            "lessons": [
                {
                    "id": str(row["id"]),
                    "parent_id": (
                        str(row["parent_id"])
                        if row["parent_id"] is not None
                        else None
                    ),
                    "node_type": str(row["node_type"]),
                    "title": str(row["title"]),
                    "sort_order": int(row["sort_order"]),
                    "duration_minutes": (
                        int(row["duration_minutes"])
                        if row["duration_minutes"] is not None
                        else None
                    ),
                    "revision": int(row["revision"]),
                }
                for row in lessons
            ],
            "materials": materials,
        }
        return snapshot, _digest(snapshot)

    @staticmethod
    def _get(
        connection: sqlite3.Connection,
        proposal_id: str,
    ) -> SemesterMappingProposal:
        row = connection.execute(
            """
            SELECT *
            FROM semester_mapping_proposals
            WHERE id = ?
            """,
            (proposal_id,),
        ).fetchone()
        if row is None:
            raise TeachingPrepNotFoundError(
                "semester mapping proposal was not found"
            )
        return _proposal(row)

    @staticmethod
    def _find_active_semantic_generation(
        connection: sqlite3.Connection,
        *,
        request_hash: str,
    ) -> sqlite3.Row | None:
        """Find the durable request identity, independent of browser ID.

        A browser normally retries with its original operation ID.  This
        additional lookup closes the refresh and two-page race: an equivalent
        request that is already running or succeeded must never start another
        physical model call.
        """
        return connection.execute(
            """
            SELECT operation.*
            FROM teaching_prep_operations AS operation
            LEFT JOIN semester_mapping_proposals AS proposal
              ON proposal.operation_id = operation.operation_id
            WHERE operation.operation_type = 'semester_mapping_model'
              AND operation.request_hash = ?
              AND (
                operation.status IN ('running', 'interrupted')
                OR (
                  operation.status = 'succeeded'
                  AND proposal.status != 'rejected'
                )
              )
            ORDER BY
                CASE operation.status
                    WHEN 'succeeded' THEN 0
                    WHEN 'interrupted' THEN 1
                    ELSE 2
                END,
                operation.created_at DESC,
                operation.operation_id DESC
            LIMIT 1
            """,
            (request_hash,),
        ).fetchone()

    @staticmethod
    def _resolve_generation(
        connection: sqlite3.Connection,
        *,
        operation: sqlite3.Row,
        request_hash: str,
    ) -> SemesterMappingProposal | None:
        if str(operation["request_hash"]) != request_hash:
            raise TeachingPrepConflictError(
                "operation ID was reused for different mapping input"
            )
        operation_status = str(operation["status"])
        operation_id = str(operation["operation_id"])
        if operation_status == "succeeded":
            row = connection.execute(
                """
                SELECT *
                FROM semester_mapping_proposals
                WHERE operation_id = ?
                """,
                (operation_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError(
                    "successful mapping operation has no proposal"
                )
            return _proposal(row)
        if operation_status == "failed":
            raise TeachingPrepRetryAvailableError(
                "previous semester mapping did not complete"
            )
        if operation_status == "running":
            raise TeachingPrepStateError(
                "semester mapping is still running"
            )
        if operation_status == "interrupted":
            raise TeachingPrepStateError(
                "semester mapping result is unknown after application restart; "
                "automatic retry is blocked"
            )
        raise TeachingPrepConflictError("mapping operation has invalid state")


def _proposal(row: sqlite3.Row) -> SemesterMappingProposal:
    payload = json.loads(str(row["payload_json"]))
    if not isinstance(payload, dict):
        raise RuntimeError("stored mapping proposal is invalid")
    return SemesterMappingProposal(
        id=str(row["id"]),
        semester_id=str(row["semester_id"]),
        operation_id=str(row["operation_id"]),
        source_state_sha256=str(row["source_state_sha256"]),
        status=str(row["status"]),
        payload=payload,
        revision=int(row["revision"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        applied_at=(
            str(row["applied_at"]) if row["applied_at"] is not None else None
        ),
    )


def _json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _compact_object_summary(value: str) -> dict[str, object]:
    raw = json.loads(value)
    if not isinstance(raw, dict):
        return {}
    allowed = {
        "object_count",
        "object_types",
        "preview_kind",
        "printed_page_number",
        "printed_page_number_source",
        "text_source",
        "ocr_layout",
    }
    return {
        str(key): item
        for key, item in raw.items()
        if str(key) in allowed
    }


def _digest(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _token(prefix: str, *parts: str) -> str:
    suffix = hashlib.sha256(":".join(parts).encode("utf-8")).hexdigest()[:32]
    return f"{prefix}-{suffix}"


def _chapter_lesson_refs(chapter: Mapping[str, object]) -> set[str]:
    """Lesson refs ('proposal:{lesson.key}') owned by one proposal chapter."""
    return {
        f"proposal:{lesson['key']}"
        for section in list(chapter.get("sections") or [])
        for lesson in list(section.get("lessons") or [])
    }


def _mapping_lesson_ref(mapping: Mapping[str, object]) -> str:
    """Effective lesson ref of a mapping after the teacher's revision."""
    teacher_revision = mapping.get("teacher_revision")
    if isinstance(teacher_revision, dict) and teacher_revision.get("lesson_ref"):
        return str(teacher_revision["lesson_ref"])
    return str(mapping.get("lesson_ref") or "")


def _proposal_node_tokens(
    proposal_id: str,
    tree: Sequence[Mapping[str, object]],
) -> set[str]:
    """Request tokens of every node this proposal's tree would insert."""
    tokens: set[str] = set()
    for chapter in tree:
        tokens.add(_token("mapping-node", proposal_id, str(chapter["key"])))
        for section in list(chapter.get("sections") or []):
            tokens.add(_token("mapping-node", proposal_id, str(section["key"])))
            for lesson in list(section.get("lessons") or []):
                tokens.add(
                    _token("mapping-node", proposal_id, str(lesson["key"]))
                )
    return tokens


def _all_chapters_applied(
    connection: sqlite3.Connection,
    proposal_id: str,
    tree: Sequence[Mapping[str, object]],
) -> bool:
    chapter_tokens = [
        _token("mapping-node", proposal_id, str(chapter["key"]))
        for chapter in tree
    ]
    if not chapter_tokens:
        return True
    placeholders = ",".join("?" for _item in chapter_tokens)
    found = connection.execute(
        f"""
        SELECT COUNT(*)
        FROM lesson_nodes
        WHERE request_token IN ({placeholders})
        """,
        tuple(chapter_tokens),
    ).fetchone()[0]
    return int(found) == len(chapter_tokens)


def _source_state_matches(
    connection: sqlite3.Connection,
    proposal: SemesterMappingProposal,
    snapshot: dict[str, object],
    digest: str,
) -> bool:
    """Source-state guard that tolerates this proposal's own chapters.

    Chapter-scoped applies insert lesson nodes while the remaining
    chapters are still being reviewed.  Those proposal-created nodes must
    not read as external tree changes; any other lesson or material
    difference still blocks review and apply.
    """
    if digest == proposal.source_state_sha256:
        return True
    tokens = _proposal_node_tokens(
        proposal.id,
        list(proposal.payload["tree"]),
    )
    if not tokens:
        return False
    placeholders = ",".join("?" for _item in sorted(tokens))
    rows = connection.execute(
        f"""
        SELECT id
        FROM lesson_nodes
        WHERE request_token IN ({placeholders})
        """,
        tuple(sorted(tokens)),
    ).fetchall()
    if not rows:
        return False
    applied_ids = {str(row["id"]) for row in rows}
    filtered = dict(snapshot)
    filtered["lessons"] = [
        item
        for item in list(snapshot.get("lessons") or [])
        if str(item["id"]) not in applied_ids
    ]
    return _digest(filtered) == proposal.source_state_sha256


__all__ = ["SemesterMappingRepository"]
