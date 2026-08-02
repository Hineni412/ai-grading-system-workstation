from __future__ import annotations

import json
import re
from contextlib import closing
from datetime import UTC, datetime
from typing import Callable
from uuid import uuid4

from .encrypted_database import EncryptedDatabase
from .errors import VaultError
from .model_approval import ModelApproval
from .secure_repository import EncryptedObjectRepository
from .sensitive_work_projection import SensitiveWorkProjection
from .support_record_service import SupportRecordService


_OPERATION = re.compile(r"[A-Za-z0-9_-]{8,128}")


def _iso() -> str:
    return datetime.now(UTC).isoformat()


class SupportRecordAIReview:
    def __init__(
        self,
        database: EncryptedDatabase,
        repository: EncryptedObjectRepository,
        key_provider: Callable[[str], bytes],
        support: SupportRecordService,
        model: ModelApproval,
        projections: SensitiveWorkProjection,
    ) -> None:
        self.database = database
        self.repository = repository
        self._key_provider = key_provider
        self.support = support
        self.model = model
        self.projections = projections

    def prepare(
        self,
        *,
        token: str,
        record_id: str,
        expected_revision: int,
        teacher_supplement: str | None = None,
    ) -> dict[str, object]:
        record = self.support.get_record(token=token, record_id=record_id)
        if int(record["current_revision"]) != int(expected_revision):
            raise VaultError(
                "support_ai_review_stale",
                "支持记录已经修订，请基于最新版本重新讨论",
                status_code=409,
            )
        subject = self.support.get_subject(token=token, subject_id=str(record["subject_id"]))
        supplement = " ".join(str(teacher_supplement or "").split())
        if len(supplement) > 4000:
            raise VaultError("support_ai_supplement_too_long", "补充内容不能超过 4000 字", status_code=422)
        vmk = self._key_provider(token)
        with closing(self.database.connect()) as connection:
            row = connection.execute(
                """
                SELECT * FROM support_ai_review_sessions
                WHERE record_id = ? AND base_revision_number = ?
                  AND state NOT IN ('applied', 'rejected', 'expired', 'invalidated')
                ORDER BY created_at DESC LIMIT 1
                """,
                (record_id, expected_revision),
            ).fetchone()
        review_id = str(row["review_id"]) if row is not None else uuid4().hex
        turn_number = int(row["active_turn"]) + 1 if row is not None else 1
        source_text = str(record.get("content") or record.get("text") or "")
        if supplement:
            source_text = f"已保存的教师记录：{source_text}\n教师本轮补充：{supplement}"
        preview = self.model.prepare(
            token=token,
            purpose="support_record_review",
            source_text=source_text,
            context={
                "subject_id": str(record["subject_id"]),
                "record_id": record_id,
                "record_revision": expected_revision,
                "review_id": review_id,
                "turn_number": turn_number,
            },
            identity_terms=(
                str(subject.get("display_name") or ""),
                str(subject.get("source_student_id") or ""),
            ),
        )
        timestamp = _iso()
        preview_object_id = f"support-review-preview-{preview['preview_id']}"
        supplement_object_id = f"support-review-supplement-{uuid4().hex}" if supplement else None
        with closing(self.database.connect()) as connection:
            with connection:
                self.repository.put(
                    connection,
                    vmk=vmk,
                    object_id=preview_object_id,
                    object_type="support_ai_review_preview",
                    payload={"exact_payload": preview["exact_payload"]},
                )
                if supplement_object_id:
                    self.repository.put(
                        connection,
                        vmk=vmk,
                        object_id=supplement_object_id,
                        object_type="support_ai_review_supplement",
                        payload={"text": supplement},
                    )
                connection.execute(
                    """
                    INSERT INTO support_ai_review_sessions (
                        review_id, record_id, base_revision_number, subject_id,
                        state, active_turn, model_operation_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'preview_ready', ?, NULL, ?, ?)
                    ON CONFLICT(review_id) DO UPDATE SET
                        state = 'preview_ready', active_turn = excluded.active_turn,
                        model_operation_id = NULL, updated_at = excluded.updated_at
                    """,
                    (
                        review_id,
                        record_id,
                        expected_revision,
                        str(record["subject_id"]),
                        turn_number,
                        timestamp,
                        timestamp,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO support_ai_review_turns (
                        turn_id, review_id, turn_number, preview_id,
                        preview_fingerprint, preview_object_id,
                        teacher_supplement_object_id, model_operation_id,
                        result_object_id, state, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL,
                        'preview_ready', ?, ?)
                    """,
                    (
                        uuid4().hex,
                        review_id,
                        turn_number,
                        str(preview["preview_id"]),
                        str(preview["fingerprint"]),
                        preview_object_id,
                        supplement_object_id,
                        timestamp,
                        timestamp,
                    ),
                )
        return {"review_id": review_id, "turn_number": turn_number, **preview}

    def confirm(
        self,
        *,
        token: str,
        review_id: str,
        preview_id: str,
        fingerprint: str,
        operation_id: str,
    ) -> dict[str, object]:
        self._validate_operation(operation_id)
        with closing(self.database.connect()) as connection:
            row = connection.execute(
                """
                SELECT * FROM support_ai_review_turns
                WHERE review_id = ? AND preview_id = ?
                """,
                (review_id, preview_id),
            ).fetchone()
        if row is None or str(row["preview_fingerprint"]) != fingerprint:
            raise VaultError("support_ai_review_preview_changed", "AI 发送预览已经变化，请重新核对", status_code=409)
        result = self.model.confirm(
            token=token,
            preview_id=preview_id,
            fingerprint=fingerprint,
            operation_id=operation_id,
        )
        state = {
            "succeeded": (
                "needs_information" if result.get("response_kind") == "follow_up" else "proposal_ready"
            ),
            "result_unknown": "result_unknown",
        }.get(str(result.get("state")), "claimed")
        session_state = {
            "needs_information": "awaiting_teacher",
            "proposal_ready": "proposal_ready",
            "result_unknown": "result_unknown",
        }.get(state, "awaiting_teacher")
        with closing(self.database.connect()) as connection:
            with connection:
                connection.execute(
                    """
                    UPDATE support_ai_review_turns
                    SET model_operation_id = ?, state = ?, updated_at = ?
                    WHERE review_id = ? AND preview_id = ?
                    """,
                    (operation_id, state, _iso(), review_id, preview_id),
                )
                connection.execute(
                    """
                    UPDATE support_ai_review_sessions
                    SET state = ?, model_operation_id = ?, updated_at = ?
                    WHERE review_id = ?
                    """,
                    (session_state, operation_id, _iso(), review_id),
                )
        return self.read(token=token, review_id=review_id)

    def read(
        self,
        *,
        token: str,
        review_id: str | None = None,
        operation_id: str | None = None,
    ) -> dict[str, object]:
        vmk = self._key_provider(token)
        with closing(self.database.connect()) as connection:
            if review_id:
                session = connection.execute(
                    "SELECT * FROM support_ai_review_sessions WHERE review_id = ?",
                    (review_id,),
                ).fetchone()
            else:
                session = connection.execute(
                    "SELECT * FROM support_ai_review_sessions WHERE model_operation_id = ?",
                    (operation_id,),
                ).fetchone()
            if session is None:
                raise VaultError("support_ai_review_not_found", "AI 复核会话不存在", status_code=404)
            review_id = str(session["review_id"])
            turns = connection.execute(
                "SELECT * FROM support_ai_review_turns WHERE review_id = ? ORDER BY turn_number",
                (review_id,),
            ).fetchall()
            public_turns = []
            for turn in turns:
                preview, _revision = self.repository.get(
                    connection,
                    vmk=vmk,
                    object_id=str(turn["preview_object_id"]),
                )
                supplement = None
                if turn["teacher_supplement_object_id"] is not None:
                    protected, _revision = self.repository.get(
                        connection,
                        vmk=vmk,
                        object_id=str(turn["teacher_supplement_object_id"]),
                    )
                    supplement = str(protected.get("text") or "")
                model_result = None
                if turn["model_operation_id"] is not None:
                    model_result = self.model.status(
                        token=token,
                        operation_id=str(turn["model_operation_id"]),
                    )
                public_turns.append({
                    "turn_number": int(turn["turn_number"]),
                    "preview_id": str(turn["preview_id"]),
                    "fingerprint": str(turn["preview_fingerprint"]),
                    "exact_payload": preview["exact_payload"],
                    "teacher_supplement": supplement,
                    "state": str(turn["state"]),
                    "model_result": model_result,
                })
        return {
            "review_id": review_id,
            "record_id": str(session["record_id"]),
            "base_revision_number": int(session["base_revision_number"]),
            "subject_id": str(session["subject_id"]),
            "state": str(session["state"]),
            "active_turn": int(session["active_turn"]),
            "turns": public_turns,
        }

    def apply(
        self,
        *,
        token: str,
        review_id: str,
        model_operation_id: str,
        expected_revision: int,
        teacher_result: dict[str, object],
        operation_id: str,
    ) -> dict[str, object]:
        self._validate_operation(operation_id)
        review = self.read(token=token, review_id=review_id)
        if int(review["base_revision_number"]) != expected_revision:
            raise VaultError("support_ai_review_stale", "支持记录版本已经变化", status_code=409)
        record = self.support.get_record(token=token, record_id=str(review["record_id"]))
        if int(record["current_revision"]) != expected_revision:
            self._set_session_state(review_id, "invalidated")
            raise VaultError("support_ai_review_stale", "支持记录已经修订，请重新讨论", status_code=409)
        result = self.model.result_context(token=token, operation_id=model_operation_id)
        context = result.get("context")
        if not isinstance(context, dict) or str(context.get("review_id")) != review_id:
            raise VaultError("support_ai_review_result_mismatch", "模型结果不属于这次记录复核", status_code=409)
        if result["response_kind"] != "proposal":
            raise VaultError("support_ai_review_follow_up_required", "模型仍有追问，不能写入学生卡", status_code=409)
        clean_result = self._teacher_result(teacher_result)
        vmk = self._key_provider(token)
        timestamp = _iso()
        projection_group_id = ""
        with closing(self.database.connect()) as connection:
            with connection:
                replay = connection.execute(
                    "SELECT target_id FROM confirmation_claims WHERE operation_id = ?",
                    (operation_id,),
                ).fetchone()
                if replay is not None and replay["target_id"] is not None:
                    entry_id = str(replay["target_id"])
                else:
                    prior = connection.execute(
                        "SELECT entry_id FROM student_card_entries WHERE review_id = ?",
                        (review_id,),
                    ).fetchone()
                    if prior is not None:
                        raise VaultError("support_ai_review_already_applied", "这次 AI 复核已经写入学生卡", status_code=409)
                    entry_id = uuid4().hex
                    object_id = f"student-card-entry-{entry_id}"
                    self.repository.put(
                        connection,
                        vmk=vmk,
                        object_id=object_id,
                        object_type="student_card_entry",
                        payload={
                            "teacher_result": clean_result,
                            "model_draft": result.get("draft_text"),
                            "teacher_confirmed_at": timestamp,
                        },
                    )
                    connection.execute(
                        """
                        INSERT INTO student_card_entries (
                            entry_id, subject_id, payload_object_id, operation_id,
                            model_operation_id, created_at, source_record_id,
                            source_record_revision, review_id, state
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')
                        """,
                        (
                            entry_id,
                            str(review["subject_id"]),
                            object_id,
                            operation_id,
                            model_operation_id,
                            timestamp,
                            str(review["record_id"]),
                            expected_revision,
                            review_id,
                        ),
                    )
                    connection.execute(
                        """
                        INSERT INTO confirmation_claims (
                            entity_kind, entity_id, operation_id, created_at,
                            state, target_kind, target_id, updated_at
                        ) VALUES ('support_ai_review', ?, ?, ?, 'completed',
                            'student_card_entry', ?, ?)
                        """,
                        (review_id, operation_id, timestamp, entry_id, timestamp),
                    )
                    connection.execute(
                        "UPDATE support_ai_review_sessions SET state = 'applied', updated_at = ? WHERE review_id = ?",
                        (timestamp, review_id),
                    )
                queued = self.projections.enqueue(
                    connection,
                    vmk=vmk,
                    source_kind="student_support",
                    source_id=entry_id,
                    state="pending",
                    due_date=(
                        str(clean_result.get("review_date"))
                        if clean_result.get("review_date")
                        else None
                    ),
                )
                projection_group_id = str(queued["group_id"])
        self.projections.drain(token=token)
        projection = self.projections.read_group(
            token=token,
            group_id=projection_group_id,
        )
        return {"saved": True, "entry_id": entry_id, "projection": projection}

    def reject(self, *, token: str, review_id: str, operation_id: str) -> dict[str, object]:
        self._key_provider(token)
        self._validate_operation(operation_id)
        self._set_session_state(review_id, "rejected")
        return {"rejected": True, "review_id": review_id}

    def _set_session_state(self, review_id: str, state: str) -> None:
        with closing(self.database.connect()) as connection:
            with connection:
                changed = connection.execute(
                    "UPDATE support_ai_review_sessions SET state = ?, updated_at = ? WHERE review_id = ?",
                    (state, _iso(), review_id),
                ).rowcount
        if changed != 1:
            raise VaultError("support_ai_review_not_found", "AI 复核会话不存在", status_code=404)

    @staticmethod
    def _teacher_result(value: dict[str, object]) -> dict[str, object]:
        summary = " ".join(str(value.get("summary") or "").split())
        if not summary or len(summary) > 4000:
            raise VaultError("support_ai_teacher_result_invalid", "请核对并填写最终摘要", status_code=422)
        result = {"summary": summary}
        for key in ("strengths", "needs", "open_questions"):
            raw = value.get(key)
            result[key] = [" ".join(str(item).split()) for item in raw][:20] if isinstance(raw, list) else []
        review_date = value.get("review_date")
        result["review_date"] = str(review_date) if review_date else None
        return result

    @staticmethod
    def _validate_operation(value: str) -> None:
        if _OPERATION.fullmatch(str(value or "")) is None:
            raise VaultError("vault_operation_id_invalid", "操作编号无效", status_code=422)


__all__ = ["SupportRecordAIReview"]
