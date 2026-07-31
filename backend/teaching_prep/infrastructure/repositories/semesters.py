from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from uuid import uuid4

from backend.teaching_prep.domain.errors import (
    TeachingPrepConflictError,
    TeachingPrepNotFoundError,
)
from backend.teaching_prep.domain.models import (
    CurriculumEdition,
    SemesterLessonProgress,
    SemesterMaterialRecord,
    TeachingSemester,
)
from backend.teaching_prep.infrastructure.database import TeachingPrepDatabase


class SemesterWorkspaceRepository:
    def __init__(self, database: TeachingPrepDatabase) -> None:
        self._database = database

    def create(
        self,
        *,
        request_token: str,
        curriculum_id: str,
        school_year: str,
        term: str,
        planned_new_lesson_count: int,
    ) -> tuple[TeachingSemester, bool]:
        values = {
            "curriculum_id": curriculum_id,
            "school_year": school_year,
            "term": term,
            "planned_new_lesson_count": planned_new_lesson_count,
        }
        request_hash = _request_hash(values)
        with self._database.connect(immediate=True) as connection:
            existing = connection.execute(
                "SELECT * FROM teaching_semesters WHERE request_token = ?",
                (request_token,),
            ).fetchone()
            if existing is not None:
                _require_same_request(existing, request_hash)
                return self._get(connection, str(existing["id"])), False
            curriculum = connection.execute(
                "SELECT grade_level, volume FROM curriculum_editions WHERE id = ?",
                (curriculum_id,),
            ).fetchone()
            if curriculum is None:
                raise TeachingPrepNotFoundError("curriculum was not found")
            if connection.execute(
                "SELECT 1 FROM teaching_semesters WHERE curriculum_id = ?",
                (curriculum_id,),
            ).fetchone() is not None:
                raise TeachingPrepConflictError(
                    "this curriculum already belongs to a semester"
                )
            scope = connection.execute(
                """
                SELECT 1
                FROM teaching_semesters AS semester
                JOIN curriculum_editions AS existing_curriculum
                  ON existing_curriculum.id = semester.curriculum_id
                WHERE existing_curriculum.grade_level = ?
                  AND existing_curriculum.volume = ?
                  AND semester.school_year = ?
                  AND semester.term = ?
                LIMIT 1
                """,
                (
                    int(curriculum["grade_level"]),
                    str(curriculum["volume"]),
                    school_year,
                    term,
                ),
            ).fetchone()
            if scope is not None:
                raise TeachingPrepConflictError(
                    "a semester workspace already exists for this "
                    "school year, term, grade, and volume"
                )
            semester_id = uuid4().hex
            connection.execute(
                """
                INSERT INTO teaching_semesters (
                    id,
                    request_token,
                    request_hash,
                    curriculum_id,
                    school_year,
                    term,
                    planned_new_lesson_count
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    semester_id,
                    request_token,
                    request_hash,
                    curriculum_id,
                    school_year,
                    term,
                    planned_new_lesson_count,
                ),
            )
            item = self._get(connection, semester_id)
        return item, True

    def create_workspace(
        self,
        *,
        request_token: str,
        title: str,
        grade_level: int,
        volume: str,
        publisher: str | None,
        edition_label: str | None,
        school_year: str,
        term: str,
        planned_new_lesson_count: int,
    ) -> tuple[CurriculumEdition, TeachingSemester, bool]:
        """Create one semester workspace atomically and recover its identity.

        A browser request token makes an immediate repeat cheap, while the
        semantic identity lets a refresh or lost response find the same
        completed workspace.  The transaction never commits a curriculum
        without its semester.
        """
        curriculum_values = {
            "title": title,
            "grade_level": grade_level,
            "volume": volume,
            "publisher": publisher,
            "edition_label": edition_label,
        }
        curriculum_token = _derived_token(
            "semester-workspace-curriculum",
            request_token,
        )
        semester_token = _derived_token(
            "semester-workspace-semester",
            request_token,
        )
        semester_values = {
            "school_year": school_year,
            "term": term,
            "planned_new_lesson_count": planned_new_lesson_count,
        }
        with self._database.connect(immediate=True) as connection:
            existing = self._find_workspace_for_scope(
                connection,
                grade_level=grade_level,
                volume=volume,
                school_year=school_year,
                term=term,
            )
            if existing is not None:
                if not _same_curriculum_identity(
                    existing,
                    title=title,
                    publisher=publisher,
                    edition_label=edition_label,
                ):
                    raise TeachingPrepConflictError(
                        "a semester workspace already exists for this "
                        "school year, term, grade, and volume"
                    )
                return (
                    _curriculum(existing),
                    self._get(connection, str(existing["semester_id"])),
                    False,
                )
            self._require_workspace_token_available(
                connection,
                token=semester_token,
            )
            curriculum = self._find_orphan_curriculum(
                connection,
                title=title,
                grade_level=grade_level,
                volume=volume,
                publisher=publisher,
                edition_label=edition_label,
            )
            if curriculum is None:
                self._require_workspace_token_available(
                    connection,
                    token=curriculum_token,
                    table="curriculum_editions",
                )
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
                        curriculum_token,
                        _request_hash(curriculum_values),
                        title,
                        grade_level,
                        volume,
                        publisher,
                        edition_label,
                    ),
                )
                curriculum_row = connection.execute(
                    "SELECT * FROM curriculum_editions WHERE id = ?",
                    (curriculum_id,),
                ).fetchone()
                if curriculum_row is None:
                    raise RuntimeError("created curriculum could not be loaded")
                curriculum = _curriculum(curriculum_row)
            semester_id = uuid4().hex
            connection.execute(
                """
                INSERT INTO teaching_semesters (
                    id,
                    request_token,
                    request_hash,
                    curriculum_id,
                    school_year,
                    term,
                    planned_new_lesson_count
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    semester_id,
                    semester_token,
                    _request_hash(
                        {
                            "curriculum_id": curriculum.id,
                            **semester_values,
                        }
                    ),
                    curriculum.id,
                    school_year,
                    term,
                    planned_new_lesson_count,
                ),
            )
            semester = self._get(connection, semester_id)
        return curriculum, semester, True

    def list(self) -> tuple[TeachingSemester, ...]:
        with self._database.connect() as connection:
            rows = connection.execute(
                _SEMESTER_SUMMARY_SQL
                + """
                ORDER BY
                    CASE semester.status
                        WHEN 'active' THEN 0
                        WHEN 'planning' THEN 1
                        WHEN 'completed' THEN 2
                        ELSE 3
                    END,
                    semester.school_year DESC,
                    semester.term,
                    semester.id
                """
            ).fetchall()
        return tuple(_semester(row) for row in rows)

    @staticmethod
    def _find_workspace_for_scope(
        connection: sqlite3.Connection,
        *,
        grade_level: int,
        volume: str,
        school_year: str,
        term: str,
    ) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT
                semester.id AS semester_id,
                curriculum.*
            FROM teaching_semesters AS semester
            JOIN curriculum_editions AS curriculum
              ON curriculum.id = semester.curriculum_id
            WHERE curriculum.grade_level = ?
              AND curriculum.volume = ?
              AND semester.school_year = ?
              AND semester.term = ?
            ORDER BY semester.created_at DESC, semester.id DESC
            LIMIT 1
            """,
            (
                grade_level,
                volume,
                school_year,
                term,
            ),
        ).fetchone()

    @staticmethod
    def _find_orphan_curriculum(
        connection: sqlite3.Connection,
        *,
        title: str,
        grade_level: int,
        volume: str,
        publisher: str | None,
        edition_label: str | None,
    ) -> CurriculumEdition | None:
        row = connection.execute(
            """
            SELECT curriculum.*
            FROM curriculum_editions AS curriculum
            LEFT JOIN teaching_semesters AS semester
              ON semester.curriculum_id = curriculum.id
            WHERE curriculum.title = ?
              AND curriculum.grade_level = ?
              AND curriculum.volume = ?
              AND curriculum.publisher IS ?
              AND curriculum.edition_label IS ?
              AND semester.id IS NULL
            ORDER BY curriculum.created_at DESC, curriculum.id DESC
            LIMIT 1
            """,
            (
                title,
                grade_level,
                volume,
                publisher,
                edition_label,
            ),
        ).fetchone()
        return _curriculum(row) if row is not None else None

    @staticmethod
    def _require_workspace_token_available(
        connection: sqlite3.Connection,
        *,
        token: str,
        table: str = "teaching_semesters",
    ) -> None:
        if table == "teaching_semesters":
            statement = (
                "SELECT 1 FROM teaching_semesters WHERE request_token = ?"
            )
        elif table == "curriculum_editions":
            statement = (
                "SELECT 1 FROM curriculum_editions WHERE request_token = ?"
            )
        else:
            raise ValueError("workspace token table is invalid")
        row = connection.execute(
            statement,
            (token,),
        ).fetchone()
        if row is not None:
            raise TeachingPrepConflictError(
                "semester workspace request token was reused for different data"
            )

    def get(self, semester_id: str) -> TeachingSemester:
        with self._database.connect() as connection:
            return self._get(connection, semester_id)

    def update(
        self,
        semester_id: str,
        *,
        expected_revision: int,
        planned_new_lesson_count: int,
        status: str,
    ) -> TeachingSemester:
        with self._database.connect(immediate=True) as connection:
            cursor = connection.execute(
                """
                UPDATE teaching_semesters
                SET planned_new_lesson_count = ?,
                    status = ?,
                    revision = revision + 1,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ? AND revision = ?
                """,
                (
                    planned_new_lesson_count,
                    status,
                    semester_id,
                    expected_revision,
                ),
            )
            if cursor.rowcount != 1:
                _raise_missing_or_stale(
                    connection,
                    "teaching_semesters",
                    semester_id,
                )
            return self._get(connection, semester_id)

    def set_lesson_progress(
        self,
        semester_id: str,
        lesson_node_id: str,
        *,
        status: str,
        expected_revision: int | None,
    ) -> SemesterLessonProgress:
        with self._database.connect(immediate=True) as connection:
            semester = connection.execute(
                """
                SELECT curriculum_id
                FROM teaching_semesters
                WHERE id = ?
                """,
                (semester_id,),
            ).fetchone()
            if semester is None:
                raise TeachingPrepNotFoundError("semester was not found")
            lesson = connection.execute(
                """
                SELECT id
                FROM lesson_nodes
                WHERE id = ?
                  AND curriculum_id = ?
                  AND node_type = 'lesson'
                  AND is_active = 1
                """,
                (lesson_node_id, str(semester["curriculum_id"])),
            ).fetchone()
            if lesson is None:
                raise TeachingPrepConflictError(
                    "lesson does not belong to this semester"
                )
            current = connection.execute(
                """
                SELECT *
                FROM semester_lesson_progress
                WHERE semester_id = ? AND lesson_node_id = ?
                """,
                (semester_id, lesson_node_id),
            ).fetchone()
            if current is None:
                if expected_revision is not None:
                    raise TeachingPrepConflictError(
                        "lesson progress changed; refresh before saving"
                    )
                progress_id = uuid4().hex
                connection.execute(
                    """
                    INSERT INTO semester_lesson_progress (
                        id,
                        semester_id,
                        lesson_node_id,
                        status
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    (progress_id, semester_id, lesson_node_id, status),
                )
            else:
                progress_id = str(current["id"])
                if str(current["status"]) == status:
                    return self._lesson_progress(connection, progress_id)
                if expected_revision != int(current["revision"]):
                    raise TeachingPrepConflictError(
                        "lesson progress changed; refresh before saving"
                    )
                connection.execute(
                    """
                    UPDATE semester_lesson_progress
                    SET status = ?,
                        revision = revision + 1,
                        updated_at = strftime(
                            '%Y-%m-%dT%H:%M:%fZ',
                            'now'
                        )
                    WHERE id = ?
                    """,
                    (status, progress_id),
                )
            return self._lesson_progress(connection, progress_id)

    def list_lesson_progress(
        self,
        semester_id: str,
    ) -> tuple[SemesterLessonProgress, ...]:
        with self._database.connect() as connection:
            if connection.execute(
                "SELECT 1 FROM teaching_semesters WHERE id = ?",
                (semester_id,),
            ).fetchone() is None:
                raise TeachingPrepNotFoundError("semester was not found")
            rows = connection.execute(
                """
                SELECT progress.*, lesson.title AS lesson_title
                FROM semester_lesson_progress AS progress
                JOIN lesson_nodes AS lesson
                  ON lesson.id = progress.lesson_node_id
                WHERE progress.semester_id = ?
                ORDER BY progress.updated_at DESC, progress.id
                """,
                (semester_id,),
            ).fetchall()
        return tuple(_lesson_progress(row) for row in rows)

    def attach_material(
        self,
        semester_id: str,
        *,
        request_token: str,
        material_version_id: str,
        material_role: str,
    ) -> tuple[SemesterMaterialRecord, bool]:
        values = {
            "semester_id": semester_id,
            "material_version_id": material_version_id,
            "material_role": material_role,
        }
        request_hash = _request_hash(values)
        with self._database.connect(immediate=True) as connection:
            token_row = connection.execute(
                """
                SELECT *
                FROM semester_material_records
                WHERE request_token = ?
                """,
                (request_token,),
            ).fetchone()
            if token_row is not None:
                _require_same_request(token_row, request_hash)
                return (
                    self._material_record(
                        connection,
                        str(token_row["id"]),
                    ),
                    False,
                )
            if connection.execute(
                "SELECT 1 FROM teaching_semesters WHERE id = ?",
                (semester_id,),
            ).fetchone() is None:
                raise TeachingPrepNotFoundError("semester was not found")
            version = connection.execute(
                """
                SELECT source_id
                FROM material_versions
                WHERE id = ?
                """,
                (material_version_id,),
            ).fetchone()
            if version is None:
                raise TeachingPrepNotFoundError(
                    "material version was not found"
                )
            source_id = str(version["source_id"])
            existing = connection.execute(
                """
                SELECT id, material_role
                FROM semester_material_records
                WHERE semester_id = ? AND material_source_id = ?
                """,
                (semester_id, source_id),
            ).fetchone()
            if existing is not None:
                if str(existing["material_role"]) != material_role:
                    raise TeachingPrepConflictError(
                        "material already has a different semester role"
                    )
                return (
                    self._material_record(
                        connection,
                        str(existing["id"]),
                    ),
                    False,
                )
            self._require_homework_slot(
                connection,
                semester_id=semester_id,
                material_role=material_role,
                excluding_id=None,
            )
            parsed = connection.execute(
                """
                SELECT 1
                FROM material_units
                WHERE material_version_id = ?
                LIMIT 1
                """,
                (material_version_id,),
            ).fetchone() is not None
            record_id = uuid4().hex
            connection.execute(
                """
                INSERT INTO semester_material_records (
                    id,
                    request_token,
                    request_hash,
                    semester_id,
                    material_source_id,
                    material_role,
                    parse_status,
                    last_parsed_version_id,
                    parsed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    request_token,
                    request_hash,
                    semester_id,
                    source_id,
                    material_role,
                    "parsed" if parsed else "not_started",
                    material_version_id if parsed else None,
                    (
                        _now(connection)
                        if parsed
                        else None
                    ),
                ),
            )
            return self._material_record(connection, record_id), True

    def list_materials(
        self,
        semester_id: str,
    ) -> tuple[SemesterMaterialRecord, ...]:
        with self._database.connect() as connection:
            if connection.execute(
                "SELECT 1 FROM teaching_semesters WHERE id = ?",
                (semester_id,),
            ).fetchone() is None:
                raise TeachingPrepNotFoundError("semester was not found")
            rows = connection.execute(
                _MATERIAL_RECORD_SQL
                + """
                WHERE record.semester_id = ?
                ORDER BY record.is_active DESC, record.created_at, record.id
                """,
                (semester_id,),
            ).fetchall()
        return tuple(_material_record(row) for row in rows)

    def update_material(
        self,
        record_id: str,
        *,
        expected_revision: int,
        material_role: str,
        mapping_status: str,
        is_active: bool,
    ) -> SemesterMaterialRecord:
        with self._database.connect(immediate=True) as connection:
            current = connection.execute(
                """
                SELECT semester_id
                FROM semester_material_records
                WHERE id = ?
                """,
                (record_id,),
            ).fetchone()
            if current is None:
                raise TeachingPrepNotFoundError(
                    "semester material was not found"
                )
            if is_active:
                self._require_homework_slot(
                    connection,
                    semester_id=str(current["semester_id"]),
                    material_role=material_role,
                    excluding_id=record_id,
                )
            cursor = connection.execute(
                """
                UPDATE semester_material_records
                SET material_role = ?,
                    mapping_status = ?,
                    is_active = ?,
                    revision = revision + 1,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ? AND revision = ?
                """,
                (
                    material_role,
                    mapping_status,
                    int(is_active),
                    record_id,
                    expected_revision,
                ),
            )
            if cursor.rowcount != 1:
                _raise_missing_or_stale(
                    connection,
                    "semester_material_records",
                    record_id,
                )
            return self._material_record(connection, record_id)

    def mark_version_parsed(self, material_version_id: str) -> None:
        with self._database.connect(immediate=True) as connection:
            version = connection.execute(
                """
                SELECT source_id
                FROM material_versions
                WHERE id = ?
                """,
                (material_version_id,),
            ).fetchone()
            if version is None:
                raise TeachingPrepNotFoundError(
                    "material version was not found"
                )
            connection.execute(
                """
                UPDATE semester_material_records
                SET parse_status = 'parsed',
                    mapping_status = CASE
                        WHEN last_parsed_version_id IS NOT NULL
                         AND last_parsed_version_id <> ?
                         AND mapping_status <> 'unmapped'
                            THEN 'needs_review'
                        ELSE mapping_status
                    END,
                    last_parsed_version_id = ?,
                    parsed_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    revision = revision + 1,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE material_source_id = ?
                  AND is_active = 1
                  AND (
                      last_parsed_version_id IS NULL
                      OR last_parsed_version_id <> ?
                      OR parse_status <> 'parsed'
                  )
                """,
                (
                    material_version_id,
                    material_version_id,
                    str(version["source_id"]),
                    material_version_id,
                ),
            )

    def mark_version_parse_failed(self, material_version_id: str) -> None:
        with self._database.connect(immediate=True) as connection:
            version = connection.execute(
                """
                SELECT source_id
                FROM material_versions
                WHERE id = ?
                """,
                (material_version_id,),
            ).fetchone()
            if version is None:
                raise TeachingPrepNotFoundError(
                    "material version was not found"
                )
            connection.execute(
                """
                UPDATE semester_material_records
                SET parse_status = 'failed',
                    revision = revision + 1,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE material_source_id = ?
                  AND is_active = 1
                  AND ? = (
                      SELECT latest.id
                      FROM material_versions AS latest
                      WHERE latest.source_id = material_source_id
                      ORDER BY latest.created_at DESC, latest.id DESC
                      LIMIT 1
                  )
                  AND parse_status <> 'failed'
                """,
                (
                    str(version["source_id"]),
                    material_version_id,
                ),
            )

    @staticmethod
    def _require_homework_slot(
        connection: sqlite3.Connection,
        *,
        semester_id: str,
        material_role: str,
        excluding_id: str | None,
    ) -> None:
        if material_role != "homework_workbook":
            return
        row = connection.execute(
            """
            SELECT id
            FROM semester_material_records
            WHERE semester_id = ?
              AND material_role = 'homework_workbook'
              AND is_active = 1
              AND id IS NOT ?
            """,
            (semester_id, excluding_id),
        ).fetchone()
        if row is not None:
            raise TeachingPrepConflictError(
                "this semester already has a daily homework workbook"
            )

    def _get(
        self,
        connection: sqlite3.Connection,
        semester_id: str,
    ) -> TeachingSemester:
        row = connection.execute(
            _SEMESTER_SUMMARY_SQL + "WHERE semester.id = ?",
            (semester_id,),
        ).fetchone()
        if row is None:
            raise TeachingPrepNotFoundError("semester was not found")
        return _semester(row)

    @staticmethod
    def _lesson_progress(
        connection: sqlite3.Connection,
        progress_id: str,
    ) -> SemesterLessonProgress:
        row = connection.execute(
            """
            SELECT progress.*, lesson.title AS lesson_title
            FROM semester_lesson_progress AS progress
            JOIN lesson_nodes AS lesson
              ON lesson.id = progress.lesson_node_id
            WHERE progress.id = ?
            """,
            (progress_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError("lesson progress could not be loaded")
        return _lesson_progress(row)

    @staticmethod
    def _material_record(
        connection: sqlite3.Connection,
        record_id: str,
    ) -> SemesterMaterialRecord:
        row = connection.execute(
            _MATERIAL_RECORD_SQL + "WHERE record.id = ?",
            (record_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError("semester material could not be loaded")
        return _material_record(row)


_SEMESTER_SUMMARY_SQL = """
SELECT
    semester.*,
    curriculum.title AS curriculum_title,
    (
        SELECT COUNT(*)
        FROM lesson_nodes AS lesson
        WHERE lesson.curriculum_id = semester.curriculum_id
          AND lesson.node_type = 'lesson'
          AND lesson.is_active = 1
    ) AS active_lesson_count,
    (
        SELECT COUNT(*)
        FROM semester_lesson_progress AS progress
        JOIN lesson_nodes AS lesson
          ON lesson.id = progress.lesson_node_id
        WHERE progress.semester_id = semester.id
          AND lesson.is_active = 1
          AND progress.status = 'preparing'
    ) AS preparing_lesson_count,
    (
        SELECT COUNT(*)
        FROM semester_lesson_progress AS progress
        JOIN lesson_nodes AS lesson
          ON lesson.id = progress.lesson_node_id
        WHERE progress.semester_id = semester.id
          AND lesson.is_active = 1
          AND progress.status = 'ready'
    ) AS ready_lesson_count,
    (
        SELECT COUNT(*)
        FROM semester_lesson_progress AS progress
        JOIN lesson_nodes AS lesson
          ON lesson.id = progress.lesson_node_id
        WHERE progress.semester_id = semester.id
          AND lesson.is_active = 1
          AND progress.status = 'taught'
    ) AS taught_lesson_count,
    (
        SELECT COUNT(*)
        FROM semester_lesson_progress AS progress
        JOIN lesson_nodes AS lesson
          ON lesson.id = progress.lesson_node_id
        WHERE progress.semester_id = semester.id
          AND lesson.is_active = 1
          AND progress.status = 'skipped'
    ) AS skipped_lesson_count,
    (
        SELECT COUNT(*)
        FROM semester_material_records AS material
        WHERE material.semester_id = semester.id
          AND material.is_active = 1
    ) AS material_count,
    (
        SELECT COUNT(*)
        FROM semester_material_records AS material
        WHERE material.semester_id = semester.id
          AND material.is_active = 1
          AND material.parse_status = 'parsed'
    ) AS parsed_material_count,
    (
        SELECT COUNT(*)
        FROM semester_material_records AS material
        WHERE material.semester_id = semester.id
          AND material.is_active = 1
          AND material.mapping_status = 'confirmed'
    ) AS mapped_material_count
FROM teaching_semesters AS semester
JOIN curriculum_editions AS curriculum
  ON curriculum.id = semester.curriculum_id
"""


_MATERIAL_RECORD_SQL = """
SELECT
    record.*,
    source.display_name,
    current_version.id AS current_material_version_id,
    current_version.file_name AS current_file_name,
    current_version.inspection_status AS current_inspection_status,
    current_version.unit_count AS current_unit_count
FROM semester_material_records AS record
JOIN material_sources AS source
  ON source.id = record.material_source_id
JOIN material_versions AS current_version
  ON current_version.id = (
      SELECT version.id
      FROM material_versions AS version
      WHERE version.source_id = record.material_source_id
      ORDER BY version.created_at DESC, version.id DESC
      LIMIT 1
  )
"""


def _semester(row: sqlite3.Row) -> TeachingSemester:
    active = int(row["active_lesson_count"])
    preparing = int(row["preparing_lesson_count"])
    ready = int(row["ready_lesson_count"])
    taught = int(row["taught_lesson_count"])
    skipped = int(row["skipped_lesson_count"])
    return TeachingSemester(
        id=str(row["id"]),
        curriculum_id=str(row["curriculum_id"]),
        curriculum_title=str(row["curriculum_title"]),
        school_year=str(row["school_year"]),
        term=str(row["term"]),
        planned_new_lesson_count=int(row["planned_new_lesson_count"]),
        status=str(row["status"]),
        active_lesson_count=active,
        not_started_lesson_count=max(
            0,
            active - preparing - ready - taught - skipped,
        ),
        preparing_lesson_count=preparing,
        ready_lesson_count=ready,
        taught_lesson_count=taught,
        skipped_lesson_count=skipped,
        material_count=int(row["material_count"]),
        parsed_material_count=int(row["parsed_material_count"]),
        mapped_material_count=int(row["mapped_material_count"]),
        revision=int(row["revision"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _lesson_progress(row: sqlite3.Row) -> SemesterLessonProgress:
    return SemesterLessonProgress(
        id=str(row["id"]),
        semester_id=str(row["semester_id"]),
        lesson_node_id=str(row["lesson_node_id"]),
        lesson_title=str(row["lesson_title"]),
        status=str(row["status"]),
        revision=int(row["revision"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _material_record(row: sqlite3.Row) -> SemesterMaterialRecord:
    current_version_id = str(row["current_material_version_id"])
    last_parsed = (
        str(row["last_parsed_version_id"])
        if row["last_parsed_version_id"] is not None
        else None
    )
    return SemesterMaterialRecord(
        id=str(row["id"]),
        semester_id=str(row["semester_id"]),
        material_source_id=str(row["material_source_id"]),
        display_name=str(row["display_name"]),
        material_role=str(row["material_role"]),
        parse_status=str(row["parse_status"]),
        mapping_status=str(row["mapping_status"]),
        current_material_version_id=current_version_id,
        current_file_name=str(row["current_file_name"]),
        current_inspection_status=str(row["current_inspection_status"]),
        current_unit_count=(
            int(row["current_unit_count"])
            if row["current_unit_count"] is not None
            else None
        ),
        last_parsed_version_id=last_parsed,
        has_unparsed_update=(
            last_parsed is not None and last_parsed != current_version_id
        ),
        parsed_at=(
            str(row["parsed_at"]) if row["parsed_at"] is not None else None
        ),
        is_active=bool(row["is_active"]),
        revision=int(row["revision"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _request_hash(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        dict(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _derived_token(namespace: str, request_token: str) -> str:
    """Derive a safe, stable internal idempotency token from one request."""
    return hashlib.sha256(
        f"{namespace}:{request_token}".encode("utf-8")
    ).hexdigest()


def _curriculum(row: sqlite3.Row) -> CurriculumEdition:
    return CurriculumEdition(
        id=str(row["id"]),
        title=str(row["title"]),
        grade_level=int(row["grade_level"]),
        volume=str(row["volume"]),
        publisher=(
            str(row["publisher"])
            if row["publisher"] is not None
            else None
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


def _same_curriculum_identity(
    row: sqlite3.Row,
    *,
    title: str,
    publisher: str | None,
    edition_label: str | None,
) -> bool:
    return (
        str(row["title"]) == title
        and (
            str(row["publisher"])
            if row["publisher"] is not None
            else None
        )
        == publisher
        and (
            str(row["edition_label"])
            if row["edition_label"] is not None
            else None
        )
        == edition_label
    )


def _require_same_request(row: sqlite3.Row, request_hash: str) -> None:
    if str(row["request_hash"]) != request_hash:
        raise TeachingPrepConflictError(
            "request token was reused for different data"
        )


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


def _now(connection: sqlite3.Connection) -> str:
    return str(
        connection.execute(
            "SELECT strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"
        ).fetchone()[0]
    )


__all__ = ["SemesterWorkspaceRepository"]
