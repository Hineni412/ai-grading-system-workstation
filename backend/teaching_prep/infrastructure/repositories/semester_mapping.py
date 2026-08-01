from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Sequence
from uuid import uuid4

from backend.teaching_prep.domain.errors import (
    TeachingPrepConflictError,
    TeachingPrepNotFoundError,
    TeachingPrepRetryAvailableError,
    TeachingPrepStateError,
)
from backend.teaching_prep.domain.models import SemesterMappingProposal
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
                    status
                )
                VALUES (?, 'semester_mapping_model', ?, ?, 'semester', ?, 'running')
                """,
                (
                    operation_id,
                    operation_id,
                    request_hash,
                    semester_id,
                ),
            )
        return None

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
            if source_sha != proposal.source_state_sha256:
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

    def apply(
        self,
        proposal_id: str,
        *,
        expected_revision: int,
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
            material_ids = tuple(
                str(item)
                for item in proposal.payload["source_material_record_ids"]
            )
            _current, digest = self._snapshot(
                connection,
                proposal.semester_id,
                material_ids,
            )
            if digest != proposal.source_state_sha256:
                raise TeachingPrepConflictError(
                    "semester lessons or materials changed; generate a new proposal"
                )
            reviewed_mappings = [
                dict(item) for item in proposal.payload["mappings"]
            ]
            if any(
                str(item.get("decision") or "pending") == "pending"
                for item in reviewed_mappings
            ):
                raise TeachingPrepConflictError(
                    "decide every mapping row before applying the proposal"
                )
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
            tree = list(proposal.payload["tree"])
            existing_rows = connection.execute(
                """
                SELECT id, node_type
                FROM lesson_nodes
                WHERE curriculum_id = ? AND is_active = 1
                """,
                (curriculum_id,),
            ).fetchall()
            if tree and existing_rows:
                raise TeachingPrepConflictError(
                    "lesson tree is no longer empty"
                )
            lesson_refs = {
                str(row["id"]): str(row["id"])
                for row in existing_rows
                if str(row["node_type"]) == "lesson"
            }
            for chapter_order, raw_chapter in enumerate(tree, start=1):
                chapter = dict(raw_chapter)
                chapter_id = self._insert_node(
                    connection,
                    proposal_id=proposal.id,
                    key=str(chapter["key"]),
                    curriculum_id=curriculum_id,
                    parent_id=None,
                    node_type="chapter",
                    title=str(chapter["title"]),
                    sort_order=chapter_order,
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

            mapped_records: set[str] = set()
            accepted_mappings = [
                item
                for item in reviewed_mappings
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
                        _token("mapping-link", proposal.id, str(index)),
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
                mapped_records.add(record_id)
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
                            )[:240],
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
        snapshot = {
            "semester": {
                "semester_id": semester_id,
                "school_year": str(semester["school_year"]),
                "term": str(semester["term"]),
                "planned_new_lesson_count": int(
                    semester["planned_new_lesson_count"]
                ),
                "revision": int(semester["revision"]),
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
            SELECT *
            FROM teaching_prep_operations
            WHERE operation_type = 'semester_mapping_model'
              AND request_hash = ?
              AND status IN ('running', 'succeeded')
            ORDER BY
                CASE status WHEN 'succeeded' THEN 0 ELSE 1 END,
                created_at DESC,
                operation_id DESC
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


__all__ = ["SemesterMappingRepository"]
