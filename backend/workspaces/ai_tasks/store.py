from __future__ import annotations

import json
import secrets
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path

from .models import (
    HandoffDraft,
    HandoffNotFoundError,
    HandoffSnapshot,
    OpaqueRef,
    OperationConflictError,
    StoredTask,
    TaskNotFoundError,
)


class WorkspaceAITaskStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path, isolation_level=None)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("PRAGMA journal_mode = WAL")
            with connection:
                yield connection
        finally:
            connection.close()

    def prepare(
        self,
        *,
        operation_id: str,
        module: str,
        task_kind: str,
        source_ref: OpaqueRef,
        context_refs: Sequence[OpaqueRef],
        prompt_contract_version: str,
        request_fingerprint: str,
        model_destination_fingerprint: str,
        return_target: str,
    ) -> StoredTask:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM workspace_ai_tasks WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["request_fingerprint"]) != request_fingerprint
                    or str(existing["model_destination_fingerprint"])
                    != model_destination_fingerprint
                ):
                    raise OperationConflictError(
                        "operation ID was reused for different input"
                    )
                connection.commit()
                return _task(existing)
            connection.execute(
                """
                UPDATE workspace_ai_handoffs
                SET adoption_state = 'stale', revision = revision + 1,
                    updated_at = datetime('now','localtime')
                WHERE task_id IN (
                    SELECT task_id FROM workspace_ai_tasks
                    WHERE module = ? AND source_kind = ? AND source_ref_id = ?
                      AND source_revision <> ?
                )
                  AND expires_on_source_change = 1
                  AND adoption_state IN ('pending', 'opened')
                """,
                (module, source_ref.kind, source_ref.id, source_ref.revision),
            )
            task_id = secrets.token_hex(16)
            connection.execute(
                """
                INSERT INTO workspace_ai_tasks (
                    task_id, operation_id, module, task_kind,
                    source_kind, source_ref_id, source_revision,
                    context_refs_json, prompt_contract_version,
                    request_fingerprint, model_destination_fingerprint,
                    return_target
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    operation_id,
                    module,
                    task_kind,
                    source_ref.kind,
                    source_ref.id,
                    source_ref.revision,
                    _json_refs(context_refs),
                    prompt_contract_version,
                    request_fingerprint,
                    model_destination_fingerprint,
                    return_target,
                ),
            )
            connection.commit()
        return self.require_task(task_id)

    def require_task(self, task_id: str) -> StoredTask:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM workspace_ai_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        if row is None:
            raise TaskNotFoundError("workspace AI task was not found")
        return _task(row)

    def find_by_operation(self, operation_id: str) -> StoredTask | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM workspace_ai_tasks WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
        return _task(row) if row is not None else None

    def list_tasks(self, module: str) -> tuple[StoredTask, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM workspace_ai_tasks
                WHERE module = ?
                ORDER BY updated_at DESC, task_id DESC
                """,
                (module,),
            ).fetchall()
        return tuple(_task(row) for row in rows)

    def list_actionable_tasks(self, module: str) -> tuple[StoredTask, ...]:
        """List only tasks that can still affect a workspace's current status."""

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT task.*
                FROM workspace_ai_tasks AS task
                WHERE task.module = ?
                  AND (
                    task.status IN ('prepared', 'queued', 'running', 'needs_input')
                    OR (
                      task.status = 'proposal_ready'
                      AND EXISTS (
                        SELECT 1
                        FROM workspace_ai_handoffs AS handoff
                        WHERE handoff.task_id = task.task_id
                          AND handoff.adoption_state IN (
                            'pending', 'opened', 'adoption_started'
                          )
                      )
                    )
                  )
                ORDER BY task.updated_at DESC, task.task_id DESC
                """,
                (module,),
            ).fetchall()
        return tuple(_task(row) for row in rows)

    def create_dispatch_job(self, task_id: str) -> tuple[StoredTask, bool]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM workspace_ai_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if row is None:
                raise TaskNotFoundError("workspace AI task was not found")
            task = _task(row)
            retryable_first_send = (
                task.status == "failed_before_dispatch"
                and task.send_attempt_count == 0
                and task.dispatch_evidence == "not_started"
            )
            if task.status != "prepared" and not retryable_first_send:
                connection.commit()
                return task, False
            cursor = connection.execute(
                """
                INSERT INTO jobs (job_type, payload_json, status)
                VALUES ('workspace_ai.run', ?, 'queued')
                """,
                (json.dumps({"task_id": task_id}, sort_keys=True),),
            )
            job_id = int(cursor.lastrowid)
            connection.execute(
                """
                UPDATE workspace_ai_tasks
                SET status = 'queued', phase = 'queued', progress = 0,
                    job_id = ?, error_code = NULL, revision = revision + 1,
                    updated_at = datetime('now','localtime'), finished_at = NULL
                WHERE task_id = ?
                """,
                (job_id, task_id),
            )
            connection.commit()
        return self.require_task(task_id), True

    def claim(self, task_id: str) -> StoredTask:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = self._task_in(connection, task_id)
            if task.status == "queued":
                if task.cancel_requested and task.send_attempt_count == 0:
                    self._finish_before_dispatch(connection, task_id, "cancelled_before_dispatch")
                else:
                    connection.execute(
                        """
                        UPDATE workspace_ai_tasks
                        SET status = 'running', phase = 'claimed', progress = 0.05,
                            revision = revision + 1,
                            updated_at = datetime('now','localtime')
                        WHERE task_id = ? AND status = 'queued'
                        """,
                        (task_id,),
                    )
            connection.commit()
        return self.require_task(task_id)

    def reserve_send_attempt(self, task_id: str) -> StoredTask:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = self._task_in(connection, task_id)
            if task.cancel_requested and task.send_attempt_count == 0:
                self._finish_before_dispatch(connection, task_id, "cancelled_before_dispatch")
            elif (
                task.status == "running"
                and task.phase == "claimed"
                and task.send_attempt_count == 0
            ):
                connection.execute(
                    """
                    UPDATE workspace_ai_tasks
                    SET phase = 'send_attempt_reserved', progress = 0.15,
                        send_attempt_count = 1,
                        dispatch_evidence = 'may_have_started',
                        revision = revision + 1,
                        updated_at = datetime('now','localtime')
                    WHERE task_id = ?
                    """,
                    (task_id,),
                )
            connection.commit()
        return self.require_task(task_id)

    def mark_validating(self, task_id: str) -> StoredTask:
        return self._update(
            task_id,
            "phase = 'validating', progress = 0.75, dispatch_evidence = 'response_persisted'",
        )

    def complete(
        self,
        task_id: str,
        *,
        proposal_ref_id: str,
        proposal_revision: str,
        handoffs: Sequence[HandoffDraft],
        needs_input: bool,
    ) -> StoredTask:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = self._task_in(connection, task_id)
            for handoff in handoffs:
                connection.execute(
                    """
                    INSERT INTO workspace_ai_handoffs (
                        handoff_id, task_id, work_item_id, module, intent,
                        handling_mode, destination_key, subject_refs_json,
                        draft_ref_id, draft_revision, prefill_keys_json,
                        missing_fields_json, source_turn_id,
                        return_destination_key, return_focus_ref,
                        expires_on_source_change
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(task_id, work_item_id) DO NOTHING
                    """,
                    (
                        secrets.token_hex(16),
                        task_id,
                        handoff.work_item_id,
                        task.module,
                        handoff.intent,
                        handoff.handling_mode,
                        handoff.destination_key,
                        _json_refs(handoff.subject_refs),
                        handoff.draft_ref.id,
                        handoff.draft_ref.revision,
                        json.dumps(list(handoff.prefill_keys), ensure_ascii=False),
                        json.dumps(list(handoff.missing_fields), ensure_ascii=False),
                        handoff.source_turn_id,
                        handoff.return_destination_key,
                        handoff.return_focus_ref,
                        int(handoff.expires_on_source_change),
                    ),
                )
            status = "needs_input" if needs_input else "proposal_ready"
            connection.execute(
                """
                UPDATE workspace_ai_tasks
                SET status = ?, phase = 'handoff_ready', progress = 1,
                    dispatch_evidence = 'response_persisted',
                    proposal_ref_id = ?, proposal_revision = ?,
                    error_code = NULL, revision = revision + 1,
                    updated_at = datetime('now','localtime'),
                    finished_at = datetime('now','localtime')
                WHERE task_id = ?
                """,
                (status, proposal_ref_id, proposal_revision, task_id),
            )
            connection.commit()
        return self.require_task(task_id)

    def fail(
        self,
        task_id: str,
        *,
        status: str,
        error_code: str,
        response_persisted: bool = False,
    ) -> StoredTask:
        evidence = "response_persisted" if response_persisted else None
        assignments = [
            "status = ?",
            "phase = 'finished'",
            "error_code = ?",
            "progress = 1",
            "revision = revision + 1",
            "updated_at = datetime('now','localtime')",
            "finished_at = datetime('now','localtime')",
        ]
        values: list[object] = [status, error_code]
        if evidence is not None:
            assignments.append("dispatch_evidence = ?")
            values.append(evidence)
        values.append(task_id)
        with self._connect() as connection:
            connection.execute(
                f"UPDATE workspace_ai_tasks SET {', '.join(assignments)} WHERE task_id = ?",
                tuple(values),
            )
        return self.require_task(task_id)

    def request_cancel(self, task_id: str) -> StoredTask:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = self._task_in(connection, task_id)
            if task.status == "prepared" or (
                task.status in {"queued", "running"}
                and task.send_attempt_count == 0
            ):
                self._finish_before_dispatch(connection, task_id, "cancelled_before_dispatch")
            elif task.status in {"queued", "running"}:
                connection.execute(
                    """
                    UPDATE workspace_ai_tasks
                    SET cancel_requested = 1, revision = revision + 1,
                        updated_at = datetime('now','localtime')
                    WHERE task_id = ?
                    """,
                    (task_id,),
                )
            connection.commit()
        return self.require_task(task_id)

    def discard_result_unknown(self, task_id: str) -> StoredTask:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = self._task_in(connection, task_id)
            if task.status == "discarded":
                connection.commit()
                return task
            if task.status != "result_unknown":
                raise ValueError(
                    "only a result-unknown workspace AI task can be discarded"
                )
            connection.execute(
                """
                UPDATE workspace_ai_tasks
                SET status = 'discarded', phase = 'finished', progress = 1,
                    error_code = NULL, revision = revision + 1,
                    updated_at = datetime('now','localtime'),
                    finished_at = datetime('now','localtime')
                WHERE task_id = ?
                """,
                (task_id,),
            )
            connection.commit()
        return self.require_task(task_id)

    def list_handoffs(self, task_id: str) -> tuple[HandoffSnapshot, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM workspace_ai_handoffs
                WHERE task_id = ? ORDER BY created_at, handoff_id
                """,
                (task_id,),
            ).fetchall()
        return tuple(_handoff(row) for row in rows)

    def require_handoff(self, handoff_id: str) -> HandoffSnapshot:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM workspace_ai_handoffs WHERE handoff_id = ?",
                (handoff_id,),
            ).fetchone()
        if row is None:
            raise HandoffNotFoundError("workspace AI handoff was not found")
        return _handoff(row)

    def begin_adoption(
        self,
        handoff_id: str,
        *,
        draft_revision: str,
        target_revision: str,
    ) -> HandoffSnapshot:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM workspace_ai_handoffs WHERE handoff_id = ?",
                (handoff_id,),
            ).fetchone()
            if row is None:
                raise HandoffNotFoundError("workspace AI handoff was not found")
            handoff = _handoff(row)
            if handoff.draft_ref.revision != draft_revision:
                from .models import RevisionConflictError

                raise RevisionConflictError("handoff draft revision changed")
            if handoff.adoption_state in {"discarded", "stale"}:
                raise RevisionConflictError("handoff is no longer adoptable")
            stored_target = str(row["target_revision"]) if row["target_revision"] else None
            if stored_target is not None and stored_target != target_revision:
                from .models import RevisionConflictError

                raise RevisionConflictError("handoff target revision changed")
            if handoff.adoption_state == "adopted":
                connection.commit()
                return handoff
            adoption_id = handoff.adoption_id or secrets.token_hex(16)
            connection.execute(
                """
                UPDATE workspace_ai_handoffs
                SET adoption_state = 'adoption_started', adoption_id = ?,
                    target_revision = ?, revision = revision + 1,
                    updated_at = datetime('now','localtime')
                WHERE handoff_id = ?
                """,
                (adoption_id, target_revision, handoff_id),
            )
            connection.commit()
        return self.require_handoff(handoff_id)

    def release_uncommitted_adoption(
        self,
        handoff_id: str,
        *,
        adoption_id: str,
        target_revision: str,
    ) -> HandoffSnapshot:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE workspace_ai_handoffs
                SET adoption_state = 'opened', target_revision = NULL,
                    revision = revision + 1,
                    updated_at = datetime('now','localtime')
                WHERE handoff_id = ?
                  AND adoption_state = 'adoption_started'
                  AND adoption_id = ?
                  AND target_revision = ?
                """,
                (handoff_id, adoption_id, target_revision),
            )
            connection.commit()
        return self.require_handoff(handoff_id)

    def rebind_handoff_draft(
        self,
        handoff_id: str,
        *,
        module: str,
        expected_draft_revision: str,
        draft_revision: str,
        subject_refs: tuple[OpaqueRef, ...],
    ) -> HandoffSnapshot:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM workspace_ai_handoffs WHERE handoff_id = ?",
                (handoff_id,),
            ).fetchone()
            if row is None:
                raise HandoffNotFoundError("workspace AI handoff was not found")
            handoff = _handoff(row)
            if handoff.module != module:
                raise ValueError("handoff module does not match domain endpoint")
            if handoff.draft_ref.revision == draft_revision:
                if handoff.subject_refs != subject_refs:
                    from .models import RevisionConflictError

                    raise RevisionConflictError(
                        "handoff subjects changed without a new draft revision"
                    )
                if handoff.adoption_state in {"pending", "stale"}:
                    connection.execute(
                        """
                        UPDATE workspace_ai_handoffs
                        SET adoption_state = 'opened', target_revision = NULL,
                            revision = revision + 1,
                            updated_at = datetime('now','localtime')
                        WHERE handoff_id = ?
                          AND adoption_state IN ('pending','stale')
                        """,
                        (handoff_id,),
                    )
                    connection.commit()
                    return self.require_handoff(handoff_id)
                connection.commit()
                return handoff
            if handoff.draft_ref.revision != expected_draft_revision:
                from .models import RevisionConflictError

                raise RevisionConflictError("handoff draft revision changed")
            if handoff.adoption_state not in {"pending", "opened", "stale"}:
                from .models import RevisionConflictError

                raise RevisionConflictError("handoff draft cannot be rebound")
            connection.execute(
                """
                UPDATE workspace_ai_handoffs
                SET draft_revision = ?, subject_refs_json = ?,
                    adoption_state = 'opened', target_revision = NULL,
                    revision = revision + 1,
                    updated_at = datetime('now','localtime')
                WHERE handoff_id = ?
                  AND draft_revision = ?
                  AND adoption_state IN ('pending','opened','stale')
                """,
                (
                    draft_revision,
                    _json_refs(subject_refs),
                    handoff_id,
                    expected_draft_revision,
                ),
            )
            if connection.total_changes != 1:
                from .models import RevisionConflictError

                raise RevisionConflictError("handoff draft rebind lost its race")
            connection.commit()
        return self.require_handoff(handoff_id)

    def set_handoff_state(self, handoff_id: str, state: str) -> HandoffSnapshot:
        if state not in {"opened", "discarded", "stale"}:
            raise ValueError("handoff lifecycle state is invalid")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            handoff = self.require_handoff(handoff_id)
            allowed = (
                handoff.adoption_state in {"pending", "opened"}
                and state in {"opened", "discarded", "stale"}
            )
            if not allowed:
                from .models import RevisionConflictError

                raise RevisionConflictError("handoff lifecycle already finished")
            connection.execute(
                """
                UPDATE workspace_ai_handoffs
                SET adoption_state = ?, revision = revision + 1,
                    updated_at = datetime('now','localtime')
                WHERE handoff_id = ?
                """,
                (state, handoff_id),
            )
            connection.commit()
        return self.require_handoff(handoff_id)

    def finish_adoption(self, handoff_id: str, *, object_ref: str) -> HandoffSnapshot:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE workspace_ai_handoffs
                SET adoption_state = 'adopted', adopted_object_ref = ?,
                    revision = revision + 1,
                    updated_at = datetime('now','localtime')
                WHERE handoff_id = ?
                """,
                (object_ref, handoff_id),
            )
        return self.require_handoff(handoff_id)

    def interrupted_tasks(self) -> tuple[StoredTask, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM workspace_ai_tasks
                WHERE status IN ('queued', 'running')
                """
            ).fetchall()
        return tuple(_task(row) for row in rows)

    def _task_in(self, connection: sqlite3.Connection, task_id: str) -> StoredTask:
        row = connection.execute(
            "SELECT * FROM workspace_ai_tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if row is None:
            raise TaskNotFoundError("workspace AI task was not found")
        return _task(row)

    @staticmethod
    def _finish_before_dispatch(
        connection: sqlite3.Connection,
        task_id: str,
        status: str,
    ) -> None:
        connection.execute(
            """
            UPDATE workspace_ai_tasks
            SET status = ?, phase = 'finished', progress = 1,
                cancel_requested = CASE WHEN ? = 'cancelled_before_dispatch' THEN 1 ELSE cancel_requested END,
                revision = revision + 1,
                updated_at = datetime('now','localtime'),
                finished_at = datetime('now','localtime')
            WHERE task_id = ?
            """,
            (status, status, task_id),
        )

    def _update(self, task_id: str, assignments: str) -> StoredTask:
        with self._connect() as connection:
            connection.execute(
                f"""
                UPDATE workspace_ai_tasks
                SET {assignments}, revision = revision + 1,
                    updated_at = datetime('now','localtime')
                WHERE task_id = ?
                """,
                (task_id,),
            )
        return self.require_task(task_id)


def _task(row: sqlite3.Row) -> StoredTask:
    return StoredTask(
        task_id=str(row["task_id"]),
        operation_id=str(row["operation_id"]),
        module=str(row["module"]),
        task_kind=str(row["task_kind"]),
        source_ref=OpaqueRef(
            kind=str(row["source_kind"]),
            id=str(row["source_ref_id"]),
            revision=str(row["source_revision"]),
        ),
        context_refs=_refs(row["context_refs_json"]),
        prompt_contract_version=str(row["prompt_contract_version"]),
        request_fingerprint=str(row["request_fingerprint"]),
        model_destination_fingerprint=str(row["model_destination_fingerprint"]),
        return_target=str(row["return_target"]),
        status=str(row["status"]),  # type: ignore[arg-type]
        phase=str(row["phase"]),
        progress=float(row["progress"]),
        send_attempt_count=int(row["send_attempt_count"]),
        dispatch_evidence=str(row["dispatch_evidence"]),  # type: ignore[arg-type]
        cancel_requested=bool(row["cancel_requested"]),
        proposal_ref_id=(str(row["proposal_ref_id"]) if row["proposal_ref_id"] else None),
        proposal_revision=(str(row["proposal_revision"]) if row["proposal_revision"] else None),
        job_id=int(row["job_id"]) if row["job_id"] is not None else None,
        error_code=str(row["error_code"]) if row["error_code"] else None,
        revision=int(row["revision"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        finished_at=str(row["finished_at"]) if row["finished_at"] else None,
    )


def _handoff(row: sqlite3.Row) -> HandoffSnapshot:
    return HandoffSnapshot(
        contract_version="teacher_workspace_handoff.v1",
        handoff_id=str(row["handoff_id"]),
        work_item_id=str(row["work_item_id"]),
        module=str(row["module"]),
        intent=str(row["intent"]),
        handling_mode=str(row["handling_mode"]),
        destination_key=str(row["destination_key"]),
        subject_refs=_refs(row["subject_refs_json"]),
        draft_ref=OpaqueRef(
            kind="draft",
            id=str(row["draft_ref_id"]),
            revision=str(row["draft_revision"]),
        ),
        adoption_state=str(row["adoption_state"]),  # type: ignore[arg-type]
        prefill_keys=tuple(str(item) for item in _list(row["prefill_keys_json"])),
        missing_fields=tuple(str(item) for item in _list(row["missing_fields_json"])),
        source_task_id=str(row["task_id"]),
        source_turn_id=str(row["source_turn_id"]) if row["source_turn_id"] else None,
        return_destination_key=str(row["return_destination_key"]),
        return_focus_ref=str(row["return_focus_ref"]) if row["return_focus_ref"] else None,
        expires_on_source_change=bool(row["expires_on_source_change"]),
        adoption_id=str(row["adoption_id"]) if row["adoption_id"] else None,
        target_revision=(
            str(row["target_revision"]) if row["target_revision"] else None
        ),
        revision=int(row["revision"]),
    )


def _json_refs(values: Sequence[OpaqueRef]) -> str:
    return json.dumps(
        [{"kind": item.kind, "id": item.id, "revision": item.revision} for item in values],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _refs(value: object) -> tuple[OpaqueRef, ...]:
    return tuple(
        OpaqueRef(
            kind=str(item.get("kind") or ""),
            id=str(item.get("id") or ""),
            revision=str(item.get("revision") or ""),
        )
        for item in _list(value)
        if isinstance(item, Mapping)
    )


def _list(value: object) -> list[object]:
    parsed = json.loads(str(value))
    if not isinstance(parsed, list):
        raise RuntimeError("stored workspace AI metadata is invalid")
    return parsed


__all__ = ["WorkspaceAITaskStore"]
