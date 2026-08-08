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
from .sensitive_work_projection import SensitiveWorkProjection
from .work_graph import WorkGraph


_OPERATION_ID = re.compile(r"[A-Za-z0-9_-]{8,128}")
_PROFILE_KEY = re.compile(r"[A-Za-z0-9_-]{2,64}")

CORE_PROFILE_DIMENSIONS = (
    ("learning_ability", "学习与能力"),
    ("interests_strengths", "兴趣与优势"),
    ("personality_behavior", "性格与行为特点"),
    ("peer_relationships", "同伴与人际关系"),
    ("family_communication", "家庭与家校沟通"),
    ("physical_emotional", "身心与情绪状态"),
    ("experiences_changes", "重要经历与近期变化"),
    ("support_needs", "当前需要支持"),
    ("effective_methods", "已验证有效的方法"),
)


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
        projections: SensitiveWorkProjection,
    ) -> None:
        self.database = database
        self.repository = repository
        self._key_provider = key_provider
        self.support = support
        self.model_approval = model_approval
        self.work = work
        self.projections = projections

    def list_cards(self, *, token: str) -> dict[str, object]:
        vmk = self._key_provider(token)
        self.drain_projection_outbox()
        self.projections.drain(token=token)
        subjects = self.support.list_subjects(token=token)["items"]
        cards: list[dict[str, object]] = []
        for subject in subjects:
            if not isinstance(subject, dict):
                continue
            cards.append(
                self._card_for_subject(
                    token=token,
                    vmk=vmk,
                    subject=subject,
                )
            )
        return {"items": cards}

    def get_card(self, *, token: str, subject_id: str) -> dict[str, object]:
        """Read one selected student's sensitive card without loading the class."""
        vmk = self._key_provider(token)
        subject = self.support.get_subject(token=token, subject_id=subject_id)
        return self._card_for_subject(token=token, vmk=vmk, subject=subject)

    def model_context(self, *, token: str, subject_id: str) -> dict[str, object]:
        """Return the one current profile used by every class-teacher AI flow."""

        card = self.get_card(token=token, subject_id=subject_id)
        profile = dict(card["current_profile"])
        return {
            "subject_ref": {
                "kind": "student",
                "id": subject_id,
                "revision": str(card["subject"]["revision"]),
            },
            "display_name": str(card["subject"].get("display_name") or ""),
            "class_label": str(card["subject"].get("class_label") or ""),
            "profile": profile,
            "support_plans": list(card["support_plans"])[:8],
        }

    def model_contexts_for_mentions(
        self,
        *,
        token: str,
        class_label: str | None,
        text: str,
        maximum: int = 4,
    ) -> list[dict[str, object]]:
        """Match unique, exact student names before a general affair model call."""

        message = str(text or "")
        requested_class = str(class_label or "").strip()
        candidates = [
            item
            for item in self.support.list_subjects(token=token)["items"]
            if (
                not requested_class
                or str(item.get("class_label") or "").strip() == requested_class
            )
            and str(item.get("display_name") or "").strip()
            and str(item.get("display_name") or "").strip() in message
        ]
        counts: dict[str, int] = {}
        for item in candidates:
            name = str(item.get("display_name") or "").strip()
            counts[name] = counts.get(name, 0) + 1
        unique = [
            item for item in candidates
            if counts.get(str(item.get("display_name") or "").strip()) == 1
        ]
        unique.sort(
            key=lambda item: len(str(item.get("display_name") or "")),
            reverse=True,
        )
        return [
            self.model_context(token=token, subject_id=str(item["subject_id"]))
            for item in unique[: max(1, min(int(maximum), 8))]
        ]

    def _card_for_subject(
        self,
        *,
        token: str,
        vmk: bytes,
        subject: dict[str, object],
    ) -> dict[str, object]:
        subject_id = str(subject["subject_id"])
        with closing(self.database.connect()) as connection:
            rows = connection.execute(
                """
                SELECT entry_id, payload_object_id, model_operation_id, created_at
                FROM student_card_entries
                WHERE subject_id = ? AND state = 'active'
                ORDER BY created_at, entry_id
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
                        "projection_state": self._projection_state(
                            connection, str(row["entry_id"]), projection_rows
                        ),
                        "created_at": str(row["created_at"]),
                    }
                )
        summary = self.support.get_summary(token=token, subject_id=subject_id)
        plans = self.support.list_support_plans(token=token, subject_id=subject_id)
        latest = entries[-1] if entries else None
        return {
            "subject": subject,
            "entries": entries,
            "current_profile": self._current_profile(latest),
            "existing_records": summary["items"],
            "support_plans": plans["items"],
        }

    def validate_profile_update(self, value: object) -> dict[str, object]:
        if not isinstance(value, dict):
            raise VaultError(
                "student_profile_invalid",
                "AI 返回的学生档案结构无效",
                status_code=422,
            )
        return self._profile(value)

    def upsert_current_profile_in_connection(
        self,
        connection: Any,
        *,
        vmk: bytes,
        subject_id: str,
        profile_update: dict[str, object],
        expected_revision: int | None,
        operation_id: str,
        model_operation_id: str,
        teacher_quote: str,
        model_draft: str,
        source_record_id: str | None = None,
    ) -> dict[str, object]:
        """Merge into one current profile without creating visible history versions."""

        self._validate_operation_id(operation_id)
        incoming = self._profile(profile_update)
        row = connection.execute(
            """
            SELECT * FROM student_card_entries
            WHERE subject_id=? AND state='active'
            ORDER BY created_at DESC, entry_id DESC LIMIT 1
            """,
            (subject_id,),
        ).fetchone()
        timestamp = _iso()
        if row is None:
            if expected_revision not in (None, 0):
                raise VaultError(
                    "student_profile_conflict",
                    "学生档案已经变化，请重新整理后再保存",
                    status_code=409,
                )
            entry_id = uuid4().hex
            object_id = f"student-card-current-{entry_id}"
            merged = incoming
            self.repository.put(
                connection,
                vmk=vmk,
                object_id=object_id,
                object_type="student_card_current_profile",
                payload={
                    "profile": merged,
                    "teacher_quote": self._optional_text(teacher_quote, 4000),
                    "model_draft": self._optional_text(model_draft, 12_000),
                    "teacher_confirmed_at": timestamp,
                },
            )
            connection.execute(
                """
                INSERT INTO student_card_entries (
                    entry_id, subject_id, payload_object_id, operation_id,
                    model_operation_id, created_at, source_record_id, state
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active')
                """,
                (
                    entry_id,
                    subject_id,
                    object_id,
                    operation_id,
                    model_operation_id,
                    timestamp,
                    source_record_id,
                ),
            )
            revision = 1
        else:
            entry_id = str(row["entry_id"])
            object_id = str(row["payload_object_id"])
            payload, revision = self.repository.get(
                connection,
                vmk=vmk,
                object_id=object_id,
            )
            if expected_revision is not None and int(expected_revision) != int(revision):
                raise VaultError(
                    "student_profile_conflict",
                    "学生档案已经变化，请重新整理后再保存",
                    status_code=409,
                )
            current = self._profile_from_payload(payload)
            merged = self._merge_profile(current, incoming)
            revision = self.repository.put(
                connection,
                vmk=vmk,
                object_id=object_id,
                object_type="student_card_current_profile",
                payload={
                    "profile": merged,
                    "teacher_quote": self._optional_text(teacher_quote, 4000),
                    "model_draft": self._optional_text(model_draft, 12_000),
                    "teacher_confirmed_at": timestamp,
                    "last_model_operation_id": model_operation_id,
                },
                expected_revision=revision,
            )
            if source_record_id:
                connection.execute(
                    "UPDATE student_card_entries SET source_record_id=? WHERE entry_id=?",
                    (source_record_id, entry_id),
                )
        return {
            "entry_id": entry_id,
            "revision": int(revision),
            "profile": merged,
            "teacher_confirmed_at": timestamp,
        }

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
        self.projections.upsert(
            token=token,
            source_kind="student_support",
            source_id=entry_id,
            state="pending",
            due_date=clean_sop.get("review_date"),
        )
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
            projection_state = self._projection_state(connection, entry_id, states)
        return {
            "entry_id": entry_id,
            "subject_id": str(row["subject_id"]),
            "revision": revision,
            "model_operation_id": str(row["model_operation_id"]),
            **payload,
            "projection_state": projection_state,
            "created_at": str(row["created_at"]),
        }

    @staticmethod
    def _projection_state(connection: Any, entry_id: str, legacy_states: list[Any]) -> str:
        group = connection.execute(
            """
            SELECT g.group_id,
                   (SELECT COUNT(*) FROM sensitive_work_projection_outbox o
                    WHERE o.group_id = g.group_id AND o.state = 'pending') AS pending_count
            FROM sensitive_work_groups g
            WHERE g.source_kind = 'student_support' AND g.source_id = ?
            """,
            (entry_id,),
        ).fetchone()
        if group is not None:
            return "pending" if int(group["pending_count"]) else "applied"
        return (
            "applied"
            if legacy_states and all(str(item["state"]) == "applied" for item in legacy_states)
            else "pending"
        )

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
    def _current_profile(entry: dict[str, object] | None) -> dict[str, object]:
        if entry is None:
            return {
                "entry_id": None,
                "revision": 0,
                "summary": "",
                "dimensions": [],
                "open_questions": [],
                "support_focus": [],
                "updated_at": None,
            }
        profile = StudentCardService._profile_from_payload(entry)
        return {
            "entry_id": str(entry.get("entry_id") or "") or None,
            "revision": int(entry.get("revision") or 0),
            **profile,
            "updated_at": entry.get("teacher_confirmed_at") or entry.get("created_at"),
        }

    @staticmethod
    def _profile_from_payload(payload: dict[str, object]) -> dict[str, object]:
        profile = payload.get("profile")
        if isinstance(profile, dict):
            return StudentCardService._profile(profile)
        portrait = payload.get("portrait") if isinstance(payload.get("portrait"), dict) else {}
        sop = payload.get("sop") if isinstance(payload.get("sop"), dict) else {}
        dimensions: list[dict[str, object]] = []
        strengths = StudentCardService._text_list(portrait.get("strengths"), "优势")
        needs = StudentCardService._text_list(portrait.get("needs"), "待支持事项")
        if strengths:
            dimensions.append({"key": "interests_strengths", "label": "兴趣与优势", "items": strengths})
        if needs:
            dimensions.append({"key": "support_needs", "label": "当前需要支持", "items": needs})
        steps = StudentCardService._text_list(sop.get("steps"), "支持步骤")
        support_focus = []
        if steps:
            support_focus.append({
                "key": "legacy_support",
                "title": str(sop.get("title") or "现有支持建议").strip(),
                "need": needs[0] if needs else "继续结合实际情况观察",
                "effective_methods": [],
                "next_actions": steps,
            })
        summary = " ".join(str(portrait.get("summary") or "").split())
        return {
            "summary": summary,
            "dimensions": dimensions,
            "open_questions": StudentCardService._text_list(
                portrait.get("open_questions"), "待了解问题"
            ),
            "support_focus": support_focus,
        }

    @staticmethod
    def _profile(value: dict[str, object]) -> dict[str, object]:
        summary = StudentCardService._text(value.get("summary"), "学生档案摘要", 4000)
        raw_dimensions = value.get("dimensions")
        if not isinstance(raw_dimensions, list) or len(raw_dimensions) > 24:
            raise VaultError("student_profile_invalid", "学生档案维度格式无效", status_code=422)
        dimensions: list[dict[str, object]] = []
        seen: set[str] = set()
        for raw in raw_dimensions:
            if not isinstance(raw, dict):
                raise VaultError("student_profile_invalid", "学生档案维度格式无效", status_code=422)
            key = str(raw.get("key") or "").strip()
            if _PROFILE_KEY.fullmatch(key) is None or key in seen:
                raise VaultError("student_profile_invalid", "学生档案维度编号无效", status_code=422)
            seen.add(key)
            label = StudentCardService._text(raw.get("label"), "学生档案维度名称", 80)
            items = StudentCardService._text_list(raw.get("items"), label, required=True)
            dimensions.append({"key": key, "label": label, "items": items})
        raw_focus = value.get("support_focus", [])
        if not isinstance(raw_focus, list) or len(raw_focus) > 20:
            raise VaultError("student_profile_invalid", "学生支持重点格式无效", status_code=422)
        focus: list[dict[str, object]] = []
        focus_seen: set[str] = set()
        for raw in raw_focus:
            if not isinstance(raw, dict):
                raise VaultError("student_profile_invalid", "学生支持重点格式无效", status_code=422)
            key = str(raw.get("key") or "").strip()
            if _PROFILE_KEY.fullmatch(key) is None or key in focus_seen:
                raise VaultError("student_profile_invalid", "学生支持重点编号无效", status_code=422)
            focus_seen.add(key)
            focus.append({
                "key": key,
                "title": StudentCardService._text(raw.get("title"), "学生支持重点", 200),
                "need": StudentCardService._text(raw.get("need"), "学生支持需要", 1000),
                "effective_methods": StudentCardService._text_list(
                    raw.get("effective_methods"), "有效支持方法"
                ),
                "next_actions": StudentCardService._text_list(
                    raw.get("next_actions"), "后续支持行动"
                ),
            })
        return {
            "summary": summary,
            "dimensions": dimensions,
            "open_questions": StudentCardService._text_list(
                value.get("open_questions"), "待了解问题"
            ),
            "support_focus": focus,
        }

    @staticmethod
    def _merge_profile(
        current: dict[str, object],
        incoming: dict[str, object],
    ) -> dict[str, object]:
        current_dimensions = {
            str(item["key"]): dict(item)
            for item in current.get("dimensions", [])
            if isinstance(item, dict) and item.get("key")
        }
        for item in incoming.get("dimensions", []):
            if isinstance(item, dict):
                current_dimensions[str(item["key"])] = dict(item)
        current_focus = {
            str(item["key"]): dict(item)
            for item in current.get("support_focus", [])
            if isinstance(item, dict) and item.get("key")
        }
        for item in incoming.get("support_focus", []):
            if isinstance(item, dict):
                current_focus[str(item["key"])] = dict(item)
        return {
            "summary": incoming["summary"],
            "dimensions": list(current_dimensions.values()),
            "open_questions": list(incoming.get("open_questions", [])),
            "support_focus": list(current_focus.values()),
        }

    @staticmethod
    def _optional_text(value: object, maximum: int) -> str:
        clean = " ".join(str(value or "").split())
        return clean[:maximum]

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
