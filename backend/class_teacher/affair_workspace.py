from __future__ import annotations

import json
import re
from contextlib import closing
from datetime import UTC, datetime, timedelta
from typing import Any, Callable
from uuid import uuid4

from .encrypted_database import EncryptedDatabase
from .errors import VaultError
from .secure_repository import EncryptedObjectRepository
from .sensitive_work_projection import SensitiveWorkProjection
from .sop_workflow_service import SopWorkflowService


_OPERATION = re.compile(r"[A-Za-z0-9_-]{8,128}")


def _iso(value: datetime | None = None) -> str:
    return (value or datetime.now(UTC)).isoformat()


class AffairWorkspace:
    def __init__(
        self,
        database: EncryptedDatabase,
        repository: EncryptedObjectRepository,
        key_provider: Callable[[str], bytes],
        sop: SopWorkflowService,
        projections: SensitiveWorkProjection,
    ) -> None:
        self.database = database
        self.repository = repository
        self._key_provider = key_provider
        self.sop = sop
        self.projections = projections

    def create(
        self,
        *,
        token: str,
        operation_id: str,
        template_version_id: str,
        title: str,
        summary: str | None,
        participant_refs: list[str],
        subject_ids: list[str] | None = None,
        idempotency_fingerprint: str | None = None,
    ) -> dict[str, object]:
        def enqueue_projection(
            connection: Any,
            vmk: bytes,
            affair_id: str,
            occurrence_id: str,
        ) -> None:
            self.projections.enqueue(
                connection,
                vmk=vmk,
                source_kind="sensitive_affair",
                source_id=affair_id,
                occurrence_id=occurrence_id,
                state="pending",
                due_date=None,
            )

        created = self.sop.create_affair(
            token=token,
            operation_id=operation_id,
            template_version_id=template_version_id,
            title=title,
            summary=summary,
            participant_refs=participant_refs,
            subject_ids=subject_ids,
            idempotency_fingerprint=idempotency_fingerprint,
            transaction_hook=enqueue_projection,
        )
        self.projections.drain(token=token)
        return self.read(token=token, affair_id=str(created["affair_id"]))

    def list(
        self,
        *,
        token: str,
        state: str | None = None,
        template: str | None = None,
        cursor: str | None = None,
    ) -> dict[str, object]:
        self.projections.drain(token=token)
        vmk = self._key_provider(token)
        items: list[dict[str, object]] = []
        with closing(self.database.connect()) as connection:
            rows = connection.execute(
                """
                SELECT affair_id, template_version_id, payload_object_id,
                       state, updated_at
                FROM affairs ORDER BY updated_at DESC, affair_id
                """
            ).fetchall()
            for row in rows:
                if state and str(row["state"]) != state:
                    continue
                if template and str(row["template_version_id"]) != template:
                    continue
                payload, _revision = self.repository.get(
                    connection,
                    vmk=vmk,
                    object_id=str(row["payload_object_id"]),
                )
                occurrence = connection.execute(
                    """
                    SELECT occurrence_id, sequence FROM affair_occurrences
                    WHERE affair_id = ? ORDER BY sequence DESC LIMIT 1
                    """,
                    (str(row["affair_id"]),),
                ).fetchone()
                counts = connection.execute(
                    """
                    SELECT
                      SUM(CASE WHEN state IN ('ready','in_progress','waiting') THEN 1 ELSE 0 END) current_count,
                      SUM(CASE WHEN state IN ('completed','waived') THEN 1 ELSE 0 END) completed_count
                    FROM step_instances WHERE occurrence_id = ?
                    """,
                    (str(occurrence["occurrence_id"]),),
                ).fetchone()
                current_count = int(counts["current_count"] or 0)
                existing_projection = connection.execute(
                    """
                    SELECT g.group_id,
                           (SELECT COUNT(*)
                            FROM sensitive_work_projection_outbox o
                            WHERE o.group_id = g.group_id
                              AND o.state = 'pending') pending_count
                    FROM sensitive_work_groups g
                    WHERE g.source_kind = 'sensitive_affair'
                      AND g.source_id = ? AND g.occurrence_id = ?
                    LIMIT 1
                    """,
                    (str(row["affair_id"]), str(occurrence["occurrence_id"])),
                ).fetchone()
                sync_state = (
                    "pending"
                    if existing_projection is None
                    or int(existing_projection["pending_count"] or 0) > 0
                    else "applied"
                )
                items.append({
                    "affair_id": str(row["affair_id"]),
                    "title": payload.get("title"),
                    "summary": payload.get("summary"),
                    "state": str(row["state"]),
                    "revision": self._workspace_revision(
                        connection,
                        str(row["affair_id"]),
                    ),
                    "occurrence_id": str(occurrence["occurrence_id"]),
                    "occurrence_sequence": int(occurrence["sequence"]),
                    "current_step_count": current_count,
                    "completed_step_count": int(counts["completed_count"] or 0),
                    "updated_at": str(row["updated_at"]),
                    "projection_state": sync_state,
                })
        return {"items": items, "cursor": cursor}

    def read(self, *, token: str, affair_id: str) -> dict[str, object]:
        self.projections.drain(token=token)
        affair = self.sop.get_affair(token=token, affair_id=affair_id)
        vmk = self._key_provider(token)
        with closing(self.database.connect()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM sop_step_drafts
                WHERE affair_id = ? AND expires_at > ?
                ORDER BY updated_at, draft_id
                """,
                (affair_id, _iso()),
            ).fetchall()
            drafts = []
            for row in rows:
                payload, _revision = self.repository.get(
                    connection,
                    vmk=vmk,
                    object_id=str(row["payload_object_id"]),
                )
                drafts.append({
                    "draft_id": str(row["draft_id"]),
                    "step_instance_id": str(row["step_instance_id"]),
                    "draft_kind": str(row["draft_kind"]),
                    "revision": int(row["revision"]),
                    "text": str(payload.get("text") or ""),
                    "updated_at": str(row["updated_at"]),
                    "expires_at": str(row["expires_at"]),
                })
            workspace_revision = self._workspace_revision(connection, affair_id)
            projection_state = self._projection_state(
                connection,
                affair_id=affair_id,
                occurrence_id=str(affair["occurrence_id"]),
            )
        return {
            **affair,
            "revision": workspace_revision,
            "drafts": drafts,
            "projection_state": projection_state,
        }

    def save_draft(
        self,
        *,
        token: str,
        affair_id: str,
        step_instance_id: str,
        draft_kind: str,
        text: str,
        expected_revision: int | None,
        operation_id: str,
    ) -> dict[str, object]:
        self._validate_operation(operation_id)
        if draft_kind not in {"fact", "communication"}:
            raise VaultError(
                "sop_draft_kind_invalid",
                "事务草稿类型无效",
                status_code=422,
            )
        clean = " ".join(str(text or "").split())
        if not clean or len(clean) > 8000:
            raise VaultError(
                "sop_draft_text_invalid",
                "请填写不超过 8000 字的事务草稿",
                status_code=422,
            )
        affair = self.sop.get_affair(token=token, affair_id=affair_id)
        steps = [
            *list(affair.get("current_steps") or []),
            *list(affair.get("completed_steps") or []),
            *list(affair.get("preview_steps") or []),
        ]
        if not any(str(item.get("step_instance_id")) == step_instance_id for item in steps):
            raise VaultError("sop_step_not_found", "事务步骤不存在", status_code=404)
        vmk = self._key_provider(token)
        timestamp = _iso()
        with closing(self.database.connect()) as connection:
            with connection:
                replay = connection.execute(
                    "SELECT result_json FROM idempotency_ledger WHERE operation_id = ? AND operation_type = 'sop.step.draft'",
                    (operation_id,),
                ).fetchone()
                if replay is not None:
                    stored = json.loads(str(replay["result_json"]))
                    draft_row = connection.execute(
                        "SELECT payload_object_id FROM sop_step_drafts WHERE draft_id = ?",
                        (str(stored["draft_id"]),),
                    ).fetchone()
                    if draft_row is None:
                        raise VaultError("sop_draft_not_found", "事务草稿不存在", status_code=404)
                    protected, _revision = self.repository.get(
                        connection, vmk=vmk, object_id=str(draft_row["payload_object_id"])
                    )
                    return {**stored, "text": str(protected.get("text") or "")}
                row = connection.execute(
                    "SELECT * FROM sop_step_drafts WHERE step_instance_id = ? AND draft_kind = ?",
                    (step_instance_id, draft_kind),
                ).fetchone()
                if row is not None and expected_revision is not None and int(row["revision"]) != expected_revision:
                    raise VaultError(
                        "sop_draft_revision_conflict",
                        "事务草稿已经变化，请刷新后再保存",
                        status_code=409,
                    )
                draft_id = str(row["draft_id"]) if row is not None else uuid4().hex
                object_id = str(row["payload_object_id"]) if row is not None else f"sop-draft-{draft_id}"
                revision = self.repository.put(
                    connection,
                    vmk=vmk,
                    object_id=object_id,
                    object_type="sop_step_draft",
                    payload={"text": clean},
                )
                expires = _iso(datetime.now(UTC) + timedelta(days=7))
                connection.execute(
                    """
                    INSERT INTO sop_step_drafts (
                        draft_id, affair_id, occurrence_id, step_instance_id,
                        draft_kind, payload_object_id, revision, expires_at,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(step_instance_id, draft_kind) DO UPDATE SET
                        revision = excluded.revision,
                        expires_at = excluded.expires_at,
                        updated_at = excluded.updated_at
                    """,
                    (
                        draft_id,
                        affair_id,
                        str(affair["occurrence_id"]),
                        step_instance_id,
                        draft_kind,
                        object_id,
                        revision,
                        expires,
                        timestamp,
                        timestamp,
                    ),
                )
                result = {
                    "draft_id": draft_id,
                    "step_instance_id": step_instance_id,
                    "draft_kind": draft_kind,
                    "revision": revision,
                    "text": clean,
                    "updated_at": timestamp,
                    "expires_at": expires,
                }
                connection.execute(
                    "INSERT INTO idempotency_ledger VALUES (?, 'sop.step.draft', ?, ?)",
                    (
                        operation_id,
                        json.dumps({key: value for key, value in result.items() if key != "text"}, ensure_ascii=False),
                        timestamp,
                    ),
                )
        return result

    def advance(
        self,
        *,
        token: str,
        affair_id: str,
        command: str,
        operation_id: str,
        expected_revision: int,
        step_instance_id: str | None = None,
        outcome: str | None = None,
        result: str | None = None,
        decision_kind: str | None = None,
        summary: str | None = None,
        decision_key: str | None = None,
        selected_option: str | None = None,
        reason: str | None = None,
    ) -> dict[str, object]:
        self._validate_operation(operation_id)
        operation_type = {
            "complete_step": "sop.step.complete",
            "teacher_decision": "sop.decision.record",
            "close": "sop.affair.close",
            "reopen": "sop.affair.reopen",
            "discard": "sop.affair.discard",
        }.get(command)
        if operation_type is None:
            raise VaultError("sop_command_invalid", "事务命令无效", status_code=422)
        with closing(self.database.connect()) as connection:
            replay = connection.execute(
                "SELECT operation_type FROM idempotency_ledger WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if replay is not None:
                if str(replay["operation_type"]) != operation_type:
                    raise VaultError(
                        "vault_operation_conflict",
                        "同一操作编号不能用于不同操作",
                        status_code=409,
                    )
                self.projections.drain(token=token)
                return self.read(token=token, affair_id=affair_id)
            workspace_revision = self._workspace_revision(connection, affair_id)
        if workspace_revision != expected_revision:
            raise VaultError(
                "sop_affair_revision_conflict",
                "事务已经变化，请刷新后再继续",
                status_code=409,
            )

        before = self.sop.get_affair(token=token, affair_id=affair_id)
        current_steps = list(before.get("current_steps") or [])
        target_step = next(
            (
                item
                for item in current_steps
                if str(item.get("step_instance_id") or "")
                == str(step_instance_id or "")
            ),
            None,
        )

        if command in {"complete_step", "teacher_decision"} and target_step is None:
            raise VaultError(
                "sop_step_not_active",
                "只能操作当前已激活的事务步骤",
                status_code=409,
            )
        if command == "teacher_decision":
            step_decision_key = str(target_step.get("decision_key") or "")
            options = [
                str(item.get("value") or item.get("key") or "")
                for item in list(target_step.get("decision_options") or [])
                if isinstance(item, dict)
            ]
            if not step_decision_key and not target_step.get("decision_prompt"):
                raise VaultError(
                    "sop_decision_step_required",
                    "当前步骤不需要教师决定",
                    status_code=409,
                )
            if decision_key and str(decision_key) != step_decision_key:
                raise VaultError(
                    "sop_decision_step_mismatch",
                    "教师决定与当前步骤不匹配",
                    status_code=409,
                )
            if options and str(selected_option or "") not in options:
                raise VaultError(
                    "sop_decision_option_invalid",
                    "请选择当前步骤提供的决定选项",
                    status_code=422,
                )
            decision_key = step_decision_key or None

        previous_occurrence_id = str(before["occurrence_id"])

        def enqueue_projection(connection: Any, vmk: bytes) -> None:
            if command == "reopen":
                self.projections.enqueue(
                    connection,
                    vmk=vmk,
                    source_kind="sensitive_affair",
                    source_id=affair_id,
                    occurrence_id=previous_occurrence_id,
                    state="completed",
                    due_date=None,
                )
            occurrence = connection.execute(
                """
                SELECT occurrence_id FROM affair_occurrences
                WHERE affair_id = ? ORDER BY sequence DESC LIMIT 1
                """,
                (affair_id,),
            ).fetchone()
            counts = connection.execute(
                """
                SELECT
                  SUM(CASE WHEN state = 'in_progress' THEN 1 ELSE 0 END) in_progress_count,
                  SUM(CASE WHEN state IN ('ready','in_progress','waiting') THEN 1 ELSE 0 END) current_count,
                  SUM(CASE WHEN state = 'waiting' THEN 1 ELSE 0 END) waiting_count
                FROM step_instances WHERE occurrence_id = ?
                """,
                (str(occurrence["occurrence_id"]),),
            ).fetchone()
            affair_row = connection.execute(
                "SELECT state FROM affairs WHERE affair_id = ?",
                (affair_id,),
            ).fetchone()
            current_count = int(counts["current_count"] or 0)
            if str(affair_row["state"]) == "closed":
                state = "completed"
            elif str(affair_row["state"]) == "discarded":
                state = "cancelled"
            elif int(counts["in_progress_count"] or 0):
                state = "in_progress"
            elif current_count and int(counts["waiting_count"] or 0) == current_count:
                state = "waiting"
            else:
                state = "pending"
            self.projections.enqueue(
                connection,
                vmk=vmk,
                source_kind="sensitive_affair",
                source_id=affair_id,
                occurrence_id=str(occurrence["occurrence_id"]),
                state=state,
                due_date=None,
            )

        if command == "complete_step":
            changed = self.sop.complete_step(
                token=token,
                affair_id=affair_id,
                step_instance_id=str(step_instance_id or ""),
                operation_id=operation_id,
                revision=int(target_step["revision"]),
                outcome=str(outcome or "completed"),
                result=str(result or ""),
                transaction_hook=enqueue_projection,
            )
        elif command == "teacher_decision":
            changed = self.sop.record_decision(
                token=token,
                affair_id=affair_id,
                operation_id=operation_id,
                decision_kind=decision_kind or "teacher",
                summary=str(summary or ""),
                step_instance_id=str(target_step["step_instance_id"]),
                decision_key=decision_key,
                selected_option=selected_option,
                expected_workspace_revision=expected_revision,
                transaction_hook=enqueue_projection,
            )
        elif command == "close":
            changed = self.sop.close_affair(
                token=token,
                affair_id=affair_id,
                operation_id=operation_id,
                revision=int(before["revision"]),
                closure_summary=str(summary or ""),
                transaction_hook=enqueue_projection,
            )
        elif command == "reopen":
            changed = self.sop.reopen_affair(
                token=token,
                affair_id=affair_id,
                operation_id=operation_id,
                revision=int(before["revision"]),
                reason=str(reason or ""),
                transaction_hook=enqueue_projection,
            )
        elif command == "discard":
            changed = self.sop.discard_affair(
                token=token,
                affair_id=affair_id,
                operation_id=operation_id,
                revision=int(before["revision"]),
                reason=str(reason or ""),
                transaction_hook=enqueue_projection,
            )
        self.projections.drain(token=token)
        return self.read(token=token, affair_id=affair_id)

    @staticmethod
    def _workspace_revision(connection: Any, affair_id: str) -> int:
        exists = connection.execute(
            "SELECT 1 FROM affairs WHERE affair_id = ?",
            (affair_id,),
        ).fetchone()
        if exists is None:
            raise VaultError("sop_affair_not_found", "事务不存在", status_code=404)
        count = connection.execute(
            "SELECT COUNT(*) FROM affair_events WHERE affair_id = ?",
            (affair_id,),
        ).fetchone()[0]
        return max(1, int(count))

    @staticmethod
    def _projection_state(
        connection: Any,
        *,
        affair_id: str,
        occurrence_id: str,
    ) -> str:
        row = connection.execute(
            """
            SELECT (SELECT COUNT(*)
                    FROM sensitive_work_projection_outbox o
                    WHERE o.group_id = g.group_id
                      AND o.state = 'pending') pending_count
            FROM sensitive_work_groups g
            WHERE g.source_kind = 'sensitive_affair'
              AND g.source_id = ? AND g.occurrence_id = ?
            LIMIT 1
            """,
            (affair_id, occurrence_id),
        ).fetchone()
        return "applied" if row is not None and int(row["pending_count"] or 0) == 0 else "pending"

    @staticmethod
    def _validate_operation(operation_id: str) -> None:
        if _OPERATION.fullmatch(str(operation_id or "")) is None:
            raise VaultError("vault_operation_id_invalid", "操作编号无效", status_code=422)


__all__ = ["AffairWorkspace"]
