from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping, Sequence
from pathlib import Path
from uuid import uuid4

from backend.teaching_prep.domain.errors import (
    TeachingPrepConflictError,
    TeachingPrepNotFoundError,
    TeachingPrepValidationError,
)
from backend.teaching_prep.domain.models import (
    CurriculumEdition,
    LessonNode,
    MaterialVersion,
)
from backend.teaching_prep.infrastructure.database import TeachingPrepDatabase


_NODE_PARENT_TYPES = {
    "chapter": None,
    "section": "chapter",
    "lesson": "section",
}
_MATERIAL_DELETION_COUNT_KEYS = (
    "material_sources",
    "material_versions",
    "material_units",
    "lesson_material_links",
    "semester_material_records",
    "semester_mapping_proposals",
    "reference_ppt_collections",
    "exercise_regions",
    "exercise_candidates",
)
_BLOCKING_PPTX_EXECUTION_STATUSES = frozenset(
    {"running", "verifying", "publishing", "interrupted"}
)


class TeachingCatalogRepository:
    def __init__(self, database: TeachingPrepDatabase) -> None:
        self._database = database

    def create_curriculum(
        self,
        *,
        request_token: str,
        title: str,
        grade_level: int,
        volume: str,
        publisher: str | None,
        edition_label: str | None,
    ) -> tuple[CurriculumEdition, bool]:
        values = {
            "title": title,
            "grade_level": grade_level,
            "volume": volume,
            "publisher": publisher,
            "edition_label": edition_label,
        }
        request_hash = _request_hash(values)
        with self._database.connect(immediate=True) as connection:
            existing = connection.execute(
                """
                SELECT *
                FROM curriculum_editions
                WHERE request_token = ?
                """,
                (request_token,),
            ).fetchone()
            if existing is not None:
                _require_same_request(existing, request_hash)
                return _curriculum(existing), False
            curriculum_id = uuid4().hex
            connection.execute(
                """
                INSERT INTO curriculum_editions (
                    id,
                    request_token,
                    request_hash,
                    title,
                    grade_level,
                    volume,
                    publisher,
                    edition_label
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    curriculum_id,
                    request_token,
                    request_hash,
                    title,
                    grade_level,
                    volume,
                    publisher,
                    edition_label,
                ),
            )
            row = connection.execute(
                "SELECT * FROM curriculum_editions WHERE id = ?",
                (curriculum_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("created curriculum could not be loaded")
        return _curriculum(row), True

    def list_curricula(self) -> tuple[CurriculumEdition, ...]:
        with self._database.connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM curriculum_editions
                ORDER BY is_active DESC, updated_at DESC, id DESC
                """
            ).fetchall()
        return tuple(_curriculum(row) for row in rows)

    def create_lesson_node(
        self,
        *,
        request_token: str,
        curriculum_id: str,
        parent_id: str | None,
        node_type: str,
        title: str,
        duration_minutes: int | None,
        source_kind: str,
    ) -> tuple[LessonNode, bool]:
        values = {
            "curriculum_id": curriculum_id,
            "parent_id": parent_id,
            "node_type": node_type,
            "title": title,
            "duration_minutes": duration_minutes,
            "source_kind": source_kind,
        }
        request_hash = _request_hash(values)
        with self._database.connect(immediate=True) as connection:
            existing = connection.execute(
                "SELECT * FROM lesson_nodes WHERE request_token = ?",
                (request_token,),
            ).fetchone()
            if existing is not None:
                _require_same_request(existing, request_hash)
                return _lesson_node(existing), False
            self._require_curriculum(connection, curriculum_id)
            self._require_valid_parent(
                connection,
                curriculum_id=curriculum_id,
                parent_id=parent_id,
                node_type=node_type,
            )
            next_order = int(
                connection.execute(
                    """
                    SELECT COALESCE(MAX(sort_order), 0) + 1
                    FROM lesson_nodes
                    WHERE curriculum_id = ?
                      AND parent_id IS ?
                    """,
                    (curriculum_id, parent_id),
                ).fetchone()[0]
            )
            node_id = uuid4().hex
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
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    node_id,
                    curriculum_id,
                    parent_id,
                    request_token,
                    request_hash,
                    node_type,
                    title,
                    next_order,
                    duration_minutes,
                    source_kind,
                ),
            )
            row = connection.execute(
                "SELECT * FROM lesson_nodes WHERE id = ?",
                (node_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("created lesson node could not be loaded")
        return _lesson_node(row), True

    def list_lesson_nodes(self, curriculum_id: str) -> tuple[LessonNode, ...]:
        with self._database.connect() as connection:
            self._require_curriculum(connection, curriculum_id)
            rows = connection.execute(
                """
                WITH RECURSIVE tree(
                    id,
                    curriculum_id,
                    parent_id,
                    node_type,
                    title,
                    sort_order,
                    duration_minutes,
                    source_kind,
                    is_active,
                    revision,
                    created_at,
                    updated_at,
                    depth,
                    path
                ) AS (
                    SELECT
                        id,
                        curriculum_id,
                        parent_id,
                        node_type,
                        title,
                        sort_order,
                        duration_minutes,
                        source_kind,
                        is_active,
                        revision,
                        created_at,
                        updated_at,
                        0,
                        printf('%08d', sort_order)
                    FROM lesson_nodes
                    WHERE curriculum_id = ? AND parent_id IS NULL
                    UNION ALL
                    SELECT
                        child.id,
                        child.curriculum_id,
                        child.parent_id,
                        child.node_type,
                        child.title,
                        child.sort_order,
                        child.duration_minutes,
                        child.source_kind,
                        child.is_active,
                        child.revision,
                        child.created_at,
                        child.updated_at,
                        tree.depth + 1,
                        tree.path || '.' || printf('%08d', child.sort_order)
                    FROM lesson_nodes AS child
                    JOIN tree ON child.parent_id = tree.id
                )
                SELECT *
                FROM tree
                ORDER BY path
                """,
                (curriculum_id,),
            ).fetchall()
        return tuple(_lesson_node(row) for row in rows)

    def update_lesson_node(
        self,
        node_id: str,
        *,
        expected_revision: int,
        title: str,
        duration_minutes: int | None,
        is_active: bool,
    ) -> LessonNode:
        with self._database.connect(immediate=True) as connection:
            cursor = connection.execute(
                """
                UPDATE lesson_nodes
                SET title = ?,
                    duration_minutes = ?,
                    is_active = ?,
                    revision = revision + 1,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ? AND revision = ?
                """,
                (
                    title,
                    duration_minutes,
                    int(is_active),
                    node_id,
                    expected_revision,
                ),
            )
            if cursor.rowcount != 1:
                self._raise_missing_or_stale(
                    connection,
                    "lesson_nodes",
                    node_id,
                )
            row = connection.execute(
                "SELECT * FROM lesson_nodes WHERE id = ?",
                (node_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("updated lesson node could not be loaded")
        return _lesson_node(row)

    def reorder_lesson_nodes(
        self,
        *,
        curriculum_id: str,
        parent_id: str | None,
        ordered_ids: Sequence[str],
        expected_revisions: Mapping[str, int],
    ) -> tuple[LessonNode, ...]:
        with self._database.connect(immediate=True) as connection:
            self._require_curriculum(connection, curriculum_id)
            rows = connection.execute(
                """
                SELECT *
                FROM lesson_nodes
                WHERE curriculum_id = ? AND parent_id IS ?
                ORDER BY sort_order
                """,
                (curriculum_id, parent_id),
            ).fetchall()
            current_by_id = {str(row["id"]): row for row in rows}
            if set(ordered_ids) != set(current_by_id):
                raise TeachingPrepConflictError(
                    "lesson tree siblings changed; refresh before reordering"
                )
            if len(ordered_ids) != len(set(ordered_ids)):
                raise TeachingPrepValidationError(
                    "ordered lesson node IDs must be unique"
                )
            for node_id, row in current_by_id.items():
                if expected_revisions.get(node_id) != int(row["revision"]):
                    raise TeachingPrepConflictError(
                        "lesson tree revision changed; refresh before reordering"
                    )
            if list(ordered_ids) != [str(row["id"]) for row in rows]:
                offset = max(
                    (int(row["sort_order"]) for row in rows),
                    default=0,
                ) + len(rows) + 100
                for position, node_id in enumerate(ordered_ids, start=1):
                    connection.execute(
                        """
                        UPDATE lesson_nodes
                        SET sort_order = ?
                        WHERE id = ?
                        """,
                        (offset + position, node_id),
                    )
                for position, node_id in enumerate(ordered_ids, start=1):
                    connection.execute(
                        """
                        UPDATE lesson_nodes
                        SET sort_order = ?,
                            revision = revision + 1,
                            updated_at = strftime(
                                '%Y-%m-%dT%H:%M:%fZ',
                                'now'
                            )
                        WHERE id = ?
                        """,
                        (position, node_id),
                    )
            reordered = connection.execute(
                """
                SELECT *
                FROM lesson_nodes
                WHERE curriculum_id = ? AND parent_id IS ?
                ORDER BY sort_order
                """,
                (curriculum_id, parent_id),
            ).fetchall()
        return tuple(_lesson_node(row) for row in reordered)

    def register_material_version(
        self,
        *,
        request_token: str,
        source_id: str | None,
        display_name: str,
        material_type: str,
        content_sha256: str,
        file_name: str,
        local_path: str,
        size_bytes: int,
        modified_ns: int | None,
        unit_count: int | None,
        inspection_status: str,
    ) -> tuple[MaterialVersion, bool]:
        values = {
            "source_id": source_id,
            "display_name": display_name,
            "material_type": material_type,
            "content_sha256": content_sha256,
            "file_name": file_name,
            "size_bytes": size_bytes,
            "modified_ns": modified_ns,
            "unit_count": unit_count,
            "inspection_status": inspection_status,
        }
        request_hash = _request_hash(values)
        with self._database.connect(immediate=True) as connection:
            token_row = self._material_query(
                connection,
                "version.request_token = ?",
                (request_token,),
            )
            if token_row is not None:
                _require_same_request(token_row, request_hash)
                return _material_version(token_row), False
            fingerprint_row = self._material_query(
                connection,
                "version.content_sha256 = ?",
                (content_sha256,),
            )
            if fingerprint_row is not None:
                return _material_version(fingerprint_row), False
            effective_source_id = source_id
            if effective_source_id is None:
                effective_source_id = uuid4().hex
                connection.execute(
                    """
                    INSERT INTO material_sources (
                        id,
                        display_name,
                        material_type
                    )
                    VALUES (?, ?, ?)
                    """,
                    (effective_source_id, display_name, material_type),
                )
            else:
                source = connection.execute(
                    """
                    SELECT material_type
                    FROM material_sources
                    WHERE id = ?
                    """,
                    (effective_source_id,),
                ).fetchone()
                if source is None:
                    raise TeachingPrepNotFoundError(
                        "material source was not found"
                    )
                if str(source["material_type"]) != material_type:
                    raise TeachingPrepConflictError(
                        "material type changed for an existing source"
                    )
            version_id = uuid4().hex
            connection.execute(
                """
                INSERT INTO material_versions (
                    id,
                    source_id,
                    request_token,
                    request_hash,
                    content_sha256,
                    file_name,
                    size_bytes,
                    modified_ns,
                    unit_count,
                    inspection_status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    version_id,
                    effective_source_id,
                    request_token,
                    request_hash,
                    content_sha256,
                    file_name,
                    size_bytes,
                    modified_ns,
                    unit_count,
                    inspection_status,
                ),
            )
            connection.execute(
                """
                INSERT INTO material_locations (
                    version_id,
                    local_path,
                    availability
                )
                VALUES (?, ?, 'available')
                """,
                (version_id, local_path),
            )
            row = self._material_query(
                connection,
                "version.id = ?",
                (version_id,),
            )
        if row is None:
            raise RuntimeError("created material version could not be loaded")
        return _material_version(row), True

    def list_material_versions(
        self,
        *,
        search: str | None = None,
        material_type: str | None = None,
        availability: str | None = None,
        include_archived: bool = False,
    ) -> tuple[MaterialVersion, ...]:
        clauses: list[str] = []
        parameters: list[object] = []
        if search:
            clauses.append(
                "(source.display_name LIKE ? ESCAPE '\\' "
                "OR version.file_name LIKE ? ESCAPE '\\')"
            )
            pattern = f"%{_escape_like(search)}%"
            parameters.extend((pattern, pattern))
        if material_type:
            clauses.append("source.material_type = ?")
            parameters.append(material_type)
        if availability:
            clauses.append("location.availability = ?")
            parameters.append(availability)
        if not include_archived:
            clauses.append("source.archived_at IS NULL")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._database.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT
                    version.*,
                    source.display_name,
                    source.material_type,
                    source.revision AS source_revision,
                    source.archived_at AS source_archived_at,
                    location.availability,
                    (SELECT COUNT(*) FROM material_units AS unit
                     WHERE unit.material_version_id = version.id)
                        AS preview_completed_count,
                    (SELECT COUNT(*) FROM material_units AS unit
                     WHERE unit.material_version_id = version.id
                       AND json_extract(unit.object_summary_json, '$.ocr_status') = 'completed')
                        AS ocr_completed_count,
                    (SELECT COUNT(*) FROM material_units AS unit
                     WHERE unit.material_version_id = version.id
                       AND json_extract(unit.object_summary_json, '$.ocr_required') = 1)
                        AS ocr_total_count
                FROM material_versions AS version
                JOIN material_sources AS source
                  ON source.id = version.source_id
                JOIN material_locations AS location
                  ON location.version_id = version.id
                {where}
                ORDER BY version.created_at DESC, version.id DESC
                """,
                tuple(parameters),
            ).fetchall()
        return tuple(_material_version(row) for row in rows)

    def update_material_source(
        self,
        source_id: str,
        *,
        expected_revision: int,
        display_name: str | None,
        archived: bool | None,
    ) -> MaterialVersion:
        with self._database.connect(immediate=True) as connection:
            source = connection.execute(
                "SELECT * FROM material_sources WHERE id = ?",
                (source_id,),
            ).fetchone()
            if source is None:
                raise TeachingPrepNotFoundError("material source was not found")
            if int(source["revision"]) != expected_revision:
                raise TeachingPrepConflictError("material source revision changed")
            if archived is True:
                active = connection.execute(
                    """
                    SELECT 1 FROM semester_material_records
                    WHERE material_source_id = ? AND is_active = 1
                    """,
                    (source_id,),
                ).fetchone()
                if active is not None:
                    raise TeachingPrepConflictError(
                        "remove the material from its active semester before archiving"
                    )
            new_name = display_name if display_name is not None else str(source["display_name"])
            archived_at = source["archived_at"]
            if archived is True:
                archived_at = connection.execute(
                    "SELECT strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"
                ).fetchone()[0]
            elif archived is False:
                archived_at = None
            connection.execute(
                """
                UPDATE material_sources
                SET display_name = ?, archived_at = ?, revision = revision + 1,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ? AND revision = ?
                """,
                (new_name, archived_at, source_id, expected_revision),
            )
            row = self._material_query(
                connection,
                "version.id = (SELECT id FROM material_versions WHERE source_id = ? ORDER BY created_at DESC, id DESC LIMIT 1)",
                (source_id,),
            )
        if row is None:
            raise TeachingPrepNotFoundError("material version was not found")
        return _material_version(row)

    def material_source_owned_paths(self, source_id: str) -> tuple[Path, ...]:
        with self._database.connect() as connection:
            rows = connection.execute(
                """
                SELECT location.local_path AS path
                FROM material_locations AS location
                JOIN material_versions AS version ON version.id = location.version_id
                WHERE version.source_id = ?
                UNION ALL
                SELECT unit.preview_relpath AS path
                FROM material_units AS unit
                JOIN material_versions AS version ON version.id = unit.material_version_id
                WHERE version.source_id = ?
                """,
                (source_id, source_id),
            ).fetchall()
        return tuple(Path(str(row["path"])) for row in rows if str(row["path"] or "").strip())

    def material_deletion_impact(
        self,
        source_id: str,
        *,
        expected_revision: int,
    ) -> dict[str, object]:
        with self._database.connect() as connection:
            return self._material_deletion_impact(
                connection,
                source_id,
                expected_revision=expected_revision,
            )

    def begin_material_deletion(
        self,
        *,
        operation_id: str,
        source_id: str,
        source_display_name: str,
        expected_revision: int,
        preview_version: str,
        request_hash: str,
        impact: dict[str, object],
    ) -> dict[str, object] | None:
        with self._database.connect(immediate=True) as connection:
            existing = connection.execute(
                """
                SELECT operation.operation_type, operation.request_hash,
                       operation.status, operation.error_code,
                       receipt.preview_version, receipt.impact_json,
                       receipt.result_json
                FROM teaching_prep_operations AS operation
                LEFT JOIN material_delete_receipts AS receipt
                  ON receipt.operation_id = operation.operation_id
                WHERE operation.operation_id = ?
                """,
                (operation_id,),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["operation_type"]) != "material_delete"
                    or str(existing["request_hash"]) != request_hash
                    or existing["impact_json"] is None
                ):
                    raise TeachingPrepConflictError(
                        "deletion operation ID was reused for different input"
                    )
                return _material_delete_receipt(existing, operation_id)
            connection.execute(
                """
                INSERT INTO teaching_prep_operations (
                    operation_id, operation_type, idempotency_key,
                    request_hash, target_kind, target_id, status
                ) VALUES (?, 'material_delete', ?, ?, 'material_source', ?, 'running')
                """,
                (operation_id, operation_id, request_hash, source_id),
            )
            connection.execute(
                """
                INSERT INTO material_delete_receipts (
                    operation_id, source_id, source_display_name,
                    expected_revision, preview_version, request_hash,
                    impact_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    operation_id,
                    source_id,
                    source_display_name,
                    int(expected_revision),
                    preview_version,
                    request_hash,
                    _json(impact),
                ),
            )
        return None

    def find_material_deletion(
        self,
        operation_id: str,
        *,
        request_hash: str,
    ) -> dict[str, object] | None:
        with self._database.connect() as connection:
            row = connection.execute(
                """
                SELECT operation.operation_type, operation.request_hash,
                       operation.status, operation.error_code,
                       receipt.preview_version, receipt.impact_json,
                       receipt.result_json
                FROM teaching_prep_operations AS operation
                LEFT JOIN material_delete_receipts AS receipt
                  ON receipt.operation_id = operation.operation_id
                WHERE operation.operation_id = ?
                """,
                (operation_id,),
            ).fetchone()
        if row is None:
            return None
        if (
            str(row["operation_type"]) != "material_delete"
            or str(row["request_hash"]) != request_hash
            or row["impact_json"] is None
        ):
            raise TeachingPrepConflictError(
                "deletion operation ID was reused for different input"
            )
        return _material_delete_receipt(row, operation_id)

    def claim_recovered_material_deletion(
        self,
        operation_id: str,
        *,
        request_hash: str,
        source_id: str,
        expected_revision: int,
        preview_version: str,
    ) -> bool:
        """Resume only a fully restored interrupted delete with unchanged input."""

        with self._database.connect(immediate=True) as connection:
            row = connection.execute(
                """
                SELECT operation.operation_type, operation.request_hash,
                       operation.status, operation.error_code,
                       receipt.source_id, receipt.expected_revision,
                       receipt.preview_version, receipt.result_json,
                       receipt.staging_manifest_json
                FROM teaching_prep_operations AS operation
                JOIN material_delete_receipts AS receipt
                  ON receipt.operation_id = operation.operation_id
                WHERE operation.operation_id = ?
                """,
                (operation_id,),
            ).fetchone()
            if row is None:
                raise TeachingPrepNotFoundError(
                    "material deletion operation was not found"
                )
            if (
                str(row["operation_type"]) != "material_delete"
                or str(row["request_hash"]) != request_hash
                or str(row["source_id"]) != source_id
                or int(row["expected_revision"]) != int(expected_revision)
                or str(row["preview_version"]) != preview_version
            ):
                raise TeachingPrepConflictError(
                    "deletion operation ID was reused for different input"
                )
            if (
                str(row["status"]) != "interrupted"
                or str(row["error_code"] or "")
                != "application_restarted_during_material_delete"
                or row["result_json"] is not None
                or str(row["staging_manifest_json"] or "[]") != "[]"
            ):
                return False
            source = connection.execute(
                "SELECT revision FROM material_sources WHERE id = ?",
                (source_id,),
            ).fetchone()
            if (
                source is None
                or int(source["revision"]) != int(expected_revision)
            ):
                return False
            try:
                current_impact = self._material_deletion_impact(
                    connection,
                    source_id,
                    expected_revision=expected_revision,
                )
            except (TeachingPrepConflictError, TeachingPrepNotFoundError):
                return False
            if (
                current_impact["preview_version"] != preview_version
                or current_impact["can_delete"] is not True
            ):
                return False
            updated = connection.execute(
                """
                UPDATE teaching_prep_operations
                SET status = 'running', error_code = NULL,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    finished_at = NULL
                WHERE operation_id = ?
                  AND operation_type = 'material_delete'
                  AND status = 'interrupted'
                  AND error_code = (
                    'application_restarted_during_material_delete'
                  )
                """,
                (operation_id,),
            ).rowcount
        return updated == 1

    def save_material_deletion_staging_manifest(
        self,
        operation_id: str,
        *,
        manifest: Sequence[Mapping[str, str]],
    ) -> None:
        payload = [dict(item) for item in manifest]
        encoded = _json(payload)
        with self._database.connect(immediate=True) as connection:
            row = connection.execute(
                """
                SELECT operation.status, receipt.staging_manifest_json
                FROM teaching_prep_operations AS operation
                JOIN material_delete_receipts AS receipt
                  ON receipt.operation_id = operation.operation_id
                WHERE operation.operation_id = ?
                  AND operation.operation_type = 'material_delete'
                """,
                (operation_id,),
            ).fetchone()
            if row is None:
                raise TeachingPrepNotFoundError(
                    "material deletion operation was not found"
                )
            if str(row["status"]) != "running":
                raise TeachingPrepConflictError(
                    "material deletion operation is no longer running"
                )
            existing = str(row["staging_manifest_json"] or "[]")
            if existing not in {"[]", encoded}:
                raise TeachingPrepConflictError(
                    "material deletion staging manifest changed"
                )
            connection.execute(
                """
                UPDATE material_delete_receipts
                SET staging_manifest_json = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE operation_id = ?
                """,
                (encoded, operation_id),
            )

    def material_deletions_needing_file_recovery(
        self,
    ) -> tuple[dict[str, object], ...]:
        with self._database.connect() as connection:
            rows = connection.execute(
                """
                SELECT receipt.operation_id, operation.status,
                       receipt.staging_manifest_json
                FROM material_delete_receipts AS receipt
                JOIN teaching_prep_operations AS operation
                  ON operation.operation_id = receipt.operation_id
                WHERE operation.operation_type = 'material_delete'
                  AND (
                    operation.status = 'running'
                    OR receipt.staging_manifest_json <> '[]'
                  )
                ORDER BY receipt.created_at, receipt.operation_id
                """
            ).fetchall()
        recoveries: list[dict[str, object]] = []
        for row in rows:
            try:
                manifest = json.loads(
                    str(row["staging_manifest_json"] or "[]")
                )
            except (TypeError, ValueError):
                manifest = None
            recoveries.append(
                {
                    "operation_id": str(row["operation_id"]),
                    "status": str(row["status"]),
                    "manifest": manifest,
                }
            )
        return tuple(recoveries)

    def clear_material_deletion_staging_manifest(
        self,
        operation_id: str,
    ) -> None:
        with self._database.connect(immediate=True) as connection:
            connection.execute(
                """
                UPDATE material_delete_receipts
                SET staging_manifest_json = '[]',
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE operation_id = ?
                """,
                (operation_id,),
            )

    def interrupt_material_deletion(
        self,
        operation_id: str,
        *,
        error_code: str,
    ) -> None:
        with self._database.connect(immediate=True) as connection:
            connection.execute(
                """
                UPDATE teaching_prep_operations
                SET status = 'interrupted', error_code = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    finished_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE operation_id = ?
                  AND operation_type = 'material_delete'
                  AND status IN ('running', 'interrupted')
                """,
                (error_code, operation_id),
            )

    def get_material_deletion(self, operation_id: str) -> dict[str, object]:
        with self._database.connect() as connection:
            row = connection.execute(
                """
                SELECT operation.status, operation.error_code,
                       receipt.preview_version, receipt.impact_json,
                       receipt.result_json
                FROM material_delete_receipts AS receipt
                JOIN teaching_prep_operations AS operation
                  ON operation.operation_id = receipt.operation_id
                WHERE receipt.operation_id = ?
                """,
                (operation_id,),
            ).fetchone()
        if row is None:
            raise TeachingPrepNotFoundError(
                "material deletion operation was not found"
            )
        return _material_delete_receipt(row, operation_id)

    def fail_material_deletion(
        self,
        operation_id: str,
        *,
        error_code: str,
    ) -> None:
        with self._database.connect(immediate=True) as connection:
            connection.execute(
                """
                UPDATE teaching_prep_operations
                SET status = 'failed', error_code = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    finished_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE operation_id = ? AND status = 'running'
                """,
                (error_code, operation_id),
            )

    def delete_material_source(
        self,
        source_id: str,
        *,
        expected_revision: int,
        expected_preview_version: str,
        operation_id: str,
        deleted_file_count: int,
    ) -> dict[str, object]:
        """Permanently delete one source and its directly derived catalog data."""

        counts: dict[str, int] = {
            key: 0 for key in _MATERIAL_DELETION_COUNT_KEYS
        }
        with self._database.connect(immediate=True) as connection:
            current_impact = self._material_deletion_impact(
                connection,
                source_id,
                expected_revision=expected_revision,
            )
            if current_impact["preview_version"] != expected_preview_version:
                raise TeachingPrepConflictError(
                    "material deletion impact changed; preview it again"
                )
            if current_impact["can_delete"] is not True:
                raise TeachingPrepConflictError(
                    "material has active courseware generation and cannot be deleted"
                )
            source = connection.execute(
                "SELECT revision FROM material_sources WHERE id = ?",
                (source_id,),
            ).fetchone()
            if source is None:
                raise TeachingPrepNotFoundError("material source was not found")
            if int(source["revision"]) != int(expected_revision):
                raise TeachingPrepConflictError("material source revision changed")
            version_ids = [
                str(row["id"])
                for row in connection.execute(
                    "SELECT id FROM material_versions WHERE source_id = ?",
                    (source_id,),
                ).fetchall()
            ]
            placeholders = ",".join("?" for _ in version_ids)
            if version_ids:
                active_generation = connection.execute(
                    f"""
                    SELECT 1
                    FROM pptx_execution_runs
                    WHERE source_material_version_id IN ({placeholders})
                      AND status IN (
                          'running', 'verifying', 'publishing', 'interrupted'
                      )
                    LIMIT 1
                    """,
                    tuple(version_ids),
                ).fetchone()
                if active_generation is not None:
                    raise TeachingPrepConflictError(
                        "material has active courseware generation and cannot be deleted"
                    )

            record_ids = [
                str(row["id"])
                for row in connection.execute(
                    "SELECT id FROM semester_material_records WHERE material_source_id = ?",
                    (source_id,),
                ).fetchall()
            ]
            if record_ids:
                record_placeholders = ",".join("?" for _ in record_ids)
                proposal_ids = [
                    str(row["id"])
                    for row in connection.execute(
                        f"""
                        SELECT DISTINCT proposal.id
                        FROM semester_mapping_proposals AS proposal,
                             json_each(
                                 proposal.payload_json,
                                 '$.source_material_record_ids'
                             ) AS source_record
                        WHERE CAST(source_record.value AS TEXT)
                              IN ({record_placeholders})
                        """,
                        tuple(record_ids),
                    ).fetchall()
                ]
                collection_rows = connection.execute(
                    f"""
                    SELECT DISTINCT collection.id,
                                    collection.mapping_proposal_id
                    FROM reference_ppt_collections AS collection
                    JOIN reference_ppt_collection_members AS member
                      ON member.collection_id = collection.id
                    WHERE member.material_record_id IN ({record_placeholders})
                    """,
                    tuple(record_ids),
                ).fetchall()
                collection_ids = [str(row["id"]) for row in collection_rows]
                proposal_ids = list(dict.fromkeys([
                    *proposal_ids,
                    *(str(row["mapping_proposal_id"]) for row in collection_rows),
                ]))
                if collection_ids:
                    collection_placeholders = ",".join("?" for _ in collection_ids)
                    counts["reference_ppt_collections"] = max(0, connection.execute(
                        f"DELETE FROM reference_ppt_collections WHERE id IN ({collection_placeholders})",
                        tuple(collection_ids),
                    ).rowcount)
                if proposal_ids:
                    proposal_placeholders = ",".join("?" for _ in proposal_ids)
                    counts["semester_mapping_proposals"] = max(0, connection.execute(
                        f"DELETE FROM semester_mapping_proposals WHERE id IN ({proposal_placeholders})",
                        tuple(proposal_ids),
                    ).rowcount)
                counts["semester_material_records"] = max(0, connection.execute(
                    f"DELETE FROM semester_material_records WHERE id IN ({record_placeholders})",
                    tuple(record_ids),
                ).rowcount)

            if version_ids:
                unit_rows = connection.execute(
                    f"SELECT id FROM material_units WHERE material_version_id IN ({placeholders})",
                    tuple(version_ids),
                ).fetchall()
                unit_ids = [str(row["id"]) for row in unit_rows]
                _historical_payload_redactions(
                    connection,
                    material_version_ids=version_ids,
                    material_unit_ids=unit_ids,
                    apply=True,
                )
                candidate_ids: list[str] = []
                if unit_ids:
                    unit_placeholders = ",".join("?" for _ in unit_ids)
                    candidate_ids = [
                        str(row["exercise_candidate_id"])
                        for row in connection.execute(
                            f"SELECT DISTINCT exercise_candidate_id FROM exercise_regions WHERE material_unit_id IN ({unit_placeholders})",
                            tuple(unit_ids),
                        ).fetchall()
                    ]
                    counts["exercise_regions"] = max(0, connection.execute(
                        f"DELETE FROM exercise_regions WHERE material_unit_id IN ({unit_placeholders})",
                        tuple(unit_ids),
                    ).rowcount)
                counts["lesson_material_links"] = max(0, connection.execute(
                    f"DELETE FROM lesson_material_links WHERE material_version_id IN ({placeholders})",
                    tuple(version_ids),
                ).rowcount)
                if candidate_ids:
                    candidate_placeholders = ",".join("?" for _ in candidate_ids)
                    counts["exercise_candidates"] = max(0, connection.execute(
                        f"""
                        DELETE FROM exercise_candidates
                        WHERE id IN ({candidate_placeholders})
                          AND NOT EXISTS (
                              SELECT 1 FROM exercise_regions
                              WHERE exercise_candidate_id = exercise_candidates.id
                          )
                        """,
                        tuple(candidate_ids),
                    ).rowcount)
                counts["material_units"] = max(0, connection.execute(
                    f"DELETE FROM material_units WHERE material_version_id IN ({placeholders})",
                    tuple(version_ids),
                ).rowcount)
                counts["material_versions"] = max(0, connection.execute(
                    f"DELETE FROM material_versions WHERE id IN ({placeholders})",
                    tuple(version_ids),
                ).rowcount)
            counts["material_sources"] = max(0, connection.execute(
                "DELETE FROM material_sources WHERE id = ?",
                (source_id,),
            ).rowcount)
            result: dict[str, object] = {
                "operation_id": operation_id,
                "status": "succeeded",
                "preview_version": expected_preview_version,
                "deleted_source_id": source_id,
                "deleted_file_count": int(deleted_file_count),
                "counts": counts,
                "error_code": None,
            }
            updated = connection.execute(
                """
                UPDATE teaching_prep_operations
                SET status = 'succeeded', error_code = NULL,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    finished_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE operation_id = ? AND status = 'running'
                """,
                (operation_id,),
            ).rowcount
            if updated != 1:
                raise TeachingPrepConflictError(
                    "material deletion operation is no longer running"
                )
            connection.execute(
                """
                UPDATE material_delete_receipts
                SET result_json = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE operation_id = ?
                """,
                (_json(result), operation_id),
            )
        return result

    def _material_deletion_impact(
        self,
        connection: sqlite3.Connection,
        source_id: str,
        *,
        expected_revision: int,
    ) -> dict[str, object]:
        source = connection.execute(
            "SELECT id, display_name, revision FROM material_sources WHERE id = ?",
            (source_id,),
        ).fetchone()
        if source is None:
            raise TeachingPrepNotFoundError("material source was not found")
        if int(source["revision"]) != int(expected_revision):
            raise TeachingPrepConflictError("material source revision changed")
        version_ids = [
            str(row["id"])
            for row in connection.execute(
                "SELECT id FROM material_versions WHERE source_id = ? ORDER BY id",
                (source_id,),
            ).fetchall()
        ]
        unit_ids: list[str] = []
        link_ids: list[str] = []
        region_ids: list[str] = []
        candidate_ids: list[str] = []
        generation_runs: list[dict[str, str]] = []
        if version_ids:
            placeholders = ",".join("?" for _ in version_ids)
            unit_ids = [
                str(row["id"])
                for row in connection.execute(
                    f"SELECT id FROM material_units WHERE material_version_id IN ({placeholders}) ORDER BY id",
                    tuple(version_ids),
                ).fetchall()
            ]
            link_ids = [
                str(row["id"])
                for row in connection.execute(
                    f"SELECT id FROM lesson_material_links WHERE material_version_id IN ({placeholders}) ORDER BY id",
                    tuple(version_ids),
                ).fetchall()
            ]
            generation_runs = [
                {
                    "id": str(row["id"]),
                    "status": str(row["status"]),
                    "source_snapshot_id": str(
                        row["source_material_version_id"]
                    ),
                    "staging_name": str(row["staging_name"]),
                }
                for row in connection.execute(
                    f"""
                    SELECT id, status, source_material_version_id, staging_name
                    FROM pptx_execution_runs
                    WHERE source_material_version_id IN ({placeholders})
                    ORDER BY id
                    """,
                    tuple(version_ids),
                ).fetchall()
            ]
        if unit_ids:
            unit_placeholders = ",".join("?" for _ in unit_ids)
            region_rows = connection.execute(
                f"SELECT id, exercise_candidate_id FROM exercise_regions WHERE material_unit_id IN ({unit_placeholders}) ORDER BY id",
                tuple(unit_ids),
            ).fetchall()
            region_ids = [str(row["id"]) for row in region_rows]
            referenced_candidates = sorted(
                {str(row["exercise_candidate_id"]) for row in region_rows}
            )
            for candidate_id in referenced_candidates:
                outside = connection.execute(
                    f"""
                    SELECT 1 FROM exercise_regions
                    WHERE exercise_candidate_id = ?
                      AND material_unit_id NOT IN ({unit_placeholders})
                    LIMIT 1
                    """,
                    (candidate_id, *unit_ids),
                ).fetchone()
                if outside is None:
                    candidate_ids.append(candidate_id)
        record_rows = connection.execute(
            """
            SELECT record.id, semester.id AS semester_id,
                   curriculum.title, semester.school_year, semester.term
            FROM semester_material_records AS record
            JOIN teaching_semesters AS semester ON semester.id = record.semester_id
            JOIN curriculum_editions AS curriculum
              ON curriculum.id = semester.curriculum_id
            WHERE record.material_source_id = ?
            ORDER BY record.id
            """,
            (source_id,),
        ).fetchall()
        record_ids = [str(row["id"]) for row in record_rows]
        affected_semesters = [
            {
                "semester_id": str(row["semester_id"]),
                "title": str(row["title"]),
                "school_year": str(row["school_year"]),
                "term": str(row["term"]),
            }
            for row in record_rows
        ]
        proposal_ids: list[str] = []
        collection_ids: list[str] = []
        if record_ids:
            record_placeholders = ",".join("?" for _ in record_ids)
            proposal_ids = [
                str(row["id"])
                for row in connection.execute(
                    f"""
                    SELECT DISTINCT proposal.id
                    FROM semester_mapping_proposals AS proposal,
                         json_each(proposal.payload_json, '$.source_material_record_ids') AS source_record
                    WHERE CAST(source_record.value AS TEXT) IN ({record_placeholders})
                    ORDER BY proposal.id
                    """,
                    tuple(record_ids),
                ).fetchall()
            ]
            collection_rows = connection.execute(
                f"""
                SELECT DISTINCT collection.id, collection.mapping_proposal_id
                FROM reference_ppt_collections AS collection
                JOIN reference_ppt_collection_members AS member
                  ON member.collection_id = collection.id
                WHERE member.material_record_id IN ({record_placeholders})
                ORDER BY collection.id
                """,
                tuple(record_ids),
            ).fetchall()
            collection_ids = [str(row["id"]) for row in collection_rows]
            proposal_ids = sorted(
                set(proposal_ids)
                | {str(row["mapping_proposal_id"]) for row in collection_rows}
            )
        owned_paths = [
            str(row["path"])
            for row in connection.execute(
                """
                SELECT location.local_path AS path
                FROM material_locations AS location
                JOIN material_versions AS version ON version.id = location.version_id
                WHERE version.source_id = ?
                UNION ALL
                SELECT unit.preview_relpath AS path
                FROM material_units AS unit
                JOIN material_versions AS version ON version.id = unit.material_version_id
                WHERE version.source_id = ?
                ORDER BY path
                """,
                (source_id, source_id),
            ).fetchall()
            if str(row["path"] or "").strip()
        ]
        counts = {
            "material_sources": 1,
            "material_versions": len(version_ids),
            "material_units": len(unit_ids),
            "lesson_material_links": len(link_ids),
            "semester_material_records": len(record_ids),
            "semester_mapping_proposals": len(proposal_ids),
            "reference_ppt_collections": len(collection_ids),
            "exercise_regions": len(region_ids),
            "exercise_candidates": len(candidate_ids),
        }
        historical_payload_redactions = _historical_payload_redactions(
            connection,
            material_version_ids=version_ids,
            material_unit_ids=unit_ids,
            apply=False,
        )
        fingerprint_payload = {
            "source_id": source_id,
            "source_revision": int(expected_revision),
            "version_ids": version_ids,
            "unit_ids": unit_ids,
            "link_ids": link_ids,
            "record_ids": record_ids,
            "proposal_ids": proposal_ids,
            "collection_ids": collection_ids,
            "region_ids": region_ids,
            "candidate_ids": candidate_ids,
            "generation_runs": generation_runs,
            "historical_payload_redactions": historical_payload_redactions,
            "owned_paths": owned_paths,
        }
        blocking_generation_count = sum(
            1
            for run in generation_runs
            if run["status"] in _BLOCKING_PPTX_EXECUTION_STATUSES
        )
        preserved_snapshot_count = len(
            {run["source_snapshot_id"] for run in generation_runs}
        )
        return {
            "source_id": source_id,
            "display_name": str(source["display_name"]),
            "source_revision": int(expected_revision),
            "impact_counts": counts,
            "affected_semesters": affected_semesters,
            "generation_history_count": len(generation_runs),
            "preserved_snapshot_count": preserved_snapshot_count,
            "blocking_generation_count": blocking_generation_count,
            "can_delete": blocking_generation_count == 0,
            "blocker_code": (
                None
                if blocking_generation_count == 0
                else "active_courseware_generation"
            ),
            "preserved_history_note": (
                None
                if not generation_runs
                else (
                    "删除后仅保留生成历史所需的资料名、安全文件名、资料类型、"
                    "版本标识和内容指纹；不保留原文件、解析正文或绝对路径，"
                    "原资料及其课时、学期资料关联无法恢复。"
                )
            ),
            "confirmation_phrase": "确认彻底删除资料",
            "preview_version": _request_hash(fingerprint_payload),
            "_owned_paths": owned_paths,
            "_terminal_generation_staging_names": [
                run["staging_name"]
                for run in generation_runs
                if run["status"] not in _BLOCKING_PPTX_EXECUTION_STATUSES
            ],
        }

    def get_material_version(self, version_id: str) -> MaterialVersion:
        with self._database.connect() as connection:
            row = self._material_query(
                connection,
                "version.id = ?",
                (version_id,),
            )
        if row is None:
            raise TeachingPrepNotFoundError(
                "material version was not found"
            )
        return _material_version(row)

    def get_material_location(self, version_id: str) -> Path:
        with self._database.connect() as connection:
            row = connection.execute(
                """
                SELECT local_path
                FROM material_locations
                WHERE version_id = ?
                """,
                (version_id,),
            ).fetchone()
        if row is None:
            raise TeachingPrepNotFoundError(
                "material version was not found"
            )
        return Path(str(row["local_path"]))

    def update_material_location(
        self,
        version_id: str,
        *,
        local_path: str,
    ) -> MaterialVersion:
        with self._database.connect(immediate=True) as connection:
            cursor = connection.execute(
                """
                UPDATE material_locations
                SET local_path = ?,
                    availability = 'available',
                    last_checked_at = strftime(
                        '%Y-%m-%dT%H:%M:%fZ',
                        'now'
                    )
                WHERE version_id = ?
                """,
                (local_path, version_id),
            )
            if cursor.rowcount != 1:
                raise TeachingPrepNotFoundError(
                    "material version was not found"
                )
            row = self._material_query(
                connection,
                "version.id = ?",
                (version_id,),
            )
        if row is None:
            raise RuntimeError("material version could not be loaded")
        return _material_version(row)

    def mark_material_missing(self, version_id: str) -> MaterialVersion:
        with self._database.connect(immediate=True) as connection:
            cursor = connection.execute(
                """
                UPDATE material_locations
                SET availability = 'needs_relocation',
                    last_checked_at = strftime(
                        '%Y-%m-%dT%H:%M:%fZ',
                        'now'
                    )
                WHERE version_id = ?
                """,
                (version_id,),
            )
            if cursor.rowcount != 1:
                raise TeachingPrepNotFoundError(
                    "material version was not found"
                )
            row = self._material_query(
                connection,
                "version.id = ?",
                (version_id,),
            )
        if row is None:
            raise RuntimeError("material version could not be loaded")
        return _material_version(row)

    @staticmethod
    def _require_curriculum(
        connection: sqlite3.Connection,
        curriculum_id: str,
    ) -> None:
        if connection.execute(
            "SELECT 1 FROM curriculum_editions WHERE id = ?",
            (curriculum_id,),
        ).fetchone() is None:
            raise TeachingPrepNotFoundError("curriculum was not found")

    @staticmethod
    def _require_valid_parent(
        connection: sqlite3.Connection,
        *,
        curriculum_id: str,
        parent_id: str | None,
        node_type: str,
    ) -> None:
        required_parent_type = _NODE_PARENT_TYPES[node_type]
        if required_parent_type is None:
            if parent_id is not None:
                raise TeachingPrepValidationError(
                    "chapter nodes cannot have a parent"
                )
            return
        if parent_id is None:
            raise TeachingPrepValidationError(
                f"{node_type} nodes require a parent"
            )
        parent = connection.execute(
            """
            SELECT node_type
            FROM lesson_nodes
            WHERE id = ? AND curriculum_id = ?
            """,
            (parent_id, curriculum_id),
        ).fetchone()
        if parent is None:
            raise TeachingPrepNotFoundError(
                "lesson node parent was not found"
            )
        if str(parent["node_type"]) != required_parent_type:
            raise TeachingPrepValidationError(
                f"{node_type} nodes require a {required_parent_type} parent"
            )

    @staticmethod
    def _raise_missing_or_stale(
        connection: sqlite3.Connection,
        table: str,
        entity_id: str,
    ) -> None:
        row = connection.execute(
            f"SELECT revision FROM {table} WHERE id = ?",
            (entity_id,),
        ).fetchone()
        if row is None:
            raise TeachingPrepNotFoundError("record was not found")
        raise TeachingPrepConflictError(
            f"record revision changed to {int(row['revision'])}"
        )

    @staticmethod
    def _material_query(
        connection: sqlite3.Connection,
        condition: str,
        parameters: tuple[object, ...],
    ) -> sqlite3.Row | None:
        return connection.execute(
            f"""
            SELECT
                version.*,
                source.display_name,
                source.material_type,
                source.revision AS source_revision,
                source.archived_at AS source_archived_at,
                location.availability,
                (SELECT COUNT(*) FROM material_units AS unit
                 WHERE unit.material_version_id = version.id)
                    AS preview_completed_count,
                (SELECT COUNT(*) FROM material_units AS unit
                 WHERE unit.material_version_id = version.id
                   AND json_extract(unit.object_summary_json, '$.ocr_status') = 'completed')
                    AS ocr_completed_count,
                (SELECT COUNT(*) FROM material_units AS unit
                 WHERE unit.material_version_id = version.id
                   AND json_extract(unit.object_summary_json, '$.ocr_required') = 1)
                    AS ocr_total_count
            FROM material_versions AS version
            JOIN material_sources AS source
              ON source.id = version.source_id
            JOIN material_locations AS location
              ON location.version_id = version.id
            WHERE {condition}
            """,
            parameters,
        ).fetchone()


def _json(payload: object) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


_HISTORICAL_SOURCE_REPLACEMENTS: dict[str, object] = {
    "text": "",
    "text_summary": "",
    "extracted_text": "",
    "object_summary": {},
    "preview_url": None,
    "asset_ref": None,
    "material_unit_id": None,
    "unit_id": None,
}


def _historical_payload_redactions(
    connection: sqlite3.Connection,
    *,
    material_version_ids: Sequence[str],
    material_unit_ids: Sequence[str],
    apply: bool,
) -> list[dict[str, object]]:
    version_ids = frozenset(str(item) for item in material_version_ids)
    unit_ids = frozenset(str(item) for item in material_unit_ids)
    if not version_ids:
        return []
    result: list[dict[str, object]] = []
    for table in ("resource_pack_versions", "slide_plan_versions"):
        rows = connection.execute(
            f"SELECT id, payload_json FROM {table} ORDER BY id"
        ).fetchall()
        for row in rows:
            original_json = str(row["payload_json"])
            try:
                payload = json.loads(original_json)
            except json.JSONDecodeError as exc:
                raise TeachingPrepConflictError(
                    "stored teaching history payload is invalid"
                ) from exc
            scrubbed, field_count = _scrub_historical_source_payload(
                payload,
                material_version_ids=version_ids,
                material_unit_ids=unit_ids,
                inherited_target=False,
            )
            if field_count == 0:
                continue
            scrubbed_json = _json(scrubbed)
            result.append(
                {
                    "table": table,
                    "id": str(row["id"]),
                    "before_sha256": hashlib.sha256(
                        original_json.encode("utf-8")
                    ).hexdigest(),
                    "after_sha256": hashlib.sha256(
                        scrubbed_json.encode("utf-8")
                    ).hexdigest(),
                    "field_count": field_count,
                }
            )
            if apply:
                connection.execute(
                    f"UPDATE {table} SET payload_json = ? WHERE id = ?",
                    (scrubbed_json, str(row["id"])),
                )
    return result


def _scrub_historical_source_payload(
    value: object,
    *,
    material_version_ids: frozenset[str],
    material_unit_ids: frozenset[str],
    inherited_target: bool,
) -> tuple[object, int]:
    if isinstance(value, list):
        scrubbed_items: list[object] = []
        field_count = 0
        for item in value:
            scrubbed, changed = _scrub_historical_source_payload(
                item,
                material_version_ids=material_version_ids,
                material_unit_ids=material_unit_ids,
                inherited_target=inherited_target,
            )
            scrubbed_items.append(scrubbed)
            field_count += changed
        return scrubbed_items, field_count
    if not isinstance(value, dict):
        return value, 0
    target_mapping = value.get("target")
    operation_targets_deleted_unit = (
        isinstance(target_mapping, dict)
        and str(target_mapping.get("material_unit_id") or "")
        in material_unit_ids
    )
    targets_deleted_source = inherited_target or (
        str(value.get("material_version_id") or "")
        in material_version_ids
        or str(value.get("material_unit_id") or "") in material_unit_ids
        or str(value.get("unit_id") or "") in material_unit_ids
        or operation_targets_deleted_unit
    )
    scrubbed_mapping: dict[str, object] = {}
    field_count = 0
    for key, item in value.items():
        if targets_deleted_source and key in _HISTORICAL_SOURCE_REPLACEMENTS:
            replacement = _HISTORICAL_SOURCE_REPLACEMENTS[key]
            replacement = (
                dict(replacement)
                if isinstance(replacement, dict)
                else replacement
            )
            scrubbed_mapping[key] = replacement
            if item != replacement:
                field_count += 1
            continue
        scrubbed, changed = _scrub_historical_source_payload(
            item,
            material_version_ids=material_version_ids,
            material_unit_ids=material_unit_ids,
            inherited_target=targets_deleted_source,
        )
        scrubbed_mapping[key] = scrubbed
        field_count += changed
    return scrubbed_mapping, field_count


def _material_delete_receipt(
    row: sqlite3.Row,
    operation_id: str,
) -> dict[str, object]:
    if row["result_json"] is not None:
        result = json.loads(str(row["result_json"]))
        if isinstance(result, dict):
            return result
    impact = json.loads(str(row["impact_json"] or "{}"))
    return {
        "operation_id": operation_id,
        "status": str(row["status"]),
        "preview_version": str(row["preview_version"]),
        "deleted_source_id": None,
        "deleted_file_count": 0,
        "counts": {key: 0 for key in _MATERIAL_DELETION_COUNT_KEYS},
        "error_code": (
            str(row["error_code"])
            if row["error_code"] is not None
            else None
        ),
        "impact": impact if isinstance(impact, dict) else {},
    }


def _request_hash(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _require_same_request(row: sqlite3.Row, request_hash: str) -> None:
    if str(row["request_hash"]) != request_hash:
        raise TeachingPrepConflictError(
            "request token was reused for different data"
        )


def _curriculum(row: sqlite3.Row) -> CurriculumEdition:
    return CurriculumEdition(
        id=str(row["id"]),
        title=str(row["title"]),
        grade_level=int(row["grade_level"]),
        volume=str(row["volume"]),
        publisher=(
            str(row["publisher"]) if row["publisher"] is not None else None
        ),
        edition_label=(
            str(row["edition_label"])
            if row["edition_label"] is not None
            else None
        ),
        revision=int(row["revision"]),
        is_active=bool(row["is_active"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _lesson_node(row: sqlite3.Row) -> LessonNode:
    return LessonNode(
        id=str(row["id"]),
        curriculum_id=str(row["curriculum_id"]),
        parent_id=(
            str(row["parent_id"]) if row["parent_id"] is not None else None
        ),
        node_type=str(row["node_type"]),
        title=str(row["title"]),
        sort_order=int(row["sort_order"]),
        duration_minutes=(
            int(row["duration_minutes"])
            if row["duration_minutes"] is not None
            else None
        ),
        source_kind=str(row["source_kind"]),
        is_active=bool(row["is_active"]),
        revision=int(row["revision"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _material_version(row: sqlite3.Row) -> MaterialVersion:
    return MaterialVersion(
        id=str(row["id"]),
        source_id=str(row["source_id"]),
        display_name=str(row["display_name"]),
        material_type=str(row["material_type"]),
        content_sha256=str(row["content_sha256"]),
        file_name=str(row["file_name"]),
        size_bytes=int(row["size_bytes"]),
        modified_ns=(
            int(row["modified_ns"])
            if row["modified_ns"] is not None
            else None
        ),
        unit_count=(
            int(row["unit_count"])
            if row["unit_count"] is not None
            else None
        ),
        parse_expected_unit_count=(
            int(row["parse_expected_unit_count"])
            if row["parse_expected_unit_count"] is not None
            else None
        ),
        preview_completed_count=int(row["preview_completed_count"]),
        ocr_completed_count=int(row["ocr_completed_count"]),
        ocr_total_count=int(row["ocr_total_count"]),
        inspection_status=str(row["inspection_status"]),
        availability=str(row["availability"]),
        source_revision=int(row["source_revision"]),
        source_archived_at=(
            str(row["source_archived_at"])
            if row["source_archived_at"] is not None
            else None
        ),
        created_at=str(row["created_at"]),
    )


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
