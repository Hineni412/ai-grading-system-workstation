from __future__ import annotations

import json
import hashlib
import re
from contextlib import closing
from datetime import UTC, datetime
from typing import Callable
from uuid import uuid4

from .action_ledger_service import ActionLedgerService
from .encrypted_database import EncryptedDatabase
from .errors import VaultError
from .secure_repository import EncryptedObjectRepository
from .sop_workflow_service import SopWorkflowService
from .support_record_service import SupportRecordService


def _iso() -> str:
    return datetime.now(UTC).isoformat()


class QuickInboxService:
    """Encrypted text inbox. Voice stays unavailable until an offline engine passes."""

    def __init__(
        self,
        database: EncryptedDatabase,
        repository: EncryptedObjectRepository,
        key_provider: Callable[[str], bytes],
        support: SupportRecordService,
        actions: ActionLedgerService,
        sop: SopWorkflowService,
    ) -> None:
        self.database = database
        self.repository = repository
        self._key_provider = key_provider
        self.support = support
        self.actions = actions
        self.sop = sop

    @staticmethod
    def capabilities() -> dict[str, object]:
        return {
            "text_inbox_available": True,
            "voice_inbox_available": False,
            "voice_status": "offline_engine_not_validated",
            "external_transcription_allowed": False,
            "model_enabled": False,
            "physical_request_count": 0,
        }

    def create_text(
        self,
        *,
        token: str,
        operation_id: str,
        text: str,
        subject_id: str | None,
    ) -> dict[str, object]:
        vmk = self._key_provider(token)
        replay = self._idempotent(operation_id, "quick_inbox.create")
        if replay is not None:
            return self.get(
                token=token,
                inbox_item_id=str(replay["inbox_item_id"]),
            )
        clean_text = self._text(text, "速记正文", 12_000)
        fragments = [
            part.strip()
            for part in re.split(r"(?<=[。！？!?；;])\s*|\n+", clean_text)
            if part.strip()
        ]
        if not fragments:
            fragments = [clean_text]
        item_id = uuid4().hex
        object_id = f"quick-inbox-{item_id}"
        timestamp = _iso()
        payload = {
            "source_kind": "text",
            "original_text": clean_text,
            "fragments": [
                {
                    "fragment_id": uuid4().hex,
                    "text": fragment,
                    "suggested_kind": "unclassified",
                }
                for fragment in fragments
            ],
            "audio_created": False,
            "audio_cleanup_state": "not_applicable",
            **self.capabilities(),
        }
        with closing(self.database.connect()) as connection:
            with connection:
                if subject_id:
                    self._subject_exists(connection, subject_id)
                self.repository.put(
                    connection,
                    vmk=vmk,
                    object_id=object_id,
                    object_type="quick_inbox_item",
                    payload=payload,
                )
                connection.execute(
                    """
                    INSERT INTO quick_inbox_items (
                        inbox_item_id, subject_id, payload_object_id,
                        state, created_at, updated_at
                    ) VALUES (?, ?, ?, 'draft', ?, ?)
                    """,
                    (item_id, subject_id, object_id, timestamp, timestamp),
                )
                self._remember(
                    connection,
                    operation_id,
                    "quick_inbox.create",
                    {"inbox_item_id": item_id},
                )
        return self.get(token=token, inbox_item_id=item_id)

    def get(self, *, token: str, inbox_item_id: str) -> dict[str, object]:
        vmk = self._key_provider(token)
        with closing(self.database.connect()) as connection:
            row = self._row(connection, inbox_item_id)
            payload, revision = self.repository.get(
                connection,
                vmk=vmk,
                object_id=str(row["payload_object_id"]),
            )
        return {
            "inbox_item_id": inbox_item_id,
            "revision": revision,
            "subject_id": (
                str(row["subject_id"]) if row["subject_id"] else None
            ),
            "state": str(row["state"]),
            "target_kind": row["target_kind"],
            "target_id": row["target_id"],
            **payload,
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }

    def list_drafts(self, *, token: str) -> dict[str, object]:
        self._key_provider(token)
        with closing(self.database.connect()) as connection:
            ids = [
                str(row[0])
                for row in connection.execute(
                    """
                    SELECT inbox_item_id FROM quick_inbox_items
                    WHERE state = 'draft' ORDER BY created_at DESC
                    """
                ).fetchall()
            ]
        return {
            "items": [
                self.get(token=token, inbox_item_id=item_id)
                for item_id in ids
            ],
            "capabilities": self.capabilities(),
        }

    def update_fragments(
        self,
        *,
        token: str,
        inbox_item_id: str,
        operation_id: str,
        revision: int,
        fragments: list[dict[str, str]],
    ) -> dict[str, object]:
        vmk = self._key_provider(token)
        replay = self._idempotent(operation_id, "quick_inbox.update")
        if replay is not None:
            return self.get(token=token, inbox_item_id=inbox_item_id)
        clean_fragments = []
        for fragment in fragments:
            clean_fragments.append(
                {
                    "fragment_id": str(
                        fragment.get("fragment_id") or uuid4().hex
                    ),
                    "text": self._text(
                        fragment.get("text"),
                        "待确认片段",
                        4000,
                    ),
                    "suggested_kind": str(
                        fragment.get("suggested_kind") or "unclassified"
                    ),
                }
            )
        if not clean_fragments:
            raise VaultError(
                "quick_inbox_fragments_required",
                "至少保留一个待确认片段",
                status_code=422,
            )
        with closing(self.database.connect()) as connection:
            with connection:
                row = self._row(connection, inbox_item_id)
                if str(row["state"]) != "draft":
                    raise VaultError(
                        "quick_inbox_not_draft",
                        "只有待确认速记可以修改",
                        status_code=409,
                    )
                payload, current_revision = self.repository.get(
                    connection,
                    vmk=vmk,
                    object_id=str(row["payload_object_id"]),
                )
                if revision != current_revision:
                    raise VaultError(
                        "vault_revision_conflict",
                        "速记已经变化，请刷新后再保存",
                        status_code=409,
                    )
                payload["fragments"] = clean_fragments
                self.repository.put(
                    connection,
                    vmk=vmk,
                    object_id=str(row["payload_object_id"]),
                    object_type="quick_inbox_item",
                    payload=payload,
                    expected_revision=revision,
                )
                connection.execute(
                    """
                    UPDATE quick_inbox_items SET updated_at = ?
                    WHERE inbox_item_id = ?
                    """,
                    (_iso(), inbox_item_id),
                )
                self._remember(
                    connection,
                    operation_id,
                    "quick_inbox.update",
                    {"inbox_item_id": inbox_item_id},
                )
        return self.get(token=token, inbox_item_id=inbox_item_id)

    def confirm(
        self,
        *,
        token: str,
        inbox_item_id: str,
        operation_id: str,
        fragment_id: str,
        target_kind: str,
        target_options: dict[str, object],
    ) -> dict[str, object]:
        replay = self._idempotent(operation_id, "quick_inbox.confirm")
        if replay is not None:
            return replay
        item = self.get(token=token, inbox_item_id=inbox_item_id)
        if item["state"] != "draft":
            raise VaultError(
                "quick_inbox_not_draft",
                "这条速记已经处理",
                status_code=409,
            )
        fragment = next(
            (
                value
                for value in item["fragments"]
                if value["fragment_id"] == fragment_id
            ),
            None,
        )
        if fragment is None:
            raise VaultError(
                "quick_inbox_fragment_not_found",
                "待确认片段不存在",
                status_code=404,
            )
        claim_entity_id = f"{inbox_item_id}:{fragment_id}"
        claim = self._claim_confirmation(
            entity_kind="quick_inbox",
            entity_id=claim_entity_id,
            operation_id=operation_id,
            requested_target_kind=target_kind,
        )
        child_operation_id = (
            "quick-confirm-target-"
            + hashlib.sha256(
                claim_entity_id.encode("utf-8")
            ).hexdigest()[:32]
        )
        if claim.get("state") == "target_created":
            target_kind = str(claim["target_kind"])
            target_id = str(claim["target_id"])
        elif target_kind == "support_record":
            subject_id = str(
                target_options.get("subject_id") or item.get("subject_id") or ""
            )
            target = self.support.create_record(
                token=token,
                operation_id=child_operation_id,
                subject_id=subject_id,
                record_kind=str(
                    target_options.get("record_kind")
                    or "teacher_observation"
                ),
                content=str(fragment["text"]),
                scene=str(target_options.get("scene") or "文字速记"),
                source="教师确认的文字速记",
                basis=str(target_options.get("basis") or "") or None,
                counterexample=(
                    str(target_options.get("counterexample") or "") or None
                ),
                category=str(target_options.get("category") or "general"),
                observed_at=str(target_options.get("observed_at") or _iso()),
                review_at=(
                    str(target_options.get("review_at"))
                    if target_options.get("review_at")
                    else None
                ),
                expires_at=(
                    str(target_options.get("expires_at"))
                    if target_options.get("expires_at")
                    else None
                ),
            )
            target_id = str(target["record_id"])
        elif target_kind == "action":
            target = self.actions.create_action(
                token=token,
                operation_id=child_operation_id,
                plan_id=str(target_options.get("plan_id") or ""),
                title=str(target_options.get("title") or fragment["text"]),
                details=str(fragment["text"]),
                due_at=(
                    str(target_options.get("due_at"))
                    if target_options.get("due_at")
                    else None
                ),
                depends_on_action_ids=[],
            )
            target_id = str(target["action_id"])
        elif target_kind == "sop":
            target = self.sop.create_affair(
                token=token,
                operation_id=child_operation_id,
                template_version_id=str(
                    target_options.get("template_version_id") or ""
                ),
                title=str(target_options.get("title") or fragment["text"]),
                summary=str(fragment["text"]),
                participant_refs=[
                    str(value)
                    for value in target_options.get("participant_refs", [])
                ],
            )
            target_id = str(target["affair_id"])
        else:
            raise VaultError(
                "quick_inbox_target_invalid",
                "请选择支持记录、行动或 SOP 事务",
                status_code=422,
            )
        if claim.get("state") != "target_created":
            self._mark_confirmation_target(
                entity_kind="quick_inbox",
                entity_id=claim_entity_id,
                operation_id=operation_id,
                target_kind=target_kind,
                target_id=target_id,
            )
        vmk = self._key_provider(token)
        with closing(self.database.connect()) as connection:
            with connection:
                row = self._row(connection, inbox_item_id)
                payload, revision = self.repository.get(
                    connection,
                    vmk=vmk,
                    object_id=str(row["payload_object_id"]),
                )
                remaining = [
                    value for value in payload["fragments"]
                    if value["fragment_id"] != fragment_id
                ]
                confirmed_targets = list(
                    payload.get("confirmed_targets") or []
                )
                confirmed_targets.append(
                    {
                        "fragment_id": fragment_id,
                        "target_kind": target_kind,
                        "target_id": target_id,
                        "confirmed_at": _iso(),
                    }
                )
                payload["confirmed_targets"] = confirmed_targets
                payload["fragments"] = remaining
                if not remaining:
                    payload["original_text"] = None
                    payload["confirmed_at"] = _iso()
                self.repository.put(
                    connection,
                    vmk=vmk,
                    object_id=str(row["payload_object_id"]),
                    object_type="quick_inbox_item",
                    payload=payload,
                    expected_revision=revision,
                )
                connection.execute(
                    """
                    UPDATE quick_inbox_items
                    SET state = ?, target_kind = ?,
                        target_id = ?, updated_at = ?
                    WHERE inbox_item_id = ?
                    """,
                    (
                        "draft" if remaining else "confirmed",
                        target_kind if not remaining else None,
                        target_id if not remaining else None,
                        _iso(),
                        inbox_item_id,
                    ),
                )
                result = {
                    "inbox_item_id": inbox_item_id,
                    "target_kind": target_kind,
                    "target_id": target_id,
                    "confirmed": not remaining,
                    "remaining_fragment_count": len(remaining),
                }
                self._remember(
                    connection,
                    operation_id,
                    "quick_inbox.confirm",
                    result,
                )
                connection.execute(
                    """
                    UPDATE confirmation_claims
                    SET state = 'completed', updated_at = ?
                    WHERE entity_kind = 'quick_inbox'
                      AND entity_id = ? AND operation_id = ?
                    """,
                    (_iso(), claim_entity_id, operation_id),
                )
        return result

    def _claim_confirmation(
        self,
        *,
        entity_kind: str,
        entity_id: str,
        operation_id: str,
        requested_target_kind: str,
    ) -> dict[str, object]:
        with closing(self.database.connect()) as connection:
            with connection:
                existing = connection.execute(
                    """
                    SELECT operation_id, state, target_kind, target_id
                    FROM confirmation_claims
                    WHERE entity_kind = ? AND entity_id = ?
                    """,
                    (entity_kind, entity_id),
                ).fetchone()
                if existing is not None:
                    state = str(existing["state"])
                    if state == "completed":
                        raise VaultError(
                            "confirmation_already_claimed",
                            "这条片段已经确认，请刷新后查看",
                            status_code=409,
                        )
                    existing_target_kind = str(
                        existing["target_kind"] or ""
                    )
                    if (
                        existing_target_kind
                        and existing_target_kind != requested_target_kind
                    ):
                        raise VaultError(
                            "confirmation_target_changed",
                            "恢复确认时不能更改去向，请刷新后按原去向继续",
                            status_code=409,
                        )
                    connection.execute(
                        """
                        UPDATE confirmation_claims
                        SET operation_id = ?, updated_at = ?
                        WHERE entity_kind = ? AND entity_id = ?
                        """,
                        (
                            operation_id,
                            _iso(),
                            entity_kind,
                            entity_id,
                        ),
                    )
                    return {
                        "state": state,
                        "target_kind": existing["target_kind"],
                        "target_id": existing["target_id"],
                    }
                connection.execute(
                    """
                    INSERT INTO confirmation_claims (
                        entity_kind, entity_id, operation_id,
                        state, target_kind, created_at, updated_at
                    ) VALUES (?, ?, ?, 'claimed', ?, ?, ?)
                    """,
                    (
                        entity_kind,
                        entity_id,
                        operation_id,
                        requested_target_kind,
                        _iso(),
                        _iso(),
                    ),
                )
        return {
            "state": "claimed",
            "target_kind": requested_target_kind,
            "target_id": None,
        }

    def _mark_confirmation_target(
        self,
        *,
        entity_kind: str,
        entity_id: str,
        operation_id: str,
        target_kind: str,
        target_id: str,
    ) -> None:
        with closing(self.database.connect()) as connection:
            with connection:
                connection.execute(
                    """
                    UPDATE confirmation_claims
                    SET state = 'target_created', target_kind = ?,
                        target_id = ?, updated_at = ?
                    WHERE entity_kind = ? AND entity_id = ?
                      AND operation_id = ?
                    """,
                    (
                        target_kind,
                        target_id,
                        _iso(),
                        entity_kind,
                        entity_id,
                        operation_id,
                    ),
                )

    def cancel(
        self,
        *,
        token: str,
        inbox_item_id: str,
        operation_id: str,
    ) -> dict[str, object]:
        vmk = self._key_provider(token)
        replay = self._idempotent(operation_id, "quick_inbox.cancel")
        if replay is not None:
            return replay
        with closing(self.database.connect()) as connection:
            with connection:
                row = self._row(connection, inbox_item_id)
                payload, revision = self.repository.get(
                    connection,
                    vmk=vmk,
                    object_id=str(row["payload_object_id"]),
                )
                payload["original_text"] = None
                payload["fragments"] = []
                payload["cancelled_at"] = _iso()
                self.repository.put(
                    connection,
                    vmk=vmk,
                    object_id=str(row["payload_object_id"]),
                    object_type="quick_inbox_item",
                    payload=payload,
                    expected_revision=revision,
                )
                connection.execute(
                    """
                    UPDATE quick_inbox_items
                    SET state = 'cancelled', updated_at = ?
                    WHERE inbox_item_id = ?
                    """,
                    (_iso(), inbox_item_id),
                )
                result = {
                    "inbox_item_id": inbox_item_id,
                    "cancelled": True,
                    "audio_cleanup_state": "not_applicable",
                }
                self._remember(
                    connection,
                    operation_id,
                    "quick_inbox.cancel",
                    result,
                )
        return result

    @staticmethod
    def _row(connection: object, inbox_item_id: str) -> object:
        row = connection.execute(
            "SELECT * FROM quick_inbox_items WHERE inbox_item_id = ?",
            (inbox_item_id,),
        ).fetchone()
        if row is None:
            raise VaultError(
                "quick_inbox_not_found",
                "速记不存在",
                status_code=404,
            )
        return row

    @staticmethod
    def _subject_exists(connection: object, subject_id: str) -> None:
        if connection.execute(
            "SELECT 1 FROM student_subject_links WHERE subject_id = ?",
            (subject_id,),
        ).fetchone() is None:
            raise VaultError(
                "support_subject_not_found",
                "学生支持对象不存在",
                status_code=404,
            )

    def _idempotent(
        self,
        operation_id: str,
        operation_type: str,
    ) -> dict[str, object] | None:
        with closing(self.database.connect()) as connection:
            row = connection.execute(
                """
                SELECT operation_type, result_json FROM idempotency_ledger
                WHERE operation_id = ?
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
        connection: object,
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
    def _text(value: object, label: str, maximum: int) -> str:
        clean = str(value or "").strip()
        if not clean or len(clean) > maximum:
            raise VaultError(
                "quick_inbox_text_invalid",
                f"{label}不能为空且不能超过 {maximum} 个字符",
                status_code=422,
            )
        return clean


__all__ = ["QuickInboxService"]
