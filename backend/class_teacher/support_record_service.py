from __future__ import annotations

import hashlib
import hmac
import json
import re
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any, Callable
from uuid import uuid4
from zoneinfo import ZoneInfo

from .encrypted_database import EncryptedDatabase
from .errors import VaultError
from .roster_ref import student_stable_ref
from .secure_repository import EncryptedObjectRepository
from .sensitive_work_projection import SensitiveWorkProjection

if TYPE_CHECKING:
    from .student_card_service import StudentCardService


_RECORD_KINDS = {
    "fact",
    "student_statement",
    "reported_statement",
    "teacher_observation",
    "provisional_judgment",
    "professional_conclusion",
    "ai_draft",
}
_EXPIRING_KINDS = {"teacher_observation", "provisional_judgment"}
_LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")


def _iso() -> str:
    return datetime.now(UTC).isoformat()


def _normalize_datetime(value: str | None, label: str) -> str | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise VaultError(
            "support_datetime_invalid",
            f"{label}无效",
            status_code=422,
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_LOCAL_TIMEZONE)
    return parsed.astimezone(UTC).isoformat()


def _followup_due_date(value: str | None) -> str | None:
    """把存库的 UTC 复查时间转回本时区日期，作为跟进提醒到期日。"""
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(_LOCAL_TIMEZONE).date().isoformat()


class SupportRecordService:
    def __init__(
        self,
        database: EncryptedDatabase,
        repository: EncryptedObjectRepository,
        key_provider: Callable[[str], bytes],
        projections: SensitiveWorkProjection | None = None,
    ) -> None:
        self.database = database
        self.repository = repository
        self._key_provider = key_provider
        self.projections = projections
        # 由 VaultService 在装配后注入；方案完成评「有效」时回写学生当前档案。
        self.student_cards: StudentCardService | None = None

    def create_subject(
        self,
        *,
        token: str,
        operation_id: str,
        source_student_id: str,
        display_name: str,
        class_label: str | None,
    ) -> dict[str, object]:
        vmk = self._key_provider(token)
        replay = self._idempotent(operation_id, "support.subject.create")
        if replay is not None:
            return self.get_subject(
                token=token,
                subject_id=str(replay["subject_id"]),
            )
        with closing(self.database.connect()) as connection:
            with connection:
                subject_id = self.ensure_subject_in_connection(
                    connection,
                    vmk=vmk,
                    source_student_id=source_student_id,
                    display_name=display_name,
                    class_label=class_label,
                )
                self._remember(
                    connection,
                    operation_id,
                    "support.subject.create",
                    {"subject_id": subject_id},
                )
        return self.get_subject(token=token, subject_id=subject_id)

    def create_subject_for_roster_source(
        self,
        *,
        token: str,
        operation_id: str,
        source_student_id: str,
        legacy_student_code: str,
        display_name: str,
        class_label: str | None,
    ) -> dict[str, object]:
        """Attach the roster source identity keyed by its stable ref (班级|学号/姓名)."""
        vmk = self._key_provider(token)
        replay = self._idempotent(operation_id, "support.subject.create")
        if replay is not None:
            return self.get_subject(
                token=token,
                subject_id=str(replay["subject_id"]),
            )
        with closing(self.database.connect()) as connection:
            with connection:
                subject_id = self.ensure_roster_subject_in_connection(
                    connection,
                    vmk=vmk,
                    source_student_id=source_student_id,
                    legacy_student_code=legacy_student_code,
                    display_name=display_name,
                    class_label=class_label,
                )
                self._remember(
                    connection,
                    operation_id,
                    "support.subject.create",
                    {"subject_id": subject_id},
                )
        return self.get_subject(token=token, subject_id=subject_id)

    def ensure_roster_subject_in_connection(
        self,
        connection: Any,
        *,
        vmk: bytes,
        source_student_id: str,
        legacy_student_code: str,
        display_name: str,
        class_label: str | None,
    ) -> str:
        """Attach the roster identity keyed by its stable ref (班级|学号/姓名)."""
        return self.ensure_subject_in_connection(
            connection,
            vmk=vmk,
            source_student_id=source_student_id,
            display_name=display_name,
            class_label=class_label,
            student_code=legacy_student_code,
        )

    def ensure_subject_in_connection(
        self,
        connection: Any,
        *,
        vmk: bytes,
        source_student_id: str,
        display_name: str,
        class_label: str | None,
        student_code: str | None = None,
    ) -> str:
        """Return one identity while leaving commit/rollback to the caller."""
        source_id = self._text(source_student_id, "来源学生编号", 240)
        clean_name = self._text(display_name, "显示名称", 240)
        # 花名册链路传入学籍号：稳定标识为 班级|学号（学号空时为 班级|姓名）。
        # 手工建档没有学籍号概念，来源编号本身即稳定键。
        code = str(student_code).strip() if student_code is not None else source_id
        fingerprint = student_stable_ref(
            class_label=class_label,
            student_code=code,
            display_name=clean_name,
        )
        existing = connection.execute(
            "SELECT subject_id FROM student_subject_links WHERE source_fingerprint = ?",
            (fingerprint,),
        ).fetchone()
        if existing is not None:
            return str(existing["subject_id"])

        subject_id = uuid4().hex
        object_id = f"student-subject-{subject_id}"
        timestamp = _iso()
        self.repository.put(
            connection,
            vmk=vmk,
            object_id=object_id,
            object_type="student_subject",
            payload={
                "source_student_id": source_id,
                "display_name": clean_name,
                "class_label": str(class_label or "").strip() or None,
                "identity_snapshot_at": timestamp,
            },
        )
        connection.execute(
            """
            INSERT INTO student_subject_links (
                subject_id, source_fingerprint, payload_object_id,
                state, created_at, updated_at
            ) VALUES (?, ?, ?, 'active', ?, ?)
            """,
            (subject_id, fingerprint, object_id, timestamp, timestamp),
        )
        return subject_id

    def get_subject(self, *, token: str, subject_id: str) -> dict[str, object]:
        vmk = self._key_provider(token)
        with closing(self.database.connect()) as connection:
            row = self._subject_row(connection, subject_id)
            payload, revision = self.repository.get(
                connection,
                vmk=vmk,
                object_id=str(row["payload_object_id"]),
            )
        return {
            "subject_id": subject_id,
            "revision": revision,
            "state": str(row["state"]),
            **payload,
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }

    def list_subjects(self, *, token: str) -> dict[str, object]:
        vmk = self._key_provider(token)
        with closing(self.database.connect()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM student_subject_links
                WHERE state = 'active' ORDER BY created_at
                """
            ).fetchall()
            items = []
            for row in rows:
                payload, revision = self.repository.get(
                    connection,
                    vmk=vmk,
                    object_id=str(row["payload_object_id"]),
                )
                items.append(
                    {
                        "subject_id": str(row["subject_id"]),
                        "revision": revision,
                        "state": str(row["state"]),
                        **payload,
                        "created_at": str(row["created_at"]),
                        "updated_at": str(row["updated_at"]),
                    }
                )
        return {"items": items}

    def update_subject(
        self,
        *,
        token: str,
        subject_id: str,
        operation_id: str,
        revision: int,
        display_name: str,
        class_label: str | None,
    ) -> dict[str, object]:
        vmk = self._key_provider(token)
        replay = self._idempotent(operation_id, "support.subject.update")
        if replay is not None:
            return self.get_subject(token=token, subject_id=subject_id)
        clean_name = self._text(display_name, "显示名称", 240)
        with closing(self.database.connect()) as connection:
            with connection:
                row = self._subject_row(connection, subject_id)
                payload, current_revision = self.repository.get(
                    connection,
                    vmk=vmk,
                    object_id=str(row["payload_object_id"]),
                )
                if current_revision != revision:
                    self._revision_conflict("学生身份快照")
                payload.update(
                    {
                        "display_name": clean_name,
                        "class_label": (
                            str(class_label or "").strip() or None
                        ),
                        "identity_snapshot_at": _iso(),
                    }
                )
                self.repository.put(
                    connection,
                    vmk=vmk,
                    object_id=str(row["payload_object_id"]),
                    object_type="student_subject",
                    payload=payload,
                    expected_revision=revision,
                )
                connection.execute(
                    """
                    UPDATE student_subject_links SET updated_at = ?
                    WHERE subject_id = ?
                    """,
                    (_iso(), subject_id),
                )
                self._remember(
                    connection,
                    operation_id,
                    "support.subject.update",
                    {"subject_id": subject_id},
                )
        return self.get_subject(token=token, subject_id=subject_id)

    def _tombstone_followup(
        self,
        *,
        token: str,
        source_id: str,
        occurrence_id: str,
    ) -> None:
        """关闭指定来源的跟进提醒；没有提醒或已关闭时静默跳过。"""
        if self.projections is None:
            return
        try:
            group = self.projections.read_source_group(
                token=token,
                source_kind="student_support",
                source_id=source_id,
                occurrence_id=occurrence_id,
            )
        except VaultError as exc:
            if exc.code == "sensitive_projection_not_found":
                return
            raise
        if str(group["state"]) == "cancelled":
            return
        self.projections.tombstone(token=token, group_id=str(group["group_id"]))

    def _active_profile_stale_groups(self, subject_id: str) -> list[str]:
        with closing(self.database.connect()) as connection:
            rows = connection.execute(
                """
                SELECT group_id FROM sensitive_work_groups
                WHERE source_kind = 'student_support'
                  AND source_id = ?
                  AND occurrence_id LIKE 'profile-stale-%'
                  AND state != 'cancelled'
                """,
                (subject_id,),
            ).fetchall()
        return [str(row["group_id"]) for row in rows]

    def create_record(
        self,
        *,
        token: str,
        operation_id: str,
        subject_id: str,
        record_kind: str,
        content: str,
        scene: str,
        source: str,
        basis: str | None,
        counterexample: str | None,
        category: str | None,
        observed_at: str,
        review_at: str | None,
        expires_at: str | None,
        plan_id: str | None = None,
        subject_identity: dict[str, str] | None = None,
        transaction_hook: Callable[[Any, bytes, str], None] | None = None,
    ) -> dict[str, object]:
        vmk = self._key_provider(token)
        replay = self._idempotent(operation_id, "support.record.create")
        if replay is not None:
            return self.get_record(
                token=token,
                record_id=str(replay["record_id"]),
            )
        with closing(self.database.connect()) as connection:
            with connection:
                resolved_subject_id = subject_id
                if subject_identity is not None:
                    resolved_subject_id = self.ensure_subject_in_connection(
                        connection,
                        vmk=vmk,
                        source_student_id=str(subject_identity.get("source_student_id") or ""),
                        display_name=str(subject_identity.get("display_name") or ""),
                        class_label=str(subject_identity.get("class_label") or "").strip() or None,
                        student_code=str(subject_identity.get("student_code") or "").strip() or None,
                    )
                else:
                    self._subject_row(connection, resolved_subject_id)
                record_id = self.create_record_in_connection(
                    connection,
                    vmk=vmk,
                    operation_id=operation_id,
                    subject_id=resolved_subject_id,
                    record_kind=record_kind,
                    content=content,
                    scene=scene,
                    source=source,
                    basis=basis,
                    counterexample=counterexample,
                    category=category,
                    observed_at=observed_at,
                    review_at=review_at,
                    expires_at=expires_at,
                    plan_id=plan_id,
                )
                if transaction_hook is not None:
                    transaction_hook(connection, vmk, record_id)
        if self.projections is not None:
            self.projections.drain(token=token)
            # 教师已经为这名学生记下新记录，视为跟进了"仍需了解"老化提醒。
            for group_id in self._active_profile_stale_groups(resolved_subject_id):
                self.projections.tombstone(token=token, group_id=group_id)
        return self.get_record(token=token, record_id=record_id)

    def create_record_in_connection(
        self,
        connection: Any,
        *,
        vmk: bytes,
        operation_id: str,
        subject_id: str,
        record_kind: str,
        content: str,
        scene: str,
        source: str,
        basis: str | None,
        counterexample: str | None,
        category: str | None,
        observed_at: str,
        review_at: str | None,
        expires_at: str | None,
        plan_id: str | None = None,
    ) -> str:
        """Create one record inside a caller-owned domain transaction."""

        self._validate_operation_id(operation_id)
        self._subject_row(connection, subject_id)
        clean_plan_id = str(plan_id).strip() if plan_id is not None else ""
        if clean_plan_id:
            plan_row = connection.execute(
                """
                SELECT subject_id FROM support_plans
                WHERE support_plan_id = ?
                """,
                (clean_plan_id,),
            ).fetchone()
            if plan_row is None or str(plan_row["subject_id"]) != subject_id:
                raise VaultError(
                    "support_plan_invalid_for_record",
                    "记录只能挂到这名学生已有的支持方案下",
                    status_code=422,
                )
        normalized = self._normalize_record(
            record_kind=record_kind,
            content=content,
            scene=scene,
            source=source,
            basis=basis,
            counterexample=counterexample,
            category=category,
            observed_at=observed_at,
            review_at=review_at,
            expires_at=expires_at,
        )
        record_id = uuid4().hex
        self._create_record_in_connection(
            connection,
            vmk=vmk,
            record_id=record_id,
            subject_id=subject_id,
            normalized=normalized,
            plan_id=clean_plan_id or None,
        )
        self._rebuild_summary(connection, vmk, subject_id)
        if self.projections is not None and normalized["review_at"] is not None:
            self.projections.enqueue(
                connection,
                vmk=vmk,
                source_kind="student_support",
                source_id=record_id,
                occurrence_id="record-review",
                state="pending",
                due_date=_followup_due_date(normalized["review_at"]),
            )
        self._remember(
            connection,
            operation_id,
            "support.record.create",
            {"record_id": record_id},
        )
        return record_id

    def get_record(self, *, token: str, record_id: str) -> dict[str, object]:
        vmk = self._key_provider(token)
        with closing(self.database.connect()) as connection:
            row = self._record_row(connection, record_id)
            revisions = self._record_revisions(connection, vmk, record_id)
        current = revisions[-1]
        return {
            "record_id": record_id,
            "subject_id": str(row["subject_id"]),
            "record_kind": str(row["record_kind"]),
            "state": str(row["state"]),
            "current_revision": int(row["current_revision"]),
            "plan_id": str(row["plan_id"]) if row["plan_id"] else None,
            **current["payload"],
            "revision_history": revisions,
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }

    def list_records(
        self,
        *,
        token: str,
        subject_id: str,
        include_inactive: bool = True,
    ) -> dict[str, object]:
        self._key_provider(token)
        with closing(self.database.connect()) as connection:
            self._subject_row(connection, subject_id)
            query = (
                "SELECT record_id FROM support_records "
                "WHERE subject_id = ?"
            )
            parameters: list[object] = [subject_id]
            if not include_inactive:
                query += " AND state = 'active'"
            query += " ORDER BY observed_at DESC, created_at DESC"
            record_ids = [
                str(row["record_id"])
                for row in connection.execute(query, parameters).fetchall()
            ]
        return {
            "items": [
                self.get_record(token=token, record_id=record_id)
                for record_id in record_ids
            ]
        }

    def revise_record(
        self,
        *,
        token: str,
        record_id: str,
        operation_id: str,
        expected_revision: int,
        content: str,
        scene: str,
        source: str,
        basis: str | None,
        counterexample: str | None,
        category: str | None,
        observed_at: str,
        review_at: str | None,
        expires_at: str | None,
        revision_reason: str,
    ) -> dict[str, object]:
        vmk = self._key_provider(token)
        replay = self._idempotent(operation_id, "support.record.revise")
        if replay is not None:
            return self.get_record(token=token, record_id=record_id)
        with closing(self.database.connect()) as connection:
            with connection:
                row = self._record_row(connection, record_id)
                current_revision = int(row["current_revision"])
                if current_revision != expected_revision:
                    self._revision_conflict("支持记录")
                normalized = self._normalize_record(
                    record_kind=str(row["record_kind"]),
                    content=content,
                    scene=scene,
                    source=source,
                    basis=basis,
                    counterexample=counterexample,
                    category=category,
                    observed_at=observed_at,
                    review_at=review_at,
                    expires_at=expires_at,
                )
                normalized["revision_reason"] = self._text(
                    revision_reason,
                    "修订原因",
                    1000,
                )
                next_revision = current_revision + 1
                revision_id = uuid4().hex
                object_id = f"support-record-revision-{revision_id}"
                self.repository.put(
                    connection,
                    vmk=vmk,
                    object_id=object_id,
                    object_type="support_record_revision",
                    payload=normalized,
                )
                connection.execute(
                    """
                    INSERT INTO record_revisions (
                        revision_id, record_id, revision_number,
                        payload_object_id, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        revision_id,
                        record_id,
                        next_revision,
                        object_id,
                        _iso(),
                    ),
                )
                connection.execute(
                    """
                    UPDATE support_records
                    SET current_revision = ?, observed_at = ?,
                        review_at = ?, expires_at = ?, updated_at = ?
                    WHERE record_id = ?
                    """,
                    (
                        next_revision,
                        normalized["observed_at"],
                        normalized["review_at"],
                        normalized["expires_at"],
                        _iso(),
                        record_id,
                    ),
                )
                if str(row["record_kind"]) in _EXPIRING_KINDS:
                    connection.execute(
                        """
                        UPDATE observations SET category = ?
                        WHERE record_id = ?
                        """,
                        (normalized["category"], record_id),
                    )
                self._rebuild_summary(
                    connection,
                    vmk,
                    str(row["subject_id"]),
                )
                if self.projections is not None and normalized["review_at"] is not None:
                    self.projections.enqueue(
                        connection,
                        vmk=vmk,
                        source_kind="student_support",
                        source_id=record_id,
                        occurrence_id="record-review",
                        state="pending",
                        due_date=_followup_due_date(normalized["review_at"]),
                    )
                self._remember(
                    connection,
                    operation_id,
                    "support.record.revise",
                    {"record_id": record_id},
                )
        if self.projections is not None:
            if normalized["review_at"] is None:
                self._tombstone_followup(
                    token=token,
                    source_id=record_id,
                    occurrence_id="record-review",
                )
            self.projections.drain(token=token)
        return self.get_record(token=token, record_id=record_id)

    def set_record_state(
        self,
        *,
        token: str,
        record_id: str,
        operation_id: str,
        expected_revision: int,
        state: str,
        reason: str,
    ) -> dict[str, object]:
        vmk = self._key_provider(token)
        replay = self._idempotent(operation_id, "support.record.state")
        if replay is not None:
            return self.get_record(token=token, record_id=record_id)
        if state not in {"active", "withdrawn", "archived"}:
            raise VaultError(
                "support_record_state_invalid",
                "支持记录状态无效",
                status_code=422,
            )
        clean_reason = self._text(reason, "状态变更原因", 1000)
        with closing(self.database.connect()) as connection:
            with connection:
                row = self._record_row(connection, record_id)
                if int(row["current_revision"]) != expected_revision:
                    self._revision_conflict("支持记录")
                latest = self._record_revisions(
                    connection,
                    vmk,
                    record_id,
                )[-1]["payload"]
                latest = dict(latest)
                latest["state_change_reason"] = clean_reason
                latest["state_changed_to"] = state
                next_revision = int(row["current_revision"]) + 1
                revision_id = uuid4().hex
                object_id = f"support-record-revision-{revision_id}"
                self.repository.put(
                    connection,
                    vmk=vmk,
                    object_id=object_id,
                    object_type="support_record_revision",
                    payload=latest,
                )
                connection.execute(
                    """
                    INSERT INTO record_revisions (
                        revision_id, record_id, revision_number,
                        payload_object_id, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        revision_id,
                        record_id,
                        next_revision,
                        object_id,
                        _iso(),
                    ),
                )
                connection.execute(
                    """
                    UPDATE support_records
                    SET state = ?, current_revision = ?, updated_at = ?
                    WHERE record_id = ?
                    """,
                    (state, next_revision, _iso(), record_id),
                )
                self._rebuild_summary(
                    connection,
                    vmk,
                    str(row["subject_id"]),
                )
                self._remember(
                    connection,
                    operation_id,
                    "support.record.state",
                    {"record_id": record_id},
                )
        if self.projections is not None:
            if state == "active" and row["review_at"] is not None:
                self.projections.upsert(
                    token=token,
                    source_kind="student_support",
                    source_id=record_id,
                    occurrence_id="record-review",
                    state="pending",
                    due_date=_followup_due_date(str(row["review_at"])),
                )
            elif state in {"withdrawn", "archived"}:
                self._tombstone_followup(
                    token=token,
                    source_id=record_id,
                    occurrence_id="record-review",
                )
        return self.get_record(token=token, record_id=record_id)

    def confirm_ai_draft(
        self,
        *,
        token: str,
        draft_record_id: str,
        operation_id: str,
        confirmed_kind: str,
    ) -> dict[str, object]:
        replay = self._idempotent(operation_id, "support.ai.confirm")
        if replay is not None:
            return self.get_record(
                token=token,
                record_id=str(replay["record_id"]),
            )
        draft = self.get_record(token=token, record_id=draft_record_id)
        if draft["record_kind"] != "ai_draft":
            raise VaultError(
                "support_record_not_ai_draft",
                "只有 AI 草稿需要教师确认分类",
                status_code=422,
            )
        if confirmed_kind == "ai_draft" or confirmed_kind not in _RECORD_KINDS:
            raise VaultError(
                "support_record_kind_invalid",
                "请选择明确的人工确认分类",
                status_code=422,
            )
        claim = self._claim_ai_confirmation(
            draft_record_id=draft_record_id,
            operation_id=operation_id,
            confirmed_kind=confirmed_kind,
        )
        if claim["state"] == "target_created":
            result = self.get_record(
                token=token,
                record_id=str(claim["target_id"]),
            )
        else:
            result = self.create_record(
                token=token,
                operation_id=(
                    "ai-confirm-target-"
                    + hashlib.sha256(
                        draft_record_id.encode("utf-8")
                    ).hexdigest()[:32]
                ),
                subject_id=str(draft["subject_id"]),
                record_kind=confirmed_kind,
                content=str(draft["content"]),
                scene=str(draft["scene"]),
                source="教师确认的 AI 草稿",
                basis=str(draft.get("basis") or "") or None,
                counterexample=str(
                    draft.get("counterexample") or ""
                ) or None,
                category=str(draft.get("category") or "") or None,
                observed_at=str(draft["observed_at"]),
                review_at=(
                    str(draft["review_at"])
                    if draft.get("review_at")
                    else None
                ),
                expires_at=(
                    str(draft["expires_at"])
                    if draft.get("expires_at")
                    else None
                ),
            )
            self._mark_ai_confirmation_target(
                draft_record_id=draft_record_id,
                operation_id=operation_id,
                record_id=str(result["record_id"]),
            )
        current_draft = self.get_record(
            token=token,
            record_id=draft_record_id,
        )
        if current_draft["state"] != "archived":
            self.set_record_state(
                token=token,
                record_id=draft_record_id,
                operation_id=f"{operation_id}:archive-draft",
                expected_revision=int(current_draft["current_revision"]),
                state="archived",
                reason="教师已确认并生成正式分类记录",
            )
        with closing(self.database.connect()) as connection:
            with connection:
                connection.execute(
                    """
                    UPDATE confirmation_claims
                    SET state = 'completed', updated_at = ?
                    WHERE entity_kind = 'ai_draft'
                      AND entity_id = ? AND operation_id = ?
                    """,
                    (_iso(), draft_record_id, operation_id),
                )
                self._remember(
                    connection,
                    operation_id,
                    "support.ai.confirm",
                    {"record_id": str(result["record_id"])},
                )
        return result

    def _claim_ai_confirmation(
        self,
        *,
        draft_record_id: str,
        operation_id: str,
        confirmed_kind: str,
    ) -> dict[str, object]:
        with closing(self.database.connect()) as connection:
            with connection:
                existing = connection.execute(
                    """
                    SELECT state, target_kind, target_id
                    FROM confirmation_claims
                    WHERE entity_kind = 'ai_draft' AND entity_id = ?
                    """,
                    (draft_record_id,),
                ).fetchone()
                if existing is not None:
                    state = str(existing["state"])
                    if state == "completed":
                        raise VaultError(
                            "support_ai_confirmation_claimed",
                            "AI 草稿已经确认，请刷新后查看",
                            status_code=409,
                        )
                    claimed_kind = str(existing["target_kind"] or "")
                    if claimed_kind and claimed_kind != confirmed_kind:
                        raise VaultError(
                            "support_ai_confirmation_kind_changed",
                            "恢复确认时不能更改正式分类，请刷新后继续",
                            status_code=409,
                        )
                    connection.execute(
                        """
                        UPDATE confirmation_claims
                        SET operation_id = ?, updated_at = ?
                        WHERE entity_kind = 'ai_draft' AND entity_id = ?
                        """,
                        (operation_id, _iso(), draft_record_id),
                    )
                    return {
                        "state": state,
                        "target_id": existing["target_id"],
                    }
                connection.execute(
                    """
                    INSERT INTO confirmation_claims (
                        entity_kind, entity_id, operation_id,
                        state, target_kind, created_at, updated_at
                    ) VALUES ('ai_draft', ?, ?, 'claimed', ?, ?, ?)
                    """,
                    (
                        draft_record_id,
                        operation_id,
                        confirmed_kind,
                        _iso(),
                        _iso(),
                    ),
                )
        return {"state": "claimed", "target_id": None}

    def _mark_ai_confirmation_target(
        self,
        *,
        draft_record_id: str,
        operation_id: str,
        record_id: str,
    ) -> None:
        with closing(self.database.connect()) as connection:
            with connection:
                connection.execute(
                    """
                    UPDATE confirmation_claims
                    SET state = 'target_created', target_id = ?,
                        updated_at = ?
                    WHERE entity_kind = 'ai_draft'
                      AND entity_id = ? AND operation_id = ?
                    """,
                    (
                        record_id,
                        _iso(),
                        draft_record_id,
                        operation_id,
                    ),
                )

    def get_summary(
        self,
        *,
        token: str,
        subject_id: str,
        as_of: str | None = None,
    ) -> dict[str, object]:
        vmk = self._key_provider(token)
        point = _normalize_datetime(as_of, "摘要时间") or _iso()
        with closing(self.database.connect()) as connection:
            with connection:
                self._subject_row(connection, subject_id)
                return self._rebuild_summary(
                    connection,
                    vmk,
                    subject_id,
                    as_of=point,
                )

    def create_support_plan(
        self,
        *,
        token: str,
        operation_id: str,
        subject_id: str,
        goal: str,
        support_actions: list[str],
        review_at: str,
        action_id: str | None,
    ) -> dict[str, object]:
        vmk = self._key_provider(token)
        replay = self._idempotent(operation_id, "support.plan.create")
        if replay is not None:
            return self.get_support_plan(
                token=token,
                support_plan_id=str(replay["support_plan_id"]),
            )
        normalized_review = _normalize_datetime(review_at, "支持计划复查时间")
        if normalized_review is None:
            raise VaultError(
                "support_plan_review_required",
                "支持计划必须设置复查时间",
                status_code=422,
            )
        actions = [
            self._text(item, "支持行动", 1000)
            for item in support_actions
        ]
        if not actions:
            raise VaultError(
                "support_plan_actions_required",
                "支持计划至少需要一项具体行动",
                status_code=422,
            )
        plan_id = uuid4().hex
        object_id = f"support-plan-{plan_id}"
        timestamp = _iso()
        payload = {
            "goal": self._text(goal, "支持目标", 1000),
            "support_actions": actions,
            "result": None,
        }
        with closing(self.database.connect()) as connection:
            with connection:
                self._subject_row(connection, subject_id)
                if action_id and connection.execute(
                    "SELECT 1 FROM actions WHERE action_id = ?",
                    (action_id,),
                ).fetchone() is None:
                    raise VaultError(
                        "action_not_found",
                        "关联行动不存在",
                        status_code=404,
                    )
                self.repository.put(
                    connection,
                    vmk=vmk,
                    object_id=object_id,
                    object_type="support_plan",
                    payload=payload,
                )
                connection.execute(
                    """
                    INSERT INTO support_plans (
                        support_plan_id, subject_id, action_id,
                        payload_object_id, state, review_at,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'active', ?, ?, ?)
                    """,
                    (
                        plan_id,
                        subject_id,
                        action_id,
                        object_id,
                        normalized_review,
                        timestamp,
                        timestamp,
                    ),
                )
                self._remember(
                    connection,
                    operation_id,
                    "support.plan.create",
                    {"support_plan_id": plan_id},
                )
                if self.projections is not None:
                    self.projections.enqueue(
                        connection,
                        vmk=vmk,
                        source_kind="student_support",
                        source_id=plan_id,
                        occurrence_id="plan-review",
                        state="pending",
                        due_date=_followup_due_date(normalized_review),
                    )
        if self.projections is not None:
            self.projections.drain(token=token)
        return self.get_support_plan(
            token=token,
            support_plan_id=plan_id,
        )

    def get_support_plan(
        self,
        *,
        token: str,
        support_plan_id: str,
    ) -> dict[str, object]:
        vmk = self._key_provider(token)
        with closing(self.database.connect()) as connection:
            row = connection.execute(
                """
                SELECT * FROM support_plans
                WHERE support_plan_id = ?
                """,
                (support_plan_id,),
            ).fetchone()
            if row is None:
                raise VaultError(
                    "support_plan_not_found",
                    "支持计划不存在",
                    status_code=404,
                )
            payload, revision = self.repository.get(
                connection,
                vmk=vmk,
                object_id=str(row["payload_object_id"]),
            )
        return {
            "support_plan_id": support_plan_id,
            "subject_id": str(row["subject_id"]),
            "revision": revision,
            "action_id": (
                str(row["action_id"]) if row["action_id"] else None
            ),
            "state": str(row["state"]),
            "review_at": str(row["review_at"]),
            **payload,
            "outcome": payload.get("outcome"),
            "completed_at": payload.get("completed_at"),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }

    def list_support_plans(
        self,
        *,
        token: str,
        subject_id: str,
    ) -> dict[str, object]:
        self._key_provider(token)
        with closing(self.database.connect()) as connection:
            self._subject_row(connection, subject_id)
            rows = connection.execute(
                """
                SELECT support_plan_id
                FROM support_plans
                WHERE subject_id = ?
                ORDER BY created_at DESC
                """,
                (subject_id,),
            ).fetchall()
        return {
            "items": [
                self.get_support_plan(
                    token=token,
                    support_plan_id=str(row["support_plan_id"]),
                )
                for row in rows
            ]
        }

    def complete_support_plan(
        self,
        *,
        token: str,
        support_plan_id: str,
        operation_id: str,
        expected_revision: int,
        result: str,
        outcome: str | None = None,
    ) -> dict[str, object]:
        vmk = self._key_provider(token)
        replay = self._idempotent(operation_id, "support.plan.complete")
        if replay is not None:
            return self.get_support_plan(
                token=token,
                support_plan_id=support_plan_id,
            )
        if outcome not in (None, "effective", "ineffective", "continue"):
            raise VaultError(
                "support_plan_outcome_invalid",
                "支持方案效果评价无效",
                status_code=422,
            )
        clean_result = self._text(result, "支持结果", 4000)
        with closing(self.database.connect()) as connection:
            with connection:
                row = connection.execute(
                    """
                    SELECT * FROM support_plans
                    WHERE support_plan_id = ?
                    """,
                    (support_plan_id,),
                ).fetchone()
                if row is None:
                    raise VaultError(
                        "support_plan_not_found",
                        "支持计划不存在",
                        status_code=404,
                    )
                payload, revision = self.repository.get(
                    connection,
                    vmk=vmk,
                    object_id=str(row["payload_object_id"]),
                )
                if revision != expected_revision:
                    self._revision_conflict("支持计划")
                payload["result"] = clean_result
                payload["outcome"] = outcome
                payload["completed_at"] = _iso()
                self.repository.put(
                    connection,
                    vmk=vmk,
                    object_id=str(row["payload_object_id"]),
                    object_type="support_plan",
                    payload=payload,
                    expected_revision=expected_revision,
                )
                connection.execute(
                    """
                    UPDATE support_plans
                    SET state = 'completed', updated_at = ?
                    WHERE support_plan_id = ?
                    """,
                    (_iso(), support_plan_id),
                )
                if outcome == "effective":
                    self._append_verified_effective_methods(
                        connection,
                        vmk=vmk,
                        subject_id=str(row["subject_id"]),
                        support_actions=[
                            str(item)
                            for item in list(payload.get("support_actions") or [])
                        ],
                        operation_id=f"plan-effective-{support_plan_id}",
                    )
                self._remember(
                    connection,
                    operation_id,
                    "support.plan.complete",
                    {"support_plan_id": support_plan_id},
                )
        self._tombstone_followup(
            token=token,
            source_id=support_plan_id,
            occurrence_id="plan-review",
        )
        return self.get_support_plan(
            token=token,
            support_plan_id=support_plan_id,
        )

    def _append_verified_effective_methods(
        self,
        connection: Any,
        *,
        vmk: bytes,
        subject_id: str,
        support_actions: list[str],
        operation_id: str,
    ) -> None:
        """把方案中验证有效的做法并入学生当前档案的 verified_effective 支持重点。

        只合并 effective_methods（去重）；档案的摘要、维度、待了解问题、教师原话
        和草稿保持原样。没有档案时新建一份仅含该支持重点的当前档案。
        """
        if self.student_cards is None:
            raise VaultError(
                "support_plan_effective_unavailable",
                "档案回写暂时不可用，方案未完成，请稍后重试",
                status_code=503,
            )
        methods: list[str] = []
        for item in support_actions:
            text = str(item).strip()
            if text and text not in methods:
                methods.append(text)
        if not methods:
            return
        current = self.student_cards.current_profile_in_connection(
            connection,
            vmk=vmk,
            subject_id=subject_id,
        )
        existing_focus: dict[str, object] = {}
        summary = ""
        open_questions: list[str] = []
        teacher_quote = ""
        model_draft = ""
        revision: int | None = None
        if current is not None:
            revision = int(current["revision"])
            profile = current["profile"]
            assert isinstance(profile, dict)
            summary = str(profile.get("summary") or "")
            open_questions = [
                str(item) for item in list(profile.get("open_questions") or [])
            ]
            teacher_quote = str(current.get("teacher_quote") or "")
            model_draft = str(current.get("model_draft") or "")
            for item in list(profile.get("support_focus") or []):
                if (
                    isinstance(item, dict)
                    and str(item.get("key") or "") == "verified_effective"
                ):
                    existing_focus = dict(item)
        merged_methods: list[str] = []
        for item in list(existing_focus.get("effective_methods") or []) + methods:
            text = str(item).strip()
            if text and text not in merged_methods:
                merged_methods.append(text)
        focus_entry = {
            "key": "verified_effective",
            "title": str(existing_focus.get("title") or "").strip() or "已验证有效",
            "need": (
                str(existing_focus.get("need") or "").strip()
                or "行动中验证有效的做法"
            ),
            "effective_methods": merged_methods,
            "next_actions": [
                str(item)
                for item in list(existing_focus.get("next_actions") or [])
            ],
        }
        self.student_cards.upsert_current_profile_in_connection(
            connection,
            vmk=vmk,
            subject_id=subject_id,
            profile_update={
                "summary": (
                    summary or "教师正在通过支持行动积累对这名学生的认识。"
                ),
                "dimensions": [],
                "open_questions": open_questions,
                "support_focus": [focus_entry],
            },
            expected_revision=revision,
            operation_id=operation_id,
            model_operation_id=f"support-{operation_id}",
            teacher_quote=teacher_quote,
            model_draft=model_draft,
        )

    def link_observation_evidence(
        self,
        *,
        token: str,
        operation_id: str,
        observation_record_id: str,
        evidence_record_id: str,
        relation_kind: str,
    ) -> dict[str, object]:
        self._key_provider(token)
        replay = self._idempotent(
            operation_id,
            "support.observation.evidence.link",
        )
        if replay is not None:
            return replay
        if relation_kind not in {"supports", "counterexample", "context"}:
            raise VaultError(
                "support_evidence_relation_invalid",
                "观察证据关系无效",
                status_code=422,
            )
        with closing(self.database.connect()) as connection:
            with connection:
                observation = connection.execute(
                    """
                    SELECT o.observation_id, o.subject_id
                    FROM observations o
                    WHERE o.record_id = ?
                    """,
                    (observation_record_id,),
                ).fetchone()
                evidence = self._record_row(connection, evidence_record_id)
                if observation is None:
                    raise VaultError(
                        "support_observation_not_found",
                        "观察记录不存在",
                        status_code=404,
                    )
                if str(observation["subject_id"]) != str(
                    evidence["subject_id"]
                ):
                    raise VaultError(
                        "support_evidence_subject_mismatch",
                        "只能关联同一学生的支持证据",
                        status_code=422,
                    )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO observation_evidence_links (
                        observation_id, evidence_record_id,
                        relation_kind, created_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        str(observation["observation_id"]),
                        evidence_record_id,
                        relation_kind,
                        _iso(),
                    ),
                )
                result = {
                    "observation_record_id": observation_record_id,
                    "evidence_record_id": evidence_record_id,
                    "relation_kind": relation_kind,
                }
                self._remember(
                    connection,
                    operation_id,
                    "support.observation.evidence.link",
                    result,
                )
        return result

    def project_closed_affair(
        self,
        *,
        token: str,
        operation_id: str,
        subject_id: str,
        affair_id: str,
    ) -> dict[str, object]:
        vmk = self._key_provider(token)
        replay = self._idempotent(operation_id, "support.affair.project")
        if replay is not None:
            return self.get_record(
                token=token,
                record_id=str(replay["record_id"]),
            )
        with closing(self.database.connect()) as connection:
            with connection:
                self._subject_row(connection, subject_id)
                affair = connection.execute(
                    """
                    SELECT payload_object_id, state, closed_at
                    FROM affairs WHERE affair_id = ?
                    """,
                    (affair_id,),
                ).fetchone()
                if affair is None or str(affair["state"]) != "closed":
                    raise VaultError(
                        "support_affair_not_closed",
                        "只有教师确认结案的事务可以进入支持时间线",
                        status_code=422,
                    )
                affair_payload, _ = self.repository.get(
                    connection,
                    vmk=vmk,
                    object_id=str(affair["payload_object_id"]),
                )
                normalized = self._normalize_record(
                    record_kind="fact",
                    content=str(
                        affair_payload.get("closure_summary")
                        or "事务已由教师确认结案"
                    ),
                    scene="SOP 结案结果",
                    source=f"已确认事务 {affair_id}",
                    basis="教师结案摘要",
                    counterexample=None,
                    category="sop_result",
                    observed_at=str(affair["closed_at"]),
                    review_at=None,
                    expires_at=None,
                )
                normalized["source_affair_id"] = affair_id
                record_id = uuid4().hex
                self._create_record_in_connection(
                    connection,
                    vmk=vmk,
                    record_id=record_id,
                    subject_id=subject_id,
                    normalized=normalized,
                )
                self._rebuild_summary(connection, vmk, subject_id)
                self._remember(
                    connection,
                    operation_id,
                    "support.affair.project",
                    {"record_id": record_id},
                )
        return self.get_record(token=token, record_id=record_id)

    def preview_subject_deletion(
        self,
        *,
        token: str,
        subject_id: str,
    ) -> dict[str, object]:
        vmk = self._key_provider(token)
        with closing(self.database.connect()) as connection:
            subject = self._subject_row(connection, subject_id)
            identity, _ = self.repository.get(
                connection,
                vmk=vmk,
                object_id=str(subject["payload_object_id"]),
            )
            markers = {
                subject_id,
                str(identity.get("source_student_id") or ""),
                str(identity.get("display_name") or ""),
            } - {""}
            shared = sorted([
                {
                    "object_id": str(row["object_id"]),
                    "object_type": str(row["object_type"]),
                    "handling": "anonymize_or_remove_shared_text",
                }
                for row in self._shared_object_rows(
                    connection,
                    vmk=vmk,
                    markers=markers,
                )
            ], key=lambda item: (item["object_type"], item["object_id"]))
            counts = {
                "support_records": connection.execute("SELECT COUNT(*) FROM support_records WHERE subject_id = ?", (subject_id,)).fetchone()[0],
                "student_cards": connection.execute("SELECT COUNT(*) FROM student_card_entries WHERE subject_id = ?", (subject_id,)).fetchone()[0],
                "academic_evidence": connection.execute("SELECT COUNT(*) FROM subject_results WHERE subject_id = ?", (subject_id,)).fetchone()[0],
                "ai_reviews": connection.execute("SELECT COUNT(*) FROM support_ai_review_sessions WHERE subject_id = ?", (subject_id,)).fetchone()[0],
                "attention_cards": connection.execute("SELECT COUNT(*) FROM attention_cards WHERE subject_id = ?", (subject_id,)).fetchone()[0],
                "support_plans": connection.execute("SELECT COUNT(*) FROM support_plans WHERE subject_id = ?", (subject_id,)).fetchone()[0],
            }
            projection_ids = sorted(
                str(row[0])
                for row in connection.execute(
                    """
                    SELECT g.group_id FROM sensitive_work_groups g
                    WHERE (g.source_kind = 'attention_followup' AND g.source_id IN (
                        SELECT attention_card_id FROM attention_cards WHERE subject_id = ?
                    )) OR (g.source_kind = 'student_support' AND g.source_id IN (
                        SELECT entry_id FROM student_card_entries WHERE subject_id = ?
                    ))
                    """,
                    (subject_id, subject_id),
                ).fetchall()
            )
        version_payload = {
            "subject_id": subject_id,
            "shared_objects": [item["object_id"] for item in shared],
            "counts": counts,
            "projection_ids": projection_ids,
        }
        preview_version = hashlib.sha256(
            json.dumps(version_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return {
            "subject_id": subject_id,
            "shared_objects": shared,
            "shared_object_count": len(shared),
            "impact_counts": counts,
            "projection_count": len(projection_ids),
            "preview_version": preview_version,
            "delete_confirmation_phrase": "确认完整删除学生支持数据",
        }

    def delete_subject(
        self,
        *,
        token: str,
        subject_id: str,
        operation_id: str,
        confirmation_phrase: str,
        preview_version: str | None = None,
    ) -> dict[str, object]:
        self._key_provider(token)
        if re.fullmatch(r"[A-Za-z0-9_-]{8,128}", operation_id) is None:
            raise VaultError(
                "vault_operation_id_invalid",
                "操作编号无效，请刷新页面后重试",
                status_code=422,
            )
        replay = self._idempotent(
            operation_id,
            "support.subject.delete",
        )
        if replay is not None:
            return replay
        preview = self.preview_subject_deletion(
            token=token,
            subject_id=subject_id,
        )
        if preview_version is not None and preview_version != preview["preview_version"]:
            raise VaultError(
                "support_delete_preview_changed",
                "删除影响已经变化，请重新查看并确认",
                status_code=409,
                details=preview,
            )
        live_database = self.database
        snapshot = live_database.snapshot_bytes()
        with TemporaryDirectory(
            prefix=".subject-delete-candidate-",
            dir=live_database.root,
        ) as candidate_root:
            candidate_database = live_database.isolated_copy(
                snapshot,
                root=Path(candidate_root),
            )
            self.database = candidate_database
            try:
                result = self._delete_subject_once(
                    token=token,
                    subject_id=subject_id,
                    operation_id=operation_id,
                    confirmation_phrase=confirmation_phrase,
                )
                candidate_snapshot = candidate_database.snapshot_bytes()
            finally:
                self.database = live_database
        transaction: Path | None = None
        tombstoned: list[dict[str, object]] = []
        try:
            if self.projections is not None:
                with closing(live_database.connect()) as connection:
                    rows = connection.execute(
                        """
                        SELECT g.* FROM sensitive_work_groups g
                        WHERE (g.source_kind = 'attention_followup' AND g.source_id IN (
                            SELECT attention_card_id FROM attention_cards WHERE subject_id = ?
                        )) OR (g.source_kind = 'student_support' AND (
                            g.source_id IN (
                                SELECT entry_id FROM student_card_entries WHERE subject_id = ?
                            ) OR (g.occurrence_id = 'record-review' AND g.source_id IN (
                                SELECT record_id FROM support_records WHERE subject_id = ?
                            )) OR (g.occurrence_id = 'plan-review' AND g.source_id IN (
                                SELECT support_plan_id FROM support_plans WHERE subject_id = ?
                            )) OR (g.occurrence_id LIKE 'profile-stale-%' AND g.source_id = ?)
                        ))
                        """,
                        (subject_id, subject_id, subject_id, subject_id, subject_id),
                    ).fetchall()
                    tombstoned = [dict(row) for row in rows]
                for group in tombstoned:
                    self.projections.tombstone(token=token, group_id=str(group["group_id"]))
            transaction = live_database.create_subject_delete_transaction(
                operation_id=operation_id,
                database_snapshot=snapshot,
            )
            live_database.replace_from_snapshot_atomically(
                candidate_snapshot
            )
            live_database.commit_subject_delete_transaction(transaction)
            return result
        except Exception:
            if transaction is not None and transaction.exists():
                live_database.recover_interrupted_operations()
            if self.projections is not None:
                for group in tombstoned:
                    try:
                        self.projections.upsert(
                            token=token,
                            source_kind=str(group["source_kind"]),
                            source_id=str(group["source_id"]),
                            occurrence_id=str(group["occurrence_id"] or "") or None,
                            state=str(group["state"]),
                            due_date=None if group["due_date"] is None else str(group["due_date"]),
                        )
                    except Exception:
                        pass
            raise

    def _delete_subject_once(
        self,
        *,
        token: str,
        subject_id: str,
        operation_id: str,
        confirmation_phrase: str,
    ) -> dict[str, object]:
        vmk = self._key_provider(token)
        replay = self._idempotent(operation_id, "support.subject.delete")
        if replay is not None:
            return replay
        if confirmation_phrase != "确认完整删除学生支持数据":
            raise VaultError(
                "support_delete_confirmation_required",
                "请输入“确认完整删除学生支持数据”后再删除",
                status_code=422,
            )
        with closing(self.database.connect()) as connection:
            with connection:
                subject = self._subject_row(connection, subject_id)
                self._redact_shared_objects(
                    connection,
                    vmk=vmk,
                    subject_id=subject_id,
                    subject=subject,
                )
                assessment_ids = [
                    str(row[0])
                    for row in connection.execute(
                        """
                        SELECT DISTINCT assessment_id FROM subject_results
                        WHERE subject_id = ?
                        """,
                        (subject_id,),
                    ).fetchall()
                ]
                object_ids = {
                    str(subject["payload_object_id"]),
                }
                linked_affair_participants = connection.execute(
                    """
                    SELECT p.participant_id, p.payload_object_id
                    FROM affair_student_links l
                    JOIN affair_participants p
                      ON p.participant_id = l.participant_id
                    WHERE l.subject_id = ?
                    """,
                    (subject_id,),
                ).fetchall()
                linked_affair_participant_ids = [
                    str(row["participant_id"])
                    for row in linked_affair_participants
                ]
                object_ids.update(
                    str(row["payload_object_id"])
                    for row in linked_affair_participants
                )
                object_ids.update(
                    str(row[0])
                    for row in connection.execute(
                        """
                        SELECT rr.payload_object_id
                        FROM record_revisions rr
                        JOIN support_records sr ON sr.record_id = rr.record_id
                        WHERE sr.subject_id = ?
                        """,
                        (subject_id,),
                    ).fetchall()
                )
                projection_groups = connection.execute(
                    """
                    SELECT group_id FROM sensitive_work_groups g
                    WHERE (g.source_kind = 'attention_followup' AND g.source_id IN (
                        SELECT attention_card_id FROM attention_cards WHERE subject_id = ?
                    )) OR (g.source_kind = 'student_support' AND g.source_id IN (
                        SELECT entry_id FROM student_card_entries WHERE subject_id = ?
                    ))
                    """,
                    (subject_id, subject_id),
                ).fetchall()
                projection_group_ids = [str(row[0]) for row in projection_groups]
                if projection_group_ids:
                    placeholders = ",".join("?" for _ in projection_group_ids)
                    object_ids.update(
                        str(row[0])
                        for row in connection.execute(
                            f"SELECT envelope_object_id FROM sensitive_work_projection_outbox WHERE group_id IN ({placeholders})",
                            projection_group_ids,
                        ).fetchall()
                    )
                object_ids.update(
                    str(row[0])
                    for row in connection.execute(
                        """
                        SELECT payload_object_id FROM quick_inbox_items
                        WHERE subject_id = ?
                        """,
                        (subject_id,),
                    ).fetchall()
                )
                object_ids.update(
                    str(row[0])
                    for row in connection.execute(
                        """
                        SELECT payload_object_id FROM subject_results
                        WHERE subject_id = ?
                        """,
                        (subject_id,),
                    ).fetchall()
                )
                object_ids.update(
                    str(row[0])
                    for row in connection.execute(
                        """
                        SELECT rc.payload_object_id
                        FROM rank_contexts rc
                        JOIN subject_results sr
                          ON sr.result_id = rc.result_id
                        WHERE sr.subject_id = ?
                        """,
                        (subject_id,),
                    ).fetchall()
                )
                object_ids.update(
                    str(row[0])
                    for row in connection.execute(
                        """
                        SELECT ev.payload_object_id
                        FROM evidence_versions ev
                        JOIN subject_results sr
                          ON sr.result_id = ev.result_id
                        WHERE sr.subject_id = ?
                        """,
                        (subject_id,),
                    ).fetchall()
                )
                object_ids.update(
                    str(row[0])
                    for row in connection.execute(
                        """
                        SELECT payload_object_id FROM attention_cards
                        WHERE subject_id = ?
                        """,
                        (subject_id,),
                    ).fetchall()
                )
                object_ids.update(
                    str(row[0])
                    for row in connection.execute(
                        """
                        SELECT payload_object_id FROM student_card_entries
                        WHERE subject_id = ?
                        """,
                        (subject_id,),
                    ).fetchall()
                )
                model_artifacts = connection.execute(
                    """
                    SELECT preview_payload_object_id, result_payload_object_id
                    FROM student_model_artifacts
                    WHERE subject_id = ?
                    """,
                    (subject_id,),
                ).fetchall()
                object_ids.update(
                    str(object_id)
                    for row in model_artifacts
                    for object_id in (
                        row["preview_payload_object_id"],
                        row["result_payload_object_id"],
                    )
                    if object_id is not None
                )
                linked_action_rows = connection.execute(
                    """
                    SELECT DISTINCT a.action_id, a.payload_object_id
                    FROM actions a
                    WHERE a.action_id IN (
                        SELECT action_id FROM support_plans
                        WHERE subject_id = ? AND action_id IS NOT NULL
                        UNION
                        SELECT action_id FROM attention_cards
                        WHERE subject_id = ? AND action_id IS NOT NULL
                        UNION
                        SELECT target_id FROM quick_inbox_items
                        WHERE subject_id = ? AND target_kind = 'action'
                          AND target_id IS NOT NULL
                    )
                    """,
                    (subject_id, subject_id, subject_id),
                ).fetchall()
                linked_action_ids = [
                    str(row["action_id"]) for row in linked_action_rows
                ]
                object_ids.update(
                    str(row["payload_object_id"])
                    for row in linked_action_rows
                )
                if linked_action_ids:
                    placeholders = ",".join("?" for _ in linked_action_ids)
                    object_ids.update(
                        str(row[0])
                        for row in connection.execute(
                            f"""
                            SELECT payload_object_id FROM reminders
                            WHERE action_id IN ({placeholders})
                            """,
                            linked_action_ids,
                        ).fetchall()
                    )
                    object_ids.update(
                        str(row[0])
                        for row in connection.execute(
                            f"""
                            SELECT payload_object_id FROM communication_drafts
                            WHERE action_id IN ({placeholders})
                            """,
                            linked_action_ids,
                        ).fetchall()
                    )
                object_ids.update(
                    str(row[0])
                    for row in connection.execute(
                        """
                        SELECT payload_object_id FROM support_plans
                        WHERE subject_id = ?
                        """,
                        (subject_id,),
                    ).fetchall()
                )
                cache = connection.execute(
                    """
                    SELECT payload_object_id FROM encrypted_summary_cache
                    WHERE subject_id = ?
                    """,
                    (subject_id,),
                ).fetchone()
                if cache is not None:
                    object_ids.add(str(cache[0]))
                record_count = int(
                    connection.execute(
                        """
                        SELECT COUNT(*) FROM support_records
                        WHERE subject_id = ?
                        """,
                        (subject_id,),
                    ).fetchone()[0]
                )
                if projection_group_ids:
                    connection.executemany(
                        "DELETE FROM sensitive_work_groups WHERE group_id = ?",
                        [(group_id,) for group_id in projection_group_ids],
                    )
                connection.execute(
                    "DELETE FROM affair_student_links WHERE subject_id = ?",
                    (subject_id,),
                )
                if linked_affair_participant_ids:
                    connection.executemany(
                        "DELETE FROM affair_participants WHERE participant_id = ?",
                        [
                            (participant_id,)
                            for participant_id in linked_affair_participant_ids
                        ],
                    )
                fingerprint = subject["source_fingerprint"]
                if isinstance(fingerprint, str):
                    connection.execute(
                        "DELETE FROM class_roster_memberships WHERE source_student_key = ?",
                        (fingerprint,),
                    )
                connection.execute(
                    "DELETE FROM student_subject_links WHERE subject_id = ?",
                    (subject_id,),
                )
                if linked_action_ids:
                    placeholders = ",".join("?" for _ in linked_action_ids)
                    connection.execute(
                        f"""
                        UPDATE step_instances SET action_id = NULL
                        WHERE action_id IN ({placeholders})
                        """,
                        linked_action_ids,
                    )
                    connection.execute(
                        f"""
                        DELETE FROM action_dependencies
                        WHERE action_id IN ({placeholders})
                           OR depends_on_action_id IN ({placeholders})
                        """,
                        [*linked_action_ids, *linked_action_ids],
                    )
                    connection.execute(
                        f"""
                        DELETE FROM reminders
                        WHERE action_id IN ({placeholders})
                        """,
                        linked_action_ids,
                    )
                    connection.execute(
                        f"""
                        DELETE FROM communication_drafts
                        WHERE action_id IN ({placeholders})
                        """,
                        linked_action_ids,
                    )
                    connection.execute(
                        f"""
                        DELETE FROM action_audit_events
                        WHERE action_id IN ({placeholders})
                        """,
                        linked_action_ids,
                    )
                    connection.executemany(
                        "DELETE FROM actions WHERE action_id = ?",
                        [(action_id,) for action_id in linked_action_ids],
                    )
                connection.executemany(
                    "DELETE FROM encrypted_objects WHERE object_id = ?",
                    [(object_id,) for object_id in object_ids],
                )
                orphan_object_ids: list[str] = []
                for assessment_id in assessment_ids:
                    if connection.execute(
                        """
                        SELECT 1 FROM subject_results
                        WHERE assessment_id = ? LIMIT 1
                        """,
                        (assessment_id,),
                    ).fetchone() is None:
                        assessment = connection.execute(
                            """
                            SELECT import_id, payload_object_id
                            FROM assessments WHERE assessment_id = ?
                            """,
                            (assessment_id,),
                        ).fetchone()
                        if assessment is not None:
                            import_id = str(assessment["import_id"])
                            orphan_object_ids.append(
                                str(assessment["payload_object_id"])
                            )
                            connection.execute(
                                "DELETE FROM assessments WHERE assessment_id = ?",
                                (assessment_id,),
                            )
                            if connection.execute(
                                """
                                SELECT 1 FROM assessments
                                WHERE import_id = ? LIMIT 1
                                """,
                                (import_id,),
                            ).fetchone() is None:
                                imported = connection.execute(
                                    """
                                    SELECT payload_object_id
                                    FROM assessment_imports
                                    WHERE import_id = ?
                                    """,
                                    (import_id,),
                                ).fetchone()
                                if imported is not None:
                                    orphan_object_ids.append(
                                        str(imported["payload_object_id"])
                                    )
                                    connection.execute(
                                        """
                                        DELETE FROM assessment_imports
                                        WHERE import_id = ?
                                        """,
                                        (import_id,),
                                    )
                connection.executemany(
                    "DELETE FROM encrypted_objects WHERE object_id = ?",
                    [(value,) for value in orphan_object_ids],
                )
                result = {
                    "subject_id": subject_id,
                    "deleted": True,
                    "support_records_deleted": record_count,
                    "linked_actions_deleted": len(linked_action_ids),
                    "encrypted_objects_deleted": len(object_ids),
                    "orphan_import_objects_deleted": len(orphan_object_ids),
                    "wal_rebuilt": True,
                }
                self._remember(
                    connection,
                    operation_id,
                    "support.subject.delete",
                    result,
                )
        return result

    def _redact_shared_objects(
        self,
        connection: Any,
        *,
        vmk: bytes,
        subject_id: str,
        subject: Any,
    ) -> None:
        identity, _ = self.repository.get(
            connection,
            vmk=vmk,
            object_id=str(subject["payload_object_id"]),
        )
        markers = {
            subject_id,
            str(identity.get("source_student_id") or ""),
            str(identity.get("display_name") or ""),
        } - {""}
        neutral = "deleted-participant-" + hmac.new(
            vmk,
            subject_id.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()[:12]

        def redact(value: Any) -> tuple[Any, bool]:
            if isinstance(value, str):
                if value in markers:
                    return neutral, True
                if any(marker in value for marker in markers):
                    return "[与已删除参与者相关的共享正文已移除]", True
                return value, False
            if isinstance(value, list):
                changed = False
                result = []
                for item in value:
                    updated, item_changed = redact(item)
                    result.append(updated)
                    changed = changed or item_changed
                return result, changed
            if isinstance(value, dict):
                changed = False
                result = {}
                for key, item in value.items():
                    updated, item_changed = redact(item)
                    result[key] = updated
                    changed = changed or item_changed
                return result, changed
            return value, False

        rows = self._shared_object_rows(
            connection,
            vmk=vmk,
            markers=markers,
        )
        for row in rows:
            payload, revision = self.repository.get(
                connection,
                vmk=vmk,
                object_id=str(row["object_id"]),
            )
            updated, changed = redact(payload)
            if changed:
                self.repository.put(
                    connection,
                    vmk=vmk,
                    object_id=str(row["object_id"]),
                    object_type=str(row["object_type"]),
                    payload=updated,
                    expected_revision=revision,
                )

    def _shared_object_rows(
        self,
        connection: Any,
        *,
        vmk: bytes,
        markers: set[str],
    ) -> list[Any]:
        rows = connection.execute(
            """
            SELECT object_id, object_type, revision
            FROM encrypted_objects
            WHERE object_type IN (
                'affair', 'affair_participant', 'affair_occurrence',
                'affair_step', 'affair_decision', 'collection_board'
            )
            """
        ).fetchall()
        direct_object_ids: set[str] = set()
        for row in rows:
            payload, _revision = self.repository.get(
                connection,
                vmk=vmk,
                object_id=str(row["object_id"]),
            )
            encoded = json.dumps(payload, ensure_ascii=False)
            if any(marker in encoded for marker in markers):
                direct_object_ids.add(str(row["object_id"]))

        affair_by_object = {
            str(row["payload_object_id"]): str(row["affair_id"])
            for row in connection.execute(
                """
                SELECT payload_object_id, affair_id FROM affairs
                UNION ALL
                SELECT payload_object_id, affair_id
                FROM affair_participants
                UNION ALL
                SELECT payload_object_id, affair_id
                FROM affair_occurrences
                UNION ALL
                SELECT payload_object_id, affair_id
                FROM step_instances
                UNION ALL
                SELECT payload_object_id, affair_id
                FROM decision_records
                """
            ).fetchall()
        }
        affected_affairs = {
            affair_by_object[object_id]
            for object_id in direct_object_ids
            if object_id in affair_by_object
        }
        affected_object_ids = direct_object_ids | {
            object_id
            for object_id, affair_id in affair_by_object.items()
            if affair_id in affected_affairs
        }
        return [
            row
            for row in rows
            if str(row["object_id"]) in affected_object_ids
        ]

    def _create_record_in_connection(
        self,
        connection: Any,
        *,
        vmk: bytes,
        record_id: str,
        subject_id: str,
        normalized: dict[str, Any],
        plan_id: str | None = None,
    ) -> None:
        timestamp = _iso()
        kind = str(normalized["record_kind"])
        connection.execute(
            """
            INSERT INTO support_records (
                record_id, subject_id, record_kind, state,
                current_revision, observed_at, review_at, expires_at,
                plan_id, created_at, updated_at
            ) VALUES (?, ?, ?, 'active', 1, ?, ?, ?, ?, ?, ?)
            """,
            (
                record_id,
                subject_id,
                kind,
                normalized["observed_at"],
                normalized["review_at"],
                normalized["expires_at"],
                plan_id,
                timestamp,
                timestamp,
            ),
        )
        revision_id = uuid4().hex
        object_id = f"support-record-revision-{revision_id}"
        self.repository.put(
            connection,
            vmk=vmk,
            object_id=object_id,
            object_type="support_record_revision",
            payload=normalized,
        )
        connection.execute(
            """
            INSERT INTO record_revisions (
                revision_id, record_id, revision_number,
                payload_object_id, created_at
            ) VALUES (?, ?, 1, ?, ?)
            """,
            (revision_id, record_id, object_id, timestamp),
        )
        if kind in _EXPIRING_KINDS:
            connection.execute(
                """
                INSERT INTO observations (
                    observation_id, subject_id, record_id,
                    category, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    uuid4().hex,
                    subject_id,
                    record_id,
                    normalized["category"],
                    timestamp,
                ),
            )

    def _normalize_record(
        self,
        *,
        record_kind: str,
        content: str,
        scene: str,
        source: str,
        basis: str | None,
        counterexample: str | None,
        category: str | None,
        observed_at: str,
        review_at: str | None,
        expires_at: str | None,
    ) -> dict[str, Any]:
        if record_kind not in _RECORD_KINDS:
            raise VaultError(
                "support_record_kind_invalid",
                "支持记录分类无效",
                status_code=422,
            )
        normalized_observed = _normalize_datetime(
            observed_at,
            "观察时间",
        )
        if normalized_observed is None:
            raise VaultError(
                "support_observed_at_required",
                "支持记录必须填写发生或观察时间",
                status_code=422,
            )
        normalized_review = _normalize_datetime(review_at, "复核时间")
        normalized_expiry = _normalize_datetime(expires_at, "到期时间")
        if record_kind in _EXPIRING_KINDS and (
            not normalized_review or not normalized_expiry
        ):
            raise VaultError(
                "support_review_expiry_required",
                "观察和阶段性判断必须设置复核与到期时间",
                status_code=422,
            )
        clean_category = str(category or "").strip() or "general"
        return {
            "record_kind": record_kind,
            "content": self._text(content, "记录正文", 12_000),
            "scene": self._text(scene, "发生场景", 1000),
            "source": self._text(source, "信息来源", 1000),
            "basis": str(basis or "").strip() or None,
            "counterexample": str(counterexample or "").strip() or None,
            "category": clean_category,
            "observed_at": normalized_observed,
            "review_at": normalized_review,
            "expires_at": normalized_expiry,
            "teacher_confirmed": record_kind != "ai_draft",
        }

    def _rebuild_summary(
        self,
        connection: Any,
        vmk: bytes,
        subject_id: str,
        *,
        as_of: str | None = None,
    ) -> dict[str, object]:
        point = as_of or _iso()
        rows = connection.execute(
            """
            SELECT * FROM support_records
            WHERE subject_id = ? AND state = 'active'
              AND record_kind <> 'ai_draft'
              AND (expires_at IS NULL OR expires_at > ?)
            ORDER BY observed_at DESC, created_at DESC
            """,
            (subject_id, point),
        ).fetchall()
        items = []
        version_parts = []
        for row in rows:
            revision = connection.execute(
                """
                SELECT * FROM record_revisions
                WHERE record_id = ? AND revision_number = ?
                """,
                (str(row["record_id"]), int(row["current_revision"])),
            ).fetchone()
            payload, _ = self.repository.get(
                connection,
                vmk=vmk,
                object_id=str(revision["payload_object_id"]),
            )
            items.append(
                {
                    "record_id": str(row["record_id"]),
                    "revision": int(row["current_revision"]),
                    "record_kind": str(row["record_kind"]),
                    "content": payload["content"],
                    "scene": payload["scene"],
                    "source": payload["source"],
                    "basis": payload.get("basis"),
                    "counterexample": payload.get("counterexample"),
                    "review_at": row["review_at"],
                    "expires_at": row["expires_at"],
                }
            )
            version_parts.append(
                f"{row['record_id']}:{row['current_revision']}:{row['expires_at']}"
            )
        source_version = hashlib.sha256(
            "|".join(version_parts).encode("utf-8")
        ).hexdigest()
        object_id = f"support-summary-{subject_id}"
        payload = {
            "subject_id": subject_id,
            "as_of": point,
            "items": items,
            "source_record_count": len(items),
        }
        existing = connection.execute(
            """
            SELECT payload_object_id FROM encrypted_summary_cache
            WHERE subject_id = ?
            """,
            (subject_id,),
        ).fetchone()
        self.repository.put(
            connection,
            vmk=vmk,
            object_id=object_id,
            object_type="support_summary_cache",
            payload=payload,
        )
        if existing is None:
            connection.execute(
                """
                INSERT INTO encrypted_summary_cache (
                    subject_id, payload_object_id,
                    source_version, updated_at
                ) VALUES (?, ?, ?, ?)
                """,
                (subject_id, object_id, source_version, _iso()),
            )
        else:
            connection.execute(
                """
                UPDATE encrypted_summary_cache
                SET source_version = ?, updated_at = ?
                WHERE subject_id = ?
                """,
                (source_version, _iso(), subject_id),
            )
        return {
            **payload,
            "source_version": source_version,
        }

    def _record_revisions(
        self,
        connection: Any,
        vmk: bytes,
        record_id: str,
    ) -> list[dict[str, object]]:
        rows = connection.execute(
            """
            SELECT * FROM record_revisions
            WHERE record_id = ? ORDER BY revision_number
            """,
            (record_id,),
        ).fetchall()
        revisions = []
        for row in rows:
            payload, _ = self.repository.get(
                connection,
                vmk=vmk,
                object_id=str(row["payload_object_id"]),
            )
            revisions.append(
                {
                    "revision_id": str(row["revision_id"]),
                    "revision_number": int(row["revision_number"]),
                    "payload": payload,
                    "created_at": str(row["created_at"]),
                }
            )
        return revisions

    @staticmethod
    def _subject_row(connection: Any, subject_id: str) -> Any:
        row = connection.execute(
            """
            SELECT * FROM student_subject_links WHERE subject_id = ?
            """,
            (subject_id,),
        ).fetchone()
        if row is None:
            raise VaultError(
                "support_subject_not_found",
                "学生支持对象不存在",
                status_code=404,
            )
        return row

    @staticmethod
    def _record_row(connection: Any, record_id: str) -> Any:
        row = connection.execute(
            "SELECT * FROM support_records WHERE record_id = ?",
            (record_id,),
        ).fetchone()
        if row is None:
            raise VaultError(
                "support_record_not_found",
                "支持记录不存在",
                status_code=404,
            )
        return row

    def _idempotent(
        self,
        operation_id: str,
        operation_type: str,
    ) -> dict[str, object] | None:
        with closing(self.database.connect()) as connection:
            row = connection.execute(
                """
                SELECT operation_type, result_json
                FROM idempotency_ledger WHERE operation_id = ?
                """,
                (operation_id,),
            ).fetchone()
        if row is None:
            return None
        if str(row["operation_type"]) != operation_type:
            raise VaultError(
                "vault_operation_conflict",
                "此操作编号已经用于另一项操作",
                status_code=409,
            )
        return dict(json.loads(str(row["result_json"])))

    @staticmethod
    def _remember(
        connection: Any,
        operation_id: str,
        operation_type: str,
        result: dict[str, object],
    ) -> None:
        connection.execute(
            """
            INSERT INTO idempotency_ledger (
                operation_id, operation_type, result_json, created_at
            ) VALUES (?, ?, ?, ?)
            """,
            (
                operation_id,
                operation_type,
                json.dumps(result, sort_keys=True),
                _iso(),
            ),
        )

    @staticmethod
    def _validate_operation_id(operation_id: str) -> None:
        if re.fullmatch(r"[A-Za-z0-9_-]{8,128}", str(operation_id or "")) is None:
            raise VaultError(
                "vault_operation_id_invalid",
                "操作编号无效，请刷新页面后重试",
                status_code=422,
            )

    @staticmethod
    def _text(value: object, label: str, maximum: int) -> str:
        clean = str(value or "").strip()
        if not clean or len(clean) > maximum:
            raise VaultError(
                "support_text_invalid",
                f"{label}不能为空且不能超过 {maximum} 个字符",
                status_code=422,
            )
        return clean

    @staticmethod
    def _revision_conflict(label: str) -> None:
        raise VaultError(
            "vault_revision_conflict",
            f"{label}已经变化，请刷新后再操作",
            status_code=409,
        )


__all__ = ["SupportRecordService"]
