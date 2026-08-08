from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from uuid import uuid4

from backend.teaching_prep.domain.errors import (
    TeachingPrepConflictError,
    TeachingPrepNotFoundError,
    TeachingPrepValidationError,
)
from backend.teaching_prep.domain.models import (
    LessonMaterialLink,
    MaterialUnit,
)
from backend.teaching_prep.infrastructure.database import TeachingPrepDatabase
from backend.teaching_prep.infrastructure.materials import ParsedMaterialUnit


@dataclass(frozen=True, slots=True)
class MaterialPreviewRecord:
    unit_id: str
    material_version_id: str
    preview_relpath: str
    source_version_sha256: str


class MaterialUnitRepository:
    def __init__(self, database: TeachingPrepDatabase) -> None:
        self._database = database

    def set_parse_expected_count(
        self,
        material_version_id: str,
        *,
        source_version_sha256: str,
        unit_count: int,
    ) -> None:
        with self._database.connect(immediate=True) as connection:
            cursor = connection.execute(
                """
                UPDATE material_versions
                SET parse_expected_unit_count = ?
                WHERE id = ? AND content_sha256 = ?
                """,
                (int(unit_count), material_version_id, source_version_sha256),
            )
            if cursor.rowcount != 1:
                raise TeachingPrepConflictError(
                    "material source version changed before parsing"
                )

    def save_parsed_units(
        self,
        material_version_id: str,
        *,
        source_version_sha256: str,
        units: tuple[ParsedMaterialUnit, ...],
        preview_relpaths: tuple[str, ...],
        preview_hashes: tuple[str, ...],
    ) -> tuple[MaterialUnit, ...]:
        if not units:
            raise TeachingPrepValidationError(
                "material contains no previewable units"
            )
        if not (
            len(units) == len(preview_relpaths) == len(preview_hashes)
        ):
            raise ValueError("parsed unit persistence inputs differ in length")
        with self._database.connect(immediate=True) as connection:
            version = connection.execute(
                """
                SELECT content_sha256
                FROM material_versions
                WHERE id = ?
                """,
                (material_version_id,),
            ).fetchone()
            if version is None:
                raise TeachingPrepNotFoundError(
                    "material version was not found"
                )
            if str(version["content_sha256"]) != source_version_sha256:
                raise TeachingPrepConflictError(
                    "material source version changed before parsing"
                )
            highest_linked = connection.execute(
                """
                SELECT MAX(end_unit)
                FROM lesson_material_links
                WHERE material_version_id = ? AND is_active = 1
                """,
                (material_version_id,),
            ).fetchone()[0]
            if highest_linked is not None and int(highest_linked) > len(units):
                raise TeachingPrepConflictError(
                    "parsed unit count conflicts with confirmed links"
                )
            for unit, relative_path, preview_hash in zip(
                units,
                preview_relpaths,
                preview_hashes,
                strict=True,
            ):
                object_summary = json.dumps(
                    unit.object_summary,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                existing = connection.execute(
                    """
                    SELECT id
                    FROM material_units
                    WHERE material_version_id = ? AND unit_index = ?
                    """,
                    (material_version_id, unit.unit_index),
                ).fetchone()
                if existing is None:
                    connection.execute(
                        """
                        INSERT INTO material_units (
                            id,
                            material_version_id,
                            unit_kind,
                            unit_index,
                            title,
                            extracted_text,
                            text_status,
                            formula_review_required,
                            object_summary_json,
                            preview_relpath,
                            preview_sha256,
                            source_version_sha256
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            uuid4().hex,
                            material_version_id,
                            unit.unit_kind,
                            unit.unit_index,
                            unit.title,
                            unit.extracted_text,
                            unit.text_status,
                            int(unit.formula_review_required),
                            object_summary,
                            relative_path,
                            preview_hash,
                            source_version_sha256,
                        ),
                    )
                else:
                    connection.execute(
                        """
                        UPDATE material_units
                        SET unit_kind = ?,
                            title = CASE
                                WHEN text_status = 'manual' THEN title
                                ELSE ?
                            END,
                            extracted_text = CASE
                                WHEN text_status = 'manual' THEN extracted_text
                                ELSE ?
                            END,
                            text_status = CASE
                                WHEN text_status = 'manual' THEN text_status
                                ELSE ?
                            END,
                            formula_review_required = CASE
                                WHEN text_status = 'manual'
                                    THEN formula_review_required
                                ELSE ?
                            END,
                            object_summary_json = ?,
                            preview_relpath = ?,
                            preview_sha256 = ?,
                            source_version_sha256 = ?,
                            revision = revision + 1,
                            updated_at = strftime(
                                '%Y-%m-%dT%H:%M:%fZ',
                                'now'
                            )
                        WHERE id = ?
                        """,
                        (
                            unit.unit_kind,
                            unit.title,
                            unit.extracted_text,
                            unit.text_status,
                            int(unit.formula_review_required),
                            object_summary,
                            relative_path,
                            preview_hash,
                            source_version_sha256,
                            str(existing["id"]),
                        ),
                    )
            connection.execute(
                """
                DELETE FROM material_units
                WHERE material_version_id = ? AND unit_index > ?
                """,
                (material_version_id, len(units)),
            )
            rows = self._unit_rows(connection, material_version_id)
        return tuple(_unit(row) for row in rows)

    def save_partial_unit(
        self,
        material_version_id: str,
        *,
        source_version_sha256: str,
        unit: ParsedMaterialUnit,
        preview_relpath: str,
        preview_sha256: str,
    ) -> MaterialUnit:
        summary = dict(unit.object_summary)
        summary["ocr_required"] = bool(
            unit.unit_kind == "pdf_page"
            and unit.text_status != "manual"
            and not unit.extracted_text.strip()
            and bool(summary.get("has_page_images"))
        )
        object_summary = json.dumps(
            summary,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        with self._database.connect(immediate=True) as connection:
            version = connection.execute(
                """
                SELECT content_sha256
                FROM material_versions
                WHERE id = ?
                """,
                (material_version_id,),
            ).fetchone()
            if version is None:
                raise TeachingPrepNotFoundError(
                    "material version was not found"
                )
            if str(version["content_sha256"]) != source_version_sha256:
                raise TeachingPrepConflictError(
                    "material source version changed before parsing"
                )
            existing = connection.execute(
                """
                SELECT id
                FROM material_units
                WHERE material_version_id = ? AND unit_index = ?
                """,
                (material_version_id, unit.unit_index),
            ).fetchone()
            if existing is None:
                unit_id = uuid4().hex
                connection.execute(
                    """
                    INSERT INTO material_units (
                        id,
                        material_version_id,
                        unit_kind,
                        unit_index,
                        title,
                        extracted_text,
                        text_status,
                        formula_review_required,
                        object_summary_json,
                        preview_relpath,
                        preview_sha256,
                        source_version_sha256
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        unit_id,
                        material_version_id,
                        unit.unit_kind,
                        unit.unit_index,
                        unit.title,
                        unit.extracted_text,
                        unit.text_status,
                        int(unit.formula_review_required),
                        object_summary,
                        preview_relpath,
                        preview_sha256,
                        source_version_sha256,
                    ),
                )
            else:
                unit_id = str(existing["id"])
                connection.execute(
                    """
                    UPDATE material_units
                    SET unit_kind = ?,
                        title = CASE
                            WHEN text_status = 'manual' THEN title
                            ELSE ?
                        END,
                        extracted_text = CASE
                            WHEN text_status = 'manual' THEN extracted_text
                            ELSE ?
                        END,
                        text_status = CASE
                            WHEN text_status = 'manual' THEN text_status
                            ELSE ?
                        END,
                        formula_review_required = CASE
                            WHEN text_status = 'manual'
                                THEN formula_review_required
                            ELSE ?
                        END,
                        object_summary_json = ?,
                        preview_relpath = ?,
                        preview_sha256 = ?,
                        source_version_sha256 = ?,
                        revision = revision + 1,
                        updated_at = strftime(
                            '%Y-%m-%dT%H:%M:%fZ',
                            'now'
                        )
                    WHERE id = ?
                    """,
                    (
                        unit.unit_kind,
                        unit.title,
                        unit.extracted_text,
                        unit.text_status,
                        int(unit.formula_review_required),
                        object_summary,
                        preview_relpath,
                        preview_sha256,
                        source_version_sha256,
                        unit_id,
                    ),
                )
            row = connection.execute(
                "SELECT * FROM material_units WHERE id = ?",
                (unit_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("parsed material unit could not be loaded")
        return _unit(row)

    def publish_preview_count(
        self,
        material_version_id: str,
        *,
        source_version_sha256: str,
        unit_count: int,
    ) -> None:
        with self._database.connect(immediate=True) as connection:
            version = connection.execute(
                """
                SELECT content_sha256
                FROM material_versions
                WHERE id = ?
                """,
                (material_version_id,),
            ).fetchone()
            if version is None:
                raise TeachingPrepNotFoundError(
                    "material version was not found"
                )
            if str(version["content_sha256"]) != source_version_sha256:
                raise TeachingPrepConflictError(
                    "material source version changed before parsing"
                )
            actual_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM material_units
                    WHERE material_version_id = ?
                      AND unit_index BETWEEN 1 AND ?
                    """,
                    (material_version_id, int(unit_count)),
                ).fetchone()[0]
            )
            if actual_count != int(unit_count):
                raise TeachingPrepConflictError(
                    "material preview set is incomplete"
                )
            connection.execute(
                """
                UPDATE material_versions
                SET unit_count = ?
                WHERE id = ?
                """,
                (int(unit_count), material_version_id),
            )

    def update_local_ocr(
        self,
        unit_id: str,
        *,
        source_version_sha256: str,
        extracted_text: str,
        formula_review_required: bool,
        printed_page_number: int | None,
        layout_items: tuple[dict[str, object], ...] = (),
    ) -> MaterialUnit:
        with self._database.connect(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM material_units WHERE id = ?",
                (unit_id,),
            ).fetchone()
            if row is None:
                raise TeachingPrepNotFoundError(
                    "material unit was not found"
                )
            if str(row["source_version_sha256"]) != source_version_sha256:
                raise TeachingPrepConflictError(
                    "material source version changed during OCR"
                )
            if str(row["text_status"]) == "manual":
                return _unit(row)
            summary = json.loads(str(row["object_summary_json"] or "{}"))
            summary["ocr_status"] = "completed"
            if extracted_text:
                summary["text_source"] = "local_ocr"
            if layout_items:
                summary["ocr_layout"] = {
                    "version": 1,
                    "items": list(layout_items),
                }
            else:
                summary.pop("ocr_layout", None)
            if printed_page_number is not None:
                summary.update(
                    {
                        "printed_page_number": int(printed_page_number),
                        "printed_page_number_source": (
                            "local_ocr_footer_or_header"
                        ),
                    }
                )
            title = next(
                (
                    line.strip()
                    for line in str(extracted_text or "").splitlines()
                    if line.strip()
                ),
                None,
            )
            connection.execute(
                """
                UPDATE material_units
                SET title = ?,
                    extracted_text = ?,
                    formula_review_required = ?,
                    object_summary_json = ?,
                    revision = revision + 1,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ? AND text_status <> 'manual'
                """,
                (
                    title,
                    str(extracted_text or ""),
                    int(formula_review_required),
                    json.dumps(
                        summary,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    unit_id,
                ),
            )
            updated = connection.execute(
                "SELECT * FROM material_units WHERE id = ?",
                (unit_id,),
            ).fetchone()
        if updated is None:
            raise RuntimeError("OCR material unit could not be loaded")
        return _unit(updated)

    def complete_parse(
        self,
        material_version_id: str,
        *,
        source_version_sha256: str,
        unit_count: int,
    ) -> tuple[MaterialUnit, ...]:
        with self._database.connect(immediate=True) as connection:
            version = connection.execute(
                """
                SELECT content_sha256
                FROM material_versions
                WHERE id = ?
                """,
                (material_version_id,),
            ).fetchone()
            if version is None:
                raise TeachingPrepNotFoundError(
                    "material version was not found"
                )
            if str(version["content_sha256"]) != source_version_sha256:
                raise TeachingPrepConflictError(
                    "material source version changed before publishing"
                )
            highest_linked = connection.execute(
                """
                SELECT MAX(end_unit)
                FROM lesson_material_links
                WHERE material_version_id = ? AND is_active = 1
                """,
                (material_version_id,),
            ).fetchone()[0]
            if highest_linked is not None and int(highest_linked) > int(unit_count):
                raise TeachingPrepConflictError(
                    "parsed unit count conflicts with confirmed links"
                )
            rows = self._unit_rows(connection, material_version_id)
            if len(rows) != int(unit_count):
                raise TeachingPrepConflictError(
                    "material parse did not produce every unit"
                )
            has_text = any(str(row["extracted_text"] or "").strip() for row in rows)
            connection.execute(
                """
                UPDATE material_versions
                SET unit_count = ?,
                    inspection_status = ?
                WHERE id = ?
                """,
                (
                    int(unit_count),
                    "ready" if has_text else "scanned_no_text",
                    material_version_id,
                ),
            )
        return tuple(_unit(row) for row in rows)

    def list_units(
        self,
        material_version_id: str,
    ) -> tuple[MaterialUnit, ...]:
        with self._database.connect() as connection:
            if connection.execute(
                "SELECT 1 FROM material_versions WHERE id = ?",
                (material_version_id,),
            ).fetchone() is None:
                raise TeachingPrepNotFoundError(
                    "material version was not found"
                )
            rows = self._unit_rows(connection, material_version_id)
        return tuple(_unit(row) for row in rows)

    def update_manual_label(
        self,
        unit_id: str,
        *,
        expected_revision: int,
        title: str | None,
        manual_text: str,
        formula_review_required: bool,
    ) -> MaterialUnit:
        with self._database.connect(immediate=True) as connection:
            cursor = connection.execute(
                """
                UPDATE material_units
                SET title = ?,
                    extracted_text = ?,
                    text_status = 'manual',
                    formula_review_required = ?,
                    revision = revision + 1,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ? AND revision = ?
                """,
                (
                    title,
                    manual_text,
                    int(formula_review_required),
                    unit_id,
                    expected_revision,
                ),
            )
            if cursor.rowcount != 1:
                _raise_missing_or_stale(connection, "material_units", unit_id)
            row = connection.execute(
                "SELECT * FROM material_units WHERE id = ?",
                (unit_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("updated material unit could not be loaded")
        return _unit(row)

    def preview_record(self, unit_id: str) -> MaterialPreviewRecord:
        with self._database.connect() as connection:
            row = connection.execute(
                """
                SELECT
                    id,
                    material_version_id,
                    preview_relpath,
                    source_version_sha256
                FROM material_units
                WHERE id = ?
                """,
                (unit_id,),
            ).fetchone()
        if row is None:
            raise TeachingPrepNotFoundError("material unit was not found")
        return MaterialPreviewRecord(
            unit_id=str(row["id"]),
            material_version_id=str(row["material_version_id"]),
            preview_relpath=str(row["preview_relpath"]),
            source_version_sha256=str(row["source_version_sha256"]),
        )

    def create_link(
        self,
        *,
        request_token: str,
        lesson_node_id: str,
        material_version_id: str,
        start_unit: int,
        end_unit: int,
        crop: dict[str, float] | None,
        purpose: str,
        teacher_note: str | None,
        confirmation_status: str,
    ) -> tuple[LessonMaterialLink, bool]:
        values = {
            "lesson_node_id": lesson_node_id,
            "material_version_id": material_version_id,
            "start_unit": start_unit,
            "end_unit": end_unit,
            "crop": crop,
            "purpose": purpose,
            "teacher_note": teacher_note,
            "confirmation_status": confirmation_status,
        }
        request_hash = _request_hash(values)
        with self._database.connect(immediate=True) as connection:
            existing = self._link_query(
                connection,
                "link.request_token = ?",
                (request_token,),
            )
            if existing is not None:
                if str(existing["request_hash"]) != request_hash:
                    raise TeachingPrepConflictError(
                        "request token was reused for a different link"
                    )
                return _link(existing), False
            lesson = connection.execute(
                """
                SELECT node_type
                FROM lesson_nodes
                WHERE id = ? AND is_active = 1
                """,
                (lesson_node_id,),
            ).fetchone()
            if lesson is None:
                raise TeachingPrepNotFoundError(
                    "active lesson node was not found"
                )
            if str(lesson["node_type"]) != "lesson":
                raise TeachingPrepValidationError(
                    "material ranges can only attach to lesson nodes"
                )
            version_sha = _require_valid_range(
                connection,
                material_version_id,
                start_unit,
                end_unit,
            )
            next_order = int(
                connection.execute(
                    """
                    SELECT COALESCE(MAX(sort_order), 0) + 1
                    FROM lesson_material_links
                    WHERE lesson_node_id = ?
                    """,
                    (lesson_node_id,),
                ).fetchone()[0]
            )
            link_id = uuid4().hex
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
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    link_id,
                    request_token,
                    request_hash,
                    lesson_node_id,
                    material_version_id,
                    start_unit,
                    end_unit,
                    (
                        json.dumps(crop, sort_keys=True, separators=(",", ":"))
                        if crop is not None
                        else None
                    ),
                    purpose,
                    teacher_note,
                    confirmation_status,
                    version_sha,
                    next_order,
                ),
            )
            row = self._link_query(
                connection,
                "link.id = ?",
                (link_id,),
            )
        if row is None:
            raise RuntimeError("created material link could not be loaded")
        return _link(row), True

    def list_links(
        self,
        lesson_node_id: str,
    ) -> tuple[LessonMaterialLink, ...]:
        with self._database.connect() as connection:
            if connection.execute(
                "SELECT 1 FROM lesson_nodes WHERE id = ?",
                (lesson_node_id,),
            ).fetchone() is None:
                raise TeachingPrepNotFoundError(
                    "lesson node was not found"
                )
            rows = connection.execute(
                _LINK_SELECT
                + """
                WHERE link.lesson_node_id = ?
                ORDER BY link.sort_order, link.id
                """,
                (lesson_node_id,),
            ).fetchall()
        return tuple(_link(row) for row in rows)

    def update_link(
        self,
        link_id: str,
        *,
        expected_revision: int,
        start_unit: int,
        end_unit: int,
        crop: dict[str, float] | None,
        purpose: str,
        teacher_note: str | None,
        confirmation_status: str,
        is_active: bool,
    ) -> LessonMaterialLink:
        with self._database.connect(immediate=True) as connection:
            current = connection.execute(
                """
                SELECT material_version_id, source_version_sha256
                FROM lesson_material_links
                WHERE id = ?
                """,
                (link_id,),
            ).fetchone()
            if current is None:
                raise TeachingPrepNotFoundError(
                    "material link was not found"
                )
            version_sha = _require_valid_range(
                connection,
                str(current["material_version_id"]),
                start_unit,
                end_unit,
            )
            if version_sha != str(current["source_version_sha256"]):
                raise TeachingPrepConflictError(
                    "linked material version changed"
                )
            cursor = connection.execute(
                """
                UPDATE lesson_material_links
                SET start_unit = ?,
                    end_unit = ?,
                    crop_json = ?,
                    purpose = ?,
                    teacher_note = ?,
                    confirmation_status = ?,
                    is_active = ?,
                    revision = revision + 1,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ? AND revision = ?
                """,
                (
                    start_unit,
                    end_unit,
                    (
                        json.dumps(crop, sort_keys=True, separators=(",", ":"))
                        if crop is not None
                        else None
                    ),
                    purpose,
                    teacher_note,
                    confirmation_status,
                    int(is_active),
                    link_id,
                    expected_revision,
                ),
            )
            if cursor.rowcount != 1:
                _raise_missing_or_stale(
                    connection,
                    "lesson_material_links",
                    link_id,
                )
            row = self._link_query(
                connection,
                "link.id = ?",
                (link_id,),
            )
        if row is None:
            raise RuntimeError("updated material link could not be loaded")
        return _link(row)

    @staticmethod
    def _unit_rows(
        connection: sqlite3.Connection,
        material_version_id: str,
    ) -> list[sqlite3.Row]:
        return connection.execute(
            """
            SELECT *
            FROM material_units
            WHERE material_version_id = ?
            ORDER BY unit_index
            """,
            (material_version_id,),
        ).fetchall()

    @staticmethod
    def _link_query(
        connection: sqlite3.Connection,
        condition: str,
        parameters: tuple[object, ...],
    ) -> sqlite3.Row | None:
        return connection.execute(
            _LINK_SELECT + f"WHERE {condition}",
            parameters,
        ).fetchone()


_LINK_SELECT = """
SELECT
    link.*,
    source.display_name AS material_name,
    source.material_type AS material_type
FROM lesson_material_links AS link
JOIN material_versions AS version
  ON version.id = link.material_version_id
JOIN material_sources AS source
  ON source.id = version.source_id
"""


def _require_valid_range(
    connection: sqlite3.Connection,
    material_version_id: str,
    start_unit: int,
    end_unit: int,
) -> str:
    row = connection.execute(
        """
        SELECT
            version.content_sha256,
            version.unit_count AS expected_unit_count,
            version.inspection_status,
            COUNT(unit.id) AS unit_count
        FROM material_versions AS version
        LEFT JOIN material_units AS unit
          ON unit.material_version_id = version.id
        WHERE version.id = ?
        GROUP BY version.id
        """,
        (material_version_id,),
    ).fetchone()
    if row is None:
        raise TeachingPrepNotFoundError("material version was not found")
    count = int(row["unit_count"])
    expected_count = (
        int(row["expected_unit_count"])
        if row["expected_unit_count"] is not None
        else 0
    )
    if (
        str(row["inspection_status"])
        not in {"ready", "scanned_no_text"}
        or expected_count <= 0
        or count != expected_count
    ):
        raise TeachingPrepConflictError(
            "material must finish parsing before linking units"
        )
    if start_unit < 1 or end_unit < start_unit or end_unit > count:
        raise TeachingPrepValidationError("material unit range is invalid")
    return str(row["content_sha256"])


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


def _request_hash(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _unit(row: sqlite3.Row) -> MaterialUnit:
    try:
        summary = json.loads(str(row["object_summary_json"] or "{}"))
    except json.JSONDecodeError:
        summary = {}
    if not isinstance(summary, dict):
        summary = {}
    text = str(row["extracted_text"] or "")
    return MaterialUnit(
        id=str(row["id"]),
        material_version_id=str(row["material_version_id"]),
        unit_kind=str(row["unit_kind"]),
        unit_index=int(row["unit_index"]),
        title=str(row["title"]) if row["title"] is not None else None,
        text_excerpt=text[:400],
        text_status=str(row["text_status"]),
        formula_review_required=bool(row["formula_review_required"]),
        object_summary=summary,
        preview_url=f"/api/teaching-prep/material-units/{row['id']}/preview",
        revision=int(row["revision"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _link(row: sqlite3.Row) -> LessonMaterialLink:
    crop_value = None
    if row["crop_json"] is not None:
        try:
            candidate = json.loads(str(row["crop_json"]))
            if isinstance(candidate, dict):
                crop_value = {
                    str(key): float(value)
                    for key, value in candidate.items()
                }
        except (json.JSONDecodeError, TypeError, ValueError):
            crop_value = None
    return LessonMaterialLink(
        id=str(row["id"]),
        lesson_node_id=str(row["lesson_node_id"]),
        material_version_id=str(row["material_version_id"]),
        material_name=str(row["material_name"]),
        material_type=str(row["material_type"]),
        start_unit=int(row["start_unit"]),
        end_unit=int(row["end_unit"]),
        crop=crop_value,
        purpose=str(row["purpose"]),
        teacher_note=(
            str(row["teacher_note"])
            if row["teacher_note"] is not None
            else None
        ),
        confirmation_status=str(row["confirmation_status"]),
        source_version_sha256=str(row["source_version_sha256"]),
        sort_order=int(row["sort_order"]),
        is_active=bool(row["is_active"]),
        revision=int(row["revision"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


__all__ = ["MaterialPreviewRecord", "MaterialUnitRepository"]
