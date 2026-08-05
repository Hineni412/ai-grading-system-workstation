from __future__ import annotations

import json
import hashlib
import sqlite3
from collections.abc import Callable, Mapping

from backend.teaching_prep.domain.models import TeachingPrepAIAdoption
from backend.teaching_prep.infrastructure.database import TeachingPrepDatabase
from backend.workspaces.ai_tasks.models import RevisionConflictError


_OBJECTS = {
    "teaching_prep.semester_mapping": (
        "semester_mapping_proposals",
        "semester_mapping_proposal",
    ),
    "teaching_prep.lesson_plan": (
        "lesson_draft_versions",
        "lesson_draft",
    ),
    "teaching_prep.exercise_suggestions": (
        "exercise_suggestion_runs",
        "exercise_suggestion_run",
    ),
    "teaching_prep.slide_change_proposal": (
        "slide_plan_versions",
        "slide_plan",
    ),
}

_COMMAND_KINDS = {
    "teaching_prep.semester_mapping": "apply_semester_mapping",
    "teaching_prep.lesson_plan": "confirm_lesson_draft",
    "teaching_prep.exercise_suggestions": "finalize_exercise_suggestions",
    "teaching_prep.slide_change_proposal": "review_slide_plan",
}


class WorkspaceAIAdoptionRepository:
    """Adopt an AI proposal into its existing A object and receipt atomically."""

    def __init__(
        self,
        database: TeachingPrepDatabase,
        *,
        resource_source_status: Callable[[str], dict[str, object]],
        selection_catalog: Callable[[str], tuple[dict[str, object], str]],
        semester_snapshot: Callable[[str, list[str]], tuple[object, str]],
    ) -> None:
        self._database = database
        self._resource_source_status = resource_source_status
        self._selection_catalog = selection_catalog
        self._semester_snapshot = semester_snapshot

    def find(self, adoption_id: str) -> TeachingPrepAIAdoption | None:
        with self._database.connect() as connection:
            receipt = connection.execute(
                """
                SELECT * FROM teaching_prep_ai_adoption_receipts
                WHERE adoption_id = ?
                """,
                (adoption_id,),
            ).fetchone()
            return (
                self._from_receipt(connection, receipt)
                if receipt is not None
                else None
            )

    def stage_command(
        self,
        *,
        source_task_id: str,
        handoff_id: str,
        adoption_id: str,
        proposal_ref_id: str,
        draft_revision: str,
        target_revision: str,
        command: Mapping[str, object],
    ) -> dict[str, object]:
        """Durably bind one teacher command before its idempotent domain action."""

        clean_command = json.loads(_canonical_json(dict(command)))
        command_sha256 = hashlib.sha256(
            _canonical_json(clean_command).encode("utf-8")
        ).hexdigest()
        envelope = {
            "adoption_id": adoption_id,
            "handoff_id": handoff_id,
            "draft_revision": draft_revision,
            "target_revision": target_revision,
            "command_sha256": command_sha256,
            "payload": clean_command,
        }
        with self._database.connect(immediate=True) as connection:
            result = connection.execute(
                """
                SELECT * FROM teaching_prep_ai_task_results
                WHERE task_id = ? AND proposal_ref_id = ?
                """,
                (source_task_id, proposal_ref_id),
            ).fetchone()
            if result is None:
                raise RevisionConflictError("AI proposal metadata is unavailable")
            if str(result["proposal_revision"]) != draft_revision:
                raise RevisionConflictError("AI proposal revision changed")
            task_kind = str(result["task_kind"])
            expected_command = _COMMAND_KINDS.get(task_kind)
            if expected_command != str(clean_command.get("kind") or ""):
                raise RevisionConflictError(
                    "teacher command does not match the AI proposal kind"
                )

            handoffs = json.loads(str(result["handoffs_json"]))
            if not isinstance(handoffs, list) or len(handoffs) != 1:
                raise RevisionConflictError("AI handoff metadata is unavailable")
            handoff = handoffs[0]
            if not isinstance(handoff, dict):
                raise RevisionConflictError("AI handoff metadata is invalid")
            staged = handoff.get("adoption_command")
            if staged is not None:
                if staged != envelope:
                    raise RevisionConflictError(
                        "adoption command was already bound to different input"
                    )
                return {
                    "task_kind": task_kind,
                    "proposal_ref_id": proposal_ref_id,
                    "command": clean_command,
                }

            # On the first attempt the source and target must still match the
            # frozen task.  A replay skips this check because the formal action
            # itself may have advanced those revisions before the receipt write.
            if task_kind != "teaching_prep.semester_mapping":
                if self._current_proposal_revision(connection, result) != draft_revision:
                    raise RevisionConflictError("AI proposal was changed after handoff")
            self._validate_context_current(connection, result)
            if self._current_target_revision(connection, result) != target_revision:
                raise RevisionConflictError("teaching-prep target revision changed")

            handoff["adoption_command"] = envelope
            connection.execute(
                """
                UPDATE teaching_prep_ai_task_results
                SET handoffs_json = ?
                WHERE task_id = ?
                """,
                (_canonical_json(handoffs), source_task_id),
            )
            return {
                "task_kind": task_kind,
                "proposal_ref_id": proposal_ref_id,
                "command": clean_command,
            }

    def load_staged_command(
        self,
        *,
        source_task_id: str,
        handoff_id: str,
        adoption_id: str,
        proposal_ref_id: str,
        draft_revision: str,
        target_revision: str,
    ) -> dict[str, object] | None:
        with self._database.connect() as connection:
            result = connection.execute(
                """
                SELECT * FROM teaching_prep_ai_task_results
                WHERE task_id = ? AND proposal_ref_id = ?
                """,
                (source_task_id, proposal_ref_id),
            ).fetchone()
            if result is None:
                return None
            handoffs = json.loads(str(result["handoffs_json"]))
            staged = (
                handoffs[0].get("adoption_command")
                if isinstance(handoffs, list)
                and len(handoffs) == 1
                and isinstance(handoffs[0], dict)
                else None
            )
            if not isinstance(staged, dict):
                return None
            if any(
                str(staged.get(key) or "") != expected
                for key, expected in (
                    ("adoption_id", adoption_id),
                    ("handoff_id", handoff_id),
                    ("draft_revision", draft_revision),
                    ("target_revision", target_revision),
                )
            ):
                raise RevisionConflictError(
                    "staged adoption command does not match the handoff claim"
                )
            command = staged.get("payload")
            if not isinstance(command, dict):
                raise RevisionConflictError("staged adoption command is invalid")
            return {
                "task_kind": str(result["task_kind"]),
                "proposal_ref_id": proposal_ref_id,
                "command": command,
            }

    def clear_staged_command(
        self,
        *,
        source_task_id: str,
        handoff_id: str,
        adoption_id: str,
    ) -> None:
        """Release a command only after a proven no-write domain rejection."""

        with self._database.connect(immediate=True) as connection:
            if connection.execute(
                """
                SELECT 1 FROM teaching_prep_ai_adoption_receipts
                WHERE adoption_id = ?
                """,
                (adoption_id,),
            ).fetchone() is not None:
                return
            result = connection.execute(
                """
                SELECT handoffs_json FROM teaching_prep_ai_task_results
                WHERE task_id = ?
                """,
                (source_task_id,),
            ).fetchone()
            if result is None:
                return
            handoffs = json.loads(str(result["handoffs_json"]))
            if (
                not isinstance(handoffs, list)
                or len(handoffs) != 1
                or not isinstance(handoffs[0], dict)
            ):
                return
            staged = handoffs[0].get("adoption_command")
            if not isinstance(staged, dict) or (
                str(staged.get("adoption_id") or "") != adoption_id
                or str(staged.get("handoff_id") or "") != handoff_id
            ):
                return
            del handoffs[0]["adoption_command"]
            connection.execute(
                """
                UPDATE teaching_prep_ai_task_results
                SET handoffs_json = ?
                WHERE task_id = ?
                """,
                (_canonical_json(handoffs), source_task_id),
            )

    def complete_command(
        self,
        *,
        source_task_id: str,
        handoff_id: str,
        adoption_id: str,
        draft_revision: str,
        target_revision: str,
        formal_object_id: str,
    ) -> TeachingPrepAIAdoption:
        """Publish a recovered command result and its receipt atomically."""

        with self._database.connect(immediate=True) as connection:
            receipt = connection.execute(
                """
                SELECT * FROM teaching_prep_ai_adoption_receipts
                WHERE adoption_id = ?
                """,
                (adoption_id,),
            ).fetchone()
            if receipt is not None:
                if (
                    str(receipt["handoff_id"]) != handoff_id
                    or str(receipt["draft_revision"]) != draft_revision
                    or str(receipt["target_revision"]) != target_revision
                ):
                    raise RevisionConflictError(
                        "adoption receipt does not match this handoff revision"
                    )
                return self._from_receipt(connection, receipt)

            result = connection.execute(
                """
                SELECT * FROM teaching_prep_ai_task_results
                WHERE task_id = ?
                """,
                (source_task_id,),
            ).fetchone()
            if result is None:
                raise RevisionConflictError("AI proposal metadata is unavailable")
            handoffs = json.loads(str(result["handoffs_json"]))
            staged = (
                handoffs[0].get("adoption_command")
                if isinstance(handoffs, list)
                and len(handoffs) == 1
                and isinstance(handoffs[0], dict)
                else None
            )
            if not isinstance(staged, dict) or any(
                str(staged.get(key) or "") != expected
                for key, expected in (
                    ("adoption_id", adoption_id),
                    ("handoff_id", handoff_id),
                    ("draft_revision", draft_revision),
                    ("target_revision", target_revision),
                )
            ):
                raise RevisionConflictError("adoption command is unavailable")

            task_kind = str(result["task_kind"])
            table, object_kind = _object_definition(task_kind)
            object_row = connection.execute(
                f"SELECT status, workspace_adoption_id FROM {table} WHERE id = ?",
                (formal_object_id,),
            ).fetchone()
            if object_row is None:
                raise RevisionConflictError("formal teaching-prep object is unavailable")
            current_adoption_id = (
                str(object_row["workspace_adoption_id"])
                if object_row["workspace_adoption_id"] is not None
                else None
            )
            if current_adoption_id not in {None, adoption_id}:
                raise RevisionConflictError("formal object was adopted elsewhere")
            connection.execute(
                f"""
                UPDATE {table}
                SET workspace_adoption_id = ?,
                    workspace_adopted_at = COALESCE(
                        workspace_adopted_at,
                        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    )
                WHERE id = ?
                  AND (workspace_adoption_id IS NULL OR workspace_adoption_id = ?)
                """,
                (adoption_id, formal_object_id, adoption_id),
            )
            object_ref = f"teaching_prep:{object_kind}:{formal_object_id}"
            connection.execute(
                """
                INSERT INTO teaching_prep_ai_adoption_receipts (
                    adoption_id, handoff_id, draft_revision, target_revision,
                    object_ref, receipt_revision
                ) VALUES (?, ?, ?, ?, ?, '1')
                """,
                (
                    adoption_id,
                    handoff_id,
                    draft_revision,
                    target_revision,
                    object_ref,
                ),
            )
            saved = connection.execute(
                """
                SELECT * FROM teaching_prep_ai_adoption_receipts
                WHERE adoption_id = ?
                """,
                (adoption_id,),
            ).fetchone()
            if saved is None:
                raise RuntimeError("teaching-prep adoption receipt was not saved")
            return self._from_receipt(connection, saved)

    def adopt(
        self,
        *,
        source_task_id: str,
        handoff_id: str,
        adoption_id: str,
        proposal_ref_id: str,
        draft_revision: str,
        target_revision: str,
    ) -> TeachingPrepAIAdoption:
        with self._database.connect(immediate=True) as connection:
            receipt = connection.execute(
                """
                SELECT * FROM teaching_prep_ai_adoption_receipts
                WHERE adoption_id = ?
                """,
                (adoption_id,),
            ).fetchone()
            if receipt is not None:
                if (
                    str(receipt["handoff_id"]) != handoff_id
                    or str(receipt["draft_revision"]) != draft_revision
                    or str(receipt["target_revision"]) != target_revision
                ):
                    raise RevisionConflictError(
                        "adoption receipt does not match this handoff revision"
                    )
                return self._from_receipt(connection, receipt)

            result = connection.execute(
                """
                SELECT * FROM teaching_prep_ai_task_results
                WHERE task_id = ? AND proposal_ref_id = ?
                """,
                (source_task_id, proposal_ref_id),
            ).fetchone()
            if result is None:
                raise RevisionConflictError("AI proposal metadata is unavailable")
            if str(result["proposal_revision"]) != draft_revision:
                raise RevisionConflictError("AI proposal revision changed")
            if self._current_proposal_revision(connection, result) != draft_revision:
                raise RevisionConflictError("AI proposal was changed after handoff")
            self._validate_context_current(connection, result)
            if self._current_target_revision(connection, result) != target_revision:
                raise RevisionConflictError("teaching-prep target revision changed")

            task_kind = str(result["task_kind"])
            table, object_kind = _object_definition(task_kind)
            object_row = connection.execute(
                f"""
                SELECT status, workspace_adoption_id
                FROM {table} WHERE id = ?
                """,
                (proposal_ref_id,),
            ).fetchone()
            if object_row is None:
                raise RevisionConflictError("AI proposal no longer exists")
            current_adoption_id = (
                str(object_row["workspace_adoption_id"])
                if object_row["workspace_adoption_id"] is not None
                else None
            )
            if current_adoption_id not in {None, adoption_id}:
                raise RevisionConflictError("AI proposal was already adopted elsewhere")
            connection.execute(
                f"""
                UPDATE {table}
                SET workspace_adoption_id = ?,
                    workspace_adopted_at = COALESCE(
                        workspace_adopted_at,
                        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    )
                WHERE id = ?
                  AND (workspace_adoption_id IS NULL OR workspace_adoption_id = ?)
                """,
                (adoption_id, proposal_ref_id, adoption_id),
            )
            object_ref = f"teaching_prep:{object_kind}:{proposal_ref_id}"
            connection.execute(
                """
                INSERT INTO teaching_prep_ai_adoption_receipts (
                    adoption_id, handoff_id, draft_revision, target_revision,
                    object_ref, receipt_revision
                ) VALUES (?, ?, ?, ?, ?, '1')
                """,
                (
                    adoption_id,
                    handoff_id,
                    draft_revision,
                    target_revision,
                    object_ref,
                ),
            )
            saved = connection.execute(
                """
                SELECT * FROM teaching_prep_ai_adoption_receipts
                WHERE adoption_id = ?
                """,
                (adoption_id,),
            ).fetchone()
            if saved is None:
                raise RuntimeError("teaching-prep adoption receipt was not saved")
            return self._from_receipt(connection, saved)

    def _from_receipt(
        self,
        connection: sqlite3.Connection,
        receipt: sqlite3.Row,
    ) -> TeachingPrepAIAdoption:
        object_ref = str(receipt["object_ref"])
        matched = next(
            (
                (task_kind, table, object_kind, object_ref.removeprefix(prefix))
                for task_kind, (table, object_kind) in _OBJECTS.items()
                if object_ref.startswith(
                    prefix := f"teaching_prep:{object_kind}:"
                )
            ),
            None,
        )
        if matched is None:
            raise RevisionConflictError("adoption receipt object is invalid")
        task_kind, table, object_kind, object_id = matched
        object_row = connection.execute(
            f"""
            SELECT status, workspace_adoption_id, workspace_adopted_at
            FROM {table} WHERE id = ?
            """,
            (object_id,),
        ).fetchone()
        if (
            object_row is None
            or str(object_row["workspace_adoption_id"] or "")
            != str(receipt["adoption_id"])
        ):
            raise RevisionConflictError("adopted teaching-prep object is unavailable")
        return TeachingPrepAIAdoption(
            adoption_id=str(receipt["adoption_id"]),
            handoff_id=str(receipt["handoff_id"]),
            task_kind=task_kind,
            proposal_ref_id=object_id,
            object_kind=object_kind,
            object_id=object_id,
            object_ref=object_ref,
            object_status=str(object_row["status"]),
            draft_revision=str(receipt["draft_revision"]),
            target_revision=str(receipt["target_revision"]),
            receipt_revision=str(receipt["receipt_revision"]),
            adopted_at=str(
                object_row["workspace_adopted_at"] or receipt["created_at"]
            ),
        )

    def _current_target_revision(
        self,
        connection: sqlite3.Connection,
        result: sqlite3.Row,
    ) -> str:
        task_kind = str(result["task_kind"])
        source_id = str(result["source_ref_id"])
        if task_kind == "teaching_prep.semester_mapping":
            refs = json.loads(str(result["context_refs_json"]))
            material_ids = [
                str(item["id"])
                for item in refs
                if item.get("kind") == "material"
            ]
            _snapshot, digest = self._semester_snapshot(source_id, material_ids)
            return digest
        row = connection.execute(
            "SELECT revision FROM lesson_nodes WHERE id = ? AND is_active = 1",
            (source_id,),
        ).fetchone()
        if row is None:
            raise RevisionConflictError("teaching-prep target no longer exists")
        return str(row[0])

    @staticmethod
    def _current_proposal_revision(
        connection: sqlite3.Connection,
        result: sqlite3.Row,
    ) -> str:
        task_kind = str(result["task_kind"])
        proposal_id = str(result["proposal_ref_id"])
        table, _object_kind = _object_definition(task_kind)
        revision_column = {
            "teaching_prep.semester_mapping": "revision",
            "teaching_prep.lesson_plan": "version_number",
            "teaching_prep.exercise_suggestions": "1",
            "teaching_prep.slide_change_proposal": "version_number",
        }[task_kind]
        status_clause = (
            " AND status = 'succeeded'"
            if task_kind == "teaching_prep.exercise_suggestions"
            else ""
        )
        row = connection.execute(
            f"SELECT {revision_column} FROM {table} WHERE id = ?{status_clause}",
            (proposal_id,),
        ).fetchone()
        if row is None:
            raise RevisionConflictError("AI proposal no longer exists")
        return str(row[0])

    def _validate_context_current(
        self,
        connection: sqlite3.Connection,
        result: sqlite3.Row,
    ) -> None:
        task_kind = str(result["task_kind"])
        refs = {
            str(item["kind"]): item
            for item in json.loads(str(result["context_refs_json"]))
        }
        if task_kind == "teaching_prep.lesson_plan":
            reference = _required_ref(refs, "resource_pack")
            row = connection.execute(
                "SELECT pack_sha256 FROM resource_pack_versions WHERE id = ?",
                (str(reference["id"]),),
            ).fetchone()
            if row is None or str(row[0]) != str(reference["revision"]):
                raise RevisionConflictError("resource pack revision changed")
            if self._resource_source_status(str(reference["id"]))["sources_changed"]:
                raise RevisionConflictError("resource pack sources changed")
        elif task_kind == "teaching_prep.exercise_suggestions":
            reference = _required_ref(refs, "reference_snapshot")
            row = connection.execute(
                """
                SELECT lesson_node_id, source_state_sha256
                FROM reference_selection_snapshots WHERE id = ?
                """,
                (str(reference["id"]),),
            ).fetchone()
            if row is None or str(row[1]) != str(reference["revision"]):
                raise RevisionConflictError("reference snapshot revision changed")
            _catalog, current = self._selection_catalog(str(row[0]))
            if current != str(row[1]):
                raise RevisionConflictError("reference sources changed")
        elif task_kind == "teaching_prep.slide_change_proposal":
            reference = _required_ref(refs, "lesson_draft")
            row = connection.execute(
                """
                SELECT version_number, resource_pack_id
                FROM lesson_draft_versions WHERE id = ?
                """,
                (str(reference["id"]),),
            ).fetchone()
            if row is None or str(row[0]) != str(reference["revision"]):
                raise RevisionConflictError("lesson draft revision changed")
            if self._resource_source_status(str(row[1]))["sources_changed"]:
                raise RevisionConflictError("slide proposal sources changed")


def _object_definition(task_kind: str) -> tuple[str, str]:
    value = _OBJECTS.get(task_kind)
    if value is None:
        raise RevisionConflictError("AI proposal kind is unsupported")
    return value


def _required_ref(
    refs: dict[str, object],
    kind: str,
) -> dict[str, object]:
    value = refs.get(kind)
    if not isinstance(value, dict):
        raise RevisionConflictError(f"{kind} context is unavailable")
    return value


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


__all__ = ["WorkspaceAIAdoptionRepository"]
