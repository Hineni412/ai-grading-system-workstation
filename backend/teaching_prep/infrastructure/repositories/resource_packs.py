from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from uuid import uuid4

from backend.teaching_prep.domain.errors import (
    TeachingPrepConflictError,
    TeachingPrepNotFoundError,
    TeachingPrepValidationError,
)
from backend.teaching_prep.domain.models import ResourcePackVersion
from backend.teaching_prep.infrastructure.database import TeachingPrepDatabase


class ResourcePackRepository:
    def __init__(self, database: TeachingPrepDatabase) -> None:
        self._database = database

    def find_idempotent(
        self,
        *,
        request_token: str,
        request_hash: str,
    ) -> ResourcePackVersion | None:
        with self._database.connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM resource_pack_versions
                WHERE request_token = ?
                """,
                (request_token,),
            ).fetchone()
        if row is None:
            return None
        if str(row["request_hash"]) != request_hash:
            raise TeachingPrepConflictError(
                "request token was reused for a different resource pack"
            )
        return _pack(row)

    def freeze(
        self,
        *,
        request_token: str,
        request_hash: str,
        lesson_node_id: str,
        class_name: str | None,
        lesson_type: str,
        teacher_context: str | None,
        reference_ppt_intents: Mapping[str, str],
        question_evidence: dict[str, object],
        assessment_evidence: dict[str, object],
        preparation_preferences: dict[str, object],
        selected_material_link_ids: tuple[str, ...] | None = None,
        selected_exercise_candidate_ids: tuple[str, ...] | None = None,
        question_selection: dict[str, object] | None = None,
    ) -> tuple[ResourcePackVersion, bool]:
        with self._database.connect(immediate=True) as connection:
            existing = connection.execute(
                """
                SELECT *
                FROM resource_pack_versions
                WHERE request_token = ?
                """,
                (request_token,),
            ).fetchone()
            if existing is not None:
                if str(existing["request_hash"]) != request_hash:
                    raise TeachingPrepConflictError(
                        "request token was reused for a different resource pack"
                    )
                return _pack(existing), False
            local_payload, source_state = _capture_local_payload(
                connection,
                lesson_node_id=lesson_node_id,
                reference_ppt_intents=reference_ppt_intents,
                selected_material_link_ids=selected_material_link_ids,
                selected_exercise_candidate_ids=selected_exercise_candidate_ids,
            )
            missing = list(local_payload.pop("missing"))
            missing.extend(_evidence_missing("question", question_evidence))
            missing.extend(
                _evidence_missing("assessment", assessment_evidence)
            )
            payload: dict[str, object] = {
                "schema_version": 1,
                **local_payload,
                "classroom": {
                    "class_name": class_name,
                    "lesson_type": lesson_type,
                    "duration_minutes": local_payload["lesson"][
                        "duration_minutes"
                    ],
                    "teacher_context": teacher_context,
                },
                "evidence": {
                    "question": question_evidence,
                    "assessment": assessment_evidence,
                },
                "preparation_preferences": preparation_preferences,
                "question_selection": question_selection or {},
                "selection": {
                    "material_link_ids": [
                        str(item["link_id"])
                        for item in local_payload["materials"]
                    ],
                    "exercise_candidate_ids": [
                        str(item["candidate_id"])
                        for item in local_payload["exercises"]
                    ],
                },
                "missing_and_uncertain": missing,
            }
            payload_json = _canonical_json(payload)
            pack_sha = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
            version_number = int(
                connection.execute(
                    """
                    SELECT COALESCE(MAX(version_number), 0) + 1
                    FROM resource_pack_versions
                    WHERE lesson_node_id = ?
                    """,
                    (lesson_node_id,),
                ).fetchone()[0]
            )
            pack_id = uuid4().hex
            connection.execute(
                """
                INSERT INTO resource_pack_versions (
                    id,
                    request_token,
                    request_hash,
                    lesson_node_id,
                    version_number,
                    source_state_sha256,
                    pack_sha256,
                    payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pack_id,
                    request_token,
                    request_hash,
                    lesson_node_id,
                    version_number,
                    source_state,
                    pack_sha,
                    payload_json,
                ),
            )
            row = connection.execute(
                "SELECT * FROM resource_pack_versions WHERE id = ?",
                (pack_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("resource pack could not be loaded")
        return _pack(row), True

    def clone_for_class(
        self,
        *,
        request_token: str,
        request_hash: str,
        base_pack: ResourcePackVersion,
        class_name: str,
        teacher_context: str | None,
        assessment_evidence: dict[str, object],
        prior_reviews: list[dict[str, object]],
    ) -> tuple[ResourcePackVersion, bool]:
        with self._database.connect(immediate=True) as connection:
            existing = connection.execute(
                """
                SELECT * FROM resource_pack_versions
                WHERE request_token = ?
                """,
                (request_token,),
            ).fetchone()
            if existing is not None:
                if str(existing["request_hash"]) != request_hash:
                    raise TeachingPrepConflictError(
                        "request token was reused for a different resource pack"
                    )
                return _pack(existing), False
            payload = json.loads(_canonical_json(base_pack.payload))
            classroom = dict(payload.get("classroom") or {})
            classroom["class_name"] = class_name
            classroom["teacher_context"] = teacher_context
            payload["classroom"] = classroom
            evidence = dict(payload.get("evidence") or {})
            evidence["assessment"] = assessment_evidence
            payload["evidence"] = evidence
            payload["derivation"] = {
                "base_resource_pack_id": base_pack.id,
                "base_resource_pack_sha256": base_pack.pack_sha256,
                "kind": "class_variant",
            }
            payload["prior_reviews"] = prior_reviews
            missing = [
                item
                for item in list(payload.get("missing_and_uncertain") or [])
                if not (
                    isinstance(item, Mapping)
                    and (
                        item.get("source") == "assessment"
                        or str(item.get("code") or "").startswith(
                            "assessment_"
                        )
                    )
                )
            ]
            missing.extend(_evidence_missing("assessment", assessment_evidence))
            payload["missing_and_uncertain"] = missing
            payload_json = _canonical_json(payload)
            pack_sha = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
            version_number = int(
                connection.execute(
                    """
                    SELECT COALESCE(MAX(version_number), 0) + 1
                    FROM resource_pack_versions
                    WHERE lesson_node_id = ?
                    """,
                    (base_pack.lesson_node_id,),
                ).fetchone()[0]
            )
            pack_id = uuid4().hex
            connection.execute(
                """
                INSERT INTO resource_pack_versions (
                    id, request_token, request_hash, lesson_node_id,
                    version_number, source_state_sha256, pack_sha256,
                    payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pack_id,
                    request_token,
                    request_hash,
                    base_pack.lesson_node_id,
                    version_number,
                    base_pack.source_state_sha256,
                    pack_sha,
                    payload_json,
                ),
            )
            row = connection.execute(
                "SELECT * FROM resource_pack_versions WHERE id = ?",
                (pack_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("class resource pack could not be loaded")
        return _pack(row), True

    def get(self, pack_id: str) -> ResourcePackVersion:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM resource_pack_versions WHERE id = ?",
                (pack_id,),
            ).fetchone()
        if row is None:
            raise TeachingPrepNotFoundError(
                "resource pack version was not found"
            )
        return _pack(row)

    def preflight_selection(
        self,
        *,
        lesson_node_id: str,
        reference_ppt_intents: Mapping[str, str],
        selected_material_link_ids: tuple[str, ...] | None,
        selected_exercise_candidate_ids: tuple[str, ...] | None,
    ) -> dict[str, object]:
        with self._database.connect() as connection:
            payload, source_state = _capture_local_payload(
                connection,
                lesson_node_id=lesson_node_id,
                reference_ppt_intents=reference_ppt_intents,
                selected_material_link_ids=selected_material_link_ids,
                selected_exercise_candidate_ids=selected_exercise_candidate_ids,
            )
        return {
            "lesson_node_id": lesson_node_id,
            "source_state_sha256": source_state,
            "material_link_ids": [
                str(item["link_id"]) for item in payload["materials"]
            ],
            "exercise_candidate_ids": [
                str(item["candidate_id"]) for item in payload["exercises"]
            ],
            "missing_and_uncertain": list(payload["missing"]),
            "ready_to_freeze": True,
        }

    def list_for_lesson(
        self,
        lesson_node_id: str,
    ) -> tuple[ResourcePackVersion, ...]:
        with self._database.connect() as connection:
            if connection.execute(
                "SELECT 1 FROM lesson_nodes WHERE id = ?",
                (lesson_node_id,),
            ).fetchone() is None:
                raise TeachingPrepNotFoundError(
                    "lesson node was not found"
                )
            rows = connection.execute(
                """
                SELECT *
                FROM resource_pack_versions
                WHERE lesson_node_id = ?
                ORDER BY version_number DESC
                """,
                (lesson_node_id,),
            ).fetchall()
        return tuple(_pack(row) for row in rows)

    def status(self, lesson_node_id: str) -> dict[str, object]:
        with self._database.connect() as connection:
            latest = connection.execute(
                """
                SELECT *
                FROM resource_pack_versions
                WHERE lesson_node_id = ?
                ORDER BY version_number DESC
                LIMIT 1
                """,
                (lesson_node_id,),
            ).fetchone()
            try:
                _local, current_source_state = _capture_local_payload(
                    connection,
                    lesson_node_id=lesson_node_id,
                    reference_ppt_intents=_reference_intents_from_pack(latest),
                    require_reference_intents=False,
                    selected_material_link_ids=_selected_link_ids(latest),
                    selected_exercise_candidate_ids=_selected_exercise_ids(latest),
                )
            except (
                TeachingPrepNotFoundError,
                TeachingPrepValidationError,
            ):
                # 旧快照可能引用已删除的资料或候选题；状态查询只读，按“来源已变化”处理
                current_source_state = None
            material_versions_changed = bool(
                latest is not None
                and _material_source_change_flags(
                    connection,
                    (str(latest["id"]),),
                ).get(str(latest["id"]), False)
            )
        return {
            "lesson_node_id": lesson_node_id,
            "has_pack": latest is not None,
            "latest_version_number": (
                int(latest["version_number"]) if latest is not None else None
            ),
            "latest_pack_id": (
                str(latest["id"]) if latest is not None else None
            ),
            "local_sources_changed": (
                latest is not None
                and (
                    str(latest["source_state_sha256"])
                    != current_source_state
                    or material_versions_changed
                )
            ),
        }

    def source_status(self, pack_id: str) -> dict[str, object]:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM resource_pack_versions WHERE id = ?",
                (pack_id,),
            ).fetchone()
            if row is None:
                raise TeachingPrepNotFoundError(
                    "resource pack version was not found"
                )
            try:
                _local, current_source_state = _capture_local_payload(
                    connection,
                    lesson_node_id=str(row["lesson_node_id"]),
                    reference_ppt_intents=_reference_intents_from_pack(row),
                    require_reference_intents=False,
                    selected_material_link_ids=_selected_link_ids(row),
                    selected_exercise_candidate_ids=_selected_exercise_ids(row),
                )
            except (
                TeachingPrepNotFoundError,
                TeachingPrepValidationError,
            ):
                current_source_state = None
            material_versions_changed = _material_source_change_flags(
                connection,
                (pack_id,),
            ).get(pack_id, False)
        frozen = str(row["source_state_sha256"])
        return {
            "resource_pack_id": pack_id,
            "frozen_source_state_sha256": frozen,
            "current_source_state_sha256": current_source_state,
            "sources_changed": (
                current_source_state != frozen or material_versions_changed
            ),
        }

    def source_change_flags(
        self,
        pack_ids: tuple[str, ...],
    ) -> dict[str, bool]:
        if not pack_ids:
            return {}
        with self._database.connect() as connection:
            return _material_source_change_flags(connection, pack_ids)


def _material_source_change_flags(
    connection: sqlite3.Connection,
    pack_ids: tuple[str, ...],
) -> dict[str, bool]:
    if not pack_ids:
        return {}
    placeholders = ",".join("?" for _item in pack_ids)
    rows = connection.execute(
        f"""
        SELECT pack.id,
               EXISTS (
                   SELECT 1
                   FROM json_each(pack.payload_json, '$.materials') AS frozen
                   LEFT JOIN material_versions AS frozen_version
                     ON frozen_version.id = json_extract(
                         frozen.value, '$.material_version_id'
                     )
                   LEFT JOIN material_sources AS source
                     ON source.id = frozen_version.source_id
                   WHERE frozen_version.id IS NULL
                      OR source.id IS NULL
                      OR source.archived_at IS NOT NULL
                      OR EXISTS (
                          SELECT 1
                          FROM material_versions AS newer
                          WHERE newer.source_id = frozen_version.source_id
                            AND newer.id <> frozen_version.id
                            AND (
                                newer.created_at > frozen_version.created_at
                                OR (
                                    newer.created_at = frozen_version.created_at
                                    AND newer.rowid > frozen_version.rowid
                                )
                            )
                      )
               ) AS sources_changed
        FROM resource_pack_versions AS pack
        WHERE pack.id IN ({placeholders})
        """,
        pack_ids,
    ).fetchall()
    return {
        str(row["id"]): bool(row["sources_changed"])
        for row in rows
    }


def _capture_local_payload(
    connection: sqlite3.Connection,
    *,
    lesson_node_id: str,
    reference_ppt_intents: Mapping[str, str],
    require_reference_intents: bool = True,
    selected_material_link_ids: tuple[str, ...] | None = None,
    selected_exercise_candidate_ids: tuple[str, ...] | None = None,
) -> tuple[dict[str, object], str]:
    lesson = connection.execute(
        """
        SELECT
            lesson.*,
            curriculum.title AS curriculum_title,
            curriculum.grade_level,
            curriculum.volume,
            curriculum.publisher,
            curriculum.edition_label,
            curriculum.revision AS curriculum_revision
        FROM lesson_nodes AS lesson
        JOIN curriculum_editions AS curriculum
            ON curriculum.id = lesson.curriculum_id
        WHERE lesson.id = ?
          AND lesson.node_type = 'lesson'
          AND lesson.is_active = 1
        """,
        (lesson_node_id,),
    ).fetchone()
    if lesson is None:
        raise TeachingPrepNotFoundError(
            "active lesson node was not found"
        )
    link_rows = connection.execute(
        """
        SELECT
            link.*,
            version.content_sha256,
            version.created_at AS material_version_created_at,
            source.display_name AS material_name,
            source.material_type,
            CASE WHEN semester_material.is_daily_workbook = 1
                 THEN 'homework_workbook'
                 ELSE semester_material.material_role END AS material_role
        FROM lesson_material_links AS link
        JOIN material_versions AS version
            ON version.id = link.material_version_id
        JOIN material_sources AS source
            ON source.id = version.source_id
        LEFT JOIN teaching_semesters AS semester
            ON semester.curriculum_id = ?
        LEFT JOIN semester_material_records AS semester_material
            ON semester_material.semester_id = semester.id
           AND semester_material.material_source_id = source.id
           AND semester_material.is_active = 1
        WHERE link.lesson_node_id = ?
          AND link.is_active = 1
          AND link.confirmation_status = 'confirmed'
        ORDER BY link.sort_order, link.id
        """,
        (str(lesson["curriculum_id"]), lesson_node_id),
    ).fetchall()
    if selected_material_link_ids is not None:
        selected_links = set(selected_material_link_ids)
        available_links = {str(row["id"]) for row in link_rows}
        if selected_links - available_links:
            raise TeachingPrepValidationError(
                "selected material link is unavailable"
            )
        link_rows = [
            row for row in link_rows if str(row["id"]) in selected_links
        ]
    reference_link_ids = {
        str(row["id"])
        for row in link_rows
        if str(row["purpose"]) == "reference_ppt"
    }
    if set(reference_ppt_intents) - reference_link_ids:
        raise TeachingPrepValidationError(
            "reference PPT intent refers to an unavailable link"
        )
    if require_reference_intents and reference_link_ids - set(
        reference_ppt_intents
    ):
        raise TeachingPrepValidationError(
            "every reference PPT range requires a teacher intent"
        )
    materials: list[dict[str, object]] = []
    source_material_state: list[dict[str, object]] = []
    allowed_selected_units: set[tuple[str, int]] = set()
    for link in link_rows:
        link_id = str(link["id"])
        purpose = str(link["purpose"])
        units = connection.execute(
            """
            SELECT *
            FROM material_units
            WHERE material_version_id = ?
              AND unit_index BETWEEN ? AND ?
            ORDER BY unit_index
            """,
            (
                str(link["material_version_id"]),
                int(link["start_unit"]),
                int(link["end_unit"]),
            ),
        ).fetchall()
        unit_payload = [
            {
                "unit_id": str(unit["id"]),
                "unit_index": int(unit["unit_index"]),
                "unit_kind": str(unit["unit_kind"]),
                "title": (
                    str(unit["title"])
                    if unit["title"] is not None
                    else None
                ),
                "text": str(unit["extracted_text"]),
                "text_status": str(unit["text_status"]),
                "formula_review_required": bool(
                    unit["formula_review_required"]
                ),
                "object_summary": _json_object(
                    str(unit["object_summary_json"] or "{}")
                ),
                "preview_url": (
                    f"/api/teaching-prep/material-units/{unit['id']}/preview"
                ),
                "preview_sha256": str(unit["preview_sha256"]),
                "source_version_sha256": str(
                    unit["source_version_sha256"]
                ),
            }
            for unit in units
        ]
        material_item: dict[str, object] = {
            "link_id": link_id,
            "link_revision": int(link["revision"]),
            "purpose": purpose,
            "teacher_note": (
                str(link["teacher_note"])
                if link["teacher_note"] is not None
                else None
            ),
            "material_version_id": str(link["material_version_id"]),
            "material_name": str(link["material_name"]),
            "material_type": str(link["material_type"]),
            "semester_material_role": (
                str(link["material_role"])
                if link["material_role"] is not None
                else None
            ),
            "content_sha256": str(link["content_sha256"]),
            "version_created_at": str(
                link["material_version_created_at"]
            ),
            "start_unit": int(link["start_unit"]),
            "end_unit": int(link["end_unit"]),
            "units": unit_payload,
        }
        if purpose == "reference_ppt":
            material_item["teacher_intent"] = reference_ppt_intents.get(
                link_id,
                "keep",
            )
        materials.append(material_item)
        allowed_selected_units.update(
            (str(link["material_version_id"]), int(unit["unit_index"]))
            for unit in units
        )
        source_material_state.append(
            {
                "link_id": link_id,
                "link_revision": int(link["revision"]),
                "content_sha256": str(link["content_sha256"]),
                "semester_material_role": (
                    str(link["material_role"])
                    if link["material_role"] is not None
                    else None
                ),
                "units": [
                    {
                        "id": str(unit["id"]),
                        "title": (
                            str(unit["title"])
                            if unit["title"] is not None
                            else None
                        ),
                        "text": str(unit["extracted_text"]),
                        "text_status": str(unit["text_status"]),
                        "formula_review_required": bool(
                            unit["formula_review_required"]
                        ),
                        "object_summary": _source_object_summary(
                            _json_object(
                                str(unit["object_summary_json"] or "{}")
                            )
                        ),
                        "source_sha256": str(
                            unit["source_version_sha256"]
                        ),
                    }
                    for unit in units
                ],
            }
        )
    exercise_rows = connection.execute(
        """
        SELECT *
        FROM exercise_candidates
        WHERE lesson_node_id = ? AND is_active = 1
        ORDER BY created_at, id
        """,
        (lesson_node_id,),
    ).fetchall()
    if selected_exercise_candidate_ids is not None:
        selected_exercises = set(selected_exercise_candidate_ids)
        available_exercises = {str(row["id"]) for row in exercise_rows}
        if selected_exercises - available_exercises:
            raise TeachingPrepValidationError(
                "selected exercise candidate is unavailable"
            )
        exercise_rows = [
            row
            for row in exercise_rows
            if str(row["id"]) in selected_exercises
        ]
    exercises: list[dict[str, object]] = []
    source_exercise_state: list[dict[str, object]] = []
    missing: list[dict[str, object]] = []
    for candidate in exercise_rows:
        regions = connection.execute(
            """
            SELECT
                region.*,
                unit.material_version_id,
                unit.unit_index,
                source.display_name AS material_name,
                CASE WHEN semester_material.is_daily_workbook = 1
                     THEN 'homework_workbook'
                     ELSE semester_material.material_role END AS material_role
            FROM exercise_regions AS region
            JOIN material_units AS unit
                ON unit.id = region.material_unit_id
            JOIN material_versions AS version
                ON version.id = unit.material_version_id
            JOIN material_sources AS source
                ON source.id = version.source_id
            LEFT JOIN teaching_semesters AS semester
                ON semester.curriculum_id = ?
            LEFT JOIN semester_material_records AS semester_material
                ON semester_material.semester_id = semester.id
               AND semester_material.material_source_id = source.id
               AND semester_material.is_active = 1
            WHERE region.exercise_candidate_id = ?
            ORDER BY region.region_role DESC, region.sequence
            """,
            (str(lesson["curriculum_id"]), str(candidate["id"])),
        ).fetchall()
        if selected_material_link_ids is not None and any(
            (
                str(region["material_version_id"]),
                int(region["unit_index"]),
            )
            not in allowed_selected_units
            for region in regions
        ):
            raise TeachingPrepValidationError(
                "selected exercise depends on a material range outside the pack"
            )
        region_payload = [
            {
                "region_id": str(region["id"]),
                "role": str(region["region_role"]),
                "sequence": int(region["sequence"]),
                "material_unit_id": str(region["material_unit_id"]),
                "material_version_id": str(
                    region["material_version_id"]
                ),
                "material_name": str(region["material_name"]),
                "semester_material_role": (
                    str(region["material_role"])
                    if region["material_role"] is not None
                    else None
                ),
                "unit_index": int(region["unit_index"]),
                "crop": _json_object(str(region["crop_json"])),
                "source_version_sha256": str(
                    region["source_version_sha256"]
                ),
                "preview_url": (
                    f"/api/teaching-prep/exercise-regions/"
                    f"{region['id']}/preview"
                ),
            }
            for region in regions
        ]
        answer_status = str(candidate["answer_status"])
        item = {
            "candidate_id": str(candidate["id"]),
            "revision": int(candidate["revision"]),
            "question_number": (
                str(candidate["question_number"])
                if candidate["question_number"] is not None
                else None
            ),
            "content_label": (
                str(candidate["content_label"])
                if candidate["content_label"] is not None
                else None
            ),
            "difficulty": str(candidate["difficulty"]),
            "classroom_use": str(candidate["classroom_use"]),
            "estimated_minutes": (
                int(candidate["estimated_minutes"])
                if candidate["estimated_minutes"] is not None
                else None
            ),
            "teaching_focus": (
                str(candidate["teaching_focus"])
                if candidate["teaching_focus"] is not None
                else None
            ),
            "selection_status": str(candidate["selection_status"]),
            "answer_status": answer_status,
            "formal_answer_usable": answer_status == "teacher_verified",
            "question_regions": [
                region
                for region in region_payload
                if region["role"] == "question"
            ],
            "answer_regions": [
                region
                for region in region_payload
                if region["role"] == "answer"
            ],
        }
        exercises.append(item)
        source_exercise_state.append(
            {
                "candidate_id": str(candidate["id"]),
                "revision": int(candidate["revision"]),
                "regions": [
                    {
                        "id": str(region["id"]),
                        "source_sha256": str(
                            region["source_version_sha256"]
                        ),
                        "semester_material_role": (
                            str(region["material_role"])
                            if region["material_role"] is not None
                            else None
                        ),
                    }
                    for region in regions
                ],
            }
        )
    if not any(item["purpose"] == "textbook" for item in materials):
        missing.append({"code": "textbook_material_missing"})
    for material in materials:
        for unit in material["units"]:
            if unit["formula_review_required"]:
                missing.append(
                    {
                        "code": "formula_text_requires_review",
                        "material_version_id": material[
                            "material_version_id"
                        ],
                        "unit_index": unit["unit_index"],
                    }
                )
    lesson_payload = {
        "lesson_node_id": str(lesson["id"]),
        "title": str(lesson["title"]),
        "revision": int(lesson["revision"]),
        "duration_minutes": (
            int(lesson["duration_minutes"])
            if lesson["duration_minutes"] is not None
            else None
        ),
        "curriculum": {
            "curriculum_id": str(lesson["curriculum_id"]),
            "title": str(lesson["curriculum_title"]),
            "grade_level": int(lesson["grade_level"]),
            "volume": str(lesson["volume"]),
            "publisher": (
                str(lesson["publisher"])
                if lesson["publisher"] is not None
                else None
            ),
            "edition_label": (
                str(lesson["edition_label"])
                if lesson["edition_label"] is not None
                else None
            ),
            "revision": int(lesson["curriculum_revision"]),
        },
    }
    source_state = _digest(
        {
            "lesson_revision": int(lesson["revision"]),
            "curriculum_revision": int(lesson["curriculum_revision"]),
            "materials": source_material_state,
            "exercises": source_exercise_state,
        }
    )
    return (
        {
            "lesson": lesson_payload,
            "materials": materials,
            "exercises": exercises,
            "missing": missing,
        },
        source_state,
    )


def _source_object_summary(
    summary: Mapping[str, object],
) -> dict[str, object]:
    """Exclude derived preview state from frozen source-change detection."""
    derived_keys = {"height", "rendered_source_sha256", "width"}
    return {
        str(key): value
        for key, value in summary.items()
        if not str(key).startswith("preview_") and str(key) not in derived_keys
    }


def _evidence_missing(
    prefix: str,
    snapshot: dict[str, object],
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    raw = snapshot.get("missing", [])
    if not isinstance(raw, list):
        return result
    for item in raw:
        if isinstance(item, dict):
            result.append(
                {
                    **item,
                    "source": prefix,
                }
            )
        else:
            result.append({"code": str(item), "source": prefix})
    return result


def _reference_intents_from_pack(
    row: sqlite3.Row | None,
) -> dict[str, str]:
    if row is None:
        return {}
    pack = _pack(row)
    materials = pack.payload.get("materials", [])
    if not isinstance(materials, list):
        return {}
    return {
        str(item["link_id"]): str(item.get("teacher_intent") or "keep")
        for item in materials
        if isinstance(item, dict)
        and item.get("purpose") == "reference_ppt"
        and item.get("link_id")
    }


def _selected_link_ids(
    row: sqlite3.Row | None,
) -> tuple[str, ...] | None:
    if row is None:
        return None
    selection = _pack(row).payload.get("selection")
    if not isinstance(selection, dict):
        return None
    values = selection.get("material_link_ids")
    if not isinstance(values, list):
        return None
    return tuple(str(item) for item in values)


def _selected_exercise_ids(
    row: sqlite3.Row | None,
) -> tuple[str, ...] | None:
    if row is None:
        return None
    selection = _pack(row).payload.get("selection")
    if not isinstance(selection, dict):
        return None
    values = selection.get("exercise_candidate_ids")
    if not isinstance(values, list):
        return None
    return tuple(str(item) for item in values)


def _pack(row: sqlite3.Row) -> ResourcePackVersion:
    try:
        payload = json.loads(str(row["payload_json"]))
    except json.JSONDecodeError as exc:
        raise RuntimeError("stored resource pack JSON is invalid") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("stored resource pack payload is invalid")
    return ResourcePackVersion(
        id=str(row["id"]),
        lesson_node_id=str(row["lesson_node_id"]),
        version_number=int(row["version_number"]),
        source_state_sha256=str(row["source_state_sha256"]),
        pack_sha256=str(row["pack_sha256"]),
        payload=payload,
        created_at=str(row["created_at"]),
    )


def _json_object(value: str) -> dict[str, object]:
    try:
        candidate = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return candidate if isinstance(candidate, dict) else {}


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


__all__ = ["ResourcePackRepository"]
