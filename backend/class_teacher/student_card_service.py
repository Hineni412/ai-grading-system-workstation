from __future__ import annotations

import re
from contextlib import closing
from datetime import UTC, date, datetime
from typing import Any, Callable
from uuid import uuid4

from .encrypted_database import EncryptedDatabase
from .errors import VaultError
from .model_approval import ModelApproval
from .secure_repository import EncryptedObjectRepository
from .support_record_service import SupportRecordService
from .work_graph import WorkGraph


_OPERATION_ID = re.compile(r"[A-Za-z0-9_-]{8,128}")


def _iso() -> str:
    return datetime.now(UTC).isoformat()


def _date(value: object, label: str) -> str | None:
    if value is None or not str(value).strip():
        return None
    try:
        return date.fromisoformat(str(value).strip()).isoformat()
    except ValueError as exc:
        raise VaultError(
            "student_card_date_invalid",
            f"{label}必须是明确的年月日",
            status_code=422,
        ) from exc


class StudentCardService:
    """One encrypted student-card seam plus an anonymous projection outbox."""

    def __init__(
        self,
        database: EncryptedDatabase,
        repository: EncryptedObjectRepository,
        key_provider: Callable[[str], bytes],
        support: SupportRecordService,
        model_approval: ModelApproval,
        work: WorkGraph,
    ) -> None:
        self.database = database
        self.repository = repository
        self._key_provider = key_provider
        self.support = support
        self.model_approval = model_approval
        self.work = work

    def list_cards(self, *, token: str) -> dict[str, object]:
        vmk = self._key_provider(token)
        self.drain_projection_outbox()
        subjects = self.support.list_subjects(token=token)["items"]
        cards: list[dict[str, object]] = []
        for subject in subjects:
            if not isinstance(subject, dict):
                continue
            subject_id = str(subject["subject_id"])
            with closing(self.database.connect()) as connection:
                rows = connection.execute(
                    """
                    SELECT entry_id, payload_object_id, model_operation_id, created_at
                    FROM student_card_entries
                    WHERE subject_id = ? ORDER BY created_at, entry_id
                    """,
                    (subject_id,),
                ).fetchall()
                entries: list[dict[str, object]] = []
                for row in rows:
                    payload, revision = self.repository.get(
                        connection,
                        vmk=vmk,
                        object_id=str(row["payload_object_id"]),
                    )
                    projection_rows = connection.execute(
                        """
                        SELECT projection_kind, state
                        FROM student_card_projection_outbox
                        WHERE entry_id = ? ORDER BY projection_kind
                        """,
                        (str(row["entry_id"]),),
                    ).fetchall()
                    entries.append(
                        {
                            "entry_id": str(row["entry_id"]),
                            "revision": revision,
                            "model_operation_id": str(row["model_operation_id"]),
                            **payload,
                            "projection_state": (
                                "applied"
                                if projection_rows
                                and all(str(item["state"]) == "applied" for item in projection_rows)
                                else "pending"
                            ),
                            "created_at": str(row["created_at"]),
                        }
                    )
            summary = self.support.get_summary(token=token, subject_id=subject_id)
            plans = self.support.list_support_plans(token=token, subject_id=subject_id)
            cards.append(
                {
                    "subject": subject,
                    "entries": entries,
                    "existing_records": summary["items"],
                    "support_plans": plans["items"],
                }
            )
        return {"items": cards}

    def confirm_structure(
        self,
        *,
        token: str,
        subject_id: str,
        model_operation_id: str,
        operation_id: str,
        portrait: dict[str, object],
        sop: dict[str, object],
    ) -> dict[str, object]:
        self._validate_operation_id(operation_id)
        vmk = self._key_provider(token)
        replay = self._entry_for_operation(token=token, operation_id=operation_id)
        if replay is not None:
            return {"saved": True, **replay}

        result = self.model_approval.result_context(
            token=token,
            operation_id=model_operation_id,
        )
        context = result["context"]
        if not isinstance(context, dict) or str(context.get("subject_id") or "") != subject_id:
            raise VaultError(
                "student_card_subject_mismatch",
                "模型草稿与当前学生卡不一致，请重新生成预览",
                status_code=409,
            )
        if result["response_kind"] != "proposal":
            raise VaultError(
                "student_card_follow_up_required",
                "模型仍有追问，请补充后重新生成预览",
                status_code=409,
            )
        self.support.get_subject(token=token, subject_id=subject_id)
        clean_portrait = self._portrait(portrait)
        clean_sop = self._sop(sop)
        teacher_quote = self._text(
            context.get("teacher_quote"),
            "教师原话",
            4000,
        )
        model_draft = self._text(result.get("draft_text"), "模型草稿", 12_000)
        entry_id = uuid4().hex
        object_id = f"student-card-entry-{entry_id}"
        timestamp = _iso()
        payload = {
            "teacher_quote": teacher_quote,
            "portrait": clean_portrait,
            "sop": clean_sop,
            "model_draft": model_draft,
            "teacher_confirmed_at": timestamp,
        }
        projection_specs = [
            ("task", clean_sop.get("review_date")),
            ("sop", clean_sop.get("review_date")),
        ]
        with closing(self.database.connect()) as connection:
            with connection:
                existing_model = connection.execute(
                    """
                    SELECT entry_id FROM student_card_entries
                    WHERE model_operation_id = ?
                    """,
                    (model_operation_id,),
                ).fetchone()
                if existing_model is not None:
                    raise VaultError(
                        "student_card_model_result_already_used",
                        "这份模型草稿已经写入学生卡",
                        status_code=409,
                    )
                self.repository.put(
                    connection,
                    vmk=vmk,
                    object_id=object_id,
                    object_type="student_card_entry",
                    payload=payload,
                )
                connection.execute(
                    """
                    INSERT INTO student_card_entries (
                        entry_id, subject_id, payload_object_id, operation_id,
                        model_operation_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entry_id,
                        subject_id,
                        object_id,
                        operation_id,
                        model_operation_id,
                        timestamp,
                    ),
                )
                for projection_kind, due_date in projection_specs:
                    connection.execute(
                        """
                        INSERT INTO student_card_projection_outbox (
                            event_id, entry_id, projection_id, projection_kind,
                            due_date, state, attempts, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, 'pending', 0, ?, ?)
                        """,
                        (
                            uuid4().hex,
                            entry_id,
                            uuid4().hex,
                            projection_kind,
                            due_date,
                            timestamp,
                            timestamp,
                        ),
                    )
        self.drain_projection_outbox()
        entry = self._entry(token=token, entry_id=entry_id)
        return {"saved": True, **entry}

    def drain_projection_outbox(self) -> int:
        if not self.database.exists:
            return 0
        with closing(self.database.connect()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM student_card_projection_outbox
                WHERE state = 'pending' ORDER BY created_at, event_id
                """
            ).fetchall()
        applied = 0
        for row in rows:
            event_id = str(row["event_id"])
            try:
                self.work.enqueue_sensitive_projection(
                    projection_id=str(row["projection_id"]),
                    projection_kind=str(row["projection_kind"]),
                    due_date=(None if row["due_date"] is None else str(row["due_date"])),
                    status="pending",
                    source_revision=1,
                    operation_id=f"card_projection_{event_id}",
                )
            except Exception:
                with closing(self.database.connect()) as connection:
                    with connection:
                        connection.execute(
                            """
                            UPDATE student_card_projection_outbox
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
                        UPDATE student_card_projection_outbox
                        SET state = 'applied', attempts = attempts + 1, updated_at = ?
                        WHERE event_id = ?
                        """,
                        (_iso(), event_id),
                    )
            applied += 1
        return applied

    def _entry_for_operation(
        self,
        *,
        token: str,
        operation_id: str,
    ) -> dict[str, object] | None:
        with closing(self.database.connect()) as connection:
            row = connection.execute(
                "SELECT entry_id FROM student_card_entries WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
        return None if row is None else self._entry(token=token, entry_id=str(row[0]))

    def _entry(self, *, token: str, entry_id: str) -> dict[str, object]:
        vmk = self._key_provider(token)
        with closing(self.database.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM student_card_entries WHERE entry_id = ?",
                (entry_id,),
            ).fetchone()
            if row is None:
                raise VaultError(
                    "student_card_entry_not_found",
                    "学生卡记录不存在",
                    status_code=404,
                )
            payload, revision = self.repository.get(
                connection,
                vmk=vmk,
                object_id=str(row["payload_object_id"]),
            )
            states = connection.execute(
                """
                SELECT state FROM student_card_projection_outbox
                WHERE entry_id = ?
                """,
                (entry_id,),
            ).fetchall()
        return {
            "entry_id": entry_id,
            "subject_id": str(row["subject_id"]),
            "revision": revision,
            "model_operation_id": str(row["model_operation_id"]),
            **payload,
            "projection_state": (
                "applied"
                if states and all(str(item["state"]) == "applied" for item in states)
                else "pending"
            ),
            "created_at": str(row["created_at"]),
        }

    @staticmethod
    def _portrait(value: dict[str, object]) -> dict[str, object]:
        summary = StudentCardService._text(value.get("summary"), "结构化肖像摘要", 4000)
        return {
            "summary": summary,
            "strengths": StudentCardService._text_list(value.get("strengths"), "优势"),
            "needs": StudentCardService._text_list(value.get("needs"), "待支持事项"),
            "open_questions": StudentCardService._text_list(
                value.get("open_questions"),
                "待核实问题",
            ),
        }

    @staticmethod
    def _sop(value: dict[str, object]) -> dict[str, object]:
        return {
            "title": StudentCardService._text(value.get("title"), "SOP 标题", 1000),
            "steps": StudentCardService._text_list(value.get("steps"), "SOP 步骤", required=True),
            "review_date": _date(value.get("review_date"), "SOP 复查日期"),
        }

    @staticmethod
    def _text(value: object, label: str, maximum: int) -> str:
        clean = " ".join(str(value or "").split())
        if not clean or len(clean) > maximum:
            raise VaultError(
                "student_card_text_invalid",
                f"{label}不能为空且不能超过 {maximum} 个字符",
                status_code=422,
            )
        return clean

    @staticmethod
    def _text_list(
        value: object,
        label: str,
        *,
        required: bool = False,
    ) -> list[str]:
        if not isinstance(value, list):
            items: list[str] = []
        else:
            items = [
                " ".join(str(item).split())
                for item in value
                if " ".join(str(item).split())
            ]
        if len(items) > 30 or any(len(item) > 1000 for item in items) or (required and not items):
            raise VaultError(
                "student_card_list_invalid",
                f"{label}格式无效",
                status_code=422,
            )
        return items

    @staticmethod
    def _validate_operation_id(operation_id: str) -> None:
        if _OPERATION_ID.fullmatch(str(operation_id or "")) is None:
            raise VaultError(
                "student_card_operation_id_invalid",
                "操作编号无效",
                status_code=422,
            )


__all__ = ["StudentCardService"]
