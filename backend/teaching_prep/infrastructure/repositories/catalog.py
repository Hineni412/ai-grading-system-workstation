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
