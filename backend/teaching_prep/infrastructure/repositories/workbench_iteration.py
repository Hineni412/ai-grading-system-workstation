from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping, Sequence
from uuid import uuid4

from backend.teaching_prep.domain.errors import (
    TeachingPrepConflictError,
    TeachingPrepNotFoundError,
    TeachingPrepStateError,
)
from backend.teaching_prep.domain.models import (
    ExerciseSuggestion,
    ExerciseSuggestionRun,
    ReferenceSelectionDraft,
    ReferenceSelectionSnapshot,
)
from backend.teaching_prep.infrastructure.database import TeachingPrepDatabase


class WorkbenchIterationRepository:
    """Persistence seam for the A-I1 workbench version and operation state."""

    def __init__(self, database: TeachingPrepDatabase) -> None:
        self._database = database

    def lesson_preparation_statuses(
        self,
        semester_id: str,
    ) -> tuple[dict[str, object], ...]:
        with self._database.connect() as connection:
            semester = connection.execute(
                "SELECT curriculum_id FROM teaching_semesters WHERE id = ?",
                (semester_id,),
            ).fetchone()
            if semester is None:
                raise TeachingPrepNotFoundError("semester was not found")
            rows = connection.execute(
                """
                SELECT
                    lesson.id,
                    lesson.title,
                    lesson.sort_order,
                    lesson.duration_minutes,
                    COALESCE(progress.status, 'not_started') AS manual_progress,
                    progress.revision AS progress_revision,
                    (SELECT COUNT(*) FROM lesson_material_links link
                     WHERE link.lesson_node_id = lesson.id
                       AND link.is_active = 1
                       AND link.confirmation_status = 'confirmed') AS material_count,
                    (SELECT id FROM resource_pack_versions pack
                     WHERE pack.lesson_node_id = lesson.id
                     ORDER BY pack.version_number DESC LIMIT 1) AS resource_pack_id,
                    (SELECT id FROM lesson_draft_versions draft
                     WHERE draft.resource_pack_id IN (
                         SELECT id FROM resource_pack_versions pack
                         WHERE pack.lesson_node_id = lesson.id
                     ) AND draft.status = 'confirmed'
                     ORDER BY draft.created_at DESC LIMIT 1) AS lesson_draft_id,
                    (SELECT id FROM slide_plan_versions plan
                     WHERE plan.resource_pack_id IN (
                         SELECT id FROM resource_pack_versions pack
                         WHERE pack.lesson_node_id = lesson.id
                     ) AND plan.status = 'approved'
                     ORDER BY plan.created_at DESC LIMIT 1) AS slide_plan_id,
                    current.pptx_version_id AS current_pptx_version_id,
                    current.revision AS current_pptx_revision,
                    (SELECT id FROM up_class_packages package
                     WHERE package.lesson_node_id = lesson.id
                       AND package.status = 'complete'
                       AND package.id = (
                           SELECT selection.package_id
                           FROM up_class_package_selections selection
                           WHERE selection.lesson_node_id = package.lesson_node_id
                             AND selection.class_scope_key = package.class_scope_key
                           ORDER BY selection.rowid DESC
                           LIMIT 1
                       )
                     ORDER BY package.created_at DESC LIMIT 1) AS up_class_package_id
                FROM lesson_nodes lesson
                LEFT JOIN semester_lesson_progress progress
                    ON progress.semester_id = ?
                   AND progress.lesson_node_id = lesson.id
                LEFT JOIN lesson_current_pptx_versions current
                    ON current.lesson_node_id = lesson.id
                WHERE lesson.curriculum_id = ?
                  AND lesson.node_type = 'lesson'
                  AND lesson.is_active = 1
                ORDER BY lesson.sort_order, lesson.id
                """,
                (semester_id, str(semester["curriculum_id"])),
            ).fetchall()
        return tuple(_preparation_status(row) for row in rows)

    def mark_interrupted(self) -> int:
        with self._database.connect(immediate=True) as connection:
            cursor = connection.execute(
                """
                UPDATE exercise_suggestion_runs
                SET status = 'result_unknown',
                    error_code = 'application_restarted',
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    finished_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE status = 'running'
                """
            )
            return int(cursor.rowcount)

    def selection_catalog(
        self,
        lesson_node_id: str,
    ) -> tuple[dict[str, object], str]:
        with self._database.connect() as connection:
            return self._selection_catalog(connection, lesson_node_id)

    def get_reference_draft(
        self,
        lesson_node_id: str,
    ) -> ReferenceSelectionDraft | None:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM reference_selection_drafts WHERE lesson_node_id = ?",
                (lesson_node_id,),
            ).fetchone()
        return _reference_draft(row) if row is not None else None

    def save_reference_draft(
        self,
        *,
        lesson_node_id: str,
        expected_revision: int | None,
        payload: dict[str, object],
        source_state_sha256: str,
    ) -> ReferenceSelectionDraft:
        with self._database.connect(immediate=True) as connection:
            _catalog, current_source = self._selection_catalog(
                connection, lesson_node_id
            )
            if current_source != source_state_sha256:
                raise TeachingPrepConflictError(
                    "reference sources changed; review the selection again"
                )
            row = connection.execute(
                "SELECT * FROM reference_selection_drafts WHERE lesson_node_id = ?",
                (lesson_node_id,),
            ).fetchone()
            if row is None:
                if expected_revision is not None:
                    raise TeachingPrepConflictError(
                        "reference selection draft no longer exists"
                    )
                connection.execute(
                    """
                    INSERT INTO reference_selection_drafts (
                        lesson_node_id, payload_json, source_state_sha256
                    ) VALUES (?, ?, ?)
                    """,
                    (lesson_node_id, _json(payload), source_state_sha256),
                )
            else:
                if expected_revision != int(row["revision"]):
                    raise TeachingPrepConflictError(
                        "reference selection draft changed; refresh before saving"
                    )
                connection.execute(
                    """
                    UPDATE reference_selection_drafts
                    SET payload_json = ?, source_state_sha256 = ?,
                        revision = revision + 1,
                        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    WHERE lesson_node_id = ? AND revision = ?
                    """,
                    (
                        _json(payload),
                        source_state_sha256,
                        lesson_node_id,
                        expected_revision,
                    ),
                )
            saved = connection.execute(
                "SELECT * FROM reference_selection_drafts WHERE lesson_node_id = ?",
                (lesson_node_id,),
            ).fetchone()
        if saved is None:
            raise RuntimeError("reference selection draft was not saved")
        return _reference_draft(saved)

    def freeze_reference_snapshot(
        self,
        *,
        request_token: str,
        request_hash: str,
        lesson_node_id: str,
        source_state_sha256: str,
        payload: dict[str, object],
    ) -> tuple[ReferenceSelectionSnapshot, bool]:
        with self._database.connect(immediate=True) as connection:
            existing = connection.execute(
                "SELECT * FROM reference_selection_snapshots WHERE request_token = ?",
                (request_token,),
            ).fetchone()
            if existing is not None:
                if str(existing["request_hash"]) != request_hash:
                    raise TeachingPrepConflictError(
                        "request token was reused for a different reference snapshot"
                    )
                return _reference_snapshot(existing), False
            _catalog, current_source = self._selection_catalog(
                connection, lesson_node_id
            )
            if current_source != source_state_sha256:
                raise TeachingPrepConflictError(
                    "reference sources changed before the snapshot was frozen"
                )
            snapshot_id = uuid4().hex
            connection.execute(
                """
                INSERT INTO reference_selection_snapshots (
                    id, lesson_node_id, request_token, request_hash,
                    source_state_sha256, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    lesson_node_id,
                    request_token,
                    request_hash,
                    source_state_sha256,
                    _json(payload),
                ),
            )
            row = connection.execute(
                "SELECT * FROM reference_selection_snapshots WHERE id = ?",
                (snapshot_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("reference snapshot was not saved")
        return _reference_snapshot(row), True

    def get_reference_snapshot(
        self,
        snapshot_id: str,
    ) -> ReferenceSelectionSnapshot:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM reference_selection_snapshots WHERE id = ?",
                (snapshot_id,),
            ).fetchone()
        if row is None:
            raise TeachingPrepNotFoundError(
                "reference selection snapshot was not found"
            )
        return _reference_snapshot(row)

    def begin_suggestion_run(
        self,
        *,
        snapshot_id: str,
        operation_id: str,
        request_hash: str,
    ) -> tuple[ExerciseSuggestionRun, bool]:
        with self._database.connect(immediate=True) as connection:
            if connection.execute(
                "SELECT 1 FROM reference_selection_snapshots WHERE id = ?",
                (snapshot_id,),
            ).fetchone() is None:
                raise TeachingPrepNotFoundError(
                    "reference selection snapshot was not found"
                )
            existing = connection.execute(
                "SELECT * FROM exercise_suggestion_runs WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if existing is not None:
                if str(existing["request_hash"]) != request_hash:
                    raise TeachingPrepConflictError(
                        "operation ID was reused for different exercise input"
                    )
                return _suggestion_run(existing), False
            semantic = connection.execute(
                """
                SELECT * FROM exercise_suggestion_runs
                WHERE request_hash = ?
                  AND status IN ('running', 'succeeded')
                ORDER BY created_at DESC LIMIT 1
                """,
                (request_hash,),
            ).fetchone()
            if semantic is not None:
                return _suggestion_run(semantic), False
            run_id = uuid4().hex
            connection.execute(
                """
                INSERT INTO exercise_suggestion_runs (
                    id, snapshot_id, operation_id, request_hash
                ) VALUES (?, ?, ?, ?)
                """,
                (run_id, snapshot_id, operation_id, request_hash),
            )
            row = connection.execute(
                "SELECT * FROM exercise_suggestion_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("exercise suggestion run was not created")
        return _suggestion_run(row), True

    def mark_suggestion_call_started(self, run_id: str) -> None:
        with self._database.connect(immediate=True) as connection:
            updated = connection.execute(
                """
                UPDATE exercise_suggestion_runs
                SET model_call_count = 1,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ? AND status = 'running' AND model_call_count = 0
                """,
                (run_id,),
            ).rowcount
            if updated != 1:
                raise TeachingPrepStateError(
                    "exercise suggestion call is no longer available"
                )

    def finish_suggestion_run(
        self,
        *,
        run_id: str,
        lesson_node_id: str,
        source_state_sha256: str,
        suggestions: Sequence[dict[str, object]],
    ) -> ExerciseSuggestionRun:
        with self._database.connect(immediate=True) as connection:
            run = connection.execute(
                "SELECT * FROM exercise_suggestion_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            if run is None:
                raise TeachingPrepNotFoundError(
                    "exercise suggestion run was not found"
                )
            if str(run["status"]) != "running":
                raise TeachingPrepConflictError(
                    "exercise suggestion run is no longer active"
                )
            for payload in suggestions:
                connection.execute(
                    """
                    INSERT INTO exercise_suggestions (
                        id, run_id, lesson_node_id, source_state_sha256,
                        original_payload_json
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        uuid4().hex,
                        run_id,
                        lesson_node_id,
                        source_state_sha256,
                        _json(payload),
                    ),
                )
            connection.execute(
                """
                UPDATE exercise_suggestion_runs
                SET status = 'succeeded',
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    finished_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ? AND status = 'running'
                """,
                (run_id,),
            )
            row = connection.execute(
                "SELECT * FROM exercise_suggestion_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("exercise suggestion run was not loaded")
        return _suggestion_run(row)

    def fail_suggestion_run(self, run_id: str, error_code: str) -> None:
        with self._database.connect(immediate=True) as connection:
            connection.execute(
                """
                UPDATE exercise_suggestion_runs
                SET status = 'failed', error_code = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    finished_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ? AND status = 'running'
                """,
                (error_code, run_id),
            )

    def cancel_suggestion_run(self, run_id: str) -> ExerciseSuggestionRun:
        with self._database.connect(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM exercise_suggestion_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                raise TeachingPrepNotFoundError(
                    "exercise suggestion run was not found"
                )
            if str(row["status"]) == "running":
                connection.execute(
                    """
                    UPDATE exercise_suggestion_runs
                    SET status = 'cancelled',
                        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                        finished_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    WHERE id = ? AND status = 'running'
                    """,
                    (run_id,),
                )
            saved = connection.execute(
                "SELECT * FROM exercise_suggestion_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
        if saved is None:
            raise RuntimeError("exercise suggestion run was not loaded")
        return _suggestion_run(saved)

    def get_suggestion_run(
        self,
        run_id: str,
    ) -> tuple[ExerciseSuggestionRun, tuple[ExerciseSuggestion, ...]]:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM exercise_suggestion_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                raise TeachingPrepNotFoundError(
                    "exercise suggestion run was not found"
                )
            suggestions = connection.execute(
                """
                SELECT * FROM exercise_suggestions
                WHERE run_id = ? ORDER BY created_at, id
                """,
                (run_id,),
            ).fetchall()
        return _suggestion_run(row), tuple(
            _suggestion(item) for item in suggestions
        )

    def get_suggestion_run_for_suggestion(
        self,
        suggestion_id: str,
    ) -> tuple[ExerciseSuggestionRun, tuple[ExerciseSuggestion, ...]]:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT run_id FROM exercise_suggestions WHERE id = ?",
                (suggestion_id,),
            ).fetchone()
        if row is None:
            raise TeachingPrepNotFoundError(
                "exercise suggestion was not found"
            )
        return self.get_suggestion_run(str(row["run_id"]))

    def update_suggestion(
        self,
        suggestion_id: str,
        *,
        expected_revision: int,
        decision: str,
        teacher_payload: dict[str, object] | None,
        rejection_reason: str | None,
        exercise_candidate_id: str | None,
    ) -> ExerciseSuggestion:
        with self._database.connect(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM exercise_suggestions WHERE id = ?",
                (suggestion_id,),
            ).fetchone()
            if row is None:
                raise TeachingPrepNotFoundError(
                    "exercise suggestion was not found"
                )
            if int(row["revision"]) != expected_revision:
                raise TeachingPrepConflictError(
                    "exercise suggestion changed; refresh before saving"
                )
            updated = connection.execute(
                """
                UPDATE exercise_suggestions
                SET decision = ?, teacher_payload_json = ?,
                    rejection_reason = ?, exercise_candidate_id = ?,
                    revision = revision + 1,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ? AND revision = ?
                """,
                (
                    decision,
                    _json(teacher_payload) if teacher_payload is not None else None,
                    rejection_reason,
                    exercise_candidate_id,
                    suggestion_id,
                    expected_revision,
                ),
            ).rowcount
            if updated != 1:
                raise TeachingPrepConflictError(
                    "exercise suggestion changed; refresh before saving"
                )
            saved = connection.execute(
                "SELECT * FROM exercise_suggestions WHERE id = ?",
                (suggestion_id,),
            ).fetchone()
        if saved is None:
            raise RuntimeError("exercise suggestion was not loaded")
        return _suggestion(saved)

    def _selection_catalog(
        self,
        connection: sqlite3.Connection,
        lesson_node_id: str,
    ) -> tuple[dict[str, object], str]:
        lesson = connection.execute(
            """
            SELECT id, curriculum_id, title, duration_minutes, revision
            FROM lesson_nodes
            WHERE id = ? AND node_type = 'lesson' AND is_active = 1
            """,
            (lesson_node_id,),
        ).fetchone()
        if lesson is None:
            raise TeachingPrepNotFoundError("active lesson node was not found")
        links = connection.execute(
            """
            SELECT link.id, link.revision, link.purpose, link.start_unit,
                   link.end_unit, link.material_version_id,
                   version.content_sha256, source.display_name,
                   source.material_type
            FROM lesson_material_links link
            JOIN material_versions version ON version.id = link.material_version_id
            JOIN material_sources source ON source.id = version.source_id
            WHERE link.lesson_node_id = ?
              AND link.is_active = 1
              AND link.confirmation_status = 'confirmed'
            ORDER BY link.sort_order, link.id
            """,
            (lesson_node_id,),
        ).fetchall()
        link_items: list[dict[str, object]] = []
        state_items: list[dict[str, object]] = []
        for link in links:
            units = connection.execute(
                """
                SELECT id, unit_index, unit_kind, title, preview_sha256,
                       source_version_sha256, text_status,
                       formula_review_required
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
            unit_items = [
                {
                    "unit_id": str(unit["id"]),
                    "unit_index": int(unit["unit_index"]),
                    "unit_kind": str(unit["unit_kind"]),
                    "title": str(unit["title"]) if unit["title"] else None,
                    "preview_url": (
                        f"/api/teaching-prep/material-units/{unit['id']}/preview"
                    ),
                    "text_status": str(unit["text_status"]),
                    "formula_review_required": bool(
                        unit["formula_review_required"]
                    ),
                }
                for unit in units
            ]
            item = {
                "link_id": str(link["id"]),
                "link_revision": int(link["revision"]),
                "purpose": str(link["purpose"]),
                "material_version_id": str(link["material_version_id"]),
                "material_name": str(link["display_name"]),
                "material_type": str(link["material_type"]),
                "content_sha256": str(link["content_sha256"]),
                "start_unit": int(link["start_unit"]),
                "end_unit": int(link["end_unit"]),
                "units": unit_items,
            }
            link_items.append(item)
            state_items.append(
                {
                    "link_id": item["link_id"],
                    "link_revision": item["link_revision"],
                    "content_sha256": item["content_sha256"],
                    "unit_state": [
                        {
                            "unit_id": str(unit["id"]),
                            "preview_sha256": str(unit["preview_sha256"]),
                            "source_version_sha256": str(
                                unit["source_version_sha256"]
                            ),
                        }
                        for unit in units
                    ],
                }
            )
        catalog = {
            "lesson": {
                "id": str(lesson["id"]),
                "title": str(lesson["title"]),
                "duration_minutes": int(lesson["duration_minutes"] or 45),
                "revision": int(lesson["revision"]),
            },
            "material_links": link_items,
        }
        source_state = {
            "lesson_id": str(lesson["id"]),
            "lesson_revision": int(lesson["revision"]),
            "links": state_items,
        }
        return catalog, _sha(source_state)


def _preparation_status(row: sqlite3.Row) -> dict[str, object]:
    manual = str(row["manual_progress"])
    blockers: list[str] = []
    stage = "select"
    next_action = "核对资料"
    if int(row["material_count"]) == 0:
        blockers.append("尚未确认本节资料")
    else:
        stage = "materials"
    if row["resource_pack_id"] is not None:
        stage = "plan"
        next_action = "确定课堂方案"
    if row["lesson_draft_id"] is not None:
        stage = "slides"
        next_action = "审核课件计划"
    if row["slide_plan_id"] is not None:
        next_action = "生成可信课件"
    if row["current_pptx_version_id"] is not None:
        stage = "package"
        next_action = "生成或核对上课包"
    if row["up_class_package_id"] is not None:
        next_action = "查看当前上课包"
    return {
        "lesson_node_id": str(row["id"]),
        "title": str(row["title"]),
        "sort_order": int(row["sort_order"]),
        "duration_minutes": int(row["duration_minutes"] or 45),
        "manual_progress": manual,
        "manual_progress_revision": (
            int(row["progress_revision"])
            if row["progress_revision"] is not None
            else None
        ),
        "preparation_stage": stage,
        "next_action": next_action,
        "blockers": blockers,
        "latest": {
            "resource_pack_id": row["resource_pack_id"],
            "lesson_draft_id": row["lesson_draft_id"],
            "slide_plan_id": row["slide_plan_id"],
            "pptx_version_id": row["current_pptx_version_id"],
            "pptx_revision": row["current_pptx_revision"],
            "up_class_package_id": row["up_class_package_id"],
        },
    }


def _reference_draft(row: sqlite3.Row) -> ReferenceSelectionDraft:
    return ReferenceSelectionDraft(
        lesson_node_id=str(row["lesson_node_id"]),
        payload=_object(row["payload_json"]),
        source_state_sha256=str(row["source_state_sha256"]),
        revision=int(row["revision"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _reference_snapshot(row: sqlite3.Row) -> ReferenceSelectionSnapshot:
    return ReferenceSelectionSnapshot(
        id=str(row["id"]),
        lesson_node_id=str(row["lesson_node_id"]),
        source_state_sha256=str(row["source_state_sha256"]),
        payload=_object(row["payload_json"]),
        created_at=str(row["created_at"]),
    )


def _suggestion_run(row: sqlite3.Row) -> ExerciseSuggestionRun:
    return ExerciseSuggestionRun(
        id=str(row["id"]),
        snapshot_id=str(row["snapshot_id"]),
        operation_id=str(row["operation_id"]),
        status=str(row["status"]),
        error_code=str(row["error_code"]) if row["error_code"] else None,
        model_call_count=int(row["model_call_count"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        finished_at=str(row["finished_at"]) if row["finished_at"] else None,
    )


def _suggestion(row: sqlite3.Row) -> ExerciseSuggestion:
    return ExerciseSuggestion(
        id=str(row["id"]),
        run_id=str(row["run_id"]),
        lesson_node_id=str(row["lesson_node_id"]),
        source_state_sha256=str(row["source_state_sha256"]),
        decision=str(row["decision"]),
        original_payload=_object(row["original_payload_json"]),
        teacher_payload=(
            _object(row["teacher_payload_json"])
            if row["teacher_payload_json"] is not None
            else None
        ),
        rejection_reason=(
            str(row["rejection_reason"])
            if row["rejection_reason"] is not None
            else None
        ),
        exercise_candidate_id=(
            str(row["exercise_candidate_id"])
            if row["exercise_candidate_id"] is not None
            else None
        ),
        revision=int(row["revision"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _object(value: object) -> dict[str, object]:
    payload = json.loads(str(value))
    if not isinstance(payload, Mapping):
        raise RuntimeError("stored workbench payload is invalid")
    return dict(payload)


def _sha(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


__all__ = ["WorkbenchIterationRepository"]
