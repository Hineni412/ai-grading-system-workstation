from __future__ import annotations

import hashlib
import re
from contextlib import closing
from datetime import UTC, date, datetime
from typing import Any, Callable
from uuid import uuid4

from .crypto import canonical_json
from .encrypted_database import EncryptedDatabase
from .errors import VaultError
from .secure_repository import EncryptedObjectRepository
from .work_graph import WorkGraph


_SOURCE_KINDS = {"sensitive_affair", "attention_followup", "student_support"}
_STATES = {"pending", "in_progress", "waiting", "completed", "cancelled"}
_ID = re.compile(r"[A-Za-z0-9_-]{8,128}")


def _iso() -> str:
    return datetime.now(UTC).isoformat()


def _date(value: str | None) -> str | None:
    if value is None or not str(value).strip():
        return None
    try:
        return date.fromisoformat(str(value).strip()).isoformat()
    except ValueError as exc:
        raise VaultError(
            "sensitive_projection_date_invalid",
            "敏感事项复查日期无效",
            status_code=422,
        ) from exc


class SensitiveWorkProjection:
    """Own the encrypted mapping and recoverable projection outbox.

    The ordinary work database sees only the fixed-title envelope. Real source
    identifiers stay inside the encrypted database and are returned only after
    a protected resolve call.
    """

    def __init__(
        self,
        database: EncryptedDatabase,
        repository: EncryptedObjectRepository,
        key_provider: Callable[[str], bytes],
        work: WorkGraph,
    ) -> None:
        self.database = database
        self.repository = repository
        self._key_provider = key_provider
        self.work = work

    def upsert(
        self,
        *,
        token: str,
        source_kind: str,
        source_id: str,
        occurrence_id: str | None = None,
        state: str,
        due_date: str | None,
    ) -> dict[str, object]:
        vmk = self._key_provider(token)
        with closing(self.database.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                queued = self.enqueue(
                    connection,
                    vmk=vmk,
                    source_kind=source_kind,
                    source_id=source_id,
                    occurrence_id=occurrence_id,
                    state=state,
                    due_date=due_date,
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        self.drain(token=token)
        return self.read_group(token=token, group_id=str(queued["group_id"]))

    def enqueue(
        self,
        connection: Any,
        *,
        vmk: bytes,
        source_kind: str,
        source_id: str,
        occurrence_id: str | None = None,
        state: str,
        due_date: str | None,
    ) -> dict[str, object]:
        """Write the encrypted mapping and outbox into the caller's transaction."""
        if source_kind not in _SOURCE_KINDS or state not in _STATES:
            raise VaultError(
                "sensitive_projection_contract_invalid",
                "敏感事项投影未通过合同校验",
                status_code=422,
            )
        if _ID.fullmatch(source_id) is None:
            raise VaultError(
                "sensitive_projection_source_invalid",
                "敏感事项来源编号无效",
                status_code=422,
            )
        normalized_occurrence = str(occurrence_id or "")
        normalized_due = _date(due_date)
        timestamp = _iso()
        row = connection.execute(
            """
            SELECT * FROM sensitive_work_groups
            WHERE source_kind = ? AND source_id = ? AND occurrence_id = ?
            """,
            (source_kind, source_id, normalized_occurrence),
        ).fetchone()
        if row is None:
            group_id = uuid4().hex
            projection_id = uuid4().hex
            revision = 1
            connection.execute(
                """
                INSERT INTO sensitive_work_groups (
                    group_id, source_kind, source_id, occurrence_id,
                    projection_id, group_revision, state, due_date,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
                """,
                (
                    group_id,
                    source_kind,
                    source_id,
                    normalized_occurrence,
                    projection_id,
                    state,
                    normalized_due,
                    timestamp,
                    timestamp,
                ),
            )
        else:
            group_id = str(row["group_id"])
            projection_id = str(row["projection_id"])
            if str(row["state"]) == state and (
                None if row["due_date"] is None else str(row["due_date"])
            ) == normalized_due:
                return {
                    "group_id": group_id,
                    "projection_id": projection_id,
                    "group_revision": int(row["group_revision"]),
                    "queued": False,
                }
            revision = int(row["group_revision"]) + 1
            connection.execute(
                """
                UPDATE sensitive_work_groups
                SET group_revision = ?, state = ?, due_date = ?, updated_at = ?
                WHERE group_id = ?
                """,
                (revision, state, normalized_due, timestamp, group_id),
            )
        envelope = {
            "projection_id": projection_id,
            "projection_type": source_kind,
            "state": state,
            "due_date": normalized_due,
            "source_revision": revision,
        }
        fingerprint = hashlib.sha256(canonical_json(envelope)).hexdigest()
        envelope["envelope_fingerprint"] = fingerprint
        object_id = f"projection-envelope-{uuid4().hex}"
        self.repository.put(
            connection,
            vmk=vmk,
            object_id=object_id,
            object_type="sensitive_work_projection_envelope",
            payload=envelope,
        )
        connection.execute(
            """
            INSERT INTO sensitive_work_projection_outbox (
                event_id, group_id, source_revision,
                envelope_fingerprint, envelope_object_id, state,
                attempts, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'pending', 0, ?, ?)
            """,
            (
                uuid4().hex,
                group_id,
                revision,
                fingerprint,
                object_id,
                timestamp,
                timestamp,
            ),
        )
        return {
            "group_id": group_id,
            "projection_id": projection_id,
            "group_revision": revision,
            "queued": True,
        }

    def write_tombstone(
        self,
        connection: Any,
        *,
        vmk: bytes,
        group_row: Any,
    ) -> dict[str, object]:
        """在调用方事务内取消该投影组并入队墓碑信封。

        普通工作库消费墓碑信封时会物理删除对应日历节点；outbox 保证
        普通库暂时不可用时之后仍会补投递，跨库不要求原子事务。
        """
        group_id = str(group_row["group_id"])
        revision = int(group_row["group_revision"]) + 1
        envelope = {
            "projection_id": str(group_row["projection_id"]),
            "projection_type": str(group_row["source_kind"]),
            "state": "tombstoned",
            "due_date": None,
            "source_revision": revision,
        }
        fingerprint = hashlib.sha256(canonical_json(envelope)).hexdigest()
        envelope["envelope_fingerprint"] = fingerprint
        object_id = f"projection-envelope-{uuid4().hex}"
        timestamp = _iso()
        self.repository.put(
            connection,
            vmk=vmk,
            object_id=object_id,
            object_type="sensitive_work_projection_envelope",
            payload=envelope,
        )
        connection.execute(
            "UPDATE sensitive_work_groups SET group_revision = ?, state = 'cancelled', updated_at = ? WHERE group_id = ?",
            (revision, timestamp, group_id),
        )
        connection.execute(
            """
            INSERT INTO sensitive_work_projection_outbox (
                event_id, group_id, source_revision,
                envelope_fingerprint, envelope_object_id, state,
                attempts, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'pending', 0, ?, ?)
            """,
            (
                uuid4().hex,
                group_id,
                revision,
                fingerprint,
                object_id,
                timestamp,
                timestamp,
            ),
        )
        return {"tombstoned": True, "group_id": group_id}

    def tombstone(self, *, token: str, group_id: str) -> dict[str, object]:
        vmk = self._key_provider(token)
        with closing(self.database.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT * FROM sensitive_work_groups WHERE group_id = ?",
                    (group_id,),
                ).fetchone()
                if row is None:
                    raise VaultError(
                        "sensitive_projection_not_found",
                        "敏感事项投影不存在",
                        status_code=404,
                    )
                self.write_tombstone(connection, vmk=vmk, group_row=row)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        self.drain(token=token)
        return {"tombstoned": True, "group_id": group_id}

    def drain(self, *, token: str, limit: int = 100) -> dict[str, int]:
        vmk = self._key_provider(token)
        with closing(self.database.connect()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM sensitive_work_projection_outbox
                WHERE state = 'pending'
                ORDER BY created_at, event_id LIMIT ?
                """,
                (max(1, min(int(limit), 500)),),
            ).fetchall()
        applied = 0
        pending = 0
        for row in rows:
            event_id = str(row["event_id"])
            try:
                with closing(self.database.connect()) as connection:
                    envelope, _revision = self.repository.get(
                        connection,
                        vmk=vmk,
                        object_id=str(row["envelope_object_id"]),
                    )
                self.work.apply_projection_envelope(envelope)
            except Exception:
                pending += 1
                with closing(self.database.connect()) as connection:
                    with connection:
                        connection.execute(
                            """
                            UPDATE sensitive_work_projection_outbox
                            SET attempts = attempts + 1, updated_at = ?
                            WHERE event_id = ?
                            """,
                            (_iso(), event_id),
                        )
                continue
            with closing(self.database.connect()) as connection:
                with connection:
                    connection.execute(
                        """
                        UPDATE sensitive_work_projection_outbox
                        SET state = 'applied', attempts = attempts + 1, updated_at = ?
                        WHERE event_id = ?
                        """,
                        (_iso(), event_id),
                    )
            applied += 1
        return {"applied": applied, "pending": pending}

    def read_group(self, *, token: str, group_id: str) -> dict[str, object]:
        self._key_provider(token)
        with closing(self.database.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM sensitive_work_groups WHERE group_id = ?",
                (group_id,),
            ).fetchone()
            if row is None:
                raise VaultError(
                    "sensitive_projection_not_found",
                    "敏感事项投影不存在",
                    status_code=404,
                )
            pending = connection.execute(
                "SELECT COUNT(*) FROM sensitive_work_projection_outbox WHERE group_id = ? AND state = 'pending'",
                (group_id,),
            ).fetchone()[0]
        return {
            "group_id": str(row["group_id"]),
            "projection_id": str(row["projection_id"]),
            "source_kind": str(row["source_kind"]),
            "group_revision": int(row["group_revision"]),
            "state": str(row["state"]),
            "due_date": None if row["due_date"] is None else str(row["due_date"]),
            "projection_state": "pending" if int(pending) else "applied",
        }

    def read_source_group(
        self,
        *,
        token: str,
        source_kind: str,
        source_id: str,
        occurrence_id: str | None = None,
    ) -> dict[str, object]:
        self._key_provider(token)
        with closing(self.database.connect()) as connection:
            row = connection.execute(
                """
                SELECT group_id FROM sensitive_work_groups
                WHERE source_kind = ? AND source_id = ? AND occurrence_id = ?
                """,
                (source_kind, source_id, str(occurrence_id or "")),
            ).fetchone()
        if row is None:
            raise VaultError(
                "sensitive_projection_not_found",
                "敏感事项投影不存在",
                status_code=404,
            )
        return self.read_group(token=token, group_id=str(row["group_id"]))

    def postpone(
        self,
        *,
        token: str,
        projection_id: str,
        due_date: str,
    ) -> dict[str, object]:
        """延后提醒：只更新投影到期日，不改来源记录或计划本身。"""
        vmk = self._key_provider(token)
        normalized_due = _date(due_date)
        if normalized_due is None:
            raise VaultError(
                "sensitive_projection_date_invalid",
                "请选择新的提醒日期",
                status_code=422,
            )
        with closing(self.database.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT * FROM sensitive_work_groups WHERE projection_id = ?",
                    (projection_id,),
                ).fetchone()
                if row is None:
                    raise VaultError(
                        "sensitive_projection_not_found",
                        "跟进提醒不存在",
                        status_code=404,
                    )
                if str(row["state"]) == "cancelled":
                    raise VaultError(
                        "sensitive_projection_dismissed",
                        "该提醒已关闭，不能延后",
                        status_code=409,
                    )
                queued = self.enqueue(
                    connection,
                    vmk=vmk,
                    source_kind=str(row["source_kind"]),
                    source_id=str(row["source_id"]),
                    occurrence_id=str(row["occurrence_id"] or "") or None,
                    state="pending",
                    due_date=normalized_due,
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        self.drain(token=token)
        return self.read_group(token=token, group_id=str(queued["group_id"]))

    def dismiss(self, *, token: str, projection_id: str) -> dict[str, object]:
        """不再跟进：关闭该提醒，不清除来源档案内容。"""
        self._key_provider(token)
        with closing(self.database.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM sensitive_work_groups WHERE projection_id = ?",
                (projection_id,),
            ).fetchone()
        if row is None:
            raise VaultError(
                "sensitive_projection_not_found",
                "跟进提醒不存在",
                status_code=404,
            )
        if str(row["state"]) == "cancelled":
            return {"dismissed": True, "group_id": str(row["group_id"])}
        result = self.tombstone(token=token, group_id=str(row["group_id"]))
        return {**result, "dismissed": True}

    def resolve(self, *, token: str, projection_id: str) -> dict[str, object]:
        self._key_provider(token)
        self.drain(token=token)
        with closing(self.database.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM sensitive_work_groups WHERE projection_id = ?",
                (projection_id,),
            ).fetchone()
            if row is None:
                return {"gone": True}
            source_kind = str(row["source_kind"])
            source_id = str(row["source_id"])
            occurrence = str(row["occurrence_id"] or "")
            if source_kind == "student_support" and occurrence == "record-review":
                table, column = "support_records", "record_id"
            elif source_kind == "student_support" and occurrence == "plan-review":
                table, column = "support_plans", "support_plan_id"
            elif source_kind == "student_support" and occurrence.startswith(
                "profile-stale-"
            ):
                table, column = "student_subject_links", "subject_id"
            else:
                table = {
                    "sensitive_affair": "affairs",
                    "attention_followup": "attention_cards",
                    "student_support": "student_card_entries",
                }[source_kind]
                column = {
                    "sensitive_affair": "affair_id",
                    "attention_followup": "attention_card_id",
                    "student_support": "entry_id",
                }[source_kind]
            exists = connection.execute(
                f"SELECT * FROM {table} WHERE {column} = ?",
                (source_id,),
            ).fetchone()
        if exists is None:
            self.tombstone(token=token, group_id=str(row["group_id"]))
            return {"gone": True}
        target = {
            "sensitive_affair": {"surface": "affairs", "target_kind": "affair"},
            "attention_followup": {"surface": "students", "panel": "academic", "target_kind": "attention"},
            "student_support": {"surface": "students", "panel": "support", "target_kind": "student_card"},
        }[source_kind]
        subject_id = None
        if source_kind in {"attention_followup", "student_support"}:
            subject_id = None if exists["subject_id"] is None else str(exists["subject_id"])
        return {**target, "target_id": source_id, "subject_id": subject_id, "gone": False}


__all__ = ["SensitiveWorkProjection"]
