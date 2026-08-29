from __future__ import annotations

import hashlib
import json
import re
from contextlib import closing
from datetime import UTC, datetime
from typing import Callable

from .encrypted_database import EncryptedDatabase
from .errors import VaultError
from .existing_student_roster import ExistingStudent, ExistingStudentRosterSource
from .roster_ref import (
    parse_stable_ref,
    student_content_revision,
    student_stable_ref,
)
from .secure_repository import EncryptedObjectRepository
from .support_record_service import SupportRecordService


_OPERATION = re.compile(r"[A-Za-z0-9_-]{8,128}")


def _iso() -> str:
    return datetime.now(UTC).isoformat()


class ClassRosterService:
    """Own current-homeroom replacement over a read-only student source."""

    def __init__(
        self,
        database: EncryptedDatabase,
        repository: EncryptedObjectRepository,
        key_provider: Callable[[str], bytes],
        support: SupportRecordService,
        source: ExistingStudentRosterSource,
    ) -> None:
        self.database = database
        self.repository = repository
        self._key_provider = key_provider
        self.support = support
        self.source = source

    def browse(
        self,
        *,
        token: str,
        q: str | None = None,
        class_label: str | None = None,
        cursor: str | None = None,
        page_size: int = 50,
    ) -> dict[str, object]:
        items, revision = self.source.snapshot()
        filtered = self._filter(items, q=q, class_label=class_label)
        # 成员表只提供花名册状态；档案编号一律按稳定标识查 student_subject_links。
        state_by_ref: dict[str, str] = {}
        subject_by_ref: dict[str, str] = {}
        if self.database.exists:
            with closing(self.database.connect()) as connection:
                for row in connection.execute(
                    "SELECT source_student_key, state FROM class_roster_memberships"
                ).fetchall():
                    state_by_ref[str(row["source_student_key"])] = str(row["state"])
                for row in connection.execute(
                    "SELECT subject_id, source_fingerprint FROM student_subject_links WHERE state='active'"
                ).fetchall():
                    fingerprint = row["source_fingerprint"]
                    if isinstance(fingerprint, str):
                        subject_by_ref[fingerprint] = str(row["subject_id"])
        offset = int(cursor or "0") if str(cursor or "0").isdigit() else 0
        size = max(1, min(int(page_size), 100))
        page = filtered[offset : offset + size]
        page_items: list[dict[str, object]] = []
        for item in page:
            ref = self._roster_ref(item)
            page_items.append(self._item(
                item,
                subject_id=subject_by_ref.get(ref),
                roster_state=state_by_ref.get(ref, "available"),
                roster_ref=ref,
                student_revision=self._student_revision(item),
            ))
        return {
            "items": page_items,
            "classes": sorted({item.class_label for item in items if item.class_label}),
            "source_revision": revision,
            "total": len(filtered),
            "cursor": str(offset + size) if offset + size < len(filtered) else None,
            "page_size": size,
            "filter": {"q": str(q or "").strip(), "class_label": str(class_label or "").strip()},
        }

    def ai_candidates(self, *, token: str, class_label: str | None) -> list[dict[str, str]]:
        if not str(class_label or "").strip():
            return []
        items = self._filter(self.source.snapshot()[0], q=None, class_label=class_label)
        return [{
            "id": self._roster_ref(item),
            "revision": self._student_revision(item),
            "display_name": item.display_name,
            "class_label": item.class_label,
        } for item in items]

    def resolve_roster_ref(
        self,
        *,
        roster_ref: str,
        expected_revision: str,
    ) -> ExistingStudent:
        """Resolve a stable roster ref against the current roster snapshot.

        兼容输入：旧格式 64 位十六进制临时编号不会匹配任何稳定标识，
        统一按「引用已失效」处理。
        """
        candidate = str(roster_ref or "")
        revision = str(expected_revision or "")
        for item in self.source.snapshot()[0]:
            if self._roster_ref(item) == candidate:
                if self._student_revision(item) != revision:
                    raise VaultError(
                        "class_teacher_target_conflict",
                        "学生资料已变化，请刷新后重新核对",
                        status_code=409,
                    )
                return item
        raise VaultError(
            "class_teacher_subject_ref_invalid",
            "学生引用已失效，请重新选择",
            status_code=409,
        )

    def roster_identity_for_ref(
        self,
        *,
        roster_ref: str,
    ) -> dict[str, object] | None:
        """Read-only preview lookup: resolve a stable roster ref without writing."""
        candidate = str(roster_ref or "")
        if not candidate:
            return None
        student = next(
            (
                item
                for item in self.source.snapshot()[0]
                if self._roster_ref(item) == candidate
            ),
            None,
        )
        if student is None:
            return None
        subject_id: str | None = None
        if self.database.exists:
            with closing(self.database.connect()) as connection:
                # 身份映射唯一走 student_subject_links：按稳定标识查档案链接。
                link = connection.execute(
                    "SELECT subject_id FROM student_subject_links WHERE source_fingerprint=? AND state='active'",
                    (candidate,),
                ).fetchone()
                if link is not None:
                    subject_id = str(link["subject_id"])
        return {
            # 对外「学号」用花名册学籍号；source_key 只是花名册行号，不能当学号展示。
            "source_student_id": student.student_code or student.source_key,
            "display_name": student.display_name,
            "class_label": student.class_label,
            "subject_id": subject_id,
        }

    def replace_current(
        self,
        *,
        token: str,
        operation_id: str,
        expected_source_revision: str,
        class_label: str,
        q: str | None = None,
    ) -> dict[str, object]:
        if _OPERATION.fullmatch(str(operation_id or "")) is None:
            raise VaultError("vault_operation_id_invalid", "操作编号无效", status_code=422)
        selected_class = str(class_label or "").strip()
        if not selected_class:
            raise VaultError(
                "class_roster_class_required", "请先选择一个班级", status_code=422
            )
        source_items, revision = self.source.snapshot()
        if revision != str(expected_source_revision or ""):
            raise VaultError(
                "class_roster_source_changed",
                "现有学生库已经变化，请刷新筛选结果后再设置",
                status_code=409,
            )
        selected = self._filter(source_items, q=q, class_label=selected_class)
        if not selected:
            raise VaultError(
                "class_roster_filter_empty", "当前筛选没有学生，未更改我班学生", status_code=422
            )
        request_fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "revision": revision,
                    "class_label": selected_class,
                    "q": str(q or "").strip(),
                    "keys": [item.source_key for item in selected],
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        with closing(self.database.connect()) as connection:
            replay = connection.execute(
                "SELECT operation_type, result_json FROM idempotency_ledger WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
        if replay is not None:
            if str(replay["operation_type"]) != "class_roster.replace":
                raise VaultError(
                    "vault_operation_conflict", "同一操作编号不能用于不同操作", status_code=409
                )
            stored = json.loads(str(replay["result_json"]))
            if stored.get("request_fingerprint") != request_fingerprint:
                raise VaultError(
                    "vault_operation_conflict", "同一操作编号对应的筛选条件不同", status_code=409
                )
            return self.current(token=token, replayed=True)

        timestamp = _iso()
        with closing(self.database.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                # 花名册激活只写状态：旧 active 置 historical、新行按稳定标识写 active，
                # 不创建学生档案，也不写 subject_id（身份映射唯一走 student_subject_links）。
                selected_refs = {self._roster_ref(item) for item in selected}
                active_rows = connection.execute(
                    "SELECT source_student_key FROM class_roster_memberships WHERE state='active'"
                ).fetchall()
                historical_count = sum(
                    1 for row in active_rows if str(row["source_student_key"]) not in selected_refs
                )
                connection.execute(
                    "UPDATE class_roster_memberships SET state='historical', historical_at=?, updated_at=? WHERE state='active'",
                    (timestamp, timestamp),
                )
                for item in selected:
                    connection.execute(
                        """
                        INSERT INTO class_roster_memberships (
                            source_student_key, state, activated_at, historical_at, updated_at
                        ) VALUES (?, 'active', ?, NULL, ?)
                        ON CONFLICT(source_student_key) DO UPDATE SET
                            state='active', activated_at=excluded.activated_at,
                            historical_at=NULL, updated_at=excluded.updated_at
                        """,
                        (self._roster_ref(item), timestamp, timestamp),
                    )
                result = {
                    "request_fingerprint": request_fingerprint,
                    "active_count": len(selected),
                    "historical_count": historical_count,
                    "source_revision": revision,
                }
                connection.execute(
                    "INSERT INTO idempotency_ledger VALUES (?, 'class_roster.replace', ?, ?)",
                    (operation_id, json.dumps(result, ensure_ascii=False), timestamp),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self.current(token=token, replayed=False)

    def current(self, *, token: str, replayed: bool = False) -> dict[str, object]:
        vmk = self._key_provider(token)
        snapshot_by_ref = {
            self._roster_ref(item): item for item in self.source.snapshot()[0]
        }
        items: list[dict[str, object]] = []
        with closing(self.database.connect()) as connection:
            rows = connection.execute(
                """
                SELECT source_student_key, state, updated_at
                FROM class_roster_memberships
                ORDER BY state, updated_at DESC
                """
            ).fetchall()
            link_by_ref: dict[str, object] = {}
            for row in connection.execute(
                "SELECT subject_id, source_fingerprint, payload_object_id FROM student_subject_links"
            ).fetchall():
                fingerprint = row["source_fingerprint"]
                if isinstance(fingerprint, str):
                    link_by_ref[fingerprint] = row
            for row in rows:
                ref = str(row["source_student_key"])
                source_item = snapshot_by_ref.get(ref)
                link = link_by_ref.get(ref)
                if source_item is not None:
                    display_name = source_item.display_name
                    class_label = source_item.class_label
                elif link is not None:
                    identity, _ = self.repository.get(
                        connection,
                        vmk=vmk,
                        object_id=str(link["payload_object_id"]),  # type: ignore[index]
                    )
                    display_name = str(identity.get("display_name") or "")
                    class_label = str(identity.get("class_label") or "")
                else:
                    parsed = parse_stable_ref(ref)
                    class_label = parsed[0] if parsed else ""
                    display_name = parsed[1] if parsed else ref
                items.append(
                    {
                        "source_key": ref,
                        "subject_id": (
                            str(link["subject_id"]) if link is not None else None  # type: ignore[index]
                        ),
                        "display_name": display_name,
                        "class_label": class_label,
                        "state": str(row["state"]),
                    }
                )
        return {
            "items": items,
            "active_count": sum(1 for item in items if item["state"] == "active"),
            "historical_count": sum(1 for item in items if item["state"] == "historical"),
            "replayed": replayed,
        }

    @staticmethod
    def _filter(
        items: list[ExistingStudent], *, q: str | None, class_label: str | None
    ) -> list[ExistingStudent]:
        needle = str(q or "").strip().casefold()
        selected_class = str(class_label or "").strip()
        return [
            item
            for item in items
            if (not selected_class or item.class_label == selected_class)
            and (
                not needle
                or needle in item.display_name.casefold()
                or needle in item.student_code.casefold()
            )
        ]

    @staticmethod
    def _item(
        item: ExistingStudent,
        *,
        subject_id: str | None,
        roster_state: str,
        roster_ref: str,
        student_revision: str,
    ) -> dict[str, object]:
        return {
            "source_key": item.source_key,
            "student_code": item.student_code,
            "display_name": item.display_name,
            "class_label": item.class_label,
            "subject_id": subject_id,
            "roster_state": roster_state,
            "roster_ref": roster_ref,
            "student_revision": student_revision,
        }

    @staticmethod
    def _roster_ref(item: ExistingStudent) -> str:
        return student_stable_ref(
            class_label=item.class_label,
            student_code=item.student_code,
            display_name=item.display_name,
        )

    @staticmethod
    def _student_revision(item: ExistingStudent) -> str:
        return student_content_revision(
            source_key=item.source_key,
            student_code=item.student_code,
            display_name=item.display_name,
            class_label=item.class_label,
        )


__all__ = ["ClassRosterService"]
