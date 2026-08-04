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
        self._key_provider(token)
        items, revision = self.source.snapshot()
        filtered = self._filter(items, q=q, class_label=class_label)
        state_by_key: dict[str, tuple[str, str]] = {}
        with closing(self.database.connect()) as connection:
            for row in connection.execute(
                "SELECT source_student_key, subject_id, state FROM class_roster_memberships"
            ).fetchall():
                state_by_key[str(row["source_student_key"])] = (
                    str(row["subject_id"]),
                    str(row["state"]),
                )
        offset = int(cursor or "0") if str(cursor or "0").isdigit() else 0
        size = max(1, min(int(page_size), 100))
        page = filtered[offset : offset + size]
        return {
            "items": [self._item(item, state_by_key.get(item.source_key)) for item in page],
            "classes": sorted({item.class_label for item in items if item.class_label}),
            "source_revision": revision,
            "total": len(filtered),
            "cursor": str(offset + size) if offset + size < len(filtered) else None,
            "page_size": size,
            "filter": {"q": str(q or "").strip(), "class_label": str(class_label or "").strip()},
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
        vmk = self._key_provider(token)
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
                subject_by_key = {
                    item.source_key: self.support.ensure_subject_in_connection(
                        connection,
                        vmk=vmk,
                        source_student_id=item.source_key,
                        display_name=item.display_name,
                        class_label=item.class_label,
                    )
                    for item in selected
                }
                selected_keys = set(subject_by_key)
                active_rows = connection.execute(
                    "SELECT source_student_key FROM class_roster_memberships WHERE state='active'"
                ).fetchall()
                historical_count = sum(
                    1 for row in active_rows if str(row["source_student_key"]) not in selected_keys
                )
                connection.execute(
                    "UPDATE class_roster_memberships SET state='historical', historical_at=?, updated_at=? WHERE state='active'",
                    (timestamp, timestamp),
                )
                for item in selected:
                    subject_id = subject_by_key[item.source_key]
                    connection.execute(
                        """
                        INSERT INTO class_roster_memberships (
                            source_student_key, subject_id, source_revision, state,
                            activated_at, historical_at, updated_at
                        ) VALUES (?, ?, ?, 'active', ?, NULL, ?)
                        ON CONFLICT(source_student_key) DO UPDATE SET
                            subject_id=excluded.subject_id,
                            source_revision=excluded.source_revision,
                            state='active', activated_at=excluded.activated_at,
                            historical_at=NULL, updated_at=excluded.updated_at
                        """,
                        (item.source_key, subject_id, revision, timestamp, timestamp),
                    )
                    subject_row = connection.execute(
                        "SELECT payload_object_id FROM student_subject_links WHERE subject_id=?",
                        (subject_id,),
                    ).fetchone()
                    if subject_row is not None:
                        self.repository.put(
                            connection,
                            vmk=vmk,
                            object_id=str(subject_row["payload_object_id"]),
                            object_type="student_subject",
                            payload={
                                "source_student_id": item.source_key,
                                "display_name": item.display_name,
                                "class_label": item.class_label or None,
                                "identity_snapshot_at": timestamp,
                            },
                        )
                        connection.execute(
                            "UPDATE student_subject_links SET state='active', updated_at=? WHERE subject_id=?",
                            (timestamp, subject_id),
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
        items: list[dict[str, object]] = []
        with closing(self.database.connect()) as connection:
            rows = connection.execute(
                """
                SELECT m.source_student_key, m.subject_id, m.state, m.updated_at,
                       s.payload_object_id
                FROM class_roster_memberships m
                JOIN student_subject_links s ON s.subject_id=m.subject_id
                ORDER BY m.state, m.updated_at DESC
                """
            ).fetchall()
            for row in rows:
                identity, _ = self.repository.get(
                    connection, vmk=vmk, object_id=str(row["payload_object_id"])
                )
                items.append(
                    {
                        "source_key": str(row["source_student_key"]),
                        "subject_id": str(row["subject_id"]),
                        "display_name": str(identity.get("display_name") or ""),
                        "class_label": str(identity.get("class_label") or ""),
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
        item: ExistingStudent, membership: tuple[str, str] | None
    ) -> dict[str, object]:
        return {
            "source_key": item.source_key,
            "student_code": item.student_code,
            "display_name": item.display_name,
            "class_label": item.class_label,
            "subject_id": membership[0] if membership else None,
            "roster_state": membership[1] if membership else "available",
        }


__all__ = ["ClassRosterService"]
